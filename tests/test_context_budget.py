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
# handoff point is 514,765 and the escalation point 690,765.
CLIMB = [70_000 + 10_000 * step for step in range(90)]


def test_the_handoff_point_reproduces_the_worked_values():
    """Verify H* = C0 + sqrt(2 g A) on the three worked cycles.

    Mutation: any dropped or rescaled term of A - the resume sum S0, the
    handoff write's w (C0 + W/2), or the m (Fw + C0 - F + W) cache
    writes - or the factor 2 under the root.
    Oracle: hand-computed from the formula with F 62,000, Fw 50,000,
    g 2,300, w 17.6, W 40,480, m 40. At C0 80,000 and S0 700,000,
    A = 700,000 + 17.6 * 100,240 + 40 * 108,480 = 6,803,424 and
    sqrt(4,600 A) = 176,906.05, so H* = 256,906; likewise 376,934 at
    C0 150,000 with S0 1,060,000, and 456,614 at C0 200,000 with S0
    1,300,000.
    """
    assert budget.handoff_write_tokens(2_300) == 40_480
    assert budget.write_read_ratio('opus-5-5', True) == 40
    for resume_context, resume_sum, point in ((80_000, 700_000, 256_906),
                                              (150_000, 1_060_000, 376_934),
                                              (200_000, 1_300_000, 456_614)):
        cycle = budget.Cycle(floor=62_000, floor_written=50_000,
                             resume_context=resume_context,
                             resume_sum=resume_sum, one_hour=True)
        assert budget.handoff_point(cycle, 2_300, 'opus-5-5') == point


def test_a_longer_resume_earns_a_longer_cycle():
    """Verify H* and the work room past the resume both rise with C0.

    Mutation: anchoring H* at the floor F instead of the resume end C0,
    so every resume token comes out of the work room - the micro-cycle
    defect, where each handoff's longer resume shortens the next cycle.
    Oracle: monotonicity - with F, Fw, S0, g, and m held fixed, both
    H* and H* - C0 must strictly increase across rising C0, since A
    rises by w + m per resume token.
    """
    points = []
    for resume_context in (62_000, 80_000, 150_000, 200_000, 300_000):
        cycle = budget.Cycle(floor=62_000, floor_written=50_000,
                             resume_context=resume_context,
                             resume_sum=700_000, one_hour=True)
        points.append((resume_context,
                       budget.handoff_point(cycle, 2_300, 'opus-5-5')))
    assert [point for _, point in points] == sorted(
        {point for _, point in points})
    rooms = [point - resume_context for resume_context, point in points]
    assert rooms == sorted(set(rooms))


def test_the_cache_ttl_picks_the_write_price_ratio(tmp_path):
    """Verify m is the published write price over the read price, by TTL.

    Mutation: swapping the 1-hour and 5-minute multipliers, pricing a
    write off the base input price rather than the tier's read
    multiplier, or reading the TTL from the wrong breakdown key.
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
    resume early and whose context reads as a compaction; or taking a
    call's tool uses from its first record only, which hides the Edit
    written in the second.
    Oracle: hand-built transcript - F 62,000 with 50,000 written, hq
    open behind --root and --session flags, a Read, hq read and hq
    standing, a sed of a .handoff path, then a call whose second record
    is an Edit. C0 is that call's 84,000 and S0 is 62,000 + 64,000 +
    70,000 + 78,000 + 84,000 = 358,000.
    """
    records = (
        _call(0, 62_000, [('Skill', {'skill': 'handoff'})], written=50_000)
        + _call(1, 64_000, [_bash('cd /w && hq --root /w --session s1 '
                                  'open auth-token')])
        + _call(2, 70_000, [('Read', {
            'file_path': '/w/.handoff/auth-token/HANDOFF.md'})])
        + _call(3, 78_000, [_bash('hq read auth-token specs/plan.md'),
                            _bash('hq standing auth-token')])
        + _call('side', 300_000, [('Edit', {'file_path': '/w/a.py'})],
                sidechain=True)
        + _call(4, 84_000, [_bash('sed -n 1,40p .handoff/auth-token/x.md'),
                            ('Edit', {'file_path': '/w/src/app.py'})])
        + _call(5, 90_000, [_bash('pytest -q')])
        + _call(6, 96_000, [_bash('hq read auth-token specs/plan.md')]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    context, _, _, cycle = cb.read_transcript(str(path))
    assert context == 96_000
    assert cycle == budget.Cycle(floor=62_000, floor_written=50_000,
                                 resume_context=84_000, resume_sum=358_000,
                                 one_hour=True)


def test_a_compaction_restarts_the_floor_and_the_resume(tmp_path):
    """Verify the cycle after a compaction is measured from its own start.

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
        resume_sum=150_000, one_hour=True)
    path.write_text('\n'.join(json.dumps(r) for r in first + opened))
    assert cb.read_transcript(str(path))[3] == budget.Cycle(
        floor=150_000, floor_written=120_000, resume_context=170_000,
        resume_sum=632_000, one_hour=True)


def test_the_resume_covers_ls_grep_cd_and_a_persisted_read(tmp_path):
    """Verify each resume shape the marker must recognize keeps j0 open.

    Mutation: dropping any one shape from the resume test - an ls of
    .handoff/, a grep or wc of a HANDOFF file, a cd into .handoff/...
    with a relative path, or a Read of a persisted tool-results file -
    which would end the resume at that call instead of the pytest
    after it.
    Oracle: hand-built j0 - the pytest call is the first whose tool use
    is not a resume shape, so C0 is its 110,000 and S0 is the six
    calls up to and including it: 62,000 + 64,000 + 70,000 + 78,000 +
    86,000 + 94,000 + 102,000 + 110,000 = 666,000.
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
        + _call(7, 110_000, [_bash('pytest -q')]))
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in records))
    _, _, _, cycle = cb.read_transcript(str(path))
    assert cycle == budget.Cycle(floor=62_000, floor_written=50_000,
                                 resume_context=110_000, resume_sum=666_000,
                                 one_hour=True)


def test_a_generation_priced_on_its_own_is_not_read_as_its_family():
    """Verify a model with its own cache rate matches before its family.

    Mutation: ordering CACHE_READ_PER_MTOK family-first, so 'opus'
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
    write price: A = 69,000 + 17.6 * 85,720 + 40 * 102,440 = 5,675,272,
    so H* = 69,000 + sqrt(3,800 A) = 215,854. From 171,000, 44,854
    tokens of room is 2.7 turns of 16,720, which must read
    "handoff in 3".
    """
    line = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': '/x/proj'},
        'context_window': {'total_input_tokens': 171_000},
        }))
    assert line.startswith('171K/215K')
    assert 'handoff in 3' in line


def test_growth_ignores_context_dropped_by_compaction():
    """Verify a compaction drop restarts the growth run, not flattens it.

    Mutation: dropping the `run = []` reset in growth_per_call, so the
    window spans the compaction.
    Oracle: hand-computed. The post-compaction run climbs 20K over five
    steps, so growth is 5,000; spanning the drop would give (60-100)/9,
    which is negative and would silently fall back.
    """
    series = [100_000, 120_000, 140_000, 160_000, 180_000,
              60_000, 65_000, 70_000, 75_000, 80_000]
    assert cb.growth_per_call(series) == 5_000


def test_growth_keeps_a_dip_smaller_than_a_compaction():
    """Verify a small fall in context stays in the growth run.

    Mutation: cutting the run at any fall, which leaves one value after
    the dip and throws the estimate to the fallback.
    Oracle: hand-computed. 100,000 to 100,900 in steps of 100, then a
    dip to 100,800: 800 over 10 intervals is 80.
    """
    series = [100_000 + 100 * step for step in range(10)] + [100_800]
    assert cb.growth_per_call(series) == 80


def test_growth_cuts_where_the_stop_hook_sees_a_compaction():
    """Verify the growth cut and the Stop hook's compaction test agree.

    Mutation: `>=` in place of `>` in growth_per_call's cut, or any
    threshold other than half of POST_COMPACTION_TOKENS.
    Oracle: boundary straddle from a 180,000 peak. A fall of 61,501
    cuts, leaving five values 5,000 apart; a fall of exactly 61,500 does
    not, so the run ends below its start and falls back.
    """
    peak = [170_000, 175_000, 180_000]
    cut = peak + [118_499 + 5_000 * step for step in range(5)]
    kept = peak + [118_500 + 5_000 * step for step in range(5)]
    assert cb.growth_per_call(cut) == 5_000
    assert cb.growth_per_call(kept) == budget.FALLBACK_GROWTH_PER_CALL


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

    Mutation: the escalation point set at H* + g instead of H* + w g, a
    flipped comparison in compose, or the desktop notification raised
    on band 0 too.
    Oracle: hand-computed on the 10,000-a-call climb - H* is 514,765
    and W = 2 turns * 8.8 calls * 10,000 = 176,000, so the escalation
    point is 690,765; the compose calls straddle both by one token.
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

    announced, stored = run(46)
    assert (stored['handoff'], stored['escalate']) == (514_765, 690_765)
    assert 'terminalSequence' not in announced
    announced, stored = run(64)
    assert stored['band'] == 1
    assert 'terminalSequence' in announced
    assert cb.compose(514_764, 10_000, 514_765, 690_765)[0] == -1
    assert cb.compose(514_765, 10_000, 514_765, 690_765)[0] == 0
    assert cb.compose(690_764, 10_000, 514_765, 690_765)[0] == 0
    assert cb.compose(690_765, 10_000, 514_765, 690_765)[0] == 1


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


def _call(index, value, tools=(), written=0, ttl=None, sidechain=False):
    """Build the records of one call, one tool_use block per record.

    Claude Code writes each content block of a response as its own
    record under the shared message id, each carrying the same usage.
    """
    usage = {'cache_read_input_tokens': value - written,
             'cache_creation_input_tokens': written, 'input_tokens': 0}
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


def _bash(command):
    """Name a Bash tool use running `command`.
    """
    return 'Bash', {'command': command}


def test_the_measured_rate_ignores_a_record_the_api_never_billed(tmp_path):
    """Verify an unbilled record changes nothing, wherever it lands.

    Mutation: dropping the billed test; or keying it off
    isApiErrorMessage, a flag two thirds of real unbilled records do not
    carry; or off the placeholder model id, which reads a record's label
    rather than what it billed. Such a record enters the series as a
    context of zero, which is indistinguishable from a compaction - the
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
    carry - 484,173 tokens on the one that prompted this - is thrown
    away and the growth run restarts at a phantom compaction.
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
    """Verify a subagent's own context never raises a compaction warning.

    Mutation: dropping the agent_id guard in main. A subagent cannot
    compact and its context dies with it, so every delegated call would
    fire a warning the user can do nothing about.
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


def test_a_band_is_announced_once_and_rearmed_by_a_compaction(monkeypatch,
                                                              capsys,
                                                              tmp_path):
    """Verify the warning fires on entry, stays quiet, then fires again.

    Mutation: writing the state unconditionally without comparing, which
    silences everything, or never writing it, which re-warns every turn
    until the user disables the hook.
    Oracle: a spy on stdout across four runs - warn at 520K, silence at
    530K, silence at the 120K a compaction drops to, and warn again at
    590K, past the new cycle's 585,205 point.
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
    handoff point - nothing is said at 280K or at 420K, short of the
    440,205 escalation point, the old point stays latched because band
    0 was already announced, and the file comes back in the new shape.
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
    assert run(36) == ''
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
    """Verify a dip that is not a compaction releases neither latch.

    Mutation: testing `context < last_context` for the compaction that
    releases the latches, with no size to it; or dropping the latch.
    Billed context falls without a compaction - a cached block
    expiring, a tool result dropped - and a dip of a few hundred tokens
    then hands the session a fresh handoff point further out and a
    rearmed band, which is the walking-backwards this latch exists to
    stop.
    Oracle: monotonicity of the state file against its own previous
    turn - from the warning at 520K, across an hq open and read that
    lengthen the resume and would raise the raw point to 1,490,745, a
    1,000-token dip, and more climbing, no stored threshold may rise
    and no stored band may fall.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    records = list(starmap(_record, enumerate(CLIMB[:46])))
    tail = ((None, 0, []),
            ('t0', 520_100, [_bash('hq open auth-token')]),
            ('t1', 519_100, []),
            ('t2', 521_100, [_bash('hq read auth-token x.md')]),
            ('t3', 522_100, []))
    stored = []
    for identifier, value, tools in tail:
        if identifier:
            records.extend(_call(identifier, value, tools))
        path.write_text('\n'.join(json.dumps(r) for r in records))
        payload = {'session_id': 'M', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        with open(tmp_path / 'state' / 'M.json') as handle:
            stored.append(json.load(handle))

    _, _, per_call, cycle = cb.read_transcript(str(path))
    assert budget.handoff_point(cycle, per_call, 'opus-5-5') == 1_490_745
    assert all(row['band'] == 0 for row in stored)
    assert [row['handoff'] for row in stored] == [514_765] * len(tail)
    escalations = [row['escalate'] for row in stored]
    assert escalations[0] == 690_765
    assert escalations == sorted(escalations, reverse=True)


def test_a_young_cycle_is_not_pinned_to_its_fallback_rate(monkeypatch,
                                                          capsys, tmp_path):
    """Verify the point follows the live rate until band 0 is announced.

    Mutation: latching from the first turn rather than from the first
    warning, so the fallback rate of a cycle's first few calls - or any
    quiet stretch before the warning - holds the point for the rest of
    the cycle.
    Oracle: hand-computed on the 70,000 floor - three calls measure no
    rate yet, so the 1,900 fallback puts the point at 175,587; seven
    more calls measure 10,000 a call and must move it out to 514,765.
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
    assert points == [175_587, 514_765]


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
    rewrite moves the unlatched point outward, and the line returns to
    green with turns to spare - contradicting a warning the user has
    already been given and acted on.
    Oracle: a spy on the rendered line across two turns - the climb
    reaching 520K crosses the 514,765 point and must read amber, and a
    cache rewrite at the 1-hour TTL at 520,100, which on its own would
    put the point at 505,790, must not read green again.
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

    records = list(starmap(_record, enumerate(CLIMB[:46])))
    assert 'handoff now' in run(records, 520_000)
    rewrite = _call('r', 520_100, written=200_000, ttl='1h')
    assert 'handoff now' in run(records + rewrite, 520_100)


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
    Oracle: hand-computed against the no-state point of 215,854 - 452K
    is 2.1x and 700K is 3.2x, both past the 249,294 escalation point.
    """
    def line(context):
        return sl.render({
            'session_id': 'none',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            })

    assert line(452_000) != line(700_000)
    assert '2.1x over' in line(452_000)
    assert '3.2x over' in line(700_000)


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
    start with the size against the 215,854 no-state point and fit a
    narrow split.
    """
    visible = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': '/x/myproject'},
        'context_window': {'total_input_tokens': 150_000},
        }))
    assert visible.startswith('150K/215K')
    assert 'handoff in' in visible.split('$')[0]
    assert len(visible) <= 64
