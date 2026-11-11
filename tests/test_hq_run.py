"""Tests for the hq run and hq loop verbs and bin/hq-completion.bash."""

import contextlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any

import pytest

from bin import hq

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPLETION = ROOT / 'bin' / 'hq-completion.bash'


class _Execed(Exception):
    """Stands in for the process image execvp would replace.
    """


def _root_with_threads(tmp_path: pathlib.Path, monkeypatch: Any) -> pathlib.Path:
    """Root with open threads auth-refresh and billing-ledger, done auth-legacy.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary path; the root is its ``root`` child.
    monkeypatch : Any
        Active pytest monkeypatch fixture; HQ_ROOT is set to the root
        and HQ_CLAUDE_CMD is unset.

    Returns
    -------
    pathlib.Path
        The root.
    """
    root = tmp_path / 'root'
    for slug in ('auth-refresh', 'billing-ledger', 'auth-legacy'):
        (root / '.handoff' / slug).mkdir(parents=True)
    (root / '.handoff' / 'auth-legacy' / '.hq.done').write_text('')
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.delenv('HQ_CLAUDE_CMD', raising=False)
    return root


def test_run_execs_claude_on_the_resolved_slug_at_the_root(
        tmp_path, monkeypatch):
    """Verify run execs claude at the root on the exact slug.

    Mutation: the chdir to the root dropped, or the slug left out of the
    /handoff argument.
    Oracle: a stub execvp that records its argv and the cwd it ran in.
    """
    root = _root_with_threads(tmp_path, monkeypatch)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    calls = []

    def fake_execvp(file, args):
        calls.append((file, args, os.getcwd()))
        raise _Execed

    monkeypatch.setattr(hq.os, 'execvp', fake_execvp)
    with pytest.raises(_Execed):
        hq.main(['run', 'auth-refresh'])
    assert calls == [
        ('claude', ['claude', '/handoff auth-refresh'], str(root))]


def test_run_execs_hq_claude_cmd_with_the_prompt_last(tmp_path, monkeypatch):
    """Verify HQ_CLAUDE_CMD replaces claude, split by shell rules.

    Mutation: the value passed whole as one argv entry, split on
    whitespace so the quoted script breaks apart, or the prompt put
    ahead of the command's own arguments.
    Oracle: a stub execvp that records its argv; the expected argv is
    the value split by hand.
    """
    _root_with_threads(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_CLAUDE_CMD', """bash -ic 'claude "$@"' bash""")
    calls = []

    def fake_execvp(file, args):
        calls.append((file, args))
        raise _Execed

    monkeypatch.setattr(hq.os, 'execvp', fake_execvp)
    with pytest.raises(_Execed):
        hq.main(['run', 'auth-refresh'])
    assert calls == [
        ('bash',
         ['bash', '-ic', 'claude "$@"', 'bash', '/handoff auth-refresh'])]


def test_run_with_unsplittable_hq_claude_cmd_exits_2(
        tmp_path, monkeypatch, capsys):
    """Verify an unclosed quote in HQ_CLAUDE_CMD prints one line, exits 2.

    Mutation: the ValueError handler dropped, so shlex's traceback
    escapes main.
    Oracle: shlex's own message for an unclosed quote.
    """
    _root_with_threads(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_CLAUDE_CMD', "claude 'oops")
    assert hq.main(['run', 'auth-refresh']) == 2
    assert capsys.readouterr().out == (
        'hq run: HQ_CLAUDE_CMD: No closing quotation\n')


def test_run_without_claude_on_path_exits_1(tmp_path, monkeypatch, capsys):
    """Verify run names the missing claude rather than a path it tried.

    Mutation: the FileNotFoundError handler dropped, so the generic
    'hq: cannot access <path>' line prints instead.
    Oracle: a stub execvp that raises as execvp does on a PATH miss.
    """
    _root_with_threads(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    def fake_execvp(file, args):
        raise FileNotFoundError(2, 'No such file or directory', '/x/claude')

    monkeypatch.setattr(hq.os, 'execvp', fake_execvp)
    assert hq.main(['run', 'billing-ledger']) == 1
    assert capsys.readouterr().out == 'hq run: no claude on PATH\n'


def _complete(words: list[str], cwd: pathlib.Path) -> list[str]:
    """Candidates the completion script offers for words, sorted.

    Parameters
    ----------
    words : list[str]
        The command line so far, ``hq`` first; the last word is the one
        being completed and may be empty.
    cwd : pathlib.Path
        Directory bash runs in.

    Returns
    -------
    list[str]
        COMPREPLY after ``_hq`` runs, sorted.
    """
    words_text = ' '.join(f"'{word}'" for word in words)
    probe = (
        f'source {COMPLETION}\n'
        f'COMP_WORDS=({words_text}); COMP_CWORD={len(words) - 1}\n'
        '_hq; printf "%s\\n" "${COMPREPLY[@]}"\n')
    out = subprocess.run(
        ['bash', '-c', probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd).stdout
    return sorted(out.split())


def test_run_refuses_a_prefix(tmp_path, monkeypatch, capsys):
    """Verify run takes only the exact slug and never execs on a prefix.

    Mutation: run resolving through the shared prefix match, so auth-r
    launches auth-refresh.
    Oracle: a stub execvp that records any call; auth-r is a unique
    prefix of auth-refresh, which the other verbs would accept.
    """
    _root_with_threads(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(hq.os, 'execvp', lambda *args: calls.append(args))
    assert hq.main(['run', 'auth-r']) == 2
    assert calls == []
    assert capsys.readouterr().out.startswith(
        "hq run: no folder named 'auth-r' under ")


@pytest.mark.parametrize(('words', 'expected'), [
    (['hq', 'ru'], ['run']),
    (['hq', 'run', 'au'], ['auth-refresh']),
    (['hq', 'read', ''], ['auth-refresh', 'billing-ledger']),
    (['hq', 'list', ''], []),
    (['hq', 'help', ''], []),
    (['hq', 'run', 'auth-refresh', ''], []),
    ])
def test_completion_offers_verbs_then_open_slugs(
        tmp_path, monkeypatch, words, expected):
    """Verify the completion script offers verbs, then open slugs only.

    Mutation: the done-marker filter dropped, the .handoff dirname wrong,
    or slugs offered after list or help, or past the slug position.
    Oracle: the hand-built root - two open threads and one marked done.
    """
    _root_with_threads(tmp_path, monkeypatch)
    assert _complete(words, tmp_path) == expected


def test_completion_verbs_match_the_parser():
    """Verify the completion script offers exactly the parser's verbs.

    Mutation: a verb added to or renamed in _build_parser with the
    completion list left as it was.
    Oracle: the argparse subcommand table.
    """
    parser = hq._build_parser()
    verbs = next(
        action for action in parser._subparsers._group_actions
        ).choices
    assert _complete(['hq', ''], COMPLETION.parent) == sorted(verbs)


def _record_execv(monkeypatch: Any) -> list[tuple[str, list[str], str]]:
    """Stub hq's os.execv to record each call and stop the verb.

    Parameters
    ----------
    monkeypatch : Any
        Active pytest monkeypatch fixture.

    Returns
    -------
    list[tuple[str, list[str], str]]
        File, argv and cwd of each call, appended as the calls happen.
    """
    calls = []

    def fake_execv(file, args):
        calls.append((file, args, os.getcwd()))
        raise _Execed

    monkeypatch.setattr(hq.os, 'execv', fake_execv)
    return calls


def test_loop_execs_launch_and_stop_at_the_root(tmp_path, monkeypatch):
    """Verify loop names the script's subcommand and runs it at the root.

    Mutation: the chdir to the root dropped, the --max-cycles default
    lost, stop sent as launch, or claude left off the launch argv.
    Oracle: a stub execv that records its argv and the cwd it ran in.
    """
    root = _root_with_threads(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    script = str(ROOT / 'bin' / 'hq-loop')
    calls = _record_execv(monkeypatch)
    for argv in (
            ['loop', 'auth-refresh'],
            ['loop', '--max-cycles', '3', 'auth-refresh'],
            ['loop', 'stop', 'billing-ledger'],
            ['loop', 'stop']):
        with pytest.raises(_Execed):
            hq.main(argv)
    assert calls == [
        (script, [script, 'launch', '10', 'auth-refresh', 'claude'], str(root)),
        (script, [script, 'launch', '3', 'auth-refresh', 'claude'], str(root)),
        (script, [script, 'stop', 'billing-ledger'], str(root)),
        (script, [script, 'launch', '10', 'stop', 'claude'], str(root))]


def test_loop_launches_hq_claude_cmd_split_by_shell_rules(
        tmp_path, monkeypatch):
    """Verify HQ_CLAUDE_CMD reaches the script as separate words.

    Mutation: the value passed whole as one word, or split on whitespace
    so the quoted script breaks apart.
    Oracle: a stub execv that records its argv; the expected words are
    the value split by hand.
    """
    _root_with_threads(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_CLAUDE_CMD', """bash -ic 'claude "$@"' bash""")
    calls = _record_execv(monkeypatch)
    with pytest.raises(_Execed):
        hq.main(['loop', 'auth-refresh'])
    assert calls[0][1][1:] == [
        'launch', '10', 'auth-refresh', 'bash', '-ic', 'claude "$@"', 'bash']


@pytest.mark.parametrize(('argv', 'claude_cmd', 'expected'), [
    (['loop', 'auth-refresh', 'billing-ledger'], None,
     'hq loop: takes <slug> or stop <slug>\n'),
    (['loop', 'stop', 'auth-refresh', 'billing-ledger'], None,
     'hq loop: takes <slug> or stop <slug>\n'),
    (['loop', 'auth-refresh'], "claude 'oops",
     'hq loop: HQ_CLAUDE_CMD: No closing quotation\n'),
    ])
def test_loop_refuses_extra_words_and_an_unsplittable_command(
        tmp_path, monkeypatch, capsys, argv, claude_cmd, expected):
    """Verify loop exits 2 with one line and never execs the script.

    Mutation: extra words passed through so the script keeps only one
    slug, or the None from the HQ_CLAUDE_CMD split left unchecked.
    Oracle: a stub execv that records any call; shlex's own message for
    an unclosed quote.
    """
    _root_with_threads(tmp_path, monkeypatch)
    if claude_cmd is not None:
        monkeypatch.setenv('HQ_CLAUDE_CMD', claude_cmd)
    calls = _record_execv(monkeypatch)
    assert hq.main(argv) == 2
    assert calls == []
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize(('words', 'expected'), [
    (['hq', 'loop', ''], ['auth-refresh', 'billing-ledger', 'stop']),
    (['hq', 'loop', 'stop', 'b'], ['billing-ledger']),
    (['hq', 'loop', 'auth-refresh', ''], []),
    (['hq', 'loop', '--max-cycles', '3', ''],
     ['auth-refresh', 'billing-ledger', 'stop']),
    (['hq', 'loop', '--max-cycles', ''], []),
    (['hq', 'loop', 'stop', 'auth-refresh', ''], []),
    ])
def test_completion_offers_stop_then_slugs_after_loop(
        tmp_path, monkeypatch, words, expected):
    """Verify loop completes stop or a slug, then a slug after stop only.

    Mutation: stop left out of the loop candidates, --max-cycles and its
    value counted as words, slugs offered for the flag's value, or slugs
    offered after a slug.
    Oracle: the hand-built root - two open threads and one marked done.
    """
    _root_with_threads(tmp_path, monkeypatch)
    assert _complete(words, tmp_path) == expected


def _wait_for(predicate: Any, limit: float = 20.0) -> bool:
    """Poll predicate every 0.2 s until it is true or limit seconds pass.
    """
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def _manifest_row(cycle, log='-', stall='0', window=None):
    """One manifest line: cycle, log at field 12, stall and window at 15, 16."""
    fields = [str(cycle), '2026-01-01'] + ['-'] * 9 + [log, '-', '-', stall]
    if window is not None:
        fields.append(str(window))
    return '\t'.join(fields)


READ = '/handoff read auth-refresh'
WRITE = '/handoff write auth-refresh'
# A Stop's decision runs DEBOUNCE + 1 s after it, plus the typing pause.
QUIET = 4.5


class _Drive:
    """A driver run against a stub tmux, its calls and output read back.
    """

    def __init__(self, tmp_path, max_cycles, rows=(), launcher=('sleep', '120')):
        bin_dir = tmp_path / 'stub'
        bin_dir.mkdir()
        self.calls_path = tmp_path / 'tmux.log'
        self.driver_dir = tmp_path / 'driver'
        self.driver_dir.mkdir()
        pid_path = tmp_path / 'launcher.pid'
        # At /exit the stub records the end file and sends the launcher
        # TERM, as claude exiting would end it.
        (bin_dir / 'tmux').write_text(
            '#!/bin/sh\n'
            f'IFS="|"; echo "$*" >> {self.calls_path}\n'
            'case "$*" in *"|-l|--|/exit")\n'
            f'    [ -e {self.driver_dir}/end ] && echo "end-file|$(cat {self.driver_dir}/end)" >> {self.calls_path}\n'
            f'    kill "$(cat {pid_path})" 2>/dev/null ;;\n'
            'esac\n'
            f'case "$*" in send-keys*-l*) [ -e {tmp_path}/send-fails ] && exit 1 ;; esac\n'
            'exit 0\n')
        (bin_dir / 'tmux').chmod(0o755)
        self.folder = tmp_path / '.handoff' / 'auth-refresh'
        (self.folder / 'cycles').mkdir(parents=True)
        self.manifest = self.folder / 'cycles' / 'manifest.tsv'
        if rows:
            self.write_manifest(rows)
        self.events = self.driver_dir / 'events'
        self.events.write_text('')
        self.budget_dir = tmp_path / 'home' / '.claude' / 'cache' / 'claude-handoff'
        self.budget_dir.mkdir(parents=True)
        self.output = tmp_path / 'driver.out'
        self.errors = tmp_path / 'driver.err'
        # A launcher started in the background of a shell that exits is
        # reaped by init, so kill -0 fails once it is gone.
        self.launcher_pid = int(subprocess.check_output(
            ['sh', '-c', '"$@" >/dev/null 2>&1 & echo $!', 'sh', *launcher]))
        pid_path.write_text(str(self.launcher_pid))
        with self.output.open('w') as out, self.errors.open('w') as err:
            self.driver = subprocess.Popen(
                [str(ROOT / 'bin' / 'hq-loop'), 'drive', str(self.driver_dir),
                 'auth-refresh', str(max_cycles), '%0', str(self.launcher_pid)],
                cwd=tmp_path,
                env={
                    **os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                    'HOME': str(tmp_path / 'home'), 'TMUX_PANE': '%9'},
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True)

    def write_manifest(self, rows):
        self.manifest.write_text('cycle\twritten\n' + ''.join(r + '\n' for r in rows))

    def set_band(self, sid, band):
        (self.budget_dir / f'{sid}.json').write_text(json.dumps({'band': band}))

    def emit(self, line):
        with self.events.open('a') as stream:
            stream.write(line + '\n')

    def start(self, sid='S1'):
        """Emit SessionStart startup and wait for the read it types.
        """
        self.emit(f'SessionStart startup {sid} 0 0')
        assert _wait_for(lambda: len(self.typed()) == 1)

    def stop(self, sid='S1', tasks=0, endsq=0, last='Waiting.'):
        """Write the hook's last file, then emit a Stop.
        """
        (self.driver_dir / 'last').write_text(last + '\n')
        self.emit(f'Stop - {sid} {tasks} {endsq}')

    def said(self):
        text = self.output.read_text() if self.output.exists() else ''
        return [re.sub(r'\x1b\[[0-9;]*m', '', line)[6:] for line in text.splitlines()]

    def calls(self):
        return self.calls_path.read_text().splitlines() if self.calls_path.exists() else []

    def typed(self):
        return [
            line.split('|', 5)[5] for line in self.calls()
            if line.startswith('send-keys|-t|%0|-l')]

    def rang(self, text):
        """How many bells carry text.
        """
        return sum(
            text in line for line in self.calls()
            if line.startswith('display-message'))

    def close(self):
        subprocess.run(['pkill', '-KILL', '-P', str(self.launcher_pid)], check=False)
        with contextlib.suppress(ProcessLookupError):
            os.kill(self.launcher_pid, 9)
        # The driver can exit with its whole group before cleanup.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.driver.pid, 9)
        self.driver.wait()


def test_foreign_sid_is_dropped(tmp_path):
    """Verify an event under another session id is ignored, and the id a
    SessionStart carries replaces the old one.

    Mutation: the drop test removed, or the sid kept across a clear.
    Oracle: a foreign Stop types nothing; after a SessionStart clear under
    a new sid, that sid's Stop types the nudge.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('PostToolUse main S1 0 0')
        drive.stop(sid='S2')
        time.sleep(QUIET)
        assert drive.typed() == [READ]
        drive.emit('SessionStart clear S3 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 2)
        drive.emit('PostToolUse main S3 0 0')
        drive.stop(sid='S3')
        assert _wait_for(lambda: len(drive.typed()) == 3)
        assert drive.typed()[2] == _loop_text('NUDGE_TEXT')
    finally:
        drive.close()


def test_no_line_is_typed_inside_a_turn(tmp_path):
    """Verify no line is typed into a turn a prompt or the read started.

    Mutation: the turn gate dropped from type_line, so a decision still
    pending when the read is typed types into the read's turn.
    Oracle: a prompt within DEBOUNCE of a Stop leaves nothing typed; a
    Stop at band 0 right after a clear types the read alone.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.set_band('S2', 0)
        drive.start()
        drive.emit('PostToolUse main S1 0 0')
        drive.stop()
        drive.emit('UserPromptSubmit - S1 0 0')
        time.sleep(QUIET)
        assert drive.typed() == [READ]
        drive.emit('SessionStart clear S2 0 0')
        drive.stop(sid='S2')
        time.sleep(QUIET)
        assert drive.typed() == [READ, READ]
    finally:
        drive.close()


def test_a_dialog_holds_typing_until_work_resumes(tmp_path):
    """Verify a dialog notice rings and blocks typing until a main tool
    call.

    Mutation: the dialog gate dropped, or never cleared.
    Oracle: a dialog, then a Stop: a bell, no line; a main tool call,
    then a Stop: a line.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('PostToolUse main S1 0 0')
        drive.emit('Notification permission_prompt S1 0 0')
        assert _wait_for(lambda: drive.rang('claude is waiting for you (permission_prompt)') == 1)
        drive.stop()
        time.sleep(QUIET)
        assert drive.typed() == [READ]
        drive.emit('PostToolUse main S1 0 0')
        drive.stop()
        assert _wait_for(lambda: len(drive.typed()) == 2)
        assert drive.typed()[1] == _loop_text('NUDGE_TEXT')
    finally:
        drive.close()


def test_idle_prompt_is_no_dialog(tmp_path):
    """Verify an idle_prompt notice neither rings nor holds typing.

    Mutation: idle_prompt read as a dialog.
    Oracle: no bell, and the next Stop types the nudge.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('PostToolUse main S1 0 0')
        drive.emit('Notification idle_prompt S1 0 0')
        drive.stop()
        assert _wait_for(lambda: len(drive.typed()) == 2)
        assert drive.rang('claude is waiting for you') == 0
    finally:
        drive.close()


@pytest.mark.parametrize('after', ['stop', 'tick'])
def test_done_ends_at_a_stop_and_at_a_rest_tick(tmp_path, after):
    """Verify .hq.done ends the loop at a Stop and at a rest tick.

    Mutation: Stop row 1 or tick row 1 dropped.
    Oracle: .hq.done with a Stop that ends on a question: END and no
    question bell; with no event but a StopFailure that leaves the
    session resting: END.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        if after == 'stop':
            (drive.folder / '.hq.done').write_text('')
            drive.stop(endsq=1, last='Which key?')
        else:
            drive.emit('StopFailure rate_limit S1 0 0')
            time.sleep(1)
            (drive.folder / '.hq.done').write_text('')
        drive.driver.wait(timeout=20)
        assert drive.said()[-1] == '[1/10] auth-refresh is marked done'
        assert drive.rang('asks:') == 0
    finally:
        drive.close()


def test_question_parks_before_the_lock_retry(tmp_path):
    """Verify a Stop that ends on a question rings the question and types
    no retry, even under the session's own lock.

    Mutation: rows 2 and 3 swapped.
    Oracle: endsq under its own lock rings asks: with the last line.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.folder / '.hq.lock').write_text('slug=auth-refresh\nsession=S1\n')
        drive.stop(endsq=1, last='Which key should I rotate?')
        assert _wait_for(lambda: drive.rang('auth-refresh asks: Which key should I rotate?') == 1)
        time.sleep(1)
        assert drive.typed() == [READ]
    finally:
        drive.close()


def test_own_lock_retries_twice_then_parks(tmp_path):
    """Verify a Stop under the session's own lock types RETRY_TEXT twice,
    then rings the finish bell.

    Mutation: RETRY_CAP off by one.
    Oracle: two retries typed, then the finish bell with the count 2.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.folder / '.hq.lock').write_text('slug=auth-refresh\nsession=S1\n')
        for count in (2, 3):
            drive.stop()
            assert _wait_for(lambda: len(drive.typed()) == count)
        drive.stop()
        assert _wait_for(lambda: drive.rang('hq finish has not passed after 2 retries') == 1)
        assert drive.typed() == [READ] + [_loop_text('RETRY_TEXT')] * 2
    finally:
        drive.close()


def test_rest_tick_runs_beside_agent_events(tmp_path):
    """Verify the rest tick keeps its clock while a background agent works.

    Mutation: the tick keyed to a quiet read timeout, so agent events a
    second apart hold off .hq.done.
    Oracle: a session parked beside a task, then .hq.done and an agent
    tool call every second: END within three ticks.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.stop(tasks=1)
        assert _wait_for(lambda: '[1/10] auth-refresh waits: Waiting.' in drive.said())
        (drive.folder / '.hq.done').write_text('')
        for _ in range(15):
            if drive.driver.poll() is not None:
                break
            drive.emit('PostToolUse a1 S1 1 0')
            time.sleep(1)
        assert drive.driver.poll() is not None
        assert drive.said()[-1] == '[1/10] auth-refresh is marked done'
    finally:
        drive.close()


def test_stop_file_at_a_tick_keeps_the_question(tmp_path):
    """Verify the Stop file at a tick never writes over an open question.

    Mutation: the tick types the write without the Stop decisions, whose
    question row comes before the Stop file.
    Oracle: a Stop that ends on a question, then the Stop file: two
    ticks type nothing.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.stop(endsq=1, last='Which key?')
        assert _wait_for(lambda: drive.rang('auth-refresh asks: Which key?') == 1)
        (drive.driver_dir / 'stop').write_text('')
        time.sleep(11)
        assert drive.typed() == [READ]
        assert drive.rang('asks:') == 1
    finally:
        drive.close()


def test_stop_file_at_a_tick_ends_after_a_written_cycle(tmp_path):
    """Verify the Stop file at a tick ends a loop whose cycle is written.

    Mutation: the tick types the write without the Stop decisions, whose
    cycle end comes before the Stop file.
    Oracle: a cycle written beside a task, then the Stop file: END
    stopped after cycle 1, and no write typed.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.write_manifest([_manifest_row(1)])
        drive.stop(tasks=1)
        time.sleep(QUIET)
        (drive.driver_dir / 'stop').write_text('')
        drive.driver.wait(timeout=20)
        assert drive.said()[-1] == '[1/10] stopped after cycle 1 as requested'
        assert drive.typed() == [READ, '/exit']
    finally:
        drive.close()


def test_a_session_start_ends_the_dialog(tmp_path):
    """Verify a dialog of the old session does not hold the new one's read.

    Mutation: dialog kept across a SessionStart, so the read after a
    clear is never typed.
    Oracle: a dialog notice, a Stop, then a clear: the read is typed.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('Notification permission_prompt S1 0 0')
        drive.stop()
        time.sleep(QUIET)
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: drive.typed() == [READ, READ])
    finally:
        drive.close()


def test_foreign_lock_parks_and_the_tick_rechecks(tmp_path):
    """Verify a foreign lock parks the loop silent, and a rest tick runs
    the Stop decisions again once the lock is gone.

    Mutation: tick row 3 dropped.
    Oracle: lock gone after a tool call and a Stop: the nudge is typed.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.folder / '.hq.lock').write_text('slug=auth-refresh\nsession=S2\n')
        drive.emit('PostToolUse main S1 0 0')
        drive.stop()
        assert _wait_for(lambda: '[1/10] waits on the lock of S2' in drive.said())
        time.sleep(1)
        assert drive.typed() == [READ]
        (drive.folder / '.hq.lock').unlink()
        assert _wait_for(lambda: len(drive.typed()) == 2, limit=12)
        assert drive.typed()[1] == _loop_text('NUDGE_TEXT')
    finally:
        drive.close()


def test_stop_file_at_rest_types_one_write(tmp_path):
    """Verify a resting session with the Stop file gets one write.

    Mutation: stop_sent dropped from tick row 2.
    Oracle: one write over two ticks after the first.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.driver_dir / 'stop').write_text('')
        drive.emit('StopFailure rate_limit S1 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 2, limit=12)
        drive.emit('StopFailure rate_limit S1 0 0')
        time.sleep(11)
        assert drive.typed() == [READ, WRITE]
    finally:
        drive.close()


def test_stop_ends_after_its_write(tmp_path):
    """Verify a written cycle with the Stop file ends the loop with no
    clear.

    Mutation: the cycle end's Stop-file test dropped.
    Oracle: a written cycle with the Stop file: END, no /clear.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.driver_dir / 'stop').write_text('')
        drive.write_manifest([_manifest_row(1)])
        drive.stop()
        drive.driver.wait(timeout=20)
        assert drive.said()[-1] == '[1/10] stopped after cycle 1 as requested'
        assert drive.typed() == [READ, '/exit']
    finally:
        drive.close()


def test_budget_band_writes_retry_then_park(tmp_path):
    """Verify a band types the write, retries it twice, then rings, and
    a higher band types again.

    Mutation: sent left unset, or RETRY_CAP off.
    Oracle: band 0 over four Stops: three writes, then the write bell;
    band 1 types one more, and a second Stop at band 1 types nothing.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.set_band('S1', 0)
        drive.start()
        for count in (2, 3, 4):
            drive.stop()
            assert _wait_for(lambda: len(drive.typed()) == count)
        drive.stop()
        assert _wait_for(lambda: drive.rang('a write was typed 3 times and cycle 1 is not written') == 1)
        assert drive.typed() == [READ] + [WRITE] * 3
        drive.set_band('S1', 1)
        drive.stop()
        assert _wait_for(lambda: len(drive.typed()) == 5)
        drive.stop()
        time.sleep(QUIET)
        assert len(drive.typed()) == 5
    finally:
        drive.close()


def test_a_ruling_types_the_write(tmp_path):
    """Verify a hold with a ruling types the write at a Stop.

    Mutation: row 9 ignoring ruling.
    Oracle: hold and ruling: a write typed.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.driver_dir / 'hold').write_text('stall: held at 4.\n')
        (drive.driver_dir / 'ruling').write_text('split it\n')
        drive.stop()
        assert _wait_for(lambda: len(drive.typed()) == 2)
        assert drive.typed()[1] == WRITE
    finally:
        drive.close()


def test_hold_alone_says_once_per_park(tmp_path):
    """Verify a hold with no ruling says its line once across a Stop and
    two rest ticks.

    Mutation: the park said at each tick.
    Oracle: the hold line printed once, and nothing typed.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.driver_dir / 'hold').write_text('stall: held at 4.\n')
        drive.stop()
        assert _wait_for(lambda: '[1/10] stall: held at 4.' in drive.said())
        time.sleep(11)
        assert drive.said().count('[1/10] stall: held at 4.') == 1
        assert drive.typed() == [READ]
    finally:
        drive.close()


def test_a_working_turn_gets_a_nudge(tmp_path):
    """Verify a Stop after tool calls types the nudge, and a Stop after
    none parks as waiting.

    Mutation: row 10 keyed to anything but tools.
    Oracle: tools 1 nudges; the nudge's own turn with tools 0 parks waits.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('PostToolUse main S1 0 0')
        drive.stop()
        assert _wait_for(lambda: len(drive.typed()) == 2)
        assert drive.typed()[1] == _loop_text('NUDGE_TEXT')
        drive.emit('UserPromptSubmit - S1 0 0')
        drive.stop(last='Waiting on the build.')
        assert _wait_for(lambda: '[1/10] auth-refresh waits: Waiting on the build.' in drive.said())
        assert len(drive.typed()) == 2
    finally:
        drive.close()


def test_cycle_end_rests_beside_a_task(tmp_path):
    """Verify a written cycle with a background task running does not
    clear, and clears at a Stop with none.

    Mutation: the tasks test dropped.
    Oracle: tasks 1: no /clear; tasks 0 after it: /clear.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.write_manifest([_manifest_row(1)])
        drive.stop(tasks=1)
        time.sleep(QUIET)
        assert drive.typed() == [READ]
        drive.stop()
        assert _wait_for(lambda: drive.typed() == [READ, '/clear'])
    finally:
        drive.close()


def test_a_typed_clear_moves_the_base(tmp_path):
    """Verify a clear the user types counts the written cycle as cleared.

    Mutation: base set only at the driver's own clear, so the next Stop
    sees the cycle as written again and clears the session that just
    read the handoff back.
    Oracle: a cycle written beside a task, then a typed clear: the next
    Stop types no /clear.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.write_manifest([_manifest_row(1)])
        drive.stop(tasks=1)
        time.sleep(QUIET)
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: drive.typed() == [READ, READ])
        drive.stop(sid='S2')
        time.sleep(QUIET)
        assert drive.typed() == [READ, READ]
    finally:
        drive.close()


def test_clear_resets_the_cycle_counters(tmp_path):
    """Verify the clear resets sent, retries, compacts and agent_calls.

    Mutation: one counter left out of the reset.
    Oracle: after the clear, three writes then the write bell (sent and
    retries), no compact bell on the next compact, and no agent bell on
    the call that would reach AGENT_CAP.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.set_band('S1', 0)
        drive.set_band('S2', 0)
        drive.start()
        with drive.events.open('a') as stream:
            stream.write('PostToolUse a1 S1 0 0\n' * 499)
        drive.emit('SessionStart compact S1 0 0')
        for count in (2, 3, 4):
            drive.stop()
            assert _wait_for(lambda: len(drive.typed()) == count)
        drive.write_manifest([_manifest_row(1)])
        drive.stop()
        assert _wait_for(lambda: drive.typed()[-1] == '/clear')
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 6)
        drive.emit('PostToolUse a1 S2 0 0')
        drive.emit('SessionStart compact S2 0 0')
        for count in (7, 8, 9):
            drive.stop(sid='S2')
            assert _wait_for(lambda: len(drive.typed()) == count)
        drive.stop(sid='S2')
        assert _wait_for(lambda: drive.rang('a write was typed 3 times and cycle 2 is not written') == 1)
        assert drive.typed()[6:] == [WRITE] * 3
        assert drive.rang('agents made') == 0
        assert drive.rang('compacted twice') == 0
    finally:
        drive.close()


def test_max_cycles_counts_from_launch(tmp_path):
    """Verify --max-cycles counts the cycles written since launch.

    Mutation: the count reset at a clear.
    Oracle: launch at c5, max 2: the clear at c6, END at c7.
    """
    drive = _Drive(tmp_path, 2, rows=[_manifest_row(5)])
    try:
        drive.start()
        drive.write_manifest([_manifest_row(5), _manifest_row(6)])
        drive.stop()
        assert _wait_for(lambda: drive.typed() == [READ, '/clear'])
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 3)
        drive.write_manifest([_manifest_row(n) for n in (5, 6, 7)])
        drive.stop(sid='S2')
        drive.driver.wait(timeout=20)
        assert drive.said()[-1] == '[2/2] reached --max-cycles 2 at cycle 7'
        assert drive.typed() == [READ, '/clear', READ, '/exit']
    finally:
        drive.close()


def test_second_compact_rings_once(tmp_path):
    """Verify the second compaction in a cycle rings, and no other does.

    Mutation: the bell at 1, or ringing again at 3.
    Oracle: compacts 1 silent, 2 rings, 3 silent.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('SessionStart compact S1 0 0')
        time.sleep(1)
        assert drive.rang('compacted twice') == 0
        drive.emit('SessionStart compact S1 0 0')
        assert _wait_for(lambda: drive.rang('compacted twice in cycle 1 with no write') == 1)
        drive.emit('SessionStart compact S1 0 0')
        time.sleep(1)
        assert drive.rang('compacted twice') == 1
    finally:
        drive.close()


def test_agent_cap_rings_once(tmp_path):
    """Verify agent tool calls ring when they reach AGENT_CAP, and once.

    Mutation: > for >=, or ringing past the cap.
    Oracle: AGENT_CAP calls ring once; five more ring nothing.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        with drive.events.open('a') as stream:
            stream.write('PostToolUse a1 S1 0 0\n' * 499)
        time.sleep(1)
        assert drive.rang('agents made') == 0
        with drive.events.open('a') as stream:
            stream.write('PostToolUse a1 S1 0 0\n')
        assert _wait_for(lambda: drive.rang('agents made 500 tool calls in cycle 1 with no write') == 1)
        with drive.events.open('a') as stream:
            stream.write('PostToolUse a1 S1 0 0\n' * 5)
        time.sleep(1)
        assert drive.rang('agents made') == 1
    finally:
        drive.close()


def test_stop_failure_parks_with_the_error(tmp_path):
    """Verify a StopFailure rings the error and types nothing.

    Mutation: StopFailure run through the Stop rows.
    Oracle: an error bell, and no nudge after a tool call that a Stop
    would have drawn.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('PostToolUse main S1 0 0')
        drive.emit('StopFailure rate_limit S1 0 0')
        assert _wait_for(lambda: drive.rang('claude stopped on an API error: rate_limit') == 1)
        time.sleep(QUIET)
        assert drive.typed() == [READ]
    finally:
        drive.close()


def test_session_starts_type_the_read(tmp_path):
    """Verify startup, clear and resume each type a read, even when a
    prompt follows inside DEBOUNCE.

    Mutation: resume or clear left out, or the read held back by a
    prompt.
    Oracle: three SessionStarts type three reads; a clear followed at
    once by a prompt still types its read.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 2)
        drive.emit('SessionStart resume S3 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 3)
        drive.emit('SessionStart clear S4 0 0')
        drive.emit('UserPromptSubmit - S4 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 4)
        assert drive.typed() == [READ] * 4
    finally:
        drive.close()


def test_a_newer_hold_rings_again(tmp_path):
    """Verify a hold rings once, and rings again when rewritten with the
    same text under a newer mtime.

    Mutation: the bell keyed to the text.
    Oracle: same text, newer mtime rings again.
    """
    drive = _Drive(tmp_path, 10)
    hold = drive.driver_dir / 'hold'
    try:
        hold.write_text('stall: held at 4.\n')
        drive.start()
        assert _wait_for(lambda: drive.rang('auth-refresh holds: stall: held at 4.') == 1)
        drive.emit('StopFailure rate_limit S1 0 0')
        time.sleep(6)
        assert drive.rang('holds:') == 1
        later = time.time() + 30
        hold.write_text('stall: held at 4.\n')
        os.utime(hold, (later, later))
        assert _wait_for(lambda: drive.rang('holds:') == 2, limit=12)
    finally:
        drive.close()


def test_drive_retries_a_refused_finish_under_its_own_lock(tmp_path):
    """Verify the driver types a retry at a Stop under its own session's
    lock, RETRY_CAP times, and waits silently on a foreign lock.

    Mutation: the session check dropped, so a foreign lock gets the retry;
    the cap dropped, so a third retry is typed; or the own-lock row
    removed, so a refused finish leaves the pane idle.
    Oracle: a stub tmux that records each call - the lines typed after
    one foreign-lock Stop and three own-lock Stops, and the bell.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.folder / '.hq.lock').write_text('slug=auth-refresh\nsession=S2\n')
        drive.stop()
        time.sleep(QUIET)
        assert len(drive.typed()) == 1
        (drive.folder / '.hq.lock').write_text('slug=auth-refresh\nsession=S1\n')
        for count in (2, 3):
            drive.stop()
            assert _wait_for(lambda: len(drive.typed()) == count)
        drive.stop()
        assert _wait_for(lambda: drive.rang('hq finish') == 1)
        assert len(drive.typed()) == 3
        assert drive.typed()[0] == READ
        assert all('hq finish' in line for line in drive.typed()[1:])
    finally:
        drive.close()


def test_drive_waits_on_a_question_asked_under_its_own_lock(tmp_path):
    """Verify a session that ends on a question while it holds its lock
    gets a bell and no retry text, with a stop pending as well, and
    gets the retry once it stops asking.

    Mutation: the endsq check dropped from the own-lock path, so the
    retry text answers a question the loop leaves to the user; or the
    stop file made to answer the question.
    Oracle: a stub tmux that records each call - the lines typed and
    the bells after a question Stop, a question Stop with the stop file
    present, and a Stop with no question.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.folder / '.hq.lock').write_text('slug=auth-refresh\nsession=S1\n')
        drive.stop(endsq=1, last='Rotate it?')
        assert _wait_for(lambda: drive.rang('asks: Rotate it?') == 1)
        (drive.driver_dir / 'stop').write_text('')
        drive.stop(endsq=1, last='Rotate it?')
        assert _wait_for(lambda: drive.rang('asks: Rotate it?') == 2)
        time.sleep(QUIET)
        assert drive.typed() == [READ]
        drive.stop()
        assert _wait_for(lambda: len(drive.typed()) == 2)
        assert 'hq finish' in drive.typed()[1]
    finally:
        drive.close()


def test_drive_counts_the_cycle_in_progress(tmp_path):
    """Verify the driver's [n/max] counter names the run in progress, so
    the first run reads 1/2 and the limit line reads 2/2.

    Mutation: the counter starts at 0, so the first run reads 0/2; or it
    goes up before the written line, so cycle 1 is written at 2/2.
    Oracle: the driver's own output across two finished cycles under
    --max-cycles 2, read against hand-written expected lines.
    """
    drive = _Drive(tmp_path, 2)
    try:
        drive.start()
        drive.write_manifest([_manifest_row(1)])
        drive.stop()
        assert _wait_for(lambda: len(drive.said()) == 2)
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: len(drive.typed()) == 3)
        drive.write_manifest([_manifest_row(1), _manifest_row(2)])
        drive.stop(sid='S2')
        drive.driver.wait(timeout=20)
        assert drive.said() == [
            '[1/2] started auth-refresh at cycle 1',
            '[1/2] cycle 1 written, clearing',
            '[2/2] reached --max-cycles 2 at cycle 2',
            ]
    finally:
        drive.close()


def test_drive_alerts_at_once_on_a_question(tmp_path):
    """Verify a Stop that ends on a question rings at once with nothing
    typed.

    Mutation: the row dropped, so the Stop falls to the waits park.
    Oracle: a stub tmux that records each call, with no idle notice sent.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        drive.stop(endsq=1, last='Ship it?')
        assert _wait_for(lambda: drive.rang('auth-refresh asks: Ship it?') == 1)
        assert drive.typed() == [READ]
    finally:
        drive.close()


def test_a_failed_type_parks_and_keeps_the_loop(tmp_path):
    """Verify a line tmux fails to type rings once and ends nothing.

    Mutation: the failed send made an END.
    Oracle: a stub whose typed lines fail: one bell, the driver still
    runs, and the next SessionStart types again.
    """
    drive = _Drive(tmp_path, 10)
    try:
        (tmp_path / 'send-fails').write_text('')
        drive.emit('SessionStart startup S1 0 0')
        assert _wait_for(lambda: drive.rang('could not type into the claude pane') == 1)
        drive.emit('StopFailure rate_limit S1 0 0')
        time.sleep(QUIET)
        assert drive.driver.poll() is None
        assert drive.rang('could not type') == 1
        (tmp_path / 'send-fails').unlink()
        drive.emit('SessionStart clear S2 0 0')
        assert _wait_for(lambda: drive.typed() == [READ, READ])
    finally:
        drive.close()


# The launcher ignores TERM, and its child writes the marker at TERM.
HUNG_LAUNCHER = """import signal, subprocess, sys
signal.signal(signal.SIGTERM, lambda *_: None)
subprocess.run(['bash', '-c', f'trap "touch {sys.argv[1]}; exit 0" TERM; while :; do sleep 0.2; done'])
"""


def _hung_launcher(tmp_path):
    """Command of a HUNG_LAUNCHER whose child writes tmp_path/termed.
    """
    script = tmp_path / 'launcher.py'
    script.write_text(HUNG_LAUNCHER)
    return (sys.executable, str(script), str(tmp_path / 'termed'))


def test_end_writes_the_reason_before_exit(tmp_path):
    """Verify END writes the reason to the end file before it types /exit,
    and the driver exits once the launcher is gone.

    Mutation: end written after /exit.
    Oracle: the tmux stub reads the end file when /exit is typed.
    """
    drive = _Drive(tmp_path, 10)
    try:
        drive.start()
        (drive.folder / '.hq.done').write_text('')
        drive.stop()
        assert drive.driver.wait(timeout=20) == 0
        assert drive.typed() == [READ, '/exit']
        assert 'end-file|auth-refresh is marked done' in drive.calls()
    finally:
        drive.close()


def test_end_confirms_the_exit_dialog(tmp_path):
    """Verify END sends Enter when claude is still up DEBOUNCE after /exit.

    Mutation: the Enter after /exit dropped.
    Oracle: a launcher that outlives /exit: two Enters follow the /exit
    line, the typing one and the dialog one.
    """
    drive = _Drive(tmp_path, 10, launcher=_hung_launcher(tmp_path))
    try:
        drive.start()
        (drive.folder / '.hq.done').write_text('')
        drive.stop()

        def enters_after_exit():
            calls = drive.calls()
            if 'send-keys|-t|%0|-l|--|/exit' not in calls:
                return 0
            at = calls.index('send-keys|-t|%0|-l|--|/exit')
            return calls[at:].count('send-keys|-t|%0|Enter')

        assert _wait_for(lambda: enters_after_exit() == 2, limit=15)
    finally:
        drive.close()


def test_end_terms_descendants_of_a_hung_launcher(tmp_path):
    """Verify END sends TERM to the launcher's descendants when the
    launcher outlives /exit, and rings no bell once it is gone.

    Mutation: the SIGTERM step dropped, so SIGKILL ends the child.
    Oracle: a child that traps TERM writes its marker before it dies.
    """
    drive = _Drive(tmp_path, 10, launcher=_hung_launcher(tmp_path))
    try:
        drive.start()
        (drive.folder / '.hq.done').write_text('')
        drive.stop()
        assert drive.driver.wait(timeout=45) == 0
        assert (tmp_path / 'termed').exists()
        assert drive.rang('did not exit') == 0
    finally:
        drive.close()


def test_launch_guards(tmp_path):
    """Verify launch dies on a leftover lock, and once it starts, hands
    claude the slug and a settings file that hooks all six events.

    Mutation: the lock guard dropped; a Loop-directive or manifest guard
    restored; the slug file left unwritten; or a hook event left out of
    settings.json.
    Oracle: the die line the lock draws; the files a claude stub copies
    out, read against the six event names the hook handles.
    """
    bin_dir = tmp_path / 'stub'
    bin_dir.mkdir()
    (bin_dir / 'tmux').write_text('#!/bin/sh\necho 40\n')
    (bin_dir / 'tmux').chmod(0o755)
    folder = tmp_path / '.handoff' / 'auth-refresh'
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text('# Handoff\n\n## Task\nWork.\n')
    out = tmp_path / 'out'
    out.mkdir()
    claude = ['sh', '-c', f'cp "$HQ_LOOP/slug" "$HQ_LOOP/settings.json" {out}']

    def launch() -> tuple[int, str]:
        proc = subprocess.run(
            [str(ROOT / 'bin' / 'hq-loop'), 'launch', '10', 'auth-refresh', *claude],
            cwd=tmp_path,
            env={
                **os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                'TMUX': 'stub', 'TMUX_PANE': '%0'},
            capture_output=True,
            text=True,
            timeout=30)
        return proc.returncode, proc.stderr.strip()

    (folder / '.hq.lock').write_text('session=S1\n')
    assert launch() == (
        1, ('hq loop: auth-refresh has an open cycle (.hq.lock) - finish it in'
            ' its session, or run hq begin auth-refresh --force once that session'
            ' is dead'))
    assert not (out / 'slug').exists()
    (folder / '.hq.lock').unlink()
    assert launch() == (0, '')
    assert (out / 'slug').read_text() == 'auth-refresh\n'
    hooks = json.loads((out / 'settings.json').read_text())['hooks']
    assert sorted(hooks) == sorted([
        'Stop', 'StopFailure', 'Notification', 'UserPromptSubmit',
        'SessionStart', 'PostToolUse'])


def test_launch_prints_the_end_reason(tmp_path):
    """Verify launch prints the reason the driver wrote, and rings.

    Mutation: the end read dropped from launch's trap.
    Oracle: a claude stub that writes the end file: hq loop: <reason> on
    stdout and a bell on stderr.
    """
    bin_dir = tmp_path / 'stub'
    bin_dir.mkdir()
    (bin_dir / 'tmux').write_text('#!/bin/sh\necho 40\n')
    (bin_dir / 'tmux').chmod(0o755)
    folder = tmp_path / '.handoff' / 'auth-refresh'
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text('# Handoff\n\n## Task\nWork.\n')
    claude = ['sh', '-c', 'echo "reached --max-cycles 1 at cycle 3" > "$HQ_LOOP/end"']
    proc = subprocess.run(
        [str(ROOT / 'bin' / 'hq-loop'), 'launch', '1', 'auth-refresh', *claude],
        cwd=tmp_path,
        env={
            **os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
            'TMUX': 'stub', 'TMUX_PANE': '%0'},
        capture_output=True,
        text=True,
        timeout=30)
    assert proc.returncode == 0
    assert 'hq loop: reached --max-cycles 1 at cycle 3' in proc.stdout.splitlines()
    assert '\a' in proc.stderr


def _loop_text(name: str) -> str:
    """The value bin/hq-loop assigns to name, with $TAIL expanded.
    """
    script = (ROOT / 'bin' / 'hq-loop').read_text()
    values = dict(re.findall(r'^([A-Z_]+)="(.*)"$', script, re.MULTILINE))
    return values[name].replace('$TAIL', values['TAIL'])


def _hook(loop_dir: pathlib.Path, payload: dict) -> str:
    """Pipe one hook payload to bin/hq-loop hook and return its stdout.

    Parameters
    ----------
    loop_dir : pathlib.Path
        The loop directory; it doubles as HQ_ROOT, so a thread folder
        goes under its ``.handoff``.
    payload : dict
        The hook's JSON input.

    Returns
    -------
    str
        The hook's stdout. Exit 0 and an empty stderr are asserted.
    """
    proc = subprocess.run(
        [str(ROOT / 'bin' / 'hq-loop'), 'hook', str(loop_dir)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, 'HQ_ROOT': str(loop_dir)}, timeout=30)
    assert (proc.returncode, proc.stderr) == (0, '')
    return proc.stdout


def _events(loop_dir: pathlib.Path) -> list[str]:
    """The lines the hook appended to the events file.
    """
    return (loop_dir / 'events').read_text().splitlines()


def test_hook_fields_by_event(tmp_path):
    """Verify each event's src field comes from its own payload key.

    Mutation: a source field swapped, such as the error read at
    Notification, or PostToolUse's main and agent_id reversed.
    Oracle: the design's src table, one hand-written line per event.
    """
    for payload in (
            {'hook_event_name': 'SessionStart', 'source': 'startup'},
            {'hook_event_name': 'StopFailure', 'error': 'rate_limit',
             'last_assistant_message': 'API Error: Rate limit reached'},
            {'hook_event_name': 'Notification',
             'notification_type': 'idle_prompt'},
            {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash'},
            {'hook_event_name': 'PostToolUse', 'agent_id': 'a7f'},
            {'hook_event_name': 'Stop', 'last_assistant_message': 'Done.',
             'background_tasks': []},
            {'hook_event_name': 'UserPromptSubmit', 'prompt': 'go on'}):
        _hook(tmp_path, {**payload, 'session_id': 'S1'})
    assert _events(tmp_path) == [
        'SessionStart startup S1 0 0',
        'StopFailure rate_limit S1 0 0',
        'Notification idle_prompt S1 0 0',
        'PostToolUse main S1 0 0',
        'PostToolUse a7f S1 0 0',
        'Stop - S1 0 0',
        'UserPromptSubmit - S1 0 0']


def test_tasks_leave_out_monitors_and_shells(tmp_path):
    """Verify the tasks field counts neither a monitor nor a shell task.

    Mutation: the monitor or shell filter dropped, so a background
    command or a monitor keeps the session counted as busy.
    Oracle: a monitor, a shell and a subagent task count 1.
    """
    _hook(tmp_path, {
        'hook_event_name': 'Stop', 'session_id': 'S1',
        'last_assistant_message': 'Waiting.',
        'background_tasks': [
            {'id': 't1', 'type': 'monitor'},
            {'id': 't2', 'type': 'shell'},
            {'id': 't3', 'type': 'subagent'}]})
    assert _events(tmp_path) == ['Stop - S1 1 0']


def test_endsq_reads_the_last_character(tmp_path):
    """Verify endsq is 1 only when ? is the last character but whitespace.

    Mutation: the markdown tolerance kept, so ?** reads 1, or no
    trailing-whitespace trim, so ? then a newline reads 0.
    Oracle: the LOOP_RULE wording, ? as the last character.
    """
    for message in ('Ship it?', 'Ship it?\n', 'Ship it?**', 'Ship it?.'):
        _hook(tmp_path, {
            'hook_event_name': 'Stop', 'session_id': 'S1',
            'last_assistant_message': message})
    assert [line.split()[-1] for line in _events(tmp_path)] == [
        '1', '1', '0', '0']


def test_last_line_is_cut_at_160(tmp_path):
    """Verify a Stop writes its last non-blank line, cut to 160 characters.

    Mutation: the cut dropped, or the blank-line skip dropped so a
    trailing blank line writes an empty last.
    Oracle: a 200-character last line followed by blank lines.
    """
    _hook(tmp_path, {
        'hook_event_name': 'Stop', 'session_id': 'S1',
        'last_assistant_message': 'First line.\n' + 'x' * 200 + '\n\n  \n'})
    assert (tmp_path / 'last').read_text() == 'x' * 160 + '\n'


def test_session_start_writes_the_mark(tmp_path):
    """Verify a SessionStart writes the manifest's last cycle to mark.

    Mutation: mark left unwritten, or the first row's cycle written.
    Oracle: a manifest whose rows end at c7, with a trailing blank line.
    """
    (tmp_path / 'slug').write_text('auth-refresh\n')
    cycles = tmp_path / '.handoff' / 'auth-refresh' / 'cycles'
    cycles.mkdir(parents=True)
    (cycles / 'manifest.tsv').write_text('cycle\twritten\n6\t-\n7\t-\n\n')
    _hook(tmp_path, {
        'hook_event_name': 'SessionStart', 'source': 'clear',
        'session_id': 'S2'})
    assert (tmp_path / 'mark').read_text() == '7\n'


def test_a_held_prompt_becomes_the_ruling(tmp_path):
    """Verify only a prompt from the user or a peer becomes the ruling.

    Mutation: the /, tag or Tail skip dropped, so a driver line, a task
    event or a teammate message rules; the 160 cut dropped; or a later
    prompt overwrites the first ruling.
    Oracle: the driver's own NUDGE_TEXT and RETRY_TEXT, the two tags
    the evidence records, and a two-line prompt with a 200-character
    first line.
    """
    (tmp_path / 'slug').write_text('auth-refresh\n')
    (tmp_path / 'hold').write_text('stall: held at 4 (no measured gain).\n')
    for prompt in (
            '/handoff read auth-refresh', _loop_text('NUDGE_TEXT'),
            _loop_text('RETRY_TEXT'),
            '<task-notification>\n<task-id>t1</task-id>',
            '<agent-message from="probe">done</agent-message>'):
        out = _hook(tmp_path, {
            'hook_event_name': 'UserPromptSubmit', 'session_id': 'S1',
            'prompt': prompt})
        assert out == ''
        assert not (tmp_path / 'ruling').exists()
    first = 'r' * 200
    out = _hook(tmp_path, {
        'hook_event_name': 'UserPromptSubmit', 'session_id': 'S1',
        'prompt': f'{first}\nSecond line.'})
    assert (tmp_path / 'ruling').read_text() == 'r' * 160 + '\n'
    assert json.loads(out) == {'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': (
            'This prompt ends the hold. Act on it by the loop rule, then run'
            ' /handoff write auth-refresh.')}}
    _hook(tmp_path, {
        'hook_event_name': 'UserPromptSubmit', 'session_id': 'S1',
        'prompt': 'A later word.'})
    assert (tmp_path / 'ruling').read_text() == 'r' * 160 + '\n'
