"""Tests for the hq.py list verb and the missing-live work-list line."""

import datetime
import os
import pathlib
import shutil
import threading
from typing import Any

from bin import hq

_SLUG = 'test-slug'
_SESSION = 'session-abc'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'


def _new_root(
    tmp_path: 'os.PathLike[str]',
    monkeypatch: Any,
    slug: str = _SLUG,
) -> 'pathlib.Path':
    """Set HQ_ROOT and required env vars; return the expected folder path.

    Parameters
    ----------
    tmp_path : os.PathLike[str]
        Pytest temporary path.
    monkeypatch : Any
        Active pytest monkeypatch fixture.
    slug : str, optional
        Handoff slug, by default _SLUG.

    Returns
    -------
    pathlib.Path
        Expected folder path (not yet created).
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root


def _run(argv: list, capsys) -> 'tuple[int, str, str]':
    """Call hq.main and return (rc, stdout, stderr).
    """
    rc = hq.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def test_list_orders_newest_first(tmp_path, monkeypatch, capsys):
    """List prints the folder whose HANDOFF.md has a newer mtime first.

    Mutation: sort ascending, or sort by name instead of mtime.
    Oracle: line order when the alphabetically-first folder is older.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()

    alpha = handoff_root / 'alpha-first'
    alpha.mkdir()
    (alpha / 'HANDOFF.md').write_text('# x\n', encoding='utf-8')

    beta = handoff_root / 'beta-second'
    beta.mkdir()
    (beta / 'HANDOFF.md').write_text('# x\n', encoding='utf-8')

    # alpha is alphabetically first but gets an OLDER mtime.
    os.utime(alpha / 'HANDOFF.md', (1000000, 1000000))
    os.utime(beta / 'HANDOFF.md', (2000000, 2000000))

    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    data_lines = out.splitlines()[2:]
    assert len(data_lines) == 2
    assert data_lines[0].startswith('beta-second')
    assert data_lines[1].startswith('alpha-first')


def test_list_count_limits_output(tmp_path, monkeypatch, capsys):
    """List count=1 prints one line; count=99 prints all; count=0 exits 2.

    Mutation: off-by-one in the slice, or the count guard dropped.
    Oracle: line count for the finite cap, message and code for zero.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()

    for i in range(3):
        folder = handoff_root / f'slug-{i:02d}'
        folder.mkdir()
        h = folder / 'HANDOFF.md'
        h.write_text('# x\n', encoding='utf-8')
        os.utime(h, (1000000 + i, 1000000 + i))

    rc1, out1, _ = _run(['list', '1'], capsys)
    assert rc1 == 0
    assert len(out1.splitlines()[2:]) == 1

    rc99, out99, _ = _run(['list', '99'], capsys)
    assert rc99 == 0
    assert len(out99.splitlines()[2:]) == 3

    rc0, out0, _ = _run(['list', '0'], capsys)
    assert rc0 == 2
    assert out0.strip() == 'hq list: count must be a positive integer'


def test_list_fields_conforming_file(tmp_path, monkeypatch, capsys):
    """List produces the correct five fields for a conforming HANDOFF.md.

    Mutation: counting checkboxes outside Plan (gives 3 open), not
    counting the indented one (gives 1 open), counting done items (gives
    3 open), or reading the wrong header token.
    Oracle: hand-computed line for a known fixture.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()

    slug = 'feature-x'
    folder = handoff_root / slug
    folder.mkdir()
    task_line = 'Implement the export pipeline.'
    h = folder / 'HANDOFF.md'
    h.write_text(
        '# Handoff: feature-x\n\n'
        'Written: 2026-09-01 | Cycle: 3 | host @ abc1234\n\n'
        '## Task\n\n' + task_line + '\n\n'
        '## Plan\n\n'
        '- [x] step one\n'
        '- [ ] step two\n'
        '  - [ ] sub-item\n\n'
        '## State\n\n'
        '- [ ] state item\n',
        encoding='utf-8')

    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    # Skip header and separator; CYCLE and PROGRESS columns widen to fit
    # their header labels (5 and 8 chars), so c3 pads to 5 and 2 open
    # to 8.
    data_line = out.splitlines()[2]
    assert data_line == f'{slug}  2026-09-01   c3     2 open    {task_line}'


def test_list_no_plan_checkboxes_prints_dash(tmp_path, monkeypatch, capsys):
    """List prints '-' for progress when Plan has only numbered items or no
    Plan, and '0 open' when every checkbox item is done.

    Mutation: f'{open_cnt} open' unconditionally, producing '0 open' for
    no checkbox; or the guard testing the open count, producing '-' for
    an all-done Plan.
    Oracle: the exact field is '-' for a numbered-only Plan and a missing
    Plan, and '0 open' for an all-done Plan.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()

    # Folder with a Plan that has only numbered items (no checkboxes).
    numbered = handoff_root / 'slug-numbered'
    numbered.mkdir()
    (numbered / 'HANDOFF.md').write_text(
        '# Handoff: x\n\nWritten: 2026-09-01 | Cycle: 1 | h @ a\n\n'
        '## Task\n\nDo it.\n\n## Plan\n\n1. First thing\n2. Second thing\n',
        encoding='utf-8')

    # Folder with no Plan section at all.
    noplan = handoff_root / 'slug-noplan'
    noplan.mkdir()
    (noplan / 'HANDOFF.md').write_text(
        '# Handoff: x\n\nWritten: 2026-09-02 | Cycle: 2 | h @ a\n\n'
        '## Task\n\nDo it.\n',
        encoding='utf-8')

    # Folder whose Plan items are all done.
    alldone = handoff_root / 'slug-alldone'
    alldone.mkdir()
    (alldone / 'HANDOFF.md').write_text(
        '# Handoff: x\n\nWritten: 2026-09-03 | Cycle: 3 | h @ a\n\n'
        '## Task\n\nDo it.\n\n## Plan\n\n- [x] First thing\n',
        encoding='utf-8')

    os.utime(alldone / 'HANDOFF.md', (3000000, 3000000))
    os.utime(noplan / 'HANDOFF.md', (2000000, 2000000))
    os.utime(numbered / 'HANDOFF.md', (1000000, 1000000))

    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    # Skip header and separator; strip empty tokens from padding.
    progress = [
        [p.strip() for p in line.split('  ') if p.strip()][3]
        for line in out.splitlines()[2:]]
    assert progress == ['0 open', '-', '-']


def test_list_non_conforming_file_prints_dashes(tmp_path, monkeypatch, capsys):
    """List prints '-  -' for date and cycle on a non-conforming HANDOFF.md.

    Mutation: skipping non-conforming folders entirely.
    Oracle: the slug still appears with '-' for both date and cycle.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()

    slug = 'legacy-work'
    folder = handoff_root / slug
    folder.mkdir()
    (folder / 'HANDOFF.md').write_text(
        '# Legacy CSV importer\n\n## Summary\n\nOld work.\n',
        encoding='utf-8')

    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    # Data starts at line index 2 (after header and separator).
    data_line = out.splitlines()[2]
    assert data_line.startswith(slug)
    parts = [p.strip() for p in data_line.split('  ') if p.strip()]
    assert parts[1] == '-', f'expected - for date: {data_line!r}'
    assert parts[2] == '-', f'expected - for cycle: {data_line!r}'


def test_list_no_handoff_prints_message_and_returns_0(tmp_path, monkeypatch, capsys):
    """List prints 'no handoff' and returns 0 when nothing qualifies.

    Mutation: return 1, or crashing on the missing directory.
    Oracle: message and code for both missing dir and empty .handoff/.
    """
    root = _new_root(tmp_path, monkeypatch)

    # Case 1: .handoff/ does not exist.
    rc1, out1, _ = _run(['list'], capsys)
    assert rc1 == 0
    assert f'hq list: no handoff under {root / ".handoff"}' in out1.strip()

    # Case 2: .handoff/ exists but contains no HANDOFF.md.
    (root / '.handoff').mkdir()
    (root / '.handoff' / 'slug-a').mkdir()
    rc2, out2, _ = _run(['list'], capsys)
    assert rc2 == 0
    assert f'hq list: no handoff under {root / ".handoff"}' in out2.strip()


def test_begin_missing_live_shows_hq_when_hint(tmp_path, monkeypatch, capsys):
    """Begin prints '  missing live: <path> - hq when <slug> <path>'.

    Mutation: the suffix dropped from the print statement.
    Oracle: the exact line for a stamped file deleted before begin.
    """
    root = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG], capsys)
    assert rc0 == 0
    folder = root / '.handoff' / _SLUG
    gone_file = folder / 'gone.md'
    gone_file.write_text('# Gone\n', encoding='utf-8')
    rc_stamp, _, _ = _run(['stamp', _SLUG, 'gone.md'], capsys)
    assert rc_stamp == 0
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    capsys.readouterr()
    gone_file.unlink()
    monkeypatch.setenv('HQ_CYCLE', '2')

    rc2, out2, _ = _run(['begin', _SLUG], capsys)
    assert rc2 == 0
    missing_lines = [ln for ln in out2.splitlines()
                     if ln.startswith('  missing live: ')]
    assert len(missing_lines) == 1
    assert missing_lines[0] == f'  missing live: gone.md - hq when {_SLUG} gone.md'


def test_list_breaks_mtime_ties_by_slug(tmp_path, monkeypatch, capsys):
    """Folders whose HANDOFF.md share an mtime list A to Z by slug.

    Mutation: the name pre-sort dropped, leaving ties in readdir order,
    or the tie order reversed.
    Oracle: six folders with one mtime print in alphabetical order.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()
    slugs = ['echo', 'alpha', 'foxtrot', 'charlie', 'bravo', 'delta']
    for slug in slugs:
        (handoff_root / slug).mkdir()
        h = handoff_root / slug / 'HANDOFF.md'
        h.write_text('# x\n', encoding='utf-8')
        os.utime(h, (1500000, 1500000))
    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    data_lines = out.splitlines()[2:]
    assert [ln.split('  ')[0].strip() for ln in data_lines] == sorted(slugs)


def test_list_skips_a_fifo_and_a_directory_named_handoff(
        tmp_path, monkeypatch, capsys):
    """A FIFO or a directory named HANDOFF.md is skipped, not opened.

    Mutation: the is_file() filter dropped, which blocks forever on the
    FIFO; the listing runs on a thread so a hang fails within seconds.
    Oracle: only the regular file's slug prints, rc 0, within 5 s.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()
    (handoff_root / 'ok-slug').mkdir()
    (handoff_root / 'ok-slug' / 'HANDOFF.md').write_text('# x\n', encoding='utf-8')
    (handoff_root / 'fifo-slug').mkdir()
    os.mkfifo(handoff_root / 'fifo-slug' / 'HANDOFF.md')
    (handoff_root / 'dir-slug' / 'HANDOFF.md').mkdir(parents=True)
    result: list = []
    worker = threading.Thread(
        target=lambda: result.append(hq.main(['list'])), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), 'list blocked on the FIFO'
    out, _ = capsys.readouterr()
    assert result == [0]
    data_lines = out.splitlines()[2:]
    assert [ln.split('  ')[0].strip() for ln in data_lines] == ['ok-slug']


def test_list_reports_an_unreadable_handoff_and_goes_on(
        tmp_path, monkeypatch, capsys):
    """An unreadable HANDOFF.md prints its slug with dashes; others still list.

    Mutation: the OSError handler dropped, so the whole survey aborts with
    exit 1 at the first unreadable file.
    Oracle: the dashed line names the reason, the good folder follows,
    rc 0.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()
    for slug, stamp in (('locked', 2000000), ('open-one', 1000000)):
        (handoff_root / slug).mkdir()
        h = handoff_root / slug / 'HANDOFF.md'
        h.write_text('# x\n', encoding='utf-8')
        os.utime(h, (stamp, stamp))
    real_read_text = pathlib.Path.read_text

    def locked_read_text(self, *args, **kwargs):
        if self.parent.name == 'locked':
            raise PermissionError(13, 'Permission denied', str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, 'read_text', locked_read_text)
    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    assert out.splitlines() == [
        'SLUG      LAST WORKED  CYCLE  PROGRESS  TASK',
        '--------------------------------------------',
        'locked    -            -      -         unreadable: Permission denied',
        'open-one  -            -      -         -',
        ]


def test_list_parses_task_plan_and_block_edges(tmp_path, monkeypatch, capsys):
    """The Task is the first non-empty line, a padded Plan heading counts,
    and a checkbox inside an hq block does not.

    Mutation: the task == '-' guard dropped (last line wins); the heading
    .strip() dropped ('## Plan   ' counts nothing); the '<!-- hq:' section
    clear dropped (the block's checkbox counts).
    Oracle: the exact data line
    'edges  2026-09-03   c4     1 open    First line.' (LAST WORKED, CYCLE
    and PROGRESS pad to their header widths, 11, 5 and 8).
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    handoff_root.mkdir()
    (handoff_root / 'edges').mkdir()
    (handoff_root / 'edges' / 'HANDOFF.md').write_text(
        '# Handoff: edges\n\n'
        'Written: 2026-09-03 | Cycle: 4 | h @ a\n\n'
        '## Task\n\nFirst line.\nSecond line.\n\n'
        '## Plan   \n\n- [x] done\n- [ ] open\n\n'
        '<!-- hq:artifacts 0123456789ab -->\n'
        '## Artifacts\n- [ ] not a plan item\n'
        '<!-- /hq:artifacts -->\n',
        encoding='utf-8')
    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    assert out.splitlines()[2] == 'edges  2026-09-03   c4     1 open    First line.'


def test_list_truncates_task_to_terminal_width(tmp_path, monkeypatch, capsys):
    """On a terminal, a long Task is cut with ... so the row fits; piped
    output keeps it whole.

    Mutation: no cut, a cut that ignores the prefix columns (row runs past
    the width), a dropped isatty guard (piped Task cut), or an off-by-one
    leaving the row one column wide.
    Oracle: COLUMNS=60, so every data row is exactly 60 columns.
    """
    root = _new_root(tmp_path, monkeypatch)
    folder = root / '.handoff' / 'feature-x'
    folder.mkdir(parents=True)
    task_line = 'Implement the export pipeline for every region. ' * 3
    (folder / 'HANDOFF.md').write_text(
        '# Handoff: feature-x\n\n'
        'Written: 2026-09-01 | Cycle: 3 | host @ abc1234\n\n'
        '## Task\n\n' + task_line + '\n',
        encoding='utf-8')
    monkeypatch.setenv('COLUMNS', '60')

    _, out, _ = _run(['list'], capsys)
    assert out.splitlines()[2].endswith(task_line.strip())

    monkeypatch.setattr(hq.sys.stdout, 'isatty', lambda: True)
    _, out, _ = _run(['list'], capsys)
    data_line = out.splitlines()[2]
    assert len(data_line) == 60
    assert data_line.endswith('...')


def test_list_orders_by_last_worked_then_written_then_mtime(
        tmp_path, monkeypatch, capsys):
    """List sorts by the Last worked date, falls back to Written, puts a
    never-worked thread last, and shows the date it sorts by.

    Mutation: sorting by mtime or by the Written date, a header with no
    Last worked falling through to mtime, or 'Last worked: -' falling
    through to Written.
    Oracle: hand-set dates whose mtime order is the reverse of the
    expected order.
    """
    root = _new_root(tmp_path, monkeypatch)
    handoff_root = root / '.handoff'
    headers = {
        'filed-into': 'Written: 2026-10-16 | Cycle: 5 | Last worked: 2026-10-01',
        'legacy': 'Written: 2026-10-03 | Cycle: 1',
        'own-work': 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-05',
        'spun-off': 'Written: 2026-10-16 | Cycle: 1 | Last worked: -',
        }
    mtimes = {
        'filed-into': 3000000, 'legacy': 2000000, 'own-work': 1000000,
        'spun-off': 4000000,
        }
    for slug, header in headers.items():
        (handoff_root / slug).mkdir(parents=True)
        h = handoff_root / slug / 'HANDOFF.md'
        h.write_text(f'# Handoff: {slug}\n\n{header}\n\n## Task\n\nDo it.\n',
                     encoding='utf-8')
        os.utime(h, (mtimes[slug], mtimes[slug]))
    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    rows = [ln.split()[:2] for ln in out.splitlines()[2:]]
    assert rows == [
        ['own-work', '2026-10-05'],
        ['legacy', '2026-10-03'],
        ['filed-into', '2026-10-01'],
        ['spun-off', '-'],
        ]


def test_list_count_keeps_the_newest_by_date_then_mtime(
        tmp_path, monkeypatch, capsys):
    """List 2 keeps the two newest by date, the newer mtime first.

    Mutation: the count slice taken before the sort, or mtime dropped
    from the sort key.
    Oracle: the A-to-Z-first slug holds the oldest date.
    """
    root = _new_root(tmp_path, monkeypatch)
    for slug, day, mtime in (
            ('aaa', '01', 3000000), ('mmm', '05', 1000000),
            ('zzz', '05', 2000000)):
        (root / '.handoff' / slug).mkdir(parents=True)
        h = root / '.handoff' / slug / 'HANDOFF.md'
        h.write_text(
            f'# Handoff: {slug}\n\nWritten: 2026-10-{day} | Cycle: 1\n',
            encoding='utf-8')
        os.utime(h, (mtime, mtime))
    _, out, _ = _run(['list', '2'], capsys)
    assert [ln.split()[0] for ln in out.splitlines()[2:]] == ['zzz', 'mmm']


def test_list_skips_a_header_value_that_is_not_a_date(
        tmp_path, monkeypatch, capsys):
    """A non-date Last worked falls back to Written; a non-date Written
    shows '-' and sorts by its mtime date.

    Mutation: any token accepted as the date, so letters sort above
    every real date; '-x' read as never worked; a header with no date
    sorted last instead of by mtime.
    Oracle: hand-set headers, and a 2026-10-10 mtime on the dateless
    one.
    """
    root = _new_root(tmp_path, monkeypatch)
    headers = {
        'bad-both': 'Written: banana | Cycle: 1',
        'bad-worked': 'Written: 2026-10-01 | Cycle: 1 | Last worked: oops',
        'dash-junk': 'Written: 2026-10-03 | Cycle: 1 | Last worked: -x',
        'good': 'Written: 2026-10-05 | Cycle: 1 | Last worked: 2026-10-05',
        }
    oct_10 = datetime.datetime(2026, 10, 10, 12).timestamp()
    for slug, header in headers.items():
        (root / '.handoff' / slug).mkdir(parents=True)
        h = root / '.handoff' / slug / 'HANDOFF.md'
        h.write_text(f'# Handoff: {slug}\n\n{header}\n', encoding='utf-8')
        mtime = oct_10 if slug == 'bad-both' else 1000000
        os.utime(h, (mtime, mtime))
    _, out, _ = _run(['list'], capsys)
    assert [ln.split()[:2] for ln in out.splitlines()[2:]] == [
        ['bad-both', '-'],
        ['good', '2026-10-05'],
        ['dash-junk', '2026-10-03'],
        ['bad-worked', '2026-10-01'],
        ]


def _cycle(root, day: str, session: str, monkeypatch) -> str:
    """Run one begin/finish cycle as session on 2026-10-<day>; return
    the header.
    """
    monkeypatch.setenv('HQ_NOW', f'2026-10-{day}T12:00:00')
    monkeypatch.setenv('HQ_SESSION', session)
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['finish', _SLUG, '--log', day]) == 0
    text = (root / '.handoff' / _SLUG / 'HANDOFF.md').read_text()
    return hq.split_handoff(text)['header']


def test_finish_moves_last_worked_only_for_the_worked_thread(
        tmp_path, monkeypatch, capsys):
    """Only a session that created the handoff fresh or opened it moves
    Last worked; a filing session keeps it.

    Mutation: every finish moves the date, creation or open not
    recorded, open --not-carried recorded, or the carry-forward reading
    Written before Last worked.
    Oracle: a creator who begins on 10-01 and finishes on 10-02, then
    filer, filer after open --not-carried, and filer after open.
    """
    root = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_NOW', '2026-10-01T12:00:00')
    monkeypatch.setenv('HQ_SESSION', 'creator')
    assert hq.main(['begin', _SLUG]) == 0
    headers = [_cycle(root, '02', 'creator', monkeypatch)]
    monkeypatch.setenv('HQ_CYCLE', '2')
    headers.append(_cycle(root, '03', 'filer', monkeypatch))
    monkeypatch.setenv('HQ_CYCLE', '3')
    assert hq.main(['open', _SLUG, '--not-carried']) == 0
    headers.append(_cycle(root, '04', 'filer', monkeypatch))
    assert hq.main(['open', _SLUG]) == 0
    monkeypatch.setenv('HQ_CYCLE', '4')
    headers.append(_cycle(root, '05', 'filer', monkeypatch))
    capsys.readouterr()
    assert headers == [
        'Written: 2026-10-02 | Cycle: 1 | Last worked: 2026-10-02',
        'Written: 2026-10-03 | Cycle: 2 | Last worked: 2026-10-02',
        'Written: 2026-10-04 | Cycle: 3 | Last worked: 2026-10-02',
        'Written: 2026-10-05 | Cycle: 4 | Last worked: 2026-10-05',
        ]


def test_finish_falls_back_to_the_manifest_without_a_header(
        tmp_path, monkeypatch, capsys):
    """A filing finish over a deleted header line keeps the last cycle's
    date.

    Mutation: the manifest fallback dropped, so the date becomes '-'.
    Oracle: a creator cycle on 10-01, then a filer on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    handoff = root / '.handoff' / _SLUG / 'HANDOFF.md'
    handoff.write_text(''.join(
        ln for ln in handoff.read_text().splitlines(keepends=True)
        if not ln.startswith('Written:')))
    monkeypatch.setenv('HQ_CYCLE', '2')
    header = _cycle(root, '05', 'filer', monkeypatch)
    capsys.readouterr()
    assert header == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-01'


def test_begin_rebuild_seeds_last_worked_from_the_manifest(
        tmp_path, monkeypatch, capsys):
    """Begin rebuilding a lost HANDOFF.md seeds the last cycle's date.

    Mutation: the seed dated today.
    Oracle: a cycle on 10-01, rebuilt on 10-09.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    folder = root / '.handoff' / _SLUG
    (folder / 'HANDOFF.md').unlink()
    (folder / 'ledger.tsv').unlink()
    monkeypatch.setenv('HQ_NOW', '2026-10-09T12:00:00')
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()
    header = hq.split_handoff((folder / 'HANDOFF.md').read_text())['header']
    assert header == 'Written: 2026-10-09 | Cycle: 1 | Last worked: 2026-10-01'


def test_the_unknown_session_never_counts_as_worked(
        tmp_path, monkeypatch, capsys):
    """A session without an id records nothing, so its finish keeps the
    date even on a handoff it created.

    Mutation: 'unknown' dropped from both guards, so it is recorded like
    any session id.
    Oracle: a creator begins on 10-01 and finishes on 10-02.
    """
    root = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_NOW', '2026-10-01T12:00:00')
    monkeypatch.setenv('HQ_SESSION', 'unknown')
    assert hq.main(['begin', _SLUG]) == 0
    header = _cycle(root, '02', 'unknown', monkeypatch)
    capsys.readouterr()
    assert header == 'Written: 2026-10-02 | Cycle: 1 | Last worked: 2026-10-01'


def test_finish_survives_a_corrupt_worked_record(
        tmp_path, monkeypatch, capsys):
    """A worked record holding invalid UTF-8 lets finish run and keep
    the date.

    Mutation: the record read without errors='replace', so finish raises.
    Oracle: a creator whose record is overwritten with a stray byte.
    """
    root = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_NOW', '2026-10-01T12:00:00')
    monkeypatch.setenv('HQ_SESSION', 'creator')
    assert hq.main(['begin', _SLUG]) == 0
    (pathlib.Path(tmp_path) / 'creator.worked').write_bytes(b'\xff\n')
    header = _cycle(root, '02', 'creator', monkeypatch)
    capsys.readouterr()
    assert header == 'Written: 2026-10-02 | Cycle: 1 | Last worked: 2026-10-01'


def test_an_empty_session_id_never_counts_as_worked(
        tmp_path, monkeypatch, capsys):
    """An empty CLAUDE_CODE_SESSION_ID records nothing, so its finish
    keeps the date on a handoff it created.

    Mutation: '' dropped from both guards, so the empty id is recorded.
    Oracle: a creator begins on 10-01 and finishes on 10-02.
    """
    root = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', '')
    monkeypatch.setenv('HQ_NOW', '2026-10-01T12:00:00')
    monkeypatch.setenv('HQ_SESSION', '')
    assert hq.main(['begin', _SLUG]) == 0
    header = _cycle(root, '02', '', monkeypatch)
    capsys.readouterr()
    assert header == 'Written: 2026-10-02 | Cycle: 1 | Last worked: 2026-10-01'


def test_a_housekeeping_open_keeps_the_worked_thread(
        tmp_path, monkeypatch, capsys):
    """A check opened with --housekeeping neither moves the checked
    handoff nor unseats the one the session works.

    Mutation: --housekeeping ignored, so the open replaces the worked
    thread.
    Oracle: X and Y created on 10-01 by two other sessions; the worker
    opens X, checks Y on 10-05, then cycles X on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'other-creator')
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'made']) == 0
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0
    assert hq.main(['open', 'other', '--housekeeping']) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    monkeypatch.setenv('HQ_NOW', '2026-10-05T12:00:00')
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'check: 0 findings applied']) == 0
    header = _cycle(root, '05', 'worker', monkeypatch)
    capsys.readouterr()
    other = hq.split_handoff(
        (root / '.handoff' / 'other' / 'HANDOFF.md').read_text())['header']
    assert other == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-01'
    assert header == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-05'


def test_spin_off_handoffs_are_never_worked(tmp_path, monkeypatch, capsys):
    """A session that opens X and writes two new handoffs leaves them
    never worked, and still moves X's date on its return.

    Mutation: a creation replacing the worked thread, or a spin-off
    seeded with today.
    Oracle: X created on 10-01 by another session, worked on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0
    monkeypatch.setenv('HQ_NOW', '2026-10-03T12:00:00')
    for spin_off in ('spin-a', 'spin-b'):
        assert hq.main(['begin', spin_off]) == 0
        assert hq.main(['finish', spin_off, '--log', 'spun off']) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    header = _cycle(root, '05', 'worker', monkeypatch)
    capsys.readouterr()
    spin_headers = [
        hq.split_handoff(
            (root / '.handoff' / slug / 'HANDOFF.md').read_text())['header']
        for slug in ('spin-a', 'spin-b')
        ]
    assert spin_headers == ['Written: 2026-10-03 | Cycle: 1 | Last worked: -'] * 2
    assert header == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-05'


def test_opening_another_handoff_unseats_the_first(
        tmp_path, monkeypatch, capsys):
    """A session that creates Y, then opens X, files into Y as a filer.

    Mutation: a created handoff staying worked after the session opens
    another.
    Oracle: X created on 10-01 by another session; the worker creates Y
    on 10-02, opens X, and files into Y on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'worker')
    monkeypatch.setenv('HQ_NOW', '2026-10-02T12:00:00')
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'made']) == 0
    assert hq.main(['open', _SLUG]) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    monkeypatch.setenv('HQ_NOW', '2026-10-05T12:00:00')
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'filed']) == 0
    capsys.readouterr()
    header = hq.split_handoff(
        (root / '.handoff' / 'other' / 'HANDOFF.md').read_text())['header']
    assert header == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-02'


def test_done_frees_the_session_for_new_work(tmp_path, monkeypatch, capsys):
    """After done on its worked thread, a session's next new handoff is
    worked, never a spin-off.

    Mutation: a done thread still counted as the session's live worked
    thread.
    Oracle: the worker opens X, ends it with done, and creates Y on
    10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0
    assert hq.main(['done', _SLUG]) == 0
    monkeypatch.setenv('HQ_NOW', '2026-10-05T12:00:00')
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'new work']) == 0
    capsys.readouterr()
    header = hq.split_handoff(
        (root / '.handoff' / 'other' / 'HANDOFF.md').read_text())['header']
    assert header == 'Written: 2026-10-05 | Cycle: 1 | Last worked: 2026-10-05'


def test_begin_worked_claims_a_thread_the_session_never_opened(
        tmp_path, monkeypatch, capsys):
    """Begin --worked makes the handoff the session's worked thread, even
    over another one it holds.

    Mutation: --worked ignored, or recorded only for a session holding
    no thread.
    Oracle: X and Y created on 10-01 by other sessions; the worker opens
    Y, then begins X with --worked and finishes on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'other-creator')
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'made']) == 0
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', 'other']) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    monkeypatch.setenv('HQ_NOW', '2026-10-05T12:00:00')
    assert hq.main(['begin', _SLUG, '--worked']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'matched work']) == 0
    capsys.readouterr()
    header = hq.split_handoff(
        (root / '.handoff' / _SLUG / 'HANDOFF.md').read_text())['header']
    assert header == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-05'


def test_a_filing_finish_with_no_date_anywhere_writes_never_worked(
        tmp_path, monkeypatch, capsys):
    """A filer finishing a first cycle whose header has no parseable date
    writes '-'.

    Mutation: the fallback chain ending at today.
    Oracle: a hand-garbled cycle-1 header and an empty manifest.
    """
    root = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'creator')
    assert hq.main(['begin', _SLUG]) == 0
    handoff = root / '.handoff' / _SLUG / 'HANDOFF.md'
    handoff.write_text(''.join(
        'Written: someday | Cycle: 1\n' if ln.startswith('Written:') else ln
        for ln in handoff.read_text().splitlines(keepends=True)))
    monkeypatch.setenv('HQ_SESSION', 'filer')
    assert hq.main(['begin', _SLUG, '--force']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'filed']) == 0
    capsys.readouterr()
    header = hq.split_handoff(handoff.read_text())['header']
    assert header.endswith('| Last worked: -')


def test_same_slug_in_another_project_is_not_the_worked_thread(
        tmp_path, monkeypatch, capsys):
    """A session working project A's handoff files into project B's
    handoff of the same name as a filer.

    Mutation: the record keyed on the folder name instead of the resolved
    path.
    Oracle: both created on 10-01 by other sessions; the worker opens A's,
    then cycles B's on 10-05.
    """
    root_a = _new_root(tmp_path, monkeypatch)
    _cycle(root_a, '01', 'creator-a', monkeypatch)
    root_b = pathlib.Path(tmp_path) / 'root-b'
    root_b.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root_b))
    _cycle(root_b, '01', 'creator-b', monkeypatch)
    monkeypatch.setenv('HQ_ROOT', str(root_a))
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0
    monkeypatch.setenv('HQ_ROOT', str(root_b))
    monkeypatch.setenv('HQ_CYCLE', '2')
    header = _cycle(root_b, '05', 'worker', monkeypatch)
    capsys.readouterr()
    assert header == 'Written: 2026-10-05 | Cycle: 2 | Last worked: 2026-10-01'


def test_open_succeeds_when_the_state_dir_is_unwritable(
        tmp_path, monkeypatch, capsys):
    """Open returns 0 when the worked record cannot be written.

    Mutation: the record write left unguarded, so open fails after it
    succeeded.
    Oracle: HQ_STATE_DIR pointed under a regular file.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    blocker = pathlib.Path(tmp_path) / 'blocker'
    blocker.write_text('')
    monkeypatch.setenv('HQ_STATE_DIR', str(blocker / 'state'))
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0


def test_begin_after_a_lost_ledger_keeps_last_worked(
        tmp_path, monkeypatch, capsys):
    """Begin adopting a HANDOFF.md whose ledger.tsv is lost carries its
    Last worked value.

    Mutation: adopt writing its header without Last worked, so list and
    finish fall back to the adopt day.
    Oracle: a spin-off ('-') and a thread worked on 10-01, both adopted
    on 10-07.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    assert hq.main(['begin', 'other']) == 0
    assert hq.main(['finish', 'other', '--log', 'spun off']) == 0
    monkeypatch.setenv('HQ_SESSION', 'adopter')
    monkeypatch.setenv('HQ_NOW', '2026-10-07T12:00:00')
    headers = []
    for slug in (_SLUG, 'other'):
        folder = root / '.handoff' / slug
        (folder / 'ledger.tsv').unlink()
        assert hq.main(['begin', slug]) == 0
        headers.append(
            hq.split_handoff((folder / 'HANDOFF.md').read_text())['header'])
    capsys.readouterr()
    assert [h.split(' | Last worked: ')[1] for h in headers] == ['2026-10-01', '-']


def test_begin_worked_overrides_the_spin_off_seed(
        tmp_path, monkeypatch, capsys):
    """A new handoff begun with --worked while the session works another
    is seeded today, never as a spin-off.

    Mutation: --worked left out of the spin-off test.
    Oracle: X created on 10-01; the worker opens X and begins Y with
    --worked on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0
    monkeypatch.setenv('HQ_NOW', '2026-10-05T12:00:00')
    assert hq.main(['begin', 'other', '--worked']) == 0
    capsys.readouterr()
    header = hq.split_handoff(
        (root / '.handoff' / 'other' / 'HANDOFF.md').read_text())['header']
    assert header == 'Written: 2026-10-05 | Cycle: 1 | Last worked: 2026-10-05'


def test_a_deleted_worked_thread_is_not_live(tmp_path, monkeypatch, capsys):
    """A session whose worked thread was deleted creates new work, never
    a spin-off.

    Mutation: the HANDOFF.md check dropped from the spin-off test.
    Oracle: the worker opens X, X's folder is removed, the worker creates
    Y on 10-05.
    """
    root = _new_root(tmp_path, monkeypatch)
    _cycle(root, '01', 'creator', monkeypatch)
    monkeypatch.setenv('HQ_SESSION', 'worker')
    assert hq.main(['open', _SLUG]) == 0
    shutil.rmtree(root / '.handoff' / _SLUG)
    monkeypatch.setenv('HQ_NOW', '2026-10-05T12:00:00')
    assert hq.main(['begin', 'other']) == 0
    capsys.readouterr()
    header = hq.split_handoff(
        (root / '.handoff' / 'other' / 'HANDOFF.md').read_text())['header']
    assert header == 'Written: 2026-10-05 | Cycle: 1 | Last worked: 2026-10-05'
