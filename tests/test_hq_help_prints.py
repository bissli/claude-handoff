"""Print-behavior tests for the topic banners appended by begin and open.

begin appends HELP_TOPICS['write'] after its work list on success only.
open appends HELP_TOPICS['read'] just before returning when HANDOFF.md
exists and --not-carried is not active.
"""

import contextlib
import io
import pathlib

from bin import hq

_SLUG = 'help-slug'
_SESSION = 'session-hp'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'
# 1h59m before _NOW: below the two-hour takeover threshold.
_YOUNG_LOCK_TIME = '2026-09-09T10:01:00'


def _root(tmp_path, monkeypatch):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    return root


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def test_begin_prints_write_topic_after_worklist(tmp_path, monkeypatch):
    """A successful begin prints the cycle-begun line then the write topic.

    Mutation: the print dropped, moved before the work list so the topic
    precedes the cycle line, or triggered on a refused begin.
    Oracle: the cycle-begun line appears before HELP_TOPICS['write'] in
    stdout and the output ends with the topic followed by a newline.
    """
    _root(tmp_path, monkeypatch)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    cycle_line = f'cycle 1 begun by {_SESSION} on {_HOST}'
    topic = hq.HELP_TOPICS['write']
    assert cycle_line in out
    assert topic in out
    assert out.index(cycle_line) < out.index(topic)
    assert out.endswith(topic + '\n')


def test_refused_begin_prints_no_write_topic(tmp_path, monkeypatch):
    """A begin refused for a foreign young lock prints no write topic.

    Mutation: the print moved outside the success path so a refused
    begin also appends the topic, sending the agent write rules it has
    not earned a cycle for.
    Oracle: the lock-held refusal line is present; HELP_TOPICS['write']
    is absent from stdout.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    (folder / '.hq.lock').write_text(
        f'slug={_SLUG}\nsession=other\nhost=other-host\n'
        f'time={_YOUNG_LOCK_TIME}\ncycle=1\n')
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1
    assert 'lock held by' in out
    assert hq.HELP_TOPICS['write'] not in out


def test_open_clean_folder_prints_only_read_topic(tmp_path, monkeypatch):
    """Open on a finished cycle with no findings prints exactly the read topic.

    Mutation: the print dropped so the agent gets no reading steps after
    open; or it fires on the --not-carried path or when HANDOFF.md is
    absent, sending instructions that do not apply.
    Oracle: the whole stdout equals HELP_TOPICS['read'] + newline after
    a bare begin+finish with no stamps.
    """
    _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _run(['finish', _SLUG, '--log', 'c1'])
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    assert out == hq.HELP_TOPICS['read'] + '\n'


def test_open_with_finding_prints_finding_before_read_topic(tmp_path, monkeypatch):
    """Open prints its findings, the late ones included, then the read topic.

    Mutation: the topic printed ahead of the later finding classes (moved
    sha, git drift, stale paths), so the agent reads the steps before the
    findings; or the topic omitted when a finding is present.
    Oracle: a moved sha, which open reports after the HANDOFF.md checks,
    appears before HELP_TOPICS['read'], and the output ends with the topic.
    """
    folder = _root(tmp_path, monkeypatch) / '.handoff' / _SLUG
    _run(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## 1. Scope\n\nOne.\n')
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'c1'])[0] == 0
    (folder / 'SPEC.md').write_text('# Spec\n\n## 1. Scope\n\nchanged\n')
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    topic = hq.HELP_TOPICS['read']
    assert out.index('sha moved since stamp: SPEC.md') < out.index(topic)
    assert out.endswith(topic + '\n')


def test_open_not_carried_prints_no_read_topic(tmp_path, monkeypatch):
    """Open --not-carried prints no read topic.

    Mutation: the topic print moved outside the not-carried guard so
    every open call appends it regardless of mode, mixing read steps
    into a not-carried listing.
    Oracle: HELP_TOPICS['read'] absent from stdout on a --not-carried run
    that exits 0 with the 'no finished cycle' advisory.
    """
    _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    rc, out, _ = _run(['open', _SLUG, '--not-carried'])
    assert rc == 0
    assert 'not carried' in out
    assert hq.HELP_TOPICS['read'] not in out


def test_open_no_handoff_prints_no_read_topic(tmp_path, monkeypatch):
    """Open on a folder with no HANDOFF.md prints no read topic.

    Mutation: the topic print moved before the HANDOFF.md existence check
    so it fires even when no session is in progress, misleading the agent
    into reading a file that does not exist.
    Oracle: HELP_TOPICS['read'] absent from stdout when the folder exists
    but HANDOFF.md does not.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    assert hq.HELP_TOPICS['read'] not in out
