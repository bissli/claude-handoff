"""Tests for skills/hq-loop/tally.py: the known set, its state file, and
the tokens it prints.
"""

import contextlib
import importlib.util
import io
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / 'skills' / 'hq-loop' / 'tally.py'
_SPEC = importlib.util.spec_from_file_location('hq_loop_tally', _SCRIPT)
tally = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tally)


def _run(monkeypatch, state_path, lines):
    monkeypatch.setattr('sys.stdin', io.StringIO(''.join(f'{x}\n' for x in lines)))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = tally.main([str(state_path), 'open_tests', 'tests_closed'])
    return rc, out.getvalue().strip()


def test_a_regression_counts_in_r_and_takes_one_from_t(tmp_path, monkeypatch):
    """Verify a unit seen closed then open stays out of K and lowers T.

    Mutation: admission ignores seen-closed, so a flaky unit closes for
    free and lifts T; or T counts only K minus O, so fix-one-break-one
    reads as progress.
    Oracle: hand-traced runs: a open, b closed; then b open; then a
    closed and b open; then both closed.
    """
    state_path = tmp_path / 'probes' / 's.tally.tsv'
    assert _run(monkeypatch, state_path, ['open a', 'closed b', 'end']) == (
        0, 'open_tests=1->0 tests_closed=+0')
    assert _run(monkeypatch, state_path, ['open a', 'open b', 'end']) == (
        0, 'open_tests=2->0 tests_closed=+-1')
    assert _run(monkeypatch, state_path, ['closed a', 'open b', 'end']) == (
        0, 'open_tests=1->0 tests_closed=+0')
    assert _run(monkeypatch, state_path, ['closed a', 'closed b', 'end']) == (
        0, 'open_tests=0->0 tests_closed=+1')


def test_an_unnamed_known_unit_keeps_its_state(tmp_path, monkeypatch):
    """Verify a run that omits a known unit leaves it as it last read.

    Mutation: an omitted unit read closed, so a crash that names nothing
    reads as every unit closing.
    Oracle: a and b open, then a run naming only end.
    """
    state_path = tmp_path / 's.tally.tsv'
    first = _run(monkeypatch, state_path, ['open a', 'open:3 b', 'end'])
    assert first == (0, 'open_tests=4->0 tests_closed=+0')
    assert _run(monkeypatch, state_path, ['end']) == first


def test_a_repeat_run_changes_nothing(tmp_path, monkeypatch):
    """Verify the same input twice prints one line and leaves the file's bytes.

    Mutation: a second run re-admits or flips a unit, so the line moves
    with no edit; or the sites weight dropped.
    Oracle: 3 + 1 sites, ids with spaces, and the state file's bytes.
    """
    state_path = tmp_path / 's.tally.tsv'
    lines = ['open:3 src/a.py', 'open src/b c.py', 'closed src/d.py', 'end']
    first = _run(monkeypatch, state_path, lines)
    state_bytes = state_path.read_bytes()
    assert first == (0, 'open_tests=4->0 tests_closed=+0')
    assert _run(monkeypatch, state_path, lines) == first
    assert state_path.read_bytes() == state_bytes


def test_a_bad_run_or_state_exits_2_and_writes_nothing(tmp_path, monkeypatch):
    """Verify no end line, a bad line, a repeated id or a bad state file exit 2.

    Mutation: a check dropped, so a truncated run updates the known set;
    or the state written before the checks.
    Oracle: the state file's bytes before and after each refused run.
    """
    state_path = tmp_path / 's.tally.tsv'
    assert _run(monkeypatch, state_path, ['open a', 'end'])[0] == 0
    state_bytes = state_path.read_bytes()
    for lines in (
            ['open b'], ['opened b', 'end'], ['closed:2 b', 'end'],
            ['open b', 'closed b', 'end']):
        rc, out = _run(monkeypatch, state_path, lines)
        assert rc == 2
        assert out.startswith('tally: ')
        assert state_path.read_bytes() == state_bytes
    state_path.write_text('id\tstate\nfoo\topen\n')
    bad_bytes = state_path.read_bytes()
    rc, out = _run(monkeypatch, state_path, ['end'])
    assert rc == 2
    assert out == f'tally: {state_path}:1 is no tally header'
    assert state_path.read_bytes() == bad_bytes


def test_an_error_run_with_no_state_file_leaves_nothing(tmp_path, monkeypatch):
    """Verify a refused first run creates no state file, no .tmp and no folder.

    Mutation: the state written whatever the run's outcome, so a probe
    that crashed before its end line leaves an empty known set behind.
    Oracle: the directory listing after a run with no end line.
    """
    state_path = tmp_path / 'probes' / 's.tally.tsv'
    rc, out = _run(monkeypatch, state_path, ['open a'])
    assert rc == 2
    assert out.startswith('tally: ')
    assert list(tmp_path.rglob('*')) == []


def test_a_malformed_state_row_names_its_line(tmp_path, monkeypatch):
    """Verify a bad field in a state row exits 2, names the line, writes nothing.

    Mutation: the row check weakened to the field count, so a row with
    admitted 2 or last shut is read as a unit.
    Oracle: a good row at line 2 and a bad row at line 3, for each bad
    field the row form bars.
    """
    state_path = tmp_path / 's.tally.tsv'
    header = 'id\tadmitted\tseen_closed\tlast\tsites'
    for bad in (
            'b\t2\t0\topen\t1', 'b\t1\t2\topen\t1', 'b\t1\t0\tshut\t1',
            'b\t1\t0\topen\tmany', 'b\t1\t0\topen'):
        state_path.write_text(f'{header}\na\t1\t0\topen\t1\n{bad}\n')
        state_bytes = state_path.read_bytes()
        rc, out = _run(monkeypatch, state_path, ['end'])
        assert rc == 2
        assert out == f'tally: {state_path}:3 is no tally row'
        assert state_path.read_bytes() == state_bytes


def test_argument_refusals_exit_2_and_write_nothing(tmp_path, monkeypatch):
    """Verify a wrong argument count, a bad name or one name twice exits 2.

    Mutation: the remaining == tally check dropped, or the name pattern
    not applied to both names.
    Oracle: a valid empty run on stdin, so only the arguments refuse.
    """
    state_path = tmp_path / 's.tally.tsv'
    state = str(state_path)
    for argv in (
            [state, 'r'], [state, 'r', 't', 'x'], [state, '1bad', 't'],
            [state, 'r', '1bad'], [state, 'r', 'bad-name'], [state, 'n', 'n']):
        monkeypatch.setattr('sys.stdin', io.StringIO('end\n'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tally.main(argv)
        assert rc == 2, argv
        assert out.getvalue().startswith('tally: ')
        assert list(tmp_path.iterdir()) == []
