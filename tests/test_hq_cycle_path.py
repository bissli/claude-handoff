"""Tests for the cycle path: begin's lock and work list, finish's checks.

Each test drives hq.main in-process with the HQ_* environment the other
verb tests use, so a failure names the printed line the agent reads.
"""

import contextlib
import io
import os
import pathlib
import subprocess

from bin import hq

_SLUG = 'test-slug'
_SESSION = 'session-abc'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'
# Exactly two hours before _NOW: the boundary L1 takes over without --force.
_BOUNDARY_LOCK_TIME = '2026-09-09T10:00:00'
_YOUNG_LOCK_TIME = '2026-09-09T11:00:00'


def _new_root(tmp_path, monkeypatch, cycle='1', now=_NOW):
    """Create an HQ_ROOT and set the HQ_* environment for one slug."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', now)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / _SLUG


def _run(argv):
    """Call hq.main, returning (rc, stdout, stderr) with SystemExit caught."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _lock(folder, session, time_str, cycle=1):
    """Write a foreign .hq.lock with the given owner and time."""
    (folder / '.hq.lock').write_text(
        f'slug={folder.name}\nsession={session}\nhost=other-host\n'
        f'time={time_str}\ncycle={cycle}\n')


def _git_repo(path):
    """Initialize a git repository at path with one committed file."""
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ['init', '-q', '-b', 'main'],
        ['config', 'user.email', 'seat@example.invalid'],
        ['config', 'user.name', 'Seat'],
    ):
        subprocess.run(['git', '-C', str(path)] + cmd, check=True,
                       capture_output=True)
    (path / 'tracked.txt').write_text('one\n')
    subprocess.run(['git', '-C', str(path), 'add', 'tracked.txt'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-q', '-m', 'seed'],
                   check=True, capture_output=True)


def test_a_wrapped_unfiled_bullet_keeps_its_continuation_line():
    """An indented line under an Unfiled bullet joins that bullet's body.

    Mutation: the continuation line skipped, so its text reaches neither
    standing.md nor HANDOFF.md and finish still exits 0.
    Oracle: hand-computed item for a bullet wrapped over two lines, and a
    refusal naming an unindented line that is no bullet at all.
    """
    wrapped = (
        '## Task\nDo.\n\n## Unfiled\n'
        '- decision: **Pin the tick size** The rounding step is fixed\n'
        '  and the remainder rides with it.\n')
    items, cursor_out, refusal = hq.drain_unfiled(wrapped)
    assert refusal is None
    assert items == [(
        'decision', 'Pin the tick size',
        'The rounding step is fixed and the remainder rides with it.')]
    assert '## Unfiled' not in cursor_out

    loose = (
        '## Task\nDo.\n\n## Unfiled\n'
        '- decision: **Pin the tick size** Fixed.\n'
        'a loose line with no bullet\n')
    _, _, refusal_loose = hq.drain_unfiled(loose)
    assert refusal_loose is not None
    assert 'a loose line with no bullet' in refusal_loose


def test_git_state_counts_staged_and_untracked_paths_as_dirty(tmp_path):
    """_git_state reports staged, untracked, and renamed paths, not only
    unstaged edits.

    Mutation: git diff --name-only in place of git status --porcelain, which
    reads a staged edit and a new file as clean.
    Oracle: a real repository holding one staged edit, one untracked file,
    and one staged rename; the rename counts as its new name.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    (repo / 'tracked.txt').write_text('two\n')
    (repo / 'renamed_from.txt').write_text('r\n')
    subprocess.run(['git', '-C', str(repo), 'add', '.'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'second'],
                   check=True, capture_output=True)
    (repo / 'tracked.txt').write_text('three\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'tracked.txt'],
                   check=True, capture_output=True)
    subprocess.run(
        ['git', '-C', str(repo), 'mv', 'renamed_from.txt', 'renamed_to.txt'],
        check=True, capture_output=True)
    (repo / 'untracked.txt').write_text('u\n')

    branch, sha, dirty = hq._git_state(repo)
    assert branch == 'main'
    assert len(sha) == 7
    assert set(dirty) == {'tracked.txt', 'renamed_to.txt', 'untracked.txt'}


def test_the_finish_header_lists_a_staged_path_as_dirty(tmp_path, monkeypatch):
    """A staged edit reaches the Written header's dirty list end to end.

    Mutation: the dirty list drawn from unstaged edits alone, so a session
    that staged its work writes 'clean' into the header.
    Oracle: a real repository whose only change is staged; the header names
    the path.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    (repo / 'tracked.txt').write_text('changed\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'tracked.txt'],
                   check=True, capture_output=True)
    monkeypatch.setenv('HQ_ROOT', str(repo))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.delenv('HQ_GIT', raising=False)
    folder = repo / '.handoff' / _SLUG

    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'staged work'])[0] == 0
    header = [
        ln for ln in (folder / 'HANDOFF.md').read_text().splitlines()
        if ln.startswith('Written:')][0]
    assert 'clean' not in header
    assert 'tracked.txt' in header


def test_a_lock_time_carrying_a_zone_offset_does_not_crash_begin(
        tmp_path, monkeypatch):
    """An aware lock time is aged against a naive now, not subtracted raw.

    Mutation: subtracting an aware lock time from a naive now, which raises
    TypeError past the except ValueError and leaves the folder unopenable
    until the lock is deleted by hand.
    Oracle: a lock three days old written with a +00:00 offset is older than
    two hours under every local zone, so begin takes it over and exits 0.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _lock(folder, 'other-session', '2026-09-06T12:00:00+00:00')
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: took over from other-session' in out


def test_every_work_list_class_caps_at_five_names_and_a_count(
        tmp_path, monkeypatch):
    """Sha moved, missing live, and successor missing each stop at five
    names and print the rest as a count.

    Mutation: a class truncated at five with no count, so a sixth item is
    invisible and the agent finishes with work it never saw.
    Oracle: six items per class; five lines then '... and 1 more' for
    each of the three per-name classes.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'target.py').write_text('t = 1\n')
    for i in range(1, 7):
        (folder / f'moved{i}.py').write_text('x = 1\n')
        (folder / f'gone{i}.py').write_text('y = 1\n')
        (folder / f'old{i}.py').write_text('z = 1\n')
        assert _run(['stamp', _SLUG, f'moved{i}.py'])[0] == 0
        assert _run(['stamp', _SLUG, f'gone{i}.py'])[0] == 0
        assert _run(['stamp', _SLUG, f'old{i}.py',
                     '--successor', 'target.py'])[0] == 0
    for i in range(1, 7):
        (folder / f'moved{i}.py').write_text('x = 2\n')
        (folder / f'gone{i}.py').unlink()
    (folder / 'target.py').unlink()

    lines = _run(['begin', _SLUG])[1].splitlines()
    for prefix in ('  sha moved: ', '  missing live: ', '  successor missing: '):
        named = [ln for ln in lines if ln.startswith(prefix)]
        assert len(named) == 5, prefix
        assert lines[lines.index(named[-1]) + 1] == '  ... and 1 more', prefix


def test_finish_checks_r3_before_a_missing_gated_row(tmp_path, monkeypatch):
    """With both pending, finish names the R3 sha move, not the missing row.

    Mutation: the two classes swapped against design step 7, so a sha move
    the agent can re-stamp hides behind a file it must first replace.
    Oracle: the design's order - lock, W1/W2, R3, missing gated row - so the
    only printed line is the R3 one.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    (folder / 'poller.py').write_text('a = 1\n')
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])[0] == 0
    assert _run(['stamp', _SLUG, 'poller.py'])[0] == 0
    (folder / 'SPEC.md').write_text('# Spec\n\nTwo.\n')
    (folder / 'poller.py').unlink()

    rc, out, _ = _run(['finish', _SLUG, '--log', 'x'])
    assert (rc, out.strip()) == (1, 'R3: SPEC.md sha moved; re-stamp before finish')


def test_a_second_begin_archives_a_later_hand_edit(tmp_path, monkeypatch):
    """Every begin compares HANDOFF.md with the manifest sha, not just the
    first begin of a session.

    Mutation: the hand-edit check placed behind the own-session early
    return, so a hand edit between two begins is never archived or named.
    Oracle: two hand edits and two begins; the second begin prints the line
    and c01.hand.md holds the second edit's bytes.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    handoff = folder / 'HANDOFF.md'
    hand_copy = folder / 'cycles' / 'c01.hand.md'

    handoff.write_text(handoff.read_text() + 'first hand edit\n')
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: HANDOFF.md changed since last finish' in out

    handoff.write_text(handoff.read_text() + 'second hand edit\n')
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: HANDOFF.md changed since last finish' in out
    assert hand_copy.read_bytes() == handoff.read_bytes()


def test_finish_refuses_an_unfiled_section_below_the_first_marker(
        tmp_path, monkeypatch):
    """An Unfiled section under the blocks is refused, never dropped.

    Mutation: the cursor cut at the first marker with no guard, so the
    section is deleted undrained and finish still exits 0.
    Oracle: byte-identical HANDOFF.md after the refusal, and the documented
    line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['begin', _SLUG])[0] == 0
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(
        handoff.read_text() + '\n## Unfiled\n- decision: **Late** body\n')
    before = handoff.read_bytes()

    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 1
    assert out.strip() == (
        "hq finish: '## Unfiled' sits below the first hq: marker;"
        ' move it above')
    assert handoff.read_bytes() == before
    assert 'Late' not in (folder / 'standing.md').read_text()


def test_acknowledge_names_the_broken_witness_and_records_it(
        tmp_path, monkeypatch):
    """Finish --acknowledge prints the witness line and writes it to the
    manifest note beside the reason.

    Mutation: the break text suppressed with the flag, so nothing in the
    record says which witness broke or what the two shas were.
    Oracle: a W1 break provoked by editing 'always' to 'alwayz' in place;
    the printed line and the note both carry the expected/got pair.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text('# Spec\n')
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['begin', _SLUG])[0] == 0
    ledger = folder / 'ledger.tsv'
    ledger.write_bytes(ledger.read_bytes().replace(b'always', b'alwayz', 1))

    rc, out, _ = _run(['finish', _SLUG, '--log', 'two',
                       '--acknowledge', 'formatter ran'])
    assert rc == 0
    printed = [ln for ln in out.splitlines() if ln.startswith('acknowledged: ')]
    assert len(printed) == 1
    assert printed[0].startswith('acknowledged: W1: ledger prefix changed')
    assert '(expected ' in printed[0]
    assert ', got ' in printed[0]
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows[-1]['note'].startswith('acknowledged W1: ledger prefix changed')
    assert rows[-1]['note'].endswith('; formatter ran')


def test_acknowledge_with_no_break_is_an_advisory_and_records_nothing(
        tmp_path, monkeypatch):
    """--acknowledge on a sound folder says so and leaves the note empty.

    Mutation: the reason recorded whether or not a witness broke, so the
    manifest claims a break that never happened.
    Oracle: a finish with no edit to ledger.tsv; note stays '-'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one',
                       '--acknowledge', 'nothing broke'])
    assert rc == 0
    assert 'advisory: --acknowledge given, no witness broke' in out
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows[-1]['note'] == '-'


def _cursor(folder, body):
    """Overwrite the cursor, keeping the header line the script owns.
    """
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text()
    handoff.write_text(text[:text.index('\n## ') + 1] + body)


def _drop_one_state_line(folder, monkeypatch):
    """File cycle 1 with a State line, then open cycle 2 having cut it.
    """
    assert _run(['begin', _SLUG])[0] == 0
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token().\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert _run(['begin', _SLUG])[0] == 0
    _cursor(folder, '## Task\nRefresh the poller token.\n\n## Now\nCap it.\n')


def test_a_cycle_that_carries_every_line_records_no_count(
        tmp_path, monkeypatch):
    """A second cycle keeping every line leaves the manifest note empty.

    Mutation: the count clause appended whenever an archive precedes the
    cycle, so the manifest reads '0 not carried from c01' on a clean
    cycle and a reader cannot tell a real drop by the note's presence.
    Oracle: cycle 2 re-filed with cycle 1's cursor unchanged; note '-'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token().\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    assert 'not carried' not in out
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows[-1]['cycle'] == '2'
    assert rows[-1]['note'] == '-'


def test_the_acknowledged_clause_stays_first_in_a_two_clause_note(
        tmp_path, monkeypatch):
    """A cycle that acknowledges a break and drops a line records both.

    Mutation: the count clause placed before the acknowledged one,
    which breaks every assertion reading the note by its prefix; or one
    clause overwriting the other so the note carries a single clause.
    Oracle: the note read back, split on the ' | ' join.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _drop_one_state_line(folder, monkeypatch)
    ledger = folder / 'ledger.tsv'
    ledger.write_bytes(ledger.read_bytes().replace(b'path', b'pathh', 1))
    rc, _, _ = _run([
        'finish', _SLUG, '--log', 'two', '--acknowledge', 'formatter ran'])
    assert rc == 0
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    clauses = rows[-1]['note'].split(' | ')
    assert len(clauses) == 2
    assert clauses[0].startswith('acknowledged ')
    assert clauses[0].endswith('; formatter ran')
    assert clauses[1] == '1 not carried from c01'


def test_the_collision_advisory_strips_heading_markers_and_backticks():
    """A collision quotes the heading text alone, with no ## and no ticks.

    Mutation: the raw heading line quoted, so the advisory reads
    '## Display `contract`' where the design fixes the bare text.
    Oracle: hand-computed line for one heading and one term.
    """
    hits = hq.collisions(
        'Rework the Display contract this cycle.',
        [],
        [('SPEC.md', 14, '## Display `contract` stays fixed')],
        {'Display': 1})
    assert hits == [
        'collides: Display <- SPEC.md:14 "Display contract stays fixed"']


def test_a_tab_in_the_log_reaches_the_rendered_log_as_a_space(
        tmp_path, monkeypatch):
    """Finish normalizes --log whitespace before both the file and the row.

    Mutation: the raw text rendered into ## Log while the manifest gets the
    normalized form, so the two records of one cycle disagree.
    Oracle: a --log holding a tab and a newline; the rendered Log line and
    the manifest log field both read as single spaces.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'shipped\tstep\n4'])[0] == 0
    text = (folder / 'HANDOFF.md').read_text()
    assert '\t' not in text
    assert 'shipped step 4' in text
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows[-1]['log'] == 'shipped step 4'


def test_an_unparsable_now_is_a_usage_error_that_writes_nothing(
        tmp_path, monkeypatch):
    """An HQ_NOW that is no timestamp exits 2 before any write.

    Mutation: the bad value carried into the lock's age arithmetic, where
    the except ValueError turns a live foreign lock into a silent takeover.
    Oracle: exit 2, the documented line, and no folder on disk.
    """
    folder = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_NOW', 'yesterday')
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 2
    assert 'hq: HQ_NOW must be an ISO 8601 timestamp' in out
    assert not folder.exists()


def test_an_unreadable_lock_is_refused_until_force(tmp_path, monkeypatch):
    """A lock with no session, or an unparsable time, refuses begin.

    Mutation: an unparsable lock treated as absent (silently overwritten)
    or as stale (taken over with no flag), either way losing a live cycle
    held by another session.
    Oracle: exit 1 and the documented line for both shapes; exit 0 with
    --force, after which the lock names this session.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    lock_path = folder / '.hq.lock'

    lock_path.write_text('garbage with no key value pair\n')
    rc, out, _ = _run(['begin', _SLUG])
    assert (rc, out.strip()) == (
        1, 'hq begin: lock file unreadable; use --force to take over')
    assert lock_path.read_text() == 'garbage with no key value pair\n'

    lock_path.write_text(
        f'slug={_SLUG}\nsession=other-session\nhost=other-host\n'
        'time=whenever\ncycle=1\n')
    rc, out, _ = _run(['begin', _SLUG])
    assert (rc, out.strip()) == (
        1, 'hq begin: lock file unreadable; use --force to take over')

    rc, out, _ = _run(['begin', _SLUG, '--force'])
    assert rc == 0
    assert f'session={_SESSION}' in lock_path.read_text()


def test_the_lock_is_created_exclusively(tmp_path, monkeypatch):
    """Begin creates .hq.lock with O_CREAT|O_EXCL, so two begins cannot both
    win the same folder.

    Mutation: read-decide-write on the lock path, which lets a second begin
    that read no lock write one after the first did.
    Oracle: a spy over os.open records the flags used for the .hq.lock path.
    """
    _new_root(tmp_path, monkeypatch)
    seen = []
    real_open = os.open

    def spy(path, flags, *args, **kwargs):
        seen.append((str(path), flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(hq.os, 'open', spy)
    assert _run(['begin', _SLUG])[0] == 0
    lock_flags = [f for p, f in seen if p.endswith('.hq.lock')]
    assert len(lock_flags) == 1
    assert lock_flags[0] & os.O_EXCL
    assert lock_flags[0] & os.O_CREAT


def test_a_non_integer_cycle_is_a_usage_error_or_a_refusal(
        tmp_path, monkeypatch):
    """A non-integer HQ_CYCLE exits 2; a non-integer manifest cycle exits 1.

    Mutation: int() called on either value with no guard, so the agent
    meets a ValueError traceback instead of a line naming the fix.
    Oracle: the two documented lines, each provoked in turn.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    monkeypatch.setenv('HQ_CYCLE', 'abc')
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 2
    assert 'hq: HQ_CYCLE must be an integer' in out

    monkeypatch.delenv('HQ_CYCLE')
    manifest = folder / 'cycles' / 'manifest.tsv'
    manifest.write_text(
        hq._MANIFEST_HEADER + '\n'
        + '\t'.join(['x'] + ['-'] * (len(hq.MANIFEST_FIELDS) - 1)) + '\n')
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 1
    assert 'hq: cycles/manifest.tsv row 1 has a non-integer cycle' in out


def test_the_session_flag_beats_its_environment_variable(
        tmp_path, monkeypatch):
    """HQ_CYCLE, HQ_NOW, and HQ_HOST set the anchors directly; --session
    overrides HQ_SESSION.

    Mutation: any one of the three env lookups dropped from anchors, so a
    default silently wins and a replay stamps the wrong cycle or owner;
    or --session no longer overriding HQ_SESSION.
    Oracle: the lock begin writes carries all four values, each set
    independently of the others.
    """
    folder = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_CYCLE', '7')
    monkeypatch.setenv('HQ_NOW', '2026-01-02T03:04:05')
    monkeypatch.setenv('HQ_HOST', 'flag-host')
    rc, _, _ = _run(['--session', 'flag-session', 'begin', _SLUG])
    assert rc == 0
    lock = hq._read_lock(folder)
    assert lock['cycle'] == '7'
    assert lock['time'] == '2026-01-02T03:04:05'
    assert lock['session'] == 'flag-session'
    assert lock['host'] == 'flag-host'


def test_a_foreign_lock_exactly_two_hours_old_is_taken_over(
        tmp_path, monkeypatch):
    """The two-hour boundary is exclusive: an age of 7200 s is stale.

    Mutation: age < _TWO_HOURS weakened to <=, or _TWO_HOURS raised to
    7260, either of which refuses a lock the design says to take over.
    Oracle: a lock timed exactly two hours before HQ_NOW; begin exits 0 and
    says it took over, while one minute younger refuses.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _lock(folder, 'other-session', _BOUNDARY_LOCK_TIME)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: took over from other-session' in out

    _lock(folder, 'other-session', '2026-09-09T10:01:00')
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1
    assert out.startswith('hq begin: lock held by other-session')


def test_a_forced_takeover_reaches_the_manifest_session_column(
        tmp_path, monkeypatch):
    """Finish records '<new> took over <old>' after begin --force.

    Mutation: the takeover key dropped from the lock, ignored at finish, or
    lost when a second begin rewrites the lock it already owns, so the
    manifest credits the cycle to the new session alone.
    Oracle: the documented session value for a forced takeover, read after
    a second begin by the taking session.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _lock(folder, 'other-session', _YOUNG_LOCK_TIME)
    assert _run(['begin', _SLUG, '--force'])[0] == 0
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows[-1]['session'] == f'{_SESSION} took over other-session'


def test_dirty_str_switches_to_a_count_at_six_paths():
    """Six dirty paths render as a count; five render as the list.

    Mutation: the threshold moved to six or more, so a sixth path pushes
    the header line past its budget instead of collapsing.
    Oracle: hand-computed tails either side of the five-path boundary.
    """
    assert hq._dirty_str(['a', 'b', 'c', 'd', 'e', 'f']) == ' | 6 files dirty'
    assert hq._dirty_str(['a', 'b', 'c', 'd', 'e']) == ' | dirty: a, b, c, d, e'


def test_an_unstampable_name_is_named_apart_from_a_conflicted_copy(
        tmp_path, monkeypatch):
    """A skip entry holding a tab prints as unstampable; a conflicted copy
    keeps its own line; a non-regular entry is named unstampable too.

    Mutation: every skip entry printed as a conflicted copy, so a name the
    ledger cannot carry reads as a sync duplicate and is resolved by hand
    that never fixes it.
    Oracle: a walk of three skip entries; begin and finish each name all
    three, with the tab shown as an escape so the line stays one line.
    """
    _new_root(tmp_path, monkeypatch)
    monkeypatch.setattr(hq, '_walk_folder', lambda folder: [
        ('tab\tname.md', 'skip'),
        ('notes (conflicted copy 2026-01-01).md', 'skip'),
        ('socket.sock', 'skip'),
        ])
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert (
        '  unstampable name: tab\\tname.md'
        ' - rename or remove it, or leave it out of the ledger'
        in out.splitlines())
    assert (
        '  conflicted copy: notes (conflicted copy 2026-01-01).md'
        ' - a sync duplicate; resolve it by hand'
        in out.splitlines())
    assert (
        '  unstampable name: socket.sock'
        ' - rename or remove it, or leave it out of the ledger'
        in out.splitlines())

    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    assert (
        'advisory: unstampable name: tab\\tname.md'
        ' - rename or remove it, or leave it out of the ledger'
        in out.splitlines())
    assert (
        'advisory: conflicted copy: notes (conflicted copy 2026-01-01).md'
        ' - a sync duplicate; resolve it by hand'
        in out.splitlines())
    assert (
        'advisory: unstampable name: socket.sock'
        ' - rename or remove it, or leave it out of the ledger'
        in out.splitlines())


def test_a_handoff_that_is_a_directory_stops_begin_and_finish(
        tmp_path, monkeypatch, capsys):
    """Begin and finish name a HANDOFF.md that is a directory instead of
    raising.

    Mutation: the guard present in adopt and open but absent from begin
    and finish, so a folder whose file was replaced by a directory meets
    an IsADirectoryError traceback from the hand-edit check.
    Oracle: exit 1 and the documented line from both verbs, no traceback.
    """
    folder = _new_root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    (folder / 'HANDOFF.md').unlink()
    (folder / 'HANDOFF.md').mkdir()
    for verb in (['begin', _SLUG], ['finish', _SLUG, '--log', 'two']):
        rc, out, _ = _run(verb)
        assert rc == 1
        assert 'hq: HANDOFF.md is a directory' in out


def test_begin_on_a_new_slug_creates_it_not_a_prefix_match(
        tmp_path, monkeypatch):
    """Begin on a slug with no exact folder creates that folder.

    Mutation: the single-candidate prefix branch running before the
    missing_ok branch in _find_folder, so begin 'auth' while 'authz'
    exists opens authz instead of creating auth.
    Oracle: after begin 'auth' with 'authz' already present, auth is a
    directory and authz carries no lock written by this begin.
    """
    handoffs = _new_root(tmp_path, monkeypatch).parent
    handoffs.mkdir(parents=True, exist_ok=True)
    (handoffs / 'authz').mkdir()
    rc, _, _ = _run(['begin', 'auth'])
    assert rc == 0
    assert (handoffs / 'auth').is_dir()
