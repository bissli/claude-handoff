#!/usr/bin/env python3
"""Status line showing context against the cycle's handoff point.

Claude Code hands this command a JSON payload on stdin whose
``context_window.total_input_tokens`` is the billed context of the last
API call: cache reads plus cache writes plus uncached input.

The line reads::

    248K/262K  Opus 5.5 proj

and, once the session is on a handoff thread, names it after the
directory::

    248K/262K  Opus 5.5 proj:auth-token

and turns amber at the handoff point, red once a handoff begun there
would have finished. Past that point it names the multiple of the
handoff point::

    310K/262K 1.2x over  Opus 5.5 proj

Notes
-----
- Claude Code has no plugin surface for a status line, so unlike the Stop
  hook this script cannot install itself. Point ``statusLine.command`` in
  settings.json at it; the project README gives the exact block.
- Both thresholds come from the state file the Stop hook writes each
  turn, so the gauge and the warning never disagree. Before the first
  Stop the line shows no gauge, since the line repaints continuously
  and reads no transcript to find the cycle's first call.
- Ordered by what has to survive truncation. A status line sharing a
  narrow pane is cut from the right, so the two numbers that carry the
  decision lead and the directory - which the shell prompt already shows
  - goes last. The thread slug sits last of all, on the directory it
  qualifies: it is the field a reader can most afford to lose.
- The slug comes from a one-line file ``hq`` writes when a session
  enters a thread, for the reason the thresholds come from the hook's
  state file.
"""

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import budget  # noqa: E402  (path must be set before this import resolves)

STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')

RESET = '\x1b[0m'
DIM = '\x1b[2m'
GREEN = '\x1b[32m'
YELLOW = '\x1b[33m'
RED = '\x1b[31m'


def session_state(session: str) -> tuple[int, int]:
    """Read the two thresholds the Stop hook stored for a session.

    Parameters
    ----------
    session : str
        Session id from the status line payload.

    Returns
    -------
    tuple[int, int]
        The handoff point and the escalation point, in tokens, or
        ``(0, 0)`` before the session's first Stop. A file with no
        escalation point takes the handoff point plus one handoff write.
    """
    safe = session.replace('/', '_')
    try:
        with open(os.path.join(STATE_DIR, f'{safe}.json')) as handle:
            state = json.load(handle)
        handoff = int(state.get('handoff') or 0)
        escalate = int(state.get('escalate') or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0, 0
    return handoff, escalate or handoff + budget.HANDOFF_WRITE_TOKENS


def session_thread(session: str) -> str:
    """Read the handoff thread a session is on, if it is on one.

    Parameters
    ----------
    session : str
        Session id from the status line payload.

    Returns
    -------
    str
        The slug ``hq`` recorded when this session last entered a
        thread, or '' when it is on none.

    Notes
    -----
    - The record is sticky across the turns between. A thread entered
      once holds the line until the session enters another or ``hq
      done`` finishes that one, which is what a reader glancing at
      the line wants to know.
    """
    safe = session.replace('/', '_')
    try:
        with open(os.path.join(STATE_DIR, f'{safe}.thread')) as handle:
            return handle.read().strip()
    except OSError:
        return ''


def render(payload: dict[str, Any]) -> str:
    """Build the status line from one status-line payload.

    Parameters
    ----------
    payload : dict[str, Any]
        The JSON object Claude Code writes to this command's stdin.

    Returns
    -------
    str
        A single line of ANSI-colored text, without a trailing newline.
    """
    window = payload.get('context_window') or {}
    context = int(window.get('total_input_tokens') or 0)
    model = payload.get('model') or {}
    label = str(model.get('display_name') or model.get('id') or '')
    workspace = payload.get('workspace') or {}
    # The project Claude Code started in, not the live shell cwd: a cd
    # into the handoff folder renames the field to the slug, and the
    # line then reads the thread twice and the project never.
    project = str(workspace.get('project_dir')
                  or workspace.get('current_dir') or payload.get('cwd') or '')
    session = str(payload.get('session_id') or '')
    where = os.path.basename(project) or project
    thread = session_thread(session)
    if thread:
        where = f'{where}:{thread}'
    tail = f'{DIM}{label} {where}{RESET}'
    handoff_at, escalate_at = session_state(session)
    if context <= 0 or handoff_at <= 0:
        return tail

    size = f'{context // 1000}K/{handoff_at // 1000}K'
    if context >= escalate_at:
        return f'{RED}{size} {context / handoff_at:.1f}x over{RESET}  {tail}'
    if context >= handoff_at:
        return f'{YELLOW}{size} handoff now{RESET}  {tail}'
    return f'{GREEN}{size}{RESET}  {tail}'


def main() -> int:
    """Print one status line for the payload on stdin.
    """
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    print(render(payload))
    return 0


if __name__ == '__main__':
    sys.exit(main())
