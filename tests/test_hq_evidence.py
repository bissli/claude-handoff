"""Tests for the evidence standing kind: note, render, and contradictions."""

import contextlib
import io
import pathlib
import re

import pytest

from bin import hq

_SLUG = 'acme'


def _root(tmp_path, monkeypatch):
    """Create an HQ_ROOT with the HQ_* environment set; return the folder."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', '2026-10-13T12:00:00')
    monkeypatch.setenv('HQ_SESSION', 'sess')
    monkeypatch.setenv('HQ_HOST', 'host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / _SLUG


def _run(argv, stdin=None):
    """Call hq.main, returning (rc, stdout) with SystemExit caught."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            if stdin is not None:
                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr('sys.stdin', io.StringIO(stdin))
                    rc = hq.main(argv)
            else:
                rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue()


def _set(folder, heading, body):
    """Replace the body of one cursor section in HANDOFF.md."""
    path = folder / 'HANDOFF.md'
    text = path.read_text()
    text = re.sub(
        rf'^## {heading}\n.*?(?=^## |^<!-- hq:)', f'## {heading}\n{body}\n\n',
        text, count=1, flags=re.MULTILINE | re.DOTALL)
    path.write_text(text)


def _begin(tmp_path, monkeypatch):
    """Begin cycle 1 with Task set; return the folder."""
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _set(folder, 'Task', 'Probe the evidence kind.')
    return folder


def _evidence(headline, body, *flags):
    """Record one evidence item, measured unless flags name a grade."""
    grade = [] if '--grade' in flags else ['--grade', 'measured']
    return _run([
        'note', _SLUG, 'evidence', *grade, *flags, '--headline', headline, body])


def _last_line(folder):
    """The last line of standing.md."""
    return (folder / 'standing.md').read_text().splitlines()[-1]


def _block(folder):
    """The Standing block body of HANDOFF.md, between its markers."""
    text = (folder / 'HANDOFF.md').read_text()
    return text.split('## Standing\n', 1)[1].split('\n<!-- /hq:standing -->')[0]


def _item(item_id, headline, body='source: out/run.csv', cycle='1'):
    """Build one standing item dict for the renderer."""
    return {
        'id': item_id, 'prefix': item_id[0], 'cycle': cycle,
        'headline': headline, 'body': body,
        }


# --- note ---

def test_the_grade_and_the_open_mark_head_the_headline(tmp_path, monkeypatch):
    """Verify note writes '<grade>: ' or '<grade>, open: ' before the headline.

    Mutation: the grade written into the body, or --open dropped.
    Oracle: the stored lines SPEC section 3 shows.
    """
    folder = _begin(tmp_path, monkeypatch)
    assert _evidence('3 retries at 4 workers', 'source: out/run-04.csv')[0] == 0
    assert _last_line(folder) == (
        '- [e01] (c1) **measured: 3 retries at 4 workers** source: out/run-04.csv')
    assert _evidence(
        'Cap holds above 8 workers', 'source: out/run-07.csv',
        '--grade', 'inferred', '--open')[0] == 0
    assert _last_line(folder) == (
        '- [e02] (c1) **inferred, open: Cap holds above 8 workers**'
        ' source: out/run-07.csv')


@pytest.mark.parametrize(('argv', 'rc', 'message'), [
    (['claim', '--headline', 'H', 'body'], 2,
     ("hq note: kind must be decision, constraint, dead-end, or evidence;"
     " got 'claim'")),
    (['decision', '--grade', 'measured', '--headline', 'H', 'body'], 2,
     'hq note: --grade, --open and --contradicts apply to evidence only'),
    (['constraint', '--open', '--headline', 'H', 'body'], 2,
     'hq note: --grade, --open and --contradicts apply to evidence only'),
    (['evidence', '--headline', 'H', 'source: x'], 2,
     'hq note: evidence needs --grade measured, inferred, or asserted'),
    (['evidence', '--grade', 'guessed', '--headline', 'H', 'source: x'], 2,
     'hq note: evidence needs --grade measured, inferred, or asserted'),
    (['evidence', '--grade', 'measured', '--headline', 'H'], 2,
     'hq note: evidence needs its source as the body'),
    (['evidence', '--grade', 'measured', '--contradicts', 'e09',
      '--headline', 'H', 'source: x'], 1,
     'hq note: --contradicts names e09, which is not a live standing item'),
    ])
def test_note_refuses_a_malformed_evidence_item_and_writes_nothing(
        tmp_path, monkeypatch, argv, rc, message):
    """Verify each malformed note exits with its code and message, no write.

    Mutation: a flag accepted on another kind; a grade outside the three
    accepted; an empty body accepted; or an absent --contradicts id
    appended unchecked.
    Oracle: the messages and exit codes SPEC section 3 fixes, and
    standing.md byte-identical.
    """
    folder = _begin(tmp_path, monkeypatch)
    before = (folder / 'standing.md').read_bytes()
    got_rc, out = _run(['note', _SLUG, *argv])
    assert (got_rc, out.split(' - ')[0].rstrip()) == (rc, message)
    assert (folder / 'standing.md').read_bytes() == before


def test_contradicts_appends_the_id_and_refuses_a_retired_one(tmp_path, monkeypatch):
    """Verify --contradicts appends '; contradicts [id]' and refuses a retired id.

    Mutation: the live check reading item ids alone, so a retired id is
    accepted.
    Oracle: e01 live, then e01 retired; d01 as a target of another kind.
    """
    folder = _begin(tmp_path, monkeypatch)
    assert _evidence('Vendor caps retries at 3', 'source: vendor docs')[0] == 0
    assert _run(['note', _SLUG, 'decision', '--headline', 'Cap at 3'])[0] == 0
    assert _evidence('Client retries 5 times', 'source: src/client.py',
                     '--contradicts', 'd01')[0] == 0
    assert _last_line(folder).endswith('source: src/client.py; contradicts [d01]')
    assert _run(['supersede', _SLUG, 'e01', '--reason', 'docs withdrawn'])[0] == 0
    rc, out = _evidence('Client retries 4 times', 'source: x', '--contradicts', 'e01')
    assert rc == 1
    assert out.startswith('hq note: --contradicts names e01, which is not a live')


def test_a_batch_line_takes_the_flags_in_any_order_before_the_headline(
        tmp_path, monkeypatch):
    """Verify batch flags parse in either order, and after --headline are body.

    Mutation: the options group fixed to one order, or flags after the
    headline consumed as options.
    Oracle: three hand-written batch lines and their stored lines.
    """
    folder = _begin(tmp_path, monkeypatch)
    rc, _ = _run(['note', _SLUG, '--batch'], stdin=(
        'evidence --grade measured --open --headline "Cap holds" source: run-07\n'
        'evidence --open --grade asserted --headline "Cap is 3" source: docs\n'
        'evidence --grade inferred --headline "Cap is 5" source: x --open\n'))
    assert rc == 0
    lines = (folder / 'standing.md').read_text().splitlines()[-3:]
    assert lines == [
        '- [e01] (c1) **measured, open: Cap holds** source: run-07',
        '- [e02] (c1) **asserted, open: Cap is 3** source: docs',
        '- [e03] (c1) **inferred: Cap is 5** source: x --open',
        ]


def test_a_batch_flag_on_another_kind_is_refused(tmp_path, monkeypatch):
    """Verify a batch decision line carrying --grade is refused, the rest run.

    Mutation: the batch path skipping the evidence-only check.
    Oracle: rc 2 and only the second line's item stored.
    """
    folder = _begin(tmp_path, monkeypatch)
    rc, _ = _run(['note', _SLUG, '--batch'], stdin=(
        'decision --grade measured --headline "Cap at 3" body\n'
        'decision --headline "Cap at 4" body\n'))
    assert rc == 2
    assert _last_line(folder) == '- [d01] (c1) **Cap at 4** body'


def test_an_evidence_unfiled_bullet_draws_its_own_refusal(tmp_path, monkeypatch):
    """Verify an '- evidence:' Unfiled bullet names hq note evidence.

    Mutation: evidence: refused with the generic untyped message, which
    names decision: as a way past it.
    Oracle: the refusal line SPEC section 3 fixes.
    """
    folder = _begin(tmp_path, monkeypatch)
    _set(folder, 'Now', 'Measure the cap.')
    path = folder / 'HANDOFF.md'
    path.write_text(path.read_text().replace(
        '## Log\n', '## Unfiled\n- evidence: **Cap is 3** source: docs\n\n## Log\n'))
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 1
    assert out == (
        "hq finish: untyped Unfiled bullet: '- evidence: **Cap is 3** source: docs'"
        ' - record evidence with hq note <slug> evidence --grade ..., then'
        ' delete the bullet\n')


def test_standing_kind_evidence_lists_evidence_alone(tmp_path, monkeypatch):
    """Verify --kind evidence keeps the e items only.

    Mutation: 'evidence' missing from _KIND_PREFIX, so --kind refuses it
    or matches no prefix.
    Oracle: one decision and one evidence item.
    """
    _begin(tmp_path, monkeypatch)
    assert _run(['note', _SLUG, 'decision', '--headline', 'Cap at 3'])[0] == 0
    assert _evidence('Cap is 3', 'source: docs')[0] == 0
    rc, out = _run(['standing', _SLUG, '--kind', 'evidence'])
    assert rc == 0
    assert out.splitlines() == ['[e01] (c1) **measured: Cap is 3** source: docs']


# --- render ---

def test_uncited_evidence_is_counted_and_cited_evidence_prints_whole():
    """Verify the Evidence group prints cited items whole and counts the rest.

    Mutation: uncited evidence printed as [id] headline like other kinds,
    or the count line plural at 1.
    Oracle: e01 cited, e02 not; the lines SPEC section 3 shows.
    """
    items = [
        _item('e01', 'measured: Cap is 3', 'source: docs'),
        _item('e02', 'measured: Cap holds', 'source: run-07'),
        ]
    block = hq.render_standing(items, set(), _SLUG, whole_ids={'e01'})
    assert block.splitlines() == [
        '### Evidence',
        '[e01] (c1) **measured: Cap is 3**  +12c - hq standing acme e01',
        '+1 live evidence item - hq standing acme --kind evidence --grep <word>',
        ]


def test_the_count_line_is_plural_and_absent_when_all_are_cited():
    """Verify the count line's plural, and no count line when every item prints.

    Mutation: the count line printed at zero, or the plural dropped.
    Oracle: two uncited items, then both cited.
    """
    items = [_item('e01', 'measured: A'), _item('e02', 'measured: B')]
    block = hq.render_standing(items, set(), _SLUG, whole_ids=set())
    assert block.splitlines() == [
        '### Evidence',
        '+2 live evidence items - hq standing acme --kind evidence --grep <word>',
        ]
    block = hq.render_standing(items, set(), _SLUG, whole_ids={'e01', 'e02'})
    assert '+' not in ''.join(ln for ln in block.splitlines() if 'live evidence' in ln)
    assert not [ln for ln in block.splitlines() if 'live evidence' in ln]


def test_contradicted_by_marks_a_cited_item_until_the_contradictor_retires():
    """Verify 'contradicted by [e03]' follows the headline while e03 is live.

    Mutation: the mark built from every item's body, retired ones
    included, or placed after the holdback.
    Oracle: e03's body holds contradicts [e01]; e03 live, then not live.
    """
    items = [
        _item('e01', 'asserted: Cap is 3', 'source: docs'),
        _item('e03', 'inferred: Cap is 5', 'source: x; contradicts [e01]'),
        ]
    block = hq.render_standing(items, set(), _SLUG, whole_ids={'e01'})
    assert (
        '[e01] (c1) **asserted: Cap is 3** contradicted by [e03]'
        '  +12c - hq standing acme e01') in block.splitlines()
    block = hq.render_standing(items, {'e03'}, _SLUG, whole_ids={'e01'})
    assert 'contradicted by' not in block


def test_contradicted_by_lists_each_contradictor_once_in_id_order():
    """Verify the mark lists each contradicting id once, in numeric id order.

    Mutation: ids taken in list order or sorted as text, so e100 sorts
    before e99; or a body naming the target twice listing its id twice.
    Oracle: e100 and e99 contradict e01, e99's body naming it twice.
    """
    items = [
        _item('e01', 'asserted: Cap is 3', 'source: docs'),
        _item('e100', 'inferred: Cap is 5', 'source: x; contradicts [e01]'),
        _item('e99', 'measured: Cap is 4',
              'contradicts [e01] in run-09; contradicts [e01]'),
        ]
    block = hq.render_standing(items, set(), _SLUG, whole_ids={'e01'})
    assert block.splitlines()[1].startswith(
        '[e01] (c1) **asserted: Cap is 3** contradicted by [e99, e100]  +')


def test_evidence_never_sets_the_general_closing_line():
    """Verify uncited evidence alone prints no 'prints any item above' line.

    Mutation: an uncited evidence item setting the folded flag.
    Oracle: a cited decision and an uncited evidence item; then an
    uncited decision, whose closing line stays last.
    """
    items = [_item('d01', 'Cap at 3', ''), _item('e01', 'measured: Cap is 3')]
    block = hq.render_standing(items, set(), _SLUG, whole_ids={'d01'})
    assert 'prints any item above whole' not in block
    block = hq.render_standing(items, set(), _SLUG, whole_ids=set())
    assert block.splitlines()[-1] == 'hq standing acme <id> prints any item above whole'


def test_evidence_recorded_this_cycle_stays_counted_unless_cited(
        tmp_path, monkeypatch):
    """Verify this cycle's evidence is counted while this cycle's decision expands.

    Mutation: the this-cycle clause in whole_ids applied to evidence, so
    every claim a cycle records prints at the next resume.
    Oracle: a decision and two evidence items from cycle 1; the cursor
    cites e02 alone.
    """
    folder = _begin(tmp_path, monkeypatch)
    assert _run(['note', _SLUG, 'decision', '--headline', 'Cap at 3'])[0] == 0
    assert _evidence('Cap is 3', 'source: docs')[0] == 0
    assert _evidence('Cap holds', 'source: run-07')[0] == 0
    _set(folder, 'Now', 'Recheck [e02] against the run.')
    assert _run(['finish', _SLUG, '--log', 'c1'])[0] == 0
    lines = _block(folder).splitlines()
    assert '[d01] (c1) **Cap at 3**' in lines
    assert lines[lines.index('### Evidence') + 1:] == [
        '[e02] (c1) **measured: Cap holds**  +14c - hq standing acme e02',
        '+1 live evidence item - hq standing acme --kind evidence --grep <word>',
        ]


# --- contradiction advisory ---

def _contradiction_lines(out):
    """The contradiction advisory lines finish printed."""
    return [ln for ln in out.splitlines() if 'contradiction' in ln]


def test_the_pair_form_prints_in_the_cycle_and_the_count_form_after(
        tmp_path, monkeypatch):
    """Verify the pair form in the contradicting item's cycle, a count later.

    Mutation: the pair form repeated at every finish, or the count form
    never printed for an older pair.
    Oracle: e02 contradicts e01, both recorded in cycle 1; cycle 2
    records nothing.
    """
    folder = _begin(tmp_path, monkeypatch)
    assert _evidence('Cap is 3', 'source: docs', '--grade', 'asserted')[0] == 0
    assert _evidence('Cap is 5', 'source: x', '--contradicts', 'e01')[0] == 0
    _set(folder, 'Now', 'Recheck the cap.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _contradiction_lines(out) == [
        ('advisory: contradiction: e02 contradicts e01, both live - re-check'
         ' both; supersede or retire one only if it no longer holds')]
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert _run(['begin', _SLUG])[0] == 0
    rc, out = _run(['finish', _SLUG, '--log', 'c2'])
    assert rc == 0
    assert _contradiction_lines(out) == [
        ('advisory: 1 live contradiction - hq standing acme --kind evidence'
         ' --grep "contradicts \\["')]


@pytest.mark.parametrize('ended', [['e01', '--reason', 'docs withdrawn'], ['e02', 'e03']])
def test_a_pair_ends_when_either_side_stops_being_live(tmp_path, monkeypatch, ended):
    """Verify a retired or superseded side ends the pair.

    Mutation: the pair check reading item ids alone, so a pair with a
    retired or superseded side still prints.
    Oracle: e02 contradicts e01; then e01 retired, or e02 superseded by
    e03, which records no contradiction.
    """
    folder = _begin(tmp_path, monkeypatch)
    assert _evidence('Cap is 3', 'source: docs')[0] == 0
    assert _evidence('Cap is 5', 'source: x', '--contradicts', 'e01')[0] == 0
    assert _evidence('Cap is 4', 'source: run-09')[0] == 0
    assert _run(['supersede', _SLUG, *ended])[0] == 0
    _set(folder, 'Now', 'Recheck the cap.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _contradiction_lines(out) == []


def test_contradicts_typed_into_a_decision_body_draws_nothing(tmp_path, monkeypatch):
    """Verify only evidence bodies form pairs.

    Mutation: the pair scan reading every kind's body.
    Oracle: a decision body holding the literal 'contradicts [d01]'.
    """
    folder = _begin(tmp_path, monkeypatch)
    assert _run(['note', _SLUG, 'decision', '--headline', 'Cap at 3'])[0] == 0
    assert _run([
        'note', _SLUG, 'decision', '--headline', 'Cap at 5',
        'This contradicts [d01] on purpose.'])[0] == 0
    _set(folder, 'Now', 'Recheck the cap.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _contradiction_lines(out) == []
