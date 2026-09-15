"""Tests for the hq.py verb suite (phase-1 table, design lines 590-624)."""

import datetime as dt
import hashlib
import io
import os
import pathlib
import shutil
import subprocess
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'handoff')

from scripts import hq

_SLUG = 'test-slug'
_SESSION = 'session-abc'
_HOST = 'test-host'
_NOW = '2026-09-09T12:00:00'
# 1h59m before _NOW; refused (below two-hour threshold)
_YOUNG_LOCK_TIME = '2026-09-09T10:01:00'
# 2h01m before _NOW; taken over (above two-hour threshold)
_STALE_LOCK_TIME = '2026-09-09T09:59:00'

# Large cursor (~4 kB) used by the payload-flatness test.
# All section bodies are deterministic strings
# so the content never drifts.
_LARGE_CURSOR = (
    '## Task\n\n'
    + '\n'.join(
        f'Task item {i:02d}: complete this objective with strict attention.'
        for i in range(1, 16))
    + '\n\n## Now\n\n'
    + '\n'.join(
        f'Step {i:02d}: execute sub-task {i:02d} carefully before moving on.'
        for i in range(1, 12))
    + '\n\n## Plan\n\n'
    + '\n'.join(
        f'{i:02d}. Plan item {i:02d}: finish this phase before the next begins.'
        for i in range(1, 26))
    + '\n\n## State\n\n'
    + '\n'.join(
        f'State variable {i:02d}: currently nominal with no observed deviation.'
        for i in range(1, 16))
    + '\n\n## Environment\n\n'
    + '\n'.join(
        f'Environment check {i:02d}: configuration verified and stable.'
        for i in range(1, 8))
    + '\n\n## Open questions\n\n'
    + '\n'.join(
        f'Question {i:02d}: under investigation; resolution is pending.'
        for i in range(1, 6))
    + '\n'
)


def _write_lock(
    folder: 'os.PathLike[str]',
    session: str,
    time_str: str,
    cycle: int = 1,
) -> None:
    """Write a .hq.lock file with the given owner.

    Parameters
    ----------
    folder : os.PathLike[str]
        The handoff folder path.
    session : str
        The lock-owning session id.
    time_str : str
        ISO timestamp for the lock time field.
    cycle : int, optional
        Cycle number stored in the lock, by default 1.
    """
    p = pathlib.Path(folder)
    (p / '.hq.lock').write_text(
        f'slug={p.name}\n'
        f'session={session}\n'
        f'host=other-host\n'
        f'time={time_str}\n'
        f'cycle={cycle}\n')


def _set_cursor(handoff_path: 'os.PathLike[str]', content: str) -> None:
    """Replace the cursor body in HANDOFF.md, preserving the preamble.

    Parameters
    ----------
    handoff_path : os.PathLike[str]
        Path to the HANDOFF.md file.
    content : str
        New cursor content starting with the first '## ' heading.

    Notes
    -----
    - The preamble is everything from the file start to (and including)
      the newline before the first '## ' heading.
    - When no '## ' heading exists the content is appended after stripping
      any trailing whitespace from the preamble.
    """
    p = pathlib.Path(handoff_path)
    text = p.read_text()
    idx = text.find('\n## ')
    if idx == -1:
        p.write_text(text.rstrip() + '\n\n' + content)
        return
    p.write_text(text[:idx + 1] + content)


def _new_root(
    tmp_path: 'os.PathLike[str]',
    monkeypatch: Any,
    slug: str = _SLUG,
    cycle: str = '1',
    now: str = _NOW,
) -> 'pathlib.Path':
    """Create an HQ_ROOT and set all required env vars for a single-slug test.

    Parameters
    ----------
    tmp_path : os.PathLike[str]
        Pytest temporary path; HQ_ROOT is placed at tmp_path / 'root'.
    monkeypatch : Any
        Active pytest monkeypatch fixture.
    slug : str, optional
        Handoff slug, by default _SLUG.
    cycle : str, optional
        HQ_CYCLE override as a string, by default '1'.
    now : str, optional
        HQ_NOW override (ISO timestamp), by default _NOW.

    Returns
    -------
    pathlib.Path
        The expected folder path root/.handoff/<slug>/ (not yet created).
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', now)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / slug


def _thread(
    tmp_path: 'os.PathLike[str]',
    cycles: int,
    artifacts: int,
    files: int,
    monkeypatch: Any,
) -> 'pathlib.Path':
    """Build a synthesized multi-cycle thread for payload-bound tests.

    Parameters
    ----------
    tmp_path : os.PathLike[str]
        Pytest temporary path; HQ_ROOT is tmp_path / 'root'.
    cycles : int
        Total begin/finish cycles to execute.
    artifacts : int
        Number of SPEC-NN.md files stamped read_before=always from cycle 1.
    files : int
        Total top-level files (first `artifacts` SPEC-NN.md, rest file-NN.md).
    monkeypatch : Any
        Active pytest monkeypatch fixture for env-var injection.

    Returns
    -------
    pathlib.Path
        The handoff folder path (root/.handoff/thread-slug/).

    Notes
    -----
    - Cycle 1 writes _LARGE_CURSOR, stamps the spec files, adds 10 constraints.
    - Subsequent cycles leave cursor, artifacts, and standing unchanged so the
      only growth in payload is the log-block rollup line added after cycle 3.
    """
    slug = 'thread-slug'
    root = pathlib.Path(str(tmp_path)) / 'root'
    root.mkdir(exist_ok=True)
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_SESSION', 'thread-session')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))

    for i in range(artifacts):
        (folder / f'SPEC-{i:02d}.md').write_text(
            f'# Spec {i:02d}\n\n## Overview {i}\n\nDetails.\n')
    for i in range(files - artifacts):
        (folder / f'file-{i:02d}.md').write_text(
            f'# File {i:02d}\n\nContent line one.\nContent line two.\n')

    base_date = dt.date(2026, 1, 1)

    for cycle in range(1, cycles + 1):
        now = dt.datetime.combine(
            base_date + dt.timedelta(days=cycle - 1),
            dt.time(12, 0, 0))
        monkeypatch.setenv('HQ_NOW', now.isoformat())
        monkeypatch.setenv('HQ_CYCLE', str(cycle))

        hq.main(['begin', slug])

        if cycle == 1:
            _set_cursor(folder / 'HANDOFF.md', _LARGE_CURSOR)
            for i in range(artifacts):
                hq.main(
                    ['stamp', slug, f'SPEC-{i:02d}.md', '--read-before', 'always'])
            for j in range(10):
                hq.main([
                    'note', slug, 'constraint',
                    '--headline', f'Constraint {j:02d}',
                    f'Body of constraint {j:02d}.',
                    ])

        hq.main(['finish', slug, '--log', f'cycle {cycle}'])

    return folder


def test_refused_stamp_appends_a_receipt_row(tmp_path, monkeypatch):
    """R2: a refused stamp still writes a ledger row
    with reason='refused: ...'. The successor leaves the disk before the
    second stamp, so R1 refuses it.

    Mutation: R2 dropped; refused stamp exits without writing to ledger.tsv.
    Oracle: hand-counted ledger rows before and after a refused restamp;
    the second row has reason starting with 'refused:'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Overview\n\nContent.\n\n## Other\n\nDetails.\n')
    (folder / 'NEXT.md').write_text('# Next\n\nContent.\n')
    hq.main([
        'stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
        '--label', 'always stamp label',
        '--where', 'Overview',
        '--successor', 'NEXT.md',
        ])

    ledger = folder / 'ledger.tsv'
    rows_before = len(ledger.read_text().splitlines()) - 1

    (folder / 'NEXT.md').unlink()
    ret = hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'edit'])
    assert ret == 1

    lines = ledger.read_text().splitlines()
    rows_after = len(lines) - 1
    assert rows_after == rows_before + 1

    first_fields = lines[1].split('\t')
    last_fields = lines[-1].split('\t')
    # Ledger field indices: cycle=0 ts=1 path=2 base=3 kind=4
    # status=5 read_before=6 successor=7 where=8 sha12=9
    # lines=10 reason=11 label=12
    assert last_fields[11].startswith('refused:')
    assert last_fields[4] == first_fields[4]
    assert last_fields[5] == first_fields[5]
    assert last_fields[6] == first_fields[6]
    assert last_fields[7] == first_fields[7]
    assert last_fields[8] == first_fields[8]
    assert last_fields[12] == first_fields[12]
    assert first_fields[7] == 'NEXT.md'
    assert first_fields[8] == 'Overview'
    assert first_fields[12] == 'always stamp label'


def test_restamp_of_superseded_spec_keeps_carried_successor(
        tmp_path, monkeypatch):
    """A re-stamp passing no --successor is judged on the carried one.

    Mutation: successor_on_disk computed from the --successor flag alone,
    so R1 refuses to re-label a spec already superseded by a file on disk.
    Oracle: exit 0 and a third ledger row carrying successor NEXT.md,
    read_before never, and the new label.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    (folder / 'NEXT.md').write_text('# Next\n\nContent.\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'NEXT.md']) == 0

    ret = hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'renamed label'])
    assert ret == 0

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    assert len(lines) == 3
    row = dict(zip(hq.LEDGER_FIELDS, lines[2].split('\t')))
    assert row['status'] == 'superseded'
    assert row['read_before'] == 'never'
    assert row['successor'] == 'NEXT.md'
    assert row['label'] == 'renamed label'


def test_relabel_of_archived_spec_keeps_its_reason(tmp_path, monkeypatch):
    """A re-stamp passing no --reason carries the archive reason forward.

    Mutation: reason reset to '-' on every re-stamp, so R1's archived
    exemption misses and an archived spec can never be re-labeled.
    Oracle: exit 0 and a ledger row with status archived, reason obsolete,
    and the new label.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--archive', '--reason', 'obsolete']) == 0

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'renamed']) == 0

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['status'] == 'archived'
    assert row['reason'] == 'obsolete'
    assert row['label'] == 'renamed'


def test_restamp_after_refusal_skips_the_receipt_reason(tmp_path, monkeypatch):
    """The reason carried forward skips a refusal receipt.

    Mutation: reason carried from the latest row, so 'refused: ...' text
    lands in the next successful row; or the receipt writing the attempted
    read_before instead of copying the previous row's.
    Oracle: after a refused demotion, a plain re-stamp exits 0 with
    reason '-' and read_before still always.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'never']) == 1

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'again']) == 0

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['reason'] == '-'
    assert row['read_before'] == 'always'


def test_successor_with_explicit_status_keeps_that_status(
        tmp_path, monkeypatch):
    """--successor defaults status to superseded unless the stamp says otherwise.

    Mutation: --successor overwriting an explicit --status, or the
    matching guard dropped so it overwrites an explicit --read-before.
    Oracle: 'stamp --successor NEXT.md --status live --read-before always'
    writes status live and read_before always.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    (folder / 'NEXT.md').write_text('# Next\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--successor', 'NEXT.md', '--where', 'Spec',
        '--status', 'live', '--read-before', 'always']) == 0

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['status'] == 'live'
    assert row['read_before'] == 'always'
    assert row['successor'] == 'NEXT.md'


def test_defer_carries_status_and_successor(tmp_path, monkeypatch):
    """--defer sets kind, read_before, and reason only; the rest carries.

    Mutation: the deferred row built with status live and successor '-',
    resurrecting a superseded row.
    Oracle: a notes file superseded by notes-later.md then deferred keeps
    status superseded and successor notes-later.md.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-early.md').write_text('# Early\n')
    (folder / 'notes-later.md').write_text('# Later\n')
    hq.main(['stamp', _SLUG, 'notes-early.md', '--successor', 'notes-later.md'])

    assert hq.main(['stamp', _SLUG, 'notes-early.md', '--defer']) == 0

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['kind'] == 'other'
    assert row['read_before'] == 'never'
    assert row['reason'] == 'deferred'
    assert row['status'] == 'superseded'
    assert row['successor'] == 'notes-later.md'


def test_directory_or_self_is_not_a_successor(tmp_path, monkeypatch):
    """R1 accepts a successor only when it is an existing file, not itself.

    Mutation: successor_on_disk using exists(), or comparing the successor
    to the artifact by spelling instead of resolved path, so a directory,
    'SPEC.md', './SPEC.md', or the absolute path of SPEC.md demotes a spec
    with exit 0.
    Oracle: every stamp exits 1 and the current row stays live and always.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    (folder / 'drafts').mkdir()
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'drafts']) == 1
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'SPEC.md']) == 1
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', './SPEC.md']) == 1
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--successor', str(folder / 'SPEC.md')]) == 1

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['status'] == 'live'
    assert row['read_before'] == 'always'
    assert row['successor'] == '-'


def test_refused_stamp_does_not_clear_r3(tmp_path, monkeypatch):
    """A refusal receipt keeps the previous sha, so finish stays blocked.

    Mutation: the receipt row recording the file's current sha12, so a
    failed stamp satisfies R3's re-stamp requirement.
    Oracle: finish returns 1 before and after the refused stamp.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])
    (folder / 'SPEC.md').write_text('# Spec\n\nChanged.\n')
    assert hq.main(['finish', _SLUG, '--log', 'probe']) == 1

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'never']) == 1

    assert hq.main(['finish', _SLUG, '--log', 'probe']) == 1


def test_home_prefixed_successor_is_found_on_disk(tmp_path, monkeypatch):
    """A ~-prefixed successor resolves like an abs artifact path.

    Mutation: the successor joined onto the folder without expanduser, so
    '~/OUT-NEXT.md' is never found and the supersession is refused.
    Oracle: exit 0 and a row with status superseded and the ~ successor.
    """
    folder = _new_root(tmp_path, monkeypatch)
    home = pathlib.Path(tmp_path) / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    hq.main(['begin', _SLUG])
    (home / 'OUT-SPEC.md').write_text('# Spec\n\nContent.\n')
    (home / 'OUT-NEXT.md').write_text('# Next\n')

    ret = hq.main([
        'stamp', _SLUG, '~/OUT-SPEC.md', '--successor', '~/OUT-NEXT.md'])
    assert ret == 0

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['status'] == 'superseded'
    assert row['successor'] == '~/OUT-NEXT.md'


def test_stale_reason_does_not_excuse_status_archived(tmp_path, monkeypatch):
    """A carried reason does not survive a status change into archived.

    Mutation: reason carried forward regardless of a kind, status, or
    read_before change, so an earlier free-text reason satisfies the
    archive's reason requirement for a bare --status archived.
    Oracle: the demoting stamp is a usage error (exit 2, nothing written)
    and the current row stays live, always, with the earlier reason intact.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--reason', 'wip note']) == 0
    before = (folder / 'ledger.tsv').read_bytes()

    ret = hq.main([
        'stamp', _SLUG, 'SPEC.md', '--status', 'archived', '--read-before', 'never'])
    assert ret == 2
    assert (folder / 'ledger.tsv').read_bytes() == before

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['status'] == 'live'
    assert row['read_before'] == 'always'
    assert row['reason'] == 'wip note'


def test_reason_with_receipt_prefix_is_usage(tmp_path, monkeypatch):
    """A --reason starting with 'refused: ' exits 2 and writes no row.

    Mutation: the reserved prefix accepted, so an archive reason beginning
    'refused: ' is skipped by the carry and locks the artifact.
    Oracle: exit 2 and a ledger of header only.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')

    ret = hq.main([
        'stamp', _SLUG, 'SPEC.md', '--archive', '--reason', 'refused: by review'])
    assert ret == 2
    assert len((folder / 'ledger.tsv').read_text().splitlines()) == 1


def test_control_chars_in_label_refused_before_ledger(tmp_path, monkeypatch):
    """--label with a tab or newline is refused before reaching the ledger.

    Mutation: the control-character check removed from _do_stamp, so a
    newline in --label appends a forged row and a tab shifts every later
    field.
    Oracle: exit 2 and no data row written to ledger.tsv.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--label', 'a\tb\nc']) == 2

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    # Header only; no data row was written.
    assert len(lines) == 1


def test_batch_reports_a_line_it_cannot_parse(tmp_path, monkeypatch):
    """A batch line with an unknown flag is reported and the batch exits 2.

    Mutation: batch parsing with parse_known_args, so a mistyped flag is
    dropped and the line lands as a plain re-stamp with exit 0.
    Oracle: exit 2, exactly one data row (the well-formed line), and the
    misspelled line named in the output.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    (folder / 'notes-a.md').write_text('# A\n')
    monkeypatch.setattr(
        'sys.stdin', io.StringIO('SPEC.md --successr NEXT.md\nnotes-a.md\n'))

    ret = hq.main(['stamp', _SLUG, '--batch', '-'])
    assert ret == 2

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    assert len(lines) == 2
    assert lines[1].split('\t')[2] == 'notes-a.md'


def test_deferred_row_reappears_in_the_work_list(tmp_path, monkeypatch, capsys):
    """Begin lists deferred rows so a deferred file is not forgotten.

    Mutation: the work list built from unstamped, sha-moved, and missing
    rows only, so a deferred row is never shown again.
    Oracle: the second begin prints 'deferred x1: notes-a.md'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# A\n')
    assert hq.main(['stamp', _SLUG, 'notes-a.md', '--defer']) == 0
    capsys.readouterr()

    assert hq.main(['begin', _SLUG]) == 0

    out = capsys.readouterr().out
    assert 'deferred x1: notes-a.md' in out


def test_batch_survives_an_unbalanced_quote(tmp_path, monkeypatch):
    """A batch line shlex cannot split is reported; later lines still run.

    Mutation: the batch loop catching SystemExit only, so shlex's
    ValueError aborts the whole batch with nothing written.
    Oracle: exit 2 and one data row, from the well-formed second line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# A\n')
    monkeypatch.setattr(
        'sys.stdin', io.StringIO('notes-a.md --label "oops\nnotes-a.md --label ok\n'))

    ret = hq.main(['stamp', _SLUG, '--batch', '-'])
    assert ret == 2

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    assert len(lines) == 2
    assert lines[1].split('\t')[-1] == 'ok'


def test_open_accepts_the_blocks_finish_wrote(tmp_path, monkeypatch, capsys):
    """Open computes the same block sha finish stored, empty blocks included.

    Mutation: block_sha keeping a trailing newline, so the writer's
    heading-plus-newline and the reader's stripped text hash apart.
    Oracle: open after a fresh finish prints no 'block sha mismatch'.
    """
    _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    assert hq.main(['finish', _SLUG, '--log', 'first']) == 0
    capsys.readouterr()

    assert hq.main(['open', _SLUG]) == 0

    assert 'block sha mismatch' not in capsys.readouterr().out


def _cursor_with(folder, section, body):
    """Insert one cursor section before ## Log in the folder's HANDOFF.md."""
    hp = folder / 'HANDOFF.md'
    text = hp.read_text()
    hp.write_text(text.replace('## Log', f'## {section}\n\n{body}\n\n## Log'))


def test_finish_renders_a_directory_and_undecodable_file_at_always(
        tmp_path, monkeypatch):
    """Finish survives an always row that is a directory or not UTF-8.

    Mutation: the read block reading every always row with strict utf-8
    and no directory guard, so finish dies with a traceback after the
    Unfiled drain already wrote standing.md; or the two sized as text.
    Oracle: rows an older cycle left, seeded straight into the ledger
    since stamp now refuses an always row with no anchor; exit 0, one
    standing item, the directory sized as one file and the binary as
    one kilobyte in the read block.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'probes').mkdir()
    (folder / 'probes' / 'a.md').write_text('# a\n')
    (folder / 'bad.md').write_bytes(b'# Notes\n\xff\xfe not utf8\n')
    for path, kind, label in (('probes', 'probe-dir', 'the probe subtree'),
                              ('bad.md', 'notes', 'a binary note')):
        hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS, {
            'cycle': '1', 'ts': '2026-09-09T12:00:00', 'path': path,
            'base': 'folder', 'kind': kind, 'status': 'live',
            'read_before': 'always', 'successor': '-', 'where': '-',
            'sha12': '-', 'lines': '1', 'reason': '-', 'label': label,
            }, '\t'.join(hq.LEDGER_FIELDS))
    _cursor_with(
        folder, 'Unfiled', '- constraint: **Stdlib only** no third-party deps.')

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    standing = (folder / 'standing.md').read_text().splitlines()
    assert [ln for ln in standing if 'Stdlib only' in ln] == [standing[0]]
    text_lines = (folder / 'HANDOFF.md').read_text().splitlines()
    assert f'.handoff/{_SLUG}/probes  (1 files)  the probe subtree' in text_lines
    assert f'.handoff/{_SLUG}/bad.md  (1 KB)  a binary note' in text_lines


def test_subdirectory_row_on_disk_does_not_block_finish(tmp_path, monkeypatch):
    """A gated row in a subdirectory counts as present when its file exists.

    Mutation: absence judged by membership in the top-level walk instead
    of by the disk, so a subdirectory row blocks finish for ever.
    Oracle: exit 0 and the row rendered as a full artifacts line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'probes').mkdir()
    (folder / 'probes' / 'run1.md').write_text('# run 1\n')
    assert hq.main([
        'stamp', _SLUG, 'probes/run1.md', '--kind', 'notes',
        '--read-before', 'edit', '--label', 'probe result']) == 0

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    assert 'probes/run1.md  notes  edit  c1  probe result' in (
        folder / 'HANDOFF.md').read_text()


def test_log_line_carries_the_dirty_count(tmp_path, monkeypatch):
    """The Log line finish writes uses the manifest's repos spelling.

    Mutation: the Log line built as branch@sha with no dirty tail while
    the manifest row stores branch@sha +N, so the same cycle renders two
    ways.
    Oracle: with one modified tracked file, the Log line ends '+1): c1 log'
    and the header tail names the file. Runs real git in the tmp root.
    """
    folder = _new_root(tmp_path, monkeypatch)
    root = folder.parent.parent
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    # Exclude the handoff dir from git status so only tracked.txt is dirty.
    (root / '.gitignore').write_text('.handoff/\n', encoding='utf-8')
    (root / 'tracked.txt').write_text('one\n')
    subprocess.run(['git', 'add', 'tracked.txt', '.gitignore'], cwd=root, check=True)
    subprocess.run([
        'git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
        'commit', '-q', '-m', 'init'], cwd=root, check=True)
    (root / 'tracked.txt').write_text('two\n')
    monkeypatch.setenv('HQ_GIT', '1')
    hq.main(['begin', _SLUG])

    assert hq.main(['finish', _SLUG, '--log', 'c1 log']) == 0

    text = (folder / 'HANDOFF.md').read_text()
    assert '+1): c1 log' in text
    assert 'dirty: tracked.txt' in text


def test_finish_prints_the_token_split(tmp_path, monkeypatch, capsys):
    """Finish reports the payload estimate per block.

    Mutation: one whole-file token figure, so the agent cannot see which
    block is growing.
    Oracle: the success line names cursor, read, artifacts, and standing.
    """
    _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    out = capsys.readouterr().out
    assert '(cursor ' in out
    assert ', read ' in out
    assert ', artifacts ' in out
    assert ', standing ' in out


def test_takeover_names_the_unfinished_cycle(tmp_path, monkeypatch, capsys):
    """Taking over a stale lock says which cycle never finished.

    Mutation: the takeover printing only who was displaced.
    Oracle: the work list carries 'cycle 1 begun by other-session on
    other-host at <time>, never finished'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    stale = (dt.datetime.fromisoformat(_NOW) - dt.timedelta(hours=3)).isoformat()
    (folder / '.hq.lock').write_text(
        f'slug={_SLUG}\nsession=other-session\nhost=other-host\n'
        f'time={stale}\ncycle=1\n')
    capsys.readouterr()

    assert hq.main(['begin', _SLUG]) == 0

    assert (f'cycle 1 begun by other-session on other-host at {stale}, never finished'
            in capsys.readouterr().out)


def test_begin_after_adopt_announces_the_next_cycle(tmp_path, monkeypatch, capsys):
    """Begin recomputes its anchors after the adopt it ran.

    Mutation: anchors computed before adopt appended the manifest row, so
    begin announces and locks cycle 1 on a folder at cycle N; or the
    hand-edit check comparing HANDOFF.md to the archived original, so
    every begin after adopt writes a spurious .hand.md copy.
    Oracle: 'cycle N+1 begun' printed, cycle=N+1 in .hq.lock, and no
    hand copy, N read from the fixture's own header.
    """
    folder = _new_root(tmp_path, monkeypatch, slug='orbit-cache-rewrite')
    shutil.copytree(os.path.join(FIXTURES, 'orbit-cache-rewrite'), folder)
    header = (folder / 'HANDOFF.md').read_text().splitlines()[2]
    fixture_cycle = int(header.split('Cycle: ')[1].split(' ')[0])
    monkeypatch.delenv('HQ_CYCLE')

    assert hq.main(['begin', 'orbit-cache-rewrite']) == 0

    out = capsys.readouterr().out
    assert f'cycle {fixture_cycle + 1} begun' in out
    assert 'changed since last finish' not in out
    assert f'cycle={fixture_cycle + 1}' in (folder / '.hq.lock').read_text()
    assert not list((folder / 'cycles').glob('*.hand.md'))


def test_open_reports_a_moved_span_after_an_unresolved_anchor(
        tmp_path, monkeypatch, capsys):
    """Open compares spans even when the stored list starts with ?.

    Mutation: the stored-span pattern admitting digits only, so a row with
    one unresolved anchor never gets a moved-span report.
    Oracle: after two lines are inserted above the resolved heading, open
    prints 'span moved: SPEC.md'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Alpha\n\na\n\n## Beta\n\nb\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--where', 'Missing;Beta',
        '--read-before', 'always']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    assert 'SPEC.md:?,' in (folder / 'HANDOFF.md').read_text()
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Alpha\n\na\nmore\nmore\n\n## Beta\n\nb\n')
    capsys.readouterr()

    assert hq.main(['open', _SLUG]) == 0

    assert 'span moved: SPEC.md' in capsys.readouterr().out


def test_adopt_carries_the_legacy_log_into_the_manifest(tmp_path, monkeypatch):
    """Adopt writes a line-count summary to the manifest log field.

    Mutation: the prior log lines inlined into the log field instead of the
    summary, so the log field grows unbounded and the note field stays '-'.
    Oracle: log field == 'adopted; prior Log: 2 lines in cycles/c02.md';
    both hand-written Log lines appear in the note field.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    log_lines = [
        '- 2026-08-30 (cycle 1): cache layout drafted.',
        '- 2026-08-31 (cycle 2): generator wired to the layout.',
    ]
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-08-31 | Cycle: 2\n\n'
        '## Task\nx\n\n## Now\ny\n\n## Log\n' + '\n'.join(log_lines) + '\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = (folder / 'cycles' / 'manifest.tsv').read_text().splitlines()
    last_row = dict(zip(hq.MANIFEST_FIELDS, rows[-1].split('\t')))
    log_field = last_row['log']
    note_field = last_row['note']
    assert log_field == 'adopted; prior Log: 2 lines in cycles/c02.md'
    for ln in log_lines:
        assert ln in note_field


def test_adopt_stores_a_key_files_pointer_outside_the_walk(tmp_path, monkeypatch):
    """A Key files pointer at a subdirectory or abs path gets its own row.

    Mutation: pointers the top-level walk cannot match only printed, so
    the label and the read grade are dropped.
    Oracle: ledger rows for probes/run1.md (base folder) and ~/OUT-NOTE.md
    (base abs), each carrying its pointer text as label.
    """
    folder = _new_root(tmp_path, monkeypatch)
    home = pathlib.Path(tmp_path) / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    folder.mkdir(parents=True)
    (folder / 'probes').mkdir()
    (folder / 'probes' / 'run1.md').write_text('# run 1\n')
    (home / 'OUT-NOTE.md').write_text('# note\n')
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 2\n\n'
        '## Task\nx\n\n## Now\ny\n\n## Plan\n\n## State\n\n'
        '## Key files\n- `probes/run1.md` the probe result\n'
        '- `~/OUT-NOTE.md` an outside note\n\n## Log\n- 2026-09-01 (cycle 1): began.\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = [dict(zip(hq.LEDGER_FIELDS, ln.split('\t')))
            for ln in (folder / 'ledger.tsv').read_text().splitlines()[1:]]
    by_path = {r['path']: r for r in rows}
    assert by_path['probes/run1.md']['base'] == 'folder'
    assert by_path['probes/run1.md']['label'] == 'the probe result'
    assert by_path['~/OUT-NOTE.md']['base'] == 'abs'
    assert by_path['~/OUT-NOTE.md']['label'] == 'an outside note'


def test_adopt_refuses_an_adopted_folder(tmp_path, monkeypatch):
    """Adopt runs once; a second run exits 1 and changes nothing.

    Mutation: no guard, so a second adopt appends a bare row per file
    that outranks the seeded labels and grades.
    Oracle: exit 1 and a byte-identical ledger.
    """
    folder = _new_root(tmp_path, monkeypatch, slug='orbit-cache-rewrite')
    shutil.copytree(os.path.join(FIXTURES, 'orbit-cache-rewrite'), folder)
    assert hq.main(['adopt', 'orbit-cache-rewrite']) == 0
    ledger_before = (folder / 'ledger.tsv').read_bytes()

    assert hq.main(['adopt', 'orbit-cache-rewrite']) == 1

    assert (folder / 'ledger.tsv').read_bytes() == ledger_before


def test_adopt_rebuilds_the_header_without_git(tmp_path, monkeypatch):
    """Adopt writes a fresh header even when no git anchor exists.

    Mutation: the legacy header copied verbatim without git, so a stale
    branch and sha are read back as this write's anchors.
    Oracle: the header line is exactly 'Written: <now> | Cycle: 3'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-01-01 | Cycle: 3 | main @ abc1234 | dirty: x.py\n\n'
        '## Task\nx\n\n## Now\ny\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    lines = (folder / 'HANDOFF.md').read_text().splitlines()
    assert lines[2] == f'Written: {_NOW[:10]} | Cycle: 3'


def test_finish_ranks_collisions_by_folder_wide_rarity(tmp_path, monkeypatch, capsys):
    """The collision advisory counts a term over every top-level file.

    Mutation: rarity counted over spec files only, so every spec term ties
    and the rarest hit can be the one the three-hit cap drops.
    Oracle: Delta (in one file) is reported before Alpha (in four).
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Alpha section\n\n## Delta section\n')
    for name in ('notes-a.md', 'notes-b.md', 'notes-c.md'):
        (folder / name).write_text('# n\n\nAbout the Alpha path.\n')
    hp = folder / 'HANDOFF.md'
    hp.write_text(hp.read_text().replace(
        '## Now\n', '## Now\nWire Alpha and Delta into the run.\n'))
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    hits = [ln for ln in capsys.readouterr().out.splitlines() if 'collides:' in ln]
    assert len(hits) == 2
    assert 'Delta <- SPEC.md:5 ' in hits[0]
    assert 'Alpha <- SPEC.md:3 ' in hits[1]


def test_when_prints_every_row_with_no_cap(tmp_path, monkeypatch, capsys):
    """When shows every row for the path, oldest first, however many.

    Mutation: a 30-row cap restored, with a count line for the rest.
    Oracle: 35 stamps of one file give 35 lines and no 'omitted' line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# n\n')
    for _ in range(35):
        assert hq.main(['stamp', _SLUG, 'notes-a.md']) == 0
    capsys.readouterr()

    assert hq.main(['when', _SLUG, 'notes-a.md']) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 35
    assert not any('omitted' in ln for ln in lines)


def test_artifacts_verb_prints_live_rows_only(tmp_path, monkeypatch, capsys):
    """Artifacts lists live rows and unstamped entries, nothing else.

    Mutation: non-live rows printed in an abbreviated form.
    Oracle: after SPEC.md is archived, no output line names it.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--archive', '--reason', 'old']) == 0
    capsys.readouterr()

    assert hq.main(['artifacts', _SLUG]) == 0

    assert 'SPEC.md' not in capsys.readouterr().out


def _conforming(folder, cycle, body):
    """Write a conforming HANDOFF.md whose sections follow the header."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\nWritten: 2026-09-01 | Cycle: {cycle}\n\n{body}')


def test_adopt_seeds_numbered_and_plain_decision_lines(tmp_path, monkeypatch):
    """Adopt takes every Decisions line as an item, not dash bullets alone.

    Mutation: only '- ' lines start an item, so a numbered or plain
    Decisions section reaches neither standing.md nor Unfiled.
    Oracle: two standing items with the headlines 'Chose alpha' and
    'Capped at fifty.', the second built from a plain line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Decisions\n'
                '1. **Chose alpha** Because beta needs a rewrite.\n'
                'Capped at fifty. Memory bound.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [i['headline'] for i in items] == ['Chose alpha', 'Capped at fifty.']
    assert items[1]['body'] == 'Memory bound.'


def test_adopt_joins_a_bullet_wrapped_at_the_column(tmp_path, monkeypatch):
    """Adopt reads a hand-wrapped bullet as one item, not one per line.

    Mutation: an unindented line always opening a new item, so a bullet
    wrapped over four lines seeds four and each continuation's first
    fragment becomes a headline.
    Oracle: one item, its headline the hand-joined sentence, against a
    section whose four physical lines carry one idea.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Constraints\n'
                '- Ruled out three routes: page offsets for the size\n'
                'problem; a packed wire format, since the reader wants\n'
                'plain text; and generated queries, which would undo\n'
                'the read-only rule.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['id'], i['headline']) for i in items] == [
        ('c01', ('Ruled out three routes: page offsets for the size problem;'
                 ' a packed wire format, since the reader wants plain text;'
                 ' and generated queries, which would undo the read-only'
                 ' rule.')),
        ]


def test_adopt_splits_a_plain_item_below_a_finished_sentence(
        tmp_path, monkeypatch):
    """Adopt keeps a plain line below a finished bullet as its own item.

    Mutation: joining every unindented line into the bullet above, so a
    section mixing a bullet with a plain one-line item seeds one item
    carrying both. This is the boundary the wrapped case turns on.
    Oracle: two items, hand-written, against the wrapped case that
    seeds one; the line above ends a sentence and this one opens
    another.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Constraints\n'
                '- Never below one.\nAlways above zero.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['id'], i['headline']) for i in items] == [
        ('c01', 'Never below one.'),
        ('c02', 'Always above zero.'),
        ]


def test_adopt_closes_an_open_item_at_a_blank_line(tmp_path, monkeypatch):
    """Adopt ends a wrapped item at a blank line, not at the next marker.

    Mutation: a blank line leaving the item open, so a paragraph below
    an unfinished bullet joins it and two ideas land as one.
    Oracle: two items, hand-written. The bullet's last line ends with
    no sentence terminator, so the blank line is the only thing that
    can separate them.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Constraints\n'
                '- Ruled out page offsets and\npacked formats\n\n'
                'Revisit when the reader changes.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['id'], i['headline']) for i in items] == [
        ('c01', 'Ruled out page offsets and packed formats'),
        ('c02', 'Revisit when the reader changes.'),
        ]


def test_adopt_keeps_the_words_of_an_inner_bold_span(tmp_path, monkeypatch):
    """Adopt drops an inner ** pair rather than nesting it in the item.

    Mutation: wrapping the headline whole, so the inner pair closes the
    outer span early and the item renders as a two-word headline
    followed by plain text.
    Oracle: the stored line carries exactly one ** pair, and the parsed
    headline holds the inner word with no markers.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Constraints\n'
                '- widgets is a **public** package - it carries no\n'
                '  internal names.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'standing.md').read_text()
    items, _ = hq._parse_standing(text)
    assert [i['headline'] for i in items] == [
        'widgets is a public package - it carries no internal names.']
    stored = [ln for ln in text.splitlines() if 'widgets' in ln]
    assert len(stored) == 1
    assert stored[0].count('**') == 2


def test_adopt_concatenates_a_repeated_heading(tmp_path, monkeypatch):
    """Two sections with the same heading both reach the cursor.

    Mutation: sections keyed by heading text with the last body winning.
    Oracle: the adopted Task carries both paragraphs.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 3, '## Task\nParagraph A, the original scope.\n\n'
                '## Now\ny\n\n## Task\nParagraph B, the revised scope.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'HANDOFF.md').read_text()
    assert 'Paragraph A, the original scope.' in text
    assert 'Paragraph B, the revised scope.' in text


def test_r3_fires_for_a_gated_row_in_a_subdirectory(tmp_path, monkeypatch, capsys):
    """A subdirectory row's sha is checked like a top-level row's.

    Mutation: the sha map built from the top-level walk only, so check_r3
    never sees a subdirectory row and finish passes an edited gated file.
    Oracle: finish exits 1 naming sub/DESIGN.md after its body changes.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'DESIGN.md').write_text('# Design\n\noriginal body\n')
    assert hq.main(['stamp', _SLUG, 'sub/DESIGN.md', '--where', 'Design',
                    '--label', 'sub spec']) == 0
    (folder / 'sub' / 'DESIGN.md').write_text('# Design\n\nedited body\n')
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 1

    assert 'R3: sub/DESIGN.md' in capsys.readouterr().out


def test_finish_recreates_a_pruned_cycles_directory(tmp_path, monkeypatch):
    """Finish makes cycles/ before its write pass.

    Mutation: no directory guard, so standing.md and HANDOFF.md are written
    and then the archive write raises, leaving the lock held for ever.
    Oracle: exit 0 and cycles/c01.md present.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    shutil.rmtree(folder / 'cycles')

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    assert (folder / 'cycles' / 'c01.md').exists()


def test_missing_row_whose_file_returns_reads_live(tmp_path, monkeypatch):
    """A row stored missing renders live once its file is on disk.

    Mutation: reconciliation adding missing marks but never clearing one.
    Oracle: after later.md is created, the artifacts block counts it under
    its kind and carries no missing count.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 3, '## Task\nx\n\n## Key files\n'
                '- `later.md` Notes on the thing, written next cycle\n\n## Log\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    assert dict(zip(hq.LEDGER_FIELDS, rows[-1].split('\t')))['status'] == 'missing'
    (folder / 'later.md').write_text('# Later\n\nnow it exists\n')
    monkeypatch.setenv('HQ_CYCLE', '4')
    hq.main(['begin', _SLUG])

    assert hq.main(['finish', _SLUG, '--log', 'made later.md']) == 0

    block = (folder / 'HANDOFF.md').read_text().split('<!-- hq:artifacts', 1)[1]
    block = block.split('<!-- /hq:artifacts -->', 1)[0]
    assert 'missing' not in block
    assert 'other x1' in block


def test_adopt_never_renders_an_absent_pointer_in_full(tmp_path, monkeypatch):
    """A Key files pointer to a file not on disk is counted, never listed.

    Mutation: the seeded status `'live' if on_disk else 'missing'`
    collapsed to `'live'`, so a nonexistent spec lands in the read block.
    Oracle: no read line names SPEC-GONE.md and the artifacts block counts
    one missing row.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 3, '## Task\nx\n\n## Key files\n'
                '- `sub/SPEC-GONE.md` Read now: a spec that is not on disk\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'HANDOFF.md').read_text()
    read_block = text.split('<!-- hq:read', 1)[1].split('<!-- /hq:read -->', 1)[0]
    assert 'SPEC-GONE' not in read_block
    assert 'missing 1' in text


def test_artifacts_verb_hides_a_folder_row_whose_file_is_gone(
        tmp_path, monkeypatch, capsys):
    """Artifacts checks folder rows against the disk like abs rows.

    Mutation: only abs rows disk-checked, so a deleted top-level file still
    prints a full line.
    Oracle: after notes-a.md is deleted, no output line names it.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# A\n')
    hq.main(['stamp', _SLUG, 'notes-a.md', '--read-before', 'mention', '--label', 'n'])
    (folder / 'notes-a.md').unlink()
    capsys.readouterr()

    assert hq.main(['artifacts', _SLUG]) == 0

    assert 'notes-a.md' not in capsys.readouterr().out


def test_worklist_does_not_report_a_present_subdirectory_row(
        tmp_path, monkeypatch, capsys):
    """Begin judges a folder row's presence by the disk.

    Mutation: presence judged by the top-level sha map, so a subdirectory
    row is reported 'missing live' on every begin.
    Oracle: the second begin prints no 'missing live' line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'DESIGN.md').write_text('# Design\n')
    hq.main(['stamp', _SLUG, 'sub/DESIGN.md', '--label', 'sub spec'])
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    capsys.readouterr()

    assert hq.main(['begin', _SLUG]) == 0

    assert 'missing live' not in capsys.readouterr().out


def test_adopt_keeps_text_before_the_first_heading(tmp_path, monkeypatch):
    """A line between the header and the first section lands in Unfiled.

    Mutation: the section scan collecting lines only after a heading, so a
    preamble is dropped without a trace.
    Oracle: the adopted cursor holds '- unfiled: Status: blocked on the
    adapter.'
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 3, 'Status: blocked on the adapter.\n\n## Task\nx\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    assert '- unfiled: Status: blocked on the adapter.' in (
        folder / 'HANDOFF.md').read_text()


def test_adopt_carries_the_continuation_lines_of_a_wrapped_header_in_the_note(
        tmp_path, monkeypatch, capsys):
    """Lines continuing a wrapped `Written:` header ride in the manifest note.

    Mutation: the preamble filter comparing each line against the matched
    header line alone, so the two continuation lines become `- unfiled:`
    bullets that finish refuses as untyped; the lines dropped instead of
    carried, so the note lacks them; or conservation counting them
    against the union, so they print as not carried.
    Oracle: the adopted cursor holds the real preamble line as its only
    Unfiled bullet, the manifest note reads `adopted <date> by <session>
    | header: <both lines joined> | <the Log item>`, and the summary
    prints `conservation: every original line carried`.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-09-01 | Cycle: 3 | master @ 0123abc | dirty:\n'
        'pyproject.toml modified; alpha/ and beta/ untracked\n'
        '(pre-existing WIP).\n\n'
        'Status: blocked on the adapter.\n\n'
        '## Task\nx\n\n## Log\n- 2026-09-01: started\n')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'HANDOFF.md').read_text()
    unfiled = [ln for ln in text.splitlines() if ln.startswith('- unfiled:')]
    assert unfiled == ['- unfiled: Status: blocked on the adapter.']
    assert 'conservation: every original line carried' in capsys.readouterr().out
    manifest = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert manifest[-1]['note'] == (
        f'adopted {_NOW[:10]} by {_SESSION}'
        ' | header: pyproject.toml modified; alpha/ and beta/ untracked'
        ' (pre-existing WIP). | - 2026-09-01: started')


def test_a_line_glued_under_the_header_reaches_the_note_not_the_void(
        tmp_path, monkeypatch, capsys):
    """A line directly under the header, no blank between, is header text the note keeps.

    Mutation: the header paragraph's continuation dropped on the floor,
    so the line is absent from the cursor, the note, and the not-carried
    list alike.
    Oracle: no Unfiled bullet, the manifest note reads `adopted <date> by
    <session> | header: Status: blocked on the adapter.`, and conservation
    reports every line carried.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-09-01 | Cycle: 3\n'
        'Status: blocked on the adapter.\n\n'
        '## Task\nx\n')

    assert hq.main(['adopt', _SLUG]) == 0

    assert '- unfiled:' not in (folder / 'HANDOFF.md').read_text()
    assert 'conservation: every original line carried' in capsys.readouterr().out
    manifest = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert manifest[-1]['note'] == (
        f'adopted {_NOW[:10]} by {_SESSION} | header: Status: blocked on the adapter.')


def test_conservation_knows_a_bold_header_by_its_pattern(
        tmp_path, monkeypatch, capsys):
    """A `**Written: ... | Cycle: N**` header line is the script's, not lost text.

    Mutation: conservation recognizing the header by a `Written:` prefix
    while split_handoff accepts the pattern anywhere on the line, so a
    bold or quoted header adopt accepts prints as not carried.
    Oracle: adopt exits 0 on the bold header and the summary prints
    `conservation: every original line carried`.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        '**Written: 2026-09-01 | Cycle: 3 | master @ 0123abc | clean**\n\n'
        '## Task\nx\n')

    assert hq.main(['adopt', _SLUG]) == 0

    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_where_anchor_escapes_a_semicolon_inside_a_heading(
        tmp_path, monkeypatch, capsys):
    r"""`\\;` in a `--where` anchor names a heading whose text carries `;`.

    Mutation: the where field split on every `;`, so each fragment of the
    heading is an unresolved anchor and the read block renders `?`; or
    the escape left in the anchor, so the literal never equals the heading.
    Oracle: after stamping notes-findings.md with the escaped heading,
    finish renders `notes-findings.md:3-4`, open prints no `unresolved
    anchor` line, and read prints the heading and its body line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-findings.md').write_text(
        '# Findings\n\n## F7. Cache hit ratio holds (n>=2; n=1 excluded)\n'
        'body\n## Next\nmore\n')
    assert hq.main([
        'stamp', _SLUG, 'notes-findings.md', '--read-before', 'always',
        '--where', 'F7. Cache hit ratio holds (n>=2\\; n=1 excluded)',
        '--label', 'f7']) == 0
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n## Task\nx\n')
    assert hq.main(['finish', _SLUG, '--log', 'one']) == 0
    assert 'notes-findings.md:3-4' in (folder / 'HANDOFF.md').read_text()
    capsys.readouterr()
    assert hq.main(['open', _SLUG]) == 0
    assert 'unresolved anchor' not in capsys.readouterr().out
    assert hq.main(['read', _SLUG, 'notes-findings.md']) == 0
    assert capsys.readouterr().out.splitlines() == [
        '## F7. Cache hit ratio holds (n>=2; n=1 excluded)', 'body']


def test_finish_flags_a_one_line_span_ended_by_a_same_level_heading(
        tmp_path, monkeypatch, capsys):
    """Finish prints an advisory for a one-line span that a same-level heading ends.

    Mutation: the span-length test dropped, so every anchored row is
    flagged; the next-line level compared loosely, so a one-line section
    closed by a higher-level heading is flagged too; or the advisory
    missing, so a heading wrapped onto a second `##` line silently yields
    a one-line span.
    Oracle: the wrapped heading at notes-wrap.md line 3 is the only line
    flagged; `## Solo` in notes-solo.md, one line long but closed by a
    `#` heading, is not; `## Alpha` in notes-multi.md, three lines long
    and closed by a `##` heading, is not; `## Last` on the final line of
    notes-last.md is not, and does not crash finish.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-wrap.md').write_text(
        '# Notes\n\n## Alpha beta gamma\n## delta epsilon\nbody\n## Next\nmore\n')
    (folder / 'notes-solo.md').write_text('# Notes\n\n## Solo\n# Top\nbody\n')
    (folder / 'notes-multi.md').write_text(
        '# Notes\n\n## Alpha\nbody one\nbody two\n## Beta\nmore\n')
    (folder / 'notes-last.md').write_text('# Notes\n\n## Last')
    for name, where in (('notes-wrap.md', 'Alpha beta gamma'),
                        ('notes-solo.md', 'Solo'),
                        ('notes-multi.md', 'Alpha'),
                        ('notes-last.md', 'Last')):
        assert hq.main([
            'stamp', _SLUG, name, '--read-before', 'always',
            '--where', where, '--label', 'n']) == 0
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n## Task\nx\n')
    capsys.readouterr()
    assert hq.main(['finish', _SLUG, '--log', 'one']) == 0
    flagged = [ln for ln in capsys.readouterr().out.splitlines()
               if 'one-line span' in ln]
    assert flagged == [
        ('advisory: one-line span at notes-wrap.md:3; a wrapped heading?'
         ' - anchor the last wrapped line, or join the heading'
         ' where the file may be edited')]


def test_key_files_continuation_with_one_space_joins_the_label(
        tmp_path, monkeypatch):
    """Any indented line under a pointer continues its free text.

    Mutation: the continuation test requiring exactly two leading spaces.
    Oracle: the row's label ends with the one-space continuation.
    """
    folder = _new_root(tmp_path, monkeypatch)
    (folder.parent / 'x').mkdir(parents=True, exist_ok=True)
    _conforming(folder, 3, '## Task\nx\n\n## Key files\n'
                '- `notes-a.md` first half of the label\n second half\n\n## Log\n')
    (folder / 'notes-a.md').write_text('# A\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = [dict(zip(hq.LEDGER_FIELDS, ln.split('\t')))
            for ln in (folder / 'ledger.tsv').read_text().splitlines()[1:]]
    assert {r['path']: r['label'] for r in rows}['notes-a.md'] == (
        'first half of the label second half')


def test_adopt_files_a_lead_in_sentence_above_the_first_bullet_under_unfiled(
        tmp_path, monkeypatch, capsys):
    """A lead-in sentence above a section's first bullet is not an item.

    Mutation: every unindented non-empty line read as an item, so the
    lead-in is filed as [x01] and stands in the block for good; or the
    skip generalized past the first bullet, so a bullet-free section
    loses all its items.
    Oracle: the hand-written fixture - the two bullets are the only dead
    ends, the lead-in is read back verbatim under ## Unfiled, and the
    three-line bullet-free Constraints section still seeds three items.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Constraints\n'
                'Never below one.\nAlways above zero.\nKeep the floor.\n\n'
                '## Dead ends\nDo not retry these.\n'
                '- Tried caching the row; the lock still breaks it.\n'
                '- Tried an async retry loop; the same lock blocks it.\n\n'
                '## Log\n- 2026-09-01: started\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['id'], i['headline']) for i in items] == [
        ('c01', 'Never below one.'),
        ('c02', 'Always above zero.'),
        ('c03', 'Keep the floor.'),
        ('x01', 'Tried caching the row; the lock still breaks it.'),
        ('x02', 'Tried an async retry loop; the same lock blocks it.'),
    ]
    assert '- unfiled: Do not retry these.' in (folder / 'HANDOFF.md').read_text()
    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_adopt_joins_a_wrapped_lead_in_sentence_into_one_unfiled_bullet(
        tmp_path, monkeypatch):
    """A lead-in sentence wrapped over two lines is one Unfiled bullet.

    Mutation: each unindented line before the first bullet filed as its
    own lead-in, so a wrapped sentence becomes two half-sentence bullets
    to retype; or the blank-line reset dropped, so two lead-in
    paragraphs merge.
    Oracle: hand-counted - the two-line sentence is one bullet, the
    paragraph after the blank line is another, and the bullet below is
    the section's only item.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Decisions\n'
                'These rulings govern the loader and survive every rewrite of\n'
                'the fetch path, so read them before touching it.\n\n'
                'Newer rulings sit below.\n'
                '- Keep the retry ceiling at four attempts.\n\n'
                '## Log\n- 2026-09-01: started\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [i['headline'] for i in items] == [
        'Keep the retry ceiling at four attempts.']
    text = (folder / 'HANDOFF.md').read_text()
    assert [ln for ln in text.splitlines() if ln.startswith('- unfiled: ')] == [
        ('- unfiled: These rulings govern the loader and survive every rewrite'
         ' of the fetch path, so read them before touching it.'),
        '- unfiled: Newer rulings sit below.',
    ]


def test_adopt_keeps_the_marker_a_header_tail_line_already_carries(
        tmp_path, monkeypatch):
    """A header tail line that opens with a dash is filed with one marker.

    Mutation: a bullet marker prefixed to every tail line, so a tail
    written as a dash list lands under ## Environment as '- - text'.
    Oracle: the fixture's two tail lines read back under ## Environment
    exactly as written, one marker each.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-09-01 | Cycle: 3 | loader-svc @ aaa1111\n'
        '- parser-lib @ bbb2222 dirty 4\n'
        '- render-kit @ ccc3333\n\n'
        '## Task\nx\n\n## Log\n- 2026-09-01: started\n')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'HANDOFF.md').read_text()
    env = text.split('## Environment\n', 1)[1].split('\n\n## ', 1)[0]
    assert env == '- parser-lib @ bbb2222 dirty 4\n- render-kit @ ccc3333'


def test_adopt_files_the_wrapped_header_tail_under_environment(
        tmp_path, monkeypatch, capsys):
    """The lines continuing a wrapped header land under ## Environment too.

    Mutation: the tail appended to the manifest note only, so the repo
    state a no-git thread recorded in its header leaves the live file at
    adopt and no verb ever shows it again.
    Oracle: the fixture's continuation line read back verbatim as a
    bullet after the section's own line in the ## Environment body adopt
    wrote, with the manifest note unchanged.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-09-01 | Cycle: 3 | loader-svc @ aaa1111 +3\n'
        'parser-lib @ bbb2222 dirty 4 | render-kit @ ccc3333\n\n'
        '## Task\nx\n\n## Environment\nServers: box-one.\n\n'
        '## Log\n- 2026-09-01: started\n')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'HANDOFF.md').read_text()
    env = text.split('## Environment\n', 1)[1].split('\n\n## ', 1)[0]
    assert env == (
        'Servers: box-one.\n- parser-lib @ bbb2222 dirty 4 | render-kit @ ccc3333')
    manifest = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert (' | header: parser-lib @ bbb2222 dirty 4 | render-kit @ ccc3333'
            in manifest[-1]['note'])
    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_adopt_headline_ends_at_a_sentence_not_a_dot(tmp_path, monkeypatch):
    """A plain item's headline is its first sentence, dots inside words kept.

    Mutation: the headline cut at the first period anywhere, so
    'loader.py' splits mid-name, and ! or ? never end a sentence.
    Oracle: hand-computed headlines and bodies for two items.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Decisions\n'
                '- Keep loader.py as the one source of truth. It reran clean.\n'
                '- Never trust mtime! It lies on network mounts.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['headline'], i['body']) for i in items] == [
        ('Keep loader.py as the one source of truth.', 'It reran clean.'),
        ('Never trust mtime!', 'It lies on network mounts.'),
    ]


def test_adopt_headline_never_ends_inside_an_open_quotation(
        tmp_path, monkeypatch):
    """A sentence end inside a quotation does not end a plain item's headline.

    Mutation: the quote-balance check dropped, so the headline ends at
    the period inside the quotation and standing.md carries a bold span
    with one unmatched quote mark.
    Oracle: hand-computed - the quotation closes before the split, so the
    headline holds both quote marks and the body is the sentence after.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Decisions\n'
                '- User, verbatim: "keep the old format, which is fine.'
                ' Migrate later." Filed as is.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['headline'], i['body']) for i in items] == [
        ('User, verbatim: "keep the old format, which is fine. Migrate later."',
         'Filed as is.'),
    ]


def test_adopt_runs_a_two_word_label_headline_on_to_the_next_sentence(
        tmp_path, monkeypatch):
    """A first sentence of one or two words is a label, not the headline.

    Mutation: the label rule dropped, so seven dead ends render as
    'Cycle 26.' in the Standing block; or the rule keyed on character
    length, so 'A pid in the lock.' swallows its body too.
    Oracle: hand-computed - 'Cycle 26.' has two words and runs on to the
    next sentence end; a colon is no sentence end, so the colon item is
    already whole; the five-word control keeps its split.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 2, '## Task\nx\n\n## Dead ends\n'
                '- Cycle 26. Reading the batch gain showed the loader still'
                ' drops rows. Not again.\n'
                '- Cycles 1-15: every attempt to shrink the batch raised'
                ' memory instead.\n'
                '- A pid in the lock. Meaningless across hosts.\n\n## Log\n')

    assert hq.main(['adopt', _SLUG]) == 0

    items, _ = hq._parse_standing((folder / 'standing.md').read_text())
    assert [(i['headline'], i['body']) for i in items] == [
        ('Cycle 26. Reading the batch gain showed the loader still drops rows.',
         'Not again.'),
        ('Cycles 1-15: every attempt to shrink the batch raised memory instead.',
         ''),
        ('A pid in the lock.', 'Meaningless across hosts.'),
    ]


def test_key_files_group_labels_grade_and_loose_lines_go_unfiled(
        tmp_path, monkeypatch):
    """Read now: and Reference only: lines grade the bullets below them.

    Mutation: the group labels ignored (a notes file under Read now: seeds
    never) and a loose line under Key files dropped without a trace.
    Oracle: notes-a.md always, notes-b.md edit, labels without the group
    text, and the loose line as an unfiled bullet.
    """
    folder = _new_root(tmp_path, monkeypatch)
    _conforming(folder, 3, '## Task\nx\n\n## Key files\nRead now:\n'
                '- `notes-a.md` the live sketch\n\nReference only:\n'
                '- `notes-b.md` the older sketch\n'
                'Everything else is draft.\n\n## Log\n')
    (folder / 'notes-a.md').write_text('# A\n')
    (folder / 'notes-b.md').write_text('# B\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in (
        dict(zip(hq.LEDGER_FIELDS, ln.split('\t')))
        for ln in (folder / 'ledger.tsv').read_text().splitlines()[1:])}
    assert rows['notes-a.md']['read_before'] == 'always'
    assert rows['notes-a.md']['label'] == 'the live sketch'
    assert rows['notes-b.md']['read_before'] == 'edit'
    assert '- unfiled: Everything else is draft.' in (
        folder / 'HANDOFF.md').read_text()


def test_absent_abs_row_is_an_advisory_not_r3(tmp_path, monkeypatch, capsys):
    """A gated abs row whose file is gone is advised, never an R3 block.

    Mutation: the sha map recording '-' for an absent abs path, which
    check_r3 reads as a moved sha.
    Oracle: finish exits 0 and prints the abs-path advisory.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    outside = folder.parent.parent / 'outside.md'
    outside.write_text('# Outside\n')
    assert hq.main([
        'stamp', _SLUG, str(outside), '--read-before', 'always', '--where', 'Outside',
        '--label', 'outside notes']) == 0
    outside.unlink()
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    out = capsys.readouterr().out
    assert 'advisory: abs path not on disk' in out
    assert 'R3:' not in out


def test_lifted_missing_row_faces_r3(tmp_path, monkeypatch, capsys):
    """A row lifted from missing to live is checked by R3 like any other.

    Mutation: check_r3 run on the stored rows before reconciliation, so a
    lifted row renders as live gated without facing the sha gate.
    Oracle: finish exits 1 naming notes-x.md after its body changes.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-x.md').write_text('# X\n\nv1\n')
    assert hq.main([
        'stamp', _SLUG, 'notes-x.md', '--read-before', 'edit',
        '--status', 'missing', '--label', 'the notes']) == 0
    (folder / 'notes-x.md').write_text('# X\n\nv2 changed\n')
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 1

    assert 'R3: notes-x.md' in capsys.readouterr().out


def test_finish_rarity_counts_files_containing_the_term(
        tmp_path, monkeypatch, capsys):
    """Rarity is the number of top-level files containing the term as text.

    Mutation: rarity counted from extracted terms only, so a plain-text
    flag in every file scores zero and outranks genuinely rare terms.
    Oracle: --force, present in five files, is the hit the cap drops;
    Zephyr, Yankee, Xenon are reported.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    for n in range(5):
        (folder / f'notes-{n}.md').write_text(
            '# n\n\nRun the loader with --force to rebuild the cache.\n')
    for word in ('--force', 'Zephyr', 'Yankee', 'Xenon'):
        assert hq.main([
            'note', _SLUG, 'dead-end', '--headline', f'the {word} path failed',
            'body']) == 0
    hp = folder / 'HANDOFF.md'
    hp.write_text(hp.read_text().replace(
        '## Now\n', '## Now\nRun `--force` on the Zephyr, Yankee and Xenon paths.\n'))
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    hits = [ln for ln in capsys.readouterr().out.splitlines() if 'collides:' in ln]
    assert len(hits) == 3
    assert not any('--force' in h for h in hits)
    assert all(any(w in h for h in hits) for w in ('Zephyr', 'Yankee', 'Xenon'))


def test_defer_on_spec_returns_1_with_receipt(tmp_path, monkeypatch):
    """Stamp SPEC.md --defer returns 1 and writes a refused receipt row.

    Mutation: the inferred-kind guard on the defer path removed, letting
    kind=other read_before=never bypass R1 for a spec with no audit trail.
    Oracle: exit 1; ledger row carries reason starting 'refused:' with
    kind=spec and read_before=always (the prior values) unchanged.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always'])

    ret = hq.main(['stamp', _SLUG, 'SPEC.md', '--defer'])
    assert ret == 1

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    last = lines[-1].split('\t')
    # Ledger field indices: cycle=0 ts=1 path=2 base=3 kind=4
    # status=5 read_before=6 successor=7 where=8 sha12=9
    # lines=10 reason=11 label=12
    assert last[11].startswith('refused:')
    assert '--defer' in last[11]
    assert last[4] == 'spec'
    assert last[6] == 'always'


def test_restamp_carries_label_and_where_forward(tmp_path, monkeypatch):
    """A restamp with no --label or --where carries the previous values.

    Mutation: a re-stamp with no --label blanking the stored label.
    Oracle: ledger second row has label equal to the first row's label.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Render pass\n\nDetails.\n\n## Other\n\nContent.\n')
    hq.main([
        'stamp', _SLUG, 'SPEC.md',
        '--label', 'original label text',
        '--where', 'Render pass',
        ])

    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Render pass\n\nUpdated.\n\n## Other\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    assert len(lines) == 3
    first = lines[1].split('\t')
    second = lines[2].split('\t')
    # Ledger field indices: cycle=0 ts=1 path=2 base=3 kind=4
    # status=5 read_before=6 successor=7 where=8 sha12=9
    # lines=10 reason=11 label=12
    assert second[12] == first[12]
    assert second[8] == first[8]
    assert first[12] == 'original label text'


def test_finish_writes_nothing_on_a_blocking_finding(tmp_path, monkeypatch):
    """R3: a sha-changed always-row blocks finish; no file changes sha.

    Mutation: writes before checks; manifest row appended and files changed
    before R3 fires.
    Oracle: sha256 of every file in the folder is identical before and after
    the blocked finish; manifest has zero data rows.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOriginal content.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always'])

    (folder / 'SPEC.md').write_text('# Spec\n\nModified content.\n')

    def _sha_all(root: str) -> dict:
        result = {}
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                path = os.path.join(dirpath, fname)
                with open(path, 'rb') as fh:
                    result[path] = hashlib.sha256(fh.read()).hexdigest()
        return result

    before = _sha_all(str(folder))
    ret = hq.main(['finish', _SLUG, '--log', 'should be blocked'])
    assert ret == 1
    after = _sha_all(str(folder))
    assert before == after

    manifest = folder / 'cycles' / 'manifest.tsv'
    rows = manifest.read_text().splitlines()[1:]
    assert len(rows) == 0


def test_finish_rerun_appends_one_log_line(tmp_path, monkeypatch):
    """A second finish on the same cycle exits 1 and adds no manifest row.

    Mutation: a re-run duplicating the Log for the same cycle; the lock guard
    that prevents it removed.
    Oracle: after begin+finish+finish, manifest has exactly one row; second
    finish exits 1. After a new begin+finish, manifest has exactly two rows.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'first cycle'])

    # Second finish with no intervening begin:
    # lock is gone, session mismatch.
    ret2 = hq.main(['finish', _SLUG, '--log', 'duplicate'])
    assert ret2 == 1

    rows = (folder / 'cycles' / 'manifest.tsv').read_text().splitlines()[1:]
    assert len(rows) == 1

    monkeypatch.setenv('HQ_CYCLE', '2')
    hq.main(['begin', _SLUG])
    ret3 = hq.main(['finish', _SLUG, '--log', 'second cycle'])
    assert ret3 == 0

    rows2 = (folder / 'cycles' / 'manifest.tsv').read_text().splitlines()[1:]
    assert len(rows2) == 2


def test_begin_twice_advances_nothing(tmp_path, monkeypatch):
    """A second begin (no finish) does not archive or advance the cycle.

    Mutation: begin archiving and bumping when the folder already exists.
    Oracle: cycles/ has no archive after two begins; manifest has zero rows.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['begin', _SLUG])

    cycles_dir = folder / 'cycles'
    archives = [
        f for f in cycles_dir.iterdir()
        if f.name.startswith('c') and f.name.endswith('.md')
        ]
    assert len(archives) == 0

    rows = (cycles_dir / 'manifest.tsv').read_text().splitlines()[1:]
    assert len(rows) == 0


def test_begin_takes_over_a_stale_lock_and_names_it(tmp_path, monkeypatch,
                                                    capsys):
    """Begin takes over a foreign lock older than two hours and names it.

    Mutation: a lock only its taker can release; stale lock blocks forever.
    Oracle: return code 0, stdout names 'other-session', lock now owns
    _SESSION.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    _write_lock(folder, 'other-session', _STALE_LOCK_TIME, cycle=1)

    ret = hq.main(['begin', _SLUG])
    assert ret == 0

    out = capsys.readouterr().out
    assert 'other-session' in out

    lock_text = (folder / '.hq.lock').read_text()
    assert f'session={_SESSION}' in lock_text


def test_begin_refuses_a_young_foreign_lock(tmp_path, monkeypatch):
    """Begin refuses when a foreign lock is younger than two hours.

    Mutation: a lock only its taker can release; age check inverted.
    Oracle: return code 1; lock still carries 'other-session' after the call.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    _write_lock(folder, 'other-session', _YOUNG_LOCK_TIME, cycle=1)

    ret = hq.main(['begin', _SLUG])
    assert ret == 1

    lock_text = (folder / '.hq.lock').read_text()
    assert 'other-session' in lock_text


def test_archive_holds_cycle_n_as_finished(tmp_path, monkeypatch):
    """Finish archives the current cycle in cycles/c<N>.md, not c<N+1>.md.

    Mutation: off-by-one putting cycle 1's text in c02.md.
    Oracle: cycles/c01.md exists and is byte-identical to HANDOFF.md.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'archive test'])

    archive = folder / 'cycles' / 'c01.md'
    assert archive.exists(), 'c01.md not found; off-by-one likely'
    assert not (folder / 'cycles' / 'c02.md').exists()
    assert archive.read_bytes() == (folder / 'HANDOFF.md').read_bytes()


def test_probe_dir_is_one_row(tmp_path, monkeypatch, capsys):
    """A subdirectory produces exactly one probe-dir row in artifacts.

    Mutation: a directory walked file by file, producing N rows instead of one.
    Oracle: artifacts output has exactly one line containing 'experiments'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    exp_dir = folder / 'experiments'
    exp_dir.mkdir()
    for i in range(5):
        (exp_dir / f'run{i:02d}.py').write_text(f'# run {i}\n')

    capsys.readouterr()
    hq.main(['artifacts', _SLUG])
    out = capsys.readouterr().out
    probe_lines = [ln for ln in out.splitlines() if 'experiments' in ln]
    assert len(probe_lines) == 1
    assert 'probe-dir' in probe_lines[0]


def test_conflicted_copy_is_skipped_and_named(tmp_path, monkeypatch, capsys):
    """A file whose name contains 'conflicted copy' is skipped and named.

    Mutation: a conflicted copy walked as a spec due to missing skip rule.
    Oracle: artifacts output contains the filename but not as 'spec'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    conflict_name = 'SPEC (conflicted copy 2026-09-09).md'
    (folder / conflict_name).write_text('# Spec\n\nContent.\n')

    hq.main(['artifacts', _SLUG])
    out = capsys.readouterr().out
    assert conflict_name in out
    # The conflicted copy must not be classified as a spec
    matching = [ln for ln in out.splitlines() if conflict_name in ln]
    assert all('spec' not in ln for ln in matching)


def test_where_resolves_to_the_current_span(tmp_path, monkeypatch):
    """Stamp --where resolves an anchor to a line span
    written into the read block.

    Mutation: anchors matched by line number;
    a missing anchor silently omitted.
    Oracle: '## Render pass' is line 3 of SPEC.md; next h2 at line 8 gives
    span 3-7.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n'
        '\n'
        '## Render pass\n'
        '\n'
        'Details about the render pass ordering.\n'
        'More details on the same topic.\n'
        '\n'
        '## Implementation\n'
        '\n'
        'Other content.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Render pass'])
    hq.main(['finish', _SLUG, '--log', 'where test'])

    handoff_text = (folder / 'HANDOFF.md').read_text()
    assert 'SPEC.md:3-7' in handoff_text


def test_read_block_line_shapes(tmp_path, monkeypatch):
    """Read block pins exact line shapes for the two-anchor and ? span forms.

    Mutation: unresolved anchor silently omitted; two-space column separator
    collapsed to one; label dropped from the line.
    Oracle: SPEC.md stamped with 'Render pass;Ghost Section' - 'Render pass'
    resolves to span 3-7, 39 characters hand-counted so 9 tokens, and
    'Ghost Section' is absent, so the line is 'SPEC.md:3-7,?  (9 tok)  -';
    NOTES.md stamped with a single missing anchor is read whole, 3 lines
    and 15 characters, giving 'NOTES.md:?  (3 lines, 3 tok)  -'.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n'
        '\n'
        '## Render pass\n'
        '\n'
        'Details.\n'
        'More details.\n'
        '\n'
        '## Implementation\n'
        '\n'
        'Other content.\n')
    (folder / 'NOTES.md').write_text('# Notes\n\nBody.\n')
    hq.main([
        'stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
        '--where', 'Render pass;Ghost Section'])
    hq.main([
        'stamp', _SLUG, 'NOTES.md', '--read-before', 'always',
        '--where', 'Missing'])
    hq.main(['finish', _SLUG, '--log', 'shape test'])

    handoff_text = (folder / 'HANDOFF.md').read_text()
    text_lines = handoff_text.splitlines()
    assert f'.handoff/{_SLUG}/SPEC.md:3-7,?  (9 tok)  -' in text_lines
    assert f'.handoff/{_SLUG}/NOTES.md:?  (3 lines, 3 tok)  -' in text_lines


def test_untyped_unfiled_bullet_blocks_and_names_the_line(
        tmp_path, monkeypatch, capsys):
    """Finish refuses an ## Unfiled section that contains an untyped bullet.

    Mutation: the drain guessing a section for an untyped bullet.
    Oracle: finish returns 1 and stdout names the offending bullet text.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    _set_cursor(
        folder / 'HANDOFF.md',
        '## Task\n\nDo the work.\n\n## Unfiled\n\n- some untyped bullet item\n')

    ret = hq.main(['finish', _SLUG, '--log', 'untyped test'])
    assert ret == 1

    out = capsys.readouterr().out
    assert 'some untyped bullet item' in out


def test_typed_unfiled_bullets_reach_their_sections(tmp_path, monkeypatch):
    """Typed bullets under ## Unfiled are drained into their correct sections.

    Mutation: drain swapping decision and constraint sections - items land in
    the wrong kind slot.
    Oracle: standing.md d-prefixed line has 'Use single writes' headline;
    c-prefixed line has 'Max 400 lines' headline (not swapped).
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    _set_cursor(
        folder / 'HANDOFF.md',
        '## Task\n\nDo the work.\n\n## Unfiled\n\n'
        '- decision: **Use single writes** Batch all writes into one call.\n'
        '- constraint: **Max 400 lines** Cursor must stay under 400 lines.\n')

    ret = hq.main(['finish', _SLUG, '--log', 'typed unfiled test'])
    assert ret == 0

    standing_text = (folder / 'standing.md').read_text()
    slines = standing_text.splitlines()
    d_line = next((ln for ln in slines if ln.startswith('- [d')), None)
    c_line = next((ln for ln in slines if ln.startswith('- [c')), None)
    assert d_line is not None
    assert 'Use single writes' in d_line
    assert c_line is not None
    assert 'Max 400 lines' in c_line


def test_abs_path_resolved_once_and_absence_is_advisory(tmp_path, monkeypatch):
    """Stamping an absent absolute path succeeds;
    absence is advisory, not blocking.

    Mutation: re-resolving the path per cwd; a missing outside file blocking
    stamp.
    Oracle: stamp returns 0; ledger row has base=abs and the given path
    unchanged regardless of cwd.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    abs_path = '/nonexistent/path/to/notes.md'

    original_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        ret = hq.main(['stamp', _SLUG, abs_path])
        lines = (folder / 'ledger.tsv').read_text().splitlines()
    finally:
        os.chdir(original_cwd)

    assert ret == 0
    assert len(lines) == 2
    fields = lines[1].split('\t')
    assert fields[2] == abs_path
    assert fields[3] == 'abs'


def test_adopt_of_a_non_conforming_handoff_changes_nothing(tmp_path,
                                                           monkeypatch):
    """Adopt on a non-conforming HANDOFF.md exits 1 and writes nothing.

    Mutation: the whole file drained into ## Unfiled on non-conforming input.
    Oracle: ledger.tsv, standing.md, and cycles/ are
    absent after adopt exits 1.
    """
    root = tmp_path / 'root'
    root.mkdir()
    src = os.path.join(FIXTURES, 'legacy-import-notes')
    dst = str(root / '.handoff' / 'legacy-import-notes')
    shutil.copytree(src, dst)

    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))

    folder = root / '.handoff' / 'legacy-import-notes'
    ret = hq.main(['adopt', 'legacy-import-notes'])
    assert ret == 1
    assert not (folder / 'ledger.tsv').exists()
    assert not (folder / 'standing.md').exists()
    assert not (folder / 'cycles').exists()


def test_adopt_conservation_excludes_the_archive(tmp_path, monkeypatch):
    """Adopt seeds ledger rows, standing items, and a
    byte-identical cycle archive.

    Mutation: the archive copy satisfying the conservation union by itself.
    Oracle: 3 ledger rows; standing has d/c/x items; cycles/c05.md
    byte-identical to original HANDOFF.md; free text from the 'Read now'
    pointer lands in label; 'Reference only' notes row graded edit; a second
    begin+finish exits 0.
    """
    root = tmp_path / 'root'
    root.mkdir()
    src = os.path.join(FIXTURES, 'orbit-cache-rewrite')
    dst = str(root / '.handoff' / 'orbit-cache-rewrite')
    shutil.copytree(src, dst)

    slug = 'orbit-cache-rewrite'
    folder = root / '.handoff' / slug
    original_bytes = (folder / 'HANDOFF.md').read_bytes()
    original_text = (folder / 'HANDOFF.md').read_text()

    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))

    ret = hq.main(['adopt', slug])
    assert ret == 0

    ledger_rows = (folder / 'ledger.tsv').read_text().splitlines()[1:]
    assert len(ledger_rows) == 3

    slines = (folder / 'standing.md').read_text().splitlines()
    assert any(ln.startswith('- [d') for ln in slines)
    assert any(ln.startswith('- [c') for ln in slines)
    assert any(ln.startswith('- [x') for ln in slines)

    assert (folder / 'cycles' / 'c05.md').read_bytes() == original_bytes

    # Step-3: free text from 'Read now' pointer lands in label verbatim.
    parsed_rows = [r.split('\t') for r in ledger_rows]
    spec_rows = [r for r in parsed_rows if r[2] == 'SPEC.md']
    assert spec_rows
    assert 'cache schema and field contracts' in spec_rows[-1][12]

    # 'Reference only' notes file is graded edit, not never.
    notes_rows = [r for r in parsed_rows if 'notes-algos' in r[2]]
    assert notes_rows
    assert notes_rows[-1][6] == 'edit'

    # Step-5: adopted HANDOFF.md carries the three managed blocks and ## Log.
    new_text = (folder / 'HANDOFF.md').read_text()
    assert '<!-- hq:read ' in new_text
    assert '<!-- hq:artifacts ' in new_text
    assert '<!-- hq:standing ' in new_text
    assert '## Log' in new_text

    # Cursor section (before first <!-- hq: marker) contains only the six
    # step-5 headings; drained sections must not be duplicated there.
    hq_marker_idx = new_text.find('<!-- hq:')
    cursor_section = new_text[:hq_marker_idx]
    for drained in ('## Decisions', '## Constraints', '## Dead ends', '## Key files'):
        assert drained not in cursor_section, (
            f'{drained!r} found verbatim in adopted cursor section')

    # Conservation: the adopted cursor plus standing cover the original.
    standing_text = (folder / 'standing.md').read_text()
    pairs = [f'{r[2]} {r[12]}' for r in parsed_rows if len(r) > 12]
    missing = hq.conservation(original_text, cursor_section, standing_text, pairs)
    assert missing == []

    monkeypatch.setenv('HQ_CYCLE', '6')
    ret = hq.main(['begin', slug])
    assert ret == 0
    ret = hq.main(['finish', slug, '--log', 'second cycle'])
    assert ret == 0


def test_open_writes_nothing(tmp_path, monkeypatch):
    """Open is read-only; no file changes sha after the call.

    Mutation: open regenerating blocks and rewriting HANDOFF.md.
    Oracle: sha256 of every file in the folder is identical before and
    after open.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always'])
    hq.main(['finish', _SLUG, '--log', 'setup'])

    def _sha_all(root: str) -> dict:
        result = {}
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                path = os.path.join(dirpath, fname)
                with open(path, 'rb') as f:
                    result[path] = hashlib.sha256(f.read()).hexdigest()
        return result

    before = _sha_all(str(folder))
    hq.main(['open', _SLUG])
    after = _sha_all(str(folder))
    assert before == after


def test_payload_is_flat_across_100_synthesized_cycles(tmp_path, monkeypatch):
    """payload_tokens at cycle 100 is within 2 percent of cycle 3.

    Mutation: any payload term proportional to cycle count (e.g., full log
    history).
    Oracle: manifest.tsv payload_tokens rows 3 and 100; abs diff / row3 <
    0.02. The log block adds one rollup line after cycle 3 (~50 bytes =
    ~12 tokens); with a ~5 kB HANDOFF.md the change is under 1 percent.
    """
    folder = _thread(tmp_path, 100, 2, 20, monkeypatch)

    manifest_text = (folder / 'cycles' / 'manifest.tsv').read_text()
    rows = manifest_text.splitlines()
    header = rows[0].split('\t')
    payload_idx = header.index('payload_tokens')

    tokens_at_3 = int(rows[3].split('\t')[payload_idx])
    tokens_at_100 = int(rows[100].split('\t')[payload_idx])

    pct_change = abs(tokens_at_100 - tokens_at_3) / tokens_at_3
    assert pct_change < 0.02, (
        f'payload grew {pct_change:.2%}: '
        f'cycle3={tokens_at_3}, cycle100={tokens_at_100}')


def test_when_shows_all_rows_for_path(tmp_path, monkeypatch, capsys):
    """When shows every ledger row for a path, oldest first.

    Mutation: when showing only the latest row for the path.
    Oracle: three stamps of SPEC.md produce three rows in when output.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nVersion 1.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--label', 'first stamp'])
    (folder / 'SPEC.md').write_text('# Spec\n\nVersion 2.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'second stamp'])
    (folder / 'SPEC.md').write_text('# Spec\n\nVersion 3.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'third stamp'])

    capsys.readouterr()
    hq.main(['when', _SLUG, 'SPEC.md'])
    out = capsys.readouterr().out
    spec_lines = [ln for ln in out.splitlines() if 'SPEC.md' in ln]
    assert len(spec_lines) == 3


def test_diff_shows_cursor_changes_between_cycles(tmp_path, monkeypatch,
                                                  capsys):
    """Diff summarizes the changed section and expands it on request.

    Mutation: diff reporting cycle offsets as line numbers instead of
    content; or the summary and the expansion swapped.
    Oracle: the summary is the one line '## Task  +1 -1'; 'task' expands
    to 'Line A' removed (- prefix) and 'Line B' added (+ prefix).
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    _set_cursor(folder / 'HANDOFF.md', '## Task\n\nLine A\n')
    hq.main(['finish', _SLUG, '--log', 'cycle one'])

    monkeypatch.setenv('HQ_CYCLE', '2')
    hq.main(['begin', _SLUG])
    _set_cursor(folder / 'HANDOFF.md', '## Task\n\nLine B\n')
    hq.main(['finish', _SLUG, '--log', 'cycle two'])
    capsys.readouterr()

    assert hq.main(['diff', _SLUG, '1', '2']) == 0
    assert capsys.readouterr().out.splitlines() == ['## Task  +1 -1']
    assert hq.main(['diff', _SLUG, '1', '2', 'task']) == 0
    out = capsys.readouterr().out
    assert any(ln.startswith('- ') and 'Line A' in ln for ln in out.splitlines())
    assert any(ln.startswith('+ ') and 'Line B' in ln for ln in out.splitlines())


def test_artifacts_verb_shows_every_live_row(tmp_path, monkeypatch, capsys):
    """Artifacts lists every live stamped row with its kind and read_before.

    Mutation: artifacts built from the previous block's text instead of a fresh
    walk.
    Oracle: output lines contain SPEC.md with 'always' and notes-impl.md with
    'edit'.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    (folder / 'notes-impl.md').write_text('# Notes\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always'])
    hq.main(['stamp', _SLUG, 'notes-impl.md', '--read-before', 'edit'])

    ret = hq.main(['artifacts', _SLUG])
    assert ret == 0

    out_lines = capsys.readouterr().out.splitlines()
    assert any('SPEC.md' in ln and 'always' in ln for ln in out_lines)
    assert any('notes-impl.md' in ln and 'edit' in ln for ln in out_lines)


def test_standing_verb_shows_unsuperseded_items(tmp_path, monkeypatch, capsys):
    """Standing omits superseded items and shows only live ones.

    Mutation: standing block including superseded items.
    Oracle: d01's headline absent from output; d02's headline present.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision',
             '--headline', 'Original approach', 'First decision body.'])
    hq.main(['note', _SLUG, 'decision',
             '--headline', 'Revised approach', 'Second decision body.'])
    hq.main(['supersede', _SLUG, 'd01', 'd02'])
    capsys.readouterr()

    ret = hq.main(['standing', _SLUG])
    assert ret == 0

    out = capsys.readouterr().out
    assert 'Revised approach' in out
    assert 'Original approach' not in out


def test_read_verb_appends_receipt(tmp_path, monkeypatch, capsys):
    """Read appends one receipt line to HQ_STATE_DIR/hq-reads-<session>.txt.

    Mutation: receipt file not created; receipt entry appended to wrong
    path; or the receipt written in place of the span, so the caller gets
    the record and not the content.
    Oracle: receipt file exists and its last line contains the slug and
    path; the first printed line is the anchor's own heading.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Design\n\nContent.\n\n## Other\n\nMore.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Design'])
    hq.main(['finish', _SLUG, '--log', 'read test'])

    capsys.readouterr()
    ret = hq.main(['read', _SLUG, 'SPEC.md'])
    assert ret == 0
    assert capsys.readouterr().out.splitlines()[0] == '## Design'

    receipt_path = tmp_path / f'hq-reads-{_SESSION}.txt'
    assert receipt_path.exists()
    last_line = receipt_path.read_text().splitlines()[-1]
    assert _SLUG in last_line
    assert 'SPEC.md' in last_line


# --- open-question rulings, 2026-09-09 -----------------------------------


def test_dangling_successor_is_an_advisory_in_finish_and_the_work_list(
        tmp_path, monkeypatch, capsys):
    """A superseded row whose successor left the disk is named, not silent.

    Mutation: finish and begin checking only live gated rows for presence,
    so a spec superseded by a file since deleted sits ungated with no line
    naming it.
    Oracle: after NEXT.md is deleted, finish exits 0 and prints
    'advisory: successor missing: SPEC.md -> NEXT.md'; the next begin
    prints 'successor missing: SPEC.md -> NEXT.md' in its work list.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nOld.\n')
    (folder / 'NEXT.md').write_text('# Spec\n\nNew.\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'NEXT.md']) == 0
    (folder / 'NEXT.md').unlink()
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'dangling']) == 0
    assert 'advisory: successor missing: SPEC.md -> NEXT.md' in capsys.readouterr().out

    hq.main(['begin', _SLUG])
    assert 'successor missing: SPEC.md -> NEXT.md' in capsys.readouterr().out


def test_r1_holds_when_the_stored_kind_is_spec_and_the_heading_moved(
        tmp_path, monkeypatch):
    """R1 binds to the stored kind as well as the inferred one.

    Mutation: R1 keyed on the inferred kind alone, so editing a spec's first
    heading away from '# Spec' lets --read-before never through.
    Oracle: after the heading edit, 'stamp plan.md --read-before never'
    exits 1 and the current row stays kind spec, live, always.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'plan.md').write_text('# Spec\n\nContent.\n')
    assert hq.main(['stamp', _SLUG, 'plan.md', '--where', 'Spec']) == 0
    (folder / 'plan.md').write_text('# Plan\n\nContent.\n')

    assert hq.main(['stamp', _SLUG, 'plan.md', '--read-before', 'never']) == 1

    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['kind'] == 'spec'
    assert row['status'] == 'live'
    assert row['read_before'] == 'always'
    assert row['reason'].startswith('refused:')


def test_archive_without_reason_is_usage_and_writes_no_row(tmp_path, monkeypatch):
    """--archive with no --reason is a usage error, not a refusal.

    Mutation: exit 1, the refusal code, for a malformed command; or a
    receipt row appended for it.
    Oracle: section 16's exit table, where 2 is usage; the ledger bytes
    are unchanged.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec']) == 0
    ledger_before = (folder / 'ledger.tsv').read_bytes()

    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--archive']) == 2
    assert (folder / 'ledger.tsv').read_bytes() == ledger_before


def test_successor_whose_own_row_is_not_live_is_refused(tmp_path, monkeypatch):
    """Two specs may not name each other as successors.

    Mutation: a successor accepted on disk presence alone, so A -> B then
    B -> A leaves no live spec and nothing gated.
    Oracle: the second stamp exits 1 with a receipt; B's current row stays
    live, always, successor '-'; A still points at B.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC-A.md').write_text('# Spec\n\nA.\n')
    (folder / 'SPEC-B.md').write_text('# Spec\n\nB.\n')
    assert hq.main(['stamp', _SLUG, 'SPEC-B.md', '--where', 'Spec']) == 0
    assert hq.main(['stamp', _SLUG, 'SPEC-A.md', '--successor', 'SPEC-B.md']) == 0

    assert hq.main(['stamp', _SLUG, 'SPEC-B.md', '--successor', 'SPEC-A.md']) == 1

    rows = [
        dict(zip(hq.LEDGER_FIELDS, ln.split('\t')))
        for ln in (folder / 'ledger.tsv').read_text().splitlines()[1:]
        ]
    latest = hq.latest_rows(rows)
    assert latest['SPEC-B.md']['status'] == 'live'
    assert latest['SPEC-B.md']['read_before'] == 'always'
    assert latest['SPEC-B.md']['successor'] == '-'
    assert latest['SPEC-B.md']['reason'].startswith('refused:')
    assert latest['SPEC-A.md']['successor'] == 'SPEC-B.md'


def test_a_kind_declared_spec_is_gated_from_that_stamp_on(tmp_path, monkeypatch):
    """--kind spec on a notes-named file seeds always and binds R1.

    Mutation: the gate kind taken from the inferred and previous kinds
    only, so 'stamp notes-a.md --kind spec' writes a spec row at never
    and the same stamp with --read-before never passes.
    Oracle: the first stamp's row is spec/live/always; the demoting stamp
    exits 1 and the current row stays always.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# Notes\n\nActually the spec.\n')

    assert hq.main(['stamp', _SLUG, 'notes-a.md', '--kind', 'spec', '--where', 'Notes']) == 0
    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert (row['kind'], row['status'], row['read_before']) == ('spec', 'live', 'always')

    assert hq.main([
        'stamp', _SLUG, 'notes-a.md', '--kind', 'spec', '--read-before', 'never']) == 1
    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert row['read_before'] == 'always'
    assert row['reason'].startswith('refused:')


# --- mutation survivors, 2026-09-09 ---------------------------------------


def test_work_list_caps_names_and_judges_presence_on_live_rows_only(
        tmp_path, monkeypatch, capsys):
    """Seven unstamped files print five names and '... and 2 more', the
    conflicted copy is not among them, and an archived row whose file is
    gone is not 'missing live'.

    Mutation: the cap moved to six, the tail arithmetic off by one, the
    skip kind counted as unstamped, or non-live rows checked for presence.
    Oracle: hand-computed counts for seven notes files; no 'missing live'
    line for the archived spec.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    for i in range(7):
        (folder / f'notes-{i}.md').write_text('# n\n')
    (folder / 'notes-x (conflicted copy 2026).md').write_text('# n\n')
    (folder / 'OLD.md').write_text('# Spec\n')
    assert hq.main(['stamp', _SLUG, 'OLD.md', '--archive', '--reason', 'old']) == 0
    (folder / 'OLD.md').unlink()
    capsys.readouterr()

    hq.main(['begin', _SLUG])
    out = capsys.readouterr().out

    line = next(ln for ln in out.splitlines() if ln.startswith('  unstamped notes x7: '))
    names, tail = line[len('  unstamped notes x7: '):].split(' ... ')
    assert len(names.split(', ')) == 5
    assert tail == 'and 2 more - stamp each'
    assert 'conflicted copy' not in names
    assert '  conflicted copy: notes-x (conflicted copy 2026).md' in out
    assert 'missing live' not in out


def test_acknowledge_passes_a_witness_break_and_records_the_reason(
        tmp_path, monkeypatch):
    """Finish --acknowledge writes through a W1 break and notes the reason.

    Mutation: the acknowledge test inverted, so an acknowledged break
    blocks and an unacknowledged one passes.
    Oracle: without the flag exit 1; with it exit 0 and the manifest note
    'acknowledged: formatter'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'one']) == 0
    hq.main(['begin', _SLUG])
    ledger = folder / 'ledger.tsv'
    ledger.write_bytes(ledger.read_bytes().replace(b'always', b'alwayz', 1))

    assert hq.main(['finish', _SLUG, '--log', 'two']) == 1
    assert hq.main(['finish', _SLUG, '--log', 'two', '--acknowledge', 'formatter']) == 0

    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    note = rows[-1]['note']
    assert note.startswith('acknowledged W1: ledger prefix changed')
    assert note.endswith('; formatter')


def test_collisions_skip_a_superseded_spec_and_a_present_abs_row_is_silent(
        tmp_path, monkeypatch, capsys):
    """A superseded spec's headings do not collide, and an abs row whose
    file exists draws no advisory.

    Mutation: the live-spec test loosened to kind or status, or the abs
    advisory keyed on base alone.
    Oracle: 'Delta' heads only the superseded OLD-SPEC.md while the Now
    step names Delta, and no collides line prints; the abs file exists,
    and no 'abs path not on disk' line prints.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'OLD-SPEC.md').write_text('# Spec\n\n## Delta section\n')
    (folder / 'SPEC.md').write_text('# Spec\n\n## Plain section\n')
    assert hq.main(['stamp', _SLUG, 'OLD-SPEC.md', '--successor', 'SPEC.md']) == 0
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec']) == 0
    outside = tmp_path / 'outside-note.md'
    outside.write_text('# n\n')
    assert hq.main(['stamp', _SLUG, str(outside)]) == 0
    hp = folder / 'HANDOFF.md'
    hp.write_text(hp.read_text().replace('## Now\n', '## Now\nWire Delta into the run.\n'))
    capsys.readouterr()

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    out = capsys.readouterr().out
    assert 'collides:' not in out
    assert 'abs path not on disk' not in out


def test_label_dropping_a_backticked_token_alone_is_advised(
        tmp_path, monkeypatch, capsys):
    """A same-length re-label that loses a backticked token, but no s<n>
    reference, draws the dropped advisory naming the token at the stamp.

    Mutation: the two dropped sets intersected instead of united, so a
    backtick-only loss is silent; or the advisory left in finish, where
    the render is already written and retyping the label costs a cycle.
    Oracle: the labels differ only by the token `--force`, and the
    finish that follows the re-stamp is silent.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--where', 'Spec',
        '--label', 'the `--force` path, s4 spans it']) == 0
    capsys.readouterr()

    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--label', 'the force path now, s4 spans it']) == 0

    assert "advisory: label dropped {'`--force`'}: SPEC.md" in capsys.readouterr().out
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    assert 'label dropped' not in capsys.readouterr().out


def test_label_losing_only_its_backticks_is_silent_on_both_counts(
        tmp_path, monkeypatch, capsys):
    """A re-label that spells an identifier without its backticks draws
    neither advisory: the label lost markup, not content.

    Mutation: the dropped set taken as the backticked spans of the old
    label minus those of the new, so un-backticking reads as an
    omission; or either length measured on the raw label, where two
    stripped backticks read as a shortening. Each leaves the agent
    unable to tell a formatting change from a real loss.
    Oracle: the two labels carry the same eighteen characters of text
    and differ only by the pair of backticks.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n')
    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--where', 'Spec',
        '--label', 'the `FINX_SURFACE` split']) == 0
    capsys.readouterr()

    assert hq.main([
        'stamp', _SLUG, 'SPEC.md', '--label', 'the FINX_SURFACE split']) == 0

    out = capsys.readouterr().out
    assert 'label dropped' not in out, out
    assert 'label shorter' not in out, out


def test_label_keeping_a_token_only_inside_a_longer_word_is_a_loss(
        tmp_path, monkeypatch, capsys):
    """An identifier that survives only as a fragment of another word is
    reported dropped.

    Mutation: the kept test written as plain substring containment, so
    `env` buried in 'environment' counts as carried and a real omission
    goes unreported while the label grows.
    Oracle: the new label is longer, so the shortening test cannot fire;
    the only signal left is the token, and 'environment' is a different
    word from the identifier `env`.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n')
    assert hq.main([
        'stamp', _SLUG, 'doc.md', '--read-before', 'mention',
        '--label', 'the `env` guard']) == 0
    capsys.readouterr()

    assert hq.main([
        'stamp', _SLUG, 'doc.md', '--read-before', 'mention',
        '--label', 'the environment guard rewritten at length']) == 0

    out = capsys.readouterr().out
    assert "advisory: label dropped {'`env`'}: doc.md" in out, out
    assert 'label shorter' not in out, out


def test_a_shorter_label_that_also_drops_a_token_prints_both_lines(
        tmp_path, monkeypatch, capsys):
    """A re-label that both shortens and loses a token names the token.

    Mutation: the shortening advisory returning before the token test,
    so the common shape of a real loss - a label cut down, a token gone
    with it - reports only that the label got shorter and never says
    which identifier the reader lost.
    Oracle: 40 characters of text down to 27, with `--force` gone and
    `FINX_SURFACE` still spelled.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n')
    assert hq.main([
        'stamp', _SLUG, 'doc.md', '--read-before', 'mention',
        '--label', 'the `--force` and `FINX_SURFACE` path']) == 0
    capsys.readouterr()

    assert hq.main([
        'stamp', _SLUG, 'doc.md', '--read-before', 'mention',
        '--label', 'the FINX_SURFACE path']) == 0

    out = capsys.readouterr().out
    assert 'advisory: label shorter than predecessor: doc.md' in out, out
    assert "advisory: label dropped {'`--force`'}: doc.md" in out, out


def test_two_unfiled_items_of_one_kind_get_consecutive_ids(tmp_path, monkeypatch):
    """Draining two decisions in one finish numbers them d01 and d02.

    Mutation: the running standing text replaced per item instead of
    extended, so both items take d01.
    Oracle: hand-computed ids in file order.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    _set_cursor(
        folder / 'HANDOFF.md',
        '## Task\n\nDo.\n\n## Unfiled\n'
        '- decision: **First** one.\n'
        '- decision: **Second** two.\n')

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    lines = (folder / 'standing.md').read_text().splitlines()
    assert lines == ['- [d01] (c1) **First** one.', '- [d02] (c1) **Second** two.']


def test_first_finish_keeps_one_log_heading(tmp_path, monkeypatch):
    """The cursor of a fresh file stops before the Log that begin wrote.

    Mutation: the Log heading match broken, so the cursor swallows the
    begin-written Log and finish writes a second one.
    Oracle: exactly one '## Log' line after the first finish.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])

    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0

    text = (folder / 'HANDOFF.md').read_text()
    assert text.count('\n## Log\n') == 1


def test_a_kind_declared_draft_is_gated_like_a_spec(tmp_path, monkeypatch):
    """--kind draft on an unrecognized file seeds always and binds R1.

    Mutation: the draft half of the gate set dropped, so a declared draft
    lands at never and demotes freely.
    Oracle: the first stamp's row is draft/live/always; the demoting
    stamp exits 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'plan.txt').write_text('def run(): pass\n')

    assert hq.main(['stamp', _SLUG, 'plan.txt', '--kind', 'draft']) == 0
    lines = (folder / 'ledger.tsv').read_text().splitlines()
    row = dict(zip(hq.LEDGER_FIELDS, lines[-1].split('\t')))
    assert (row['kind'], row['status'], row['read_before']) == ('draft', 'live', 'edit')
    assert hq.main(['stamp', _SLUG, 'plan.txt', '--read-before', 'never']) == 1


def test_help_with_no_topic_lists_every_key(capsys):
    """Hq help with no topic prints one line per HELP_TOPICS key, exit 0.

    Mutation: a topic added to HELP_TOPICS but dropped from the listing
    loop, so the agent cannot discover it by running help alone.
    Oracle: the dict's keys, in insertion order, one per output line.
    """
    rc = hq.main(['help'])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert rc == 0
    assert len(lines) == len(hq.HELP_TOPICS)
    for line, key in zip(lines, hq.HELP_TOPICS):
        assert line.startswith(f'hq help {key}: ')


def test_help_with_known_topic_prints_body_and_with_unknown_exits_2(capsys):
    """Hq help anchors prints HELP_TOPICS['anchors'] verbatim, exit 0;
    hq help nope exits 2 and names the four topics.

    Mutation: the unknown-topic branch returning 0, or the topic body
    printed through a formatter that re-wraps it.
    Oracle: the constant itself for the known case; exit 2 and the
    topic list for the unknown case.
    """
    rc = hq.main(['help', 'anchors'])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.rstrip('\n') == hq.HELP_TOPICS['anchors']

    rc = hq.main(['help', 'nope'])
    out = capsys.readouterr().out
    assert rc == 2
    assert 'anchors' in out
    assert 'kinds' in out
    assert 'rules' in out
    assert 'stale-path' in out


# --- the read tiers ---------------------------------------------------


def test_an_always_row_with_no_anchor_is_refused_with_its_headings(
        tmp_path, monkeypatch, capsys):
    """A stamp leaving a live always row unanchored is refused and lists headings.

    Mutation: the anchor check dropped, so a whole spec lands at always
    and is read whole at every resume; the headings not printed, so the
    agent cannot pick a --where; or the receipt row skipped, so the
    attempt leaves no record.
    Oracle: hand-written refusal line and the file's three headings,
    each on its own indented line; one receipt row with the reason and
    the seed tier; the same stamp with --where s1 exits 0.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## 1. Scope\n\nOne.\n\n## 2. Retry\n')
    capsys.readouterr()
    assert hq.main(['stamp', _SLUG, 'SPEC.md']) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        ('hq stamp: refused: SPEC.md always with no anchor - 7 lines read whole at'
         ' every resume; stamp --where <heading> or --read-before edit'),
        '  # Spec',
        '  ## 1. Scope',
        '  ## 2. Retry',
        ]
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    receipt = dict(zip(hq.LEDGER_FIELDS, rows[-1].split('\t')))
    assert (receipt['reason'], receipt['read_before'], receipt['where']) == (
        'refused: SPEC.md always with no anchor', 'always', '-')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 's1']) == 0
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    assert dict(zip(hq.LEDGER_FIELDS, rows[-1].split('\t')))['where'] == 's1'


def test_a_draft_keeps_edit_or_always_and_steps_down_from_an_old_always(
        tmp_path, monkeypatch, capsys):
    """R1 holds a draft at edit or always; an older always draft may re-stamp edit.

    Mutation: the draft floor kept at always alone, so the step-down an
    older thread needs is refused; or the floor dropped to any tier, so
    a draft lands at mention and leaves the gate.
    Oracle: drafts/x.py seeds edit; --read-before mention exits 1 with
    the documented draft line; a seeded always row on poller.py
    re-stamped --read-before edit exits 0 and reads edit.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'drafts').mkdir()
    (folder / 'drafts' / 'x.py').write_text('x = 1\n')
    assert hq.main(['stamp', _SLUG, 'drafts/x.py']) == 0
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    assert dict(zip(hq.LEDGER_FIELDS, rows[-1].split('\t')))['read_before'] == 'edit'
    capsys.readouterr()
    assert hq.main(['stamp', _SLUG, 'drafts/x.py', '--read-before', 'mention']) == 1
    assert capsys.readouterr().out.startswith(
        'hq stamp: refused: R1: draft read_before must stay edit or always'
        ' without --successor or --archive')
    (folder / 'poller.py').write_text('p = 1\n')
    hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS, {
        'cycle': '1', 'ts': _NOW, 'path': 'poller.py', 'base': 'folder',
        'kind': 'draft', 'status': 'live', 'read_before': 'always',
        'successor': '-', 'where': '-', 'sha12': hq._sha12_path(folder / 'poller.py'),
        'lines': '1', 'reason': '-', 'label': 'old draft',
        }, '\t'.join(hq.LEDGER_FIELDS))
    assert hq.main(['stamp', _SLUG, 'poller.py', '--read-before', 'edit']) == 0
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    assert dict(zip(hq.LEDGER_FIELDS, rows[-1].split('\t')))['read_before'] == 'edit'


def test_begin_lists_an_unanchored_always_row_from_an_older_cycle(
        tmp_path, monkeypatch, capsys):
    """The work list names each live always row with no anchor and its move.

    Mutation: the tier-break scan dropped, so an older thread never
    hears which whole files it reads at every resume; or keyed on
    where != '-', so the anchored row is named and the unanchored one
    is not.
    Oracle: two seeded always rows, one anchored; the work list carries
    the hand-written line for notes-x.md alone.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes-x.md').write_text('# X\n\nOne.\n')
    (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
    for path, where in (('notes-x.md', '-'), ('SPEC.md', 'Spec')):
        hq._append_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS, {
            'cycle': '1', 'ts': _NOW, 'path': path, 'base': 'folder',
            'kind': 'other' if where == '-' else 'spec', 'status': 'live',
            'read_before': 'always', 'successor': '-', 'where': where,
            'sha12': hq._sha12_path(folder / path), 'lines': '3',
            'reason': '-', 'label': '-',
            }, '\t'.join(hq.LEDGER_FIELDS))
    hq.main(['finish', _SLUG, '--log', 'c1'])
    monkeypatch.setenv('HQ_CYCLE', '2')
    capsys.readouterr()
    assert hq.main(['begin', _SLUG]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert ('  notes-x.md always with no anchor, 3 lines'
            ' - stamp --where <heading> or --read-before edit') in lines
    assert not any('SPEC.md always with no anchor' in ln for ln in lines)


def test_blocks_print_a_root_path_relative_to_its_base_with_sizes(
        tmp_path, monkeypatch):
    """A repo file prints relative to the root, the base named once, sized.

    Mutation: the stored absolute path printed as is; the base line
    dropped or repeated per row; or the span size dropped from the
    read line.
    Oracle: hand-computed - the anchored span is 13 characters, 3
    tokens; the root is under tmp_path, so its base prints absolutely;
    the Read first and Artifacts blocks each open with 'root <root>'
    once and name 'src/app.md'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    root = folder.parent.parent
    hq.main(['begin', _SLUG])
    (root / 'src').mkdir()
    (root / 'src' / 'app.md').write_text('# App\n\n## Part\n\nbody\n')
    assert hq.main([
        'stamp', _SLUG, str(root / 'src' / 'app.md'), '--kind', 'spec',
        '--where', 'Part']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    blocks = hq.split_handoff((folder / 'HANDOFF.md').read_text())['blocks']
    assert blocks['read'].splitlines() == [
        '## Read first',
        f'root {root}',
        'src/app.md:3-5  (3 tok)  -',
        ]
    art = blocks['artifacts'].splitlines()
    assert art[:2] == ['## Artifacts', f'root {root}']
    assert 'src/app.md  spec  always  c1  -' in art
    assert art.count(f'root {root}') == 1


def test_standing_by_id_prints_the_body_and_the_superseded_marker(
        tmp_path, monkeypatch, capsys):
    """Hq standing <slug> <id> prints items in full; an unknown id exits 1.

    Mutation: the successor read from the wrong side of the arrow, so
    the marker names the old id; the marker dropped for a superseded
    item; or an unknown id passing silently with exit 0.
    Oracle: two decisions with d01 superseded by d02 in cycle 1; the
    hand-written lines for d01 and d02 and the unknown-id line.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'Old way', 'Sidecar.'])
    hq.main(['note', _SLUG, 'decision', '--headline', 'New way', 'In process.'])
    hq.main(['supersede', _SLUG, 'd01', 'd02'])
    capsys.readouterr()
    assert hq.main(['standing', _SLUG, 'd01']) == 0
    assert capsys.readouterr().out.splitlines() == [
        '[d01] (c1) **Old way** Sidecar. [superseded by d02 in c1]']
    assert hq.main(['standing', _SLUG, 'd02', 'zz9']) == 1
    assert capsys.readouterr().out.splitlines() == [
        f'hq standing: zz9 not in standing.md - hq standing {_SLUG} lists the ids',
        '[d02] (c1) **New way** In process.',
        ]


# --- diff and addressing ----------------------------------------------


def test_diff_counts_a_moved_line_in_both_sections_and_names_the_forms(
        tmp_path, monkeypatch, capsys):
    """A bullet moved between sections shows as -1 and +1, by any cycle spelling.

    Mutation: a set difference of cursor lines, so the move cancels and
    prints nothing while the help text says empty means identical; the
    section match case-sensitive, so 'now' finds nothing; a cycle
    spelling refused; or an unknown section exiting 0.
    Oracle: hand-written summary lines for a cursor whose only change is
    one bullet moved from Now to State; the same lines for '1 2', 'c1
    c2', and 'c01 c02'; the expansion of 'now'; the unknown-section line
    with exit 1; a non-number exiting 2.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    _set_cursor(folder / 'HANDOFF.md', '## Now\n- bullet\n## State\n')
    hq.main(['finish', _SLUG, '--log', 'one'])
    monkeypatch.setenv('HQ_CYCLE', '2')
    hq.main(['begin', _SLUG])
    _set_cursor(folder / 'HANDOFF.md', '## Now\n## State\n- bullet\n')
    hq.main(['finish', _SLUG, '--log', 'two'])
    capsys.readouterr()
    for first, second in (('1', '2'), ('c1', 'c2'), ('c01', 'c02')):
        assert hq.main(['diff', _SLUG, first, second]) == 0, (first, second)
        assert capsys.readouterr().out.splitlines() == ['## Now  -1', '## State  +1']
    assert hq.main(['diff', _SLUG, '1', '2', 'now']) == 0
    assert capsys.readouterr().out.splitlines() == ['## Now  -1', '- - bullet']
    assert hq.main(['diff', _SLUG, '1', '2', 'bogus']) == 1
    assert capsys.readouterr().out.strip() == (
        'hq diff: no section bogus in c1 or c2 - sections: Now, State')
    assert hq.main(['diff', _SLUG, 'x', '2']) == 2


def test_when_and_read_resolve_a_token_against_the_root_and_the_pin(
        tmp_path, monkeypatch, capsys):
    """A relative token the folder lacks is tried against the root, then the pin.

    Mutation: the resolver joining a relative token onto the folder
    alone, so 'src/app.py' and 'run.py' print nothing and exit 0; or a
    miss exiting 0 in silence.
    Oracle: two drafts stamped by their absolute paths under the root
    and under the pinned directory; each found by the relative token,
    'hq read' printing the pinned file's one line, and a token with no
    row printing the hand-written miss line with exit 1.
    """
    folder = _new_root(tmp_path, monkeypatch, cycle='1')
    root = folder.parent.parent
    (root / 'src').mkdir()
    (root / 'src' / 'app.py').write_text('a = 1\n')
    (root / 'experiments').mkdir()
    (root / 'experiments' / 'run.py').write_text('r = 1\n')
    hq.main(['begin', _SLUG])
    assert hq.main(['work-dir', _SLUG, 'experiments']) == 0
    for path in (root / 'src' / 'app.py', root / 'experiments' / 'run.py'):
        assert hq.main(['stamp', _SLUG, str(path), '--kind', 'draft']) == 0
    capsys.readouterr()
    assert hq.main(['when', _SLUG, 'src/app.py']) == 0
    assert capsys.readouterr().out.startswith(f'{root}/src/app.py\t1\tlive\tedit')
    assert hq.main(['when', _SLUG, 'run.py']) == 0
    assert capsys.readouterr().out.startswith(f'{root}/experiments/run.py\t1\tlive\tedit')
    assert hq.main(['read', _SLUG, 'run.py']) == 0
    assert capsys.readouterr().out == 'r = 1\n'
    assert hq.main(['when', _SLUG, 'nothere.md']) == 1
    assert capsys.readouterr().out.strip() == (
        f'hq when: nothere.md not in ledger - hq artifacts {_SLUG} lists the rows')
