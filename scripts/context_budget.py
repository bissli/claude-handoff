#!/usr/bin/env python3
"""Stop hook that warns while there is still room to hand off and compact.

Claude Code resends the whole conversation on every API call, so even a
fully cached turn still bills the entire context at the cache-read rate.
Cost tracks the area under the context curve, and the only lever a user
has is where the session gets compacted.

Leaving is not instant. It takes a turn to write the handoff, and the
turn already in flight when the warning lands is gone too. So this hook
does not warn at a fixed token count. It warns when the room left before
the budget has shrunk to what those turns will consume, measured at the
rate this session is actually growing. A session reading large files
fills up several times faster than a conversation and is warned much
earlier in absolute tokens.

The budget itself is set in dollars per turn and converted to a token
target per model in :mod:`budget`, so an expensive model is held to a
proportionally shorter session rather than the same one.

Notes
-----
- Bands fire on ENTRY only. A Stop hook runs every turn, so repeating a
  band already reached would be noise. The highest band announced is kept
  in a per-session state file and rearmed when the context drops.
- The target is latched in that same file and may fall within a session,
  never rise. It is derived from the measured growth rate, and that rate
  triples when a session starts reading large files, so an unlatched
  target outruns the context it is measuring: a session already shown as
  over budget is handed more room and told it is fine.
- Growth is measured from the context series itself, not by counting
  turns. Transcripts interleave real user prompts with injected system
  reminders, tool results, and hook output, and no reliable rule
  separates them; the context series has no such ambiguity.
- Only records that billed something are points on that series. A failed
  call is written as an assistant record too, and reading its zeroed
  usage as a context of zero is indistinguishable from a compaction.
- Both thresholds are latched down-only, for the same reason: context
  only grows, so one that moves outward walks the gauge backwards.
- A subagent carries its own short-lived context and never compacts, so
  the hook exits silently when ``agent_id`` is present.
"""

import json
import math
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import budget  # noqa: E402  (path must be set before this import resolves)

# Calls to average growth over. Long enough to survive one quiet stretch,
# short enough to react when a session starts reading large files.
GROWTH_WINDOW_CALLS = 60

STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')

OSC_NOTIFY = '\x1b]9;{}\x07'


def growth_per_call(series: list[int]) -> int:
    """Estimate how many tokens each assistant call adds to the context.

    Parameters
    ----------
    series : list[int]
        Billed context of each assistant call, in order.

    Returns
    -------
    int
        Mean tokens added per call over the most recent unbroken run of
        growth, or ``budget.FALLBACK_GROWTH_PER_CALL`` when the series is
        too short to measure.

    Notes
    -----
    - The series is cut at a compaction, the drop ``main`` tests for:
      averaging across one reads as near-zero growth, which would
      silence the warning exactly where it matters most. A smaller dip,
      such as an expired cache block, stays in the run.
    - The mean is right here rather than the median: what matters is how
      fast the context fills, and one 40K tool result fills it just as
      surely as forty small ones.
    """
    window = series[-GROWTH_WINDOW_CALLS:]
    run: list[int] = []
    for value in window:
        if run and run[-1] - value > budget.POST_COMPACTION_TOKENS // 2:
            run = []
        run.append(value)
    if len(run) < 5 or run[-1] <= run[0]:
        return budget.FALLBACK_GROWTH_PER_CALL
    return max(1, (run[-1] - run[0]) // (len(run) - 1))


def read_transcript(path: str) -> tuple[int, str, int]:
    """Read a transcript's current context, model, and growth rate.

    Parameters
    ----------
    path : str
        Absolute path to the session transcript, as handed to the hook.

    Returns
    -------
    tuple[int, str, int]
        Billed context of the last assistant call, that call's model id,
        and the estimated tokens added per call. Context is 0 when the
        transcript holds no usage record yet.

    Notes
    -----
    - Claude Code writes one API response as several progressive stream
      snapshots sharing a message id, with output growing across them.
      The cache counts are fixed when the request is sent, so keeping
      the first snapshot is safe for context even though it understates
      output.
    - A call that never reached the model is written as an assistant
      record all the same, carrying a zeroed usage block and a
      placeholder model id. It billed nothing, so it is not a point on
      the context curve and is dropped whole - the model id included,
      which would otherwise overwrite the real one and silence the hook.
    - What separates such a record from a real one is where the counts
      are, never the error flag it may or may not carry: a real call
      puts them at the top level or under ``iterations``, a failed one
      has neither.
    """
    series: list[int] = []
    model = ''
    seen: set[str] = set()
    try:
        handle = open(path, errors='replace')
    except OSError:
        return 0, '', budget.FALLBACK_GROWTH_PER_CALL
    with handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get('message') or {}
            usage = message.get('usage')
            if not usage or message.get('role') != 'assistant':
                continue
            billed = ((usage.get('cache_read_input_tokens') or 0)
                      + (usage.get('cache_creation_input_tokens') or 0)
                      + (usage.get('input_tokens') or 0))
            # Some records carry the counts one level down instead, and
            # only a call that never reached the model has them in
            # neither place.
            inner = (usage.get('iterations') or [{}])[0]
            if not billed:
                billed = ((inner.get('cache_read_input_tokens') or 0)
                          + (inner.get('cache_creation_input_tokens') or 0)
                          + (inner.get('input_tokens') or 0))
            # A record that billed nothing is a failed call, not a
            # smaller context. Left in the series it reads as a
            # compaction, and the run that restarts after it counts the
            # whole conversation as growth since zero - which triples
            # the measured rate for the rest of the session.
            if billed <= 0:
                continue
            identifier = message.get('id') or ''
            if identifier and identifier in seen:
                continue
            if identifier:
                seen.add(identifier)
            series.append(billed)
            model = message.get('model') or model
    if not series:
        return 0, model, budget.FALLBACK_GROWTH_PER_CALL
    return series[-1], model, growth_per_call(series)


def compose(context: int, tier: str, per_call: int, target: int,
            handoff_at: int) -> tuple[int, str]:
    """Pick the band for a context size and write its message.

    Parameters
    ----------
    context : int
        Current billed context in tokens.
    tier : str
        Price tier from ``budget.model_tier``.
    per_call : int
        Estimated tokens added per assistant call.
    target : int
        The compaction target in force for this session, in tokens.
    handoff_at : int
        The context size at which to warn, from :func:`latched_handoff`.

    Returns
    -------
    tuple[int, str]
        Band index and the message to show, or ``(-1, '')`` when the
        session still has room and nothing needs saying.

    Notes
    -----
    - Both thresholds are handed in rather than derived, so the bands
      move with the session's latched figures and not with the growth
      rate measured this turn.
    - A floor-inflated target can equal the over boundary. Band 1 is
      then never entered, so the band-2 message also names /handoff.
    - The growth rate still shapes the reserve and both countdowns.
      Those are estimates of how much room is left and are meant to
      react; only the thresholds have to hold still.
    """
    over = budget.over_budget_tokens(tier, target)
    per_turn = per_call * budget.CALLS_PER_TURN
    cost = budget.cost_per_turn(context, tier)
    now = f'{context // 1000}K'
    goal = f'{target // 1000}K'
    rate = f'{int(per_turn) // 1000}K' if per_turn >= 1000 else '<1K'

    if context >= over:
        return 2, (f'Context {now}, about ${cost:.2f} a turn - '
                   f'{cost / budget.COST_PER_TURN_TARGET:.1f}x the '
                   f'${budget.COST_PER_TURN_TARGET:.2f} target. Every '
                   f'further turn pays to re-read history you are not '
                   f'using. Run /handoff.')
    if context >= target:
        cycle = (target - budget.POST_COMPACTION_TOKENS) / max(per_turn, 1)
        return 1, (f'Context {now}, at the {goal} budget. Run /handoff, '
                   f'then run it again in a fresh session to read it '
                   f'back: that restarts near '
                   f'{budget.FRESH_SESSION_TOKENS // 1000}K plus the file, '
                   f'against {budget.POST_COMPACTION_TOKENS // 1000}K for '
                   f'/compact. Compact instead only to carry the tail of this '
                   f'conversation, which buys about {cycle:.0f} more turns.')
    if context >= handoff_at:
        left = math.ceil((target - context) / max(per_turn, 1))
        turns = 'turn' if left == 1 else 'turns'
        return 0, (f'Context {now} of a {goal} budget, growing {rate} a turn. '
                   f'About {left} {turns} of room left - a good point to '
                   f'run /handoff.')
    return -1, ''


def latched_target(tier: str, per_call: int, in_force: int) -> int:
    """Hold a session's target where it is, or lower, never higher.

    Parameters
    ----------
    tier : str
        Price tier from ``budget.model_tier``.
    per_call : int
        Estimated tokens added per assistant call.
    in_force : int
        Target this session was held to last turn, 0 when it has none.

    Returns
    -------
    int
        Billed context in tokens.

    Notes
    -----
    - Context only grows, so a rising target walks the gauge backwards.
      A burst of large tool results lifts the measured growth rate, the
      cycle floor lifts the target with it, and a session already past
      the budget is shown as having room again.
    - Raising the target is also backwards on cost, which is what the
      budget is for. A fast-growing session is the expensive one, and
      following its rate up hands it the most room of all.
    - A session with no target yet starts at the one the fallback rate
      justifies. That is what the status line already shows before the
      first Stop writes any state, so starting anywhere higher would
      itself be a rise on the first turn.
    """
    seed = budget.target_tokens(tier)
    ceiling = min(in_force, seed) if in_force > 0 else seed
    return min(budget.target_tokens(tier, per_call), ceiling)


def latched_handoff(target: int, per_call: int, in_force: int) -> int:
    """Hold a session's handoff point where it is, or lower, never higher.

    Parameters
    ----------
    target : int
        The compaction target in force for this session, in tokens.
    per_call : int
        Estimated tokens added per assistant call.
    in_force : int
        Handoff point this session was held to last turn, 0 when it has
        none.

    Returns
    -------
    int
        Billed context in tokens.

    Notes
    -----
    - Latching the target alone is not enough. The reserve below it
      follows the growth rate, and that rate swings by a factor of three
      within a session, so the point the gauge counts down to walks
      outward on its own - the status line announces the handoff, then
      reports several turns of room again on the next repaint, and
      contradicts the warning the hook has already given.
    - Down-only, for the reason the target is: context only grows, so a
      threshold that moves outward walks the gauge backwards.
    - A session with no latch yet takes whatever the current rate
      justifies. There is nothing to walk back from on the first turn.
    """
    point = target - budget.reserve_tokens(target, per_call)
    return min(point, in_force) if in_force > 0 else point


def load_state(state_path: str) -> tuple[int, int, int, int]:
    """Read the band announced, the context seen, and the target in force.

    Parameters
    ----------
    state_path : str
        Path to the per-session state file.

    Returns
    -------
    tuple[int, int, int, int]
        The stored band index, billed context, target, and handoff
        point, or ``(-1, 0, 0, 0)`` when nothing is recorded.

    Notes
    -----
    - A file written before the handoff point was latched has no such
      key, and reads as no latch in force. The band memory in the same
      file is what stops that one free turn re-announcing a band.
    """
    try:
        with open(state_path) as handle:
            data = json.load(handle)
        return (int(data.get('band', -1)), int(data.get('context', 0)),
                int(data.get('target', 0)), int(data.get('handoff', 0)))
    except (OSError, ValueError, AttributeError, TypeError):
        return -1, 0, 0, 0


def store_state(state_path: str, band: int, per_call: int, target: int,
                handoff_at: int, context: int) -> None:
    """Record the band, growth rate, target, and context for a session.

    Parameters
    ----------
    state_path : str
        Path to the per-session state file.
    band : int
        Highest band to remember as announced, -1 for none.
    per_call : int
        Estimated tokens added per assistant call.
    target : int
        Compaction target derived from that growth rate.
    handoff_at : int
        Context size at which this session is told to hand off.
    context : int
        Billed context this record was written at.

    Returns
    -------
    None
    """
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, 'w') as handle:
            json.dump({
                'band': band,
                'growth_per_call': per_call,
                'target': target,
                'handoff': handoff_at,
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

    context, model, per_call = read_transcript(transcript)
    if context <= 0:
        return 0
    tier = budget.model_tier(model)
    if tier is None:
        return 0

    state_path = os.path.join(STATE_DIR, f'{session}.json')
    announced, last_context, in_force, handoff_held = load_state(state_path)
    # Notes:
    # - A compaction is the one event that resets a session: it rearms
    #   the bands and releases both latches, so the cycle that follows
    #   is measured on its own terms. Everything here turns on telling
    #   one from a dip, which is why the test is a size and not just a
    #   fall.
    # - Billed context does fall without a compaction - a cached block
    #   expiring, a tool result dropped from the window. Across 301
    #   real drops the smallest true compaction freed 72,591 tokens and
    #   the largest dip 59,611, so half of where a compaction restarts
    #   separates them with room on both sides.
    compacted = last_context - context > budget.POST_COMPACTION_TOKENS // 2
    target = latched_target(tier, per_call, 0 if compacted else in_force)
    handoff_at = latched_handoff(target, per_call,
                                 0 if compacted else handoff_held)
    band, message = compose(context, tier, per_call, target, handoff_at)
    # The stored band falls only when the context itself fell, never
    # because slowing growth lifted a threshold past the current
    # context, which would re-fire a band already announced.
    kept = band if compacted else max(band, announced)
    store_state(state_path, kept, per_call, target, handoff_at, context)
    if band <= announced or band < 0:
        return 0

    out: dict[str, str] = {'systemMessage': message}
    if band > 0:
        out['terminalSequence'] = OSC_NOTIFY.format(message)
    json.dump(out, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
