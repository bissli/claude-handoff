"""Tests for the measure probe hq finish runs: the probe contract, the
composed row, the typed-prefix strip, the hold under the thread's own
loop, and the lines hq open prints from them.
"""

import contextlib
import io
import pathlib
import time

from bin import hq

_SLUG = 'measure-slug'
_NOW = '2026-11-10T12:00:00'
_STALL_LINE = (
    'stall: {n} cycles without progress ({cause})'
    ' - close a unit on another workable item')


def _root(tmp_path, monkeypatch):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', 'session-ms')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    monkeypatch.delenv('HQ_LOOP', raising=False)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    return folder


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _probe(folder, body):
    probe = folder / 'probes' / 'hq-measure'
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(f'#!/bin/sh\n{body}\n')
    probe.chmod(0o755)


def _cycle(log='work'):
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', log])
    assert rc == 0, out
    return out


def _rows(folder):
    return hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)


def _loop(tmp_path, monkeypatch, slug=_SLUG):
    loop_dir = tmp_path / f'loop-{slug}'
    loop_dir.mkdir()
    (loop_dir / 'slug').write_text(f'{slug}\n')
    monkeypatch.setenv('HQ_LOOP', str(loop_dir))
    return loop_dir


# --- probe contract --------------------------------------------------


def test_probe_runs_in_the_work_dir_with_its_env(tmp_path, monkeypatch):
    """Verify the probe runs in the pinned work dir with HQ_MEASURE and
    HQ_TALLY set.

    Mutation: the root for the work dir, or an env var dropped.
    Oracle: the probe writes its cwd and both variables to a file.
    """
    folder = _root(tmp_path, monkeypatch)
    work = folder.parent.parent / 'work'
    work.mkdir()
    assert _run(['work-dir', _SLUG, str(work)])[0] == 0
    seen = tmp_path / 'seen'
    _probe(
        folder,
        f'printf "%s\\n%s\\n%s\\n" "$(pwd)" "$HQ_MEASURE" "$HQ_TALLY" > {seen}\n'
        "echo 'a=1->0'")
    _cycle()
    tally = pathlib.Path(hq.__file__).resolve().parent.parent / 'skills' / 'hq-loop'
    assert seen.read_text().splitlines() == [
        str(work.resolve()), '1', str(tally / 'tally.py')]


def test_probe_row_stores_tokens_then_the_log(tmp_path, monkeypatch):
    """Verify the row holds the last non-blank stdout line, then the log.

    Mutation: the log before the bar, no '-' for an empty log, or the
    first stdout line read.
    Oracle: stdout x, summary: a=3->0 and a blank line.
    """
    folder = _root(tmp_path, monkeypatch)
    _probe(folder, "printf 'x\\nsummary: a=3->0\\n\\n'")
    _cycle('fixed x')
    _cycle('')
    assert [row['log'] for row in _rows(folder)] == [
        'summary: a=3->0 | fixed x', 'summary: a=3->0 | -']


def test_failed_exit_stores_the_cut_cause(tmp_path, monkeypatch):
    """Verify a nonzero exit stores the last stderr line, cut to 120, with
    a bar stored as a slash.

    Mutation: the cause uncut, or '|' kept.
    Oracle: a 200-char stderr line that opens with 'a|'.
    """
    folder = _root(tmp_path, monkeypatch)
    cause = 'a|' + 'b' * 198
    _probe(folder, f"echo early >&2\necho '{cause}' >&2\nexit 2")
    _cycle()
    assert _rows(folder)[-1]['log'] == (
        f'summary: probe-failed: exited 2: a/{"b" * 118} | work')


def test_a_probe_that_cannot_start_fails_the_row(tmp_path, monkeypatch):
    """Verify a probe whose interpreter is missing stores a failed row.

    Mutation: the start error raised out of finish.
    Oracle: a shebang naming no file; finish exits 0.
    """
    folder = _root(tmp_path, monkeypatch)
    probe = folder / 'probes' / 'hq-measure'
    probe.parent.mkdir()
    probe.write_text('#!/no/such/interpreter\n')
    probe.chmod(0o755)
    _cycle()
    assert _rows(folder)[-1]['log'].startswith('summary: probe-failed: cannot start')


def test_cap_kills_the_probe_group(tmp_path, monkeypatch):
    """Verify the cap kills the probe's whole process group.

    Mutation: a kill of the probe pid alone.
    Oracle: a grandchild sleep's pid is gone after the cap.
    """
    monkeypatch.setattr(hq, 'PROBE_CAP_SECS', 1)
    pid_file = tmp_path / 'pid'
    probe = tmp_path / 'hq-measure'
    probe.write_text(
        f'#!/bin/sh\nsleep 30 >/dev/null 2>&1 &\necho $! > {pid_file}\nwait\n')
    probe.chmod(0o755)
    assert hq.run_probe(probe, tmp_path) == ('probe-failed: ran past 1 s', False, '')
    stat = pathlib.Path(f'/proc/{pid_file.read_text().strip()}/stat')
    for _ in range(50):
        if not stat.exists() or stat.read_text().split(')')[-1].split()[0] == 'Z':
            break
        time.sleep(0.1)
    else:
        raise AssertionError('the grandchild outlived the cap')


def test_a_detached_child_does_not_hold_the_probe(tmp_path, monkeypatch):
    """Verify a probe that leaves a detached child with its streams returns
    its row at its own exit.

    Mutation: the streams read through pipes, which the child holds open
    past the cap.
    Oracle: a setsid sleep of 5 s that keeps stdout, under a cap of 1 s.
    """
    monkeypatch.setattr(hq, 'PROBE_CAP_SECS', 1)
    probe = tmp_path / 'hq-measure'
    probe.write_text("#!/bin/sh\nsetsid sleep 5 &\necho 'a=1->0'\n")
    probe.chmod(0o755)
    assert hq.run_probe(probe, tmp_path) == ('a=1->0', False, 'a=1->0')


def test_a_bar_in_the_token_line_fails_the_row(tmp_path, monkeypatch):
    """Verify a token line holding '|' stores a failed row.

    Mutation: the bar check dropped.
    Oracle: a token line split by a bar.
    """
    folder = _root(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=1->0 | b=2->0'")
    _cycle()
    assert _rows(folder)[-1]['log'].startswith(
        'summary: probe-failed: line does not parse')


def test_empty_and_unmeasured_output_store_unmeasured(tmp_path, monkeypatch):
    """Verify empty stdout, unmeasured, and summary: unmeasured store the
    unmeasured row.

    Mutation: unmeasured read as a token line.
    Oracle: the three outputs, each a cycle.
    """
    folder = _root(tmp_path, monkeypatch)
    for body in ('true', 'echo unmeasured', "echo 'summary: unmeasured'"):
        _probe(folder, body)
        _cycle()
    assert [row['log'] for row in _rows(folder)] == [
        'summary: unmeasured | work'] * 3


def test_no_probe_stores_the_log_and_never_refuses(tmp_path, monkeypatch):
    """Verify finish with no probe stores the stripped log and stall '-'.

    Mutation: a refusal left in for an unparsed summary under the loop.
    Oracle: a malformed typed summary under the thread's own loop.
    """
    folder = _root(tmp_path, monkeypatch)
    _loop(tmp_path, monkeypatch)
    _cycle('summary: a=1->0 junk')
    row = _rows(folder)[-1]
    assert (row['log'], row['stall'], row['window']) == ('a=1->0 junk', '-', '-')


def test_typed_prefixes_strip_in_any_order(tmp_path, monkeypatch):
    """Verify every leading summary: and stall review: strips from the log.

    Mutation: one strip pass.
    Oracle: stall review: summary: x stores x.
    """
    folder = _root(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=1->0'")
    _cycle('stall review: Summary: x')
    assert _rows(folder)[-1]['log'] == 'summary: a=1->0 | x'


def test_attended_ruling_needs_a_carried_three(tmp_path, monkeypatch):
    """Verify a typed stall review: is a ruling at a carried 3, and text at 2.

    Mutation: the threshold at 2 or 4.
    Oracle: a stuck probe; the marker at carried 2, then at carried 3.
    """
    folder = _root(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=5->0'")
    for _ in range(3):
        _cycle()
    _cycle('stall review: go')
    _cycle('stall review: go')
    rows = _rows(folder)
    assert [row['stall'] for row in rows] == ['0', '1', '2', '3', '0']
    assert [row['log'] for row in rows[-2:]] == [
        'summary: a=5->0 | go', 'summary: a=5->0 | stall review: go']


# --- the hold ----------------------------------------------------------


def test_exit_three_holds_only_under_its_own_loop(tmp_path, monkeypatch):
    """Verify exit 3 writes hold under this thread's loop and not another's.

    Mutation: the slug check dropped.
    Oracle: the same probe under a loop for this slug and for another.
    """
    folder = _root(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=1->0'\necho 'suite green' >&2\nexit 3")
    other = _loop(tmp_path, monkeypatch, 'other-slug')
    _cycle()
    assert not (other / 'hold').exists()
    own = _loop(tmp_path, monkeypatch)
    out = _cycle()
    hold_line = (
        'finish line met: suite green. To go past it, the ruling gives a new'
        ' goal for probes/hq-measure.')
    assert (own / 'hold').read_text() == hold_line + '\n'
    assert f'{hold_line} {hq._HOLD_TEXT}' in out.splitlines()


def test_every_token_at_goal_meets_the_finish_line(tmp_path, monkeypatch):
    """Verify the finish line needs every counting token at its goal.

    Mutation: the at-goal check on any token.
    Oracle: a=0->0 b=2->0 holds nothing; a=0->0 b=0->0 holds.
    """
    folder = _root(tmp_path, monkeypatch)
    loop_dir = _loop(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=0->0 b=2->0'")
    _cycle()
    assert not (loop_dir / 'hold').exists()
    _probe(folder, "echo 'a=0->0 b=0->0'")
    _cycle()
    assert (loop_dir / 'hold').read_text().startswith(
        'finish line met: a=0->0 b=0->0.')


def test_a_ruling_file_ends_the_hold(tmp_path, monkeypatch):
    """Verify a ruling file makes the row a ruling and removes both files.

    Mutation: hold kept, or the row stored plain.
    Oracle: a held loop dir with a ruling.
    """
    folder = _root(tmp_path, monkeypatch)
    loop_dir = _loop(tmp_path, monkeypatch)
    (loop_dir / 'hold').write_text('stall: held at 4 (no measured gain).\n')
    (loop_dir / 'ruling').write_text('keep  at it\n')
    _probe(folder, "echo 'a=5->0'")
    _cycle()
    assert _rows(folder)[-1]['log'] == (
        'summary: a=5->0 | stall review: keep at it | work')
    assert not (loop_dir / 'hold').exists()
    assert not (loop_dir / 'ruling').exists()


def test_stalls_from_four_hold(tmp_path, monkeypatch):
    """Verify a stall of 3 holds nothing and stalls of 4 and 5 hold.

    Mutation: STALL_PARK off by one, or '==' for '>='.
    Oracle: a stuck probe; the hold removed after stall 4.
    """
    folder = _root(tmp_path, monkeypatch)
    loop_dir = _loop(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=5->0'")
    for _ in range(4):
        _cycle()
    assert not (loop_dir / 'hold').exists()
    _cycle()
    assert (loop_dir / 'hold').read_text() == (
        'stall: held at 4 (no measured gain).\n')
    (loop_dir / 'hold').unlink()
    _cycle()
    assert (loop_dir / 'hold').read_text() == (
        'stall: held at 5 (no measured gain).\n')


def test_finish_line_met_again_holds_again(tmp_path, monkeypatch):
    """Verify a met finish line after a ruling holds again.

    Mutation: the hold keyed to a first met only.
    Oracle: met, ruling, met.
    """
    folder = _root(tmp_path, monkeypatch)
    loop_dir = _loop(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=0->0'")
    _cycle()
    assert (loop_dir / 'hold').exists()
    (loop_dir / 'ruling').write_text('raise the goal\n')
    _cycle()
    assert not (loop_dir / 'hold').exists()
    _cycle()
    assert (loop_dir / 'hold').exists()


# --- hq open -----------------------------------------------------------


def test_open_prints_the_stall_line_from_two(tmp_path, monkeypatch):
    """Verify open prints the stall line at 2, not 1, with the failed cause.

    Mutation: the threshold at 1 or 3.
    Oracle: a measured row, then failed rows at stall 1 and 2.
    """
    folder = _root(tmp_path, monkeypatch)
    _probe(folder, "echo 'a=5->0'")
    _cycle()
    _probe(folder, 'echo boom >&2\nexit 2')
    _cycle()
    assert 'cycles without progress' not in _run(['open', _SLUG])[1]
    _cycle()
    line = _STALL_LINE.format(n=2, cause='exited 2: boom')
    assert line in _run(['open', _SLUG])[1].splitlines()


def test_open_prints_the_hold_only_for_its_loop(tmp_path, monkeypatch):
    """Verify open prints the hold line under this thread's loop alone.

    Mutation: the slug check dropped.
    Oracle: one hold file read under a loop for this slug and another.
    """
    _root(tmp_path, monkeypatch)
    _cycle()
    hold_line = 'stall: held at 4 (no measured gain).'
    for slug in (_SLUG, 'other-slug'):
        loop_dir = _loop(tmp_path, monkeypatch, slug)
        (loop_dir / 'hold').write_text(hold_line + '\n')
    _, out, _ = _run(['open', _SLUG])
    assert f'{hold_line} {hq._HOLD_TEXT}' not in out.splitlines()
    monkeypatch.setenv('HQ_LOOP', str(tmp_path / f'loop-{_SLUG}'))
    _, out, _ = _run(['open', _SLUG])
    assert f'{hold_line} {hq._HOLD_TEXT}' in out.splitlines()
