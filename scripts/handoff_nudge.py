#!/usr/bin/env python3
"""Prompt and tool-call hook that asks for a handoff once context is spent.

The context-budget hook names the handoff point at the end of every
turn, but it names it to the user: a Stop hook's ``systemMessage``
reaches the terminal and never the model. So an agent works on past the
point it should have handed off, and only the user knows.

This hook puts the same fact in front of the agent, at the top of a
turn, while the sentinel ``~/.claude/.nudge-handoff`` exists. Under
``hq loop`` it also runs after each main tool call, so a turn that never
ends still hears it, until a cycle is written.

Notes
-----
- The message asks for a handoff at the next stopping point, never at
  once. The agent plans the turn after this text arrives and before any
  work starts, which is the one moment an instruction redirects a turn
  without interrupting it. A Stop hook cannot say this: by the time
  Stop fires, the only stopping point left is now.
- The message makes the handoff conditional on work a later session
  must pick up. The end of a task is the most natural stopping point
  there is, so an unconditional request reads as an order to hand off
  a task already finished and committed.
- Being past the point is a standing condition, not an event, so the
  message repeats every turn it holds and the hook keeps no state. A
  handoff written at 290K leaves a session at 295K and climbing, where
  asking again is right and ``hq begin`` opens the next cycle.
- The hook reads the sentinel by presence alone and never writes it, so
  creating or removing the file takes effect at the next prompt.
  Nothing can stick armed.
- The hook measures context from the transcript rather than reading the
  context the budget hook stored. That figure dates from the last Stop,
  so it lags the turn in progress, and a ``/clear`` issues a new
  session id that has none.
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
    # pytest and mutmut run from the repo root, with src/ on sys.path
    # (pytest.ini).
    from handoff import storage
    from scripts import context_budget
except ImportError:
    # Notes:
    # - Script mode puts scripts/ on sys.path rather than the plugin
    #   root, so reach each module by its own path.
    # - One branch or the other binds context_budget, never both, so a
    #   test patching it patches the module the hook holds.
    # - ../src comes before ../bin, since mutmut writes its mutated copy
    #   there.
    here = pathlib.Path(__file__).resolve().parent
    sys.path[:0] = [str(here), str(here.parent / 'src'), str(here.parent / 'bin')]
    import context_budget
    from handoff import storage

# The README names this path as the switch, so moving it silently
# disarms every machine that created the file.
SENTINEL = os.path.expanduser('~/.claude/.nudge-handoff')


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
        folders = candidate / storage.HANDOFF_DIRNAME
        if next(folders.glob('*/cycles/manifest.tsv'), None) is None:
            continue
        for lock in folders.glob('*/.hq.lock'):
            age = storage.lock_age(storage.read_lock(lock.parent), now)
            if age is None or age < storage.TWO_HOURS:
                return True
        return False
    return False


def main() -> int:
    """Ask for a handoff once context passes the session's handoff point.

    Returns
    -------
    int
        Always 0. Exit status 2 blocks the prompt, which no advisory
        message may do, and any other non-zero shows the user an error.

    Notes
    -----
    - A PostToolUse payload speaks only under its own ``hq loop``, where
      ``$HQ_LOOP/slug`` names the thread, and ignores the sentinel. It
      stays silent for a subagent's call, inside an open cycle, and once
      the manifest's last cycle is above ``$HQ_LOOP/mark``.
    """
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
        loop = payload.get('hook_event_name') == 'PostToolUse'
        if loop:
            loop_dir = os.environ.get('HQ_LOOP')
            if not loop_dir or payload.get('agent_id'):
                return 0
            slug = (pathlib.Path(loop_dir) / 'slug').read_text().strip()
            root = os.environ.get('HQ_ROOT') or str(payload.get('cwd') or '')
            folder = pathlib.Path(root) / storage.HANDOFF_DIRNAME / slug
            if (folder / '.hq.lock').exists():
                return 0
            manifest = storage.read_tsv(
                folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
            mark = int((pathlib.Path(loop_dir) / 'mark').read_text())
            if manifest and int(manifest[-1]['cycle']) > mark:
                return 0
        elif not os.path.exists(SENTINEL):
            return 0
        session = str(payload.get('session_id') or 'unknown').replace('/', '_')
        state_path = os.path.join(context_budget.STATE_DIR, f'{session}.json')
        handoff_at = context_budget.load_state(state_path)[2]
        context, model, per_call, cycle = context_budget.read_transcript(
            str(payload.get('transcript_path') or ''))
        tier = context_budget.budget.model_tier(model)
        if loop and cycle is not None and tier is not None:
            # A /clear issues a new session id, which has no stored
            # point until its first Stop, and that turn may never end.
            handoff_at = context_budget.latched(
                context_budget.budget.handoff_point(cycle, per_call, tier),
                handoff_at)
        if not handoff_at:
            return 0
        if context < handoff_at:
            return 0
        if not loop:
            now = (os.environ.get('HQ_NOW')
                   or datetime.datetime.now().isoformat(timespec='seconds'))
            start = pathlib.Path(str(payload.get('cwd') or ''))
            if open_cycle_above(start, now):
                return 0
    except (ValueError, AttributeError, TypeError, OSError):
        return 0

    if loop:
        event = 'PostToolUse'
        message = (
            'Context is past the handoff point: finish the step in hand,'
            ' wait for every running agent to report, then run'
            f' /handoff write {slug}.')
    else:
        event = 'UserPromptSubmit'
        message = (
            f"Context {context // 1000}K, past this session's"
            f' {handoff_at // 1000}K handoff point. If work remains for a'
            ' later session - a task in flight, an edit not yet committed, an'
            ' open plan step, an open question, a decision or finding the'
            ' repo does not record - run /handoff at the next natural'
            ' stopping point. A task that is finished and committed, with'
            ' nothing left open or unrecorded, needs no handoff. Do not start'
            ' work that will not fit in what is left.')
    json.dump({
        'hookSpecificOutput': {
            'hookEventName': event,
            'additionalContext': message,
            }}, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
