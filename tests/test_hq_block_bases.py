"""Every path a rendered block prints resolves from the base it states.

Each test joins a block's base line to one of its rows and opens the
result on disk, so a display the reader cannot follow fails here rather
than costing a resuming session a search.
"""

import io
import pathlib
import re

from bin import hq

_SLUG = 'block-base-slug'
_SESSION = 'session-blocks'
_HOST = 'test-host'
_NOW = '2026-09-14T12:00:00'


def _root(tmp_path, monkeypatch):
    """Create an HQ_ROOT with the HQ_* environment set; return the root."""
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


def _cursor(folder):
    """Give the thread's HANDOFF.md a Task and a Now so finish accepts it."""
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(handoff.read_text().replace(
        '## Task\n', '## Task\nT.\n').replace('## Now\n', '## Now\nN.\n'))


def _seeded(tmp_path, monkeypatch):
    """Run one cycle over a spec in specs/ and return (root, folder)."""
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    (folder / 'specs').mkdir(parents=True)
    (folder / 'specs' / 'SPEC.md').write_text(
        '# Spec\n\n## Key scheme\n\nOne key per tenant.\n')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'stamp', _SLUG, 'specs/SPEC.md', '--where', 'Key scheme',
        '--label', 'the cache contract']) == 0
    _cursor(folder)
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    return root, folder


def _block(folder, name):
    """Return the named block's lines below its heading, as rendered."""
    text = (folder / 'HANDOFF.md').read_text()
    return hq.split_handoff(text)['blocks'][name].splitlines()[1:]


def _base_dir(line):
    """Return the directory a block's base line names."""
    return pathlib.Path(line.split(' ', 1)[1]).expanduser()


def test_an_artifacts_row_in_the_folder_opens_from_the_stated_base(
        tmp_path, monkeypatch):
    """The Artifacts row for a file inside the handoff folder carries the
    folder, so the base line and the row join to the file on disk.

    Mutation: shown_paths passing over a folder-base row, which prints
    the bare stored path under a base line naming the root, where no
    such file sits; or the always row's label restored after the cycle
    field, where the Read first block already prints it whole.
    Oracle: the base line and the row's first field joined with
    pathlib, opened on disk.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    lines = _block(folder, 'artifacts')

    base, row = lines[0], lines[1]

    assert base == f'root {root}'
    assert row == f'.handoff/{_SLUG}/specs/SPEC.md  spec  always  c1'
    assert (_base_dir(base) / row.split('  ')[0]).is_file()


def test_the_read_first_block_names_the_base_its_rows_resolve_from(
        tmp_path, monkeypatch):
    """Read first carries a base line of its own, and its anchored path
    opens from it.

    Mutation: the base line rendered for Artifacts alone, leaving Read
    first - the block a resuming session opens first - with no base and
    a path that resolves from nowhere it names.
    Oracle: the base line and the path before the span colon joined with
    pathlib, opened on disk.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    lines = _block(folder, 'read')

    base, row = lines[0], lines[1]

    assert base == f'root {root}'
    assert row.startswith(f'.handoff/{_SLUG}/specs/SPEC.md:3-5  (')
    assert (_base_dir(base) / row.split(':')[0]).is_file()


def test_an_unstamped_entry_resolves_from_the_same_base_as_a_stamped_row(
        tmp_path, monkeypatch):
    """An unstamped walk entry prints from the block's base, as the rows
    beside it do.

    Mutation: the unstamped line built from the raw walk name while the
    stamped rows carry the folder, so one line in the block resolves
    from the folder and the rest from the root.
    Oracle: the base line and the unstamped line's first field joined
    with pathlib, opened on disk as a directory.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    (folder / 'probes').mkdir()
    (folder / 'probes' / 'try.py').write_text('x = 1\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    lines = _block(folder, 'artifacts')

    base = lines[0]
    unstamped = [ln for ln in lines if ln.endswith('  unstamped')]

    assert unstamped == [f'.handoff/{_SLUG}/probes  probe-dir?  unstamped']
    assert (_base_dir(base) / unstamped[0].split('  ')[0]).is_dir()


def test_a_work_dir_under_the_root_adds_no_second_base(tmp_path, monkeypatch):
    """A pin the root already holds prints its files from the root, and
    the block names one base.

    Mutation: the pin kept as a base wherever it is pinned, so a block
    whose paths all resolve from the root still states two bases and
    leaves the reader to guess which row belongs to which.
    Oracle: the one base line, and the pinned file printed as
    'working/w.py' - the path the root resolves.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    (root / 'working').mkdir()
    (root / 'working' / 'w.py').write_text('w = 1\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['work-dir', _SLUG, 'working']) == 0
    assert hq.main([
        'stamp', _SLUG, str(root / 'working' / 'w.py'), '--kind', 'draft',
        '--label', 'the loader']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    lines = _block(folder, 'artifacts')

    base = lines[0]

    assert base == f'root {root}'
    assert 'work dir' not in '\n'.join(lines)
    assert 'working/w.py  draft  edit  c2  the loader' in lines
    assert (_base_dir(base) / 'working/w.py').is_file()


def test_a_work_dir_outside_the_root_keeps_its_own_base(tmp_path, monkeypatch):
    """A pin the root does not hold stays a second base, named beside the
    root on the block's first line.

    Mutation: the second base dropped for every pin, so a worktree
    thread's made files print relative to a directory the block never
    names.
    Oracle: both bases on the first line, and the pinned file printed as
    'w.py', which opens under the pin and not under the root.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    # The pin has to sit outside the root, and every path a test can make
    # is under the system temp directory the check otherwise refuses.
    monkeypatch.setattr(hq, '_TEMP_DIRS', ())
    pin = pathlib.Path(tmp_path) / 'worktree'
    pin.mkdir()
    (pin / 'w.py').write_text('w = 1\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['work-dir', _SLUG, str(pin)]) == 0
    assert hq.main([
        'stamp', _SLUG, str(pin / 'w.py'), '--kind', 'draft',
        '--label', 'the loader']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    lines = _block(folder, 'artifacts')

    assert lines[0] == f'root {root}; work dir {pin}'
    assert 'w.py  draft  edit  c2  the loader' in lines
    assert (pin / 'w.py').is_file()
    assert not (root / 'w.py').exists()


def test_hq_read_takes_the_path_the_block_prints(tmp_path, monkeypatch, capsys):
    """The path a block prints is a path hq read accepts, and it opens the
    same span as the path the ledger stores.

    Mutation: a display prefix _ledger_key cannot resolve back to its
    row, so the block names a path the verb answers 'not in ledger' to
    and the reader is sent hunting after all.
    Oracle: the span printed for the displayed path against the span
    printed for the stored path, both hand-anchored at 'Key scheme'.
    """
    _, folder = _seeded(tmp_path, monkeypatch)
    display = _block(folder, 'read')[1].split(':')[0]
    capsys.readouterr()

    assert hq.main(['read', _SLUG, display]) == 0
    shown = capsys.readouterr().out
    assert hq.main(['read', _SLUG, 'specs/SPEC.md']) == 0
    stored = capsys.readouterr().out

    assert display == f'.handoff/{_SLUG}/specs/SPEC.md'
    assert shown == stored
    assert shown.splitlines()[0] == '## Key scheme'


def test_stamp_takes_the_path_the_block_prints(tmp_path, monkeypatch, capsys):
    """The path a block prints is a path hq stamp accepts, and it keys on
    the row the bare spelling keys on.

    Mutation: the root rung dropped, so the one spelling the block
    offers is refused on the write path; or the rung storing the token as
    given, so a repo file typed bare is recorded as a file the folder
    holds and the same file keeps two keys.
    Oracle: the ledger's base column after both stamps - 'specs/SPEC.md'
    the only folder-base key, the repo file keyed by the absolute path
    the root rung resolved.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text('x = 1\n')
    display = _block(folder, 'artifacts')[1].split('  ')[0]
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0

    assert hq.main(['stamp', _SLUG, display, '--label', 'relabeled twice']) == 0
    capsys.readouterr()
    accepted = hq.main(['stamp', _SLUG, 'src/app.py', '--label', 'a repo file'])

    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    repo_key = str(root / 'src' / 'app.py')
    assert display == f'.handoff/{_SLUG}/specs/SPEC.md'
    assert hq.latest_rows(rows)['specs/SPEC.md']['label'] == 'relabeled twice'
    assert accepted == 0
    assert {row['path'] for row in rows} == {'specs/SPEC.md', repo_key}
    assert {
        row['path'] for row in rows if row['base'] == 'folder'
        } == {'specs/SPEC.md'}
    assert hq.latest_rows(rows)[repo_key]['base'] == 'abs'


def test_open_reads_a_block_an_older_version_rendered(tmp_path, monkeypatch, capsys):
    """Label drift and span drift are found in a block that carries the
    stored path where this version prints the display.

    Mutation: only the display spelling searched, so every folder row
    goes unjudged on the first resume after the upgrade - the one resume
    that happens before any finish re-renders the block.
    Oracle: a block body rewritten to the pre-upgrade spelling with its
    sha recomputed, against a row re-stamped with a new label and a spec
    whose anchor has moved two lines down.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text().replace(f'.handoff/{_SLUG}/specs/', 'specs/')
    for name, heading in (('read', 'Read first'), ('artifacts', 'Artifacts')):
        body = hq.split_handoff(text)['blocks'][name]
        text = re.sub(
            f'<!-- hq:{name} [0-9a-f]{{12}} -->',
            f'<!-- hq:{name} {hq.block_sha(body)} -->', text, count=1)
    handoff.write_text(text)
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'stamp', _SLUG, 'specs/SPEC.md', '--label', 'a later label']) == 0
    (folder / 'specs' / 'SPEC.md').write_text(
        '# Spec\n\nPreamble.\n\n## Key scheme\n\nOne key per tenant.\n')
    (folder / '.hq.lock').unlink()
    capsys.readouterr()

    assert hq.main(['open', _SLUG]) == 0

    out = capsys.readouterr().out
    assert 'block sha mismatch' not in out
    assert 'LEDGER BEHIND: block label differs: specs/SPEC.md' in out
    assert 'span moved: specs/SPEC.md [(3, 5)] -> [(5, 7)]' in out


def test_a_batch_line_never_folder_bases_another_slugs_file(
        tmp_path, monkeypatch, capsys):
    """A batch line keys each printed prefix on its own base: this
    thread's folder for its own file, absolutely for another thread's.

    Mutation: the in-folder retry widened from 'inside this folder' to
    'under the root', so .handoff/<other-slug>/notes/x.md is recorded as
    a file this thread holds and two threads share one row; or the root
    rung storing the token as given, so the key is a bare relative path
    belonging to neither base.
    Oracle: the ledger's base column after the batch - 'specs/SPEC.md'
    the only folder-base key, the other thread's file keyed by its
    absolute path.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    other = root / '.handoff' / 'other-slug' / 'notes'
    other.mkdir(parents=True)
    (other / 'x.md').write_text('# Other\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    monkeypatch.setattr('sys.stdin', io.StringIO(
        f'.handoff/{_SLUG}/specs/SPEC.md --label "the cache contract again"\n'
        '.handoff/other-slug/notes/x.md --label "another thread"\n'))
    capsys.readouterr()
    assert hq.main(['stamp', _SLUG, '--batch']) == 0
    out = capsys.readouterr().out

    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    other_key = str(other / 'x.md')
    assert {
        row['path'] for row in rows if row['base'] == 'folder'
        } == {'specs/SPEC.md'}
    assert hq.latest_rows(rows)[other_key]['base'] == 'abs'
    assert (hq.latest_rows(rows)['specs/SPEC.md']['label']
            == 'the cache contract again')
    assert f'.handoff/other-slug/notes/x.md -> {other_key} (root)' in out


def test_stamp_takes_the_repo_path_the_block_prints(
        tmp_path, monkeypatch, capsys):
    """The root-relative spelling a block prints re-stamps the row the
    absolute path opened, never a second one.

    Mutation: the root rung storing the token as given, so the printed
    path opens a second folder-base row and the file's R1 history and R3
    drift split across two keys; or the rung dropped, so the one
    spelling the block offers exits 2 on the write path.
    Oracle: the ledger's own key set after the re-stamp - one key, the
    absolute path the first stamp wrote - and hq when's answer for the
    same display string.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    (root / 'src').mkdir()
    repo_file = root / 'src' / 'app.py'
    repo_file.write_text('x = 1\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'stamp', _SLUG, str(repo_file), '--read-before', 'never',
        '--label', 'the loader']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    display = next(
        ln.split('  ')[0] for ln in _block(folder, 'artifacts')
        if 'the loader' in ln)
    assert display == 'src/app.py'

    monkeypatch.setenv('HQ_CYCLE', '3')
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()
    assert hq.main([
        'stamp', _SLUG, display, '--label', 'the loader, relabeled']) == 0

    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    key = str(repo_file)
    assert {row['path'] for row in rows} == {'specs/SPEC.md', key}
    assert hq.latest_rows(rows)[key]['label'] == 'the loader, relabeled'
    capsys.readouterr()
    assert hq.main(['when', _SLUG, display]) == 0
    when_out = capsys.readouterr().out
    assert when_out.count(key) == 2
    assert hq.main(['finish', _SLUG, '--log', 'c3']) == 0
    assert display in [
        ln.split('  ')[0] for ln in _block(folder, 'artifacts')]


def test_a_repo_row_recorded_missing_by_the_printed_path_stays_one_row(
        tmp_path, monkeypatch, capsys):
    """Retiring a repo row by the printed path retires the row the
    absolute path opened.

    Mutation: the base resolution gated behind not recorded_on_purpose,
    so a repo row retired by the path the block prints opens a second
    folder-base row and the live row is never retired.
    Oracle: the ledger's key set after the missing stamp - one abs key -
    and the latest row's status read back from ledger.tsv.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    (root / 'src').mkdir()
    repo_file = root / 'src' / 'app.py'
    repo_file.write_text('x = 1\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'stamp', _SLUG, str(repo_file), '--read-before', 'never',
        '--label', 'the loader']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    display = next(
        ln.split('  ')[0] for ln in _block(folder, 'artifacts')
        if 'the loader' in ln)
    repo_file.unlink()

    monkeypatch.setenv('HQ_CYCLE', '3')
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()
    assert hq.main([
        'stamp', _SLUG, display, '--status', 'missing', '--label', 'gone']) == 0

    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    key = str(repo_file)
    assert {row['path'] for row in rows} == {'specs/SPEC.md', key}
    assert hq.latest_rows(rows)[key]['status'] == 'missing'


def test_a_stamp_of_a_pinned_work_dir_path_keys_the_row_when_answers(
        tmp_path, monkeypatch, capsys):
    """A pin-relative spelling stamps the row it names, and when agrees.

    Mutation: the pin dropped from the base list stamp searches, so a
    pinned thread's own made files are the one case the printed path
    still refuses.
    Oracle: the single ledger key after the re-stamp, against hq when's
    answer for the same string.
    """
    root, folder = _seeded(tmp_path, monkeypatch)
    # The pin has to sit outside the root, and every path a test can make
    # is under the system temp directory the check otherwise refuses.
    monkeypatch.setattr(hq, '_TEMP_DIRS', ())
    pin = pathlib.Path(tmp_path) / 'worktree'
    pin.mkdir()
    (pin / 'w.py').write_text('w = 1\n')
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['work-dir', _SLUG, str(pin)]) == 0
    assert hq.main([
        'stamp', _SLUG, str(pin / 'w.py'), '--kind', 'draft',
        '--label', 'the loader']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    display = next(
        ln.split('  ')[0] for ln in _block(folder, 'artifacts')
        if 'the loader' in ln)
    assert display == 'w.py'

    monkeypatch.setenv('HQ_CYCLE', '3')
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()
    assert hq.main([
        'stamp', _SLUG, display, '--label', 'the loader, relabeled']) == 0

    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    key = str(pin / 'w.py')
    assert {row['path'] for row in rows} == {'specs/SPEC.md', key}
    assert hq.latest_rows(rows)[key]['label'] == 'the loader, relabeled'
    capsys.readouterr()
    assert hq.main(['when', _SLUG, display]) == 0
    assert capsys.readouterr().out.count(key) == 2

