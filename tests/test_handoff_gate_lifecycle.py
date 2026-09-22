"""Lifecycle cases for the handoff gate hook.

Each case drives the real CLI and hook entry points against synthetic
files: a permission decision the gate does not make, a refusal that
outlasts a retry, a receipt that follows the content read, and a
suppression key naming one artifact of one thread of one project.
"""

import contextlib
import io
import json
import sys

import pytest
from scripts import handoff_gate

from bin import hq

_SLUG = 'gate-lifecycle'
_SESSION = 'session-lifecycle'
_NOW = '2026-09-21T12:00:00'
_SPEC = '# Spec\n\n## Scope\n\nKeep the retry bounded.\n\n## Risks\n\nCheck expiry.\n'


def _run(argv):
    """Call hq.main and return its exit code and captured stdout."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = hq.main(argv)
    return rc, out.getvalue()


def _setup(tmp_path, monkeypatch):
    """Isolate CLI and hook state and return the synthetic project root."""
    root = tmp_path / 'root'
    root.mkdir()
    for key, value in {
        'HQ_ROOT': str(root), 'HQ_STATE_DIR': str(tmp_path / 'state'),
        'HQ_SESSION': _SESSION, 'HQ_NOW': _NOW, 'HQ_HOST': 'test-host',
        'HQ_GIT': '0', 'HQ_GATE': '1',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    monkeypatch.delenv('HQ_GATE_DENY', raising=False)
    return root


def _thread(root, slug=_SLUG, where='Scope'):
    """Begin a thread with a real stamped spec and a source edit target."""
    root.mkdir(parents=True, exist_ok=True)
    assert _run(['--root', str(root), 'begin', slug])[0] == 0
    folder = root / '.handoff' / slug
    (folder / 'SPEC.md').write_text(_SPEC, encoding='utf-8')
    (root / 'app.py').write_text('value = 1\n', encoding='utf-8')
    assert _run([
        '--root', str(root), 'stamp', slug, 'SPEC.md', '--where', where,
    ])[0] == 0
    return folder


def _tool(transcript, name, fields):
    """Append one assistant tool call without replacing earlier records."""
    entry = {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': name, 'input': fields},
    ]}}
    with transcript.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry) + '\n')


def _gate(monkeypatch, root, transcript):
    """Run the hook over stdin and return its decision, or an empty dict."""
    payload = {
        'hook_event_name': 'PreToolUse', 'session_id': _SESSION,
        'cwd': str(root), 'transcript_path': str(transcript),
        'tool_name': 'Edit',
        'tool_input': {'file_path': str(root / 'app.py')},
    }
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = handoff_gate.main()
    assert rc == 0
    text = out.getvalue().strip()
    return json.loads(text)['hookSpecificOutput'] if text else {}


def test_unread_writes_stay_denied_until_a_read(tmp_path, monkeypatch):
    """Retrying a denied write without reading earns no credit for it.

    Mutation: a denied path joins the once-only reported list, so the
    first refusal silences enforcement for every retry after it.
    Oracle: two denied edits, then no denial once a Read tool call on
    the spec sits in the transcript.
    """
    root = _setup(tmp_path, monkeypatch)
    folder = _thread(root)
    transcript = tmp_path / 'transcript.jsonl'
    _tool(transcript, 'Bash', {'command': f'hq open {_SLUG}'})
    monkeypatch.setenv('HQ_GATE_DENY', '1')

    first = _gate(monkeypatch, root, transcript)
    retry = _gate(monkeypatch, root, transcript)
    _tool(transcript, 'Read', {'file_path': str(folder / 'SPEC.md')})
    after_read = _gate(monkeypatch, root, transcript)

    assert first.get('permissionDecision') == 'deny'
    assert retry.get('permissionDecision') == 'deny'
    assert 'SPEC.md' in retry['permissionDecisionReason']
    assert after_read.get('permissionDecision') != 'deny'


def test_a_resumed_session_denies_a_path_it_once_only_warned_about(
        tmp_path, monkeypatch):
    """Deny mode refuses a path an earlier advisory in that session named.

    Mutation: the reported list is still consulted under deny, so a
    session resumed with the switch on never refuses a write whose one
    warning it had already spent.
    Oracle: a state file carrying the spec's resolved path in reported -
    the shape an advisory leaves behind - still draws a denial.
    """
    root = _setup(tmp_path, monkeypatch)
    folder = _thread(root)
    transcript = tmp_path / 'transcript.jsonl'
    _tool(transcript, 'Bash', {'command': f'hq open {_SLUG}'})
    state_dir = tmp_path / 'state'
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f'{_SESSION}.handoff.json').write_text(
        json.dumps({'gate': {'slug': _SLUG, 'offset': 0, 'reads': [],
                             'read_commands': [],
                             'reported': [str(folder / 'SPEC.md')]}}),
        encoding='utf-8')
    monkeypatch.setenv('HQ_GATE_DENY', '1')

    decision = _gate(monkeypatch, root, transcript)

    assert decision.get('permissionDecision') == 'deny'
    assert 'SPEC.md' in decision['permissionDecisionReason']


def test_the_gate_prescribes_a_read_that_can_clear_it(tmp_path, monkeypatch):
    """A row holding one dead anchor is prescribed the whole file.

    Mutation: the remedy keyed on a row that resolved no span at all,
    so a row whose sibling anchors do resolve is handed a read earning
    no receipt, and the write stays refused on every retry.
    Oracle: the prescribed command, run verbatim as the gate spells it,
    leaves the next write undenied.
    """
    root = _setup(tmp_path, monkeypatch)
    _thread(root, where='Scope;Ghost')
    transcript = tmp_path / 'transcript.jsonl'
    _tool(transcript, 'Bash', {'command': f'hq open {_SLUG}'})
    monkeypatch.setenv('HQ_GATE_DENY', '1')

    reason = _gate(monkeypatch, root, transcript)['permissionDecisionReason']
    prescribed = reason.split('or run: ')[1].split(';')[0].split()
    rc, _ = _run(prescribed[1:])
    after = _gate(monkeypatch, root, transcript)

    assert prescribed[-1] == '--whole'
    assert rc == 0
    assert after.get('permissionDecision') != 'deny'


@pytest.mark.parametrize(('where', 'flags', 'diagnostic'), [
    ('Scope', ['--section', 'Ghost'], '? unresolved: Ghost'),
    ('Ghost', [], '? unresolved: Ghost'),
    ('Scope;Ghost', [], '? unresolved: Ghost'),
    ('Risks', ['--section', 'Scope'], 'Keep the retry bounded.'),
], ids=['absent-section', 'stale-anchor', 'partial-anchors', 'unrelated-section'])
def test_a_read_without_the_required_content_does_not_clear_the_gate(
        tmp_path, monkeypatch, where, flags, diagnostic):
    """A receipt cannot stand for required content the command did not print.

    Mutation: every hq read writes the same path-only receipt, including
    a failed anchor, a partial span set, or a different section entirely;
    or the containment test dropping its end bound, so a section opening
    before the required span counts as covering it.
    Oracle: after the diagnostic or unrelated section prints, an edit
    still receives a denial naming the required spec.
    """
    root = _setup(tmp_path, monkeypatch)
    _thread(root, where=where)
    transcript = tmp_path / 'transcript.jsonl'
    _tool(transcript, 'Bash', {'command': f'hq open {_SLUG}'})
    monkeypatch.setenv('HQ_GATE_DENY', '1')

    _, out = _run(['read', _SLUG, 'SPEC.md', *flags])
    assert diagnostic in out
    decision = _gate(monkeypatch, root, transcript)

    assert decision.get('permissionDecision') == 'deny'
    assert 'SPEC.md' in decision['permissionDecisionReason']


def test_a_successful_required_read_clears_the_gate(tmp_path, monkeypatch):
    """An anchored read printing its required span is the positive control.

    Mutation: receipt credit disabled wholesale to fix unresolved reads,
    so a successful read still leaves the agent unable to edit.
    Oracle: hq prints the required content and exits 0; the next write
    receives no denial without any transcript Read-tool evidence.
    """
    root = _setup(tmp_path, monkeypatch)
    _thread(root)
    transcript = tmp_path / 'transcript.jsonl'
    _tool(transcript, 'Bash', {'command': f'hq open {_SLUG}'})
    monkeypatch.setenv('HQ_GATE_DENY', '1')

    rc, out = _run(['read', _SLUG, 'SPEC.md'])
    decision = _gate(monkeypatch, root, transcript)

    assert rc == 0
    assert 'Keep the retry bounded.' in out
    assert decision.get('permissionDecision') != 'deny'


@pytest.mark.parametrize('other_project', [False, True])
def test_a_warning_for_one_spec_does_not_hide_another(
        tmp_path, monkeypatch, other_project):
    """Stored spellings shared by distinct threads are not suppression keys.

    Mutation: reported contains only SPEC.md, so opening a second thread
    or project skips its distinct unread spec after the first warning.
    Oracle: both writes warn about their respective thread's spec, even
    when projects also share a slug; revisiting the second stays quiet.
    """
    root = _setup(tmp_path, monkeypatch)
    _thread(root)
    second_root = tmp_path / 'other-root' if other_project else root
    second_slug = _SLUG if other_project else 'second-thread'
    _thread(second_root, second_slug)
    transcript = tmp_path / 'transcript.jsonl'
    _tool(transcript, 'Bash', {'command': f'hq open {_SLUG}'})
    first = _gate(monkeypatch, root, transcript)
    assert 'SPEC.md' in first['additionalContext']

    _tool(transcript, 'Bash', {'command': f'hq open {second_slug}'})
    second = _gate(monkeypatch, second_root, transcript)
    repeat = _gate(monkeypatch, second_root, transcript)

    assert 'additionalContext' in second
    assert 'SPEC.md' in second['additionalContext']
    assert second_slug in second['additionalContext']
    assert repeat == {}
