"""The hq program: the command line for per-project handoff folders.

Invoked as ``hq <verb> <slug> [args] [options]``. ``bin/hq`` is the
wrapper the plugin puts on PATH. It runs ``bin/hq.py``, which calls
``handoff.cli.main``.
"""
