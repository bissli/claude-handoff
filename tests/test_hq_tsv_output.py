"""Tests for hq.py --tsv output on artifacts, when, standing, and list."""

import pathlib
from typing import Any

from scripts import hq

_SLUG = 'test-slug'
_NOW = '2026-09-09T12:00:00'


def _new_folder(
    tmp_path: Any,
    monkeypatch: Any,
    slug: str = _SLUG,
    cycle: str = '1',
) -> pathlib.Path:
    """Create the handoff folder and set all env vars; return its path.

    Parameters
    ----------
    tmp_path : Any
        Pytest temporary path; HQ_ROOT is placed at tmp_path / 'root'.
    monkeypatch : Any
        Active pytest monkeypatch fixture.
    slug : str, optional
        Handoff slug, by default _SLUG.
    cycle : str, optional
        HQ_CYCLE value, by default '1'.

    Returns
    -------
    pathlib.Path
        The handoff folder path, already created on disk.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', 'session-abc')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True)
    return folder


def _run(argv: list, capsys: Any) -> tuple:
    """Call hq.main and return (rc, stdout).
    """
    rc = hq.main(argv)
    out, _ = capsys.readouterr()
    return rc, out


def _write_row(folder: pathlib.Path, path: str, **kwargs: str) -> None:
    """Append one ledger row with sensible defaults.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder whose ledger.tsv receives the row.
    path : str
        The path key for the row, relative to the folder.
    **kwargs : str
        Field overrides; anything not named keeps its default.
    """
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({
        'cycle': '1',
        'ts': _NOW,
        'base': 'folder',
        'kind': 'notes',
        'status': 'live',
        'read_before': 'never',
        'label': 'test',
        'path': path,
        })
    row.update(kwargs)
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS, row, hq._LEDGER_HEADER)


def test_artifacts_tsv_lines_are_the_ledger_lines(tmp_path, monkeypatch, capsys):
    """Each --tsv data line is byte-identical to the matching ledger.tsv line.

    Mutation: a column dropped, added, or reordered in the --tsv output,
    or the fields joined with a space instead of a tab.
    Oracle: ledger.tsv's own bytes, read in the test and matched line for
    line against the --tsv output.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'a.md').write_text('a\n')
    (folder / 'b.md').write_text('b\n')
    _write_row(folder, 'a.md', kind='spec', read_before='always', label='alpha')
    _write_row(folder, 'b.md', kind='draft', read_before='edit', label='beta')

    rc, out = _run(['artifacts', _SLUG, '--tsv'], capsys)
    assert rc == 0

    tsv_lines = out.splitlines()
    assert tsv_lines[0] == hq._LEDGER_HEADER

    ledger_lines = (folder / 'ledger.tsv').read_text(encoding='utf-8').splitlines()
    # ledger.tsv line 0 is the header; data lines follow.
    assert tsv_lines[1:] == ledger_lines[1:]


def test_when_tsv_prints_every_column_and_the_bare_form_keeps_seven(
    tmp_path, monkeypatch, capsys
):
    """When --tsv prints 13 columns; each value sits under its own header.

    Mutation: columns reordered in the tsv branch of _verb_when, or --tsv
    reusing the seven-column bare list so a cut on a specific field index
    returns the wrong value.
    Oracle: header parsed and each value keyed by its column name; label and
    path pulled by name and compared to what _write_row wrote.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'a.md').write_text('a\n')
    _write_row(folder, 'a.md', label='my-label')

    _, out_bare = _run(['when', _SLUG, 'a.md'], capsys)
    _, out_tsv = _run(['when', _SLUG, 'a.md', '--tsv'], capsys)

    bare_lines = [ln for ln in out_bare.splitlines() if ln]
    tsv_lines = out_tsv.splitlines()

    assert tsv_lines[0] == hq._LEDGER_HEADER
    data_line = tsv_lines[1]
    header_cols = tsv_lines[0].split('\t')
    data_cols = data_line.split('\t')
    assert len(data_cols) == len(hq.LEDGER_FIELDS)
    row_by_col = dict(zip(header_cols, data_cols))
    assert row_by_col['path'] == 'a.md'
    assert row_by_col['label'] == 'my-label'
    assert row_by_col['cycle'] == '1'
    assert row_by_col['kind'] == 'notes'

    assert len(bare_lines[0].split('\t')) == 7


def test_standing_tsv_names_its_columns_and_follows_the_chain_to_its_end(
    tmp_path, monkeypatch, capsys
):
    """Standing --tsv header matches spec; d01 in a two-hop chain names d03.

    Mutation: superseded_by naming the first hop d02 instead of the chain
    end d03, or the superseded flag absent so an item superseded with no
    edge is indistinguishable from a live one.
    Oracle: a hand-built two-hop chain in standing.md, with d03 and its
    cycle written by the test.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'standing.md').write_text(
        '- [d01] (c1) **First** first body\n'
        '- [d02] (c2) **Second** second body\n'
        '- [d03] (c3) **Third** third body\n'
        '- (c2) d01 -> d02\n'
        '- (c3) d02 -> d03\n',
        encoding='utf-8')

    rc, out = _run(['standing', _SLUG, '--tsv', '--all'], capsys)
    assert rc == 0

    expected_header = (
        'id\tprefix\tcycle\theadline\tbody'
        '\tsuperseded\tsuperseded_by\tsuperseded_in'
    )
    lines = out.splitlines()
    assert lines[0] == expected_header

    # Find d01's row and verify headline, body, chain, and supersession cols.
    d01_row = next(ln for ln in lines[1:] if ln.startswith('d01\t'))
    cols = d01_row.split('\t')
    assert cols[3] == 'First'
    assert cols[4] == 'first body'
    assert cols[5] == 'yes'
    assert cols[6] == 'd03'
    assert cols[7] == '3'


def test_list_tsv_drops_the_padding_and_the_c_prefix(
    tmp_path, monkeypatch, capsys
):
    """List --tsv emits bare numbers with no padding; cycle drops the 'c'.

    Mutation: --tsv falling through to the padded table, or the cycle
    column keeping 'c5' so cut -f3 yields a non-number.
    Oracle: the same folder's bare list output, fields split on two or
    more spaces, compared field by field to the tsv row.
    """
    folder = _new_folder(tmp_path, monkeypatch, cycle='5')
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-09-01 | Cycle: 5 | host @ abc1234\n\n'
        '## Task\n\nBuild the export pipeline.\n\n'
        '## Plan\n\n'
        '- [x] step one\n'
        '- [ ] step two\n',
        encoding='utf-8')

    _, out_bare = _run(['list'], capsys)
    _, out_tsv = _run(['list', '--tsv'], capsys)

    tsv_lines = out_tsv.splitlines()
    assert tsv_lines[0] == 'slug\twritten\tcycle\tprogress\ttask'
    assert not any('--' in ln for ln in tsv_lines)

    # The bare table has a data line; split on runs of two+ spaces.
    import re as _re
    data_line = out_bare.splitlines()[2]
    bare_fields = _re.split(r'  +', data_line.strip())

    tsv_data = tsv_lines[1].split('\t')
    assert tsv_data[0] == bare_fields[0]
    assert tsv_data[1] == bare_fields[1]
    # Bare shows 'c5'; tsv shows '5'.
    assert tsv_data[2] == '5'
    assert bare_fields[2] == 'c5'
    assert tsv_data[3] == bare_fields[3]


def test_tsv_prints_one_header_and_nothing_else_on_an_empty_result(
    tmp_path, monkeypatch, capsys
):
    """An empty --tsv run prints exactly one line: the header.

    Mutation: a prose no-result line leaking into a tsv run, which puts
    an unparseable line in a stream a caller feeds to cut.
    Oracle: out.splitlines() of length 1, equal to the verb's header.
    """
    # Case 1: list --tsv under a root holding no HANDOFF.md.
    root = pathlib.Path(tmp_path) / 'empty-root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')

    _, out_list = _run(['list', '--tsv'], capsys)
    assert out_list.splitlines() == ['slug\twritten\tcycle\tprogress\ttask']

    # Case 2: artifacts --tsv --kind snapshot against a thread with no snapshot.
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'spec.md').write_text('spec\n')
    _write_row(folder, 'spec.md', kind='spec')

    _, out_art = _run(['artifacts', _SLUG, '--tsv', '--kind', 'snapshot'], capsys)
    assert out_art.splitlines() == [hq._LEDGER_HEADER]


def test_append_tsv_folds_control_chars_in_values(tmp_path):
    """_append_tsv folds tab, CR, and LF inside a value to spaces.

    Mutation: the fold expressions removed from _append_tsv, so a newline
    in a value splits a single row into two ledger lines and _read_tsv
    returns two rows instead of one; a tab shifts every later field right.
    Oracle: _read_tsv returns exactly one data row; the label and reason
    fields read back with spaces, not the original control characters.
    """
    ledger = tmp_path / 'ledger.tsv'
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row['label'] = 'line1\nline2'
    row['reason'] = 'tab\there'
    hq._append_tsv(ledger, hq.LEDGER_FIELDS, row, hq._LEDGER_HEADER)
    rows = hq._read_tsv(ledger, hq.LEDGER_FIELDS)
    assert len(rows) == 1
    assert rows[0]['label'] == 'line1 line2'
    assert rows[0]['reason'] == 'tab here'


def test_tsv_line_folds_control_chars_and_agrees_with_append_tsv(tmp_path):
    """_tsv_line folds tab, CR, and LF to spaces; its fold matches _append_tsv.

    Mutation: the fold in _tsv_line removed, so a tab in a value adds a
    spurious column and the parsed field count exceeds len(fields).
    Oracle: splitting on tab yields exactly len(LEDGER_FIELDS) parts; the
    label slot contains spaces; _read_tsv returns the same label value.
    """
    fields = hq.LEDGER_FIELDS
    values = dict.fromkeys(fields, '-')
    values['label'] = 'a\tb\rc\nd'
    line = hq._tsv_line([values[f] for f in fields])
    parts = line.split('\t')
    assert len(parts) == len(fields)
    label_idx = fields.index('label')
    assert parts[label_idx] == 'a b c d'
    ledger = tmp_path / 'ledger.tsv'
    hq._append_tsv(ledger, fields, values, hq._LEDGER_HEADER)
    rows = hq._read_tsv(ledger, fields)
    assert rows[0]['label'] == parts[label_idx]
