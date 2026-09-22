"""Tests for --whole and --section on hq read (spec slice a1-read-reach).

Each test targets a specific mutation named in its Mutation: line.
"""

import contextlib
import io
import os
import pathlib
from typing import Any

from bin import hq

_SLUG = 'reach-slug'
_SESSION = 'session-reach'
_NOW = '2026-09-15T10:00:00'

_SPEC_TEXT = (
    '# Spec\n'
    '\n'
    '## 1. Scope\n'
    '\n'
    'The scope section.\n'
    '\n'
    '## 2. Summary\n'
    '\n'
    'The summary section.\n'
    '\n'
    '## 3. Implementation\n'
    '\n'
    'The implementation section.\n'
    '\n'
    '## 4. Risks\n'
    '\n'
    'The risks section.\n'
)


def _new_root(
    tmp_path: 'os.PathLike[str]',
    monkeypatch: Any,
) -> pathlib.Path:
    """Return the .handoff/slug folder and configure HQ_* env vars.

    Parameters
    ----------
    tmp_path : os.PathLike[str]
        pytest tmp_path fixture; becomes HQ_STATE_DIR.
    monkeypatch : Any
        pytest monkeypatch fixture.

    Returns
    -------
    pathlib.Path
        Path to .handoff/<_SLUG> under the temp root.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / _SLUG


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Invoke hq.main() and return (exit_code, stdout, stderr).

    Parameters
    ----------
    argv : list[str]
        Argument list passed to hq.main.

    Returns
    -------
    tuple[int, str, str]
        Exit code, captured stdout, captured stderr.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def test_whole_prints_past_the_anchored_span(tmp_path, monkeypatch):
    """--whole reads the entire file even when the row anchors only s1.

    Mutation: The --whole branch written as `where = section or row['where']`,
    so the flag is accepted and ignored and the stored s1 span still prints.
    Oracle: The file's own text, read in the test: stdout.splitlines()
    equals (folder / 'SPEC.md').read_text().splitlines().
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(_SPEC_TEXT)
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0

    rc, out, _ = _run(['read', _SLUG, 'SPEC.md', '--whole'])

    assert rc == 0
    assert out.splitlines() == (folder / 'SPEC.md').read_text().splitlines()


def test_section_resolves_an_anchor_the_row_does_not_carry(tmp_path, monkeypatch):
    """--section s3 prints s3, and earns no receipt, when the row wants s1.

    Mutation: --section falling through to row['where'], so resolve_where
    is called on s1 and s3 content never appears in stdout; or the
    section's own span counting as the row's required content, so reading
    one section discharges the obligation to read another.
    Oracle: hq.resolve_where(text, ['s3']) computed in the test, compared
    with stdout; s1 heading absent from output confirms isolation, and
    the remedy line names the bare form that reads s1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(_SPEC_TEXT)
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0

    rc, out, _ = _run(['read', _SLUG, 'SPEC.md', '--section', 's3'])

    assert rc == 0
    spans, unresolved = hq.resolve_where(_SPEC_TEXT, ['s3'])
    assert not unresolved
    file_lines = _SPEC_TEXT.splitlines()
    expected = '\n'.join(
        '\n'.join(file_lines[sp[0] - 1:sp[1]]) for sp in spans)
    assert out.startswith(expected)
    assert '## 1. Scope' not in out
    assert out.strip().endswith(
        'hq read: no receipt for SPEC.md - the gate still reports it;'
        f' run: hq read {_SLUG} SPEC.md')


def test_section_takes_the_joined_anchor_grammar(tmp_path, monkeypatch):
    """--section 's2;Risks' resolves both anchors in order.

    Mutation: The flag value passed to resolve_where as a one-element list
    without _split_where, so 's2;Risks' finds no heading and only the
    unresolved marker prints.
    Oracle: Two hand-computed spans at known lines of _SPEC_TEXT,
    concatenated in anchor order (s2 first, Risks second).
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(_SPEC_TEXT)
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0

    rc, out, _ = _run(['read', _SLUG, 'SPEC.md', '--section', 's2;Risks'])

    assert rc == 0
    file_lines = _SPEC_TEXT.splitlines()
    # s2 (## 2. Summary) at lines 7-10 in the fixture
    s2_block = '\n'.join(file_lines[6:10])
    # Risks (## 4. Risks) at lines 15-17 in the fixture
    risks_block = '\n'.join(file_lines[14:17])
    expected = s2_block + '\n' + risks_block + '\n'
    assert out.startswith(expected)


def test_an_unresolved_section_earns_no_receipt_and_exits_zero(tmp_path, monkeypatch):
    """--section Ghost exits 0, prints the marker, and records no receipt.

    Mutation: the receipt written whatever the read printed, so a command
    that resolved no span discharges the artifact's read obligation; or
    the unresolved read exiting 1, which costs a caller the diagnostic.
    Oracle: exit code 0, the unresolved marker pinned in
    test_hq_messages.py, a remedy naming the bare form the row's own
    anchor answers, and no receipt file at all.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(_SPEC_TEXT)
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0

    rc, out, _ = _run(['read', _SLUG, 'SPEC.md', '--section', 'Ghost'])

    assert rc == 0
    assert out.strip() == (
        '? unresolved: Ghost'
        ' - use --whole to read the whole file when no span printed above\n'
        'hq read: no receipt for SPEC.md - the gate still reports it;'
        f' run: hq read {_SLUG} SPEC.md')
    assert not (pathlib.Path(tmp_path) / f'hq-reads-{_SESSION}.txt').exists()


def test_the_receipt_follows_the_content_the_row_requires(
        tmp_path, monkeypatch):
    """Bare and --whole each append one identical line; a foreign section none.

    Mutation: the receipt write moved inside the anchored branch, so
    --whole skips it; or written for every form, so reading s3 clears a
    row that requires s1.
    Oracle: the bare form's own receipt line as comparand - the file
    holds it twice after --whole and still twice after --section s3.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(_SPEC_TEXT)
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0

    assert _run(['read', _SLUG, 'SPEC.md'])[0] == 0
    assert _run(['read', _SLUG, 'SPEC.md', '--whole'])[0] == 0
    assert _run(['read', _SLUG, 'SPEC.md', '--section', 's3'])[0] == 0

    receipt = pathlib.Path(tmp_path) / f'hq-reads-{_SESSION}.txt'
    lines = receipt.read_text().splitlines()
    assert len(lines) == 2
    assert lines[0] == lines[1]


def test_whole_with_section_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    """--whole --section s1 exits 2 with the guard message and no receipt.

    Mutation: The guard dropped so --whole silently wins, or the guard
    placed after the receipt write so a refused command still claims
    gate credit.
    Oracle: Exit code 2, the exact guard text, and absence of the receipt
    path in HQ_STATE_DIR.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(_SPEC_TEXT)
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0

    rc, out, _ = _run(['read', _SLUG, 'SPEC.md', '--whole', '--section', 's1'])

    assert rc == 2
    assert out.strip() == (
        'hq read: --whole and --section name different reads'
        ' - pass one or the other')
    receipt = pathlib.Path(tmp_path) / f'hq-reads-{_SESSION}.txt'
    assert not receipt.exists()
