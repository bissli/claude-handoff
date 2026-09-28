"""Tests for the context-budget Stop hook."""

import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)

SPEC = importlib.util.spec_from_file_location(
    'context_budget', os.path.join(SCRIPTS, 'context_budget.py'))
cb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cb)

from itertools import starmap

import budget  # noqa: E402  (path must be set before this import resolves)
import statusline as sl  # noqa: E402  (same)

# A cycle climbing 10,000 tokens a call from a 70,000 floor, on Opus 5.5
# at the 1-hour write price with nothing written on the first call: the
# handoff point is 376,209 and the escalation point 405,209.
CLIMB = [70_000 + 10_000 * step for step in range(90)]


def _cycle(resume_context=80_000, resume_sum=700_000, resume_output=3_000,
           one_hour=True):
    """Build the worked cycle: F 62,000 with 50,000 written.
    """
    return budget.Cycle(floor=62_000, floor_written=50_000,
                        resume_context=resume_context, resume_sum=resume_sum,
                        resume_output=resume_output, one_hour=one_hour)


def test_the_handoff_point_reproduces_the_worked_values():
    """Verify H* = C0 + sqrt(2 g A) on hand-computed cycles.

    Mutation: any dropped or rescaled term of A - the resume sum S0, the
    write's w (C0 + W/2), the cache writes priced at m rather than
    m - 1, the output r (Or + Ow) left out or priced at m - or the
    factor 2 under the root.
    Oracle: hand-computed with F 62,000, Fw 50,000, g 2,300, Or 3,000,
    w 17.6, W 29,000, Ow 20,000. On Opus 5.5 at the 1-hour TTL, m 40
    and r 100: at C0 80,000 and S0 700,000, A = 700,000 + 17.6 * 94,500
    + 39 * 97,000 + 100 * 23,000 = 8,446,200 and sqrt(4,600 A) =
    197,110.43, so H* = 277,110; likewise 392,350 at C0 150,000 with S0
    1,060,000, and 469,918 at C0 200,000 with S0 1,300,000. On Fable
    5.1 at the 5-minute TTL, m 50 and r 200, the first cycle has A =
    11,716,200 and H* = 312,152.
    """
    assert budget.write_read_ratio('opus-5-5', True) == 40
    for resume_context, resume_sum, point in ((80_000, 700_000, 277_110),
                                              (150_000, 1_060_000, 392_350),
                                              (200_000, 1_300_000, 469_918)):
        cycle = _cycle(resume_context, resume_sum)
        assert budget.handoff_point(cycle, 2_300, 'opus-5-5') == point
    assert budget.handoff_point(
        _cycle(one_hour=False), 2_300, 'fable-5-1') == 312_152


def test_the_handoff_write_does_not_grow_with_the_work_rate():
    """Verify the room past the resume grows as the square root of g.

    Mutation: sizing the handoff write as w * g, the session's own work
    rate. A then grows with g, and a session reading large files is told
    its two-turn write adds hundreds of thousands of tokens, which runs
    its point out toward the window.
    Oracle: a relation - A does not depend on g, so quadrupling g must
    double H* - C0, to within the rounding of H* to a token.
    """
    for tier in ('opus-5-5', 'fable-5-1'):
        slow = budget.handoff_point(_cycle(), 2_000, tier) - 80_000
        fast = budget.handoff_point(_cycle(), 8_000, tier) - 80_000
        assert abs(fast - 2 * slow) <= 2


def test_output_tokens_bill_at_the_output_price():
    """Verify each resume output token adds r, the output price over the
    cache-read price, to A.

    Mutation: leaving output out of A, which places every point early;
    or pricing it at the base input price, or at the write ratio m.
    Oracle: the published per-MTok prices - output at $20 against a
    $0.20 read on Opus 5.5 is r 100, and $50 against $0.25 on Fable 5.1
    is r 200. Adding 10,000 resume output tokens must raise
    (H* - C0)**2 / (2 g), which is A, by r * 10,000, to within the
    rounding of H* to a token.
    """
    for tier, ratio in (('opus-5-5', 100), ('fable-5-1', 200)):
        rooms = [budget.handoff_point(_cycle(resume_output=output), 2_300,
                                      tier) - 80_000
                 for output in (0, 10_000)]
        added = (rooms[1] ** 2 - rooms[0] ** 2) / (2 * 2_300)
        assert abs(added - ratio * 10_000) < 200


def test_the_point_leaves_a_whole_write_inside_the_window():
    """Verify H* stops one handoff write short of Claude Code's own
    compaction point.

    Mutation: dropping the cap, so a fast session's point lands where
    Claude Code has already compacted it; or capping at the 1M window,
    or at the compaction point itself, where a write begun at the point
    is cut off.
    Oracle: boundary straddle on Fable 5.1 at the 1-hour TTL, where the
    worked cycle has A = 14,626,200. At g 25,100 the raw point is
    80,000 + sqrt(50,200 A) = 936,875, under the 938,000 cap, and stands;
    at g 25,300 it is 940,282, over the cap and under both the window and
    the compaction point, and must read 967,000 - 29,000 = 938,000.
    """
    assert budget.handoff_point(_cycle(), 25_100, 'fable-5-1') == 936_875
    assert budget.handoff_point(_cycle(), 25_300, 'fable-5-1') == 938_000


def test_a_longer_resume_earns_a_longer_cycle():
    """Verify H* and the work room past the resume both rise with C0.

    Mutation: anchoring H* at the floor F instead of the resume end C0,
    so every resume token comes out of the work room - the micro-cycle
    defect, where each handoff's longer resume shortens the next cycle.
    Oracle: monotonicity - with F, Fw, S0, g, and m held fixed, both
    H* and H* - C0 must strictly increase across rising C0, since A
    rises by w + m - 1 per resume token.
    """
    points = []
    for resume_context in (62_000, 80_000, 150_000, 200_000, 300_000):
        cycle = _cycle(resume_context)
        points.append((resume_context,
                       budget.handoff_point(cycle, 2_300, 'opus-5-5')))
    assert [point for _, point in points] == sorted(
        {point for _, point in points})
    rooms = [point - resume_context for resume_context, point in points]
    assert rooms == sorted(set(rooms))


def test_the_cache_ttl_picks_the_write_price_ratio(tmp_path):
    """Verify m is the published write price over the read price, by TTL.

    Mutation: swapping the 1-hour and 5-minute multipliers, pricing a
    write off the cache-read price or off a family's read discount
    rather than the tier's own base and read prices, or swapping the
    two breakdown keys.
    Oracle: the pricing page's per-MTok rows - Opus 5.5 writes at $5
    (5m) and $8 (1h) against a $0.20 read, Fable 5.1 at $12.50 and $20
    against $0.25, Opus 5 at $6.25 and $10 against $0.50, Sonnet 5 at
    $2.50 and $4 against $0.20; and a transcript whose writes are
    1-hour must read as one_hour, a 5-minute one must not.
    """
    for tier, five_minute, one_hour, read in (
            ('opus-5-5', 5.00, 8.00, 0.20),
            ('fable-5-1', 12.50, 20.00, 0.25),
            ('fable', 12.50, 20.00, 1.00),
            ('opus', 6.25, 10.00, 0.50),
            ('sonnet-5', 2.50, 4.00, 0.20),
            ('sonnet', 3.75, 6.00, 0.30)):
        assert abs(budget.write_read_ratio(tier, False) - five_minute / read) \
            < 1e-9
        assert abs(budget.write_read_ratio(tier, True) - one_hour / read) \
            < 1e-9
    path = tmp_path / 's.jsonl'
    for ttl, expected in (('1h', True), ('5m', False)):
        records = []
        for index, value in enumerate(CLIMB[:6]):
            records.extend(_call(index, value, written=10_000, ttl=ttl))
        path.write_text('\n'.join(json.dumps(r) for r in records))
        assert cb.read_transcript(str(path))[3].one_hour is expected


def test_the_resume_ends_at_the_first_call_that_does_other_work(tmp_path):
    """Verify j0 is the first call after hq open that is not a handoff read.

    Mutation: ending the resume at the first hq read or at the call
    after hq open; counting a sidechain record, whose Edit ends the
    resume early and whose context reads as a restart in place; taking
    a call's tool uses from its first record only, which hides the Edit
    written in the second; or summing a call's output over its records,
    each of which repeats the same count.
    Oracle: hand-built transcript - F 62,000 with 50,000 written, hq
    open behind --root and --session flags, a Read, hq read and hq
    standing, a sed of a .handoff path, then a call whose second record
    is an Edit. C0 is that call's 84,000, S0 is 62,000 + 64,000 +
    70,000 + 78,000 + 84,000 = 358,000, and Or is the output of the four
    calls before it, 100 + 200 + 300 + 400 = 1,000.
    """
    records = (
        _call(0, 62_000, [('Skill', {'skill': 'handoff'})], written=50_000,
              output=100)
        + _call(1, 64_000, [_bash('cd /w && hq --root /w --session s1 '
                                  'open auth-token')], output=200)
        + _call(2, 70_000, [('Read', {
            'file_path': '/w/.handoff/auth-token/HANDOFF.md'})], output=300)
        + _call(3, 78_000, [_bash('hq read auth-token specs/plan.md'),
                            _bash('hq standing auth-token')], output=400)
        + _call('side', 300_000, [('Edit', {'file_path': '/w/a.py'})],
                sidechain=True, output=9_000)
        + _call(4, 84_000, [_bash('sed -n 1,40p .handoff/auth-token/x.md'),
                            ('Edit', {'file_path': '/w/src/app.py'})],
                output=500)
        + _call(5, 90_000, [_bash('pytest -q')], output=600)
        + _call(6, 96_000, [_bash('hq read auth-token specs/plan.md')]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    context, _, _, cycle = cb.read_transcript(str(path))
    assert context == 96_000
    assert cycle == budget.Cycle(floor=62_000, floor_written=50_000,
                                 resume_context=84_000, resume_sum=358_000,
                                 resume_output=1_000, one_hour=True)


def test_a_restart_in_place_restarts_the_floor_and_the_resume(tmp_path):
    """Verify the cycle after a restart in place starts its own measure.

    Mutation: scanning the whole transcript as one cycle, so the floor
    and the resume stay those of the first cycle; or carrying the first
    cycle's hq open across the drop.
    Oracle: hand-built transcripts. A first cycle opens a handoff and
    climbs to 300,000, then drops to 150,000 with 120,000 written. With
    no open after the drop, C0 = S0 = F = 150,000; with an open at the
    second call, C0 is the Edit's 170,000 and S0 is 150,000 + 152,000 +
    160,000 + 170,000 = 632,000.
    """
    first = (_call(0, 62_000, written=50_000)
             + _call(1, 64_000, [_bash('hq open auth-token')])
             + _call(2, 70_000, [('Read', {
                 'file_path': '.handoff/auth-token/HANDOFF.md'})])
             + _call(3, 90_000, [('Edit', {'file_path': 'a.py'})])
             + _call(4, 300_000, [_bash('pytest -q')]))
    plain = (_call(5, 150_000, written=120_000)
             + _call(6, 155_000, [('Edit', {'file_path': 'a.py'})])
             + _call(7, 160_000, [_bash('pytest -q')]))
    opened = (_call(5, 150_000, written=120_000)
              + _call(6, 152_000, [_bash('hq open auth-token')])
              + _call(7, 160_000, [_bash('hq arc auth-token')])
              + _call(8, 170_000, [('Edit', {'file_path': 'a.py'})]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in first + plain))
    assert cb.read_transcript(str(path))[3] == budget.Cycle(
        floor=150_000, floor_written=120_000, resume_context=150_000,
        resume_sum=150_000, resume_output=0, one_hour=True)
    path.write_text('\n'.join(json.dumps(r) for r in first + opened))
    assert cb.read_transcript(str(path))[3] == budget.Cycle(
        floor=150_000, floor_written=120_000, resume_context=170_000,
        resume_sum=632_000, resume_output=0, one_hour=True)


def test_the_resume_covers_ls_grep_cd_and_a_persisted_read(tmp_path):
    """Verify each resume shape the marker must recognize keeps j0 open.

    Mutation: dropping any one shape from the resume test - an ls of
    .handoff/, a grep or wc of a HANDOFF file, a cd into .handoff/...
    with a relative path, a Read of a persisted tool-results file, or
    either drift check hq open prints, git log from the handoff's sha
    to HEAD and git status --porcelain - which would end the resume at
    that call instead of the pytest after it.
    Oracle: hand-built j0 - the pytest call is the first whose tool use
    is not a resume shape, so C0 is its 126,000 and S0 is the ten calls
    up to and including it: 62,000 + 64,000 + 70,000 + 78,000 + 86,000 +
    94,000 + 102,000 + 110,000 + 118,000 + 126,000 = 910,000.
    """
    records = (
        _call(0, 62_000, written=50_000)
        + _call(1, 64_000, [_bash('hq open auth-token')])
        + _call(2, 70_000, [_bash('ls .handoff/auth-token/')])
        + _call(3, 78_000, [_bash('grep -c "##" .handoff/auth-token/'
                                  'HANDOFF.md')])
        + _call(4, 86_000, [_bash('wc -l HANDOFF.md')])
        + _call(5, 94_000, [_bash('cd .handoff/auth-token && '
                                  'cat notes/x.md')])
        + _call(6, 102_000, [('Read', {
            'file_path': '/tmp/scratch/tool-results/abc.txt'})])
        + _call(7, 110_000, [_bash('git log --oneline 1a2b3c4..HEAD')])
        + _call(8, 118_000, [_bash('git status --porcelain')])
        + _call(9, 126_000, [_bash('pytest -q')]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    _, _, _, cycle = cb.read_transcript(str(path))
    assert cycle == budget.Cycle(floor=62_000, floor_written=50_000,
                                 resume_context=126_000, resume_sum=910_000,
                                 resume_output=0, one_hour=True)


def test_only_the_drift_check_log_stays_in_the_resume(tmp_path):
    """Verify a git log that is not the drift check ends the resume.

    Mutation: a drift pattern loose enough to take any git log, or any
    git status, so the owner's own look at history reads as resume.
    Oracle: boundary straddle - after hq open, git log --oneline -5, a
    plain git status, and a git log chained to a later diff to HEAD each
    end the resume at their own call, 70,000, where git log --oneline
    1a2b3c4..HEAD would not.
    """
    path = tmp_path / 's.jsonl'
    for command in ('git log --oneline -5', 'git status',
                    'git log --oneline -5 && git diff 1a2b3c4..HEAD'):
        records = (_call(0, 62_000, written=50_000)
                   + _call(1, 64_000, [_bash('hq open auth-token')])
                   + _call(2, 70_000, [_bash(command)])
                   + _call(3, 76_000, [_bash('pytest -q')]))
        path.write_text('\n'.join(json.dumps(r) for r in records))
        assert cb.read_transcript(str(path))[3].resume_context == 70_000


def test_a_persons_prompt_ends_the_resume(tmp_path):
    """Verify the first call answering a typed prompt after hq open is j0.

    Mutation: ignoring user records, so the owner's go-ahead and the
    Read it asks for stay inside the resume; taking a task notification
    for a typed prompt, which ends the resume a call early; or taking a
    sidechain's prompt for the session's.
    Oracle: hand-built transcript - a typed /handoff command, Skill, hq
    open, and a Read; then a task notification, a sidechain prompt, and
    another Read; then the owner's typed "go ahead" and a Read of a
    .handoff file. The call answering the prompt is j0: C0 is its
    90,000 and S0 is 62,000 + 64,000 + 70,000 + 80,000 + 90,000 =
    366,000.
    """
    records = (
        [_prompt('<command-message>claude-handoff:handoff</command-message>')]
        + _call(0, 62_000, [('Skill', {'skill': 'handoff'})], written=50_000)
        + _call(1, 64_000, [_bash('hq open auth-token')])
        + _call(2, 70_000, [('Read', {
            'file_path': '/w/.handoff/auth-token/HANDOFF.md'})])
        + [_prompt('<task-notification>done</task-notification>',
                   kind='task-notification'),
           _prompt('a subagent brief', sidechain=True)]
        + _call(3, 80_000, [('Read', {
            'file_path': '/w/.handoff/auth-token/notes.md'})])
        + [_prompt('go ahead')]
        + _call(4, 90_000, [('Read', {
            'file_path': '/w/.handoff/auth-token/plan.md'})])
        + _call(5, 96_000, [_bash('pytest -q')]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    _, _, _, cycle = cb.read_transcript(str(path))
    assert (cycle.resume_context, cycle.resume_sum) == (90_000, 366_000)


def test_a_session_opened_for_its_handoff_keeps_its_whole_resume(tmp_path):
    """Verify the call answering the /handoff command starts the resume,
    whatever else it runs.

    Mutation: requiring that call to read back too, so a first call that
    loads the skill and runs a memman recall beside it counts as earlier
    work, and its growth comes out of C0 and S0, which puts the point
    early.
    Oracle: hand-built transcript - the typed /handoff command, a call
    at 62,000 running Skill and memman recall, hq open at 80,000, a Read
    at 90,000, and an Edit at 100,000. Nothing comes out: C0 is 100,000
    and S0 is 62,000 + 80,000 + 90,000 + 100,000 = 332,000.
    """
    records = (
        [_prompt('<command-message>claude-handoff:handoff</command-message>')]
        + _call(0, 62_000, [('Skill', {'skill': 'handoff'}),
                            _bash('memman recall "auth token"')],
                written=50_000)
        + _call(1, 80_000, [_bash('hq open auth-token')])
        + _call(2, 90_000, [('Read', {
            'file_path': '.handoff/auth-token/HANDOFF.md'})])
        + _call(3, 100_000, [('Edit', {'file_path': 'a.py'})]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    cycle = cb.read_transcript(str(path))[3]
    assert (cycle.resume_context, cycle.resume_sum) == (100_000, 332_000)


def test_a_cycle_still_reading_back_ends_its_resume_at_its_last_call(
        tmp_path):
    """Verify j0 is the last call while every call since hq open reads
    back.

    Mutation: leaving j0 at the open, or at the first call, when no call
    ends the resume, so a session still reading its handoff back prices
    a resume shorter than the one it is paying.
    Oracle: hand-built transcript - hq open at 64,000, then a Read at
    70,000 and an hq read at 78,000. C0 is 78,000 and S0 is 62,000 +
    64,000 + 70,000 + 78,000 = 274,000.
    """
    records = (_call(0, 62_000, written=50_000)
               + _call(1, 64_000, [_bash('hq open auth-token')])
               + _call(2, 70_000, [('Read', {
                   'file_path': '.handoff/auth-token/HANDOFF.md'})])
               + _call(3, 78_000, [_bash('hq read auth-token plan.md')]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    cycle = cb.read_transcript(str(path))[3]
    assert (cycle.resume_context, cycle.resume_sum) == (78_000, 274_000)


def test_the_first_call_of_work_bills_its_output_as_work(tmp_path):
    """Verify Or counts the resume's calls before j0 and never j0 itself.

    Mutation: summing Or through j0, so whatever the first call of work
    writes is priced as resume at r and moves the point out; dropping
    the last call's output while the cycle still reads back, where that
    call is resume; or pricing the first call of a cycle that opened no
    handoff as resume.
    Oracle: hand-built transcripts. A typed /handoff, a Skill call at
    62,000, hq open, and a Read bill 100 + 200 + 300 output, and a
    Workflow call billing 40,000 answers the typed "go ahead", so Or is
    600. Cut before the prompt, the cycle still reads back and Or is 600
    through the Read. With no hq open, a first call billing 5,000 leaves
    Or at 0.
    """
    reading = (
        [_prompt('<command-message>claude-handoff:handoff</command-message>')]
        + _call(0, 62_000, [('Skill', {'skill': 'handoff'})], written=50_000,
                output=100)
        + _call(1, 64_000, [_bash('hq open auth-token')], output=200)
        + _call(2, 70_000, [('Read', {
            'file_path': '.handoff/auth-token/HANDOFF.md'})], output=300))
    working = (reading + [_prompt('go ahead')]
               + _call(3, 80_000, [('Workflow', {'script': '...'})],
                       output=40_000)
               + _call(4, 90_000, [_bash('pytest -q')], output=700))
    unopened = (_call(0, 62_000, [('Edit', {'file_path': 'a.py'})],
                      written=50_000, output=5_000)
                + _call(1, 70_000, [_bash('pytest -q')], output=700))
    path = tmp_path / 's.jsonl'
    for records, output in ((working, 600), (reading, 600), (unopened, 0)):
        path.write_text('\n'.join(json.dumps(r) for r in records))
        assert cb.read_transcript(str(path))[3].resume_output == output


def test_growth_is_measured_from_the_first_call_of_work(tmp_path):
    """Verify the resume's own reading stays out of the growth rate.

    Mutation: averaging from the cycle's first call, so the resume's
    reading, which C0 already counts, inflates g and throws the point
    out until the window slides past it; or measuring while the cycle
    still reads back, before any call of work has run.
    Oracle: hand-computed. From the 62,000 floor, hq open at 64,000 and
    four Reads climb 20,000 a call to 144,000. j0 is an Edit at 150,000,
    and five pytest calls climb 2,000 a call to 160,000, so g is
    10,000 / 5 = 2,000, where the floor would give 98,000 / 11 = 8,909.
    Cut after the last Read, the cycle still reads back and g is the
    fallback.
    """
    reading = (_call(0, 62_000, written=50_000)
               + _call(1, 64_000, [_bash('hq open auth-token')])
               + [record for step in range(4)
                  for record in _call(2 + step, 84_000 + 20_000 * step, [(
                      'Read', {'file_path': f'.handoff/auth-token/{step}.md'})])])
    working = (reading + _call(6, 150_000, [('Edit', {'file_path': 'a.py'})])
               + [record for step in range(1, 6)
                  for record in _call(6 + step, 150_000 + 2_000 * step,
                                      [_bash('pytest -q')])])
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in working))
    assert cb.read_transcript(str(path))[2] == 2_000
    path.write_text('\n'.join(json.dumps(r) for r in reading))
    assert cb.read_transcript(str(path))[2] == budget.FALLBACK_GROWTH_PER_CALL


def test_work_before_a_late_open_is_not_priced_as_resume(tmp_path):
    """Verify a mid-session hq open prices its resume from the floor.

    Mutation: summing S0 or Or from the cycle's first call, which prices
    every call of the earlier work as resume and throws the point
    hundreds of thousands of tokens late; leaving C0 at its billed
    context; letting the resume's start cross a work call; or not
    cutting it at the prompt that asked for the open.
    Oracle: hand-computed. Work climbs from the 62,000 floor through an
    Edit at 100,000 and a pytest at 160,000, each call billing 1,000
    output. With no prompt between, the agent's own Skill call at
    170,000 starts the resume, so 108,000 of growth comes out: C0 =
    196,000 - 108,000 = 88,000, S0 = 62,000 + 68,000 + 78,000 + 88,000
    = 296,000, and Or = 3,000 from the three calls before the Edit.
    With the agent's reply at 170,000 and a typed /handoff command after
    it, the resume starts at the Skill call at 176,000 that answers the
    command, so 114,000 comes out: C0 = 200,000 - 114,000 = 86,000 and
    S0 = 62,000 + 66,000 + 76,000 + 86,000 = 290,000.
    """
    work = (_call(0, 62_000, written=50_000, output=1_000)
            + _call(1, 100_000, [('Edit', {'file_path': 'a.py'})],
                    output=1_000)
            + _call(2, 160_000, [_bash('pytest -q')], output=1_000))
    unprompted = (work
                  + _call(3, 170_000, [('Skill', {'skill': 'handoff'})],
                          output=1_000)
                  + _call(4, 176_000, [_bash('hq open auth-token')],
                          output=1_000)
                  + _call(5, 186_000, [('Read', {
                      'file_path': '.handoff/auth-token/HANDOFF.md'})],
                          output=1_000)
                  + _call(6, 196_000, [('Edit', {'file_path': 'b.py'})],
                          output=1_000))
    prompted = (work
                + _call(3, 170_000, output=1_000)
                + [_prompt('<command-message>claude-handoff:handoff'
                           '</command-message>')]
                + _call(4, 176_000, [('Skill', {'skill': 'handoff'})])
                + _call(5, 180_000, [_bash('hq open auth-token')])
                + _call(6, 190_000, [('Read', {
                    'file_path': '.handoff/auth-token/HANDOFF.md'})])
                + _call(7, 200_000, [('Edit', {'file_path': 'b.py'})]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in unprompted))
    assert cb.read_transcript(str(path))[3] == budget.Cycle(
        floor=62_000, floor_written=50_000, resume_context=88_000,
        resume_sum=296_000, resume_output=3_000, one_hour=True)
    path.write_text('\n'.join(json.dumps(r) for r in prompted))
    cycle = cb.read_transcript(str(path))[3]
    assert (cycle.resume_context, cycle.resume_sum) == (86_000, 290_000)


def test_a_generation_priced_on_its_own_is_not_read_as_its_family():
    """Verify a model with its own cache rate matches before its family.

    Mutation: ordering PRICES_PER_MTOK family-first, so 'opus'
    matches claude-opus-5-5 and prices its cache reads at $0.50 against
    the $0.20 it is billed - two and a half times the real cost, and a
    target held to two fifths of the room already paid for.
    Oracle: the published cache-read price per million tokens, run
    through cost_per_turn at a context of exactly one million.
    """
    assert budget.model_tier('claude-opus-5-5') == 'opus-5-5'
    assert budget.model_tier('claude-opus-5') == 'opus'
    assert budget.model_tier('claude-fable-5-1') == 'fable-5-1'
    assert budget.model_tier('claude-fable-5') == 'fable'
    assert budget.model_tier('claude-sonnet-5') == 'sonnet-5'
    assert budget.model_tier('claude-sonnet-4-6') == 'sonnet'
    for tier, price in (('opus-5-5', 0.20), ('opus', 0.50),
                        ('fable-5-1', 0.25), ('fable', 1.00),
                        ('sonnet-5', 0.20), ('sonnet', 0.30)):
        assert abs(budget.cost_per_turn(1_000_000, tier)
                   - price * budget.CALLS_PER_TURN) < 1e-9


def test_a_cheap_model_is_left_alone(monkeypatch, capsys, tmp_path):
    """Verify a Haiku session raises no warning, and a Sonnet one does.

    Mutation: model_tier falling back to 'opus-5-5' for an unlisted model,
    which is what makes a plugin nag about a session whose whole cost is
    a few cents and train the user to ignore it; or Sonnet dropped from
    the price table, which silences a session billed at Opus 5.5's rate.
    Oracle: a spy on stdout - the same climb to 520K prints on opus
    and sonnet and prints nothing on haiku.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    assert budget.model_tier('claude-haiku-4-5') is None

    def run(model, session):
        path = tmp_path / f'{session}.jsonl'
        path.write_text('\n'.join(
            json.dumps(_record(index, value, model=model))
            for index, value in enumerate(CLIMB[:46])))
        payload = {'session_id': session, 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        return capsys.readouterr().out.strip()

    assert run('claude-opus-5-5', 'a')
    assert run('claude-sonnet-5', 'c')
    assert run('claude-haiku-4-5', 'b') == ''


def test_the_countdown_rounds_up_so_it_never_sticks():
    """Verify turns-left counts by ceiling, not floor-with-a-floor.

    Mutation: restoring max(1, int(...)), which shows "in 1" from 1.9
    turns of room all the way to the threshold - two turns reading the
    same - and overstates sub-turn room as a full turn.
    Oracle: hand-computed at 1,900 tokens a call on opus with no state,
    where the point is priced for a fresh 69,000 cycle at the 1-hour
    write price with no resume output: A = 69,000 + 17.6 * 83,500 +
    39 * 98,000 + 100 * 20,000 = 7,360,600, so H* = 69,000 +
    sqrt(3,800 A) = 236,243. From 190,000, 46,243 tokens of room is 2.8
    turns of 16,720, which must read "handoff in 3".
    """
    line = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': '/x/proj'},
        'context_window': {'total_input_tokens': 190_000},
        }))
    assert line.startswith('190K/236K')
    assert 'handoff in 3' in line


def test_growth_ignores_context_dropped_by_a_restart_in_place(tmp_path):
    """Verify a restart-in-place drop restarts the growth run.

    Mutation: starting the cycle at the transcript's first call, or
    measuring growth from the series start, so the window spans the
    drop.
    Oracle: hand-computed. The post-restart run climbs 20K over five
    steps, so growth is 5,000; spanning the drop would give (60-100)/9,
    which is negative and would silently fall back.
    """
    series = [100_000, 120_000, 140_000, 160_000, 180_000,
              60_000, 65_000, 70_000, 75_000, 80_000]
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(_record(index, value))
                              for index, value in enumerate(series)))
    assert cb.read_transcript(str(path))[2] == 5_000


def test_growth_keeps_a_dip_smaller_than_a_restart_in_place():
    """Verify a small fall in context stays in the growth run.

    Mutation: cutting the run at any fall, which leaves one value after
    the dip and throws the estimate to the fallback.
    Oracle: hand-computed. 100,000 to 100,900 in steps of 100, then a
    dip to 100,800: 800 over 10 intervals is 80.
    """
    series = [100_000 + 100 * step for step in range(10)] + [100_800]
    assert cb.growth_per_call(series) == 80


def test_growth_cuts_where_the_stop_hook_sees_a_restart_in_place(
        tmp_path):
    """Verify the growth cut and the Stop hook's restart-in-place test
    agree.

    Mutation: `>=` in place of `>` in read_transcript's cycle start, or
    any threshold other than half of RESTART_IN_PLACE_TOKENS.
    Oracle: boundary straddle from a 180,000 peak. A fall of 61,501
    cuts, leaving five values 5,000 apart; a fall of exactly 61,500 does
    not, so the run ends below its start and falls back.
    """
    peak = [170_000, 175_000, 180_000]
    cut = peak + [118_499 + 5_000 * step for step in range(5)]
    kept = peak + [118_500 + 5_000 * step for step in range(5)]
    path = tmp_path / 's.jsonl'
    for series, growth in ((cut, 5_000),
                           (kept, budget.FALLBACK_GROWTH_PER_CALL)):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(series)))
        assert cb.read_transcript(str(path))[2] == growth


def test_growth_falls_back_on_a_short_series():
    """Verify a young session uses the documented fallback, not zero.

    Mutation: returning 0 or the raw difference when fewer than five
    calls exist, which would make the reserve collapse and the warning
    fire on the session's first turn.
    Oracle: the module's own declared fallback constant.
    """
    assert cb.growth_per_call([50_000, 60_000]) == budget.FALLBACK_GROWTH_PER_CALL
    assert cb.growth_per_call([]) == budget.FALLBACK_GROWTH_PER_CALL


def test_band_one_sits_a_handoff_write_past_band_zero(monkeypatch, capsys,
                                                      tmp_path):
    """Verify band 0 fires at H*, band 1 at H* + W, and only band 1 rings.

    Mutation: the escalation point set at H* + g, or at H* + w g as if
    the write grew at the work rate, instead of H* + W; a flipped
    comparison in compose; or the desktop notification raised on band 0
    too.
    Oracle: hand-computed on the 10,000-a-call climb - H* is 376,209 and
    W is 29,000 at any rate, so the escalation point is 405,209; the
    compose calls straddle both by one token.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'

    def run(upto):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(CLIMB[:upto])))
        payload = {'session_id': 'W', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        out = capsys.readouterr().out.strip()
        with open(tmp_path / 'state' / 'W.json') as handle:
            return json.loads(out) if out else {}, json.load(handle)

    announced, stored = run(32)
    assert (stored['handoff'], stored['escalate']) == (376_209, 405_209)
    assert 'terminalSequence' not in announced
    announced, stored = run(35)
    assert stored['band'] == 1
    assert 'terminalSequence' in announced
    assert cb.compose(376_208, 10_000, 376_209, 405_209)[0] == -1
    assert cb.compose(376_209, 10_000, 376_209, 405_209)[0] == 0
    assert cb.compose(405_208, 10_000, 376_209, 405_209)[0] == 0
    assert cb.compose(405_209, 10_000, 376_209, 405_209)[0] == 1


def test_message_names_the_numbers_the_reader_has_to_act_on():
    """Verify the warning text carries context, handoff point, and growth.

    Mutation: a message that says only "context is large", which gives
    the reader nothing to decide with, or one that prints the
    escalation point in place of the handoff point.
    Oracle: the hand-computed strings for a 460K context on a 450,810
    point growing 88,000 tokens a turn.
    """
    _, message = cb.compose(460_000, 10_000, 450_810, 626_810)
    assert '460K' in message
    assert '450K handoff point' in message
    assert '626K' not in message
    assert '88K a turn' in message


def test_the_warning_names_the_skill_the_plugin_ships():
    """Verify every warning band names the bundled handoff skill.

    Mutation: renaming skills/handoff/, or rewording a band so the
    warning names a command that no longer exists. The word boundary is
    what makes a rename to a longer name (handoff2) fail rather than
    pass on the prefix.
    Oracle: the skill's own frontmatter name on disk, matched against
    the text of both bands.
    """
    skill = os.path.join(HERE, '..', 'skills', 'handoff', 'SKILL.md')
    with open(skill) as handle:
        front = handle.read().split('---\n', 2)[1]
    name = re.search(r'^name:\s*(\S+)', front, re.M).group(1)
    for context in (460_000, 700_000):
        band, message = cb.compose(context, 10_000, 450_810, 626_810)
        assert band >= 0
        assert re.search(rf'/{name}\b', message)


def test_repeated_stream_snapshots_do_not_inflate_the_series(tmp_path):
    """Verify one API response counts once, however often it is written.

    Mutation: dropping the message-id dedupe in read_transcript. Claude
    Code writes a response as several progressive snapshots sharing an
    id, so counting each would treble the call count and divide the
    measured growth by three.
    Oracle: hand-built transcript - four responses climbing 10K each,
    written as ten records, must read as 10,000 per call.
    """
    path = tmp_path / 'session.jsonl'
    records = []
    for index, context in enumerate((100_000, 110_000, 120_000, 130_000,
                                     140_000, 150_000)):
        records.extend({
                'type': 'assistant',
                'message': {
                    'id': f'msg_{index}',
                    'role': 'assistant',
                    'model': 'claude-opus-5-5',
                    'usage': {
                        'cache_read_input_tokens': context,
                        'cache_creation_input_tokens': 0,
                        'input_tokens': 0,
                        'output_tokens': snapshot,
                        },
                    },
                } for snapshot in range(3))
    path.write_text('\n'.join(json.dumps(r) for r in records))
    context, model, per_call, _ = cb.read_transcript(str(path))
    assert context == 150_000
    assert model == 'claude-opus-5-5'
    assert per_call == 10_000


def _record(index, value, model='claude-opus-5-5', nested=False, flag=False):
    """Build one transcript record billing `value`, or nothing if 0.
    """
    counts = {'cache_read_input_tokens': value,
              'cache_creation_input_tokens': 0, 'input_tokens': 0}
    usage = dict(counts) if not nested else {
        'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0,
        'input_tokens': 0, 'iterations': [counts]}
    record = {'type': 'assistant',
              'message': {'id': f'm{index}', 'role': 'assistant',
                          'model': model, 'usage': usage}}
    if flag:
        record['isApiErrorMessage'] = True
    return record


def _call(index, value, tools=(), written=0, ttl=None, sidechain=False,
          output=0):
    """Build the records of one call, one tool_use block per record.

    Claude Code writes each content block of a response as its own
    record under the shared message id, each carrying the same usage.
    """
    usage = {'cache_read_input_tokens': value - written,
             'cache_creation_input_tokens': written, 'input_tokens': 0,
             'output_tokens': output}
    if ttl:
        usage['cache_creation'] = {
            'ephemeral_1h_input_tokens': written if ttl == '1h' else 0,
            'ephemeral_5m_input_tokens': written if ttl == '5m' else 0,
            }
    blocks = [{'type': 'tool_use', 'name': name, 'input': given}
              for name, given in tools] or [{'type': 'text', 'text': '.'}]
    return [{'type': 'assistant', 'isSidechain': sidechain,
             'message': {'id': f'c{index}', 'role': 'assistant',
                         'model': 'claude-opus-5-5', 'usage': usage,
                         'content': [block]}} for block in blocks]


def _five_minute_climb(upto):
    """Build the first `upto` calls of CLIMB, each writing 10,000 tokens at
    the 5-minute TTL.

    Priced at m = 25, the climb's handoff point is 369,773 and its
    escalation point 398,773. A later 1-hour rewrite larger than all
    those writes together flips the cycle to m = 40, which moves the
    raw point outward.
    """
    return [record for index, value in enumerate(CLIMB[:upto])
            for record in _call(f'f{index}', value, written=10_000, ttl='5m')]


def _bash(command):
    """Name a Bash tool use running `command`.
    """
    return 'Bash', {'command': command}


def _prompt(text, kind='human', sidechain=False):
    """Build one user record, typed by a person unless `kind` says not.
    """
    return {'type': 'user', 'isSidechain': sidechain, 'origin': {'kind': kind},
            'message': {'role': 'user', 'content': text}}


def test_the_measured_rate_ignores_a_record_the_api_never_billed(tmp_path):
    """Verify an unbilled record changes nothing, wherever it lands.

    Mutation: dropping the billed test; or keying it off
    isApiErrorMessage, a flag a real unbilled record need not carry; or
    off the placeholder model id, which reads a record's label rather
    than what it billed. Such a record enters the series as a context
    of zero, which is indistinguishable from a restart in place - the
    growth run restarts there and counts the whole conversation as
    growth since.
    Oracle: invariance under insertion - splicing the record at every
    position of a fixed climb must return the identical triple. That is
    a relation, not a recomputed number, so it holds for any record
    shape that bills nothing.
    """
    climb = list(range(100_000, 120_000, 2_000))
    clean = list(starmap(_record, enumerate(climb)))
    path = tmp_path / 'session.jsonl'

    def read(records):
        path.write_text('\n'.join(json.dumps(r) for r in records))
        return cb.read_transcript(str(path))[:3]

    assert read(clean) == (118_000, 'claude-opus-5-5', 2_000)
    for position in range(len(clean) + 1):
        for flag in (False, True):
            spliced = list(clean)
            spliced.insert(position, _record('x', 0, '<synthetic>', flag=flag))
            assert read(spliced) == (118_000, 'claude-opus-5-5', 2_000)


def test_a_call_billed_one_level_down_still_counts(tmp_path):
    """Verify counts under `iterations` are read, not discarded as zero.

    Mutation: concluding a record billed nothing from its top-level
    counts alone. Real records put the counts in either place, and the
    top level reads as all zeros on some of them, so the context they
    carry is thrown away and the growth run restarts at a phantom
    restart in place.
    Oracle: differential - the same climb written both ways must
    measure the same, and only a record with the counts in neither
    place may be dropped.
    """
    climb = list(range(100_000, 120_000, 2_000))
    path = tmp_path / 'session.jsonl'

    def read(records):
        path.write_text('\n'.join(json.dumps(r) for r in records))
        return cb.read_transcript(str(path))[:3]

    flat = list(starmap(_record, enumerate(climb)))
    nested = [_record(index, value, model='claude-fable-5', nested=True)
              for index, value in enumerate(climb)]
    assert read(nested) == (118_000, 'claude-fable-5', 2_000)
    assert read(flat)[0] == read(nested)[0]
    assert read(flat + [_record('e', 0, '<synthetic>')])[0] == 118_000


def test_a_missing_or_unreadable_transcript_stays_silent(tmp_path):
    """Verify the hook never breaks a turn over its own input.

    Mutation: letting the OSError escape read_transcript. A Stop hook
    that raises turns every single turn into an error banner.
    Oracle: the documented zero-context return, which main() treats as
    nothing to say.
    """
    context, model, per_call, cycle = cb.read_transcript(
        str(tmp_path / 'nope.jsonl'))
    assert context == 0
    assert per_call == budget.FALLBACK_GROWTH_PER_CALL
    assert cycle is None


def test_subagent_turns_are_never_announced(monkeypatch, capsys, tmp_path):
    """Verify a subagent's own context never raises a budget warning.

    Mutation: dropping the agent_id guard in main. A subagent never
    restarts in place and its context dies with it, so every delegated
    call would fire a warning the user can do nothing about.
    Oracle: a spy on stdout - the main-thread payload prints, the
    subagent payload with the identical transcript does not.
    """
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(_record(index, value))
                              for index, value in enumerate(CLIMB[:46])))
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))

    payload = {'session_id': 'main-1', 'transcript_path': str(path)}
    monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
    cb.main()
    assert capsys.readouterr().out.strip()

    sub = {'session_id': 'sub-1', 'transcript_path': str(path),
           'agent_id': 'agent-9'}
    monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(sub)))
    cb.main()
    assert capsys.readouterr().out.strip() == ''


def test_a_band_is_announced_once_and_rearmed_by_a_restart_in_place(
        monkeypatch, capsys, tmp_path):
    """Verify the warning fires on entry, stays quiet, then fires again.

    Mutation: writing the state unconditionally without comparing, which
    silences everything, or never writing it, which re-warns every turn
    until the user disables the hook.
    Oracle: a spy on stdout across four runs - warn at 520K, silence at
    530K, silence at the 120K a restart in place drops to, and warn
    again at 610K, past the new cycle's 455,207 point.
    """
    state = tmp_path / 'state'
    monkeypatch.setattr(cb, 'STATE_DIR', str(state))
    path = tmp_path / 's.jsonl'
    after = [120_000 + 10_000 * step for step in range(50)]

    def run(series):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(series)))
        payload = {'session_id': 'S', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        return capsys.readouterr().out.strip()

    assert 'handoff' in run(CLIMB[:46])
    assert run(CLIMB[:47]) == ''
    assert run(CLIMB[:47] + after[:1]) == ''
    assert 'handoff' in run(CLIMB[:47] + after)


def test_a_state_file_in_the_old_format_loads_and_keeps_its_band(
        monkeypatch, capsys, tmp_path):
    """Verify a session in flight across the upgrade neither crashes nor
    re-warns.

    Mutation: reading a key this version writes by subscript, which
    raises KeyError on a file written before it existed; reading the
    old 'target' slot as the handoff point; or storing the freshly
    computed band unconditionally.
    Oracle: a spy on stdout and the rewritten file across two turns
    after an old file holding band 0, a 350,000 target, and a 264,205
    handoff point - nothing is said at 280K, at a dip to 260K under the
    point, or back at 290K, short of the 293,205 escalation point one
    handoff write past it. The old point stays latched because band 0
    was already announced, the band stored across the dip stays 0 so
    the climb back cannot announce it again, and the file comes back in
    the new shape.
    """
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'U.json').write_text(json.dumps({
        'band': 0, 'growth_per_call': 3_000, 'target': 350_000,
        'handoff': 264_205, 'context': 275_000,
        }))
    monkeypatch.setattr(cb, 'STATE_DIR', str(state))
    path = tmp_path / 's.jsonl'

    def run(upto):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(CLIMB[:upto])))
        payload = {'session_id': 'U', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        return capsys.readouterr().out.strip()

    assert run(22) == ''
    assert run(20) == ''
    assert run(23) == ''
    stored = json.loads((state / 'U.json').read_text())
    assert set(stored) == {'band', 'growth_per_call', 'handoff', 'escalate',
                           'context'}
    assert stored['handoff'] == 264_205
    assert stored['band'] == 0


def test_the_hook_and_the_gauge_never_name_a_different_threshold(monkeypatch,
                                                                 capsys,
                                                                 tmp_path):
    """Verify the amber bar and the hook's band cross together.

    Mutation: re-deriving the handoff point in compose, or in
    session_state, rather than reading the latched one. The two
    surfaces then disagree wherever the measurements have moved since
    the latch was set - the bar reads "handoff now" for turns on end
    while the hook says nothing, which is the one thing the status line
    promises cannot happen.
    Oracle: differential across two surfaces - the rendered line is
    amber or red on exactly the turns whose stored band is 0 or more,
    on a climb that crosses the point and then rewrites its cache at
    the 1-hour TTL, which would re-derive the point further out.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    records = list(starmap(_record, enumerate(CLIMB[:40])))
    for step in range(1, 11):
        records.extend(_call(f'r{step}', CLIMB[39] + 100 * step,
                             written=100_000, ttl='1h'))
    ids = list(dict.fromkeys(r['message']['id'] for r in records))

    seen = set()
    for upto in range(30, len(ids) + 1):
        keep = set(ids[:upto])
        path.write_text('\n'.join(json.dumps(r) for r in records
                                  if r['message']['id'] in keep))
        payload = {'session_id': 'A', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        with open(tmp_path / 'state' / 'A.json') as handle:
            stored = json.load(handle)
        line = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
            'session_id': 'A',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': stored['context']},
            }))
        warning = 'handoff now' in line or 'over' in line
        assert warning == (stored['band'] >= 0), (stored, line)
        seen.add(warning)
    assert seen == {False, True}


def test_no_threshold_rises_once_a_warning_is_given(monkeypatch, capsys,
                                                    tmp_path):
    """Verify a latched threshold holds while the raw point moves out,
    and a dip that is not a restart in place releases neither latch.

    Mutation: testing `context < last_context` for the restart in place
    that releases the latches, with no size to it; or dropping the
    latch. Billed context falls without a restart in place - a cached
    block expiring, a tool result dropped - and a dip of a few hundred
    tokens then hands the session a fresh handoff point further out and
    a rearmed band, which is the walking-backwards this latch exists to
    stop.
    Oracle: monotonicity of the state file against its own first turn,
    with a spy on the raw point. From the warning at 380K on the
    5-minute climb, a 1-hour rewrite at 380,100 moves the raw point out
    to 383,712, and it stays past 369,773 across a 1,000-token dip and
    a climb to 385,000; every turn must store the first turn's 369,773
    and 398,773 and band 0.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    records = _five_minute_climb(32)
    tail = ((None, 0, 0), ('t0', 380_100, 380_000), ('t1', 379_100, 0),
            ('t2', 385_000, 0))
    stored, raw = [], []
    for identifier, value, written in tail:
        if identifier:
            records.extend(_call(identifier, value, written=written,
                                 ttl='1h' if written else None))
        path.write_text('\n'.join(json.dumps(r) for r in records))
        payload = {'session_id': 'M', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        with open(tmp_path / 'state' / 'M.json') as handle:
            stored.append(json.load(handle))
        _, _, per_call, cycle = cb.read_transcript(str(path))
        raw.append(budget.handoff_point(cycle, per_call, 'opus-5-5'))

    assert raw[:2] == [369_773, 383_712]
    assert all(point > 369_773 for point in raw[1:])
    assert all(row['band'] == 0 for row in stored)
    assert [row['handoff'] for row in stored] == [369_773] * len(tail)
    assert [row['escalate'] for row in stored] == [398_773] * len(tail)


def test_a_young_cycle_is_not_pinned_to_its_fallback_rate(monkeypatch,
                                                          capsys, tmp_path):
    """Verify the point follows the live rate until band 0 is announced.

    Mutation: latching from the first turn rather than from the first
    warning, so the fallback rate of a cycle's first few calls - or any
    quiet stretch before the warning - holds the point for the rest of
    the cycle.
    Oracle: hand-computed on the 70,000 floor - three calls measure no
    rate yet, so the 1,900 fallback puts the point at 203,473; seven
    more calls measure 10,000 a call and must move it out to 376,209.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    points = []
    for upto in (3, 10):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(CLIMB[:upto])))
        payload = {'session_id': 'Y', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        assert capsys.readouterr().out.strip() == ''
        with open(tmp_path / 'state' / 'Y.json') as handle:
            points.append(json.load(handle)['handoff'])
    assert points == [203_473, 376_209]


def test_the_latch_holds_a_threshold_down_and_never_up():
    """Verify a latched threshold may fall and may never rise.

    Mutation: max() in place of min() in latched, or treating a stored
    0 as a latch in force, which pins every new session at zero.
    Oracle: hand-computed - with no latch the point passes through, and
    against a latch of 400,000 a point of 450,810 is held at 400,000
    while one of 300,000 is taken.
    """
    assert cb.latched(450_810, 0) == 450_810
    assert cb.latched(450_810, 400_000) == 400_000
    assert cb.latched(300_000, 400_000) == 300_000


def test_the_gauge_never_hands_back_room_it_has_withdrawn(monkeypatch,
                                                          capsys,
                                                          tmp_path):
    """Verify the status line does not undo a handoff warning next turn.

    Mutation: re-deriving the handoff point in render, or in compose,
    from this turn's measurements. The warning is given, a cache
    rewrite moves the unlatched point outward past the context, and the
    line returns to green with turns to spare - contradicting a warning
    the user has already been given and acted on.
    Oracle: a spy on the rendered line across two turns - the 5-minute
    climb reaching 380K crosses its 369,773 point and must read amber,
    and a 1-hour rewrite at 380,100, which on its own puts the point at
    383,712, past the context, must not read green again.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'

    def run(records, context):
        path.write_text('\n'.join(json.dumps(r) for r in records))
        payload = {'session_id': 'L', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        return re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
            'session_id': 'L',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            }))

    records = _five_minute_climb(32)
    assert 'handoff now' in run(records, 380_000)
    rewrite = _call('r', 380_100, written=380_000, ttl='1h')
    assert 'handoff now' in run(records + rewrite, 380_100)
    _, _, per_call, cycle = cb.read_transcript(str(path))
    assert budget.handoff_point(cycle, per_call, 'opus-5-5') == 383_712


def test_a_barely_growing_session_never_reads_as_zero_growth():
    """Verify the growth rate never prints as 0K a turn.

    Mutation: restoring the bare integer floor for the rate, which
    prints "growing 0K a turn" for a session measured under 114 tokens
    a call - a statement the reader can only parse as broken.
    Oracle: hand-computed - 100 tokens a call is 880 a turn, under the
    1K floor, so the message must carry "<1K a turn".
    """
    _, message = cb.compose(300_000, 100, 290_000, 400_000)
    assert '<1K a turn' in message
    assert ' 0K a turn' not in message


class _Stdin:
    """Minimal stdin stand-in returning a fixed payload to json.load.
    """

    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, *args: object) -> str:
        return self.text


def test_the_gauge_stays_readable_past_the_escalation_point():
    """Verify two different sizes past band 1 do not render identically.

    Mutation: keeping the bar past the escalation point. It clamps to
    full, so 2.3x and 3.6x print the same glyph - and past it is
    exactly where the reader needs to tell them apart.
    Oracle: hand-computed against the no-state point of 236,243 - 452K
    is 1.9x and 700K is 3.0x, both past the 265,243 escalation point.
    """
    def line(context):
        return sl.render({
            'session_id': 'none',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            })

    assert line(452_000) != line(700_000)
    assert '1.9x over' in line(452_000)
    assert '3.0x over' in line(700_000)


def test_the_gauge_turns_red_one_handoff_write_past_its_point():
    """Verify the no-state gauge escalates exactly one write past its point.

    Mutation: the fallback escalation point set at H* + g, or at H* + w g
    as if the write grew at the work rate, instead of H* + W - the gauge
    then turns red on a different turn from the hook's band 1.
    Oracle: boundary straddle against the no-state point of 236,243 -
    236,243 + 29,000 = 265,243, so 265,242 reads amber and 265,243 reads
    red.
    """
    def line(context):
        return sl.render({
            'session_id': 'none',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            })

    assert line(265_242).startswith(sl.YELLOW)
    assert 'handoff now' in line(265_242)
    assert line(265_243).startswith(sl.RED)
    assert 'x over' in line(265_243)


def test_the_gauge_marks_its_cost_as_an_estimate():
    """Verify the gauge hedges its cost figure as an estimate.

    Mutation: dropping the ~ from the cost field, leaving a bare
    two-decimal figure that reads as the price of the next turn rather
    than as an estimate of cached-context re-reading, which is all the
    model covers.
    Oracle: hand-computed - 0.517M tokens at $0.20 per million over 8.8
    calls is $0.91, and the field must carry the tilde.
    """
    visible = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5.5'},
        'workspace': {'current_dir': '/x/proj'},
        'context_window': {'total_input_tokens': 517_000},
        }))
    assert '~$0.91/t' in visible


def test_the_decision_numbers_survive_a_narrow_pane():
    """Verify size and action lead the line, and the line stays short.

    Mutation: putting the directory or the model first, as most status
    lines do. A pane is cut from the right, so the two fields that carry
    the decision are the ones lost.
    Oracle: hand-checked - the visible line with color stripped must
    start with the size against the 236,243 no-state point and fit a
    narrow split.
    """
    visible = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': '/x/myproject'},
        'context_window': {'total_input_tokens': 150_000},
        }))
    assert visible.startswith('150K/236K')
    assert 'handoff in' in visible.split('$')[0]
    assert len(visible) <= 64
