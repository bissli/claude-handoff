"""Tests for the hq.py list verb and the missing-live work-list line."""

import os
import pathlib
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

    Mutation: counting checkboxes outside Plan (gives 2/4), not counting
    the indented one (gives 1/2), or reading the wrong header token.
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
        '  - [x] sub-item\n\n'
        '## State\n\n'
        '- [ ] state item\n',
        encoding='utf-8')

    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    # Skip header and separator; CYCLE and PROGRESS columns widen to fit
    # their header labels (5 and 8 chars), so c3 pads to 5 and 2/3 to 8.
    data_line = out.splitlines()[2]
    assert data_line == f'{slug}  2026-09-01  c3     2/3       {task_line}'


def test_list_no_plan_checkboxes_prints_dash(tmp_path, monkeypatch, capsys):
    """List prints '-' for progress when Plan has only numbered items or no Plan.

    Mutation: f'{done}/{total}' unconditionally, producing '0/0'.
    Oracle: the exact field is '-' for a numbered-only Plan and a missing Plan.
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

    os.utime(noplan / 'HANDOFF.md', (2000000, 2000000))
    os.utime(numbered / 'HANDOFF.md', (1000000, 1000000))

    rc, out, _ = _run(['list'], capsys)
    assert rc == 0
    # Skip header and separator; strip empty tokens from padding.
    for line in out.splitlines()[2:]:
        parts = [p.strip() for p in line.split('  ') if p.strip()]
        assert parts[3] == '-', f'expected - for progress in: {line!r}'


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
        'SLUG      WRITTEN  CYCLE  PROGRESS  TASK',
        '----------------------------------------',
        'locked    -        -      -         unreadable: Permission denied',
        'open-one  -        -      -         -',
        ]


def test_list_parses_task_plan_and_block_edges(tmp_path, monkeypatch, capsys):
    """The Task is the first non-empty line, a padded Plan heading counts,
    and a checkbox inside an hq block does not.

    Mutation: the task == '-' guard dropped (last line wins); the heading
    .strip() dropped ('## Plan   ' counts nothing); the '<!-- hq:' section
    clear dropped (the block's checkbox counts).
    Oracle: the exact data line 'edges  2026-09-03  c4     1/2       First line.'
    (CYCLE and PROGRESS pad to their header widths, 5 and 8).
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
    assert out.splitlines()[2] == 'edges  2026-09-03  c4     1/2       First line.'
