"""``nanodirt vortex``: vortex's editor or REPL, playing through nanodirt in one process."""

from __future__ import annotations

import argparse
import sys


def run(args: argparse.Namespace) -> int:
    """Start the engine, register :mod:`nanodirt.vortex.boot` as ``vortex.boot``, start vortex."""
    try:
        import vortex  # noqa: F401
    except ImportError:
        print(
            "nanodirt vortex needs vortex (Python 3.11-3.13):\n"
            "  pip install git+https://codeberg.org/uzu/vortex",
            file=sys.stderr,
        )
        return 1
    from ..cli import start
    from . import boot

    server, dirt = start(args)
    boot.dirt = dirt
    sys.modules["vortex.boot"] = boot  # vortex_dsl() imports this name
    try:
        if args.cli:
            from vortex.repl import start_repl

            start_repl()
        else:
            from vortex.gui import start_gui

            start_gui()
    finally:
        server.quit()
    return 0
