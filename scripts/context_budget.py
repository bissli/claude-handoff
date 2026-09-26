#!/usr/bin/env python3
"""Stop hook that warns once a handoff costs less than carrying on.

Claude Code resends the whole conversation on every API call, so even a
fully cached turn still bills the entire context at the cache-read rate.
Each call of work costs more than the one before it. A handoff resets the
context but pays a cycle's fixed overhead again: the resume, the write,
and the cache writes of the floor. The hook warns at the context where
the two balance, the handoff point of :func:`budget.handoff_point`,
priced from this cycle's own transcript.

Notes
-----
- Bands fire on ENTRY only. A Stop hook runs every turn, so repeating a
  band already reached would be noise. The highest band announced is kept
  in a per-session state file and rearmed when the context drops.
- Band 0 sits at the handoff point. Band 1 sits one handoff write's
  growth past it, where a handoff begun at band 0 would have finished,
  and raises a desktop notification.
- Once band 0 has been announced, both thresholds are latched in that
  same file and may fall, never rise. Context only grows, so a
  threshold that moves outward after a warning walks the gauge
  backwards, and the point rises with the measured growth rate, which
  swings when a session starts reading large files. A compaction
  releases both.
- Growth is measured from the context series itself, not by counting
  turns. Transcripts interleave real user prompts with injected system
  reminders, tool results, and hook output, and no reliable rule
  separates them; the context series has no such ambiguity.
- Only records that billed something are points on that series. A failed
  call is written as an assistant record too, and reading its zeroed
  usage as a context of zero is indistinguishable from a compaction.
- Sidechain records are skipped everywhere. They carry a subagent's
  context, not this session's.
- A subagent carries its own short-lived context and never compacts, so
  the hook exits silently when ``agent_id`` is present.
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import budget  # noqa: E402  (path must be set before this import resolves)

# Calls to average growth over. Long enough to survive one quiet stretch,
# short enough to react when a session starts reading large files.
GROWTH_WINDOW_CALLS = 60

STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')

OSC_NOTIFY = '\x1b]9;{}\x07'

# An hq invocation up to its verb, past any global flags ahead of it.
HQ_VERB = r'\bhq(?:\s+--(?:root|session)(?:=|\s+)\S+)*\s+'

HQ_OPEN = re.compile(HQ_VERB + r'open\b')

# A Bash command that could be reading the handoff back: it names the
# .handoff directory or a HANDOFF file by any path, or runs an hq query
# verb. open is listed with the query verbs, so a retried open stays
# inside the resume rather than ending it.
RESUME_COMMAND = re.compile(
    r'\.handoff|HANDOFF|' + HQ_VERB
    + r'(?:open|read|standing|artifacts|when|arc|list|help)\b')

# Tool names that never end a resume by themselves: reading a file,
# searching for a skill or a tool, is exploration, not new work.
RESUME_TOOLS = {'Read', 'Skill', 'ToolSearch'}


@dataclass
class Call:
    """One assistant API call, merged across the records that share its id.

    Attributes
    ----------
    billed : int
        Billed context: cache reads plus cache writes plus uncached input.
    written : int
        Of that, the tokens written to the cache.
    hour_written : int
        Cache-write tokens at the 1-hour TTL.
    minutes_written : int
        Cache-write tokens at the 5-minute TTL.
    tools : list[dict[str, Any]]
        The call's ``tool_use`` blocks, from every record of the call.
    """

    billed: int
    written: int
    hour_written: int
    minutes_written: int
    tools: list[dict[str, Any]] = field(default_factory=list)


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


def read_transcript(path: str) -> tuple[int, str, int, budget.Cycle | None]:
    """Read a transcript's current context, model, growth, and cycle.

    Parameters
    ----------
    path : str
        Absolute path to the session transcript, as handed to the hook.

    Returns
    -------
    tuple[int, str, int, budget.Cycle or None]
        Billed context of the last assistant call, that call's model id,
        the estimated tokens added per call, and the measurements of the
        cycle since the last compaction. Context is 0 and the cycle None
        when the transcript holds no usage record yet.

    Notes
    -----
    - Claude Code writes one API response as several records sharing a
      message id, one content block each. The cache counts are fixed
      when the request is sent, so the first record's counts stand for
      the call, while its tool uses are gathered from every record.
    - A call that never reached the model is written as an assistant
      record all the same, carrying a zeroed usage block and a
      placeholder model id. It billed nothing, so it is not a point on
      the context curve and is dropped whole - the model id included,
      which would otherwise overwrite the real one and silence the hook.
    - What separates such a record from a real one is where the counts
      are, never the error flag it may or may not carry: a real call
      puts them at the top level or under ``iterations``, a failed one
      has neither.
    - The cycle starts at the last drop ``main`` would read as a
      compaction. Its resume ends at j0, the first call after the
      cycle's first ``hq open`` whose tool uses are not all a Read, a
      Skill or ToolSearch call, or a Bash command naming ``.handoff``,
      ``HANDOFF``, or an hq read verb. A cycle with no ``hq open`` has
      j0 at its first call, and one still reading its handoff back has
      j0 at its last.
    - The TTL is the one that carried more of the cycle's cache writes.
      A record with no TTL breakdown counts toward neither, and a cycle
      with none prices its writes at ``budget.DEFAULT_ONE_HOUR``, the
      TTL Claude Code itself writes at.
    """
    calls: list[Call] = []
    by_id: dict[str, Call] = {}
    model = ''
    try:
        handle = open(path, errors='replace')
    except OSError:
        return 0, '', budget.FALLBACK_GROWTH_PER_CALL, None
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
            counts = usage
            billed = ((counts.get('cache_read_input_tokens') or 0)
                      + (counts.get('cache_creation_input_tokens') or 0)
                      + (counts.get('input_tokens') or 0))
            # Some records carry the counts one level down instead, and
            # only a call that never reached the model has them in
            # neither place.
            if not billed:
                counts = (usage.get('iterations') or [{}])[0]
                billed = ((counts.get('cache_read_input_tokens') or 0)
                          + (counts.get('cache_creation_input_tokens') or 0)
                          + (counts.get('input_tokens') or 0))
            # A record that billed nothing is a failed call, not a
            # smaller context. Left in the series it reads as a
            # compaction, and the run that restarts after it counts the
            # whole conversation as growth since zero - which triples
            # the measured rate for the rest of the session.
            if billed <= 0:
                continue
            identifier = message.get('id') or ''
            call = by_id.get(identifier) if identifier else None
            if call is None:
                ttl = counts.get('cache_creation') or {}
                call = Call(
                    billed=billed,
                    written=counts.get('cache_creation_input_tokens') or 0,
                    hour_written=ttl.get('ephemeral_1h_input_tokens') or 0,
                    minutes_written=ttl.get('ephemeral_5m_input_tokens') or 0)
                calls.append(call)
                if identifier:
                    by_id[identifier] = call
                model = message.get('model') or model
            call.tools.extend(
                block for block in message.get('content') or []
                if isinstance(block, dict) and block.get('type') == 'tool_use')
    if not calls:
        return 0, model, budget.FALLBACK_GROWTH_PER_CALL, None

    series = [call.billed for call in calls]
    start = 0
    for index in range(1, len(series)):
        if series[index - 1] - series[index] > budget.POST_COMPACTION_TOKENS // 2:
            start = index
    cycle = calls[start:]

    opened = False
    resume_end = 0
    for index, call in enumerate(cycle):
        inputs = [(tool.get('name'), tool.get('input') or {})
                  for tool in call.tools]
        commands = [str(given.get('command') or '')
                    for name, given in inputs if name == 'Bash']
        if not opened:
            opened = any(HQ_OPEN.search(command) for command in commands)
            resume_end = index if opened else 0
            continue
        resume_end = index
        reads = [
            name in RESUME_TOOLS
            or (name == 'Bash'
                and RESUME_COMMAND.search(str(given.get('command') or '')))
            for name, given in inputs]
        if not all(reads):
            break

    hour_written = sum(call.hour_written for call in cycle)
    minutes_written = sum(call.minutes_written for call in cycle)
    one_hour = (hour_written > minutes_written if hour_written
                or minutes_written else budget.DEFAULT_ONE_HOUR)
    measured = budget.Cycle(
        floor=cycle[0].billed,
        floor_written=cycle[0].written,
        resume_context=cycle[resume_end].billed,
        resume_sum=sum(call.billed for call in cycle[:resume_end + 1]),
        one_hour=one_hour)
    return series[-1], model, growth_per_call(series), measured


def compose(context: int, per_call: int, handoff_at: int,
            escalate_at: int) -> tuple[int, str]:
    """Pick the band for a context size and write its message.

    Parameters
    ----------
    context : int
        Current billed context in tokens.
    per_call : int
        Estimated tokens added per assistant call.
    handoff_at : int
        The latched handoff point, band 0, in tokens.
    escalate_at : int
        The latched escalation point, band 1, in tokens.

    Returns
    -------
    tuple[int, str]
        Band index and the message to show, or ``(-1, '')`` when the
        session is still short of the handoff point.

    Notes
    -----
    - Both thresholds are handed in rather than derived, so the bands
      move with the session's latched figures and not with the growth
      rate measured this turn. The rate printed is this turn's.
    """
    per_turn = per_call * budget.CALLS_PER_TURN
    now = f'{context // 1000}K'
    point = f'{handoff_at // 1000}K'
    rate = f'{int(per_turn) // 1000}K' if per_turn >= 1000 else '<1K'

    if context >= escalate_at:
        past = f'{(context - handoff_at) // 1000}K'
        return 1, (f'Context {now}, {past} past the {point} handoff point, '
                   f'where a handoff begun there would have finished. Every '
                   f'further turn raises the cost of each call of work. '
                   f'Run /handoff.')
    if context >= handoff_at:
        return 0, (f'Context {now}, at the {point} handoff point for this '
                   f'cycle, growing {rate} a turn - a good point to run '
                   f'/handoff.')
    return -1, ''


def latched(point: int, in_force: int) -> int:
    """Hold a session's threshold where it is, or lower, never higher.

    Parameters
    ----------
    point : int
        Threshold this turn's measurements justify, in tokens.
    in_force : int
        Threshold the session was held to last turn, 0 when it has none.

    Returns
    -------
    int
        Billed context in tokens.

    Notes
    -----
    - Down-only, because context only grows: a threshold that moves
      outward walks the gauge backwards, and the status line reports
      room again after the hook has already warned.
    - A threshold with no latch in force takes whatever this turn
      justifies.
    """
    return min(point, in_force) if in_force > 0 else point


def load_state(state_path: str) -> tuple[int, int, int, int]:
    """Read the band announced, the context seen, and both latches.

    Parameters
    ----------
    state_path : str
        Path to the per-session state file.

    Returns
    -------
    tuple[int, int, int, int]
        The stored band index, billed context, handoff point, and
        escalation point, or ``(-1, 0, 0, 0)`` when nothing is recorded.

    Notes
    -----
    - A key the file lacks reads as 0, no latch in force, and a key it
      carries that this version no longer writes is ignored. The band
      memory in the same file is what stops the one unlatched turn
      re-announcing a band.
    """
    try:
        with open(state_path) as handle:
            data = json.load(handle)
        return (int(data.get('band', -1)), int(data.get('context', 0)),
                int(data.get('handoff', 0)), int(data.get('escalate', 0)))
    except (OSError, ValueError, AttributeError, TypeError):
        return -1, 0, 0, 0


def store_state(state_path: str, band: int, per_call: int, handoff_at: int,
                escalate_at: int, context: int) -> None:
    """Record the band, growth rate, both latches, and context for a session.

    Parameters
    ----------
    state_path : str
        Path to the per-session state file.
    band : int
        Highest band to remember as announced, -1 for none.
    per_call : int
        Estimated tokens added per assistant call.
    handoff_at : int
        Latched handoff point, band 0, in tokens.
    escalate_at : int
        Latched escalation point, band 1, in tokens.
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

    context, model, per_call, cycle = read_transcript(transcript)
    if context <= 0 or cycle is None:
        return 0
    tier = budget.model_tier(model)
    if tier is None:
        return 0

    state_path = os.path.join(STATE_DIR, f'{session}.json')
    announced, last_context, handoff_held, escalate_held = load_state(
        state_path)
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
    # The latches hold only once band 0 has been announced. Before that
    # the point follows the live rate both ways, so a quiet stretch
    # early in a cycle cannot pin it low for the rest of the cycle.
    held = not compacted and announced >= 0
    handoff_at = latched(
        budget.handoff_point(cycle, per_call, tier),
        handoff_held if held else 0)
    escalate_at = latched(
        handoff_at + budget.handoff_write_tokens(per_call),
        escalate_held if held else 0)
    band, message = compose(context, per_call, handoff_at, escalate_at)
    # The stored band falls only when the context itself fell, never
    # because slowing growth lifted a threshold past the current
    # context, which would re-fire a band already announced.
    kept = band if compacted else max(band, announced)
    store_state(state_path, kept, per_call, handoff_at, escalate_at, context)
    if band <= announced or band < 0:
        return 0

    out: dict[str, str] = {'systemMessage': message}
    if band > 0:
        out['terminalSequence'] = OSC_NOTIFY.format(message)
    json.dump(out, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
