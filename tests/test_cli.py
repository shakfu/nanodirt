"""The CLI's engine options: audio input opens only where it is used."""

from __future__ import annotations

from typing import Any

import pytest

from nanodirt import cli


class Booted(Exception):
    pass


@pytest.mark.parametrize(
    ("argv", "default", "want"),
    [
        ([], 2, 2),  # nanodirt, as SuperDirt's startup file
        (["--inputs", "0"], 2, 0),
        (["strudel", "song.strudel"], 0, 0),  # as play_strudel_file calls start
        (["--inputs", "2", "strudel", "song.strudel"], 0, 2),
    ],
)
def test_input_channels(
    argv: list[str], default: int, want: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    class FakeServer:
        def __init__(self, options: Any) -> None:
            seen.append(options)

        def boot(self) -> None:
            raise Booted

    monkeypatch.setattr("nanosynth.server.Server", FakeServer)
    with pytest.raises(Booted):
        cli.start(cli.build_parser().parse_args(argv), inputs=default)
    assert seen[0].input_bus_channel_count == want
