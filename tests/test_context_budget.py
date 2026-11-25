"""Tests for the context-budget Stop hook and the status line."""

import importlib.util
import json
import os
import re
import sys

import pytest

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

# A cycle climbing 10,000 tokens a call from a 70,000 floor: at the
# default W of 200,000 its handoff point is 270,000 and its escalation
# point 299,000.
CLIMB = [70_000 + 10_000 * step for step in range(90)]


@pytest.fixture(autouse=True)
def _default_work_budget(monkeypatch):
    """Hold W at its default whatever the caller's shell sets.
    """
    monkeypatch.delenv('HQ_WORK_BUDGET', raising=False)


class _Stdin:
    """Minimal stdin stand-in returning a fixed payload to json.load.
    """

    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, *args: object) -> str:
        return self.text


def _record(index, value, nested=False, sidechain=False):
    """Build one transcript record billing `value`, or nothing if 0.
    """
    counts = {'cache_read_input_tokens': value,
              'cache_creation_input_tokens': 0, 'input_tokens': 0}
    usage = dict(counts) if not nested else {
        'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0,
        'input_tokens': 0, 'iterations': [counts]}
    return {'type': 'assistant', 'isSidechain': sidechain,
            'message': {'id': f'm{index}', 'role': 'assistant',
                        'model': 'claude-opus-5-5', 'usage': usage}}


def _write(path, series):
    """Write one record per billed context in `series` to `path`.
    """
    path.write_text('\n'.join(json.dumps(_record(index, value))
                              for index, value in enumerate(series)))


def _stop(monkeypatch, capsys, path, session='S', **extra):
    """Run the Stop hook once on `path` and return what it printed.
    """
    payload = {'session_id': session, 'transcript_path': str(path), **extra}
    monkeypatch.setattr(sys, 'stdin', _Stdin(json.dumps(payload)))
    cb.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else {}


def _line(context, session='none'):
    """Render the status line for one context, color codes kept.
    """
    return sl.render({
        'session_id': session,
        'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5.5'},
        'workspace': {'current_dir': '/x/myproject'},
        'context_window': {'total_input_tokens': context},
        })


def test_the_handoff_point_is_the_floor_plus_the_work_budget(monkeypatch):
    """Verify the point is F + W, capped one write short of compaction.

    Mutation: W added to the current context or to a resume end in
    place of F; the cap dropped or set at AUTO_COMPACT_TOKENS itself.
    Oracle: hand-computed - 70,000 + 200,000 = 270,000; and 967,000 -
    29,000 = 938,000 for any floor within W of it.
    """
    assert budget.handoff_point(70_000) == 270_000
    assert budget.handoff_point(737_999) == 937_999
    assert budget.handoff_point(738_001) == 938_000
    assert budget.handoff_point(900_000) == 938_000


def test_hq_work_budget_replaces_the_default(monkeypatch):
    """Verify HQ_WORK_BUDGET sets W, and a malformed value raises.

    Mutation: the variable read but ignored, added to the default, a
    malformed value silently replaced by the default, or a zero or
    negative W accepted, which puts the point at or under F and rings
    band 1 on every cycle.
    Oracle: hand-computed - 70,000 + 150,000 = 220,000; an empty value
    reads as unset.
    """
    monkeypatch.setenv('HQ_WORK_BUDGET', '150000')
    assert budget.handoff_point(70_000) == 220_000
    monkeypatch.setenv('HQ_WORK_BUDGET', '')
    assert budget.handoff_point(70_000) == 270_000
    for malformed in ('150K', '0', '-300000'):
        monkeypatch.setenv('HQ_WORK_BUDGET', malformed)
        with pytest.raises(ValueError):
            budget.handoff_point(70_000)


def test_a_restart_in_place_restarts_the_floor(tmp_path):
    """Verify F is the first call after the last restart in place.

    Mutation: F taken as the transcript's first call, its minimum, or
    the call after any fall at all, so a cache block expiring mid-cycle
    moves the point.
    Oracle: a drop of 61,501 restarts the cycle and one of 61,500 does
    not, half of RESTART_IN_PLACE_TOKENS being 61,500.
    """
    path = tmp_path / 's.jsonl'
    _write(path, [70_000, 200_000, 138_499, 150_000])
    assert cb.read_transcript(str(path)) == (150_000, 138_499)
    _write(path, [70_000, 200_000, 138_500, 150_000])
    assert cb.read_transcript(str(path)) == (150_000, 70_000)
    _write(path, [70_000, 300_000, 120_000, 200_000, 125_000, 130_000])
    assert cb.read_transcript(str(path)) == (130_000, 125_000)


def test_an_unbilled_record_changes_nothing_wherever_it_lands(tmp_path):
    """Verify a record that billed nothing never reads as a restart.

    Mutation: dropping the billed test, so the record enters the series
    as a context of zero and the call after it becomes F.
    Oracle: invariance under insertion - splicing the record at every
    position of a fixed climb returns the same pair.
    """
    path = tmp_path / 's.jsonl'
    clean = list(starmap(_record, enumerate(CLIMB[:10])))
    for position in range(len(clean) + 1):
        spliced = list(clean)
        spliced.insert(position, _record('x', 0))
        path.write_text('\n'.join(json.dumps(r) for r in spliced))
        assert cb.read_transcript(str(path)) == (160_000, 70_000)


def test_a_call_billed_one_level_down_still_counts(tmp_path):
    """Verify counts under `iterations` are read, not discarded as zero.

    Mutation: concluding a record billed nothing from its top-level
    counts alone, which drops every nested record from the series.
    Oracle: the same climb written nested reads as it does flat.
    """
    path = tmp_path / 's.jsonl'
    path.write_text('\n'.join(
        json.dumps(_record(index, value, nested=True))
        for index, value in enumerate(CLIMB[:10])))
    assert cb.read_transcript(str(path)) == (160_000, 70_000)


def test_sidechain_records_are_left_out(tmp_path):
    """Verify a subagent's records never enter the session's series.

    Mutation: dropping the isSidechain test, so a subagent's small
    context reads as a restart in place and its last call as the
    current context.
    Oracle: a 20,000 sidechain call after a 160,000 main call leaves
    the pair as the main calls alone give it.
    """
    path = tmp_path / 's.jsonl'
    records = list(starmap(_record, enumerate(CLIMB[:10])))
    records.append(_record('sub', 20_000, sidechain=True))
    path.write_text('\n'.join(json.dumps(r) for r in records))
    assert cb.read_transcript(str(path)) == (160_000, 70_000)


def test_a_missing_transcript_stays_silent(monkeypatch, capsys, tmp_path):
    """Verify the hook never breaks a turn over its own input.

    Mutation: letting the OSError escape read_transcript. A Stop hook
    that raises turns every turn into an error banner.
    Oracle: the documented (0, 0) return, and nothing printed.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    missing = tmp_path / 'nope.jsonl'
    assert cb.read_transcript(str(missing)) == (0, 0)
    assert _stop(monkeypatch, capsys, missing) == {}


def test_band_one_sits_a_handoff_write_past_band_zero(monkeypatch, capsys,
                                                      tmp_path):
    """Verify band 0 fires at F + W, band 1 one write past, and only band 1
    rings.

    Mutation: the escalation point set at the handoff point or at the
    compaction cap; a flipped comparison in compose; or the desktop
    notification raised on band 0 too.
    Oracle: hand-computed on the climb - 270,000 and 299,000, with the
    compose calls straddling both by one token.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    _write(path, CLIMB[:21])
    announced = _stop(monkeypatch, capsys, path)
    stored = json.loads((tmp_path / 'state' / 'S.json').read_text())
    assert (stored['handoff'], stored['escalate']) == (270_000, 299_000)
    assert 'terminalSequence' not in announced
    _write(path, CLIMB[:24])
    announced = _stop(monkeypatch, capsys, path)
    assert 'terminalSequence' in announced
    assert cb.compose(269_999, 270_000, 299_000)[0] == -1
    assert cb.compose(270_000, 270_000, 299_000)[0] == 0
    assert cb.compose(298_999, 270_000, 299_000)[0] == 0
    assert cb.compose(299_000, 270_000, 299_000)[0] == 1


def test_the_warning_names_its_numbers_and_the_skill():
    """Verify each band names the context, the handoff point, and /handoff.

    Mutation: a message that prints the escalation point in place of the
    handoff point, or one naming a skill that no longer exists. The word
    boundary makes a rename to a longer name fail.
    Oracle: the skill's frontmatter name on disk, and hand-computed
    strings for a 280K context on a 270K point.
    """
    skill = os.path.join(HERE, '..', 'skills', 'handoff', 'SKILL.md')
    with open(skill) as handle:
        front = handle.read().split('---\n', 2)[1]
    name = re.search(r'^name:\s*(\S+)', front, re.M).group(1)
    _, message = cb.compose(280_000, 270_000, 299_000)
    assert message.startswith('Context 280K, at the 270K handoff point')
    assert '299K' not in message
    for context in (280_000, 310_000):
        band, message = cb.compose(context, 270_000, 299_000)
        assert band >= 0
        assert re.search(rf'/{name}\b', message)


def test_subagent_turns_are_never_announced(monkeypatch, capsys, tmp_path):
    """Verify a subagent's own Stop never raises a warning.

    Mutation: dropping the agent_id guard in main.
    Oracle: the same transcript prints for the main thread and stays
    silent with agent_id set.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    _write(path, CLIMB[:30])
    assert _stop(monkeypatch, capsys, path, session='main-1')
    assert _stop(monkeypatch, capsys, path, session='sub-1',
                 agent_id='agent-9') == {}


def test_a_band_is_announced_once_and_rearmed_by_a_restart_in_place(
        monkeypatch, capsys, tmp_path):
    """Verify the warning fires on entry, stays quiet, then fires again.

    Mutation: writing the state unconditionally without comparing, which
    silences everything, or never writing it, which re-warns every turn.
    Oracle: warn at 270K, silence at 280K, silence at the 120K a restart
    in place drops to, and warn again at 320K, the new cycle's 120K
    floor plus 200K.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    after = [120_000 + 10_000 * step for step in range(21)]
    _write(path, CLIMB[:21])
    assert 'handoff' in _stop(monkeypatch, capsys, path)['systemMessage']
    _write(path, CLIMB[:22])
    assert _stop(monkeypatch, capsys, path) == {}
    _write(path, CLIMB[:22] + after[:1])
    assert _stop(monkeypatch, capsys, path) == {}
    _write(path, CLIMB[:22] + after[:20])
    assert _stop(monkeypatch, capsys, path) == {}
    _write(path, CLIMB[:22] + after)
    assert '320K handoff point' in _stop(monkeypatch, capsys,
                                         path)['systemMessage']


def test_the_hook_and_the_gauge_name_the_same_threshold(monkeypatch, capsys,
                                                        tmp_path):
    """Verify the gauge turns amber and red on the hook's own bands.

    Mutation: session_state reading a key the hook does not write, or
    the gauge deriving its own point, so the two cross on different
    turns.
    Oracle: the state the hook stored for the climb, 270,000 and
    299,000, straddled by one token on each side.
    """
    monkeypatch.setattr(cb, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    path = tmp_path / 's.jsonl'
    _write(path, CLIMB[:2])
    _stop(monkeypatch, capsys, path, session='G')
    assert _line(269_999, 'G').startswith(sl.GREEN)
    assert _line(270_000, 'G').startswith(sl.YELLOW)
    assert 'handoff now' in _line(298_999, 'G')
    assert _line(299_000, 'G').startswith(sl.RED)


def test_the_gauge_waits_for_the_first_stop(monkeypatch, tmp_path):
    """Verify no gauge shows before the hook has stored a point.

    Mutation: a zero handoff point rendered as a gauge, which divides by
    nothing or shows every context as over.
    Oracle: an empty state directory, and the plain line it must give.
    """
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path / 'state'))
    visible = re.sub(r'\x1b\[[0-9;]*m', '', _line(150_000))
    assert visible == 'Opus 5.5 myproject'


def test_the_decision_numbers_survive_a_narrow_pane(monkeypatch, tmp_path):
    """Verify the size leads the line and the line stays short.

    Mutation: putting the directory or the model first, as most status
    lines do. A pane is cut from the right, so the fields that carry the
    decision are the ones lost.
    Oracle: hand-written - the visible line for 150K against a stored
    270K point.
    """
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'N.json').write_text(json.dumps(
        {'band': -1, 'handoff': 270_000, 'escalate': 299_000,
         'context': 150_000}))
    monkeypatch.setattr(sl, 'STATE_DIR', str(state))
    visible = re.sub(r'\x1b\[[0-9;]*m', '', _line(150_000, 'N'))
    assert visible == '150K/270K  Opus 5.5 myproject'


def test_a_state_file_without_an_escalation_point_reads_one_write_past(
        monkeypatch, tmp_path):
    """Verify a state file an older hook wrote does not render as over.

    Mutation: a missing escalate key read as 0, so every context is past
    band 1 and the line shows red at any size.
    Oracle: an older file holding only a 290K handoff point - 150K is
    green, and red starts at 290,000 + 29,000 = 319,000.
    """
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'O.json').write_text(json.dumps(
        {'band': 2, 'growth_per_call': 1600, 'target': 350_000,
         'handoff': 290_000, 'context': 1}))
    monkeypatch.setattr(sl, 'STATE_DIR', str(state))
    assert _line(150_000, 'O').startswith(sl.GREEN)
    assert _line(318_999, 'O').startswith(sl.YELLOW)
    assert _line(319_000, 'O').startswith(sl.RED)
