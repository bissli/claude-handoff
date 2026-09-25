"""Clean refusals where the script used to crash or hang: unreadable
files, a FIFO under stamp, a tilde root, a slug with two dots, and a
silent diff of equal cursors.
"""

import os
import pathlib
import signal
import threading

import pytest

from bin import hq

_SLUG = 'rob-test'
_NOW = '2026-09-10T09:00:00'
_SESSION = 'session-rob'
_HOST = 'test-host'


def _setup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, slug: str = _SLUG,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create HQ_ROOT and set env vars; return (root, folder) paths.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_CYCLE', '1')
    folder = root / '.handoff' / slug
    return root, folder


def _begin(slug: str, folder: pathlib.Path) -> None:
    """Run begin and assert it created the folder.
    """
    rc = hq.main(['begin', slug])
    assert rc == 0
    assert folder.is_dir()


def _strip_read(out: str) -> str:
    """Remove the appended read topic from open output before comparing."""
    return out.replace(hq.HELP_TOPICS['read'] + '\n', '')


# --- The OSError guard in main ---


@pytest.mark.skipif(os.geteuid() == 0, reason='root bypasses chmod 000')
def test_unreadable_ledger_artifacts(tmp_path, monkeypatch, capsys):
    """Artifacts on a chmod-000 ledger.tsv prints the access error and exits 1.

    Mutation: OSError from read_text not caught in main, producing a
    traceback instead of the documented 'hq: cannot access ...' line.
    Oracle: stdout contains 'hq: cannot access <ledger>: Permission denied',
    rc is 1, and stderr has no 'Traceback'.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _begin(_SLUG, folder)
    capsys.readouterr()
    ledger = folder / 'ledger.tsv'
    ledger.chmod(0o000)
    try:
        rc = hq.main(['artifacts', _SLUG])
    finally:
        ledger.chmod(0o644)
    out, err = capsys.readouterr()
    assert rc == 1
    assert f'hq: cannot access {ledger}: Permission denied' in out
    assert 'Traceback' not in err


@pytest.mark.skipif(os.geteuid() == 0, reason='root bypasses chmod 000')
def test_handoff_dir_is_file_begin(tmp_path, monkeypatch, capsys):
    """Begin with .handoff/ as a regular file prints the access line.

    Mutation: NotADirectoryError from mkdir not caught in main, producing
    a traceback instead of the documented 'hq: cannot access ...' line.
    Oracle: stdout contains 'hq: cannot access <folder>: Not a directory',
    rc is 1, and stderr has no 'Traceback'.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    handoff_dir = root / '.handoff'
    handoff_dir.write_text('not a directory')
    rc = hq.main(['begin', _SLUG])
    out, err = capsys.readouterr()
    assert rc == 1
    assert f'hq: cannot access {folder}: Not a directory' in out
    assert 'Traceback' not in err


# --- An unreadable lock ---


@pytest.mark.skipif(os.geteuid() == 0, reason='root bypasses chmod 000')
def test_begin_unreadable_lock(tmp_path, monkeypatch, capsys):
    """Begin on a chmod-000 lock prints the documented line and exits 1.

    Mutation: _read_lock missing OSError catch, raising PermissionError
    instead of returning {} - begin takes the traceback path rather than
    the 'lock file unreadable' path.
    Oracle: stdout contains 'hq begin: lock file unreadable; use --force
    to take over', rc is 1.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'cycles').mkdir()
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-10 | Cycle: 1\n\n'
        '## Task\n\n## Now\n\n## Plan\n\n## State\n\n'
        '## Environment\n\n## Open questions\n\n## Log\n',
        encoding='utf-8')
    (folder / 'ledger.tsv').write_text(
        '\t'.join(hq.LEDGER_FIELDS) + '\n', encoding='utf-8')
    (folder / 'standing.md').write_text('', encoding='utf-8')
    (folder / 'cycles' / 'manifest.tsv').write_text(
        '\t'.join(hq.MANIFEST_FIELDS) + '\n', encoding='utf-8')
    lock = folder / '.hq.lock'
    lock.write_text(
        f'slug={_SLUG}\nsession=foreign-session\nhost=other\n'
        f'time={_NOW}\ncycle=1\n',
        encoding='utf-8')
    lock.chmod(0o000)
    try:
        rc = hq.main(['begin', _SLUG])
    finally:
        lock.chmod(0o644)
    out, _ = capsys.readouterr()
    assert rc == 1
    assert 'hq begin: lock file unreadable; use --force to take over' in out


# --- A FIFO under stamp ---


def test_stamp_fifo_prints_error_and_exits_2(tmp_path, monkeypatch, capsys):
    """Stamp on a FIFO prints the error line, exits 2, and writes no row.

    Mutation: missing is_file() guard in _do_stamp; open() on a FIFO
    blocks forever rather than returning immediately with an error.
    Oracle: stdout contains 'hq stamp: <fifo>: is not a regular file or
    directory', rc is 2, ledger unchanged; signal.alarm guards against hang.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _begin(_SLUG, folder)
    capsys.readouterr()
    fifo = folder / 'my-pipe'
    os.mkfifo(str(fifo))
    ledger_before = (folder / 'ledger.tsv').read_bytes()

    result = {'rc': None}

    def _run():
        result['rc'] = hq.main(['stamp', _SLUG, 'my-pipe'])

    def _alarm(signum, frame):
        raise TimeoutError('stamp on FIFO did not return - _do_stamp hung')

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(3)
    try:
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=2)
    finally:
        signal.alarm(0)

    out, _ = capsys.readouterr()
    assert result['rc'] == 2
    assert 'hq stamp: my-pipe is not a regular file or directory' in out
    assert (folder / 'ledger.tsv').read_bytes() == ledger_before


# --- diff on identical cursors ---


def test_diff_identical_cursors_no_output(tmp_path, monkeypatch, capsys):
    r"""Diff <slug> N N writes zero bytes to stdout when cursors are equal.

    Mutation: unconditional print('\\n'.join(out)) with out=[] emits a
    newline even when the two cursors are identical.
    Oracle: capsys out == '' after diff <slug> 1 1.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _begin(_SLUG, folder)
    capsys.readouterr()
    monkeypatch.setenv('HQ_CYCLE', '1')
    rc_finish = hq.main(['finish', _SLUG, '--log', 'first cycle done'])
    capsys.readouterr()
    assert (folder / 'cycles' / 'c01.md').exists()
    rc = hq.main(['diff', _SLUG, '1', '1'])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert out == ''


# --- A tilde in HQ_ROOT ---


def test_resolve_root_expands_tilde(tmp_path, monkeypatch, capsys):
    """Begin creates the folder under the expanded tilde path, not under cwd.

    Mutation: pathlib.Path(root) without .expanduser() so HQ_ROOT='~/r'
    creates '<cwd>/~/r/.handoff/<slug>' rather than '<home>/r/.handoff/<slug>'.
    Oracle: folder created under tmp_path / 'r' / '.handoff'; nothing named
    '~' appears under the current working directory.
    """
    fake_home = tmp_path / 'home'
    fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.setenv('HQ_ROOT', '~/r')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.chdir(tmp_path)
    rc = hq.main(['begin', _SLUG])
    capsys.readouterr()
    assert rc == 0
    expected_folder = fake_home / 'r' / '.handoff' / _SLUG
    assert expected_folder.is_dir(), (
        f'folder not created at {expected_folder}')
    assert not (tmp_path / '~').exists(), (
        'a "~" directory was created under cwd - tilde was not expanded')


# --- Two dots in a slug ---


def test_slug_with_double_dot_exits_2(tmp_path, monkeypatch, capsys):
    """Begin 'has..dots' exits 2 with 'hq: invalid slug' and creates nothing.

    Mutation: slug regex [A-Za-z0-9._-]* admits consecutive dots so '..'
    passes the fullmatch check, allowing path traversal out of .handoff/.
    Oracle: SystemExit code 2, out contains 'hq: invalid slug', no folder.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    bad_slug = 'has..dots'
    with pytest.raises(SystemExit) as exc:
        hq.main(['begin', bad_slug])
    out, _ = capsys.readouterr()
    assert exc.value.code == 2
    assert 'hq: invalid slug' in out
    assert not (root / '.handoff' / bad_slug).exists()


def test_slug_single_dot_still_valid(tmp_path, monkeypatch, capsys):
    """Begin 'has.dots' succeeds - a single dot remains a valid slug char.

    Mutation: an overly broad '..' check rejecting any slug with a dot.
    Oracle: rc is 0, folder created at root/.handoff/has.dots.
    """
    root, _ = _setup(tmp_path, monkeypatch)
    good_slug = 'has.dots'
    rc = hq.main(['begin', good_slug])
    capsys.readouterr()
    assert rc == 0
    assert (root / '.handoff' / good_slug).is_dir()


def _pre_ledger_folder(folder: pathlib.Path) -> None:
    """Write a conforming HANDOFF.md with no ledger into the folder.
    """
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\nWritten: 2026-09-10 | Cycle: 1\n\n'
        '## Task\nwork\n\n## Now\nnext\n',
        encoding='utf-8')


@pytest.mark.skipif(os.geteuid() == 0, reason='root bypasses chmod 000')
def test_begin_force_takes_over_an_unreadable_lock(tmp_path, monkeypatch, capsys):
    """Begin --force replaces a lock the process cannot open for writing.

    Mutation: writing into the chmod-000 lock without unlinking it first,
    so the takeover ends in 'hq: cannot access' and the lock stays.
    Oracle: rc 0, 'took over from' printed, and the new lock names this
    session.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _pre_ledger_folder(folder)
    lock = folder / '.hq.lock'
    lock.write_text(
        f'slug={_SLUG}\nsession=foreign-session\nhost=other\n'
        f'time={_NOW}\ncycle=1\n',
        encoding='utf-8')
    lock.chmod(0o000)
    try:
        rc = hq.main(['begin', '--force', _SLUG])
    finally:
        if lock.exists():
            lock.chmod(0o644)
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert 'hq begin: took over from' in out
    assert f'session={_SESSION}' in lock.read_text(encoding='utf-8')


def test_begin_refuses_a_foreign_lock_before_adopt(tmp_path, monkeypatch, capsys):
    """A young foreign lock refuses begin before adopt rewrites the folder.

    Mutation: adopt run before the lock is checked, so a held folder gains
    ledger.tsv and a rewritten HANDOFF.md from a session that was refused.
    Oracle: rc 1, 'lock held by' printed, and no ledger.tsv exists.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _pre_ledger_folder(folder)
    (folder / '.hq.lock').write_text(
        f'slug={_SLUG}\nsession=foreign-session\nhost=other\n'
        f'time={_NOW}\ncycle=1\n',
        encoding='utf-8')
    rc = hq.main(['begin', _SLUG])
    out, _ = capsys.readouterr()
    assert rc == 1
    assert 'hq begin: lock held by foreign-session' in out
    assert not (folder / 'ledger.tsv').exists()


def test_unknown_user_in_a_tilde_root_keeps_the_literal_path(
        tmp_path, monkeypatch, capsys):
    """A root naming an unknown user is used as written, without a crash.

    Mutation: expanduser left unguarded, so its RuntimeError escapes the
    OSError guard as a traceback.
    Oracle: the documented 'no folder matching' line with the literal root
    and exit 2.
    """
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        hq.main(['--root', '~nosuchuser12345/x', 'open', 'nosuch'])
    out, _ = capsys.readouterr()
    assert exit_info.value.code == 2
    assert "no folder matching 'nosuch' under ~nosuchuser12345/x/.handoff" in out


@pytest.mark.skipif(os.geteuid() == 0, reason='root bypasses chmod 000')
def test_unreadable_gated_artifact_is_not_a_moved_sha(
        tmp_path, monkeypatch, capsys):
    """A gated file the process cannot read is not reported as moved.

    Mutation: the '-' sha of an unreadable file compared against the
    stored sha, so open prints 'sha moved since stamp' and finish blocks
    on R3 for a file nobody changed.
    Oracle: no 'sha moved' line from open and no 'R3' line from finish.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _begin(_SLUG, folder)
    spec = folder / 'SPEC-x.md'
    spec.write_text('# Spec\n\nBody.\n', encoding='utf-8')
    assert hq.main(['stamp', _SLUG, 'SPEC-x.md', '--where', 'Spec']) == 0
    spec.chmod(0o000)
    # Drain begin/stamp output so opened captures only what open prints.
    capsys.readouterr()
    try:
        hq.main(['open', _SLUG])
        opened, _ = capsys.readouterr()
        opened = _strip_read(opened)
        hq.main(['finish', _SLUG, '--log', 'x'])
        finished, _ = capsys.readouterr()
    finally:
        spec.chmod(0o644)
    assert 'sha moved' not in opened
    assert 'R3' not in finished


def test_access_error_without_a_path_prints_a_placeholder(
        tmp_path, monkeypatch, capsys):
    """An OSError with no filename prints '?' where the path would be.

    Mutation: falling back to str(exc), which repeats the reason in the
    path slot.
    Oracle: the exact line 'hq: cannot access ?: OSError'.
    """
    root, folder = _setup(tmp_path, monkeypatch)

    def _raise(argv):
        raise OSError('boom')

    monkeypatch.setattr(hq, '_resolve_root', _raise)
    rc = hq.main(['open', _SLUG])
    out, _ = capsys.readouterr()
    assert rc == 1
    assert out == 'hq: cannot access ?: OSError\n'


# --- A cwd inside the handoff tree ---


def test_root_resolves_to_the_ancestor_holding_the_handoff_dir(
        tmp_path, monkeypatch, capsys):
    """Outside git, a cwd inside .handoff/<slug>/ or any subdirectory of
    the root resolves the root to the ancestor that holds .handoff/.

    Mutation: the walk-up dropped, so the cwd is the root and `open` looks
    for <folder>/.handoff/<slug> and exits 2 with `no folder matching`.
    Oracle: `open` and `list` exit 0 from inside the folder, from a probe
    directory nested in it, and from an unrelated subdirectory of the root;
    `list` names the slug.
    """
    root, folder = _setup(tmp_path, monkeypatch)
    _begin(_SLUG, folder)
    capsys.readouterr()
    monkeypatch.delenv('HQ_ROOT')
    probes = folder / 'probes'
    probes.mkdir()
    deep = root / 'src' / 'pkg'
    deep.mkdir(parents=True)
    for cwd in (folder, probes, deep):
        monkeypatch.chdir(cwd)
        assert hq.main(['open', _SLUG]) == 0, f'open failed from {cwd}'
        capsys.readouterr()
        assert hq.main(['list']) == 0
        assert capsys.readouterr().out.splitlines()[2].startswith(f'{_SLUG}  ')


# --- A failed write during finish ---


def test_finish_archives_before_it_truncates_the_handoff(
        tmp_path, monkeypatch, capsys):
    """A write failing partway through HANDOFF.md leaves the cursor in cycles/.

    Mutation: HANDOFF.md written before cycles/cNN.md, so the failure
    truncates the only file holding the new cursor.
    Oracle: an injected ENOSPC after the first ten characters of the
    HANDOFF.md write; the cursor line must survive in cycles/c01.md.
    """
    _, folder = _setup(tmp_path, monkeypatch)
    _begin(_SLUG, folder)
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(
        handoff.read_text(encoding='utf-8').replace(
            '## Task\n', '## Task\nRefresh the poller token.\n', 1),
        encoding='utf-8')
    capsys.readouterr()
    real_write_text = pathlib.Path.write_text

    def failing_write_text(self, data, *args, **kwargs):
        if self.name == 'HANDOFF.md':
            real_write_text(self, data[:10], *args, **kwargs)
            raise OSError(28, 'No space left on device', str(self))
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, 'write_text', failing_write_text)
    rc = hq.main(['finish', _SLUG, '--log', 'first cycle done'])
    out, _ = capsys.readouterr()
    assert rc == 1
    assert 'No space left on device' in out
    assert 'Refresh the poller token.' not in handoff.read_text(encoding='utf-8')
    archive = (folder / 'cycles' / 'c01.md').read_text(encoding='utf-8')
    assert 'Refresh the poller token.' in archive
