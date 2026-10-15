"""Run vortex in-process through the spike.

python spike_vortex.py           # vortex GUI
python spike_vortex.py --cli     # vortex IPython REPL
python spike_vortex.py --auto 4  # headless: play a fixed pattern for 4 s and record
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from nanosynth import Options, Server

import spike
import vortex_boot


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true")
    ap.add_argument("--auto", type=float, default=0)
    ap.add_argument("--record", type=Path)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    server = Server(Options(output_bus_channel_count=2, input_bus_channel_count=2))
    server.boot()
    for d in spike.build_defs():
        server.send_synthdef(d)
    if a.record:
        server.record(a.record, num_channels=2)
    vortex_boot._spike = sp = spike.Spike(server, spike.SAMPLES)
    sys.modules["vortex.boot"] = vortex_boot  # vortex_dsl() imports this name
    try:
        if a.auto:
            from vortex import vortex_dsl

            with vortex_dsl() as mod:
                exec('d[1] = s("bd*2 [sn hh]") >> speed("1 1.5")', vars(mod))
                time.sleep(a.auto)
                exec("hush()", vars(mod))
        elif a.cli:
            from vortex.repl import start_repl

            start_repl()
        else:
            from vortex.gui import start_gui

            start_gui()
    finally:
        sp.report()
        if a.record:
            server.stop_recording()
        server.quit()
        print("vortex.stream imported:", "vortex.stream" in sys.modules)
        print("pyliblo3 imported:", "pyliblo3" in sys.modules)


if __name__ == "__main__":
    main()
