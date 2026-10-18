"""Sample library (DirtSoundLibrary.sc): folders of sound files, read on first use.

Folders are listed at startup; a file's header is parsed, and its buffer read
asynchronously, the first time an event plays it. The read is sent at once, so
it normally completes within the event's latency (0.1-0.3 s).
"""

from __future__ import annotations

import logging
import struct
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_EXTENSIONS = (".wav", ".aif", ".aiff", ".aifc")
LONG_FILE_FRAMES = 16777216  # BufRd's float phase loses precision beyond 2^24


@dataclass(frozen=True)
class Header:
    frames: int
    channels: int
    sample_rate: float

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def _ieee_extended(data: bytes) -> float:
    """Decode an 80-bit IEEE 754 extended float (AIFF sample rate)."""
    exponent = ((data[0] & 0x7F) << 8) | data[1]
    mantissa = int.from_bytes(data[2:10], "big")
    if exponent == 0 and mantissa == 0:
        return 0.0
    value = mantissa * 2.0 ** (exponent - 16383 - 63)
    return -value if data[0] & 0x80 else value


def read_header(path: Path) -> Header | None:
    """Frames, channels and sample rate of a WAV or AIFF file, or None if unreadable."""
    try:
        with open(path, "rb") as f:
            riff = f.read(12)
            if len(riff) < 12:
                return None
            if riff[:4] in (b"RIFF", b"RF64") and riff[8:12] == b"WAVE":
                return _wav_header(f)
            if riff[:4] == b"FORM" and riff[8:12] in (b"AIFF", b"AIFC"):
                return _aiff_header(f)
    except OSError:
        pass
    return None


def _wav_header(f) -> Header | None:  # type: ignore[no-untyped-def]
    channels = rate = block_align = 0
    while True:
        chunk = f.read(8)
        if len(chunk) < 8:
            return None
        cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
        if cid == b"fmt ":
            fmt = f.read(size)
            channels, rate = struct.unpack("<HI", fmt[2:8])
            block_align = struct.unpack("<H", fmt[12:14])[0]
        elif cid == b"data":
            if not channels or not block_align:
                return None
            return Header(size // block_align, channels, float(rate))
        else:
            f.seek(size, 1)
        if size % 2:
            f.seek(1, 1)


def _aiff_header(f) -> Header | None:  # type: ignore[no-untyped-def]
    while True:
        chunk = f.read(8)
        if len(chunk) < 8:
            return None
        cid, size = chunk[:4], struct.unpack(">I", chunk[4:])[0]
        if cid == b"COMM":
            comm = f.read(size)
            channels, frames = struct.unpack(">hI", comm[:6])
            return Header(frames, channels, _ieee_extended(comm[8:18]))
        f.seek(size + size % 2, 1)


@dataclass
class Sample:
    path: Path
    header: Header | None = None
    buffer: int | None = None

    @property
    def hash(self) -> int:
        """Stands in for SuperDirt's ``buffer.identityHash`` in cut groups."""
        return hash(self.path) & 0x7FFFFFFF


@dataclass
class SoundLibrary:
    """Banks of samples by name, each sorted by path, as SuperDirt loads them."""

    num_channels: int = 2
    read_buffer: Callable[[str], int] | None = None
    banks: dict[str, list[Sample]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_folders(self, paths: Iterable[str | Path]) -> None:
        """Register every subfolder of each path as a bank named after it."""
        for root in paths:
            root = Path(root).expanduser()
            if root.is_dir() and any(p.suffix.lower() in FILE_EXTENSIONS for p in root.iterdir()):
                self.add_folder(root)
                continue
            for folder in sorted(p for p in root.glob("*") if p.is_dir()):
                self.add_folder(folder)

    def add_folder(self, folder: Path, name: str | None = None) -> None:
        files = sorted(
            p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in FILE_EXTENSIONS
        )
        if files:
            self.banks[name or folder.name] = [Sample(p) for p in files]

    def get(self, name: str, index: float | None) -> Sample | None:
        """The sample for ``s`` and ``n``: ``n`` truncated, wrapped over the bank."""
        bank = self.banks.get(name)
        if not bank:
            return None
        sample = bank[int(index or 0) % len(bank)]
        self._ensure_loaded(sample)
        return sample if sample.header is not None else None

    def _ensure_loaded(self, sample: Sample) -> None:
        with self._lock:
            if sample.header is None:
                sample.header = read_header(sample.path)
                if sample.header is None or sample.header.frames == 0:
                    logger.warning("cannot read sound file %s", sample.path)
                    sample.header = None
                    return
            if sample.buffer is None and self.read_buffer is not None:
                sample.buffer = self.read_buffer(str(sample.path))

    def instrument(self, sample: Sample) -> str:
        assert sample.header is not None
        kind = "dirt_sample_long" if sample.header.frames > LONG_FILE_FRAMES else "dirt_sample"
        return f"{kind}_{sample.header.channels}_{self.num_channels}"

    def stretch_instrument(self, sample: Sample) -> str:
        assert sample.header is not None
        return f"dirt_stretchsample_{sample.header.channels}_{self.num_channels}"
