"""Tests for hq.py artifacts verb column filters (FEATURE-ledger-queries section 2)."""

import pathlib
from typing import Any

from bin import hq

_SLUG = 'test-slug'
_NOW = '2026-09-09T12:00:00'


def _new_folder(
    tmp_path: Any,
    monkeypatch: Any,
    slug: str = _SLUG,
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

    Returns
    -------
    pathlib.Path
        The handoff folder path, already created on disk.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
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


def test_each_column_flag_selects_on_its_own_ledger_field(
    tmp_path, monkeypatch, capsys
):
    """Each filter flag selects on its own column, not a neighboring one.

    Mutation: --kind wired to read_before, or --successor wired to where,
    so a fixture where the fields agree hides the mismatch.
    Oracle: hand-listed expected paths per flag, built from a fixture where
    kind, read_before, and successor deliberately disagree per row.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'a.md').write_text('a\n')
    (folder / 'b.md').write_text('b\n')
    (folder / 'c.md').write_text('c\n')
    _write_row(folder, 'a.md', kind='spec', read_before='always')
    _write_row(folder, 'b.md', kind='draft', read_before='edit')
    _write_row(folder, 'c.md', kind='other', read_before='never', successor='a.md')

    _, out_kind = _run(['artifacts', _SLUG, '--kind', 'spec'], capsys)
    assert 'a.md' in out_kind
    assert 'b.md' not in out_kind
    assert 'c.md' not in out_kind

    _, out_rb = _run(['artifacts', _SLUG, '--read-before', 'edit'], capsys)
    assert 'b.md' in out_rb
    assert 'a.md' not in out_rb
    assert 'c.md' not in out_rb

    _, out_succ = _run(['artifacts', _SLUG, '--successor', 'a.md'], capsys)
    assert 'c.md' in out_succ
    assert 'a.md' not in out_succ
    assert 'b.md' not in out_succ


def test_status_replaces_the_implicit_live_filter(tmp_path, monkeypatch, capsys):
    """--status archived reaches archived rows; the bare run reaches live ones only.

    Mutation: the `status == 'live'` test left beside the new filter, so
    --status archived prints nothing and the verb can never reach a non-live row.
    Oracle: the two rows' status fields against the two runs' output sets.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'live.md').write_text('live\n')
    (folder / 'arch.md').write_text('arch\n')
    _write_row(folder, 'live.md', status='live', label='keep')
    _write_row(folder, 'arch.md', status='archived', label='old')

    _, out_bare = _run(['artifacts', _SLUG], capsys)
    assert 'live.md' in out_bare
    assert 'arch.md' not in out_bare

    _, out_arch = _run(['artifacts', _SLUG, '--status', 'archived'], capsys)
    assert 'arch.md' in out_arch
    assert 'live.md' not in out_arch


def test_in_cycle_selects_the_current_rows_cycle_not_every_row(
    tmp_path, monkeypatch, capsys
):
    """--in-cycle matches the path's latest row only, not a stale earlier one.

    Mutation: filtering the raw rows list instead of the latest_rows fold,
    which shows spec.md for --in-cycle 1 via the stale cycle-1 row.
    Oracle: hq.latest_rows over the test's own row list, computed
    independently, confirms spec.md's current cycle is 2.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'spec.md').write_text('spec\n')
    _write_row(folder, 'spec.md', cycle='1', kind='spec')
    _write_row(folder, 'spec.md', cycle='2', kind='spec')

    # Oracle: latest_rows independently confirms the current cycle is 2.
    raw = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    assert hq.latest_rows(raw)['spec.md']['cycle'] == '2'

    _, out_c1 = _run(['artifacts', _SLUG, '--in-cycle', '1'], capsys)
    assert 'spec.md' not in out_c1

    _, out_c2 = _run(['artifacts', _SLUG, '--in-cycle', '2'], capsys)
    assert 'spec.md' in out_c2

    # Also accept the cN spelling.
    _, out_c2_token = _run(['artifacts', _SLUG, '--in-cycle', 'c2'], capsys)
    assert 'spec.md' in out_c2_token


def test_a_filter_drops_the_walk_only_entries(tmp_path, monkeypatch, capsys):
    """A filtered run suppresses unstamped entries that carry no field to select on.

    Mutation: rows_only suppression dropped, so a filtered run prints
    entries that carry no field the filter could have selected on.
    Oracle: the bare run's own output as the comparand: the unstamped
    line present in one, absent in the other, matched spec row identical.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'stamped.md').write_text('stamped\n')
    (folder / 'unstamped.md').write_text('unstamped\n')
    _write_row(folder, 'stamped.md', kind='spec', read_before='always')

    _, out_bare = _run(['artifacts', _SLUG], capsys)
    assert 'unstamped' in out_bare

    _, out_filtered = _run(['artifacts', _SLUG, '--kind', 'spec'], capsys)
    assert 'unstamped' not in out_filtered
    # The matched spec row's line is present in both.
    spec_line = next(ln for ln in out_bare.splitlines() if 'stamped.md' in ln)
    assert spec_line in out_filtered


def test_an_empty_filter_result_prints_its_line_at_exit_zero(
    tmp_path, monkeypatch, capsys
):
    """An empty filter result exits 0 and prints its one message.

    Mutation: an empty result exiting 1, or printing nothing, so a caller
    cannot tell an empty answer from a swallowed error.
    Oracle: (rc, out.strip()) compared to (0, the documented message).
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'spec.md').write_text('spec\n')
    _write_row(folder, 'spec.md', kind='spec')

    rc, out = _run(['artifacts', _SLUG, '--kind', 'snapshot'], capsys)

    expected = (
        f'hq artifacts: no row matches those flags: {_SLUG}'
        ' - drop one, or run the verb bare for every live row'
    )
    assert rc == 0
    assert out.strip() == expected


def test_a_filtered_line_is_the_bare_runs_line(tmp_path, monkeypatch, capsys):
    """A filtered run prints the same line as the bare run for a matched row.

    Mutation: a filtered run emitting a reduced or reordered field set,
    so a caller's parser has to know which flags were passed.
    Oracle: the bare run's line for that path, from its own stdout.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'spec.md').write_text('spec\n')
    _write_row(folder, 'spec.md', kind='spec', read_before='always', label='my-label')

    _, out_bare = _run(['artifacts', _SLUG], capsys)
    spec_line = next(ln for ln in out_bare.splitlines() if 'spec.md' in ln)

    _, out_kind = _run(['artifacts', _SLUG, '--kind', 'spec'], capsys)
    assert spec_line in out_kind.splitlines()

    _, out_rb = _run(['artifacts', _SLUG, '--read-before', 'always'], capsys)
    assert spec_line in out_rb.splitlines()


def test_the_bare_run_counts_every_row_it_held_back(tmp_path, monkeypatch, capsys):
    """A bare run closes with one count line per non-live status it dropped.

    Mutation: dropping the count block, so a bare run answers a stamped
    path with silence at exit 0; or folding live rows into the counts.
    Oracle: three hand-placed non-live rows against one live row, and
    the row count each named --status run prints back.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'live.md').write_text('live\n')
    (folder / 'gone-successor.md').write_text('successor\n')
    _write_row(folder, 'live.md', read_before='edit', label='the live one')
    _write_row(
        folder, 'old.md', status='superseded', successor='gone-successor.md')
    _write_row(folder, 'done.md', status='archived', reason='consumed')
    _write_row(folder, 'vanished.md', status='missing')

    _, out = _run(['artifacts', _SLUG], capsys)
    lines = out.splitlines()

    assert 'live.md  notes  edit  c1  the live one' in lines
    assert lines[-3:] == [
        f'superseded 1  - hq artifacts {_SLUG} --status superseded',
        f'archived 1  - hq artifacts {_SLUG} --status archived',
        f'missing 1  - hq artifacts {_SLUG} --status missing',
        ]

    for status, path in (
            ('superseded', 'old.md'),
            ('archived', 'done.md'),
            ('missing', 'vanished.md')):
        _, out_status = _run(['artifacts', _SLUG, '--status', status], capsys)
        assert [ln.split('  ')[0] for ln in out_status.splitlines()] == [path]


def test_an_absolute_path_that_never_arrived_is_counted_not_dropped(
        tmp_path, monkeypatch, capsys):
    """A live abs row with no file on disk leaves a missing count behind.

    Mutation: the bare run printing only the rows the folder walk sees,
    so a file stamped outside the folder and never written vanishes from
    the verb while its ledger row still reads live.
    Oracle: the one count line, against hq when, which reports the row
    exactly as the ledger stored it.
    """
    folder = _new_folder(tmp_path, monkeypatch)
    (folder / 'notes.md').write_text('notes\n')
    _write_row(folder, 'notes.md', read_before='edit', label='in the folder')
    outside = pathlib.Path(tmp_path) / 'elsewhere' / 'REPORT.md'
    _write_row(
        folder, str(outside), base='abs', kind='other',
        read_before='mention', label='filed in a sibling repo')

    _, out = _run(['artifacts', _SLUG], capsys)
    assert str(outside) not in out
    assert out.splitlines()[-1] == (
        f'missing 1  - hq artifacts {_SLUG} --status missing')

    _, out_when = _run(['when', _SLUG, str(outside)], capsys)
    assert out_when.split('\t')[2] == 'live'
