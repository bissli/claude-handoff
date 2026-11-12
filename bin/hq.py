#!/usr/bin/env python3
"""Entry point the ``bin/hq`` wrapper runs.
"""
import pathlib
import sys

# Notes:
# - An interpreter run with PYTHONSAFEPATH puts no script directory on
#   sys.path, so bin/ goes on by hand.
# - ../src comes first, since mutmut writes its mutated copy there.
here = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(here.parent / 'src'), str(here)]
from handoff.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
