"""The hq program: fifteen verbs for managing per-project handoff files.

Each handoff lives in .handoff/<slug>/: HANDOFF.md written by the agent,
ledger.tsv stamping every artifact, and standing.md for decisions,
constraints, dead ends, and evidence. Three rendered blocks keep the payload bounded
across cycles, because each block shows live counts and unsuperseded items,
not history.

Invoke as::

    hq <verb> <slug> [args] [options]

``bin/hq`` is a wrapper the plugin puts on the agent's PATH; it runs
``python3 bin/hq.py`` with the same arguments, which calls
``handoff.cli.main``. Or call ``handoff.cli.main(argv)`` in-process.
Exit 0 on success, 1 on a refusal or a blocking finding, 2 on bad usage
or an unresolvable slug.

Notes
-----
- Every rule and rendering is a pure function importable from the module
  that owns its file format.
- All impure inputs enter through ``anchors()``.
- ``finish`` writes nothing until every check passes; a failed run appends
  nothing to the ledger or standing.md.
- ``ledger.tsv`` and ``standing.md`` are append-only; ``witness()`` detects
  any edit to a previously recorded line via a byte-prefix sha check.
"""
