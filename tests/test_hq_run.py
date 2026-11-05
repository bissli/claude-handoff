"""Tests for the hq run and hq loop verbs and bin/hq-completion.bash."""

import os
import pathlib
import subprocess
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
