"""Tests for the hq.py done verb and the list filter it drives."""

import json
import os
import pathlib
from typing import Any

from scripts import handoff_stop

from bin import hq

_SLUG = 'test-slug'
_OTHER = 'other-slug'
_SESSION = 'session-abc'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'


def _new_root(
    tmp_path: 'os.PathLike[str]',
    monkeypatch: Any,
    cycle: str = '1',
) -> 'pathlib.Path':
    """Set HQ_ROOT and the required env vars; return the root path.

    Parameters
    ----------
    tmp_path : os.PathLike[str]
        Pytest temporary path.
    monkeypatch : Any
        Active pytest monkeypatch fixture.
    cycle : str, optional
        HQ_CYCLE override as a string, by default '1'.

    Returns
    -------
    pathlib.Path
        The project root; handoff folders sit under root/.handoff/.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('HQ_GIT', '0')
    return root


def _cycle(slug: str, log: str = 'work') -> None:
    """Run one full begin/finish cycle on a slug, creating it if absent.
    """
    assert hq.main(['begin', slug]) == 0
    assert hq.main(['finish', slug, '--log', log]) == 0


def _run(argv: list, capsys) -> 'tuple[int, str]':
    """Call hq.main and return (rc, stdout).
    """
    rc = hq.main(argv)
    return rc, capsys.readouterr().out


def test_done_hides_the_folder_from_list_and_done_shows_it(
        tmp_path, monkeypatch, capsys):
    """A marked folder leaves plain list and is all that --done shows.

    Mutation: the marker comparison flipped, so --done and the bare list
    swap their sets; or the filter dropped, so both print both folders.
    Oracle: two folders, one marked, checked in both directions - each
    listing names exactly one slug and denies the other.
    """
    _new_root(tmp_path, monkeypatch)
    _cycle(_SLUG)
    _cycle(_OTHER)
    capsys.readouterr()

    assert hq.main(['done', _SLUG]) == 0
    capsys.readouterr()

    _, open_out = _run(['list'], capsys)
    assert _OTHER in open_out
    assert _SLUG not in open_out.replace(_OTHER, '')
    assert '1 marked done' in open_out

    _, done_out = _run(['list', '--done'], capsys)
    assert _SLUG in done_out
    assert _OTHER not in done_out
    assert 'marked done - run hq list --done' not in done_out


def test_done_refuses_an_open_cycle_until_finish(
        tmp_path, monkeypatch, capsys):
    """A held lock stops done until finish closes the cycle.

    Mutation: the lock guard dropped, so a thread is marked mid-cycle and
    its lock outlives it; or a leftover flag reopening the override.
    Oracle: the marker file's own existence after each call - refused
    while the lock stands, written once finish clears it.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()

    rc, out = _run(['done', _SLUG], capsys)
    assert rc == 1
    assert not (folder / hq._DONE_NAME).exists()
    assert 'is still open' in out
    assert f'hq finish {_SLUG}' in out
    assert '--force' not in out

    assert (folder / '.hq.lock').exists()

    assert hq.main(['finish', _SLUG, '--log', 'closed']) == 0
    rc, _ = _run(['done', _SLUG], capsys)
    assert rc == 0
    assert (folder / hq._DONE_NAME).exists()


def test_begin_refuses_a_marked_folder_until_undo(
        tmp_path, monkeypatch, capsys):
    """A marked folder stops begin until --undo clears the mark.

    Mutation: the begin guard dropped, so a done thread reopens silently;
    or --undo leaving the marker in place, so the thread can never resume.
    Oracle: the lock file, which begin creates only when it proceeds -
    absent after the refusal, present after the undo.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    assert hq.main(['done', _SLUG]) == 0
    capsys.readouterr()

    rc, out = _run(['begin', _SLUG], capsys)
    assert rc == 1
    assert not (folder / '.hq.lock').exists()
    assert f'hq done {_SLUG} --undo' in out

    rc, out = _run(['done', _SLUG, '--undo'], capsys)
    assert rc == 0
    assert not (folder / hq._DONE_NAME).exists()
    assert 'reopened' in out

    assert hq.main(['begin', _SLUG]) == 0
    assert (folder / '.hq.lock').exists()


def test_a_second_done_keeps_the_first_finish_date(
        tmp_path, monkeypatch, capsys):
    """Marking a marked folder again leaves the marker as the first wrote it.

    Mutation: the second done rewriting the marker, which resets the
    finish date and the cycle to the day of the repeat.
    Oracle: the marker bytes, written under one HQ_NOW and re-read after
    a second done under a later one.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    assert hq.main(['done', _SLUG, '--reason', 'shipped']) == 0
    first = (folder / hq._DONE_NAME).read_bytes()
    capsys.readouterr()

    monkeypatch.setenv('HQ_NOW', '2026-12-25T09:00:00')
    rc, out = _run(['done', _SLUG, '--reason', 'shipped twice'], capsys)
    assert rc == 0
    assert (folder / hq._DONE_NAME).read_bytes() == first
    assert _NOW in out


def test_the_marker_records_the_last_finished_cycle(tmp_path, monkeypatch):
    """The marker's cycle is the cycle that finished, not the next one.

    Mutation: writing anchors()' cycle, which is the last manifest row
    plus one, so every marker names a cycle that never ran.
    Oracle: HQ_CYCLE forced to 9 against a manifest whose last finished
    row is 2; the marker must read 2.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    monkeypatch.setenv('HQ_CYCLE', '1')
    _cycle(_SLUG)
    monkeypatch.setenv('HQ_CYCLE', '2')
    _cycle(_SLUG)

    monkeypatch.setenv('HQ_CYCLE', '9')
    assert hq.main(['done', _SLUG]) == 0
    assert hq._read_kv(folder / hq._DONE_NAME)['cycle'] == '2'


def test_the_reason_collapses_to_one_line(tmp_path, monkeypatch):
    """A reason carrying a newline still leaves a four-field marker.

    Mutation: writing the reason raw, so an embedded newline opens a
    fifth line the key=value reader takes for another field, or swallows
    the field that followed.
    Oracle: the parsed marker - four keys, the reason one line of
    single-spaced words.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    assert hq.main(['done', _SLUG, '--reason', 'shipped\ncycle=99  and done']) == 0

    state = hq._read_kv(folder / hq._DONE_NAME)
    assert sorted(state) == ['cycle', 'reason', 'slug', 'time']
    assert state['reason'] == 'shipped cycle=99 and done'
    assert state['cycle'] == '1'


def test_the_marker_is_invisible_to_the_folder_walk(tmp_path, monkeypatch, capsys):
    """The folder walk never reports the marker as an unstamped file.

    Mutation: naming the marker without its leading dot, which puts it in
    the walk and makes every finished thread report an unstamped file.
    Oracle: the artifacts listing and the raw walk, neither naming it.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    assert hq.main(['done', _SLUG]) == 0
    capsys.readouterr()

    _, out = _run(['artifacts', _SLUG], capsys)
    assert hq._DONE_NAME not in out
    assert 'unstamped' not in out
    assert [name for name, _ in hq._walk_folder(folder)] == []


def test_the_stop_hook_leaves_a_marked_folder_alone(
        tmp_path, monkeypatch, capsys):
    """A hand edit to a marked folder's HANDOFF.md draws no Stop message.

    Mutation: the marker check dropped from the folder loop, so every
    finished thread nags at the end of every turn forever.
    Oracle: the same edit on the same folder, reported before the mark
    and silent after it.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    (folder / 'HANDOFF.md').write_text('# Handoff\n\n## Task\n\nEdited.\n')
    capsys.readouterr()

    payload = {
        'hook_event_name': 'Stop', 'cwd': str(root),
        'session_id': 'S1', 'stop_hook_active': False,
        }
    assert handoff_stop.report(payload) == 0
    before = capsys.readouterr().out
    assert 'HANDOFF.md' in json.loads(before)['systemMessage']

    assert hq.main(['done', _SLUG]) == 0
    capsys.readouterr()
    assert handoff_stop.report(dict(payload, session_id='S2')) == 0
    assert not capsys.readouterr().out


def test_an_unparseable_marker_still_refuses_begin(tmp_path, monkeypatch, capsys):
    """A marker no key=value reader can parse still stops begin.

    Mutation: testing the parsed marker rather than the file, which lets
    an empty or hand-edited marker hide a folder from list while begin
    opens a cycle on it.
    Oracle: the lock file, which begin creates only when it proceeds,
    against a marker truncated to zero bytes.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    (folder / hq._DONE_NAME).write_text('')
    capsys.readouterr()

    rc, out = _run(['begin', _SLUG], capsys)
    assert rc == 1
    assert not (folder / '.hq.lock').exists()
    assert 'is marked done (c-, -)' in out

    rc, _ = _run(['list'], capsys)
    assert rc == 0


def test_undo_clears_an_unparseable_marker_and_says_so(
        tmp_path, monkeypatch, capsys):
    """--undo on an empty marker removes it and reports the reopen.

    Mutation: unlinking before the existence test, which prints "was not
    marked done - hq list already shows it" on the very call that put the
    folder back in the listing.
    Oracle: the printed line against the folder's state one call earlier,
    when list did hide it.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    (folder / hq._DONE_NAME).write_text('')
    capsys.readouterr()
    _, hidden = _run(['list'], capsys)
    assert _SLUG not in hidden

    rc, out = _run(['done', _SLUG, '--undo'], capsys)
    assert rc == 0
    assert 'reopened' in out
    assert 'was not marked done' not in out
    assert not (folder / hq._DONE_NAME).exists()


def test_a_directory_under_the_marker_name_names_the_hand_removal(
        tmp_path, monkeypatch, capsys):
    """A directory marker refuses --undo and names removing it by hand.

    Mutation: unlink alone, which raises IsADirectoryError and prints the
    bare "cannot access" line, leaving the thread hidden from list with
    no move named and no verb able to clear it.
    Oracle: the exit code and the printed move, with the directory still
    standing afterward.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    (folder / hq._DONE_NAME).mkdir()
    capsys.readouterr()

    rc, out = _run(['done', _SLUG, '--undo'], capsys)
    assert rc == 1
    assert 'is a directory' in out
    assert 'remove it by hand' in out
    assert (folder / hq._DONE_NAME).is_dir()


def test_adopt_refuses_a_marked_folder(tmp_path, monkeypatch, capsys):
    """Adopt will not open a ledger on a thread already marked done.

    Mutation: the guard placed on begin alone, which leaves adopt as a
    second road into a finished thread - it writes a ledger and a cycle
    that no list run will ever surface.
    Oracle: ledger.tsv, which adopt creates only when it proceeds.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / 'legacy-slug'
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text(
        '# Handoff: legacy-slug\n\n'
        'Written: 2026-09-01 | Cycle: 2\n\n'
        '## Task\n\nShip it.\n', encoding='utf-8')

    assert hq.main(['done', 'legacy-slug']) == 0
    capsys.readouterr()

    rc, out = _run(['adopt', 'legacy-slug'], capsys)
    assert rc == 1
    assert not (folder / 'ledger.tsv').exists()
    assert 'hq adopt: legacy-slug is marked done' in out


def test_undo_refuses_the_reason_flag(tmp_path, monkeypatch, capsys):
    """--undo paired with --reason is a usage error.

    Mutation: the pair accepted, so a reason passed with --undo is
    silently dropped and the caller believes it was recorded.
    Oracle: exit 2 and an untouched marker.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    _cycle(_SLUG)
    assert hq.main(['done', _SLUG, '--reason', 'shipped']) == 0
    before = (folder / hq._DONE_NAME).read_bytes()
    capsys.readouterr()

    rc, out = _run(['done', _SLUG, '--undo', '--reason', 'oops'], capsys)
    assert rc == 2
    assert 'takes no --reason' in out
    assert (folder / hq._DONE_NAME).read_bytes() == before
