#!/usr/bin/env python3
"""Stop hook that catches a HANDOFF.md edited outside the ledger.

Every finished cycle records the digest of the HANDOFF.md it finished
on. An agent that writes the file directly - the habit the skill exists
to replace - leaves that digest behind, and nothing says so until the
next ``hq open`` prints LEDGER BEHIND, often a session later.

This hook compares the file of the thread the session is on - the slug
``hq open``, ``begin``, or ``adopt`` records - against its last recorded
digest at the end of every turn, and names the cycle it is now behind.
A session on no thread hears nothing, and no other thread is named.

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
import pathlib
import sys
from typing import Any

try:
    # pytest and mutmut put src/ on sys.path (pytest.ini).
    from handoff import storage, threads
except ImportError:
    # Notes:
    # - Script mode puts scripts/ on sys.path rather than bin/, so reach
    #   the package by its own path.
    # - ../src comes first, since mutmut writes its mutated copy there.
    plugin_root = pathlib.Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(plugin_root / 'src'), str(plugin_root / 'bin')]
    from handoff import storage, threads


def report(payload: dict[str, Any]) -> int:
    """Name the session's thread when its HANDOFF.md has drifted from its
    last cycle.

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
    session = str(payload.get('session_id') or 'unknown').replace('/', '_')
    try:
        slug = threads.thread_record(session).read_text(encoding='utf-8').strip()
    except OSError:
        return 0
    if not slug:
        return 0
    name = storage.HANDOFF_DIRNAME
    start = pathlib.Path(str(payload.get('cwd') or '') or '.')
    folder = None
    for candidate in [start, *start.parents]:
        if (candidate / name / slug / 'cycles' / 'manifest.tsv').is_file():
            folder = candidate / name / slug
            break
    if folder is None:
        return 0

    handoff = folder / 'HANDOFF.md'
    if not handoff.is_file() or (folder / '.hq.lock').exists():
        return 0
    # A thread hq done has closed is no longer anyone's to file.
    if (folder / storage.DONE_NAME).exists():
        return 0
    rows = storage.read_tsv(folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
    if not rows:
        return 0
    current = storage.sha12_path(handoff)
    if current in {'-', storage.live_sha(rows[-1])}:
        return 0

    state_dir = threads.state_dir()
    state_path = state_dir / f'{session}.handoff.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    announced = (state.get('stop') or {}).get('reported') or {}
    if announced.get(slug) == current:
        return 0

    announced[slug] = current
    state['stop'] = {'reported': announced}
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding='utf-8')
    except OSError:
        pass
    cycle = rows[-1]['cycle']
    message = (
        f'handoff: {name}/{slug}/HANDOFF.md was written by hand since cycle'
        f' {cycle} finished; run hq begin {slug}, then hq finish {slug}'
        ' --log "...", or the next open reports LEDGER BEHIND')
    json.dump({'systemMessage': message}, sys.stdout)
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
