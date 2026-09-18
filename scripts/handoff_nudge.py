#!/usr/bin/env python3
"""UserPromptSubmit hook that asks for a handoff once context is spent.

The context-budget hook names the handoff point at the end of every
turn, but it names it to the user: a Stop hook's ``systemMessage``
reaches the terminal and never the model. So an agent works on past the
point it should have handed off, and only the user knows.

This hook puts the same fact in front of the agent, at the top of a
turn, while the sentinel ``~/.claude/.nudge-handoff`` exists.

Notes
-----
- The message asks for a handoff at the next stopping point, never at
  once. The agent plans the turn after this text arrives and before any
  work starts, which is the one moment an instruction redirects a turn
  without interrupting it. A Stop hook cannot say this: by the time
  Stop fires, the only stopping point left is now.
- Being past the point is a standing condition, not an event, so the
  message repeats every turn it holds and the hook keeps no state. A
  handoff written at 290K leaves a session at 295K and climbing, where
  asking again is right and ``hq begin`` opens the next cycle.
- The hook reads the sentinel by presence alone and never writes it, so
  ``hq nudge on`` and ``hq nudge off`` take effect at the next prompt.
  Nothing can stick armed.
- The hook measures context from the transcript rather than reading the
  context the budget hook stored. ``/clear`` and ``/compact`` both keep
  the session id and leave that stored figure behind them, so a
  remembered context asks a conversation holding nothing to hand off.
  The handoff point itself is latched per session and does not go
  stale, so it still comes from the stored state.
- A folder holding a live .hq.lock is inside an open cycle, so the
  agent is already doing what the message would ask. The age rule is
  hq's own: a lock its session abandoned stops silencing the nudge at
  the same two hours after which ``hq begin`` takes it over.
"""

import datetime
import json
import os
import pathlib
import sys
from typing import Any

try:
    # pytest and mutmut run from the repo root and import the package.
    from scripts import context_budget

    from bin import hq
except ImportError:
    # Script mode puts scripts/ on sys.path rather than the plugin root,
    # so reach each module by its own path. One branch or the other
    # binds both names, never both branches, so a test patching either
    # module patches the one the hook holds.
    here = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / 'bin'))
    sys.path.insert(0, str(here))
    import context_budget
    import hq

# hq owns the path so the verb that writes it and the hook that reads
# it cannot disagree.
SENTINEL = hq.SENTINEL


def open_cycle_above(start: pathlib.Path, now: str) -> bool:
    """Report whether a live cycle is open at or above a directory.

    Parameters
    ----------
    start : pathlib.Path
        Directory the search starts at, walking up through its parents.
    now : str
        Current ISO timestamp, against which a lock's age is measured.

    Returns
    -------
    bool
        True when the nearest handoff root holds a lock hq would still
        refuse, False when no root is found or its locks are dead.

    Notes
    -----
    - Only the nearest root is examined. A root further up belongs to an
      enclosing project whose cycle does not govern this one.
    - An unreadable lock counts as live, matching hq begin, which
      refuses one rather than guessing it is dead.
    """
    for candidate in [start, *start.parents]:
        folders = candidate / hq.HANDOFF_DIRNAME
        if next(folders.glob('*/cycles/manifest.tsv'), None) is None:
            continue
        for lock in folders.glob('*/.hq.lock'):
            age = hq._lock_age(hq._read_lock(lock.parent), now)
            if age is None or age < hq._TWO_HOURS:
                return True
        return False
    return False


def main() -> int:
    """Ask for a handoff when the sentinel is armed and the budget is spent.

    Returns
    -------
    int
        Always 0. Exit status 2 blocks the prompt, which no advisory
        message may do, and any other non-zero shows the user an error.
    """
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
        if not os.path.exists(SENTINEL):
            return 0
        session = str(payload.get('session_id') or 'unknown').replace('/', '_')
        state_path = os.path.join(context_budget.STATE_DIR, f'{session}.json')
        handoff_at = context_budget.load_state(state_path)[3]
        if not handoff_at:
            return 0
        context = context_budget.read_transcript(
            str(payload.get('transcript_path') or ''))[0]
        if context < handoff_at:
            return 0
        now = (os.environ.get('HQ_NOW')
               or datetime.datetime.now().isoformat(timespec='seconds'))
        start = pathlib.Path(str(payload.get('cwd') or ''))
        if open_cycle_above(start, now):
            return 0
    except (ValueError, AttributeError, TypeError, OSError):
        return 0

    message = (
        f"Context {context // 1000}K, past this session's"
        f' {handoff_at // 1000}K handoff point. Finish the work in flight,'
        ' then run /handoff at the next natural stopping point. Do not'
        ' interrupt a task for it, and do not start work that will not'
        ' fit in what is left.')
    json.dump({
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': message,
            }}, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
