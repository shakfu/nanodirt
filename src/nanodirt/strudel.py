"""Strudel: field rules and sample maps (superdough's sampler.mjs, prebake.mjs).

Strudel's OSC output sends only ``s``, ``n`` and ``bank``; the browser loads its
samples from JSON maps. nanodirt loads the same default maps, and downloads a
file into its cache the first time an event plays it. Until the download
finishes, the event plays without a source, as SuperDirt does for an unknown
sound.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .library import Sample
from .retry import ERRORS, Backoff, transient
from .soundfonts import Soundfonts, write_wav

logger = logging.getLogger(__name__)

CDN = "https://strudel.b-cdn.net"
# prebake.mjs at Strudel a3302cf: (map, base) pairs.
DEFAULT_MAPS = [
    (f"{CDN}/piano.json", f"{CDN}/piano/"),
    (f"{CDN}/vcsl.json", f"{CDN}/VCSL/"),
    (f"{CDN}/tidal-drum-machines.json", f"{CDN}/tidal-drum-machines/machines/"),
    (f"{CDN}/uzu-drumkit.json", f"{CDN}/uzu-drumkit/"),
    (f"{CDN}/uzu-wavetables.json", f"{CDN}/uzu-wavetables/"),
    (f"{CDN}/mridangam.json", f"{CDN}/mrid/"),
]
ALIAS_MAP = f"{CDN}/tidal-drum-machines-alias.json"
SUPERDOUGH_DEFAULT_MIDI = 36  # valueToMidi(hapValue, 36)


def normalize(event: dict[str, Any]) -> dict[str, Any]:
    """Strudel's fields in SuperDirt's terms (docs/dev/integration.md, "Differences")."""
    e = dict(event)
    if "sustain" in e:  # superdough: ADSR sustain level, not a duration
        e["strudelSustain"] = e.pop("sustain")
    if "clip" in e:  # delta already includes clip; superdough then cuts at the hap end
        del e["clip"]
        e.setdefault("legato", 1.0)
    e.pop("duration", None)
    e.pop("channels", None)
    return e


_NOTE = re.compile(r"^([a-gA-G])([#sb]*)(-?\d+)?$")


def note_to_midi(name: str) -> int:
    """superdough's noteToMidi: ``c3`` is 48; ``s`` and ``#`` sharpen, ``b`` flattens."""
    m = _NOTE.match(name)
    if m is None:
        raise ValueError(f"not a note name: {name!r}")
    letter, accidentals, octave = m.groups()
    chroma = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}[letter.lower()]
    offset = sum(1 if a in "#s" else -1 for a in accidentals)
    return (int(octave) if octave else 3) * 12 + 12 + chroma + offset


def github_path(base: str, subpath: str = "") -> str:
    """sampler.mjs ``githubPath``: ``github:user/repo/branch/...`` to a raw URL."""
    parts = base[len("github:") :].rstrip("/").split("/")
    user = parts[0]
    repo = parts[1] if len(parts) > 1 else "samples"
    branch = parts[2] if len(parts) > 2 else "main"
    rest = "/".join([*parts[3:], subpath])
    return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{rest}"


def map_url(spec: str) -> str:
    if spec.startswith("github:"):
        return github_path(spec, "strudel.json")
    if spec.startswith("shabda:"):
        return f"https://shabda.ndre.gr/{spec[len('shabda:') :]}.json?strudel=1"
    return spec


class StrudelSamples:
    """Sounds from Strudel sample maps, downloaded on first use."""

    def __init__(self, cache: Path, fetch: bool = True, blocking: bool = False) -> None:
        self.cache = cache
        self.fetch = fetch
        self.blocking = blocking  # download on first use, before returning (offline rendering)
        self.banks: dict[str, list[str] | dict[str, list[str]]] = {}
        self.aliases: dict[str, str] = {}
        self.samples: dict[str, Sample] = {}
        self.failed: set[str] = set()  # URLs that cannot load: undecodable, or HTTP 4xx
        self._retry = Backoff()  # URLs that failed to download
        self._pending: set[str] = set()
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._loaded = threading.Condition(self._lock)  # notified after each download
        self._worker: threading.Thread | None = None
        self.soundfonts = Soundfonts(cache / "soundfonts", fetch, blocking)

    # -- maps

    def load_map(self, data: dict[str, Any], base: str = "") -> None:
        """``processSampleMap``: each entry is a list of paths, or a dict by note."""
        base = data.get("_base", base)
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if isinstance(value, str):
                value = [value]
            b = value.get("_base", base) if isinstance(value, dict) else base
            if b.startswith("github:"):
                b = github_path(b)
            if isinstance(value, list):
                self.banks[key] = [b + v for v in value]
            else:
                self.banks[key] = {
                    note: [b + s for s in ([v] if isinstance(v, str) else v)]
                    for note, v in value.items()
                    if not note.startswith("_")
                }

    def load_map_file(self, spec: str, base: str = "") -> None:
        path = Path(spec).expanduser()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(
                self._fetch_cached(map_url(spec), suffix=".json").read_text(encoding="utf-8")
            )
        if not base and not path.exists():
            base = map_url(spec).rsplit("/", 1)[0] + "/"
        self.load_map(data, base)

    def load_defaults(self) -> None:
        for spec, base in DEFAULT_MAPS:
            try:
                self.load_map_file(spec, base)
            except OSError as exc:
                logger.warning("cannot load Strudel sample map %s: %s", spec, exc)
        try:
            aliases = json.loads(
                self._fetch_cached(ALIAS_MAP, suffix=".json").read_text(encoding="utf-8")
            )
            self.aliases = {short.lower(): full for full, short in aliases.items()}
        except OSError as exc:
            logger.warning("cannot load Strudel bank aliases: %s", exc)

    def resolve_bank(self, name: str) -> str:
        """``bank_s`` with a bank alias (``tr909_bd``) expanded to its full name."""
        bank, sep, rest = name.partition("_")
        if sep and bank.lower() in self.aliases:
            return f"{self.aliases[bank.lower()]}_{rest}"
        return name

    # -- lookup

    def get(
        self, name: str, index: float | None, midi: float | None
    ) -> tuple[Sample, float | None] | None:
        """The sample for an event and, for a pitched bank, its root note."""
        found = self._url(name, index, midi)
        if found is None:
            return None
        url, root = found
        sample = self._local(url)
        return (sample, root) if sample is not None else None

    def _url(
        self, name: str, index: float | None, midi: float | None
    ) -> tuple[str, float | None] | None:
        bank = self.banks.get(self.resolve_bank(name))
        if bank is None:
            return None
        n = int(index or 0)
        if isinstance(bank, list):
            return bank[n % len(bank)], None
        target = SUPERDOUGH_DEFAULT_MIDI if midi is None else midi
        key = min(bank, key=lambda k: abs(note_to_midi(k) - target))
        return bank[key][n % len(bank[key])], float(note_to_midi(key))

    def prepare(self, keys: Iterable[tuple[str, int, float | None]], timeout: float) -> bool:
        """Download the samples for *keys* (name, index, midi), waiting up to *timeout*.

        Returns False if some were still downloading at the timeout; they go on in the background.
        """
        urls = {u for k in set(keys) if (u := self._url(*k)) is not None}
        waiting = [url for url, _ in urls if self._local(url) is None]
        deadline = time.monotonic() + timeout
        with self._loaded:
            while any(url in self._pending for url in waiting):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._loaded.wait(remaining)
        return True

    def _local(self, url: str) -> Sample | None:
        path = self._cache_path(url)
        with self._lock:
            if url in self.samples:
                return self.samples[url]
            if url in self.failed or self._retry.waiting(url):
                return None
            download = self.fetch and self.blocking and not path.exists()
        if download:
            self._try_download(url)
        with self._lock:
            if url in self.samples:
                return self.samples[url]
            if path.exists():
                sample = self.samples[url] = Sample(path)
                return sample
            if self.fetch and not self.blocking and url not in self._pending:
                self._pending.add(url)
                self._queue.put(url)
                self._start_worker()
        return None

    def prefetch(self, names: list[str] | None = None) -> None:
        """Download every file of *names*, or of every bank, now."""
        for name in names or list(self.banks):
            bank = self.banks.get(self.resolve_bank(name))
            urls = (
                bank if isinstance(bank, list) else [u for us in (bank or {}).values() for u in us]
            )
            for url in urls:
                if not self._cache_path(url).exists():
                    self._download(url)

    # -- downloads

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode()).hexdigest()[:20]
        ext = url.rsplit(".", 1)[-1].lower() if "." in url.rsplit("/", 1)[-1] else "wav"
        if ext not in ("wav", "aif", "aiff", "aifc"):
            ext = "wav"  # transcoded
        return self.cache / "samples" / f"{digest}.{ext}"

    def _fetch_cached(self, url: str, suffix: str) -> Path:
        path = self.cache / "maps" / (hashlib.sha1(url.encode()).hexdigest()[:20] + suffix)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url.replace("#", "%23"), timeout=30) as r:
                path.write_bytes(r.read())
        return path

    def _start_worker(self) -> None:  # with self._lock held
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, name="nanodirt-fetch", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            try:
                url = self._queue.get(timeout=5)
            except queue.Empty:
                with self._lock:  # a URL queued after the timeout finds no worker and starts one
                    if self._queue.empty():
                        self._worker = None
                        return
                continue
            try:
                self._try_download(url)
            finally:
                with self._lock:
                    self._pending.discard(url)
                    self._loaded.notify_all()

    def _try_download(self, url: str) -> None:
        """Download *url*; a transient failure may be retried after a delay, any other never."""
        try:
            self._download(url)
        except ERRORS as exc:
            with self._lock:
                if transient(exc):
                    delay = self._retry.failed(url)
                    logger.warning("cannot download %s: %s; retrying in %.0f s", url, exc, delay)
                else:
                    self.failed.add(url)
                    logger.warning("cannot download %s: %s", url, exc)
        else:
            with self._lock:
                self._retry.succeeded(url)

    def _download(self, url: str) -> None:
        target = self._cache_path(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url.replace("#", "%23"), timeout=30) as r:
            data = r.read()
        source_ext = url.rsplit(".", 1)[-1].lower()
        if source_ext in ("wav", "aif", "aiff", "aifc"):
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
            return
        # nanosynth's libsndfile is built without MP3, Ogg and FLAC.
        if source_ext == "mp3":
            from ._mp3 import decode

            try:
                pcm, channels, rate = decode(data)
            except ValueError as exc:
                raise ValueError(f"cannot decode: {exc}") from None
            write_wav(target, pcm, channels, rate)
            return
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ValueError(f"needs ffmpeg to transcode .{source_ext}")
        src = target.with_suffix("." + source_ext)
        src.write_bytes(data)
        try:
            subprocess.run(
                [ffmpeg, "-loglevel", "error", "-y", "-i", str(src), str(target)],
                check=True,
                timeout=60,
            )
        except subprocess.SubprocessError as exc:
            raise ValueError(f"ffmpeg failed: {exc}") from None
        finally:
            src.unlink(missing_ok=True)
