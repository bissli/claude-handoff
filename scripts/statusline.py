#!/usr/bin/env python3
"""Status line showing the live context budget and what a turn now costs.

Claude Code hands this command a JSON payload on stdin whose
``context_window.total_input_tokens`` is the billed context of the last
API call: cache reads plus cache writes plus uncached input. That is the
number cost tracks, since every call re-reads the whole conversation.

The line reads::

    262K/350K [=======---] handoff in 2  $1.15/t  opus myproject

and, once the session is on a handoff thread, names it after the
directory::

    262K/350K [=======---] handoff in 2  $1.15/t  opus myproject:auth-token

and turns amber when it is time to write a handoff, red once the budget
is behind you, so the moment to hand off is visible several turns out.

Notes
-----
- Claude Code has no plugin surface for a status line, so unlike the Stop
  hook this script cannot install itself. Point ``statusLine.command`` in
  settings.json at it; the project README gives the exact block.
- The growth rate and both thresholds come from the state file the Stop
  hook writes each turn, so the gauge and the warning never disagree.
  Re-deriving them here would mean re-reading the transcript on a line
  that repaints continuously - and would re-derive them from a rate the
  hook has already latched, which is what must not move: a threshold
  computed fresh on every repaint counts down to a handoff, announces
  it, then reports room again.
- Every threshold comes from :mod:`budget`, shared with the hook, so the
  gauge cannot drift from the warning it is meant to anticipate.
- Ordered by what has to survive truncation. A status line sharing a
  narrow pane is cut from the right, so the two numbers that carry the
  decision lead and the directory - which the shell prompt already shows
  - goes last. The thread slug sits last of all, on the directory it
  qualifies: it is the field a reader can most afford to lose.
- The slug comes from a one-line file ``hq`` writes when a session
  enters a thread, for the reason the thresholds come from the hook's
  state file: the line repaints continuously and reads no transcript.
- Over budget the bar is replaced by the multiplier, not filled in. A
  saturated bar reads the same at 1.1x as at 3x, which is exactly the
  range where the reader needs to tell them apart.
"""

import json
import math
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

BAR_CELLS = 10

BAR_FILL = '='
BAR_TRACK = '-'


def session_state(session: str, tier: str) -> tuple[int, int, int]:
    """Read the growth rate and the two thresholds a session is held to.

    Parameters
    ----------
    session : str
        Session id from the status line payload.
    tier : str
        Price tier from ``budget.model_tier``.

    Returns
    -------
    tuple[int, int, int]
        Estimated tokens added per assistant call, the compaction target
        in tokens, and the context size at which to say hand off.

    Notes
    -----
    - Before the first Stop there is no state, so all three fall back to
      what the fallback growth rate justifies. The hook latches both
      thresholds down from those same figures, so the gauge can only
      ever tighten as state appears - it never jumps outward.
    - A threshold from an older state file is clamped to those figures
      too. The latches were added after some files were written, and
      those hold values they would never grant now.
    """
    safe = session.replace('/', '_')
    per_call, target, handoff = budget.FALLBACK_GROWTH_PER_CALL, 0, 0
    try:
        with open(os.path.join(STATE_DIR, f'{safe}.json')) as handle:
            state = json.load(handle)
        per_call = max(1, int(state['growth_per_call']))
        target = int(state.get('target') or 0)
        handoff = int(state.get('handoff') or 0)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    seed = budget.target_tokens(tier)
    target = min(target, seed) if target > 0 else seed
    point = target - budget.reserve_tokens(target, per_call)
    return per_call, target, min(handoff, point) if handoff > 0 else point


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
    plain = f'{DIM}{where}  {label}{RESET}'
    if context <= 0:
        return plain

    tier = budget.model_tier(str(model.get('id') or ''))
    if tier is None:
        return plain
    per_call, target, handoff_at = session_state(session, tier)
    per_turn = per_call * budget.CALLS_PER_TURN

    cost = budget.cost_per_turn(context, tier)
    size = f'{context // 1000}K/{target // 1000}K'
    tail = f'{DIM}~${cost:.2f}/t {tier} {where}{RESET}'

    if context >= target:
        # No bar past the budget: it would read the same at 1.1x as at
        # 3x, and telling those apart is the whole job from here on.
        return f'{RED}{size}  {context / target:.1f}x over{RESET}  {tail}'

    filled = int(context / target * BAR_CELLS)
    bar = BAR_FILL * filled + BAR_TRACK * (BAR_CELLS - filled)
    if context >= handoff_at:
        color, note = YELLOW, 'handoff now'
    else:
        left = math.ceil((handoff_at - context) / max(per_turn, 1))
        color, note = GREEN, f'handoff in {left}'
    return f'{color}{size} [{bar}] {note}{RESET}  {tail}'


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
