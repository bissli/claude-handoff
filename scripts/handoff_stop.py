#!/usr/bin/env python3
"""Stop hook that catches a HANDOFF.md edited outside the ledger.

Every finished cycle records the digest of the HANDOFF.md it finished
on. An agent that writes the file directly - the habit the skill exists
to replace - leaves that digest behind, and nothing says so until the
next ``hq open`` prints LEDGER BEHIND, often a session later.

This hook compares the file against the last recorded digest at the end
of every turn and names the cycle the folder is now behind.

Notes
-----
- A folder holding .hq.lock is inside an open cycle. The edit is the
  work in progress and ``finish`` will record it, so it is skipped.
- One message per digest per session. A second hand edit changes the
  digest and reports again; the same edit never repeats.
- Exit is always 0. Exit 2 would block the stop, which no advisory
  message may do.
- The state file is ``<session>.handoff.json``, shared with the gate
  hook. The context-budget hook's ``<session>.json`` is never touched.
"""

import json
import os
import pathlib
import sys
from typing import Any

try:
    # pytest and mutmut run from the repo root and import the package.
    from bin import hq
except ImportError:
    # Script mode puts scripts/ on sys.path rather than the plugin root,
    # so reach the program in bin/ by its own path. One branch or the
    # other binds hq, never both, so a test patching bin.hq patches the
    # module the hook holds.
    sys.path.insert(
        0, str(pathlib.Path(__file__).resolve().parent.parent / 'bin'))
    import hq

STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')


def report(payload: dict[str, Any]) -> int:
    """Name every handoff whose file has drifted from its last cycle.

    Parameters
    ----------
    payload : dict[str, Any]
        The Stop payload, carrying cwd, session_id, stop_hook_active,
        and agent_id when a subagent raised the event.

    Returns
    -------
    int
        Always 0, whether or not a message was printed.

    Notes
    -----
    - A subagent shares neither the folder nor the user's attention, so
      its Stop is dropped whole.
    - stop_hook_active means this Stop was itself raised by a hook. The
      state would suppress a repeat anyway; the guard keeps the hook out
      of the loop in the first place.
    """
    if payload.get('agent_id') or payload.get('stop_hook_active'):
        return 0
    name = hq.HANDOFF_DIRNAME
    start = pathlib.Path(str(payload.get('cwd') or '') or '.')
    root = None
    for candidate in [start, *start.parents]:
        if next((candidate / name).glob('*/cycles/manifest.tsv'), None):
            root = candidate
            break
    if root is None:
        return 0

    drifted: list[tuple[str, str, str]] = []
    for folder in sorted((root / name).iterdir()):
        handoff = folder / 'HANDOFF.md'
        manifest = folder / 'cycles' / 'manifest.tsv'
        if not manifest.is_file() or not handoff.is_file():
            continue
        if (folder / '.hq.lock').exists():
            continue
        rows = hq._read_tsv(manifest, hq.MANIFEST_FIELDS)
        if not rows:
            continue
        current = hq._sha12_path(handoff)
        if current == '-':
            continue
        if current == hq.live_sha(rows[-1]):
            continue
        drifted.append((folder.name, current, rows[-1]['cycle']))
    if not drifted:
        return 0

    session = str(payload.get('session_id') or 'unknown').replace('/', '_')
    state_dir = pathlib.Path(os.environ.get('HQ_STATE_DIR', STATE_DIR))
    state_path = state_dir / f'{session}.handoff.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    announced = (state.get('stop') or {}).get('reported') or {}
    fresh = [item for item in drifted if announced.get(item[0]) != item[1]]
    if not fresh:
        return 0

    for slug, sha, _ in fresh:
        announced[slug] = sha
    state['stop'] = {'reported': announced}
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding='utf-8')
    except OSError:
        pass
    clauses = '; '.join(
        f'{name}/{slug}/HANDOFF.md was written by hand since cycle {cycle} '
        f'finished; run hq begin {slug}, then hq finish {slug} '
        f'--log "...", or the next open reports LEDGER BEHIND'
        for slug, _, cycle in fresh)
    json.dump({'systemMessage': f'handoff: {clauses}'}, sys.stdout)
    return 0


def main() -> int:
    """Read one Stop payload from stdin and report any hand-edited handoff.
    """
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        return report(payload)
    except Exception:
        return 0


if __name__ == '__main__':
    sys.exit(main())
