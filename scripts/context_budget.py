#!/usr/bin/env python3
"""Stop hook that warns once a cycle has spent its work budget.

Claude Code resends the whole conversation on every API call, so each
call of work costs more than the one before it. The hook warns at the
handoff point of :func:`budget.handoff_point`, the cycle's first-call
context plus the work budget W.

Notes
-----
- Bands fire on ENTRY only. A Stop hook runs every turn, so repeating a
  band already reached would be noise. The highest band announced is kept
  in a per-session state file and rearmed when the context drops.
- Band 0 sits at the handoff point. Band 1 sits one handoff write past
  it, where a handoff begun at band 0 would have finished, and raises a
  desktop notification.
- Only records that billed something are points on the context series.
  A failed call is written as an assistant record too, and reading its
  zeroed usage as a context of zero is indistinguishable from a restart
  in place.
- Sidechain records are skipped everywhere. They carry a subagent's
  context, not this session's.
- A subagent carries its own short-lived context and never restarts in
  place, so the hook exits silently when ``agent_id`` is present.
"""

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import budget  # noqa: E402  (path must be set before this import resolves)

STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')

OSC_NOTIFY = '\x1b]9;{}\x07'


def read_transcript(path: str) -> tuple[int, int]:
    """Read a transcript's current context and its cycle's floor.

    Parameters
    ----------
    path : str
        Absolute path to the session transcript, as handed to the hook.

    Returns
    -------
    tuple[int, int]
        Billed context of the last assistant call, and of the first call
        since the last restart in place, F. Both are 0 when the
        transcript is missing or holds no billed call.

    Notes
    -----
    - A call that never reached the model is written as an assistant
      record all the same, carrying a zeroed usage block. What separates
      it from a real one is where the counts are: a real call puts them
      at the top level or under ``iterations``, a failed one has neither.
    - The cycle starts at the last drop ``main`` would read as a restart
      in place. A smaller dip, such as an expired cache block, stays
      inside the cycle.
    """
    series: list[int] = []
    try:
        handle = open(path, errors='replace')
    except OSError:
        return 0, 0
    with handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get('isSidechain'):
                continue
            message = entry.get('message') or {}
            usage = message.get('usage')
            if not usage or message.get('role') != 'assistant':
                continue
            billed = 0
            for counts in (usage, (usage.get('iterations') or [{}])[0]):
                billed = ((counts.get('cache_read_input_tokens') or 0)
                          + (counts.get('cache_creation_input_tokens') or 0)
                          + (counts.get('input_tokens') or 0))
                if billed:
                    break
            if billed > 0:
                series.append(billed)
    if not series:
        return 0, 0
    start = 0
    for index in range(1, len(series)):
        if series[index - 1] - series[index] > budget.RESTART_IN_PLACE_TOKENS // 2:
            start = index
    return series[-1], series[start]


def compose(context: int, handoff_at: int, escalate_at: int) -> tuple[int, str]:
    """Pick the band for a context size and write its message.

    Parameters
    ----------
    context : int
        Current billed context in tokens.
    handoff_at : int
        The handoff point, band 0, in tokens.
    escalate_at : int
        The escalation point, band 1, in tokens.

    Returns
    -------
    tuple[int, str]
        Band index and the message to show, or ``(-1, '')`` when the
        session is still short of the handoff point.
    """
    now = f'{context // 1000}K'
    point = f'{handoff_at // 1000}K'

    if context >= escalate_at:
        past = f'{(context - handoff_at) // 1000}K'
        return 1, (f'Context {now}, {past} past the {point} handoff point, '
                   f'where a handoff begun there would have finished. Every '
                   f'further turn raises the cost of each call of work. '
                   f'Run /handoff.')
    if context >= handoff_at:
        return 0, (f'Context {now}, at the {point} handoff point for this '
                   f'cycle - a good point to run /handoff.')
    return -1, ''


def load_state(state_path: str) -> tuple[int, int, int]:
    """Read the band announced, the context seen, and the handoff point.

    Parameters
    ----------
    state_path : str
        Path to the per-session state file.

    Returns
    -------
    tuple[int, int, int]
        The stored band index, billed context, and handoff point, or
        ``(-1, 0, 0)`` when nothing is recorded. A key the file lacks
        reads as its value in that default.
    """
    try:
        with open(state_path) as handle:
            data = json.load(handle)
        return (int(data.get('band', -1)), int(data.get('context', 0)),
                int(data.get('handoff', 0)))
    except (OSError, ValueError, AttributeError, TypeError):
        return -1, 0, 0


def store_state(state_path: str, band: int, handoff_at: int, escalate_at: int,
                context: int) -> None:
    """Record the band, both thresholds, and context for a session.

    Parameters
    ----------
    state_path : str
        Path to the per-session state file.
    band : int
        Highest band to remember as announced, -1 for none.
    handoff_at : int
        Handoff point, band 0, in tokens.
    escalate_at : int
        Escalation point, band 1, in tokens.
    context : int
        Billed context this record was written at.
    """
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, 'w') as handle:
            json.dump({
                'band': band,
                'handoff': handoff_at,
                'escalate': escalate_at,
                'context': context,
                }, handle)
    except OSError:
        pass


def main() -> int:
    """Announce the context band when a session first enters one.
    """
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
    except ValueError:
        return 0
    if payload.get('agent_id'):
        return 0
    transcript = payload.get('transcript_path') or ''
    if not transcript:
        return 0
    session = str(payload.get('session_id') or 'unknown').replace('/', '_')

    context, floor = read_transcript(transcript)
    if context <= 0:
        return 0

    state_path = os.path.join(STATE_DIR, f'{session}.json')
    announced, last_context, _ = load_state(state_path)
    # Notes:
    # - A restart in place is the one event that rearms the bands, so
    #   the test is a size and not just a fall.
    # - Billed context does fall without a restart in place - a cached
    #   block expiring, a tool result dropped from the window - so half
    #   of the restart-in-place drop is the size that separates the two,
    #   with room on both sides.
    restarted = last_context - context > budget.RESTART_IN_PLACE_TOKENS // 2
    handoff_at = budget.handoff_point(floor)
    escalate_at = handoff_at + budget.HANDOFF_WRITE_TOKENS
    band, message = compose(context, handoff_at, escalate_at)
    # The stored band falls only when the context itself fell, never
    # because a larger HQ_WORK_BUDGET lifted a threshold past the
    # current context, which would re-fire a band already announced.
    kept = band if restarted else max(band, announced)
    store_state(state_path, kept, handoff_at, escalate_at, context)
    if band <= announced or band < 0:
        return 0

    out: dict[str, str] = {'systemMessage': message}
    if band > 0:
        out['terminalSequence'] = OSC_NOTIFY.format(message)
    json.dump(out, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
