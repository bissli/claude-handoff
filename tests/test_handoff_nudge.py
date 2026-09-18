"""The nudge hook's trigger, its guards, and the shape it emits.

The message reaches the model only through the UserPromptSubmit
contract, so this file pins both the condition that fires it and the
envelope that carries it.
"""

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys

from bin import hq
from scripts import context_budget, handoff_nudge

_SESSION = 'session-nudge'
_REPO = pathlib.Path(__file__).resolve().parent.parent


def _arm(tmp_path, monkeypatch, context, handoff_at, sentinel=True):
    """Point the hook at a tmp sentinel, state file, and transcript.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Directory the sentinel, state dir, transcript, and workspace are
        built under. It is created if absent, so a caller may pass a
        fresh subdirectory to get an independent arming.
    monkeypatch : pytest.MonkeyPatch
        Fixture that repoints ``SENTINEL`` and ``STATE_DIR``.
    context : int
        Billed context the transcript's one assistant record reports.
    handoff_at : int
        Handoff point stored in the state file.
    sentinel : bool, default True
        Whether to create the sentinel. False leaves it absent, which
        disarms the hook.

    Returns
    -------
    pathlib.Path
        The workspace directory to pass as the payload's ``cwd``.
    """
    tmp_path = pathlib.Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    sentinel_path = tmp_path / '.nudge-handoff'
    if sentinel:
        sentinel_path.write_text('armed\n')
    monkeypatch.setattr(handoff_nudge, 'SENTINEL', str(sentinel_path))
    state_dir = tmp_path / 'cache'
    state_dir.mkdir()
    (state_dir / f'{_SESSION}.json').write_text(json.dumps({
        'band': 2,
        'growth_per_call': 1600,
        'target': 350000,
        'handoff': handoff_at,
        'context': 1,
        }))
    monkeypatch.setattr(context_budget, 'STATE_DIR', str(state_dir))
    (tmp_path / 'transcript.jsonl').write_text(json.dumps({
        'message': {
            'id': 'm1',
            'role': 'assistant',
            'model': 'claude-opus-5',
            'usage': {'cache_read_input_tokens': context,
                      'cache_creation_input_tokens': 0,
                      'input_tokens': 0,
                      'output_tokens': 0},
            },
        }) + '\n')
    work = tmp_path / 'work'
    work.mkdir()
    return work


def _hq(argv):
    """Drive hq in process and return its exit code, stdout, and stderr.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _run():
    """Drive the hook's main() over the fed payload and return its stdout.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = handoff_nudge.main()
    return rc, out.getvalue()


def _feed(monkeypatch, work, cwd=None, **extra):
    """Put one payload on the hook's stdin, naming cwd or else work.

    The transcript always sits beside the workspace, so a cwd deeper in
    the tree does not move it.
    """
    payload = {'session_id': _SESSION, 'cwd': str(cwd or work),
               'transcript_path': str(pathlib.Path(work).parent
                                      / 'transcript.jsonl')}
    payload.update(extra)
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(payload)))


def _seed_thread(work, locked_at=None):
    """Give work a handoff folder, optionally holding a lock of that age.
    """
    folder = work / '.handoff' / 'a-thread'
    (folder / 'cycles').mkdir(parents=True, exist_ok=True)
    (folder / 'cycles' / 'manifest.tsv').write_text('cycle\n1\n')
    lock = folder / '.hq.lock'
    if locked_at is None:
        lock.unlink(missing_ok=True)
    else:
        lock.write_text(f'session={_SESSION}\ntime={locked_at}\ncycle=2\n')
    return folder


def test_nudge_fires_at_the_point_and_stays_silent_one_token_below(
        tmp_path, monkeypatch):
    """Verify the threshold is inclusive and nothing fires below it.

    Mutation: >= narrowed to > at the context test, < widened to <=, or
    the handoff point read from the target slot of load_state's tuple.
    Oracle: two transcripts straddling the stored handoff point by one
    token, with the emitted text naming both figures in thousands.
    """
    work = _arm(tmp_path, monkeypatch, context=290000, handoff_at=290000)
    _feed(monkeypatch, work)
    rc, out = _run()
    assert rc == 0
    assert json.loads(out)['hookSpecificOutput'][
        'additionalContext'].startswith(
            "Context 290K, past this session's 290K handoff point.")

    work = _arm(tmp_path / 'below', monkeypatch,
                context=289999, handoff_at=290000)
    _feed(monkeypatch, work)
    assert _run() == (0, '')


def test_nudge_measures_context_live_rather_than_from_stored_state(
        tmp_path, monkeypatch):
    """Verify a cleared session is judged on its transcript, not memory.

    Mutation: context taken from load_state instead of read_transcript,
    which /clear and /compact leave at its pre-clear figure while the
    session id and state file survive.
    Oracle: a state file whose stored context is far past the point and
    a transcript that is not - the state file's figure would fire.
    """
    work = _arm(tmp_path, monkeypatch, context=1000, handoff_at=290000)
    state = pathlib.Path(context_budget.STATE_DIR, f'{_SESSION}.json')
    stored = json.loads(state.read_text())
    stored['context'] = 301000
    state.write_text(json.dumps(stored))
    _feed(monkeypatch, work)
    assert _run() == (0, ''), 'stored context must not decide the nudge'


def test_nudge_carries_the_userpromptsubmit_envelope(tmp_path, monkeypatch):
    """Verify the emitted object names the event the harness matches on.

    Mutation: hookEventName misspelled or set to Stop, additionalContext
    renamed, or the object emitted without its hookSpecificOutput wrapper
    - each of which the harness drops in silence, so no other assertion
    in this file would fail.
    Oracle: the literal contract keys, checked by exact equality.
    """
    work = _arm(tmp_path, monkeypatch, context=300000, handoff_at=290000)
    _feed(monkeypatch, work)
    emitted = json.loads(_run()[1])
    assert list(emitted) == ['hookSpecificOutput']
    assert emitted['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit'
    assert set(emitted['hookSpecificOutput']) == {
        'hookEventName', 'additionalContext'}


def test_nudge_is_silent_unless_the_sentinel_is_present(
        tmp_path, monkeypatch):
    """Verify presence of the sentinel is what arms the hook.

    Mutation: the sentinel test dropped or inverted.
    Oracle: the same over-budget payload with and without the file, one
    emitting and one silent.
    """
    work = _arm(tmp_path, monkeypatch, context=300000, handoff_at=290000,
                sentinel=False)
    _feed(monkeypatch, work)
    assert _run() == (0, '')

    pathlib.Path(handoff_nudge.SENTINEL).write_text('armed\n')
    _feed(monkeypatch, work)
    assert _run()[1] != ''


def test_nudge_holds_off_for_an_open_cycle_at_or_above_the_cwd(
        tmp_path, monkeypatch):
    """Verify a live lock silences the nudge from any depth under its root.

    Mutation: the parents dropped from the walk so only cwd is examined,
    the manifest miss breaking the loop instead of continuing, or the
    lock glob pointed at a name no cycle writes.
    Oracle: one lock, read from three cwds - the root itself, a
    subdirectory, and a deeper subdirectory - each silent while the lock
    is held and each firing once it is gone.
    """
    work = _arm(tmp_path, monkeypatch, context=300000, handoff_at=290000)
    monkeypatch.setenv('HQ_NOW', '2026-09-18T12:00:00')
    _seed_thread(work, locked_at='2026-09-18T11:59:00')
    deep = work / 'pkg' / 'deep'
    deep.mkdir(parents=True)
    for cwd in (work, work / 'pkg', deep):
        _feed(monkeypatch, work, cwd=cwd)
        assert _run() == (0, ''), f'a live lock must silence cwd {cwd}'

    _seed_thread(work, locked_at=None)
    for cwd in (work, deep):
        _feed(monkeypatch, work, cwd=cwd)
        assert _run()[1] != '', f'clearing the lock must release cwd {cwd}'


def test_nudge_ignores_a_lock_its_session_abandoned(tmp_path, monkeypatch):
    """Verify a dead lock stops silencing the nudge, as hq begin allows.

    Mutation: the age rule dropped so presence alone silences, which
    leaves a crashed session's lock muting the nudge for that repo for
    good; or the comparison flipped so a live lock is treated as dead.
    Oracle: two locks straddling hq's own two-hour rule by a minute,
    read against a fixed HQ_NOW.
    """
    work = _arm(tmp_path, monkeypatch, context=300000, handoff_at=290000)
    monkeypatch.setenv('HQ_NOW', '2026-09-18T12:00:00')

    _seed_thread(work, locked_at='2026-09-18T09:59:00')
    _feed(monkeypatch, work)
    assert _run()[1] != '', 'a lock past two hours is dead to hq begin too'

    _seed_thread(work, locked_at='2026-09-18T10:01:00')
    _feed(monkeypatch, work)
    assert _run() == (0, ''), 'a lock inside two hours is still live'

    (work / '.handoff' / 'a-thread' / '.hq.lock').write_text('garbage\n')
    _feed(monkeypatch, work)
    assert _run() == (0, ''), 'an unreadable lock counts as live, as in hq'


def test_nudge_survives_a_payload_it_cannot_read(tmp_path, monkeypatch):
    """Verify no malformed input reaches the harness as a failure.

    Mutation: the guard narrowed to ValueError, which a list payload
    walks past into AttributeError on .get, or removed so a traceback
    exits non-zero and shows the user an error on every prompt.
    Oracle: three payloads that are not an object - text, a list, and a
    bare number - each answered with a clean 0 and no output.
    """
    _arm(tmp_path, monkeypatch, context=300000, handoff_at=290000)
    for raw in ('not json at all', '["a", "list"]', '17'):
        monkeypatch.setattr('sys.stdin', io.StringIO(raw))
        assert _run() == (0, ''), f'payload {raw!r} must not raise'


def test_nudge_runs_as_the_script_hooks_json_invokes(tmp_path, monkeypatch):
    """Verify the script-mode import fallback binds both modules.

    Mutation: either sys.path insert in the except-ImportError branch
    dropped or pointed at the wrong directory, which breaks the hook in
    every real session while every in-process test stays green.
    Oracle: the file run as hooks.json runs it, from an unrelated cwd,
    with the sentinel absent so the exit status is all that is asserted.
    """
    done = subprocess.run(
        [sys.executable, str(_REPO / 'scripts' / 'handoff_nudge.py')],
        input=json.dumps({'session_id': _SESSION, 'cwd': str(tmp_path)}),
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
    assert (done.returncode, done.stdout, done.stderr) == (0, '', '')


def test_nudge_stays_silent_until_a_handoff_point_is_recorded(
        tmp_path, monkeypatch):
    """Verify a session with no recorded point is never past it.

    Mutation: the `not handoff_at` guard dropped, after which an
    unrecorded point reads as zero and every context clears it, so a
    session the budget hook has never measured is asked to hand off.
    Oracle: a stored point of zero and a missing state file, both with
    a live context well above zero.
    """
    work = _arm(tmp_path, monkeypatch, context=300000, handoff_at=0)
    _feed(monkeypatch, work)
    assert _run() == (0, ''), 'zero is not a handoff point'

    pathlib.Path(context_budget.STATE_DIR, f'{_SESSION}.json').unlink()
    _feed(monkeypatch, work)
    assert _run() == (0, ''), 'an unmeasured session has no point either'


def test_nudge_verb_arms_disarms_and_reports_which(tmp_path, monkeypatch):
    """Verify hq nudge writes, removes, and reports the sentinel.

    Mutation: off unlinking nothing, on truncating a file it should
    leave alone, the report inverted, a repeat exiting non-zero, or the
    explaining body dropped so the file cannot say what it is for.
    Oracle: the file on disk after each call, read independently of the
    line the verb prints.
    """
    sentinel = tmp_path / '.nudge-handoff'
    monkeypatch.setattr(hq, 'SENTINEL', str(sentinel))

    rc, out, _ = _hq(['nudge'])
    assert (rc, sentinel.exists()) == (0, False)
    assert 'nudge is off' in out

    assert _hq(['nudge', 'on'])[0] == 0
    body = sentinel.read_text()
    assert 'hq nudge off' in body, 'the file must say how to disarm it'
    assert 'nudge is on' in _hq(['nudge'])[1]

    rc, out, _ = _hq(['nudge', 'on'])
    assert (rc, 'already on' in out) == (0, True)
    assert sentinel.read_text() == body, 'a repeat must not rewrite it'

    assert _hq(['nudge', 'off'])[0] == 0
    assert not sentinel.exists()
    rc, out, _ = _hq(['nudge', 'off'])
    assert (rc, 'already off' in out) == (0, True)


def test_nudge_verb_and_hook_agree_on_the_sentinel_path(tmp_path):
    """Verify the verb arms the very file the hook reads.

    Mutation: either half holding its own copy of the path, which every
    test that drives one side alone passes while the shipped feature
    cannot be turned on at all.
    Oracle: the verb and the hook run as separate processes under one
    redirected HOME, so only a shared path makes the hook fire.
    """
    home = tmp_path / 'home'
    (home / '.claude' / 'cache' / 'claude-handoff').mkdir(parents=True)
    (home / '.claude' / 'cache' / 'claude-handoff' / 'sid.json').write_text(
        json.dumps({'handoff': 290000, 'context': 1}))
    transcript = tmp_path / 'tr.jsonl'
    transcript.write_text(json.dumps({
        'message': {'id': 'm1', 'role': 'assistant', 'model': 'claude-opus-5',
                    'usage': {'cache_read_input_tokens': 300000,
                              'cache_creation_input_tokens': 0,
                              'input_tokens': 0, 'output_tokens': 0}},
        }) + '\n')
    payload = json.dumps({'session_id': 'sid', 'cwd': str(tmp_path),
                          'transcript_path': str(transcript)})

    def hook():
        done = subprocess.run(
            [sys.executable, str(_REPO / 'scripts' / 'handoff_nudge.py')],
            input=payload, capture_output=True, text=True,
            env={**os.environ, 'HOME': str(home)}, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout

    def verb(*args):
        done = subprocess.run(
            [str(_REPO / 'bin' / 'hq'), 'nudge', *args],
            capture_output=True, text=True,
            env={**os.environ, 'HOME': str(home)}, timeout=60)
        assert done.returncode == 0, done.stderr

    assert hook() == '', 'disarmed by default'
    verb('on')
    assert 'additionalContext' in hook(), 'hq nudge on must arm the hook'
    verb('off')
    assert hook() == '', 'hq nudge off must disarm the hook'
