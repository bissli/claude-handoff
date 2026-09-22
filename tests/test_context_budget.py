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


def test_every_model_is_held_to_the_same_cost_per_turn():
    """Verify targets are derived from price, not set per model by hand.

    Mutation: hard-coding a token target per model, which is how a
    costlier model ends up with the same room and quietly costs more.
    Oracle: differential - on the tiers whose cost parity clears the
    compaction cycle floor, each derived target must reproduce the
    declared dollar budget when run back through the cost formula, and
    the costlier of the two must be given the smaller target.
    """
    slow = 1_200
    for tier in ('fable-5-1', 'opus-5-5'):
        target = budget.target_tokens(tier, slow)
        assert abs(budget.cost_per_turn(target, tier)
                   - budget.COST_PER_TURN_TARGET) < 0.02
    assert (budget.target_tokens('fable-5-1', slow)
            < budget.target_tokens('opus-5-5', slow))


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
    assert budget.model_tier('claude-sonnet-5') is None
    for tier, price in (('opus-5-5', 0.20), ('opus', 0.50),
                        ('fable-5-1', 0.25), ('fable', 1.00)):
        assert abs(budget.cost_per_turn(1_000_000, tier)
                   - price * budget.CALLS_PER_TURN) < 1e-9


def test_a_cheap_model_is_left_alone(monkeypatch, capsys, tmp_path):
    """Verify a Sonnet or Haiku session raises no warning at all.

    Mutation: model_tier falling back to 'opus-5-5' for an unlisted model,
    which is what makes a plugin nag about a session whose whole cost is
    a few cents and train the user to ignore it.
    Oracle: a spy on stdout - the same 600K transcript prints on opus and
    prints nothing on sonnet.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    assert budget.model_tier('claude-sonnet-5') is None
    assert budget.model_tier('claude-haiku-4-5') is None

    def run(model, session):
        path = tmp_path / f'{session}.jsonl'
        path.write_text(json.dumps({
            'type': 'assistant',
            'message': {
                'id': 'm1', 'role': 'assistant', 'model': model,
                'usage': {'cache_read_input_tokens': 600_000,
                          'cache_creation_input_tokens': 0, 'input_tokens': 0},
                },
            }))
        payload = {'session_id': session, 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        return capsys.readouterr().out.strip()

    assert run('claude-opus-5-5', 'a')
    assert run('claude-sonnet-5', 'b') == ''


def test_the_over_band_holds_the_dollar_limit_and_the_order():
    """Verify over-budget means the dollar limit and never undercuts the
    target.

    Mutation: restoring over = target * (limit / target ratio), which on
    a fast-growing fable session moves "overpriced" from $2.20 to $5 a
    turn because the cycle floor inflates the target it scales; or
    dropping the max() clamp, which puts the loudest band below the
    budget one.
    Oracle: the declared dollar limit run back through the cost formula
    where cost decides, and the target itself where the cycle floor has
    passed the limit.
    """
    for tier in budget.CACHE_READ_PER_MTOK:
        seeded = budget.target_tokens(tier)
        over = budget.over_budget_tokens(tier, seeded)
        if budget.tokens_for_cost(budget.COST_PER_TURN_LIMIT, tier) > seeded:
            assert abs(budget.cost_per_turn(over, tier)
                       - budget.COST_PER_TURN_LIMIT) < 0.02
        else:
            assert over == seeded
        for per_call in (1_200, 1_900, 2_500, 6_300, 10_000):
            target = budget.target_tokens(tier, per_call)
            assert budget.over_budget_tokens(tier, target) >= target
    assert budget.over_budget_tokens('fable', 400_200) == 400_200


def test_the_over_band_prices_a_turn_in_dollars_not_in_tokens():
    """Verify the loudest band names what a turn costs, not a ratio of
    two token counts.

    Mutation: printing context/target in the band-2 message. It reads
    1.2x at the very point the turn costs 1.4x the target, so the number
    that decides whether to keep going is understated by a fifth.
    Oracle: hand-computed - fable crosses the over band at 250,000
    tokens, where a turn costs $2.20, which is 3.55x the $0.62 target,
    while the same context against fable's 206,600 target is 1.21x.
    """
    target = budget.target_tokens('fable')
    assert target == 206_600
    band, message = cb.compose(250_000, 'fable', 1_900, target,
                               cb.latched_handoff(target, 1_900, 0))
    assert band == 2
    assert '$2.20' in message
    assert '3.5x' in message
    assert '1.2x' not in message


def test_the_countdown_rounds_up_so_it_never_sticks():
    """Verify turns-left counts by ceiling, not floor-with-a-floor.

    Mutation: restoring max(1, int(...)), which shows "in 1" from 1.9
    turns of room all the way to the threshold - two turns reading the
    same - and overstates sub-turn room as a full turn.
    Oracle: hand-computed at 1,900 tokens a call on opus - 23,408 tokens
    of room to the 290,000 handoff point is 1.4 turns, which must read
    "handoff in 2", and 50,000 to the 350,000 target is 2.99 turns,
    "About 3".
    """
    line = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': '/x/proj'},
        'context_window': {'total_input_tokens': 266_592},
        }))
    assert 'handoff in 2' in line
    _, message = cb.compose(300_000, 'opus-5-5', 1_900, 350_000, 290_000)
    assert 'About 3 turns' in message


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


def test_growth_falls_back_on_a_short_series():
    """Verify a young session uses the documented fallback, not zero.

    Mutation: returning 0 or the raw difference when fewer than five
    calls exist, which would make the reserve collapse and the warning
    fire on the session's first turn.
    Oracle: the module's own declared fallback constant.
    """
    assert cb.growth_per_call([50_000, 60_000]) == budget.FALLBACK_GROWTH_PER_CALL
    assert cb.growth_per_call([]) == budget.FALLBACK_GROWTH_PER_CALL


def test_reserve_holds_a_floor_for_a_slow_session():
    """Verify a barely-growing session still gets room to hand off.

    Mutation: dropping the MIN_RESERVE_TOKENS floor, so a session growing
    100 tokens a call reserves ~4K and the warning arrives with no room
    left to write anything.
    Oracle: hand-computed - 2 turns * 8.8 calls * 100 * 1.5 = 2,640,
    which is below the floor and must be lifted to it. The floor holds
    on a small target too, where a quarter of the budget is less than
    the floor itself.
    """
    assert budget.reserve_tokens(350_000, 100) == budget.MIN_RESERVE_TOKENS
    assert budget.reserve_tokens(206_600, 100) == budget.MIN_RESERVE_TOKENS


def test_reserve_is_capped_so_it_cannot_swallow_the_session():
    """Verify a fast-growing session cannot reserve the whole budget.

    Mutation: removing the MAX_RESERVE_FRACTION ceiling. A session
    adding 20K a call then reserves everything the post-compaction
    clamp allows, 227,000 of a 350,000 target, and is warned at 123,000
    - the first turn of every cycle, forever.
    Oracle: hand-computed - the cap is a quarter of 350,000, and holds
    at every rate past the one that saturates it.
    """
    for per_call in (6_000, 20_000, 100_000):
        assert budget.reserve_tokens(350_000, per_call) == 87_500


def test_bands_fire_in_order_and_not_before_the_handoff_point():
    """Verify each band starts exactly where the arithmetic says it does.

    Mutation: a flipped comparison in compose, or the handoff band
    keyed off the target instead of target-minus-reserve, either of
    which shifts every warning by a full reserve.
    Oracle: hand-computed at 1,900 tokens/call - the reserve is
    2 * 1,900 * 8.8 * 1.5 = 50,160, below the 60,000 floor and lifted to
    it, so handoff starts at 290,000, the target at 350,000, and
    over-budget at 500,000.
    """
    per_call = 1_900
    assert budget.reserve_tokens(350_000, per_call) == 60_000
    assert cb.compose(289_999, 'opus-5-5', per_call, 350_000, 290_000)[0] == -1
    assert cb.compose(290_000, 'opus-5-5', per_call, 350_000, 290_000)[0] == 0
    assert cb.compose(349_999, 'opus-5-5', per_call, 350_000, 290_000)[0] == 0
    assert cb.compose(350_000, 'opus-5-5', per_call, 350_000, 290_000)[0] == 1
    assert cb.compose(499_999, 'opus-5-5', per_call, 350_000, 290_000)[0] == 1
    assert cb.compose(500_000, 'opus-5-5', per_call, 350_000, 290_000)[0] == 2


def test_handoff_warning_arrives_earlier_when_the_session_fills_faster():
    """Verify the warning point tracks growth instead of a fixed number.

    Mutation: replacing the measured per-call growth with a constant, the
    whole point of the design - a session reading large files would then
    be warned at the same token count as a chat and blow past the target
    mid-handoff.
    Oracle: differential at a context of 270,000 - the slow rate puts
    the point at 290,000 and stays silent, the fast rate puts it at
    262,500 and is already warning.
    """
    slow = cb.compose(270_000, 'opus-5-5', 1_900, 350_000,
                      cb.latched_handoff(350_000, 1_900, 0))
    fast = cb.compose(270_000, 'opus-5-5', 6_000, 350_000,
                      cb.latched_handoff(350_000, 6_000, 0))
    assert slow[0] == -1
    assert fast[0] == 0


def test_message_names_the_numbers_the_reader_has_to_act_on():
    """Verify the warning text carries context, target, and turns left.

    Mutation: a message that says only "context is large", which gives
    the reader nothing to decide with.
    Oracle: the hand-computed strings for a 300K context on a 350K
    target growing 16,720 tokens a turn.
    """
    _, message = cb.compose(300_000, 'opus-5-5', 1_900, 350_000, 290_000)
    assert '300K' in message
    assert '350K' in message
    assert 'handoff' in message
    assert '16K a turn' in message


def test_the_warning_names_the_skill_the_plugin_ships():
    """Verify every warning band names the bundled handoff skill.

    Mutation: renaming skills/handoff/, or rewording a band so the
    warning names a command that no longer exists - band 2 included,
    since a floor-inflated target skips band 1 and band 2 is then the
    only message carrying the exit. The word boundary is what makes a
    rename to a longer name (handoff2) fail rather than pass on the
    prefix.
    Oracle: the skill's own frontmatter name on disk, matched against
    the text of all three bands.
    """
    skill = os.path.join(HERE, '..', 'skills', 'handoff', 'SKILL.md')
    with open(skill) as handle:
        front = handle.read().split('---\n', 2)[1]
    name = re.search(r'^name:\s*(\S+)', front, re.M).group(1)
    for context in (300_000, 360_000, 600_000):
        _, message = cb.compose(context, 'opus-5-5', 1_900, 350_000, 290_000)
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
    context, model, per_call = cb.read_transcript(str(path))
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
        return cb.read_transcript(str(path))

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
        return cb.read_transcript(str(path))

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
    context, model, per_call = cb.read_transcript(str(tmp_path / 'nope.jsonl'))
    assert context == 0
    assert per_call == budget.FALLBACK_GROWTH_PER_CALL


def test_subagent_turns_are_never_announced(monkeypatch, capsys, tmp_path):
    """Verify a subagent's own context never raises a compaction warning.

    Mutation: dropping the agent_id guard in main. A subagent cannot
    compact and its context dies with it, so every delegated call would
    fire a warning the user can do nothing about.
    Oracle: a spy on stdout - the main-thread payload prints, the
    subagent payload with the identical transcript does not.
    """
    path = tmp_path / 's.jsonl'
    path.write_text(json.dumps({
        'type': 'assistant',
        'message': {
            'id': 'm1', 'role': 'assistant', 'model': 'claude-opus-5-5',
            'usage': {'cache_read_input_tokens': 600_000,
                      'cache_creation_input_tokens': 0, 'input_tokens': 0},
            },
        }))
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
    Oracle: a spy on stdout across three runs - warn, silence, and warn
    again once a compaction has dropped the context and it climbs back.
    """
    state = tmp_path / 'state'
    monkeypatch.setattr(cb, 'STATE_DIR', str(state))
    path = tmp_path / 's.jsonl'

    def write(context):
        path.write_text(json.dumps({
            'type': 'assistant',
            'message': {
                'id': f'm{context}', 'role': 'assistant',
                'model': 'claude-opus-5-5',
                'usage': {'cache_read_input_tokens': context,
                          'cache_creation_input_tokens': 0, 'input_tokens': 0},
                },
            }))

    def run():
        payload = {'session_id': 'S', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        return capsys.readouterr().out.strip()

    write(300_000)
    assert 'handoff' in run()
    write(305_000)
    assert run() == ''
    write(120_000)
    assert run() == ''
    write(300_000)
    assert 'handoff' in run()


def test_a_state_file_without_the_latch_cannot_re_announce_a_band(monkeypatch,
                                                                  capsys,
                                                                  tmp_path):
    """Verify the one unlatched turn on upgrade does not re-warn.

    Mutation: storing the freshly computed band unconditionally. A file
    written before the handoff point was latched carries no such key, so
    that turn re-derives the point freely and it can land above the
    context - the band computes as -1, erases the memory of the warning
    already given, and the same band fires a second time when the
    context climbs back.
    Oracle: a spy on stdout across the two turns after a legacy file -
    at 276,000 the re-derived point is 290,000 and nothing is said, and
    at 291,000, back over that point, nothing may be said either.
    """
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'U.json').write_text(json.dumps({
        'band': 0, 'growth_per_call': 3_000, 'target': 350_000,
        'context': 275_000,
        }))
    monkeypatch.setattr(cb, 'STATE_DIR', str(state))
    path = tmp_path / 's.jsonl'

    def run(contexts):
        records = [{
            'type': 'assistant',
            'message': {
                'id': f'm{value}', 'role': 'assistant',
                'model': 'claude-opus-5-5',
                'usage': {'cache_read_input_tokens': value,
                          'cache_creation_input_tokens': 0,
                          'input_tokens': 0},
                },
            } for value in contexts]
        path.write_text('\n'.join(json.dumps(r) for r in records))
        payload = {'session_id': 'U', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        return capsys.readouterr().out.strip()

    assert run([266_500, 268_400, 270_300, 272_200, 274_100, 276_000]) == ''
    assert run([285_000, 286_500, 288_000, 289_500, 291_000]) == ''


def test_the_hook_and_the_gauge_never_name_a_different_threshold(monkeypatch,
                                                                 capsys,
                                                                 tmp_path):
    """Verify the amber bar and the hook's band cross together.

    Mutation: re-deriving the handoff point in compose, or in
    session_state, rather than reading the latched one. The two
    surfaces then disagree wherever the rate has moved since the latch
    was set - the bar reads "handoff now" for turns on end while the
    hook says nothing, which is the one thing the status line promises
    cannot happen.
    Oracle: differential across two surfaces - the rendered line is
    amber or red on exactly the turns whose stored band is 0 or more,
    on a burst that latches the point at 264,205 followed by a
    slowdown that would re-derive it further out.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    burst = [240_000 + 6_000 * step for step in range(6)]
    series = burst + [burst[-1] + 100 * step for step in range(1, 11)]

    seen = set()
    for upto in range(1, len(series) + 1):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(series[:upto])))
        payload = {'session_id': 'A', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        with open(tmp_path / 'state' / 'A.json') as handle:
            band = json.load(handle)['band']
        line = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
            'session_id': 'A',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': series[upto - 1]},
            }))
        warning = 'handoff now' in line or 'over' in line
        assert warning == (band >= 0), (series[upto - 1], band, line)
        seen.add(warning)
    assert seen == {False, True}


def test_no_threshold_rises_while_the_context_is_still_climbing(monkeypatch,
                                                                capsys,
                                                                tmp_path):
    """Verify a dip that is not a compaction releases neither latch.

    Mutation: testing `context < last_context` for the compaction that
    releases the latches, with no size to it. Billed context falls
    without a compaction - a cached block expiring, a tool result
    dropped - and a dip of a few hundred tokens then hands the session
    a fresh target, a fresh handoff point, and a rearmed band, which is
    the walking-backwards this latch exists to stop.
    Oracle: monotonicity of the state file against its own previous
    turn - across a burst, a 1,000-token dip, and more climbing, no
    stored threshold may rise and no stored band may fall.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    series = [232_000, 238_000, 244_000, 250_000, 256_000, 262_000,
              265_000, 264_000, 266_000, 268_000]

    stored = []
    for upto in range(1, len(series) + 1):
        path.write_text('\n'.join(json.dumps(_record(index, value))
                                  for index, value in enumerate(series[:upto])))
        payload = {'session_id': 'M', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        with open(tmp_path / 'state' / 'M.json') as handle:
            stored.append(json.load(handle))

    assert [row['target'] for row in stored] == \
        sorted((row['target'] for row in stored), reverse=True)
    assert [row['handoff'] for row in stored] == \
        sorted((row['handoff'] for row in stored), reverse=True)
    assert [row['band'] for row in stored] == sorted(row['band'] for row in stored)
    assert stored[-1]['handoff'] == 264_205


def test_the_handoff_point_falls_within_a_session_and_never_rises():
    """Verify a slowdown cannot walk the handoff point back outward.

    Mutation: max() in place of min() in latched_handoff, or dropping
    the latch and re-deriving the point from the rate measured this
    turn. Latching the target alone leaves the reserve free to shrink,
    which moves the point the gauge counts down to.
    Oracle: hand-computed - 3,000 a call puts the point at 270,800, and
    a fall back to 1,900, which on its own justifies 290,000, must not
    be granted; a rise to 6,000 must still pull it down to 262,500.
    """
    assert cb.latched_handoff(350_000, 3_000, 0) == 270_800
    assert cb.latched_handoff(350_000, 1_900, 0) == 290_000
    assert cb.latched_handoff(350_000, 1_900, 270_800) == 270_800
    assert cb.latched_handoff(350_000, 6_000, 270_800) == 262_500


def test_a_fast_session_is_not_told_to_hand_off_at_half_the_gauge():
    """Verify the warning cannot arrive before three quarters of the budget.

    Mutation: MAX_RESERVE_FRACTION back at a half. Every session past
    3,315 tokens a call saturates the reserve, and at a half that put
    the warning beside a half-filled bar - a broken gauge, whatever the
    arithmetic behind it says.
    Oracle: hand-computed against the bar itself - on a 350,000 target
    the saturated point is 262,500, which fills seven of the ten cells;
    at a half it was 175,000 and five.
    """
    for per_call in (5_000, 20_000, 100_000):
        point = cb.latched_handoff(350_000, per_call, 0)
        assert point == 262_500
        assert int(point / 350_000 * sl.BAR_CELLS) == 7


def test_the_gauge_never_hands_back_room_it_has_withdrawn(monkeypatch,
                                                          capsys,
                                                          tmp_path):
    """Verify the status line does not undo a handoff warning next turn.

    Mutation: re-deriving the handoff point in render, or in compose,
    from the live growth rate. A burst warns, the rate falls back, and
    the line returns to green with turns to spare - contradicting a
    warning the user has already been given and acted on.
    Oracle: a spy on the rendered line across two turns - a 6,000-a-call
    burst reaching 275,000 crosses the 262,500 point and must read
    amber, and a slowdown to 1,900 a call at 276,000, which on its own
    would put the point at 290,000, must not read green again.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'

    def run(contexts):
        records = [{
            'type': 'assistant',
            'message': {
                'id': f'm{index}', 'role': 'assistant',
                'model': 'claude-opus-5-5',
                'usage': {'cache_read_input_tokens': value,
                          'cache_creation_input_tokens': 0,
                          'input_tokens': 0},
                },
            } for index, value in enumerate(contexts)]
        path.write_text('\n'.join(json.dumps(r) for r in records))
        payload = {'session_id': 'L', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        return re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
            'session_id': 'L',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': contexts[-1]},
            }))

    burst = run([245_000, 251_000, 257_000, 263_000, 269_000, 275_000])
    assert 'handoff now' in burst
    calm = run([266_500, 268_400, 270_300, 272_200, 274_100, 276_000])
    assert 'handoff now' in calm


def test_a_barely_growing_session_never_reads_as_zero_growth():
    """Verify the growth rate never prints as 0K a turn.

    Mutation: restoring the bare integer floor for the rate, which
    prints "growing 0K a turn" for a session measured under 114 tokens
    a call - a statement the reader can only parse as broken.
    Oracle: hand-computed - 100 tokens a call is 880 a turn, under the
    1K floor, so the message must carry "<1K a turn".
    """
    _, message = cb.compose(300_000, 'opus-5-5', 100, 350_000, 290_000)
    assert '<1K a turn' in message
    assert ' 0K a turn' not in message


def test_the_target_falls_within_a_session_and_never_rises():
    """Verify the latch is one-directional and released only by a drop.

    Mutation: max() in place of min() in latched_target, or seeding an
    empty latch from the measured rate rather than the fallback - either
    lets the target rise mid-session, which is what walks the gauge
    backwards from over-budget to "handoff now".
    Oracle: hand-computed - fable's fallback seed is 206,600, and at
    900 tokens a call the cycle floor is 123,000 + 4.4 * 900 = 162,600,
    so a quiet stretch pulls the latch down there; a 6,000-per-call
    burst, which on its own justifies 123,000 + 44 * 6,000 = 387,000,
    must still read 162,600.
    """
    assert budget.target_tokens('fable', 6_000) == 387_000
    assert cb.latched_target('fable', 6_000, 0) == 206_600
    assert cb.latched_target('fable', 900, 206_600) == 162_600
    assert cb.latched_target('fable', 6_000, 162_600) == 162_600
    # A file written before the latch existed holds a target it would
    # never grant now, so the seed clamps that on the way in.
    assert cb.latched_target('fable', 6_000, 563_000) == 206_600


def test_a_growth_burst_never_widens_the_budget(monkeypatch, capsys,
                                                tmp_path):
    """Verify a session already past its budget is not handed more room.

    Mutation: deriving the target in compose or in render from the rate
    measured this turn - the shipped defect. A fable session that starts
    reading large files lifts its measured rate from 900 to 4,537 a
    call, the cycle floor lifts the target from 162,600 to 322,628 with
    it, and a context shown as over budget one turn reads "handoff now"
    with room to spare the next.
    Oracle: differential against the defect - replaying a quiet climb
    followed by a 30,000-a-call burst, the stored target must never
    exceed the one before it, the status line must never leave red once
    it has entered it, and the over band must still fire. At the burst
    the unlatched target would be 322,628, which renders amber at a
    context of 206,300 and pushes the over boundary out to 546,720, past
    every context the session reaches.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    series = [170_000, 170_900, 171_800, 172_700, 173_600, 174_500,
              175_400, 176_300, 206_300, 236_300, 266_300]

    def run(upto):
        records = [{
            'type': 'assistant',
            'message': {
                'id': f'm{index}', 'role': 'assistant',
                'model': 'claude-fable-5',
                'usage': {'cache_read_input_tokens': value,
                          'cache_creation_input_tokens': 0,
                          'input_tokens': 0},
                },
            } for index, value in enumerate(series[:upto])]
        path.write_text('\n'.join(json.dumps(r) for r in records))
        payload = {'session_id': 'B', 'transcript_path': str(path)}
        monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
        cb.main()
        capsys.readouterr()
        with open(tmp_path / 'state' / 'B.json') as handle:
            stored = json.load(handle)['target']
        line = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
            'session_id': 'B',
            'model': {'id': 'claude-fable-5', 'display_name': 'Fable 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': series[upto - 1]},
            }))
        return stored, line

    targets, lines = [], []
    for upto in range(1, len(series) + 1):
        stored, line = run(upto)
        targets.append(stored)
        lines.append(line)
    assert budget.target_tokens('fable', 4_537) == 322_628
    assert targets == sorted(targets, reverse=True)
    assert targets[-1] == 162_600
    entered = next(index for index, line in enumerate(lines) if 'over' in line)
    assert all('over' in line for line in lines[entered:])
    assert '1.3x over' in lines[8]
    with open(tmp_path / 'state' / 'B.json') as handle:
        assert json.load(handle)['band'] == 2

    # A compaction is the one thing that releases the latch, and it
    # reseeds rather than jumping to whatever the burst justified.
    series.append(120_000)
    stored, _ = run(len(series))
    assert stored == budget.target_tokens('fable')


class _Stdin:
    """Minimal stdin stand-in returning a fixed payload to json.load.
    """

    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, *args: object) -> str:
        return self.text


def test_an_expensive_model_gets_a_workable_compaction_cycle():
    """Verify the target a session is seeded with clears where a
    compaction lands, by real turns.

    Mutation: deriving the target from cost alone. Fable's cost-parity
    target is 70K while a compaction lands at 123K, so every fable
    session would be seeded below the point it restarts at.
    Oracle: hand-computed - at the 1,900-per-call fallback the cycle
    floor is 123,000 + 5 * 1,900 * 8.8 = 206,600, which beats fable's
    cost parity of 70,455, and every tier must clear MIN_CYCLE_TURNS.
    """
    assert budget.target_tokens('fable') == 206_600
    assert budget.tokens_for_cost(budget.COST_PER_TURN_TARGET, 'fable') \
        == 70_455
    per_turn = budget.FALLBACK_GROWTH_PER_CALL * budget.CALLS_PER_TURN
    for tier in budget.CACHE_READ_PER_MTOK:
        seeded = cb.latched_target(tier, budget.FALLBACK_GROWTH_PER_CALL, 0)
        assert seeded == budget.target_tokens(tier)
        cycle = (seeded - budget.POST_COMPACTION_TOKENS) / per_turn
        assert cycle >= budget.MIN_CYCLE_TURNS - 0.01


def test_a_fast_session_is_told_how_little_a_compaction_buys():
    """Verify the budget message counts the cycle at the live rate.

    Mutation: computing the cycle from FALLBACK_GROWTH_PER_CALL, or from
    the target's own floor, which promises five more turns to a session
    that will get two. The latched target no longer moves with growth,
    so this sentence is the only place a fast session learns its cycle
    has collapsed.
    Oracle: hand-computed - fable 5.1 is held to 281,818, a compaction
    lands at 123,000, and at 10,000 tokens a call a turn eats 88,000,
    so the cycle is 158,818 / 88,000 = 1.8 turns.
    """
    target = budget.target_tokens('fable-5-1')
    band, message = cb.compose(target, 'fable-5-1', 10_000, target,
                               cb.latched_handoff(target, 10_000, 0))
    assert band == 1
    assert 'about 2 more turns' in message


def test_the_warning_never_lands_below_where_a_compaction_restarts():
    """Verify the handoff point stays above the post-compaction floor.

    Mutation: leaving the reserve unclamped. On an expensive model the
    reserve exceeds the gap between the target and where a compaction
    lands, putting the warning below the restart point - so it fires on
    the first turn of every cycle and the user learns to ignore it.
    Oracle: differential across rates - every target the code can adopt,
    minus its reserve, must clear POST_COMPACTION_TOKENS, which holds
    because no derived target falls below the cycle floor; and on a
    floor-set target run against a faster rate the clamp is what holds
    the warning at the restart point rather than the ceiling.
    """
    for tier in budget.CACHE_READ_PER_MTOK:
        for per_call in (1_200, 1_900, 2_500, 4_000, 8_000):
            assert (budget.target_tokens(tier, per_call)
                    >= budget.cycle_floor_tokens(per_call))
            for target in (budget.target_tokens(tier, per_call),
                           cb.latched_target(tier, per_call, 0)):
                handoff = target - budget.reserve_tokens(target, per_call)
                assert handoff >= budget.POST_COMPACTION_TOKENS
    floored = budget.cycle_floor_tokens(1_200)
    assert budget.reserve_tokens(floored, 8_000) == \
        floored - budget.POST_COMPACTION_TOKENS


def test_the_gauge_stays_readable_past_the_budget():
    """Verify two different over-budget sizes do not render identically.

    Mutation: keeping the bar past the budget. It clamps to full, so
    1.1x and 3x print the same glyph - and past the budget is exactly
    where the reader needs to tell them apart.
    Oracle: differential - the same session at 452K and at 700K must
    produce different text.
    """
    def line(context):
        return sl.render({
            'session_id': 'none',
            'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
            'workspace': {'current_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            })

    assert line(452_000) != line(700_000)
    assert '1.3x over' in line(452_000)
    assert '2.0x over' in line(700_000)


def test_the_gauge_marks_its_cost_as_an_estimate():
    """Verify the gauge hedges the figure every Stop band already hedges.

    Mutation: dropping the ~ from the cost field, leaving a bare
    two-decimal figure that reads as the price of the next turn rather
    than as an estimate of cached-context re-reading, which is all the
    model covers.
    Oracle: differential across the two surfaces - the rendered field
    against the Stop band's own wording at the same context and tier.
    """
    visible = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5.5'},
        'workspace': {'current_dir': '/x/proj'},
        'context_window': {'total_input_tokens': 517_000},
        }))
    assert '~$0.91/t' in visible
    target = budget.target_tokens('opus-5-5')
    _, message = cb.compose(517_000, 'opus-5-5', 2_160, target,
                            cb.latched_handoff(target, 2_160, 0))
    assert 'about $0.91 a turn' in message


def test_the_decision_numbers_survive_a_narrow_pane():
    """Verify size and action lead the line, and the line stays short.

    Mutation: putting the directory or the model first, as most status
    lines do. A pane is cut from the right, so the two fields that carry
    the decision are the ones lost.
    Oracle: hand-checked - the visible line with color stripped must
    start with the size and fit a narrow split.
    """
    visible = re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': 'none',
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': '/x/myproject'},
        'context_window': {'total_input_tokens': 248_000},
        }))
    assert visible.startswith('248K/352K')
    assert 'handoff in' in visible.split('$')[0]
    assert len(visible) <= 64
