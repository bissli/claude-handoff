"""Tests for the handoff slug the status line names beside the directory."""

import os
import pathlib
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)

import statusline as sl  # noqa: E402  (path must be set before this resolves)

from bin import hq  # noqa: E402  (same)

_SESSION = 'session-abc'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'


def _visible(session, cwd, state_dir, monkeypatch):
    """Render one status line with color stripped, against a temp cache."""
    monkeypatch.setattr(sl, 'STATE_DIR', str(state_dir))
    return re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
        'session_id': session,
        'model': {'id': 'claude-opus-5', 'display_name': 'Opus 5'},
        'workspace': {'current_dir': cwd},
        'context_window': {'total_input_tokens': 248_000},
        }))


def _root(tmp_path, monkeypatch, slug):
    """Create an HQ_ROOT holding one handoff folder and point hq at it."""
    root = pathlib.Path(tmp_path) / 'root'
    (root / '.handoff' / slug).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / slug


def test_the_thread_is_named_after_the_directory_never_instead_of_it(
        tmp_path, monkeypatch):
    """Verify the slug qualifies the directory and sits last on the line.

    Mutation: rendering the slug in place of the directory, or ahead of
    it. Either loses the field the line has always carried, and the one
    ahead survives truncation at the slug's expense.
    Oracle: hand-computed - a session on auth-token in /x/myproject
    renders a line ending 'opus myproject:auth-token'.
    """
    (tmp_path / f'{_SESSION}.thread').write_text('auth-token\n')
    line = _visible(_SESSION, '/x/myproject', tmp_path, monkeypatch)
    assert line.endswith('opus myproject:auth-token')


def test_a_session_on_no_thread_renders_the_line_it_always_did(
        tmp_path, monkeypatch):
    """Verify the slug field vanishes whole when a session has no thread.

    Mutation: appending the separator outside the guard, which leaves
    every ordinary session reading 'myproject:' - a dangling colon that
    reads as a truncated slug.
    Oracle: differential - the same payload with the record present and
    absent, the absent form matched against the pre-feature text.
    """
    bare = _visible(_SESSION, '/x/myproject', tmp_path, monkeypatch)
    assert bare.endswith('opus myproject')
    (tmp_path / f'{_SESSION}.thread').write_text('auth-token\n')
    assert _visible(_SESSION, '/x/myproject', tmp_path, monkeypatch) != bare


def test_concurrent_sessions_each_read_their_own_thread(
        tmp_path, monkeypatch):
    """Verify the record is keyed by session, not by directory or host.

    Mutation: keying the file by cwd, by host, or by one shared name.
    Two sessions in one checkout then overwrite each other, and the
    line names the thread the other session opened.
    Oracle: hand-written records for two session ids in one cache, both
    rendered against the same cwd.
    """
    (tmp_path / 'sess-one.thread').write_text('auth-token\n')
    (tmp_path / 'sess-two.thread').write_text('rate-limits\n')
    one = _visible('sess-one', '/x/myproject', tmp_path, monkeypatch)
    two = _visible('sess-two', '/x/myproject', tmp_path, monkeypatch)
    assert one.endswith('myproject:auth-token')
    assert two.endswith('myproject:rate-limits')


def test_open_records_the_resolved_folder_not_the_typed_prefix(
        tmp_path, monkeypatch):
    """Verify a prefix opens under the full slug the folder is named.

    Mutation: recording the slug argument rather than the resolved
    folder name. The line then shows 'auth', which names no folder and
    matches no /handoff argument the reader could type back.
    Oracle: hand-computed - hq open auth against the folder
    auth-token-refresh must record auth-token-refresh.
    """
    _root(tmp_path, monkeypatch, 'auth-token-refresh')
    assert hq.main(['open', 'auth']) == 0
    record = tmp_path / f'{_SESSION}.thread'
    assert record.read_text().strip() == 'auth-token-refresh'


def test_a_query_against_another_thread_does_not_move_the_session(
        tmp_path, monkeypatch):
    """Verify only adopt, begin, and open put a session on a thread.

    Mutation: recording for every verb that takes a slug - moving the
    call past the _THREAD_VERBS test. Looking up one artifact row on a
    neighboring thread then relabels the whole session.
    Oracle: hand-computed - open auth-token-refresh, then artifacts on
    rate-limits; the record must still name auth-token-refresh.
    """
    folder = _root(tmp_path, monkeypatch, 'auth-token-refresh')
    (folder.parent / 'rate-limits').mkdir(parents=True, exist_ok=True)
    assert hq.main(['open', 'auth-token-refresh']) == 0
    hq.main(['artifacts', 'rate-limits'])
    record = tmp_path / f'{_SESSION}.thread'
    assert record.read_text().strip() == 'auth-token-refresh'


def test_a_write_that_dies_midway_leaves_the_last_whole_slug(
        tmp_path, monkeypatch):
    """Verify a half-written record never reaches the status line.

    Mutation: writing the slug straight to the read path instead of
    staging and renaming. A crash or a full disk then leaves a prefix
    of the new slug, which raises nothing and renders as a real thread.
    Oracle: hand-computed - a write_text that emits three bytes and
    raises; the read path must still hold the whole earlier slug.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    record = tmp_path / f'{_SESSION}.thread'
    record.write_text('auth-token-refresh\n')

    whole_write = pathlib.Path.write_text

    def half_then_die(self, data, **kwargs):
        whole_write(self, data[:3])
        raise OSError('disk full')

    monkeypatch.setattr(pathlib.Path, 'write_text', half_then_die)
    hq._record_thread(_SESSION, 'rate-limits')
    monkeypatch.undo()
    assert record.read_text().strip() == 'auth-token-refresh'
    assert not list(tmp_path.glob(f'{_SESSION}.thread.*'))


def test_done_takes_the_session_off_the_thread_it_finished(
        tmp_path, monkeypatch):
    """Verify a finished thread stops being named on the status line.

    Mutation: leaving the record in place after done, or clearing it
    on finish instead. The line then names a thread nobody is working,
    or drops the slug at the end of every cycle.
    Oracle: hand-computed - open auth-token-refresh, then done on it;
    the read path must be gone, and the line back to the bare
    directory.
    """
    _root(tmp_path, monkeypatch, 'auth-token-refresh')
    assert hq.main(['open', 'auth-token-refresh']) == 0
    assert hq.main(['done', 'auth-token-refresh']) == 0
    assert not (tmp_path / f'{_SESSION}.thread').exists()
    line = _visible(_SESSION, '/x/myproject', tmp_path, monkeypatch)
    assert line.endswith('opus myproject')


def test_done_on_a_neighbor_leaves_the_session_where_it_is(
        tmp_path, monkeypatch):
    """Verify clearing tests the recorded slug, not merely the verb.

    Mutation: unlinking the record whatever thread done names. A
    session filing a stale neighbor then loses the slug for the thread
    it is actually working.
    Oracle: hand-computed - open auth-token-refresh, then done on
    rate-limits; the record must still name auth-token-refresh.
    """
    folder = _root(tmp_path, monkeypatch, 'auth-token-refresh')
    (folder.parent / 'rate-limits').mkdir(parents=True, exist_ok=True)
    assert hq.main(['open', 'auth-token-refresh']) == 0
    assert hq.main(['done', 'rate-limits']) == 0
    record = tmp_path / f'{_SESSION}.thread'
    assert record.read_text().strip() == 'auth-token-refresh'


def test_undo_reopens_without_dropping_the_slug(tmp_path, monkeypatch):
    """Verify --undo is not read as the marking it reverses.

    Mutation: clearing on every done, --undo included. Reopening a
    thread would then take the session off the thread it just put
    back in play.
    Oracle: hand-computed - done then done --undo, both on the open
    thread; the record must survive both.
    """
    _root(tmp_path, monkeypatch, 'auth-token-refresh')
    assert hq.main(['open', 'auth-token-refresh']) == 0
    assert hq.main(['done', 'auth-token-refresh']) == 0
    assert hq.main(['open', 'auth-token-refresh']) == 0
    assert hq.main(['done', 'auth-token-refresh', '--undo']) == 0
    record = tmp_path / f'{_SESSION}.thread'
    assert record.read_text().strip() == 'auth-token-refresh'


def test_a_refused_begin_puts_the_session_on_no_thread(
        tmp_path, monkeypatch):
    """Verify entering is recorded on the exit code, not on the attempt.

    Mutation: recording before dispatch. A begin refused for a lock
    another live session holds would then relabel this session with a
    thread it never entered.
    Oracle: hand-computed - a lock held by another session minutes
    old; begin exits 1 and must leave no record.
    """
    folder = _root(tmp_path, monkeypatch, 'auth-token-refresh')
    (folder / '.hq.lock').write_text(
        f'slug={folder.name}\nsession=other-session\nhost=other-host\n'
        'time=2026-09-09T11:30:00\ncycle=1\n')
    assert hq.main(['begin', 'auth-token-refresh']) == 1
    assert not (tmp_path / f'{_SESSION}.thread').exists()
