"""Tests for retiring a standing item with hq supersede --reason."""

import contextlib
import io
import pathlib

import pytest

from bin import hq

_SLUG = 'retire'


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


def _run(argv):
    """Call hq.main, returning (rc, stdout) with SystemExit caught."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue()


def _thread(tmp_path, monkeypatch, count):
    """Begin a cycle holding decisions d01 to d<count>; return the folder."""
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    for n in range(1, count + 1):
        assert _run([
            'note', _SLUG, 'decision', '--headline', f'Retry rule {n}',
            f'The poller retries {n} times.'])[0] == 0
    return folder


def test_retire_writes_the_line_with_the_reason_on_one_line(tmp_path, monkeypatch):
    """Verify the retire line holds '-' as successor and the reason, one line.

    Mutation: the reason written raw, so its newline splits the line and
    the second half parses as nothing; or the '-' slot dropped.
    Oracle: the hand-written line '- (c1) d01 -> - vendor dropped the cap'.
    """
    folder = _thread(tmp_path, monkeypatch, 1)
    rc, out = _run([
        'supersede', _SLUG, 'd01', '--reason', 'vendor dropped\nthe cap'])
    assert rc == 0
    assert out.startswith('dropped from the block: [d01] (c1) **Retry rule 1**')
    lines = (folder / 'standing.md').read_text().splitlines()
    assert lines[-1] == '- (c1) d01 -> - vendor dropped the cap'


@pytest.mark.parametrize(('argv', 'message'), [
    (['d01', 'd02', '--reason', 'gone'],
     'hq supersede: give a new id or --reason, not both - '),
    (['d01'], 'hq supersede: give a new id or --reason - '),
    (['d01', ''], 'hq supersede: give a new id or --reason - '),
    (['d01', '--reason', ' - '],
     'hq supersede: --reason needs a word - say why the item no longer holds'),
    (['d09', '--reason', 'gone'], "hq supersede: 'd09' not found in standing.md"),
    ])
def test_supersede_refuses_a_malformed_retire_and_writes_nothing(
        tmp_path, monkeypatch, argv, message):
    """Verify each malformed form exits 1 with its message and no write.

    Mutation: the both-forms check dropped, so --reason is ignored; an
    empty new_id taken as given; a reason of punctuation accepted; or the
    absent-id check skipped on the retire form.
    Oracle: the messages SPEC section 2 fixes, and standing.md
    byte-identical.
    """
    folder = _thread(tmp_path, monkeypatch, 2)
    before = (folder / 'standing.md').read_bytes()
    rc, out = _run(['supersede', _SLUG, *argv])
    assert rc == 1
    assert out.startswith(message)
    assert (folder / 'standing.md').read_bytes() == before


@pytest.mark.parametrize('second', [['d03'], ['--reason', 'again']])
def test_both_forms_refuse_an_old_id_already_retired(tmp_path, monkeypatch, second):
    """Verify a retired id takes no second outgoing line.

    Mutation: the old-id check reading only supersession lines, so a
    retired id is superseded or retired again.
    Oracle: d01 retired in c1, then each form on d01.
    """
    _thread(tmp_path, monkeypatch, 3)
    assert _run(['supersede', _SLUG, 'd01', '--reason', 'gone'])[0] == 0
    rc, out = _run(['supersede', _SLUG, 'd01', *second])
    assert rc == 1
    assert out == (
        f'hq supersede: d01 was already retired in c1 - hq standing {_SLUG} d01'
        ' shows it\n')


@pytest.mark.parametrize('second', [['d03'], ['--reason', 'again']])
def test_both_forms_refuse_an_old_id_already_superseded(
        tmp_path, monkeypatch, second):
    """Verify a superseded id takes no second outgoing line.

    Mutation: the old-id check dropped, so d01 gains a second successor.
    Oracle: d01 -> d02, then each form on d01.
    """
    _thread(tmp_path, monkeypatch, 3)
    assert _run(['supersede', _SLUG, 'd01', 'd02'])[0] == 0
    rc, out = _run(['supersede', _SLUG, 'd01', *second])
    assert rc == 1
    assert out == (
        'hq supersede: d01 was already superseded by d02 - supersede d02 instead\n')


def test_an_old_id_whose_chain_ends_retired_names_the_retirement(
        tmp_path, monkeypatch):
    """Verify a superseded id whose successor retired draws no dead advice.

    Mutation: the plain superseded refusal printed, which says to
    supersede d02 when d02 refuses as already retired.
    Oracle: d01 -> d02, d02 retired in c1, then d01 -> d03.
    """
    _thread(tmp_path, monkeypatch, 3)
    assert _run(['supersede', _SLUG, 'd01', 'd02'])[0] == 0
    assert _run(['supersede', _SLUG, 'd02', '--reason', 'gone'])[0] == 0
    rc, out = _run(['supersede', _SLUG, 'd01', 'd03'])
    assert rc == 1
    assert out == (
        'hq supersede: d01 was already superseded by d02, retired in c1'
        f' - hq standing {_SLUG} d01 shows it\n')


def test_a_retired_successor_is_refused_by_its_own_message(tmp_path, monkeypatch):
    """Verify a retired id named as successor draws the retired refusal.

    Mutation: the retired check missing, so the generic itself-superseded
    refusal prints, or the supersession goes through.
    Oracle: d02 retired in c1, then d01 -> d02.
    """
    _thread(tmp_path, monkeypatch, 2)
    assert _run(['supersede', _SLUG, 'd02', '--reason', 'gone'])[0] == 0
    rc, out = _run(['supersede', _SLUG, 'd01', 'd02'])
    assert rc == 1
    assert out == (
        'hq supersede: d02 was retired in c1 - record the replacement with a'
        ' note, then supersede onto its id\n')


def test_standing_marks_a_retired_item_and_hides_it_from_the_listing(
        tmp_path, monkeypatch):
    """Verify the retired mark under --all and the id form, hidden otherwise.

    Mutation: the listing filter reading only supersession lines, so a
    retired item stays listed; or the mark dropped from the id form.
    Oracle: d01 retired with reason 'gone', d02 live.
    """
    _thread(tmp_path, monkeypatch, 2)
    assert _run(['supersede', _SLUG, 'd01', '--reason', 'gone'])[0] == 0
    mark = '[d01] (c1) **Retry rule 1** The poller retries 1 times. [retired in c1: gone]'
    assert '[d01]' not in _run(['standing', _SLUG])[1]
    assert mark in _run(['standing', _SLUG, '--all'])[1].splitlines()
    assert _run(['standing', _SLUG, 'd01'])[1].splitlines() == [mark]


def test_a_superseded_item_whose_successor_retired_shows_both(tmp_path, monkeypatch):
    """Verify a chain ending in a retirement prints both marks.

    Mutation: the retirement read from the start id alone, so the mark
    stops at the supersession.
    Oracle: d01 -> d02 in c1, d02 retired in c2.
    """
    _thread(tmp_path, monkeypatch, 2)
    assert _run(['supersede', _SLUG, 'd01', 'd02'])[0] == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert _run(['supersede', _SLUG, 'd02', '--reason', 'gone'])[0] == 0
    out = _run(['standing', _SLUG, 'd01'])[1]
    assert out.rstrip().endswith('[superseded by d02 in c1, retired in c2: gone]')


def test_a_retire_reason_with_a_former_folder_path_is_not_stale(
        tmp_path, monkeypatch):
    """Verify open skips the retire line in its stale-path scan.

    Mutation: the scan reading the retire line, so its reason draws an
    advisory the append-only file cannot clear.
    Oracle: a reason naming working/<slug>/, the form the scan flags.
    """
    _thread(tmp_path, monkeypatch, 1)
    assert _run([
        'supersede', _SLUG, 'd01', '--reason',
        f'the notes moved out of working/{_SLUG}/'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'c1'])[0] == 0
    out = _run(['open', _SLUG])[1]
    assert [ln for ln in out.splitlines() if ln.startswith('stale folder path')] == []
