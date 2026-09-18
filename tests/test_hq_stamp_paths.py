"""Stamp-path identity, the R1 gate's history, and the receipt it writes.

Each test pins one defect of the stamp path: a file that gets a second
ledger key from a second spelling, a gate that forgets a kind the agent
once declared, a refusal receipt that promotes an ungated file, an enum
value argparse never checked, a line count that is not wc -l, and a
ledger row glued onto a truncated last line.
"""

import io
import os
import pathlib

from bin import hq

_SLUG = 'stamp-slug'
_SESSION = 'session-paths'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'


def _root(tmp_path, monkeypatch, slug=_SLUG):
    """Create an HQ_ROOT and set the HQ_* environment for one slug.

    Returns the folder path root/.handoff/<slug>/, not yet created.
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
    return root / '.handoff' / slug


def _rows(folder):
    """Return the ledger's data rows as dicts, header dropped."""
    lines = (folder / 'ledger.tsv').read_text().splitlines()[1:]
    return [dict(zip(hq.LEDGER_FIELDS, ln.split('\t'))) for ln in lines
            if ln.strip()]


def test_every_spelling_of_one_in_folder_file_shares_one_ledger_key(
        tmp_path, monkeypatch):
    """Four spellings of SPEC.md write four rows under the one path key.

    Mutation: the stored path taken from the token as typed, so './SPEC.md',
    'sub/../SPEC.md', and the absolute path each open their own history.
    Oracle: the four rows, each read back as ('SPEC.md', 'folder').
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    (folder / 'sub').mkdir()

    for token in ('SPEC.md', './SPEC.md', 'sub/../SPEC.md',
                  str(folder / 'SPEC.md')):
        assert hq.main(['stamp', _SLUG, token, '--where', 'Spec']) == 0, token

    rows = _rows(folder)
    assert [(r['path'], r['base']) for r in rows] == [('SPEC.md', 'folder')] * 4
    assert list(hq.latest_rows(rows)) == ['SPEC.md']


def test_a_backticked_and_dot_slash_successor_keys_on_the_stored_path(
        tmp_path, monkeypatch):
    """A successor spelled './SPEC2.md' is refused exactly as 'SPEC2.md' is.

    Mutation: the liveness lookup keyed on the raw --successor token, so a
    non-canonical spelling misses the archived row and buries a live spec.
    Oracle: both spellings exit 1 and leave SPEC.md live at always.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    (folder / 'SPEC2.md').write_text('# Spec\n\nTwo.\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC2.md', '--archive', '--reason', 'parked']) == 0

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'SPEC2.md']) == 1
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', './SPEC2.md']) == 1
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--successor', '`SPEC2.md`']) == 1

    current = hq.latest_rows(_rows(folder))['SPEC.md']
    assert (current['status'], current['read_before']) == ('live', 'always')


def test_a_relative_token_is_never_searched_in_the_home_directory(
        tmp_path, monkeypatch, capsys):
    """A bare relative token stays folder-relative when only $HOME has it.

    Mutation: the home fallback of the candidate search, which turns
    'SPEC2.md' into a gated ~/SPEC2.md row outside the folder.
    Oracle: exit 2 naming the folder the token was read against, and a
    ledger of header only - no ('~/SPEC2.md', 'abs') row.
    """
    folder = _root(tmp_path, monkeypatch)
    home = pathlib.Path(tmp_path) / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    hq.main(['begin', _SLUG])
    (home / 'SPEC2.md').write_text('# Spec\n\nOutside.\n')
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    assert hq.main(['stamp', _SLUG, 'SPEC2.md']) == 2

    assert f'no such file under {folder}' in capsys.readouterr().out
    assert _rows(folder) == []


def test_a_path_carrying_a_tab_or_a_newline_is_a_usage_error(
        tmp_path, monkeypatch, capsys):
    """A tab or newline in the path token exits 2 and writes no row.

    Mutation: the check dropped, so the sanitizing append writes a row
    whose stored path is not the path the agent named.
    Oracle: exit 2, the documented line, and a ledger of header only.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    capsys.readouterr()

    assert hq.main(['stamp', _SLUG, 'bad\tname.md']) == 2
    assert hq.main(['stamp', _SLUG, 'bad\nname.md']) == 2

    out = capsys.readouterr().out
    refusal = 'hq stamp: path may not contain a tab or a line break'
    assert out.count(refusal) == 2
    assert _rows(folder) == []


def test_a_kind_once_declared_spec_keeps_gating_after_an_archive(
        tmp_path, monkeypatch):
    """A path whose history holds a spec row may not return live at never.

    Mutation: the gate read from the previous row alone, so --kind other on
    the archiving stamp erases the spec from the gate and the next stamp
    lands the file live at never with no receipt.
    Oracle: the third stamp exits 1 and the current row stays archived.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'plan.txt').write_text('# Plan\n\nBody.\n')
    assert hq.main(['stamp', _SLUG, 'plan.txt', '--kind', 'spec', '--where', 'Plan']) == 0
    assert hq.main([
        'stamp', _SLUG, 'plan.txt', '--archive', '--reason', 'parked',
        '--kind', 'other']) == 0

    assert hq.main([
        'stamp', _SLUG, 'plan.txt', '--status', 'live',
        '--read-before', 'never']) == 1

    current = hq.latest_rows(_rows(folder))['plan.txt']
    assert (current['status'], current['read_before']) == ('archived', 'never')
    assert current['reason'].startswith('refused: ')


def test_a_first_stamp_receipt_seeds_read_before_from_the_kind_table(
        tmp_path, monkeypatch):
    """A refused first stamp of a notes file records never, not always.

    Mutation: the receipt defaulting read_before to always with no previous
    row, which promotes an ungated notes file into the read block and the
    R3 gate.
    Oracle: the receipt row reads kind notes, read_before never, with the
    file's own sha12 and line count.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC2.md').write_text('# Spec\n\nTwo.\n')
    (folder / 'notes-a.md').write_text('# A\n\nBody.\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC2.md', '--archive', '--reason', 'parked']) == 0

    assert hq.main([
        'stamp', _SLUG, 'notes-a.md', '--successor', 'SPEC2.md']) == 1

    receipt = hq.latest_rows(_rows(folder))['notes-a.md']
    assert (receipt['kind'], receipt['read_before']) == ('notes', 'never')
    assert receipt['sha12'] == hq._sha12((folder / 'notes-a.md').read_bytes())
    assert (receipt['lines'], receipt['label']) == ('3', '-')


def test_a_refused_first_stamp_still_puts_the_file_under_r3(
        tmp_path, monkeypatch, capsys):
    """The receipt for a first stamp records the sha on disk, so R3 sees it.

    Mutation: the receipt writing '-' for sha12 and lines when no previous
    row exists, which check_r3 skips for ever.
    Oracle: after the refusal the file is edited and finish exits 1 naming
    the path.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    (folder / 'SPEC2.md').write_text('# Spec\n\nTwo.\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC2.md', '--archive', '--reason', 'parked']) == 0
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'SPEC2.md']) == 1
    (folder / 'SPEC.md').write_text('# Spec\n\nEdited.\n')
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 1

    assert 'R3: SPEC.md sha moved' in capsys.readouterr().out


def test_an_unknown_enum_value_is_a_usage_error_that_writes_nothing(
        tmp_path, monkeypatch):
    """--status Live, --read-before mentions, and --kind spce each exit 2.

    Mutation: the argparse choices dropped, so a misspelled value is stored
    and the row drops out of every view.
    Oracle: exit 2 on each of the three, and a ledger of header only.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')

    for flag, value in (('--status', 'Live'), ('--read-before', 'mentions'),
                        ('--kind', 'spce')):
        assert hq.main(['stamp', _SLUG, 'SPEC.md', flag, value]) == 2, flag

    assert _rows(folder) == []


def test_a_batch_line_with_a_bad_value_is_numbered_and_silent_on_stderr(
        tmp_path, monkeypatch, capsys):
    """A rejected batch line prints one numbered stdout line and no usage.

    Mutation: the choices dropped so the line lands as a row, or the
    parser's stderr left unsuppressed so a four-line usage block precedes
    the numbered line.
    Oracle: exit 2, empty stderr, the numbered line, and only the good
    row written.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    (folder / 'notes-a.md').write_text('# A\n')
    monkeypatch.setattr(
        'sys.stdin', io.StringIO('SPEC.md --status Live\nnotes-a.md\n'))
    capsys.readouterr()

    assert hq.main(['stamp', _SLUG, '--batch', '-']) == 2

    captured = capsys.readouterr()
    assert 'hq stamp: batch line 1 not parsed: SPEC.md --status Live' in (
        captured.out)
    assert captured.err == ''
    assert [r['path'] for r in _rows(folder)] == ['notes-a.md']


def test_a_stamp_with_no_path_is_a_usage_error_in_both_forms(
        tmp_path, monkeypatch, capsys):
    """A missing path exits 2 alone, and is numbered inside a batch.

    Mutation: the missing-path branch returning 1, or the batch loop
    reporting it unnumbered, so a dropped path reads as a refusal the
    agent must answer instead of a line it must fix.
    Oracle: exit 2 from the bare form; the numbered line from the batch,
    whose second line still lands.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# A\n')

    assert hq.main(['stamp', _SLUG]) == 2

    monkeypatch.setattr(
        'sys.stdin', io.StringIO('--label "orphan"\nnotes-a.md\n'))
    capsys.readouterr()

    assert hq.main(['stamp', _SLUG, '--batch', '-']) == 2

    out = capsys.readouterr().out
    assert 'hq stamp: batch line 1 not parsed: --label "orphan"' in out
    assert [r['path'] for r in _rows(folder)] == ['notes-a.md']


def test_lines_counts_newline_bytes_and_a_directory_counts_its_files(
        tmp_path, monkeypatch):
    r"""A file with no final newline counts one fewer; a directory counts files.

    Mutation: lines taken from the iterated line count, so an unterminated
    last line is counted, or a directory counting dotfiles and subdirectories
    alongside its files.
    Oracle: hand-counted 2 newlines in 'a\\nb\\nc', and one regular file
    among three directory entries.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('a\nb\nc')
    probes = folder / 'probes'
    probes.mkdir()
    (probes / 'run1.md').write_text('# run 1\n')
    (probes / '.hidden').write_text('x\n')
    (probes / 'nested').mkdir()

    assert hq.main(['stamp', _SLUG, 'notes-a.md']) == 0
    assert hq.main(['stamp', _SLUG, 'probes']) == 0

    live = hq.latest_rows(_rows(folder))
    assert live['notes-a.md']['lines'] == '2'
    assert live['probes']['lines'] == '1'


def test_both_archive_spellings_write_the_same_status_and_read_before(
        tmp_path, monkeypatch):
    """--status archived matches --archive: archived at never, both times.

    Mutation: --status archived leaving read_before at always, so the two
    spellings write different rows; or --archive seeding read_before at
    mention instead of never.
    Oracle: the pair of rows, each read back as ('archived', 'never').
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    (folder / 'DESIGN.md').write_text('# Design\n\nTwo.\n')

    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--archive', '--reason', 'obsolete']) == 0
    assert hq.main([
        'stamp', _SLUG, 'DESIGN.md', '--status', 'archived',
        '--reason', 'obsolete']) == 0

    live = hq.latest_rows(_rows(folder))
    assert (live['SPEC.md']['status'], live['SPEC.md']['read_before']) == (
        'archived', 'never')
    assert (live['DESIGN.md']['status'], live['DESIGN.md']['read_before']) == (
        'archived', 'never')


def test_an_uppercase_todo_name_infers_the_todo_kind(tmp_path, monkeypatch):
    """TODO.md infers todo at never, as todo-list.md does.

    Mutation: the 'TODO*' pattern dropped from the name table, so an
    uppercase todo falls through to other.
    Oracle: infer_kind on both spellings, and the stamped row's kind.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'TODO.md').write_text('- one\n')

    assert hq.infer_kind('TODO.md', False, '') == ('todo', 'never')
    assert hq.infer_kind('todo-list.md', False, '') == ('todo', 'never')
    assert hq.main(['stamp', _SLUG, 'TODO.md']) == 0

    assert hq.latest_rows(_rows(folder))['TODO.md']['kind'] == 'todo'


def test_a_ledger_whose_last_newline_was_lost_does_not_glue_the_next_row(
        tmp_path, monkeypatch):
    """An append to a ledger with no final newline starts its own line.

    Mutation: the append writing the row with no leading newline, which
    glues 26 fields onto the last recorded line and loses the stamp.
    Oracle: three physical lines, each data line exactly thirteen fields.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--label', 'first']) == 0
    ledger = folder / 'ledger.tsv'
    ledger.write_text(ledger.read_text().rstrip('\n'))

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'second']) == 0

    lines = ledger.read_text().splitlines()
    assert len(lines) == 3
    assert [len(ln.split('\t')) for ln in lines[1:]] == [13, 13]
    assert [ln.split('\t')[-1] for ln in lines[1:]] == ['first', 'second']


def test_an_absolute_token_inside_the_folder_is_stored_relative(
        tmp_path, monkeypatch):
    """An absolute in-folder path stores as its folder-relative name.

    Mutation: any '/'-prefixed token stored as abs, so the same file
    renders twice and R3 checks two keys.
    Oracle: os.path.join of the folder and the name, stamped after the
    relative spelling, lands on the one 'folder' key.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'DESIGN.md').write_text('# Design\n\nOne.\n')

    assert hq.main(['stamp', _SLUG, 'sub/DESIGN.md', '--where', 'Design', '--label', 'sub']) == 0
    assert hq.main([
        'stamp', _SLUG, os.path.join(str(folder), 'sub', 'DESIGN.md')]) == 0

    rows = _rows(folder)
    assert [(r['path'], r['base']) for r in rows] == [
        ('sub/DESIGN.md', 'folder')] * 2


def test_read_and_when_find_a_row_by_any_spelling_of_its_path(
        tmp_path, monkeypatch, capsys):
    """Read and when key on the stored path, and the receipt carries it.

    Mutation: the lookup, the disk path, or the receipt taking the
    argument as typed, so an unquoted ``~`` the shell expanded prints
    ``not in ledger``, ``sub/../SPEC.md`` with no ``sub/`` prints ``not
    on disk``, and a receipt under the expanded path never credits the
    read to the gate.
    Oracle: the row stamped as ``~/repo/auth.py``; the receipt file
    hand-written as ``<now> <slug> ~/repo/auth.py`` then ``... SPEC.md``.
    """
    folder = _root(tmp_path, monkeypatch)
    home = pathlib.Path(tmp_path) / 'home'
    (home / 'repo').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    (home / 'repo' / 'auth.py').write_text('x = 1\n')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    assert hq.main(['stamp', _SLUG, '~/repo/auth.py']) == 0
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec']) == 0
    expanded = str(home / 'repo' / 'auth.py')
    capsys.readouterr()

    assert hq.main(['read', _SLUG, expanded]) == 0
    assert capsys.readouterr().out == 'x = 1\n'
    assert hq.main(['read', _SLUG, 'sub/../SPEC.md']) == 0
    assert capsys.readouterr().out == '# Spec\n\nContent.\n'
    assert hq.main(['when', _SLUG, expanded]) == 0
    when_lines = capsys.readouterr().out.splitlines()
    assert [ln.split('\t')[0] for ln in when_lines] == ['~/repo/auth.py']

    receipts = (pathlib.Path(tmp_path) / f'hq-reads-{_SESSION}.txt')
    assert receipts.read_text().splitlines() == [
        f'{_NOW} {_SLUG} ~/repo/auth.py',
        f'{_NOW} {_SLUG} SPEC.md',
        ]


def test_a_first_stamp_of_a_path_the_folder_lacks_is_a_usage_error(
        tmp_path, monkeypatch, capsys):
    """A token no base holds exits 2 and writes no row.

    Mutation: the existence check dropped at every rung, so a stamp of a
    path nothing holds exits 0 and leaves a live row with sha '-' until
    finish calls it missing.
    Oracle: exit 2 with the line naming the three bases tried and a
    ledger of header only; the same file by its absolute path exits 0 as
    base abs; a token given --status missing, --successor, --archive, or
    --status archived still writes its row, as does a re-stamp of a row
    whose file is gone; an absolute token inside the folder is told to
    create the file or record it gone.
    """
    folder = _root(tmp_path, monkeypatch)
    repo_file = pathlib.Path(tmp_path) / 'root' / 'docs' / 'SPEC-b.md'
    repo_file.parent.mkdir(parents=True)
    repo_file.write_text('# Spec\n\nOutside.\n')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC2.md').write_text('# Spec\n\nTwo.\n')
    capsys.readouterr()

    assert hq.main(['stamp', _SLUG, 'docs/SPEC-absent.md']) == 2

    out = capsys.readouterr().out
    assert out.startswith(
        f'hq stamp: docs/SPEC-absent.md: no such file under {folder}')
    assert 'not under the folder, the root, or the work dir' in out
    assert _rows(folder) == []
    assert hq.main(['stamp', _SLUG, str(repo_file), '--where', 'Spec']) == 0
    assert hq.main([
        'stamp', _SLUG, 'gone.md', '--status', 'missing', '--label', 'gone']) == 0
    assert hq.main(['stamp', _SLUG, 'old-SPEC.md', '--successor', 'SPEC2.md']) == 0
    assert hq.main([
        'stamp', _SLUG, 'gone2.md', '--archive', '--reason', 'never written']) == 0
    assert hq.main([
        'stamp', _SLUG, 'gone3.md', '--status', 'archived', '--reason', 'parked']) == 0
    (folder / 'notes-x.md').write_text('# X\n')
    assert hq.main(['stamp', _SLUG, 'notes-x.md']) == 0
    (folder / 'notes-x.md').unlink()
    assert hq.main(['stamp', _SLUG, 'notes-x.md', '--label', 'gone now']) == 0
    capsys.readouterr()
    assert hq.main(['stamp', _SLUG, str(folder / 'inside-gone.md')]) == 2
    assert ('create it first, or record it gone with --status missing'
            in capsys.readouterr().out)
    assert [(r['path'], r['base'], r['status']) for r in _rows(folder)] == [
        (str(repo_file), 'abs', 'live'),
        ('gone.md', 'folder', 'missing'),
        ('old-SPEC.md', 'folder', 'superseded'),
        ('gone2.md', 'folder', 'archived'),
        ('gone3.md', 'folder', 'archived'),
        ('notes-x.md', 'folder', 'live'),
        ('notes-x.md', 'folder', 'live'),
        ]
