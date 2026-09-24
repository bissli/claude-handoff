"""Tests for the handoff PreToolUse gate and the handoff Stop hook."""

import io
import json
import pathlib
import sys

from scripts import handoff_gate, handoff_stop

from bin import hq

_SLUG = 'demo-slug'
_NOW = '2026-09-09T12:00:00'
_SPEC_TEXT = '# Spec\n\n## Scope\n\nOne.\nTwo.\n\n## Risks\n\nThree.\n'
_SPEC_SPAN = 'lines 3-7'
_LEDGER_HEADER = '\t'.join(hq.LEDGER_FIELDS)
_MANIFEST_HEADER = '\t'.join(hq.MANIFEST_FIELDS)
_REPO = pathlib.Path(__file__).resolve().parents[1]


def _row(path: str, **overrides: str) -> dict:
    """Build one ledger row with live/always defaults.

    Parameters
    ----------
    path : str
        Value for the ``path`` field, stored as the ledger stores it.
    **overrides : str
        Field values replacing the defaults, keyed by ledger field name.

    Returns
    -------
    dict
        A row carrying every field in ``hq.LEDGER_FIELDS``.
    """
    row = {
        'cycle': '1', 'ts': _NOW, 'path': path, 'base': 'folder',
        'kind': 'spec', 'status': 'live', 'read_before': 'always',
        'successor': '-', 'where': 'Scope', 'sha12': '-', 'lines': '10',
        'reason': '-', 'label': '-',
        }
    row.update(overrides)
    return row


def _handoff_root(tmp_path: pathlib.Path, slug: str = _SLUG) -> tuple:
    """Create a project root holding one handoff folder and its ledger.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary directory; the root is placed at ``tmp_path/proj``.
    slug : str, default _SLUG
        Handoff folder name under ``.handoff/``.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        The project root and the handoff folder.

    Notes
    -----
    - SPEC.md is the only gated row: NOTES.md is read_before=mention and
      OLD.md is superseded, so a filter that drops either test breaks.
    """
    root = pathlib.Path(tmp_path) / 'proj'
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)
    (root / 'src').mkdir(exist_ok=True)
    (root / 'src' / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    (folder / 'NOTES.md').write_text('# Notes\n\n## Scope\n\nOne.\n',
                                     encoding='utf-8')
    (folder / 'OLD.md').write_text('# Old\n\n## Scope\n\nOne.\n',
                                   encoding='utf-8')
    (folder / 'HANDOFF.md').write_text('# Handoff\n\n## Task\n\nWork.\n',
                                       encoding='utf-8')
    ledger = folder / 'ledger.tsv'
    for row in (_row('SPEC.md'),
                _row('NOTES.md', read_before='mention'),
                _row('OLD.md', status='superseded')):
        hq._append_tsv(ledger, hq.LEDGER_FIELDS, row, _LEDGER_HEADER)
    return root, folder


def _bash(command: str) -> dict:
    """Return one transcript line holding a Bash tool call.
    """
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}]}}


def _read(file_path: str) -> dict:
    """Return one transcript line holding a Read tool call.
    """
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Read',
         'input': {'file_path': file_path}}]}}


def _transcript(path: pathlib.Path, entries: list) -> pathlib.Path:
    """Write a JSONL transcript and return its path.
    """
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n',
                    encoding='utf-8')
    return path


def _payload(root: pathlib.Path, transcript: pathlib.Path, session: str,
             tool_name: str, tool_input: dict) -> dict:
    """Build a PreToolUse payload of the shape Claude Code sends.

    Parameters
    ----------
    root : pathlib.Path
        Directory the tool call runs in, sent as ``cwd``.
    transcript : pathlib.Path
        Path to the session transcript.
    session : str
        Session id, which names the state and receipt files.
    tool_name : str
        Tool being called, e.g. ``Bash`` or ``Edit``.
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


def _run(monkeypatch, capsys, module, payload):
    """Drive a hook's main() over one payload and return its stdout.
    """
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    module.main()
    return capsys.readouterr().out.strip()


def _context(out: str) -> str:
    """Return the additionalContext text from a gate's stdout.
    """
    return json.loads(out)['hookSpecificOutput']['additionalContext']


def test_gate_ignores_stderr_only_redirects():
    """Verify bash_writes() reads only real writes as writes.

    Mutation: testing the raw command for '>' without stripping stderr
    redirections, quoted spans, and heredoc bodies - which makes every
    `cmd 2>&1` a write and fires the gate on read-only commands.
    Oracle: hand-classified command list, each side of the boundary.
    """
    quiet = [
        'cmd 2>&1',
        'cmd >/dev/null 2>&1',
        'cmd 2>/dev/null',
        'echo ">" | grep x',
        "echo 'a > b'",
        "cat <<'EOF'\n> not a redirect\nEOF",
        'sed -n 1,20p file',
        'grep -n x file',
        'python3 bin/hq.py stamp demo-slug SPEC.md > out.txt',
        ]
    loud = [
        'cmd > out.txt',
        'cmd >> log',
        'cmd &> both.txt',
        'sed -i s/a/b/ f',
        'sed -ni 1,2p f',
        'sed --in-place s/a/b/ f',
        'tee f',
        'git add .',
        'git commit -m x',
        ]
    assert [c for c in quiet if handoff_gate.bash_writes(c)] == []
    assert [c for c in loud if not handoff_gate.bash_writes(c)] == []


def test_gate_fires_on_a_heredoc_and_a_redirect_write(monkeypatch, capsys,
                                                      tmp_path):
    """Verify an armed session with an unread gated path is reported.

    Mutation: dropping the read_before/status filter, or naming the row
    without its span, so the message cannot be acted on; or reporting a
    permission decision beside the advisory, which skips the user's
    prompt on the write the gate is warning about.
    Oracle: hand-computed - SPEC.md's 'Scope' heading spans lines 3-7 of
    the fixture, and NOTES.md and OLD.md must not appear.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'S1', 'Bash',
                       {'command': "cat > x.py <<'EOF'\nprint(1)\nEOF"})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    text = _context(out)
    assert json.loads(out)['hookSpecificOutput'] == {
        'hookEventName': 'PreToolUse', 'additionalContext': text,
        }
    assert text == (
        f'handoff gate: {_SLUG}: 1 gated path(s) not read this session'
        f' - SPEC.md ({_SPEC_SPAN}); read each or run:'
        f' hq read {_SLUG} SPEC.md')


def test_gate_arms_on_the_bare_hq_command(monkeypatch, capsys, tmp_path):
    """Verify `hq open <slug>`, the PATH form, arms the gate as hq.py does.

    Mutation: _OPEN_VERB requiring the `.py` suffix, so a session that
    opens through bin/hq is never armed and the write goes unreported.
    Oracle: the message the python3 form produces on the same fixture -
    SPEC.md at its hand-computed span, pointing at `hq read`.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [_bash(f'hq open {_SLUG}')])
    payload = _payload(root, tr, 'S1', 'Bash', {'command': 'cat > x.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert _context(out) == (
        f'handoff gate: {_SLUG}: 1 gated path(s) not read this session'
        f' - SPEC.md ({_SPEC_SPAN}); read each or run:'
        f' hq read {_SLUG} SPEC.md')


def test_gate_exempts_a_segment_whose_command_word_is_hq():
    """Verify only a segment running `hq` or `hq.py` escapes the write test.

    Mutation: the exemption testing for the literal 'hq.py', which gates
    a `stamp` run through bin/hq; requiring whitespace after the word,
    which gates the quoted-path form hooks.json uses; or matching the
    word anywhere, which exempts a commit message or a heredoc body that
    merely mentions hq.
    Oracle: hand-classified commands on each side of the command-word
    rule, the quoted, assigned, chained, and heredoc forms included.
    """
    exempt = [
        'hq stamp demo-slug SPEC.md > out.txt',
        'HQ_ROOT=/tmp/x hq begin demo-slug | tee log',
        'cd /tmp/x && hq begin demo-slug > log',
        'bin/hq note demo-slug decision "x" > /tmp/o',
        'python3 bin/hq.py stamp demo-slug SPEC.md > out.txt',
        'python3 "${CLAUDE_PLUGIN_ROOT}/bin/hq.py" stamp demo-slug a.md > o',
        'bash -c "hq open demo-slug > f"',
        ]
    gated = [
        'echo x > hq-notes.md',
        'echo chq > f',
        'echo x > .handoff/hq.md',
        'echo x > hq',
        'my-hq stamp demo-slug SPEC.md > out.txt',
        'git commit -m "add hq wrapper"',
        "cat > notes.md <<'EOF'\nhq begin demo-slug\nEOF",
        ]
    assert [c for c in exempt if handoff_gate.bash_writes(c)] == []
    assert [c for c in gated if not handoff_gate.bash_writes(c)] == []


def test_gate_counts_only_read_shaped_evidence(monkeypatch, capsys, tmp_path):
    """Verify only a read verb or the Read tool clears a gated path.

    Mutation: counting any mention of the path in the transcript, so an
    `ls` or a `wc -l` reads as having read the file.
    Oracle: a spy on stdout - the same write payload reports under ls,
    wc, and stamp, and is silent under cat, sed -n, and Read.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    spec = folder / 'SPEC.md'
    armed = [_bash(f'python3 bin/hq.py open {_SLUG}')]

    def run(session, extra):
        tr = _transcript(tmp_path / f'{session}.jsonl', armed + extra)
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('m1', [
        _bash(f'ls .handoff/{_SLUG}/SPEC.md'),
        _bash(f'wc -l .handoff/{_SLUG}/SPEC.md'),
        _bash(f'python3 bin/hq.py stamp {_SLUG} SPEC.md'),
        ])
    assert run('m2', [_bash(f'cat .handoff/{_SLUG}/SPEC.md')]) == ''
    assert run('m3', [_bash(f'sed -n 1,20p {spec}')]) == ''
    assert run('m4', [_read(str(spec))]) == ''


def test_gate_credits_a_read_receipt(monkeypatch, capsys, tmp_path):
    """Verify an hq.py read receipt clears a path never read in-transcript.

    Mutation: matching the receipt line on the path alone, so a receipt
    from another handoff folder clears this one; matching without the
    leading space, so a slug that ends with this one's name clears it;
    or dropping the receipt check, which makes `hq.py read` no answer to
    the gate it names.
    Oracle: a spy on stdout - the identical payload reports with no
    receipt, reports with a receipt naming another folder or a folder
    whose name ends with this one's, and is silent with the receipt
    naming this one.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('r1')
    (state / 'hq-reads-r2.txt').write_text(f'{_NOW} other-slug SPEC.md\n',
                                           encoding='utf-8')
    assert 'SPEC.md' in run('r2')
    (state / 'hq-reads-r3.txt').write_text(f'{_NOW} pre-{_SLUG} SPEC.md\n',
                                           encoding='utf-8')
    assert 'SPEC.md' in run('r3')
    (state / 'hq-reads-r4.txt').write_text(f'{_NOW} {_SLUG} SPEC.md\n',
                                           encoding='utf-8')
    assert run('r4') == ''


def test_gate_counts_the_spans_it_does_not_name(
        monkeypatch, capsys, tmp_path):
    """The gate names the first resolved span and how many there are.

    Mutation: the count dropped, so a multi-anchor row advertises its
    first span as the whole gated extent; the count off by one; or the
    count printed on a single-span row.
    Oracle: hand-computed against the fixture spec - 'Scope' runs 3-7 and
    'Risks' 8-10. A two-anchor row reads 'lines 3-7 of 2 spans' and the
    same payload reads a bare 'lines 8-10' once the set is narrowed to
    Risks alone.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _context(_run(monkeypatch, capsys, handoff_gate, payload))

    ledger = folder / 'ledger.tsv'
    hq._append_tsv(ledger, hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='2', where='Scope;Risks'),
                   _LEDGER_HEADER)
    assert 'SPEC.md (lines 3-7 of 2 spans)' in run('w1')
    hq._append_tsv(ledger, hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='3', where='Risks'),
                   _LEDGER_HEADER)
    assert 'SPEC.md (lines 8-10)' in run('w2')


def test_gate_counts_every_resolved_span_in_anchor_order(
        monkeypatch, capsys, tmp_path):
    """The named span is the first anchor's, whatever its place in the file.

    Mutation: the resolved list sorted or indexed at its last element, or
    the pair read as the outer bounds of every span, so a row whose first
    anchor sits late in the file advertises a span it does not lead with.
    Oracle: hand-computed against the fixture spec - 'Risks;Scope'
    resolves to 8-10 then 3-7, so the message reads 'lines 8-10 of 2
    spans' and never 3-10 or 3-7.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='2', where='Risks;Scope'),
                   _LEDGER_HEADER)
    payload = _payload(root, tr, 'o1', 'Edit', {'file_path': 'src/app.py'})
    out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
    assert 'SPEC.md (lines 8-10 of 2 spans)' in out
    assert 'lines 3-' not in out


def test_a_read_of_a_narrowed_span_still_clears_the_gate(
        monkeypatch, capsys, tmp_path):
    """Gate credit is keyed on the path, whatever fraction the anchor names.

    Mutation: the receipt matched against the row's anchor set as well as
    its path, which makes a trimmed row unclearable - every write after a
    narrowing reports a path the session has already read.
    Oracle: a spy on stdout - one receipt naming the folder and the path
    silences a row anchored at one of the fixture's two sections, and the
    same payload reports without it.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='2', where='Risks'),
                   _LEDGER_HEADER)

    def run(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('n1')
    (state / 'hq-reads-n2.txt').write_text(f'{_NOW} {_SLUG} SPEC.md\n',
                                           encoding='utf-8')
    assert run('n2') == ''


def test_a_receipt_in_the_folder_clears_the_gate(
        monkeypatch, capsys, tmp_path):
    """A receipt hq read left in the handoff folder counts as a read.

    Mutation: the gate reading receipts from the state dir alone, so a
    read made under a read-only state dir is reported at every write.
    Oracle: a spy on stdout - the row reports with no receipt, a folder
    receipt for the session silences it, and one for another session
    does not.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='2', where='Risks'),
                   _LEDGER_HEADER)

    def run(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('f1')
    (folder / '.hq.reads-f2').write_text(f'{_NOW} {_SLUG} SPEC.md\n',
                                         encoding='utf-8')
    assert run('f2') == ''
    assert 'SPEC.md' in run('f3')


def test_gate_arms_only_on_hq_open(monkeypatch, capsys, tmp_path):
    """Verify nothing is gated until an hq.py open names a slug.

    Mutation: arming on the presence of a handoff root, so every session
    in the repo is gated whether or not it opened a handoff.
    Oracle: a spy on stdout - a transcript reading HANDOFF.md is silent,
    the same transcript plus `hq.py open` reports, and a prefix of the
    slug resolves to the same folder.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    seen = [_read(str(folder / 'HANDOFF.md'))]

    def run(session, entries):
        tr = _transcript(tmp_path / f'{session}.jsonl', entries)
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert run('a1', seen) == ''
    assert 'SPEC.md' in run('a2', seen + [_bash(f'hq.py open {_SLUG}')])
    assert 'SPEC.md' in run('a3', seen + [_bash('hq.py open demo')])


def test_gate_slug_stops_at_shell_punctuation_and_quotes(monkeypatch, capsys,
                                                         tmp_path):
    """Verify the slug of `hq.py open` ends where the shell word does.

    Mutation: capturing every non-space character, so `hq.py open
    demo-slug; echo x` arms the gate on 'demo-slug;', no folder matches,
    and the session's gated paths are never reported; a class missing
    the single quote, so a quoted slug never matches; a class missing
    `.` or `_`, so a dotted slug truncates to a prefix its siblings
    share; arming on a quoted mention, so a grep for the phrase points
    the gate at a folder never opened; treating every quoted span as a
    mention, so an open run through `bash -c` or straddled by two
    comment apostrophes never arms; reading the mention verb from the
    whole line, so an earlier `echo` hides a later `bash -c`; a `-m`
    flag as the mention mark, so docker's `-m 512m` silences a real
    open while `git commit -am` overwrites the armed slug; a `$(` in an
    echoed string read as text, so the open it runs never arms;
    splitting the raw prefix, so a `|` inside an earlier argument hides
    the grep; a list without `sed`, so a substitution on the phrase
    arms; or taking the first of two prefix matches.
    Oracle: the report naming SPEC.md for the open commands hand-listed
    at each boundary - a trailing `;`, a `|`, an `&&` chain, a double-
    and a single-quoted slug, `demo.v2_x` beside two siblings sharing
    each truncation, an open under `bash -lc`, `ssh`, `docker run -m`,
    an `echo; bash -c` chain, and an echoed `$(`, one between two
    apostrophes, and one followed by a `-am` mention of another slug;
    silence for the grep, two-pattern grep, sed, and commit-message
    mentions and for the prefix `demo.v2`.
    """
    root, folder = _handoff_root(tmp_path)
    _handoff_root(tmp_path, 'demo.v2_x')
    _handoff_root(tmp_path, 'demo.v2-y')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    spellings = [
        f'python3 bin/hq.py open {_SLUG}; echo done',
        f'python3 bin/hq.py open {_SLUG}|head -3',
        f'python3 bin/hq.py open {_SLUG}&&echo ok',
        f'python3 bin/hq.py open "{_SLUG}" 2>&1',
        f"python3 bin/hq.py open '{_SLUG}'",
        'python3 bin/hq.py open demo.v2_x',
        f'bash -lc "cd /tmp && python3 bin/hq.py open {_SLUG}"',
        f"ssh host 'python3 bin/hq.py open {_SLUG}'",
        f'echo hi; bash -c "python3 bin/hq.py open {_SLUG}"',
        f"# don't stamp yet\npython3 bin/hq.py open {_SLUG}\n# it's open",
        f'python3 bin/hq.py open {_SLUG}; git commit -am "hq.py open other"',
        f'docker run --rm -m 512m img bash -c "python3 hq.py open {_SLUG}"',
        f'echo "$(python3 bin/hq.py open {_SLUG})"',
        ]
    mentions = [
        f'grep -n "hq.py open {_SLUG}" README.md',
        f'grep -e "a|b" -e "hq.py open {_SLUG}" README.md',
        f"sed 's/hq.py open {_SLUG}/x/' README.md",
        f'git commit -m "note: hq.py open {_SLUG}"',
        'python3 bin/hq.py open demo.v2',
        ]

    def run(session, command):
        tr = _transcript(tmp_path / f'{session}.jsonl', [_bash(command)])
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    for n, command in enumerate(spellings):
        assert 'SPEC.md' in run(f's{n}', command), command
    for n, command in enumerate(mentions):
        assert run(f'm{n}', command) == '', command


def test_gate_exempts_hq_commands_and_folder_writes(monkeypatch, capsys,
                                                    tmp_path):
    """Verify writing the handoff itself never trips its own gate.

    Mutation: gating every write once armed, which blocks `hq.py stamp`
    and every edit of HANDOFF.md - the work the gate exists to protect.
    Oracle: a spy on stdout across four payloads in one session - the hq
    command, the folder redirect, and the HANDOFF.md edit are silent,
    and the repo write that follows still reports.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(tool_name, tool_input):
        payload = _payload(root, tr, 'E1', tool_name, tool_input)
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert run('Bash', {
        'command': f'python3 bin/hq.py stamp {_SLUG} notes-a.md'}) == ''
    assert run('Bash', {
        'command': f'echo x > .handoff/{_SLUG}/notes-a.md'}) == ''
    assert run('Edit', {'file_path': str(folder / 'HANDOFF.md')}) == ''
    assert 'SPEC.md' in run('Edit', {'file_path': 'src/app.py'})


def test_gate_scans_the_transcript_once_per_session(monkeypatch, capsys,
                                                    tmp_path):
    """Verify the scan resumes from the stored offset and never rescans.

    Mutation: dropping the offset and the persisted reads, so every call
    re-reads the whole transcript - the cost the gate must not pay on a
    hook that runs on every Bash and Edit.
    Oracle: a counting spy on scan_transcript, which must be called once
    however many writes follow, plus the deleted transcript, so a report
    on the later call can only come from stored state.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    scanned = []
    real = handoff_gate.scan_transcript

    def counted(text):
        scanned.append(text)
        return real(text)

    monkeypatch.setattr(handoff_gate, 'scan_transcript', counted)

    def run(tool_input):
        payload = _payload(root, tr, 'O1', 'Bash', tool_input)
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert run({'command': f'echo x > .handoff/{_SLUG}/notes-a.md'}) == ''
    assert len(scanned) == 1
    assert run({'command': f'echo y > .handoff/{_SLUG}/notes-b.md'}) == ''
    assert len(scanned) == 1
    tr.unlink()
    assert 'SPEC.md' in run({'command': 'echo x > src/app.py'})
    assert run({'command': 'echo y > src/app.py'}) == ''
    assert len(scanned) == 1


def test_gate_resumes_at_a_line_boundary(monkeypatch, capsys, tmp_path):
    """Verify a half-written transcript line is scanned whole on the next call.

    Mutation: advancing the offset to the end of the bytes read, so the
    tail of a line the host was still writing is split across two
    scans and the hq.py open it carried is never seen.
    Oracle: a spy on stdout - the same repo write is silent while the open
    line is half written and reports once the line is completed.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [_read(str(folder / 'HANDOFF.md'))])
    open_line = json.dumps(_bash(f'python3 bin/hq.py open {_SLUG}'))
    cut = len(open_line) // 2
    with tr.open('a', encoding='utf-8') as handle:
        handle.write(open_line[:cut])

    def run(command):
        payload = _payload(root, tr, 'B1', 'Bash', {'command': command})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert run('echo x > src/app.py') == ''
    with tr.open('a', encoding='utf-8') as handle:
        handle.write(open_line[cut:] + '\n')
    assert 'SPEC.md' in _context(run('echo y > src/app.py'))


def test_gate_allows_when_off_marker_present(monkeypatch, capsys, tmp_path):
    """Verify HQ_GATE=0 silences the gate on a payload that reports.

    Mutation: reading the switch as truthy, so HQ_GATE=0 turns the gate
    on and the documented way out does not work.
    Oracle: a spy on stdout - the same payload and transcript report
    with the switch unset and print nothing with HQ_GATE=0.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('g1')
    monkeypatch.setenv('HQ_GATE', '0')
    assert run('g2') == ''


def test_gate_deny_path_behind_env(monkeypatch, capsys, tmp_path):
    """Verify HQ_GATE_DENY=1 blocks the call with the same message.

    Mutation: emitting the deny form by default, which stops the tool
    call for every user who never asked for a hard block; or reporting a
    permission decision by default, which skips the user's prompt on the
    very call the gate has doubts about.
    Oracle: differential - the deny reason must equal the report's
    context from the identical payload, and the default report must
    carry no permission decision at all.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(session):
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    reported = json.loads(run('d1'))['hookSpecificOutput']
    monkeypatch.setenv('HQ_GATE_DENY', '1')
    denied = json.loads(run('d2'))['hookSpecificOutput']
    assert 'permissionDecision' not in reported
    assert denied == {
        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
        'permissionDecisionReason': reported['additionalContext'],
        }


def test_gate_is_silent_outside_a_handoff_root(monkeypatch, capsys, tmp_path):
    """Verify a repo with no handoff folder costs nothing and says nothing.

    Mutation: walking to the filesystem root and gating anyway, or
    writing state before the root is found - which leaves a state file
    per session for every project on the machine.
    Oracle: a spy on the state directory, which must stay empty, plus
    stdout.
    """
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _payload(elsewhere, tr, 'X1', 'Bash',
                       {'command': 'echo x > out.txt'})
    assert _run(monkeypatch, capsys, handoff_gate, payload) == ''
    assert not state.exists() or list(state.iterdir()) == []


def test_gate_survives_a_corrupt_state_file(monkeypatch, capsys, tmp_path):
    """Verify a truncated state file does not silence the gate.

    Mutation: letting the state read raise into the fail-open catch, so
    one bad write disables the gate for the rest of the session.
    Oracle: a spy on stdout, with gate() called directly so a raise
    surfaces as an error instead of being swallowed.
    """
    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    (state / 'C1.handoff.json').write_text('{"gate": ', encoding='utf-8')
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'C1', 'Edit', {'file_path': 'src/app.py'})
    assert handoff_gate.gate(payload) == 0
    assert 'SPEC.md' in capsys.readouterr().out


def _finished(folder: pathlib.Path, cycle: str, sha: str, **overrides: str) -> None:
    """Append one finished-cycle row to a handoff folder's manifest.

    Parameters
    ----------
    folder : pathlib.Path
        The handoff folder holding ``cycles/manifest.tsv``.
    cycle : str
        Cycle number the row records.
    sha : str
        HANDOFF.md digest the cycle finished on.
    **overrides : str
        Further manifest fields to set, ``rewrite_sha`` among them.

    Returns
    -------
    None
    """
    (folder / 'cycles').mkdir(exist_ok=True)
    row = dict.fromkeys(hq.MANIFEST_FIELDS, '-')
    row.update({'cycle': cycle, 'written': _NOW, 'handoff_sha': sha})
    row.update(overrides)
    hq._append_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS,
                   row, _MANIFEST_HEADER)


def _stop_payload(root: pathlib.Path, session: str, **extra) -> dict:
    """Build a Stop payload of the shape Claude Code sends.
    """
    payload = {'hook_event_name': 'Stop', 'cwd': str(root),
               'session_id': session, 'stop_hook_active': False}
    payload.update(extra)
    return payload


def test_stop_reports_when_handoff_sha_differs_from_manifest(monkeypatch,
                                                             capsys,
                                                             tmp_path):
    """Verify a hand-edited HANDOFF.md is reported with its cycle number.

    Mutation: comparing against the first manifest row instead of the
    last, which names a stale cycle, or comparing file size rather than
    the digest, which misses an edit of equal length.
    Oracle: hand-computed - the manifest records cycle 2 on the original
    digest, and the file is rewritten to the same length.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', 'aaaaaaaaaaaa')
    _finished(folder, '2', hq._sha12_path(handoff))
    handoff.write_text('# Handoff\n\n## Task\n\nWonk.\n', encoding='utf-8')
    out = _run(monkeypatch, capsys, handoff_stop, _stop_payload(root, 'P1'))
    assert json.loads(out) == {'systemMessage': (
        f'handoff: .handoff/{_SLUG}/HANDOFF.md was written by hand since'
        f' cycle 2 finished; run hq begin {_SLUG}, then hq finish'
        f' {_SLUG} --log "...", or the next open reports LEDGER BEHIND')}


def test_stop_compares_against_the_rewrite_an_adopt_row_records(
        monkeypatch, capsys, tmp_path):
    """Verify an adopted folder is silent while HANDOFF.md is the rewrite.

    Mutation: comparing the file against the row's handoff_sha alone,
    which on an adopt row names the archived original, so every Stop
    after an adopt reports the rewrite hq itself wrote as a hand edit;
    or preferring handoff_sha over a recorded rewrite_sha.
    Oracle: a spy on stdout - a last row whose handoff_sha is a foreign
    digest and whose rewrite_sha is the file's digest is silent, and the
    same folder reports once the file is edited.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '3', 'aaaaaaaaaaaa',
              rewrite_sha=hq._sha12_path(handoff))
    assert _run(monkeypatch, capsys, handoff_stop,
                _stop_payload(root, 'R1')) == ''
    handoff.write_text('# Handoff\n\n## Task\n\nOther.\n', encoding='utf-8')
    assert 'since cycle 3 finished' in _run(
        monkeypatch, capsys, handoff_stop, _stop_payload(root, 'R2'))


def test_stop_silent_when_it_matches(monkeypatch, capsys, tmp_path):
    """Verify an untouched handoff, and a locked one, say nothing.

    Mutation: dropping the .hq.lock skip, so every turn of an open cycle
    reports the edit the agent is in the middle of making, or reading
    the first manifest row rather than the last, which reports a folder
    whose digest matches the cycle it actually finished on.
    Oracle: a spy on stdout - a manifest whose last row matches and
    whose first does not is silent, the same folder with a changed file
    and a lock is silent, and removing the lock reports.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', 'aaaaaaaaaaaa')
    _finished(folder, '2', hq._sha12_path(handoff))
    assert _run(monkeypatch, capsys, handoff_stop,
                _stop_payload(root, 'Q1')) == ''
    (folder / '.hq.lock').write_text(f'slug={_SLUG}\ncycle=2\n',
                                     encoding='utf-8')
    handoff.write_text('# Handoff\n\n## Task\n\nOther.\n', encoding='utf-8')
    assert _run(monkeypatch, capsys, handoff_stop,
                _stop_payload(root, 'Q2')) == ''
    (folder / '.hq.lock').unlink()
    assert 'HANDOFF.md' in _run(monkeypatch, capsys, handoff_stop,
                                _stop_payload(root, 'Q3'))


def test_stop_reports_once_per_session_with_flag_set_or_not(monkeypatch,
                                                            capsys,
                                                            tmp_path):
    """Verify one report per hand edit, and none inside a stop loop.

    Mutation: dropping the stop_hook_active guard, which re-fires inside
    the loop it is meant to end, or keying the memory on the slug alone,
    which silences every later edit of the same file.
    Oracle: a spy on stdout across four calls - report, silence, silence
    under the flag in a fresh session, and report again after a second
    edit changes the digest.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', hq._sha12_path(handoff))
    handoff.write_text('# Handoff\n\n## Task\n\nFirst edit.\n',
                       encoding='utf-8')
    assert 'HANDOFF.md' in _run(monkeypatch, capsys, handoff_stop,
                                _stop_payload(root, 'R1'))
    assert _run(monkeypatch, capsys, handoff_stop,
                _stop_payload(root, 'R1')) == ''
    assert _run(monkeypatch, capsys, handoff_stop,
                _stop_payload(root, 'R2', stop_hook_active=True)) == ''
    handoff.write_text('# Handoff\n\n## Task\n\nSecond edit.\n',
                       encoding='utf-8')
    assert 'HANDOFF.md' in _run(monkeypatch, capsys, handoff_stop,
                                _stop_payload(root, 'R1'))


def test_stop_keeps_the_band_message(monkeypatch, capsys, tmp_path):
    """Verify the new hooks are added beside the band hook, not over it.

    Mutation: replacing the context_budget.py Stop entry with the new
    one, or writing the band's own session state from this hook - either
    way the cost warning stops arriving.
    Oracle: hooks.json read from disk, plus a byte comparison of a
    pre-existing <session>.json across the call.
    """
    config = json.loads((_REPO / 'hooks' / 'hooks.json').read_text())
    stop = [h['command'] for entry in config['hooks']['Stop']
            for h in entry['hooks']]
    assert len(stop) == 2
    assert stop[0].endswith('scripts/context_budget.py"')
    assert stop[1].endswith('scripts/handoff_stop.py"')
    pre = config['hooks']['PreToolUse']
    assert len(pre) == 1
    assert pre[0]['matcher'] == 'Edit|Write|NotebookEdit|Bash'
    assert pre[0]['hooks'][0]['command'].endswith('scripts/handoff_gate.py"')

    root, folder = _handoff_root(tmp_path)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    band = state / 'B1.json'
    band.write_text('{"band": 1, "context": 300000}', encoding='utf-8')
    before = band.read_bytes()
    handoff = folder / 'HANDOFF.md'
    _finished(folder, '1', 'aaaaaaaaaaaa')
    assert 'HANDOFF.md' in _run(monkeypatch, capsys, handoff_stop,
                                _stop_payload(root, 'B1'))
    assert band.read_bytes() == before
    assert (state / 'B1.handoff.json').exists()


def test_stop_ignores_subagents(monkeypatch, capsys, tmp_path):
    """Verify a subagent's Stop never reports a hand-edited handoff.

    Mutation: dropping the agent_id guard, so every delegated agent
    repeats the same message on the same folder.
    Oracle: a spy on stdout - the main-thread payload reports and the
    identical payload carrying agent_id does not.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    _finished(folder, '1', 'aaaaaaaaaaaa')
    assert 'HANDOFF.md' in _run(monkeypatch, capsys, handoff_stop,
                                _stop_payload(root, 'T1'))
    assert _run(monkeypatch, capsys, handoff_stop,
                _stop_payload(root, 'T2', agent_id='agent-9')) == ''


def test_gate_resolves_escaped_semicolon_in_where(monkeypatch, capsys, tmp_path):
    r"""Verify a heading whose text contains a semicolon resolves correctly.

    Mutation: splitting row['where'] with str.split(';') instead of
    hq._split_where, which turns the stored '3\\; Retry' into ['3\\',
    'Retry'] and leaves both anchors unresolved, printing 'anchor not
    found' for a heading that exists.
    Oracle: hq._split_where's own output on the stored value confirms
    the anchor list; the gate must report a line span, not 'anchor not
    found'.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## 3; Retry\n\nBody.\n', encoding='utf-8')
    stored_where = '3\\; Retry'
    assert hq._split_where(stored_where) == ['3; Retry']
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='2', where=stored_where),
                   _LEDGER_HEADER)
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'sc1', 'Edit', {'file_path': 'src/app.py'})
    out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
    assert 'anchor not found' not in out
    assert 'SPEC.md (lines 3-5)' in out


def test_gate_counts_distinct_spans_not_anchors(monkeypatch, capsys, tmp_path):
    """Verify two anchors resolving to the same extent count as one span.

    Mutation: using len(spans) rather than len(set(spans)), which prints
    'lines 3-6 of 2 spans' when two different spellings of the same heading
    resolve to one line range, hiding that no second extent exists.
    Oracle: hand-computed - '3. Retry' and 's3' both match '## 3. Retry'
    at lines 3-6; one distinct span, so the gate shows 'lines 3-6' with
    no count.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## 3. Retry\n\nBody.\n\n## Next\n\nOther.\n',
        encoding='utf-8')
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS,
                   _row('SPEC.md', cycle='2', where='3. Retry;s3'),
                   _LEDGER_HEADER)
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _payload(root, tr, 'dd1', 'Edit', {'file_path': 'src/app.py'})
    out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
    assert 'SPEC.md (lines 3-6)' in out
    assert 'of 2 spans' not in out


def test_store_guard_fires_on_the_shape_seen_in_practice(monkeypatch, capsys,
                                                         tmp_path):
    """Verify a bare `cat ledger.tsv` from inside the folder reports.

    Mutation: testing each path as spelled instead of resolved against
    cwd, which misses the one store read seen in practice - the lock
    and the ledger named bare in one command, run from inside the
    folder.
    Oracle: the hand-written verb for the ledger, with the lock named
    beside it changing nothing.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])
    payload = _payload(folder, tr, 'G1', 'Bash',
                       {'command': 'cat .hq.lock ledger.tsv'})
    out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
    assert f'{_SLUG}/ledger.tsv is dictated, not opened' in out
    assert f'hq artifacts {_SLUG}' in out


def test_store_guard_stays_silent_on_the_verbs_it_names(monkeypatch, capsys,
                                                        tmp_path):
    """Verify the hq verbs that replace a direct read report nothing.

    Mutation: matching a store name anywhere in the command instead of
    as a read verb's own argument, which fires on `hq when <slug>
    ledger.tsv` and so punishes the route the message recommends.
    Oracle: empty stdout for each of the four verbs, one of them piped
    into head.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])
    for index, command in enumerate([
            f'hq artifacts {_SLUG}',
            f'hq when {_SLUG} ledger.tsv',
            f'hq standing {_SLUG} | head -3',
            f'hq diff {_SLUG} c01 c02',
            ]):
        payload = _payload(root, tr, f'G2{index}', 'Bash',
                           {'command': command})
        assert _run(monkeypatch, capsys, handoff_gate, payload) == ''


def test_store_guard_counts_only_read_shaped_evidence(monkeypatch, capsys,
                                                      tmp_path):
    """Verify listing or searching a store is not opening it.

    Mutation: dropping the read-verb filter and testing every token of
    every command, which reports an `ls` or a `grep` that never read a
    line.
    Oracle: empty stdout under ls, wc, and grep against the same path
    that reports under cat, which is the positive guard.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])
    stored = f'.handoff/{_SLUG}/ledger.tsv'

    def run(session, command):
        payload = _payload(root, tr, session, 'Bash', {'command': command})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert run('G30', f'ls {stored}') == ''
    assert run('G31', f'wc -l {stored}') == ''
    assert run('G32', f'grep refresh {stored}') == ''
    assert 'ledger.tsv' in _context(run('G33', f'cat {stored}'))


def test_store_guard_names_the_three_stores_not_the_folder(monkeypatch, capsys,
                                                           tmp_path):
    """Verify the guard covers three paths, never the folder at large.

    Mutation: matching the folder prefix instead of the store names,
    which fires on HANDOFF.md - the file the read path exists to open -
    and turns the guard into noise on the prescribed route.
    Oracle: empty stdout for HANDOFF.md and for a notes file, against
    the ledger reporting from the same cwd.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])

    def run(session, target):
        payload = _payload(root, tr, session, 'Bash',
                           {'command': f'cat .handoff/{_SLUG}/{target}'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert run('G40', 'HANDOFF.md') == ''
    assert run('G41', 'notes/idp-quirks.md') == ''
    assert run('G42', 'specs/SPEC.md') == ''
    assert 'ledger.tsv' in _context(run('G43', 'ledger.tsv'))


def test_store_guard_pairs_each_store_with_its_own_verb(monkeypatch, capsys,
                                                        tmp_path):
    """Verify each store names its own verb and a cycles/ read passes.

    Mutation: one verb for both stores, which sends a reader of
    standing.md to hq artifacts; or cycles/ left in the guard, which
    reports the range read of an archived cycle the store rule allows.
    Oracle: the hand-written verb per store, checked against the
    documented mapping rather than against the code, and no context
    for a cycles/ file or the manifest.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])

    def verb(session, target):
        payload = _payload(root, tr, session, 'Bash',
                           {'command': f'cat .handoff/{_SLUG}/{target}'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert f'hq artifacts {_SLUG}' in _context(verb('G50', 'ledger.tsv'))
    assert f'hq standing {_SLUG}' in _context(verb('G51', 'standing.md'))
    assert verb('G52', 'cycles/c01.md') == ''
    assert verb('G54', 'cycles/manifest.tsv') == ''


def test_store_guard_needs_no_ledger_and_no_arming(monkeypatch, capsys,
                                                   tmp_path):
    """Verify a store read reports where no ledger and no hq open exist.

    Mutation: placing the store test below the root search or behind
    the armed slug, which restores the hole it closes - the read seen
    in practice came while the run was still resolving its target, so
    possibly before any hq open.
    Oracle: a directory holding a .handoff/<slug>/ path and no
    ledger.tsv anywhere, an empty transcript, and a spy on the state
    directory, which must stay empty. The plain read exits at the write
    test; the read that also writes is what reaches the root search, so
    both shapes are needed to pin the placement.
    """
    bare = tmp_path / 'bare'
    (bare / '.handoff' / 'other').mkdir(parents=True)
    state = tmp_path / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(state))
    tr = _transcript(tmp_path / 't.jsonl', [])

    def run(session, command):
        payload = _payload(bare, tr, session, 'Bash', {'command': command})
        return _context(_run(monkeypatch, capsys, handoff_gate, payload))

    expected = 'other/ledger.tsv is dictated, not opened'
    assert expected in run('G60', 'cat .handoff/other/ledger.tsv')
    assert expected in run('G61', 'cat .handoff/other/ledger.tsv > out.txt')
    assert not state.exists() or list(state.iterdir()) == []


def test_store_reads_grades_a_read_tool_payload(monkeypatch, capsys,
                                                tmp_path):
    """Verify a Read payload is graded like a read verb.

    Mutation: grading the Bash route alone, which leaves the plainest
    way to open a store - the Read tool - unreported. hooks.json
    matches Edit|Write|NotebookEdit|Bash, so this pins store_reads
    rather than a route a session can take today.
    Oracle: a Read payload naming standing.md by absolute path, against
    the hand-written verb for that store.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])
    payload = _payload(root, tr, 'G7', 'Read',
                       {'file_path': str(folder / 'standing.md')})
    out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
    assert f'hq standing {_SLUG}' in out


def test_store_guard_joins_the_write_gate_report(monkeypatch, capsys,
                                                 tmp_path):
    """Verify a command that both reads a store and writes reports twice.

    Mutation: returning the store report on its own, which silently
    drops the older gate's finding whenever one command does both.
    Oracle: both findings in one additionalContext - the ledger's verb
    and SPEC.md's unread span, each hand-written.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])
    payload = _payload(
        root, tr, 'G8', 'Bash',
        {'command': f'cat .handoff/{_SLUG}/ledger.tsv > out.txt'})
    out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
    assert f'hq artifacts {_SLUG}' in out
    assert 'SPEC.md' in out
    assert _SPEC_SPAN in out


def test_store_guard_honors_both_env_switches(monkeypatch, capsys, tmp_path):
    """Verify HQ_GATE=0 silences the store report and HQ_GATE_DENY denies.

    Mutation: computing the store report above the HQ_GATE check, which
    makes the documented off switch stop silencing the hook.
    Oracle: empty stdout under the off switch, and permissionDecision
    deny carrying the reason under the deny switch.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])
    command = {'command': f'cat .handoff/{_SLUG}/ledger.tsv'}

    monkeypatch.setenv('HQ_GATE', '0')
    payload = _payload(root, tr, 'G90', 'Bash', command)
    assert _run(monkeypatch, capsys, handoff_gate, payload) == ''

    monkeypatch.delenv('HQ_GATE')
    monkeypatch.setenv('HQ_GATE_DENY', '1')
    payload = _payload(root, tr, 'G91', 'Bash', command)
    decision = json.loads(
        _run(monkeypatch, capsys, handoff_gate, payload))['hookSpecificOutput']
    assert decision['permissionDecision'] == 'deny'
    assert 'ledger.tsv' in decision['permissionDecisionReason']


def test_store_guard_reports_one_store_once(monkeypatch, capsys, tmp_path):
    """Verify a store named twice reports once and two stores report twice.

    Mutation: dropping the repeat check, which reports the same line
    twice for a command naming one store in two arguments.
    Oracle: a count of the report phrase, hand-set to 1 for the repeat
    and 2 for the pair.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl', [])
    stored = f'.handoff/{_SLUG}'

    def count(session, command):
        payload = _payload(root, tr, session, 'Bash', {'command': command})
        out = _context(_run(monkeypatch, capsys, handoff_gate, payload))
        return out.count('is dictated, not opened')

    assert count('GA0', f'cat {stored}/ledger.tsv {stored}/ledger.tsv') == 1
    assert count('GA1', f'cat {stored}/ledger.tsv {stored}/standing.md') == 2


def test_gate_fires_on_heredoc_interpreter_write(monkeypatch, capsys, tmp_path):
    """Verify a heredoc-fed interpreter that writes a gated file is a write.

    Mutation: testing only redirect operators and in-place flags, so python3
    fed a heredoc calling write_text escapes the write test entirely.
    Oracle: a spy on stdout - the sed -i form reports (control) and the
    heredoc-interpreter form on the same file also reports.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(session, command):
        payload = _payload(root, tr, session, 'Bash', {'command': command})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('HD1', "sed -i 's/x/y/' src/app.py")
    heredoc = (
        "python3 - <<'PY'\n"
        "import pathlib\n"
        "pathlib.Path('src/app.py').write_text('y = 2\\n')\n"
        "PY"
    )
    assert 'SPEC.md' in run('HD2', heredoc)


def test_gate_exempts_only_segments_whose_command_word_is_hq(monkeypatch,
                                                             capsys,
                                                             tmp_path):
    """Verify hq on the line does not exempt writes in other segments.

    Mutation: searching the whole command line for any hq command pattern,
    which exempts a write chained after hq and a redirect in an echo whose
    argument mentions hq.
    Oracle: a spy on stdout - echo x alone reports (control), the hq-then-sed
    chain reports, and the echo-hq redirect also reports.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}')])

    def run(session, command):
        payload = _payload(root, tr, session, 'Bash', {'command': command})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('EX1', 'echo x > src/app.py')
    assert 'SPEC.md' in run('EX2',
                            f'hq artifacts {_SLUG}; sed -i "s/x/y/" src/app.py')
    assert 'SPEC.md' in run('EX3', f'echo "hq open {_SLUG}" > src/app.py')


def test_gate_denies_read_credit_for_a_redirected_cat(monkeypatch, capsys,
                                                      tmp_path):
    """Verify cat redirected to a file does not clear a gated path.

    Mutation: stripping only the redirect target from the segment tokens but
    still counting the segment as a read, so cat f > /tmp/copy.md silences
    the gate even though the content never reached the model. Also splitting
    the segments on the `&` of `&>`, which leaves the read verb in a segment
    carrying no redirect at all.
    Oracle: a spy on stdout - ls correctly still reports (control), and the
    cat redirected by `>` and by `&>` both still report; a cat whose stderr
    alone is redirected keeps its credit and reports nothing.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    spec = folder / 'SPEC.md'
    armed = [_bash(f'python3 bin/hq.py open {_SLUG}')]

    def run(session, extra):
        tr = _transcript(tmp_path / f'{session}.jsonl', armed + extra)
        payload = _payload(root, tr, session, 'Edit',
                           {'file_path': 'src/app.py'})
        return _run(monkeypatch, capsys, handoff_gate, payload)

    assert 'SPEC.md' in run('RR1', [_bash(f'ls -l {spec}')])
    assert 'SPEC.md' in run('RR2', [_bash(f'cat {spec} > /tmp/copy.md')])
    assert 'SPEC.md' in run('RR3', [_bash(f'cat {spec} &> /tmp/copy.md')])
    assert 'SPEC.md' not in run('RR4', [_bash(f'cat {spec} 2>/dev/null')])


def test_gate_ignores_hq_open_inside_a_heredoc_body(monkeypatch, capsys,
                                                    tmp_path):
    """Verify a mention of hq open in a heredoc body does not re-arm the gate.

    Mutation: running _OPEN_VERB.finditer on the raw command instead of the
    heredoc-stripped command, so prose naming hq open inside a batch body
    overwrites the armed slug with a garbage word, resolving to no folder.
    Oracle: a spy on stdout - the write after the note command still reports
    SPEC.md; the state slug is the real slug, not the word from the body.
    """
    root, folder = _handoff_root(tmp_path)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    note_cmd = (
        f"hq note {_SLUG} --batch <<'ROWS'\n"
        "The reader runs hq open by hand.\n"
        "ROWS"
    )
    tr = _transcript(tmp_path / 't.jsonl',
                     [_bash(f'python3 bin/hq.py open {_SLUG}'),
                      _bash(note_cmd)])
    payload = _payload(root, tr, 'HA1', 'Edit', {'file_path': 'src/app.py'})
    out = _run(monkeypatch, capsys, handoff_gate, payload)
    assert 'SPEC.md' in out
    state_path = (tmp_path / 'state') / 'HA1.handoff.json'
    assert json.loads(state_path.read_text())['gate']['slug'] == _SLUG
