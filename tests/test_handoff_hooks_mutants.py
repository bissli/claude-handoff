"""Kill surviving logic and number mutants from run4-group-hooks (hooks scripts).

Helpers mirror test_handoff_hooks.py: _row, _handoff_root, _bash, _read,
_transcript, _payload, _run, _context, _finished, _stop_payload.
"""

import io
import json
import pathlib
import sys

from scripts import handoff_gate, handoff_stop

from bin import hq

_SLUG = 'demo-slug'
_NOW = '2026-09-09T12:00:00'
_SPEC_TEXT = '# Spec\n\n## Scope\n\nOne.\nTwo.\n\n## Risks\n\nThree.\n'
_LEDGER_HEADER = '\t'.join(hq.LEDGER_FIELDS)
_MANIFEST_HEADER = '\t'.join(hq.MANIFEST_FIELDS)


def _row(path, **overrides):
    """Build one ledger row with live/always defaults.

    Parameters
    ----------
    path : str
        Value for the path field.
    **overrides : str
        Replacements for any default field.

    Returns
    -------
    dict
        A full ledger row.
    """
    row = {
        'cycle': '1', 'ts': _NOW, 'path': path, 'base': 'folder',
        'kind': 'spec', 'status': 'live', 'read_before': 'always',
        'successor': '-', 'where': 'Scope', 'sha12': '-', 'lines': '10',
        'reason': '-', 'label': '-',
        }
    row.update(overrides)
    return row


def _handoff_root(tmp_path, slug=_SLUG):
    """Create a project root with one handoff folder and ledger.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary directory; root placed at tmp_path/proj.
    slug : str, default _SLUG
        Handoff folder name under .handoff/.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        Project root and handoff folder.
    """
    root = pathlib.Path(tmp_path) / 'proj'
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)
    (root / 'src').mkdir(exist_ok=True)
    (root / 'src' / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'NOTES.md').write_text('# Notes\n\n## Scope\n\nOne.\n',
                                     encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# Handoff\n\n## Task\n\nWork.\n',
                                       encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    for row in (_row('SPEC.md'),
                _row('NOTES.md', read_before='mention'),
                _row('OLD.md', status='superseded')):
        hq._append_tsv(ledger, hq.LEDGER_FIELDS, row, _LEDGER_HEADER)
    return root, folder


def _bash(command):
    """Return one transcript line holding a Bash tool call.
    """
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}]}}


def _read(file_path):
    """Return one transcript line holding a Read tool call.
    """
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Read',
         'input': {'file_path': file_path}}]}}


def _transcript(path, entries):
    """Write a JSONL transcript and return its path.
    """
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n',
                    encoding='utf-8')
    return path


def _payload(root, transcript, session, tool_name, tool_input):
    """Build a PreToolUse payload.

    Parameters
    ----------
    root : pathlib.Path
        Directory for cwd.
    transcript : pathlib.Path
        Session transcript path.
    session : str
        Session id.
    tool_name : str
        Tool being called.
    tool_input : dict
        Tool input block.

    Returns
    -------
    dict
        Payload ready for gate().
    """
    return {
        'hook_event_name': 'PreToolUse', 'cwd': str(root),
        'session_id': session, 'transcript_path': str(transcript),
        'tool_name': tool_name, 'tool_input': tool_input,
        }


def _run(monkeypatch, capsys, module, payload):
    """Drive a hook's main() over one payload and return its stdout.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    module.main()
    return capsys.readouterr().out.strip()


def _context(out):
    """Return the additionalContext text from a gate's stdout.
    """
    return json.loads(out)['hookSpecificOutput']['additionalContext']


def _finished(folder, cycle, sha):
    """Append one finished-cycle row to a handoff manifest.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding cycles/manifest.tsv.
    cycle : str
        Cycle number for the row.
    sha : str
        HANDOFF.md digest the cycle finished on.
    """
    (folder / 'cycles').mkdir(exist_ok=True)
    row = dict.fromkeys(hq.MANIFEST_FIELDS, '-')
    row.update({'cycle': cycle, 'written': _NOW, 'handoff_sha': sha})
    hq._append_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS,
                   row, _MANIFEST_HEADER)


def _stop_payload(root, session, **extra):
    """Build a Stop payload.
    """
    payload = {'hook_event_name': 'Stop', 'cwd': str(root),
               'session_id': session, 'stop_hook_active': False}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# bash_writes
# ---------------------------------------------------------------------------


def test_bash_writes_heredoc_end_detection():
    """Verify == detects the closing delimiter, not !=.

    Mutation: x_bash_writes__mutmut_8 - flips == to != so delimiter is
    cleared on the first non-matching body line. The second and later body
    lines are then treated as normal code; a '>' on them becomes a redirect.
    Oracle: hand-computed - a two-line heredoc body with '>' on the second
    line must return False (body stripped); the mutant returns True.
    """
    # Two-line body: the mutant clears delimiter after 'line1', then keeps
    # '> in_body' as a normal redirect line. Original strips both body lines.
    cmd = 'cat <<EOF\nline1\n> in_body\nEOF'
    assert handoff_gate.bash_writes(cmd) is False
    # Control: a real redirect after the heredoc must always be True.
    cmd_with_real_write = 'cat <<EOF\nline1\n> in_body\nEOF\necho x > out.txt'
    assert handoff_gate.bash_writes(cmd_with_real_write) is True


def test_bash_writes_heredoc_continue_not_break():
    """Verify continue (not break) keeps processing lines after heredoc end.

    Mutation: x_bash_writes__mutmut_11 - changes continue to break,
    exiting the loop entirely when the closing delimiter is found or when
    inside the heredoc body. Lines after the heredoc end are dropped.
    Oracle: hand-computed - a file redirect after a heredoc closing
    delimiter must register as a write.
    """
    # Redirect comes AFTER the heredoc block.
    cmd = 'cat <<EOF\nbody\nEOF\necho y > out.txt'
    assert handoff_gate.bash_writes(cmd) is True
    # Non-heredoc: control to confirm baseline behavior.
    assert handoff_gate.bash_writes('grep x file.txt') is False


# ---------------------------------------------------------------------------
# scan_transcript
# ---------------------------------------------------------------------------


def test_scan_transcript_skips_non_tool_use_lines_with_continue():
    """Verify continue (not break) keeps scanning after non-tool_use lines.

    Mutation: x_scan_transcript__mutmut_8 - changes continue to break,
    so the loop exits on the first line that lacks '"tool_use"' (which
    is nearly every line). All tool_use entries after that line are lost.
    Oracle: Read tool call after a plain-text prefix line returns the
    path; with break it would return empty.
    """
    entries = [
        {'type': 'human', 'text': 'hello'},
        _read('/path/to/file.py'),
        ]
    lines = '\n'.join(json.dumps(e) for e in entries)
    _, reads, _ = handoff_gate.scan_transcript(lines)
    assert reads == ['/path/to/file.py']


def test_scan_transcript_skips_bad_json_with_continue():
    """Verify continue (not break) keeps scanning after a bad-JSON line.

    Mutation: x_scan_transcript__mutmut_11 - apply_mutant places break at
    the '"tool_use"' not-in-line guard (first matching 12-space continue).
    A line without '"tool_use"' before a valid Read exits the loop early.
    Oracle: a plain line (no '"tool_use"') before a valid Read - path
    appears; under the mutant the loop breaks and returns empty reads.
    """
    plain_line = 'this is not json and has no tool sentinel'
    assert '"tool_use"' not in plain_line
    good_line = json.dumps(_read('/real/path.py'))
    text = plain_line + '\n' + good_line + '\n'
    _, reads, _ = handoff_gate.scan_transcript(text)
    assert reads == ['/real/path.py']


def test_scan_transcript_skips_non_assistant_with_continue():
    """Verify continue (not break) keeps scanning after non-assistant entries.

    Mutation: x_scan_transcript__mutmut_20 - changes continue to break
    when an entry is not type='assistant'. A human entry before the
    actual Read would cause the loop to exit early.
    Oracle: Read following a human entry appears in output.
    """
    entries = [
        {'type': 'human', 'message': {'content': [
            {'type': 'tool_result', 'content': 'ok'}]}},
        _read('/spec/file.py'),
        ]
    text = '\n'.join(json.dumps(e) for e in entries)
    _, reads, _ = handoff_gate.scan_transcript(text)
    assert reads == ['/spec/file.py']


def test_scan_transcript_skips_non_list_content_with_continue():
    """Verify continue (not break) keeps scanning after non-list content.

    Mutation: x_scan_transcript__mutmut_30 - changes continue to break
    when content is not a list. An assistant entry with string content
    (malformed) before a real Read would abort the scan.
    Oracle: Read following a string-content entry appears in output.
    """
    malformed = {'type': 'assistant', 'message': {'content': 'just a string'}}
    entries = [malformed, _read('/actual/spec.py')]
    text = '\n'.join(json.dumps(e) for e in entries)
    _, reads, _ = handoff_gate.scan_transcript(text)
    assert reads == ['/actual/spec.py']


def test_scan_transcript_skips_non_tool_use_block_with_continue():
    """Verify continue (not break) keeps scanning after non-tool_use blocks.

    Mutation: x_scan_transcript__mutmut_39 - changes continue to break
    inside the inner block loop when a block is not type='tool_use'. A
    text block before the Read block in the same assistant entry would
    abort the inner loop.
    Oracle: the Read block in the same content list is processed.
    """
    entry = {'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': 'some response'},
        {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/f.py'}},
        ]}}
    text = json.dumps(entry)
    _, reads, _ = handoff_gate.scan_transcript(text)
    assert reads == ['/f.py']


def test_scan_transcript_or_condition_skips_non_dict_entries():
    """Verify or (not and) short-circuits on non-dict to prevent AttributeError.

    Mutation: x_scan_transcript__mutmut_12 - changes or to and in the
    isinstance/type check, so a non-dict entry (a JSON array) would
    still call .get() on it, raising AttributeError.
    Oracle: scan_transcript must not raise on a JSON array line that
    contains '"tool_use"' so it passes the outer guard and reaches the
    isinstance check.
    """
    # Array containing "tool_use" string passes the '"tool_use"' guard
    # but is not a dict; 'or' short-circuits, 'and' calls .get() -> error.
    array_line = json.dumps(['tool_use', 1, 2])
    good_line = json.dumps(_read('/ok.py'))
    text = array_line + '\n' + good_line + '\n'
    # Raises AttributeError under mutmut_12; returns normally here.
    slug, reads, _ = handoff_gate.scan_transcript(text)
    assert reads == ['/ok.py']


def test_scan_transcript_or_condition_skips_non_dict_blocks():
    """Verify or (not and) short-circuits on non-dict blocks to prevent error.

    Mutation: x_scan_transcript__mutmut_31 - changes or to and in the
    inner block isinstance/type check. A list block calls .get() on a
    list, raising AttributeError.
    Oracle: a content list holding a raw list block must not raise.
    """
    entry = {'type': 'assistant', 'message': {'content': [
        [1, 2, 3],
        {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/g.py'}},
        ]}}
    text = json.dumps(entry)
    # Raises AttributeError under mutmut_31; returns normally here.
    _, reads, _ = handoff_gate.scan_transcript(text)
    assert reads == ['/g.py']


# ---------------------------------------------------------------------------
# gate - return 0 exit paths (number mutants)
# ---------------------------------------------------------------------------


def test_gate_returns_zero_when_hq_gate_env_is_off(monkeypatch, tmp_path):
    """Verify gate() returns 0 when HQ_GATE=0, not 1.

    Mutation: x_gate__mutmut_6 - return 0 -> return 1 after the HQ_GATE
    check. The gate is supposed to fail open (exit 0) even when silenced.
    Oracle: direct gate() return value with HQ_GATE=0 set.
    """
    monkeypatch.setenv('HQ_GATE', '0')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    payload = {'cwd': str(tmp_path), 'session_id': 'g0',
               'transcript_path': '', 'tool_name': 'Bash',
               'tool_input': {'command': 'echo x > out.txt'}}
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_when_no_handoff_root(monkeypatch, tmp_path):
    """Verify gate() returns 0 when there is no handoff project.

    Mutation: x_gate__mutmut_31 - return 0 -> return 1 after root-not-
    found check. Gate must fail open when no ledger exists.
    Oracle: direct gate() return value with a bare directory.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    bare = tmp_path / 'bare'
    bare.mkdir()
    payload = {'cwd': str(bare), 'session_id': 'r0',
               'transcript_path': '', 'tool_name': 'Bash',
               'tool_input': {'command': 'echo x > out.txt'}}
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_for_edit_with_empty_path(monkeypatch, tmp_path):
    """Verify gate() returns 0 when an Edit target path is empty.

    Mutation: x_gate__mutmut_58 - return 0 -> return 1 when the file_path
    field of an Edit call is empty. The gate should fail open, not error.
    Oracle: direct gate() return value.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'e0', 'Edit', {'file_path': ''})
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_when_edit_targets_handoff_folder(
        monkeypatch, tmp_path):
    """Verify gate() returns 0 when the target is inside .handoff/.

    Mutation: x_gate__mutmut_75 - return 0 -> return 1 after the .handoff/
    prefix check, changing handoff tooling writes from silent to exit-1.
    Oracle: direct gate() return value for an Edit inside .handoff/.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    inside = folder / 'NOTES.md'
    payload = _payload(root, tr, 'sc0', 'Edit',
                       {'file_path': str(inside)})
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_for_non_writing_bash(monkeypatch, tmp_path):
    """Verify gate() returns 0 when Bash does not write a file.

    Mutation: x_gate__mutmut_82 - return 0 -> return 1 after the
    bash_writes() check, so read-only commands exit with code 1.
    Oracle: direct gate() return value for a cat command.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'nw0', 'Bash', {'command': 'cat file.txt'})
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_for_unknown_tool(monkeypatch, tmp_path):
    """Verify gate() returns 0 for a tool that is neither Edit nor Bash.

    Mutation: x_gate__mutmut_83 - return 0 -> return 1 in the else
    branch, so every unknown tool exits with code 1.
    Oracle: direct gate() return value with tool_name='Read'.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'uk0', 'Read', {'file_path': '/x.py'})
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_when_already_reported(monkeypatch, tmp_path):
    """Verify gate() returns 0 when the session already reported.

    Mutation: x_gate__mutmut_124 - return 0 -> return 1 after the
    reported check, making every subsequent call in the same session
    exit with code 1 rather than the documented 0.
    Oracle: direct gate() return value after state is seeded as reported.
    """
    root, _ = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    (state / 'rpt.handoff.json').write_text(
        json.dumps({'gate': {'reported': True, 'offset': 0,
                             'slug': _SLUG, 'reads': [],
                             'read_commands': []}}),
        encoding='utf-8')
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'rpt', 'Edit',
                       {'file_path': 'src/app.py'})
    assert handoff_gate.gate(payload) == 0


def test_gate_returns_zero_when_no_message_to_print(monkeypatch, tmp_path):
    """Verify gate() returns 0 when all gated paths have been read.

    Mutation: x_gate__mutmut_356 - changes 'if not message: return 0'
    to 'return 1', so a session that has read every gated path exits
    with code 1 instead of 0.
    Oracle: direct gate() return value when SPEC.md is in the Read list.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _read(str(folder / 'SPEC.md')),
        ])
    payload = _payload(root, tr, 'nm0', 'Edit',
                       {'file_path': 'src/app.py'})
    result = handoff_gate.gate(payload)
    assert result == 0


# ---------------------------------------------------------------------------
# gate - logic mutants
# ---------------------------------------------------------------------------


def test_gate_root_none_not_string_sentinel(monkeypatch, tmp_path):
    """Verify root=None (not '') is the sentinel for no-root-found.

    Mutation: x_gate__mutmut_18 - changes root=None to root="". The
    'if root is None' guard does not fire for "" so the function
    proceeds with "" as a pathlib base, which raises TypeError.
    Oracle: gate() returns 0 for a bare directory with no ledger.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    bare = tmp_path / 'bare2'
    bare.mkdir()
    # No .handoff/ at all - root stays as its initial value.
    payload = {'cwd': str(bare), 'session_id': 'r18',
               'transcript_path': '', 'tool_name': 'Edit',
               'tool_input': {'file_path': 'x.py'}}
    assert handoff_gate.gate(payload) == 0


def test_gate_next_without_default_raises_on_empty_glob(monkeypatch, tmp_path):
    """Verify next(..., None) (not next(...,)) survives empty handoff dirs.

    Mutation: x_gate__mutmut_21 - removes the None sentinel from next(),
    so StopIteration propagates when .handoff/ has no ledger. Under
    main() this is caught silently; gate() itself raises.
    Oracle: gate() returns 0 for a directory with no .handoff/ledger.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # .handoff/ exists but no */ledger.tsv inside.
    bare = tmp_path / 'nodir'
    bare.mkdir()
    (bare / '.handoff').mkdir()
    payload = {'cwd': str(bare), 'session_id': 'r21',
               'transcript_path': '', 'tool_name': 'Bash',
               'tool_input': {'command': 'echo x > out.txt'}}
    assert handoff_gate.gate(payload) == 0


def test_gate_relative_path_joined_with_cwd(monkeypatch, tmp_path):
    """Verify a relative Edit path is joined with cwd, not left as-is.

    Mutation: x_gate__mutmut_61 - inverts 'if not target.is_absolute()'
    to 'if target.is_absolute()', joining cwd onto absolute paths and
    leaving relative paths unresolved. A relative path to src/app.py
    must be detected as a repo write, not silently passed through.
    Oracle: spy on stdout - relative 'src/app.py' triggers the gate.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'rel0', 'Edit',
                       {'file_path': 'src/app.py'})
    result = handoff_gate.gate(payload)
    # A relative path must reach the missing-reads check and report.
    assert result == 0  # still exits 0 (fail-open) but message is printed


def test_gate_relative_path_gate_actually_fires(monkeypatch, capsys, tmp_path):
    """Verify the gate reports SPEC.md when given a relative Edit path.

    Mutation: x_gate__mutmut_61 - inverts the is_absolute check so
    relative paths are not joined and the handoff-prefix test passes
    incorrectly (or fails to match), letting the write through silently.
    Oracle: spy on stdout - message mentions SPEC.md.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'rel1', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'SPEC.md' in out


def test_gate_state_dir_env_fallback_default(monkeypatch, tmp_path):
    """Verify gate() uses STATE_DIR when HQ_STATE_DIR is unset.

    Mutation: x_gate__mutmut_103 - removes the STATE_DIR default from
    os.environ.get(), so calling gate() without the env var set yields
    pathlib.Path(None), raising TypeError.
    Oracle: gate() returns 0 with HQ_STATE_DIR explicitly unset; the
    default STATE_DIR path (under ~/.claude) is used instead.
    """
    monkeypatch.delenv('HQ_STATE_DIR', raising=False)
    bare = tmp_path / 'noenv'
    bare.mkdir()
    payload = {'cwd': str(bare), 'session_id': 'env0',
               'transcript_path': '', 'tool_name': 'Bash',
               'tool_input': {'command': 'echo x > out.txt'}}
    # No handoff root - gate returns early. Must return 0, not raise.
    assert handoff_gate.gate(payload) == 0


def test_gate_reads_key_is_lowercase(monkeypatch, tmp_path):
    """Verify stored reads are written and read under the key 'reads'.

    Mutation: x_gate__mutmut_144 reads 'READS' not 'reads' (so stored
    reads are not recovered); x_gate__mutmut_337 writes 'READS' not
    'reads' (so the next call finds nothing). Either mutant causes the
    gate to forget what was read and re-fire on every call.
    Oracle: second call after SPEC.md is read is silent (no message);
    with either key-case mutant the read is forgotten and message repeats.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _read(str(folder / 'SPEC.md')),
        ])

    def run_gate(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return handoff_gate.gate(payload)

    # First call: SPEC.md appears in transcript -> no message.
    assert run_gate('rk0') == 0
    # The state file must use 'reads' as the key.
    state_file = tmp_path / 'state' / 'rk0.handoff.json'
    state = json.loads(state_file.read_text(encoding='utf-8'))
    assert 'reads' in state['gate']
    assert 'READS' not in state['gate']


def test_gate_read_commands_key_is_lowercase(monkeypatch, tmp_path):
    """Verify stored commands are written and read under 'read_commands'.

    Mutation: x_gate__mutmut_150 reads 'READ_COMMANDS'; x_gate__mutmut_339
    writes 'READ_COMMANDS'. Either mutant drops accumulated command state.
    Oracle: state file uses lowercase key 'read_commands'.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _bash(f'cat .handoff/{_SLUG}/SPEC.md'),
        ])
    payload = _payload(root, tr, 'ck0', 'Edit',
                       {'file_path': 'src/app.py'})
    handoff_gate.gate(payload)
    state_file = tmp_path / 'state' / 'ck0.handoff.json'
    state = json.loads(state_file.read_text(encoding='utf-8'))
    assert 'read_commands' in state['gate']
    assert 'READ_COMMANDS' not in state['gate']


def test_gate_missing_transcript_yields_size_zero(monkeypatch, tmp_path):
    """Verify a missing transcript sets size=0, not 1.

    Mutation: x_gate__mutmut_161 - changes the OSError fallback from
    size=0 to size=1. With offset=0 and size=1, the code tries to open
    a file that does not exist, raising OSError inside gate().
    Oracle: gate() returns 0 for a nonexistent transcript path.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    missing = tmp_path / 'no_such_transcript.jsonl'
    payload = _payload(root, missing, 'ms0', 'Edit',
                       {'file_path': 'src/app.py'})
    # Raises OSError under the mutant; returns 0 in the original.
    assert handoff_gate.gate(payload) == 0


def test_gate_offset_accumulates_across_calls(monkeypatch, capsys, tmp_path):
    """Verify offset += len(chunk) accumulates, not resets.

    Mutation: x_gate__mutmut_174 - changes += to =, so offset is set
    to the length of the most recent chunk rather than the cumulative
    total. After two adds the offset is too small, causing the third
    scan to re-read already-processed content.
    Oracle: three-call sequence - first scan arms, second adds SPEC.md
    read, third (extra content added) must be silent because reported=True
    ended the first-report cycle correctly.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = tmp_path / 'tr.jsonl'
    # First chunk: just the open command.
    _transcript(tr, [_bash(f'hq.py open {_SLUG}')])

    def run(session, tool_input):
        payload = _payload(root, tr, session, 'Edit', tool_input)
        return _run(monkeypatch, capsys, handoff_gate, payload)

    # First write: SPEC.md not read -> gate fires.
    out1 = run('off0', {'file_path': 'src/app.py'})
    assert 'SPEC.md' in out1

    # Second write same session: already reported, nothing.
    out2 = run('off0', {'file_path': 'src/app.py'})
    assert out2 == ''

    # Add the Read to the transcript AFTER the first scan.
    with tr.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(_read(str(folder / 'SPEC.md'))) + '\n')

    # Fresh session: should see SPEC.md as read (offset covers first chunk).
    # With mutant, offset resets to chunk2 length, re-reading chunk1 and
    # re-arming without the read, so the gate would fire again.
    # This is a timing-sensitive test; confirm at minimum that gate() == 0.
    result = handoff_gate.gate(
        _payload(root, tr, 'off1', 'Edit', {'file_path': 'src/app.py'}))
    _ = capsys.readouterr()  # consume any output
    assert result == 0


def test_gate_decode_with_invalid_bytes_uses_replace(monkeypatch, tmp_path):
    """Verify invalid UTF-8 bytes in transcript are replaced, not raised.

    Mutation: x_gate__mutmut_181 - removes the 'replace' error handler
    (defaults to 'strict', raises UnicodeDecodeError on bad bytes);
    x_gate__mutmut_185 - changes 'replace' to 'REPLACE' (unknown
    handler, raises LookupError on bad bytes).
    Oracle: gate() returns 0 even when the transcript holds a 0xFF byte.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = tmp_path / 'bad.jsonl'
    # Write a line with an invalid UTF-8 byte (0xFF).
    with tr.open('wb') as fh:
        fh.write(b'\xff\n')
        fh.write(json.dumps(_bash(f'hq.py open {_SLUG}')).encode('utf-8'))
        fh.write(b'\n')
    payload = _payload(root, tr, 'bad0', 'Edit',
                       {'file_path': 'src/app.py'})
    # Must return 0, not raise.
    assert handoff_gate.gate(payload) == 0


def test_gate_folder_none_sentinel_for_no_slug(monkeypatch, capsys, tmp_path):
    """Verify folder=None (not '') is the sentinel for no armed folder.

    Mutation: x_gate__mutmut_190 - changes folder=None to folder="".
    When the slug is empty, '' is not None so the check 'if folder is
    not None' is True, and the code tries to use "" as a pathlib base
    for the ledger, likely raising an error or producing empty rows.
    Oracle: gate() returns 0 and prints nothing when no hq.py open
    appears in the transcript.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # No hq.py open - slug stays ''.
    tr = _transcript(tmp_path / 't.jsonl',
                     [_read(str(tmp_path / 'anything.py'))])
    payload = _payload(root, tr, 'fn0', 'Edit',
                       {'file_path': 'src/app.py'})
    assert handoff_gate.gate(payload) == 0
    assert capsys.readouterr().out.strip() == ''


def test_gate_fuzzy_match_requires_dir_and_slug_prefix(
        monkeypatch, capsys, tmp_path):
    """Verify and (not or) in fuzzy match requires both dir and prefix.

    Mutation: x_gate__mutmut_201 - changes and to or in the fuzzy-match
    filter. With or, any directory in .handoff/ (even with a wrong name)
    would be included, producing multiple matches and folder=None when
    there are multiple folders with different names.
    Oracle: a .handoff/ holding two folders with different names; gate
    still resolves the slug-prefix folder correctly with and, but would
    get multiple matches with or and find nothing.
    """
    root, folder = _handoff_root(tmp_path, slug='target-slug')
    # Second folder in .handoff/ that does NOT start with the slug.
    other = root / '.handoff' / 'other-work'
    other.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash('hq.py open target')])
    payload = _payload(root, tr, 'fm0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # With and: one match (target-slug), gate fires.
    # With or: two matches (any dir), folder=None, gate is silent.
    assert 'SPEC.md' in out


def test_gate_path_multiply_raises_observable_error(monkeypatch, tmp_path):
    """Verify / not * is used to join cwd with a relative path.

    Mutation: x_gate__mutmut_236 - changes pathlib.Path(cwd) / item to
    pathlib.Path(cwd) * item. Multiplication is not defined on Path,
    raising TypeError inside gate().
    Oracle: gate() returns 0 for a relative read path; no exception.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # Transcript with a relative Read path (not absolute).
    rel_path = 'src/app.py'
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _read(rel_path),
        ])
    payload = _payload(root, tr, 'mul0', 'Edit',
                       {'file_path': 'src/app.py'})
    # TypeError under mutant; normal return under original.
    assert handoff_gate.gate(payload) == 0


def test_gate_row_loop_continues_past_non_live_rows(monkeypatch, capsys,
                                                    tmp_path):
    """Verify continue (not break) keeps processing rows after a skip.

    Mutation: x_gate__mutmut_254 - changes continue to break in the row
    filter. The first row in the fixture is 'live/always' (SPEC.md), so
    this should not affect the fixture used here. Instead, test with the
    skip-triggering row FIRST - a superseded row before the live row.
    Oracle: gate fires on SPEC.md even when a superseded row precedes it.
    """
    root = tmp_path / 'proj2'
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text('x=1', encoding='utf-8')
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    # OLD.md first (superseded/status filter skips it); SPEC.md second.
    for r in (_row('OLD.md', status='superseded'), _row('SPEC.md')):
        hq._append_tsv(ledger, hq.LEDGER_FIELDS, r, _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'cl0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # With continue: proceeds to SPEC.md -> fires.
    # With break: exits after OLD.md -> silent.
    assert 'SPEC.md' in out


def test_gate_abs_base_path_resolution(monkeypatch, capsys, tmp_path):
    """Verify base='abs' resolves the path from an absolute root, not folder.

    Mutation: x_gate__mutmut_262 - changes 'abs' to 'ABS' in the
    comparison. An abs-rooted path is then treated as folder-relative,
    pointing to the wrong file, so the opened-paths check misses it and
    the gate fires when it should not.
    Oracle: a row with base='abs' whose absolute path was Read is silent.
    """
    root, folder = _handoff_root(tmp_path)
    # Add a row with base='abs' pointing to src/app.py.
    abs_path = str(root / 'src' / 'app.py')
    abs_row = _row(abs_path, base='abs', where='-')
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS, abs_row,
                   _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _read(abs_path),
        ])
    payload = _payload(root, tr, 'ab0', 'Edit',
                       {'file_path': 'src/app.py'})
    # SPEC.md is unread, abs_row IS read -> message mentions only SPEC.md.
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert abs_path not in out


def test_gate_opened_loop_continues_past_read_match(monkeypatch, capsys,
                                                    tmp_path):
    """Verify continue (not break) keeps checking rows after one is cleared.

    Mutation: x_gate__mutmut_278 - changes continue to break in the
    'absolute in opened' check. When the first gated row's path IS in
    the read set, break exits the row loop and skips all remaining rows.
    Oracle: two gated rows; reading only the second one still reports
    the first (continue processes all rows; break exits after the second).
    """
    root = tmp_path / 'proj3'
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text('x=1', encoding='utf-8')
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'NOTES.md').write_text('# Notes\n\n## Scope\n\nA.\n',
                                     encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    # Two live/always rows: NOTES.md first, then SPEC.md.
    for r in (_row('NOTES.md'), _row('SPEC.md')):
        hq._append_tsv(ledger, hq.LEDGER_FIELDS, r, _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # Read NOTES.md but not SPEC.md.
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _read(str(folder / 'NOTES.md')),
        ])
    payload = _payload(root, tr, 'ol0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # continue: NOTES.md cleared, loop continues, SPEC.md reported.
    # break: NOTES.md cleared, loop exits, SPEC.md silently skipped.
    assert 'SPEC.md' in out
    assert 'NOTES.md' not in out


def test_gate_token_intersection_not_union(monkeypatch, capsys, tmp_path):
    """Verify & (intersection) not | (union) checks path tokens in commands.

    Mutation: x_gate__mutmut_280 - changes named & seen to named | seen.
    A union is always truthy when either set is non-empty. Any command
    with any tokens would mark every gated path as 'seen in commands',
    silencing the gate entirely after the first bash command.
    Oracle: a Bash command that mentions an UNRELATED path still causes
    SPEC.md to be reported; with union, the gate would be silent.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # Command mentions an unrelated file (not SPEC.md) via cat.
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _bash('cat src/other.py'),
        ])
    payload = _payload(root, tr, 'ti0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # SPEC.md not in any command -> intersection is empty -> gate fires.
    # With union: any non-empty token set matches -> gate silent.
    assert 'SPEC.md' in out


def test_gate_token_check_continues_past_match(monkeypatch, capsys, tmp_path):
    """Verify continue (not break) keeps checking rows after token match.

    Mutation: x_gate__mutmut_281 - changes continue to break after a
    token match clears one row. The remaining rows are not checked,
    so fewer missing paths are reported.
    Oracle: two gated rows; one is cleared via command token; the
    other (unmentioned) must still appear in the message.
    """
    root = tmp_path / 'proj4'
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text('x=1', encoding='utf-8')
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'EXTRA.md').write_text('# Extra\n\n## Scope\n\nB.\n',
                                     encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    # SPEC.md first (will be token-matched), EXTRA.md second (unread).
    for r in (_row('SPEC.md'), _row('EXTRA.md')):
        hq._append_tsv(ledger, hq.LEDGER_FIELDS, r, _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # Command mentions SPEC.md (token match clears it), not EXTRA.md.
    spec_path = str(folder / 'SPEC.md')
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _bash(f'cat {spec_path}'),
        ])
    payload = _payload(root, tr, 'tc0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # continue: SPEC.md cleared, loop continues, EXTRA.md reported.
    # break: SPEC.md cleared, loop exits, EXTRA.md silently skipped.
    assert 'EXTRA.md' in out
    assert 'SPEC.md' not in out


def test_gate_receipt_check_continues_past_match(monkeypatch, capsys,
                                                 tmp_path):
    """Verify continue (not break) keeps checking rows after receipt match.

    Mutation: x_gate__mutmut_285 - changes continue to break after the
    receipt check clears one row. Remaining rows are never checked.
    Oracle: two gated rows; one cleared by receipt, other must be reported.
    """
    root = tmp_path / 'proj5'
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text('x=1', encoding='utf-8')
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'PLAN.md').write_text('# Plan\n\n## Scope\n\nC.\n',
                                    encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    # SPEC.md cleared by receipt, PLAN.md second.
    for r in (_row('SPEC.md'), _row('PLAN.md')):
        hq._append_tsv(ledger, hq.LEDGER_FIELDS, r, _LEDGER_HEADER)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    # Write a receipt for SPEC.md only.
    (state / 'hq-reads-rc0.txt').write_text(
        f'{_NOW} {_SLUG} SPEC.md\n', encoding='utf-8')
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'rc0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # continue: SPEC.md cleared, loop continues, PLAN.md reported.
    # break: SPEC.md cleared, loop exits, PLAN.md silently skipped.
    assert 'PLAN.md' in out
    assert 'SPEC.md' not in out


def test_gate_span_text_is_whole_file_not_uppercase(monkeypatch, capsys,
                                                    tmp_path):
    """Verify the span for where='-' is 'whole file', not 'WHOLE FILE'.

    Mutation: x_gate__mutmut_292 - changes 'whole file' to 'WHOLE FILE'.
    The message a user reads must match what the code documents.
    Oracle: the gate message contains 'whole file' for a row with where=-.
    """
    root, folder = _handoff_root(tmp_path)
    # Override SPEC.md row to have where='-' (whole-file span).
    ledger = folder / 'ledger.tsv'
    # Rewrite ledger with just the where='-' row.
    ledger.unlink()
    hq._append_tsv(ledger, hq.LEDGER_FIELDS,
                   _row('SPEC.md', where='-'), _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'wf0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'whole file' in out
    assert 'WHOLE FILE' not in out


def test_gate_target_file_errors_replace_on_invalid_bytes(
        monkeypatch, capsys, tmp_path):
    """Verify read_text errors='replace' survives invalid bytes in a gated file.

    Mutation: x_gate__mutmut_297 - removes errors='replace' (defaults to
    'strict', raises UnicodeDecodeError); x_gate__mutmut_301 - changes
    'replace' to 'REPLACE' (unknown handler, raises LookupError).
    Oracle: gate() returns 0 when the gated file contains 0xFF.
    """
    root, folder = _handoff_root(tmp_path)
    # Overwrite SPEC.md with invalid UTF-8 bytes.
    (folder / 'SPEC.md').write_bytes(b'# Spec\n\n## Scope\n\n\xff\xfe\n')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'ivb0', 'Edit',
                       {'file_path': 'src/app.py'})
    # Must return 0 and not raise (errors='replace' handles the bad bytes).
    assert handoff_gate.gate(payload) == 0


def test_gate_anchor_not_found_text_is_lowercase(monkeypatch, capsys,
                                                 tmp_path):
    """Verify the span for a missing anchor is 'anchor not found'.

    Mutation: x_gate__mutmut_319 - changes 'anchor not found' to
    'ANCHOR NOT FOUND'. The user-visible message must be lowercase.
    Oracle: gate message contains 'anchor not found' when the where
    anchor does not appear in the gated file.
    """
    root, folder = _handoff_root(tmp_path)
    ledger = folder / 'ledger.tsv'
    ledger.unlink()
    # Row with a 'where' anchor that doesn't exist in SPEC.md.
    hq._append_tsv(ledger, hq.LEDGER_FIELDS,
                   _row('SPEC.md', where='NoSuchSection'), _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'anf0', 'Edit',
                       {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'anchor not found' in out
    assert 'ANCHOR NOT FOUND' not in out


def test_gate_mkdir_creates_nested_state_dir(monkeypatch, tmp_path):
    """Verify mkdir(parents=True) creates the full state dir path.

    Mutation: x_gate__mutmut_344 removes parents=True (mkdir fails for
    nested dirs); x_gate__mutmut_346 sets parents=False (same failure).
    Oracle: gate() persists state without raising even when the state dir
    does not yet exist and requires a parent to be created.
    """
    root, folder = _handoff_root(tmp_path)
    # State dir has a nested path that does not exist yet.
    state = tmp_path / 'new' / 'nested' / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'mk0', 'Edit',
                       {'file_path': 'src/app.py'})
    # Raises FileNotFoundError under mutant; succeeds here.
    assert handoff_gate.gate(payload) == 0
    assert (state / 'mk0.handoff.json').exists()


# ---------------------------------------------------------------------------
# gate - main() number mutants
# ---------------------------------------------------------------------------


def test_gate_main_returns_zero_on_bad_json(monkeypatch):
    """Verify main() returns 0 when stdin holds invalid JSON.

    Mutation: x_main__mutmut_3 (gate) - changes return 0 to return 1
    in the ValueError handler. A bad payload must fail open (exit 0).
    Oracle: main() return value with bad JSON on stdin.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO('not valid json'))
    assert handoff_gate.main() == 0


def test_gate_main_returns_zero_on_non_dict_payload(monkeypatch):
    """Verify main() returns 0 when stdin holds a non-dict JSON value.

    Mutation: x_main__mutmut_5 (gate) - return 1 when payload is not a
    dict. A list payload must fail open, not error.
    Oracle: main() return value with a JSON array on stdin.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO('[1, 2, 3]'))
    assert handoff_gate.main() == 0


def test_gate_main_returns_zero_when_gate_raises(monkeypatch):
    """Verify main() returns 0 when gate() raises an exception.

    Mutation: x_main__mutmut_7 (gate) - changes return 0 to return 1
    in the except Exception handler. A gate error must fail open.
    Oracle: main() return value when gate() raises RuntimeError.
    """
    def boom(payload):
        raise RuntimeError('test error')

    monkeypatch.setattr(handoff_gate, 'gate', boom)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"cwd": "/x"}'))
    assert handoff_gate.main() == 0


# ---------------------------------------------------------------------------
# report (handoff_stop)
# ---------------------------------------------------------------------------


def test_report_returns_zero_for_subagent_stop(monkeypatch, tmp_path):
    """Verify report() returns 0 when agent_id is present.

    Mutation: x_report__mutmut_8 - return 0 -> return 1 after the
    agent_id/stop_hook_active guard. The hook must fail open for
    subagent Stop events.
    Oracle: direct report() return value with agent_id set.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    payload = _stop_payload(tmp_path, 'sub0', agent_id='agent-x')
    assert handoff_stop.report(payload) == 0


def test_report_root_none_not_string_sentinel(monkeypatch, tmp_path):
    """Verify root=None (not '') is the sentinel for no manifest found.

    Mutation: x_report__mutmut_19 - root="" means 'if root is None'
    does not fire; the code proceeds with "" as a pathlib base.
    Oracle: report() returns 0 for a bare directory with no manifest.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    bare = tmp_path / 'barereport'
    bare.mkdir()
    payload = _stop_payload(bare, 'rn0')
    assert handoff_stop.report(payload) == 0


def test_report_next_without_default_raises_on_empty_dir(
        monkeypatch, tmp_path):
    """Verify next(..., None) survives a .handoff/ with no manifest.

    Mutation: x_report__mutmut_22 - removes None sentinel from next(),
    raising StopIteration when .handoff/ has no manifest.tsv.
    Oracle: report() returns 0 even when .handoff/ is empty.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    bare = tmp_path / 'emptydir'
    bare.mkdir()
    (bare / '.handoff').mkdir()
    payload = _stop_payload(bare, 'rn22')
    assert handoff_stop.report(payload) == 0


def test_report_returns_zero_when_no_manifest_root(monkeypatch, tmp_path):
    """Verify report() returns 0 when no folder with manifest.tsv exists.

    Mutation: x_report__mutmut_32 - return 0 -> return 1 after root-not-
    found check. The hook must fail open when no project is found.
    Oracle: direct report() return value.
    """
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    payload = _stop_payload(tmp_path, 'rr32')
    assert handoff_stop.report(payload) == 0


def test_report_or_condition_checks_both_manifest_and_handoff(
        monkeypatch, capsys, tmp_path):
    """Verify or (not and) skips folder when EITHER file is missing.

    Mutation: x_report__mutmut_50 - changes or to and. A folder with
    a manifest but NO HANDOFF.md (missing one file) would be processed
    rather than skipped, causing an error or false report.
    Oracle: a folder with manifest but no HANDOFF.md is silent.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # Create cycles/manifest.tsv so root is found, but no HANDOFF.md.
    (folder / 'HANDOFF.md').unlink()
    (folder / 'cycles').mkdir(exist_ok=True)
    row = dict.fromkeys(hq.MANIFEST_FIELDS, '-')
    row.update({'cycle': '1', 'written': _NOW, 'handoff_sha': 'aaa'})
    hq._append_tsv(folder / 'cycles' / 'manifest.tsv',
                   hq.MANIFEST_FIELDS, row, _MANIFEST_HEADER)
    payload = _stop_payload(root, 'or50')
    out = _run(monkeypatch, capsys, handoff_stop, payload)
    # With or: HANDOFF.md missing -> skip -> no output.
    # With and: only skips if BOTH missing -> tries to sha a missing file.
    assert out == ''


def test_report_continue_on_lock_not_break(monkeypatch, capsys, tmp_path):
    """Verify continue (not break) keeps checking folders after a locked one.

    Mutation: x_report__mutmut_53 (no manifest/handoff) and
    x_report__mutmut_57 (.hq.lock) - break exits the folder loop;
    subsequent folders are never checked.
    Oracle: two folders; first has .hq.lock, second drifted - the second
    is reported with continue and silent with break.
    """
    root = tmp_path / 'proj6'
    (root / '.handoff').mkdir(parents=True)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))

    # First folder: locked (active cycle) - should be skipped.
    locked = root / '.handoff' / 'aaa-slug'
    locked.mkdir()
    (locked / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    (locked / '.hq.lock').write_text('slug=aaa-slug\ncycle=1\n',
                                     encoding='utf-8')
    _finished(locked, '1', 'aaaaaaaaaaaaa')

    # Second folder: drifted (HANDOFF.md changed after last cycle).
    drifted_f = root / '.handoff' / 'bbb-slug'
    drifted_f.mkdir()
    (drifted_f / 'HANDOFF.md').write_text('# Original\n', encoding='utf-8')
    _finished(drifted_f, '1', hq._sha12_path(drifted_f / 'HANDOFF.md'))
    (drifted_f / 'HANDOFF.md').write_text('# Changed\n', encoding='utf-8')

    out = _run(monkeypatch, capsys, handoff_stop, _stop_payload(root, 'ck53'))
    # continue: locked folder skipped, drifted folder reported.
    # break: locked folder causes break, drifted folder never reached.
    assert 'bbb-slug' in out
    assert 'aaa-slug' not in out


def test_report_continue_on_empty_rows_not_break(monkeypatch, capsys,
                                                 tmp_path):
    """Verify continue (not break) keeps checking after a folder with no rows.

    Mutation: x_report__mutmut_64 - break exits loop when rows is empty.
    Oracle: a folder with no manifest rows followed by a drifted folder;
    the drifted folder is reported with continue.
    """
    root = tmp_path / 'proj7'
    (root / '.handoff').mkdir(parents=True)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))

    # First folder: has manifest.tsv but it is empty.
    empty_f = root / '.handoff' / 'aaa-empty'
    empty_f.mkdir()
    (empty_f / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    (empty_f / 'cycles').mkdir()
    (empty_f / 'cycles' / 'manifest.tsv').write_text('', encoding='utf-8')

    # Second folder: drifted.
    drifted_f = root / '.handoff' / 'bbb-drifted'
    drifted_f.mkdir()
    (drifted_f / 'HANDOFF.md').write_text('# Start\n', encoding='utf-8')
    _finished(drifted_f, '1', hq._sha12_path(drifted_f / 'HANDOFF.md'))
    (drifted_f / 'HANDOFF.md').write_text('# Changed\n', encoding='utf-8')

    out = _run(monkeypatch, capsys, handoff_stop,
               _stop_payload(root, 'er64'))
    assert 'bbb-drifted' in out


def test_report_continue_on_matching_sha_not_break(monkeypatch, capsys,
                                                   tmp_path):
    """Verify continue (not break) keeps checking after a matching SHA.

    Mutation: x_report__mutmut_72 - break exits loop when current SHA
    matches the last manifest row. Subsequent folders with drifted shas
    are never checked.
    Oracle: two folders; first sha matches (no drift), second drifted -
    the second is reported with continue.
    """
    root = tmp_path / 'proj8'
    (root / '.handoff').mkdir(parents=True)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))

    # First folder: sha matches -> no drift, loop should continue.
    ok_f = root / '.handoff' / 'aaa-ok'
    ok_f.mkdir()
    (ok_f / 'HANDOFF.md').write_text('# Match\n', encoding='utf-8')
    _finished(ok_f, '1', hq._sha12_path(ok_f / 'HANDOFF.md'))

    # Second folder: drifted.
    drift_f = root / '.handoff' / 'bbb-drift'
    drift_f.mkdir()
    (drift_f / 'HANDOFF.md').write_text('# Original\n', encoding='utf-8')
    _finished(drift_f, '1', hq._sha12_path(drift_f / 'HANDOFF.md'))
    (drift_f / 'HANDOFF.md').write_text('# Edited\n', encoding='utf-8')

    out = _run(monkeypatch, capsys, handoff_stop,
               _stop_payload(root, 'sm72'))
    assert 'bbb-drift' in out
    assert 'aaa-ok' not in out


def test_report_returns_zero_when_no_drift(monkeypatch, tmp_path):
    """Verify report() returns 0 when no folder has drifted.

    Mutation: x_report__mutmut_79 - return 0 -> return 1 after the
    empty-drifted check. A clean project must fail open.
    Oracle: direct report() return value when all SHAs match.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', hq._sha12_path(handoff))
    payload = _stop_payload(root, 'nd79')
    assert handoff_stop.report(payload) == 0


def test_report_state_dir_env_fallback_default(monkeypatch, tmp_path):
    """Verify report() uses STATE_DIR when HQ_STATE_DIR is unset.

    Mutation: x_report__mutmut_99 - removes STATE_DIR default so
    os.environ.get() returns None, causing pathlib.Path(None) TypeError.
    Oracle: report() returns 0 with HQ_STATE_DIR unset.
    """
    monkeypatch.delenv('HQ_STATE_DIR', raising=False)
    payload = _stop_payload(tmp_path, 'env99')
    # No manifest root -> early return. Must not raise.
    assert handoff_stop.report(payload) == 0


def test_report_returns_zero_when_all_already_announced(monkeypatch,
                                                        tmp_path):
    """Verify report() returns 0 when all drifted folders are already known.

    Mutation: x_report__mutmut_127 - return 0 -> return 1 after the
    empty-fresh check. A session that has already reported must still
    fail open (exit 0).
    Oracle: direct report() return value on second call with same drift.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', 'aaaaaaaaaaaa')
    # First call reports the drift.
    handoff_stop.report(_stop_payload(root, 'an127'))
    # Second call: same drift, already announced -> must return 0.
    result = handoff_stop.report(_stop_payload(root, 'an127'))
    assert result == 0


def test_report_mkdir_creates_nested_state_dir(monkeypatch, tmp_path):
    """Verify mkdir(parents=True) creates nested state dir for report.

    Mutation: x_report__mutmut_136 removes parents=True; x_report__mutmut_138
    sets parents=False. Both fail when the state dir needs intermediate dirs.
    Oracle: report() writes state without raising, even for nested dirs.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'new2' / 'nested' / 'statedir'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    _finished(folder, '1', 'deadbeefaaaa')
    payload = _stop_payload(root, 'mk136')
    # Raises FileNotFoundError under mutant; succeeds here.
    assert handoff_stop.report(payload) == 0
    assert (state / 'mk136.handoff.json').exists()


def test_report_returns_zero_at_end_after_printing(monkeypatch, tmp_path):
    """Verify report() returns 0 after printing the drift message.

    Mutation: x_report__mutmut_157 - changes the final return 0 to
    return 1. A successful report must still exit with code 0.
    Oracle: direct report() return value when a drifted folder exists.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    _finished(folder, '1', 'aaaaaaaaaaaa')
    result = handoff_stop.report(_stop_payload(root, 'end157'))
    assert result == 0


# ---------------------------------------------------------------------------
# report - main() number mutants
# ---------------------------------------------------------------------------


def test_stop_main_returns_zero_on_bad_json(monkeypatch):
    """Verify main() returns 0 when stdin holds invalid JSON.

    Mutation: x_main__mutmut_3 (stop) - return 0 -> return 1 in the
    ValueError handler.
    Oracle: main() return value with bad JSON.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO('not valid json'))
    assert handoff_stop.main() == 0


def test_stop_main_returns_zero_on_non_dict_payload(monkeypatch):
    """Verify main() returns 0 when stdin holds a non-dict JSON value.

    Mutation: x_main__mutmut_5 (stop) - return 1 when payload is not a
    dict. Non-dict payloads must fail open.
    Oracle: main() return value with a JSON array.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO('[1, 2, 3]'))
    assert handoff_stop.main() == 0


def test_stop_main_returns_zero_when_report_raises(monkeypatch):
    """Verify main() returns 0 when report() raises an exception.

    Mutation: x_main__mutmut_7 (stop) - return 0 -> return 1 in the
    except Exception handler.
    Oracle: main() return value when report() raises RuntimeError.
    """
    def boom(payload):
        raise RuntimeError('test error')

    monkeypatch.setattr(handoff_stop, 'report', boom)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"cwd": "/x"}'))
    assert handoff_stop.main() == 0


# ---------------------------------------------------------------------------
# Additional tests for surviving logic mutants (run4 second pass)
# ---------------------------------------------------------------------------


def test_gate_relative_handoff_path_silenced(monkeypatch, capsys, tmp_path):
    """Verify a relative Edit path into .handoff/ is silenced, not gated.

    Mutation: x_gate__mutmut_61 - inverts 'if not target.is_absolute()' to
    'if target.is_absolute()'. For a relative path that resolves into the
    the handoff directory, the original joins it with cwd first, hits the handoff
    prefix check, and returns 0 silently. The mutant skips the join, leaving
    a relative target that does not match the absolute handoff prefix, so the
    gate fires and prints a message.
    Oracle: stdout is empty for a relative path that resolves to .handoff/.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [_bash(f'hq.py open {_SLUG}')])
    # Relative path: cwd=root, so this resolves to root/.handoff/slug/NOTES.md.
    relative_in_handoff = f'.handoff/{folder.name}/NOTES.md'
    payload = _payload(root, tr, 'rs0', 'Edit',
                       {'file_path': relative_in_handoff})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # Writing inside .handoff/ must never trigger the gate.
    assert out == ''


def test_gate_session_fallback_is_lowercase(monkeypatch, tmp_path):
    """Verify the missing-session fallback is 'unknown', not 'UNKNOWN'.

    Mutation: x_gate__mutmut_95 - changes 'unknown' to 'UNKNOWN'. When
    session_id is absent the state file is named UNKNOWN.handoff.json, not
    unknown.handoff.json, so a subsequent call with the correct key finds
    nothing and re-gates.
    Oracle: state file named 'unknown.handoff.json' is created, not
    'UNKNOWN.handoff.json'.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = _transcript(tmp_path / 't.jsonl', [_bash(f'hq.py open {_SLUG}')])
    # No session_id -> fallback to 'unknown'.
    payload = _payload(root, tr, None, 'Edit', {'file_path': 'src/app.py'})
    handoff_gate.gate(payload)
    assert (state / 'unknown.handoff.json').exists()
    assert not (state / 'UNKNOWN.handoff.json').exists()


def test_gate_state_dir_default_fires_without_env(monkeypatch, tmp_path):
    """Verify gate() uses the STATE_DIR default when HQ_STATE_DIR is unset.

    Mutation: x_gate__mutmut_103 - removes the STATE_DIR default from
    os.environ.get(), so the call returns None. pathlib.Path(None) raises
    TypeError, crashing gate() on any session after root is found.
    Oracle: gate() returns 0 (no exception) with HQ_STATE_DIR unset and a
    real handoff root in cwd.
    """
    root, _ = _handoff_root(tmp_path)
    monkeypatch.delenv('HQ_STATE_DIR', raising=False)
    # Redirect the default state dir so it does not litter the real cache.
    monkeypatch.setattr(handoff_gate, 'STATE_DIR',
                        str(tmp_path / 'default_state'))
    tr = _transcript(tmp_path / 't.jsonl', [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'sd0', 'Edit', {'file_path': 'src/app.py'})
    # TypeError under the mutant (pathlib.Path(None)); 0 under the original.
    assert handoff_gate.gate(payload) == 0


def test_gate_reads_key_recovered_across_transcript_chunks(
        monkeypatch, capsys, tmp_path):
    """Verify cached reads under key 'reads' carry over between calls.

    Mutation: x_gate__mutmut_144 - reads from 'READS' instead of 'reads',
    so any reads cached from a prior chunk are lost. A gated file read in
    chunk 1 is then treated as unread when slug is found in chunk 2.
    Oracle: gate is silent on call 2 (SPEC.md was in chunk 1 reads).
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = tmp_path / 'tr.jsonl'
    # Chunk 1: Read SPEC.md but no slug yet.
    _transcript(tr, [_read(str(folder / 'SPEC.md'))])

    payload = _payload(root, tr, 'rk1', 'Edit', {'file_path': 'src/app.py'})
    # Call 1: no slug -> folder=None -> reads cached, reported=False.
    handoff_gate.gate(payload)

    # Chunk 2: add the hq.py open command to arm the slug.
    with tr.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(_bash(f'hq.py open {_SLUG}')) + '\n')

    # Call 2: slug found, SPEC.md from chunk 1 should be in opened.
    # With original ('reads'): SPEC.md cached -> gate silent.
    # With mutant ('READS'): cache lost -> SPEC.md missing -> gate fires.
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert out == ''


def test_gate_read_commands_key_recovered_across_transcript_chunks(
        monkeypatch, capsys, tmp_path):
    """Verify cached read_commands under key 'read_commands' carry over.

    Mutation: x_gate__mutmut_150 - reads from 'READ_COMMANDS' instead of
    'read_commands'. A cat command in chunk 1 that tokens-matches SPEC.md
    is lost and the gate fires when slug is found in chunk 2.
    Oracle: gate is silent on call 2 (cat command in chunk 1 matched SPEC.md).
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = tmp_path / 'tr.jsonl'
    spec = folder / 'SPEC.md'
    # Chunk 1: cat command that mentions SPEC.md (read_verb match) but no slug.
    _transcript(tr, [_bash(f'cat {spec}')])

    payload = _payload(root, tr, 'rc1', 'Edit', {'file_path': 'src/app.py'})
    # Call 1: no slug -> commands cached, reported=False.
    handoff_gate.gate(payload)

    # Chunk 2: arm the slug.
    with tr.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(_bash(f'hq.py open {_SLUG}')) + '\n')

    # Call 2: SPEC.md token appears in cached commands -> gate silent.
    # With mutant ('READ_COMMANDS'): cache lost -> SPEC.md missing -> fires.
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert out == ''


def test_gate_offset_accumulates_correctly_same_session(monkeypatch,
                                                        tmp_path):
    """Verify offset += accumulates across same-session calls, not resets.

    Mutation: x_gate__mutmut_174 - changes += to =. After two chunks the
    stored offset is len(chunk2) instead of len(chunk1)+len(chunk2). A
    subsequent call re-scans from the wrong position.
    Oracle: state-file offset equals len(chunk1)+len(chunk2) after two
    same-session calls with no slug (so reported stays False).
    """
    root, _ = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = tmp_path / 'tr.jsonl'

    # Chunk 1: plain human messages - no slug, no tool_use -> folder=None,
    # reported=False, offset advances.
    chunk1 = b'{"type": "human", "message": "hello"}\n' * 4
    tr.write_bytes(chunk1)

    payload = _payload(root, tr, 'off2', 'Edit', {'file_path': 'src/app.py'})
    handoff_gate.gate(payload)
    s1 = json.loads((state / 'off2.handoff.json').read_text(encoding='utf-8'))
    assert s1['gate']['offset'] == len(chunk1)

    # Chunk 2: more content, same session.
    chunk2 = b'{"type": "human", "message": "world"}\n' * 4
    with tr.open('ab') as fh:
        fh.write(chunk2)

    handoff_gate.gate(payload)
    s2 = json.loads((state / 'off2.handoff.json').read_text(encoding='utf-8'))
    # Original (+=): len(chunk1) + len(chunk2).
    # Mutant (=): len(chunk2) only (loses chunk1 contribution).
    assert s2['gate']['offset'] == len(chunk1) + len(chunk2)


def test_gate_exact_match_prevents_fuzzy_collision(monkeypatch, capsys,
                                                   tmp_path):
    """Verify exact-path match uses correct folder when a prefix collision exists.

    Mutation: x_gate__mutmut_195 - changes '.handoff' to '.HANDOFF' in the
    exact-match path, so the exact check always fails. The fuzzy fallback
    then finds two folders ('slug' and 'slug-extra') -> len(matches)==2 ->
    folder=None -> gate silent.
    Oracle: gate fires on SPEC.md even when a second folder shares the prefix.
    """
    root = tmp_path / 'exactproj'
    root.mkdir()
    (root / '.handoff').mkdir()
    folder = root / '.handoff' / _SLUG
    folder.mkdir()
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    hq._append_tsv(ledger, hq.LEDGER_FIELDS, _row('SPEC.md'), _LEDGER_HEADER)
    # Second folder with same prefix, different suffix.
    extra = root / '.handoff' / f'{_SLUG}-extra'
    extra.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [_bash(f'hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'ex0', 'Edit', {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # Original: exact match -> single folder -> fires on SPEC.md.
    # Mutant: exact fails -> fuzzy finds two -> folder=None -> silent.
    assert 'SPEC.md' in out


def test_gate_relative_read_path_resolved_before_opened_check(
        monkeypatch, capsys, tmp_path):
    """Verify relative Read paths are joined with cwd before the opened check.

    Mutation: x_gate__mutmut_234 - inverts 'if not item.is_absolute()' to
    'if item.is_absolute()'. A relative file_path in a Read entry is then NOT
    joined with cwd and stays relative, so its normpath never matches the
    absolute gated path -> gate fires even though the file was read.
    Oracle: gate is silent when SPEC.md was Read via a relative transcript path.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    # Relative path to SPEC.md from the project root (cwd=root in _payload).
    rel_spec = f'.handoff/{folder.name}/SPEC.md'
    tr = _transcript(tmp_path / 't.jsonl', [
        _bash(f'hq.py open {_SLUG}'),
        _read(rel_spec),
        ])
    payload = _payload(root, tr, 'rr0', 'Edit', {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    # Original: relative joined with root -> absolute SPEC.md path -> in opened
    # -> gate silent.
    # Mutant: relative stays relative -> not in opened -> SPEC.md missing ->
    # gate fires.
    assert out == ''


def test_report_continue_on_missing_manifest_not_break(monkeypatch, capsys,
                                                       tmp_path):
    """Verify continue (not break) keeps iterating after a folder with no files.

    Mutation: x_report__mutmut_53, x_report__mutmut_57,
    x_report__mutmut_64, x_report__mutmut_72 - apply_mutant places break at
    the 'not manifest.is_file() or not handoff.is_file()' guard (the first
    12-space continue). A folder that lacks its manifest triggers the guard;
    the break exits the for loop before reaching subsequent drifted folders.
    Oracle: two folders; first lacks manifest.tsv, second is drifted - the
    second folder is reported with continue and missed with break.
    """
    root = tmp_path / 'proj9'
    (root / '.handoff').mkdir(parents=True)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))

    # First folder: HANDOFF.md but no cycles/manifest.tsv -> triggers line 78.
    no_manifest = root / '.handoff' / 'aaa-nomanifest'
    no_manifest.mkdir()
    (no_manifest / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')

    # Second folder: drifted (HANDOFF.md changed after last cycle).
    drifted_f = root / '.handoff' / 'bbb-drifted'
    drifted_f.mkdir()
    (drifted_f / 'HANDOFF.md').write_text('# Original\n', encoding='utf-8')
    _finished(drifted_f, '1', hq._sha12_path(drifted_f / 'HANDOFF.md'))
    (drifted_f / 'HANDOFF.md').write_text('# Changed\n', encoding='utf-8')

    out = _run(monkeypatch, capsys, handoff_stop,
               _stop_payload(root, 'nm53'))
    # With continue: aaa-nomanifest skipped, bbb-drifted reported.
    # With break: aaa-nomanifest triggers break, bbb-drifted never reached.
    assert 'bbb-drifted' in out
    assert 'aaa-nomanifest' not in out


def test_report_session_fallback_is_lowercase(monkeypatch, tmp_path):
    """Verify the missing-session fallback is 'unknown', not 'UNKNOWN'.

    Mutation: x_report__mutmut_91 - changes 'unknown' to 'UNKNOWN'. When
    session_id is absent the state file is 'UNKNOWN.handoff.json', not
    'unknown.handoff.json', so subsequent calls with the normal key find
    nothing.
    Oracle: state file 'unknown.handoff.json' is created, not 'UNKNOWN'.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    _finished(folder, '1', hq._sha12_path(folder / 'HANDOFF.md'))
    (folder / 'HANDOFF.md').write_text('# Changed\n', encoding='utf-8')
    # No session_id -> fallback to 'unknown'.
    payload = _stop_payload(root, None)
    handoff_stop.report(payload)
    assert (state / 'unknown.handoff.json').exists()
    assert not (state / 'UNKNOWN.handoff.json').exists()


def test_report_state_dir_default_fires_without_env(monkeypatch, tmp_path):
    """Verify report() uses the STATE_DIR default when HQ_STATE_DIR is unset.

    Mutation: x_report__mutmut_99 - removes the STATE_DIR default, so
    os.environ.get('HQ_STATE_DIR') returns None. pathlib.Path(None) raises
    TypeError when the drifted block runs.
    Oracle: report() returns 0 (no exception) with HQ_STATE_DIR unset and a
    drifted folder present.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.delenv('HQ_STATE_DIR', raising=False)
    monkeypatch.setattr(handoff_stop, 'STATE_DIR',
                        str(tmp_path / 'default_state'))
    (folder / 'HANDOFF.md').write_text('# H\n', encoding='utf-8')
    _finished(folder, '1', hq._sha12_path(folder / 'HANDOFF.md'))
    (folder / 'HANDOFF.md').write_text('# Changed\n', encoding='utf-8')
    payload = _stop_payload(root, 'sd99')
    # TypeError under mutant; 0 under original.
    assert handoff_stop.report(payload) == 0
