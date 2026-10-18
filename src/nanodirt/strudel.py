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
import urllib.request
from pathlib import Path
from typing import Any

from .library import Sample

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
    if "clip" in e and "legato" not in e:
        e["legato"] = e.pop("clip")
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

    def __init__(self, cache: Path, fetch: bool = True) -> None:
        self.cache = cache
        self.fetch = fetch
        self.banks: dict[str, list[str] | dict[str, list[str]]] = {}
        self.aliases: dict[str, str] = {}
        self.samples: dict[str, Sample] = {}
        self._pending: set[str] = set()
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

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
            data = json.loads(path.read_text())
        else:
            data = json.loads(self._fetch_cached(map_url(spec), suffix=".json").read_text())
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
            aliases = json.loads(self._fetch_cached(ALIAS_MAP, suffix=".json").read_text())
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
        name = self.resolve_bank(name)
        bank = self.banks.get(name)
        if bank is None:
            return None
        n = int(index or 0)
        root: float | None = None
        if isinstance(bank, list):
            url = bank[n % len(bank)]
        else:
            target = SUPERDOUGH_DEFAULT_MIDI if midi is None else midi
            key = min(bank, key=lambda k: abs(note_to_midi(k) - target))
            root = float(note_to_midi(key))
            url = bank[key][n % len(bank[key])]
        sample = self._local(url)
        return (sample, root) if sample is not None else None

    def _local(self, url: str) -> Sample | None:
        with self._lock:
            sample = self.samples.get(url)
            if sample is not None:
                return sample
            path = self._cache_path(url)
            if path.exists():
                sample = self.samples[url] = Sample(path)
                return sample
            if self.fetch and url not in self._pending:
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

    def _start_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="nanodirt-fetch", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            try:
                url = self._queue.get(timeout=5)
            except queue.Empty:
                return
            try:
                self._download(url)
            except OSError as exc:
                logger.warning("cannot download %s: %s", url, exc)
            finally:
                with self._lock:
                    self._pending.discard(url)

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
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            logger.warning("%s needs ffmpeg to transcode .%s", url, source_ext)
            return
        src = target.with_suffix("." + source_ext)
        src.write_bytes(data)
        subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", str(src), str(target)],
            check=True,
            timeout=60,
        )
        src.unlink()
