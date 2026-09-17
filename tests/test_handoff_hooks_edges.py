"""Edge-case tests for handoff_gate.py and handoff_stop.py."""

import io
import json
import os
import pathlib
import sys
import types
from typing import Any

import pytest
from scripts import handoff_gate, handoff_stop

from bin import hq

_SLUG = 'test-proj'
_NOW = '2026-09-09T12:00:00'
_LEDGER_HEADER = '\t'.join(hq.LEDGER_FIELDS)
_MANIFEST_HEADER = '\t'.join(hq.MANIFEST_FIELDS)


def _row(path: str, **overrides: str) -> dict:
    """Build one ledger row with live/always defaults.

    Parameters
    ----------
    path : str
        Value for the ``path`` field.
    **overrides : str
        Field values replacing the defaults.

    Returns
    -------
    dict
        A row carrying every field in ``hq.LEDGER_FIELDS``.
    """
    row = {
        'cycle': '1', 'ts': _NOW, 'path': path, 'base': 'folder',
        'kind': 'spec', 'status': 'live', 'read_before': 'always',
        'successor': '-', 'where': '-', 'sha12': '-', 'lines': '10',
        'reason': '-', 'label': '-',
        }
    row.update(overrides)
    return row


def _handoff_root(
    tmp_path: pathlib.Path, slug: str = _SLUG,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a project root with one handoff folder and a gated ledger row.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary directory.
    slug : str, default _SLUG
        Handoff folder name under ``.handoff/``.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        The project root and the handoff folder.
    """
    root = tmp_path / 'proj'
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)
    (root / 'src').mkdir(exist_ok=True)
    (root / 'src' / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'HANDOFF.md').write_text(
        '# Handoff\n\n## Task\n\nWork.\n', encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    hq._append_tsv(ledger, hq.LEDGER_FIELDS, _row('SPEC.md'), _LEDGER_HEADER)
    return root, folder


def _finished(folder: pathlib.Path, cycle: int | str, sha: str) -> None:
    """Append one finished-cycle row to the manifest.

    Parameters
    ----------
    folder : pathlib.Path
        The handoff folder holding ``cycles/manifest.tsv``.
    cycle : str
        Cycle number the row records.
    sha : str
        HANDOFF.md digest the cycle finished on.

    Returns
    -------
    None
    """
    (folder / 'cycles').mkdir(exist_ok=True)
    row = dict.fromkeys(hq.MANIFEST_FIELDS, '-')
    row.update({'cycle': cycle, 'written': _NOW, 'handoff_sha': sha})
    hq._append_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS,
                   row, _MANIFEST_HEADER)


def _bash(command: str) -> dict:
    """Return one transcript line holding a Bash tool call.
    """
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Bash',
         'input': {'command': command}}]}}


def _transcript(path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    """Write a JSONL transcript and return its path.
    """
    path.write_text(
        '\n'.join(json.dumps(e) for e in entries) + '\n',
        encoding='utf-8')
    return path


def _gate_payload(
    root: pathlib.Path,
    transcript: pathlib.Path,
    session: str,
    tool_name: str,
    tool_input: dict,
) -> dict:
    """Build a PreToolUse payload of the shape Claude Code sends.

    Parameters
    ----------
    root : pathlib.Path
        Directory the tool call runs in, sent as ``cwd``.
    transcript : pathlib.Path
        Path to the session transcript.
    session : str
        Session id.
    tool_name : str
        Tool being called.
    tool_input : dict
        The tool's own input block.

    Returns
    -------
    dict
        A payload ready to hand to the gate over stdin.
    """
    return {
        'hook_event_name': 'PreToolUse', 'cwd': str(root),
        'session_id': session, 'transcript_path': str(transcript),
        'tool_name': tool_name, 'tool_input': tool_input,
        }


def _stop_payload(root: pathlib.Path, session: str, **extra: Any) -> dict:
    """Build a Stop payload of the shape Claude Code sends.
    """
    payload = {'hook_event_name': 'Stop', 'cwd': str(root),
               'session_id': session, 'stop_hook_active': False}
    payload.update(extra)
    return payload


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    module: types.ModuleType,
    payload: dict,
) -> str:
    """Drive a hook's main() over one payload and return its stdout.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    module.main()
    return capsys.readouterr().out.strip()


# --- The Stop hook and an unreadable file ---


def test_stop_silent_on_unreadable_handoff(monkeypatch, capsys, tmp_path):
    """Verify an unreadable HANDOFF.md is not reported as a hand edit.

    Mutation: treating the '-' sha from _sha12_path on OSError as a real
    digest mismatch, which fires a false alarm on any chmod-000 file.
    Oracle: stdout must be empty; the only evidence is an OSError, not a
    hash difference between the file's content and the recorded digest.
    """
    if os.geteuid() == 0:
        pytest.skip('root bypasses chmod')
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', hq._sha12_path(handoff))
    handoff.chmod(0o000)
    try:
        out = _run(monkeypatch, capsys, handoff_stop, _stop_payload(root, 'U1'))
        assert out == ''
    finally:
        handoff.chmod(0o644)


# --- The gate's write-target exemption ---


def test_gate_fires_when_folder_precedes_redirect(monkeypatch, capsys, tmp_path):
    """Verify a read-from-folder then redirect-elsewhere trips the gate.

    Mutation: exempting any command that mentions the folder path, so
    'cat .handoff/<slug>/SPEC.md > src/out.txt' is silenced although
    SPEC.md is gated and the redirect target is outside the folder.
    Oracle: stdout must name SPEC.md; the folder path precedes '>' so the
    write target is not in the folder.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(
        root, tr, 'V1', 'Bash',
        {'command': f'cat .handoff/{_SLUG}/SPEC.md > src/out.txt'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'SPEC.md' in out


def test_gate_redirect_into_folder_stays_exempt(monkeypatch, capsys, tmp_path):
    """Verify a redirect whose target is inside the folder stays silent.

    Mutation: removing the write-target check so every command mentioning
    the folder trips the gate, including 'echo x > .handoff/<slug>/notes.md'
    which writes into the managed folder.
    Oracle: stdout must be empty; the redirect target is inside the folder.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(
        root, tr, 'V2', 'Bash',
        {'command': f'echo x > .handoff/{_SLUG}/notes.md'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert out == ''


def test_gate_sed_inplace_into_folder_stays_exempt(monkeypatch, capsys,
                                                   tmp_path):
    """Verify sed -i targeting the folder stays silent.

    Mutation: removing the write-target check so sed -i on a folder file
    trips the gate, blocking in-place edits of managed files.
    Oracle: stdout must be empty; sed -i writes to the folder path.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(
        root, tr, 'V3', 'Bash',
        {'command': f'sed -i s/a/b/ .handoff/{_SLUG}/notes.md'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert out == ''


def test_gate_tee_into_folder_stays_exempt(monkeypatch, capsys, tmp_path):
    """Verify tee targeting the folder stays silent.

    Mutation: removing the write-target check so 'echo x | tee
    .handoff/<slug>/notes.md' trips the gate, blocking tee writes into
    the managed folder.
    Oracle: stdout must be empty; tee's argument is inside the folder.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(
        root, tr, 'V4', 'Bash',
        {'command': f'echo x | tee .handoff/{_SLUG}/notes.md'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert out == ''


def test_gate_relative_target_inside_folder_stays_exempt(
        monkeypatch, capsys, tmp_path):
    """A bare-name target written from inside the folder stays silent.

    Mutation: testing the command text alone for the `.handoff/<slug>/`
    prefix, so a target that reaches the folder only through cwd
    reports; or resolving redirect targets alone, so the sed -i and tee
    spellings report.
    Oracle: stdout empty for three in-folder spellings run from a
    subdirectory of the folder, then stdout naming SPEC.md for the repo
    write that follows in the same session, so no exempt write spent
    the one-shot.
    """
    root, folder = _handoff_root(tmp_path)
    inside = folder / 'evidence' / 'run-1'
    inside.mkdir(parents=True)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    spellings = [
        ('python probe.py run > smoke-A-0.log 2>&1 &\n'
         'python probe.py run > smoke-C-0.log 2>&1 &\nwait'),
        'sed -i s/a/b/ notes.md',
        'echo x | tee -a notes.md',
        ]
    for command in spellings:
        payload = _gate_payload(inside, tr, 'V9', 'Bash', {'command': command})
        assert _run(monkeypatch, capsys, handoff_gate, payload) == '', command
    payload = _gate_payload(root, tr, 'V9', 'Bash',
                            {'command': 'echo x > src/out.txt'})
    assert 'SPEC.md' in _run(monkeypatch, capsys, handoff_gate, payload)


def test_gate_target_climbing_out_of_folder_fires(monkeypatch, capsys,
                                                  tmp_path):
    """A write run from inside the folder to a path outside it reports.

    Mutation: exempting every write whose cwd is inside the folder; or
    joining the target to cwd without normalizing, so
    `<folder>/evidence/../../../src/out.txt` still carries the folder
    prefix.
    Oracle: stdout names SPEC.md for a `..` climb run from a
    subdirectory of the folder.
    """
    root, folder = _handoff_root(tmp_path)
    inside = folder / 'evidence'
    inside.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(inside, tr, 'V10', 'Bash',
                            {'command': 'echo x > ../../../src/out.txt'})
    assert 'SPEC.md' in _run(monkeypatch, capsys, handoff_gate, payload)


def test_gate_folder_mention_after_a_redirect_elsewhere_fires(
        monkeypatch, capsys, tmp_path):
    """A folder path later on the line exempts no write before it.

    Mutation: scanning all text after the operator or the verb for the
    folder prefix instead of the target words, so `> src/out.txt; ls
    .handoff/<slug>/` and `git add src/out.txt; ls .handoff/<slug>/`
    are exempt although their one write lands outside the folder.
    Oracle: stdout names SPEC.md for both spellings, each in a fresh
    session; the write's target is src/out.txt.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    spellings = ['echo x > src/out.txt', 'git add src/out.txt']
    for n, write in enumerate(spellings):
        payload = _gate_payload(
            root, tr, f'V12{n}', 'Bash',
            {'command': f'{write}; ls .handoff/{_SLUG}/'})
        out = _run(monkeypatch, capsys, handoff_gate, payload)
        assert 'SPEC.md' in out, write


def test_gate_folder_boundary_is_the_armed_folder_alone(monkeypatch, capsys,
                                                        tmp_path):
    """The exemption covers the armed folder itself and no sibling.

    Mutation: dropping the trailing separator from the folder path, so
    `.handoff/<slug>-2/f` is exempt; or from the resolved target, so
    `git add .` run from the folder reports; or dropping the literal
    prefix fast path, so `$ROOT/.handoff/<slug>/f` reports.
    Oracle: stdout empty for the folder itself as a target, spelled
    `git add .` from the folder and `git add .handoff/<slug>` from the
    root, and for a redirect to `$ROOT/.handoff/<slug>/f`; stdout
    naming SPEC.md for a redirect into `.handoff/<slug>-2/`.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    exempt = [
        (folder, 'git add .'),
        (root, f'git add .handoff/{_SLUG}'),
        (root, f'echo x > $ROOT/.handoff/{_SLUG}/f'),
        ]
    for cwd, command in exempt:
        payload = _gate_payload(cwd, tr, 'V14', 'Bash', {'command': command})
        assert _run(monkeypatch, capsys, handoff_gate, payload) == '', command
    payload = _gate_payload(root, tr, 'V14', 'Bash',
                            {'command': f'echo x > .handoff/{_SLUG}-2/f'})
    assert 'SPEC.md' in _run(monkeypatch, capsys, handoff_gate, payload)


def test_gate_reads_a_noclobber_target_and_survives_a_tilde(
        monkeypatch, capsys, tmp_path):
    """A `>|` target is read, and a `~user` target never crashes the gate.

    Mutation: the redirect regex without its optional `|`, so `echo x >|
    smoke.log` run from the folder reports; or pathlib's expanduser in
    place of os.path's, which raises on an unknown user and leaves the
    gate silent for a repo write.
    Oracle: stdout empty for `>| smoke.log` run from the folder; stdout
    naming SPEC.md for `> ~nosuchuser42/out.log` run from the root.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(folder, tr, 'V15', 'Bash',
                            {'command': 'echo x >| smoke.log'})
    assert _run(monkeypatch, capsys, handoff_gate, payload) == ''
    payload = _gate_payload(root, tr, 'V15', 'Bash',
                            {'command': 'echo x > ~nosuchuser42/out.log'})
    assert 'SPEC.md' in _run(monkeypatch, capsys, handoff_gate, payload)


# --- The most recent open wins ---


def test_scan_transcript_last_open_wins():
    """Verify scan_transcript returns the slug of the last hq.py open.

    Mutation: the first open winning instead of the last, so a session
    that switches slugs arms on the wrong folder.
    Oracle: two sequential opens in one chunk; the second slug must be
    returned regardless of the first.
    """
    entries = [
        _bash('python3 bin/hq.py open foo'),
        _bash('python3 bin/hq.py open bar'),
        ]
    text = '\n'.join(json.dumps(e) for e in entries) + '\n'
    slug, _, _ = handoff_gate.scan_transcript(text)
    assert slug == 'bar'


def test_gate_quoted_target_inside_folder_stays_exempt(
        monkeypatch, capsys, tmp_path):
    """A redirect to a quoted path inside the folder stays silent.

    Mutation: quoted spans removed before the write-target scan, so the
    folder path vanishes from the text and the handoff's own write is
    reported.
    Oracle: stdout empty for a redirect whose target is the folder path
    in double quotes.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(
        root, tr, 'V7', 'Bash',
        {'command': f'echo x > ".handoff/{_SLUG}/notes.md"'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert out == ''


def test_gate_quoted_operator_before_a_folder_read_still_fires(
        monkeypatch, capsys, tmp_path):
    """A '>' inside quotes is no write operator for the exemption.

    Mutation: quoted spans kept whole, so the quoted '>' counts as a
    redirect that precedes the folder path and the read is exempted.
    Oracle: stdout names SPEC.md when the only real redirect follows the
    folder path.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(
        root, tr, 'V8', 'Bash',
        {'command': f"grep '>' .handoff/{_SLUG}/SPEC.md > src/out.txt"})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'SPEC.md' in out


# --- The gate reads the write ---


def test_gate_credits_only_the_read_verbs_own_segment(monkeypatch, capsys,
                                                      tmp_path):
    """A path is read only as an argument of the read verb's own segment.

    Mutation: searching the whole command for a read verb, so `hq when
    s f | head` and `grep x f | head` credit f because `head` sits on
    the line; or keeping a redirect target in the segment, so `cat >
    f` credits f.
    Oracle: a spy on stdout across fresh sessions - the same Edit
    payload names SPEC.md after each of the three non-reads and is
    silent after `cat f | head` and `head -30 f`.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    spec = f'.handoff/{_SLUG}/SPEC.md'
    armed = [_bash(f'python3 bin/hq.py open {_SLUG}')]

    def run(session, command):
        tr = _transcript(tmp_path / f'{session}.jsonl', armed + [_bash(command)])
        payload = _gate_payload(root, tr, session, 'Edit',
                                {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    not_read = [
        f'hq when {_SLUG} {spec} | head',
        f'grep -n Content {spec} | head -5',
        f"cat > {spec} <<'EOF'\n# Spec\nEOF",
        ]
    for n, command in enumerate(not_read):
        assert 'SPEC.md' in run(f'seg-miss-{n}', command), command
    read = [f'cat {spec} | head -20', f'head -30 {spec}']
    for n, command in enumerate(read):
        assert run(f'seg-hit-{n}', command) == '', command


def test_gate_reach_is_the_write_target_not_the_cwd(monkeypatch, capsys,
                                                    tmp_path):
    """Reach is decided by where the write lands, not where it is run.

    Mutation: taking the root from cwd alone, so a Write beside the repo
    reports from a cwd inside it and a Write into the repo is silent
    from a cwd outside it; or resolving reach from any single target,
    so a redirect beside the repo reports when a `git add` on the line
    stays inside it.
    Oracle: a spy on stdout - a Write and a redirect landing beside the
    repo are silent from the root, and the repo Edit that follows in
    the same session still names SPEC.md, so the silence spent nothing;
    the same Write into the repo from a cwd above it names SPEC.md; a
    Write into a second project holding its own ledger is silent,
    since the armed slug is no folder there.
    """
    root, folder = _handoff_root(tmp_path)
    (tmp_path / 'out').mkdir()
    other = tmp_path / 'other'
    (other / '.handoff' / 'else' / 'ledger.tsv').parent.mkdir(parents=True)
    (other / '.handoff' / 'else' / 'ledger.tsv').write_text(
        _LEDGER_HEADER + '\n', encoding='utf-8')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(cwd, session, tool_name, tool_input):
        payload = _gate_payload(cwd, tr, session, tool_name, tool_input)
        return _run(monkeypatch, capsys, handoff_gate, payload)

    beside = str(tmp_path / 'out' / 'x.md')
    assert run(root, 'R1', 'Write', {'file_path': beside}) == ''
    assert run(root, 'R1', 'Bash', {'command': f'echo x > {beside}'}) == ''
    assert 'SPEC.md' in run(root, 'R1', 'Edit', {'file_path': 'src/app.py'})
    assert 'SPEC.md' in run(
        tmp_path, 'R2', 'Write', {'file_path': str(root / 'src' / 'app.py')})
    assert run(root, 'R3', 'Write',
               {'file_path': str(other / 'src' / 'f.py')}) == ''


def test_gate_reaches_the_pinned_work_dir(monkeypatch, capsys, tmp_path):
    """A write under the armed folder's work-dir pin is in reach.

    Mutation: testing reach against the root alone, so a Write into the
    pinned directory outside the repo is silent; or against the pin's
    parent, so its sibling reports.
    Oracle: a spy on stdout - a Write into the pinned directory names
    SPEC.md and a Write into its sibling, in a fresh session, is
    silent; the pin is a directory beside the repo, so the root alone
    cannot reach it.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setattr(handoff_gate.hq, '_TEMP_DIRS', ())
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    pinned = tmp_path / 'wt'
    pinned.mkdir()
    (tmp_path / 'wt-sibling').mkdir()
    (folder / 'work-dir').write_text(str(pinned) + '\n', encoding='utf-8')
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(root, tr, 'P1', 'Write',
                            {'file_path': str(pinned / 'S.md')})
    assert 'SPEC.md' in _run(monkeypatch, capsys, handoff_gate, payload)
    payload = _gate_payload(root, tr, 'P2', 'Write',
                            {'file_path': str(tmp_path / 'wt-sibling' / 'S.md')})
    assert _run(monkeypatch, capsys, handoff_gate, payload) == ''


def test_gate_names_each_gated_path_once_at_the_write_that_needs_it(
        monkeypatch, capsys, tmp_path):
    """An always row is named at the first write, an edit row at its own.

    Mutation: one reported flag for the session, so the second draft
    edited after the first report is never named; naming edit rows at
    any write in reach, so both drafts appear at the first Edit; or
    dropping the reported list, so SPEC.md is named at every write.
    Oracle: hand-listed stdout for three Edits in one session - the
    first names SPEC.md and a.py alone, the second names b.py alone,
    the third is silent.
    """
    root, folder = _handoff_root(tmp_path)
    a_path = str(root / 'src' / 'a.py')
    b_path = str(root / 'src' / 'b.py')
    for path in (a_path, b_path):
        pathlib.Path(path).write_text('x = 1\n', encoding='utf-8')
        hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                       _row(path, base='abs', kind='draft', read_before='edit'),
                       _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(file_path):
        payload = _gate_payload(root, tr, 'O1', 'Edit', {'file_path': file_path})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    first = run(a_path)
    assert 'SPEC.md' in first
    assert a_path in first
    assert b_path not in first
    second = run(b_path)
    assert b_path in second
    assert 'SPEC.md' not in second
    assert a_path not in second
    assert run(a_path) == ''


def test_gate_names_a_draft_in_the_folder_at_its_own_edit(monkeypatch, capsys,
                                                          tmp_path):
    """A folder write names no always row but still its own edit row.

    Mutation: the folder exemption returning before the edit-row match,
    so a draft under drafts/ is edited unread in silence; or dropping
    the exemption for edit rows only, so the HANDOFF.md edit that
    follows names SPEC.md.
    Oracle: a spy on stdout - an Edit of drafts/x.py names drafts/x.py
    and not SPEC.md, and an Edit of HANDOFF.md in the same session is
    silent.
    """
    root, folder = _handoff_root(tmp_path)
    (folder / 'drafts').mkdir()
    (folder / 'drafts' / 'x.py').write_text('x = 1\n', encoding='utf-8')
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row('drafts/x.py', kind='draft', read_before='edit'),
                   _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(root, tr, 'D1', 'Edit',
                            {'file_path': str(folder / 'drafts' / 'x.py')})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'drafts/x.py' in out
    assert 'SPEC.md' not in out
    payload = _gate_payload(root, tr, 'D1', 'Edit',
                            {'file_path': str(folder / 'HANDOFF.md')})
    assert _run(monkeypatch, capsys, handoff_gate, payload) == ''


def test_gate_commit_with_no_target_names_always_rows_only(monkeypatch, capsys,
                                                           tmp_path):
    """A write verb with no path takes the cwd's root and names always rows.

    Mutation: treating an empty target list as out of reach, so a bare
    `git commit` is silent with SPEC.md unread; or matching edit rows
    against the cwd, so the draft is named by a commit that never
    touched it.
    Oracle: a spy on stdout - `git commit` from the root names SPEC.md
    and not the edit row; the same command from a directory outside
    every root is silent.
    """
    root, folder = _handoff_root(tmp_path)
    draft = str(root / 'src' / 'app.py')
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row(draft, base='abs', kind='draft', read_before='edit'),
                   _LEDGER_HEADER)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _gate_payload(root, tr, 'C1', 'Bash', {'command': 'git commit'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'SPEC.md' in out
    assert draft not in out
    payload = _gate_payload(tmp_path, tr, 'C2', 'Bash', {'command': 'git commit'})
    assert _run(monkeypatch, capsys, handoff_gate, payload) == ''
