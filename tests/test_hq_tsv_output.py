"""Tests for hq.py's internal tab-separated ledger row encoding."""

import pathlib

from bin import hq


def test_append_tsv_folds_control_chars_in_values(tmp_path: pathlib.Path) -> None:
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
