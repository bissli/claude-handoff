"""Tests for skills/hq-loop/tally.py: the known set, its state file, and
the tokens it prints.
"""

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / 'skills' / 'hq-loop' / 'tally.py'
_SPEC = importlib.util.spec_from_file_location('hq_loop_tally', _SCRIPT)
tally = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tally)


def _run(monkeypatch, state_path, lines):
    """Run tally.py under HQ_MEASURE=1; stdout on exit 0, else stderr.
    """
    monkeypatch.setenv('HQ_MEASURE', '1')
    monkeypatch.setattr('sys.stdin', io.StringIO(''.join(f'{x}\n' for x in lines)))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = tally.main([str(state_path), 'open_tests', 'tests_closed'])
    return rc, (out if rc == 0 else err).getvalue().strip()


def test_a_regression_counts_in_r_and_takes_one_from_t(tmp_path, monkeypatch):
    """Verify a unit seen closed then open stays out of K and lowers T.

    Mutation: admission on any open line, so a flaky unit closes for
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
    header = 'id\tadmitted\tlast\tsites'
    for bad in (
            'b\t2\topen\t1', 'b\t1\tshut\t1', 'b\t1\topen\tmany',
            'b\t1\topen', 'b\t1\t0\topen\t1'):
        state_path.write_text(f'{header}\na\t1\topen\t1\n{bad}\n')
        state_bytes = state_path.read_bytes()
        rc, out = _run(monkeypatch, state_path, ['end'])
        assert rc == 2
        assert out == f'tally: {state_path}:3 is no tally row'
        assert state_path.read_bytes() == state_bytes


def test_an_old_five_column_state_reads_by_position(tmp_path, monkeypatch):
    """Verify a state file with the seen_closed column keeps each unit.

    Mutation: the old header refused, the wrong column dropped, or the
    dropped column left unchecked.
    Oracle: a admitted and open with 3 sites, b first named closed; a
    run naming b open reads as a regression and rewrites both rows; a
    seen_closed of 2 is refused.
    """
    state_path = tmp_path / 's.tally.tsv'
    state_path.write_text(
        'id\tadmitted\tseen_closed\tlast\tsites\n'
        'a\t1\t0\topen\t3\nb\t0\t1\tclosed\t0\n')
    assert _run(monkeypatch, state_path, ['open b', 'end']) == (
        0, 'open_tests=4->0 tests_closed=+-1')
    assert state_path.read_text().splitlines() == [
        'id\tadmitted\tlast\tsites', 'a\t1\topen\t3', 'b\t0\topen\t1']
    state_path.write_text(
        'id\tadmitted\tseen_closed\tlast\tsites\na\t1\t2\topen\t1\n')
    assert _run(monkeypatch, state_path, ['end']) == (
        2, f'tally: {state_path}:2 is no tally row')


def test_a_dry_run_writes_no_state(tmp_path, monkeypatch):
    """Verify a run without HQ_MEASURE=1 prints its tokens and writes nothing.

    Mutation: the HQ_MEASURE gate dropped, so a dry run moves the known
    set and the next measured cycle reads no change.
    Oracle: the directory listing after one dry run.
    """
    monkeypatch.delenv('HQ_MEASURE', raising=False)
    monkeypatch.setattr('sys.stdin', io.StringIO('open a\nend\n'))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = tally.main([str(tmp_path / 'probes' / 's.tally.tsv'), 'R', 'T'])
    assert (rc, out.getvalue().strip()) == (0, 'R=1->0 T=+0')
    assert list(tmp_path.iterdir()) == []


def test_argument_refusals_exit_2_and_write_nothing(tmp_path, monkeypatch):
    """Verify a wrong argument count, a bad name, one name twice or a bad step
    exits 2.

    Mutation: the remaining == tally check dropped, the name pattern
    not applied to both names, or a step of 0 or no number accepted.
    Oracle: a valid empty run on stdin, so only the arguments refuse.
    """
    state_path = tmp_path / 's.tally.tsv'
    state = str(state_path)
    for argv in (
            [state, 'r'], [state, 'r', 't', 'x'], [state, '1bad', 't'],
            [state, 'r', '1bad'], [state, 'r', 'bad-name'], [state, 'n', 'n'],
            [state, 'r@0', 't'], [state, 'r@', 't'], [state, 'r@x', 't']):
        monkeypatch.setattr('sys.stdin', io.StringIO('end\n'))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = tally.main(argv)
        assert rc == 2, argv
        assert err.getvalue().startswith('tally: ')
        assert list(tmp_path.iterdir()) == []


def test_the_step_reaches_the_remaining_token_alone(tmp_path, monkeypatch):
    """Verify R@5 puts the step on the remaining token and none on the tally.

    Mutation: the step left off the remaining token, or put on the tally.
    Oracle: one open unit and one closed one under R@5.
    """
    monkeypatch.setattr('sys.stdin', io.StringIO('open a\nclosed b\nend\n'))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = tally.main([str(tmp_path / 's.tally.tsv'), 'R@5', 'T'])
    assert (rc, out.getvalue().strip()) == (0, 'R=1->0@5 T=+0')


def _template(tmp_path, name, dest):
    """Write the sh block under '## <name>' in templates.md to dest.

    The file is made executable and returned.
    """
    text = (_ROOT / 'skills' / 'hq-loop' / 'templates.md').read_text()
    block_m = re.search(
        rf'^## {re.escape(name)}\n.*?^```sh\n(.*?)^```', text, re.M | re.S)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(block_m.group(1))
    dest.chmod(0o755)
    return dest


def _shim_python3(tmp_path, body):
    """A dir holding an executable python3 shell shim, for PATH.
    """
    shim_dir = tmp_path / 'shim'
    shim_dir.mkdir()
    shim = shim_dir / 'python3'
    shim.write_text(f'#!/bin/sh\n{body}\n')
    shim.chmod(0o755)
    return {**os.environ, 'PATH': f'{shim_dir}:{os.environ["PATH"]}'}


def test_open_zero_is_refused(tmp_path, monkeypatch):
    """Verify 'open:0 <id>' exits 2, since an open unit holds one site or more.

    Mutation: the site count accepted as 0, so a unit stored open reads
    r=0 and the finish line is met with a unit still open.
    Oracle: the existing refusal form, exit 2 and a 'tally: ' message.
    """
    rc, out = _run(monkeypatch, tmp_path / 's.tally.tsv', ['open:0 x', 'end'])
    assert rc == 2
    assert out.startswith('tally: ')


def test_tally_template_closes_a_deleted_file(tmp_path):
    """Verify a file deleted after it held a TODO no longer counts as open.

    Mutation: the template lists only existing files, so a deleted unit
    keeps its last open state and the remaining count never reaches 0.
    Oracle: hand-traced runs, one TODO in src/a.py, then the file removed.
    """
    def git(*args):
        subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com',
             *args], cwd=tmp_path, check=True, capture_output=True)

    git('init', '-q')
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'a.py').write_text('# TODO fix\n')
    git('add', 'src/a.py')
    git('commit', '-qm', 'add')
    probe = _template(tmp_path, 'Tally', tmp_path / 'probes' / 'hq-measure')
    env = {
        **os.environ, 'HQ_MEASURE': '1',
        'HQ_TALLY': str(_SCRIPT),
        }

    def run_probe():
        return subprocess.run(
            [str(probe)], cwd=tmp_path, env=env, capture_output=True, text=True)

    first = run_probe()
    assert first.stdout.startswith('open_sites=1->0'), first
    git('rm', '-q', 'src/a.py')
    git('commit', '-qm', 'delete')
    second = run_probe()
    assert second.stdout.startswith('open_sites=0->0'), second


def test_pass_or_fail_template_keeps_a_test_id_with_a_space(tmp_path):
    """Verify a failing test whose id holds a space is rerun, not cut.

    Mutation: the id cut at its first space, so the rerun names no test,
    pytest exits 4 and the probe exits nonzero.
    Oracle: a parametrized test with id 'a b' that always fails.
    """
    (tmp_path / 'test_x.py').write_text(
        'import pytest\n\n'
        "@pytest.mark.parametrize('v', ['a b'])\n"
        'def test_x(v):\n    assert False\n')
    env = _shim_python3(tmp_path, f'exec {sys.executable} "$@"')
    probe = _template(tmp_path, 'Pass or Fail', tmp_path / 'probe.sh')
    done = subprocess.run(
        [str(probe)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (done.returncode, done.stdout.strip()) == (0, 'failing=1->0'), done


def test_custom_score_template_ignores_stderr(tmp_path):
    """Verify a stderr warning on a passing coverage run stays out of value.

    Mutation: stderr folded into the value, so awk compares it as a
    string, caps it at the goal and prints a met finish line.
    Oracle: a python3 shim writing a warning to stderr and 85 to stdout.
    """
    env = _shim_python3(
        tmp_path,
        'if [ "$*" = "-m coverage report --format=total --fail-under=0" ]; then\n'
        '    echo "CoverageWarning: no data" >&2\n'
        '    echo 85\n'
        'fi')
    probe = _template(tmp_path, 'Custom Score', tmp_path / 'probe.sh')
    done = subprocess.run(
        [str(probe)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert done.stdout.strip().splitlines()[-1] == 'score=85->90@1', done


def _git_repo_with_todo(tmp_path):
    """Init a repo in tmp_path holding a committed src/a.py with one TODO.
    """
    def git(*args):
        subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com',
             *args], cwd=tmp_path, check=True, capture_output=True)

    git('init', '-q')
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'a.py').write_text('# TODO fix\n')
    git('add', 'src/a.py')
    git('commit', '-qm', 'add')


def _tally_env():
    """The environment with HQ_MEASURE=1 and HQ_TALLY set to tally.py.
    """
    return {**os.environ, 'HQ_MEASURE': '1', 'HQ_TALLY': str(_SCRIPT)}


def test_tally_template_fails_outside_a_git_repo(tmp_path):
    """Verify a failed git ls-files fails the probe and writes no state.

    Mutation: git ls-files piped straight into the filter loop, so its
    exit status is lost, the listing reads empty and every known unit
    reads closed.
    Oracle: a state file seeded by a run in a repo, then the probe run in
    a plain directory with its bytes compared.
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git_repo_with_todo(repo)
    probe = _template(tmp_path, 'Tally', tmp_path / 'probes' / 'hq-measure')
    state = tmp_path / 'probes' / 'tally.tsv'
    seeded = subprocess.run(
        [str(probe)], cwd=repo, env=_tally_env(), capture_output=True, text=True)
    assert seeded.stdout.startswith('open_sites=1->0'), seeded
    state_bytes = state.read_bytes()
    plain = tmp_path / 'plain'
    plain.mkdir()
    ceiling_env = {**_tally_env(), 'GIT_CEILING_DIRECTORIES': str(tmp_path)}
    done = subprocess.run(
        [str(probe)], cwd=plain, env=ceiling_env,
        capture_output=True, text=True)
    assert done.returncode != 0, done
    assert done.stdout == ''
    assert state.read_bytes() == state_bytes


def test_tally_template_closes_a_file_removed_from_disk_only(tmp_path):
    """Verify a file still in the index but gone from disk reads closed.

    Mutation: the existing-file filter dropped, so grep -c fails on the
    missing file and the probe exits nonzero.
    Oracle: hand-traced runs, one TODO in src/a.py, then a plain rm.
    """
    _git_repo_with_todo(tmp_path)
    probe = _template(tmp_path, 'Tally', tmp_path / 'probes' / 'hq-measure')

    def run_probe():
        return subprocess.run(
            [str(probe)], cwd=tmp_path, env=_tally_env(),
            capture_output=True, text=True)

    assert run_probe().stdout.startswith('open_sites=1->0')
    (tmp_path / 'src' / 'a.py').unlink()
    second = run_probe()
    assert second.returncode == 0, second
    assert second.stdout.startswith('open_sites=0->0'), second


def test_pass_or_fail_template_does_not_count_a_flaky_pass(tmp_path):
    """Verify a test failing once and passing on the rerun counts as 0 failing.

    Mutation: --lf dropped from the rerun, so a full rerun also runs
    the test that fails on any second run; or the first run's failures
    counted.
    Oracle: a flaky test failing until its marker exists, and a test
    that passes once and fails if it runs again.
    """
    (tmp_path / 'test_x.py').write_text(
        'import pathlib\n\n'
        'def test_flaky():\n'
        "    marker = pathlib.Path('marker')\n"
        '    seen = marker.exists()\n'
        '    marker.touch()\n'
        '    assert seen\n\n'
        'def test_once_only():\n'
        "    marker = pathlib.Path('ran')\n"
        '    seen = marker.exists()\n'
        '    marker.touch()\n'
        '    assert not seen\n')
    env = _shim_python3(tmp_path, f'exec {sys.executable} "$@"')
    probe = _template(tmp_path, 'Pass or Fail', tmp_path / 'probe.sh')
    done = subprocess.run(
        [str(probe)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (done.returncode, done.stdout.strip()) == (0, 'failing=0->0'), done


def test_custom_score_template_relays_a_failed_coverage_run(tmp_path):
    """Verify a failing coverage run exits nonzero with its stdout on stderr.

    Mutation: the stdout relay dropped, so the failure exits with
    nothing for the stored row to name.
    Oracle: a python3 shim printing a message to stdout and exiting 2.
    """
    env = _shim_python3(
        tmp_path,
        'if [ "$*" = "-m coverage report --format=total --fail-under=0" ]; then\n'
        '    echo "coverage: no data to report"\n'
        '    exit 2\n'
        'fi')
    probe = _template(tmp_path, 'Custom Score', tmp_path / 'probe.sh')
    done = subprocess.run(
        [str(probe)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert done.returncode == 1, done
    assert done.stderr.strip().splitlines()[-1] == 'coverage: no data to report'
    assert done.stdout == ''
