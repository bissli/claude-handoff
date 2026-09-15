"""Tests pinning the fixes of the 2026-09-10 settlement round.

Each test names a defect the merged-diff review reproduced after three
seats' fixes were merged: a seam between two seats' changes, or a rule
one path applied and another skipped.
"""

import contextlib
import io
import os
import pathlib
import stat

from scripts import hq

_SLUG = 'settle'
_NOW = '2026-09-10T12:00:00'


def _root(tmp_path, monkeypatch, cycle='1'):
    """Create an HQ_ROOT with the HQ_* environment set; return the folder."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', 'sess')
    monkeypatch.setenv('HQ_HOST', 'host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / _SLUG


def _run(argv):
    """Call hq.main, returning (rc, stdout) with SystemExit caught."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue()


def _cursor(folder, extra):
    """Rewrite the cursor above the first block marker, appending extra."""
    text = (folder / 'HANDOFF.md').read_text()
    cut = text.find('<!-- hq:')
    if cut < 0:
        cut = text.find('\n## Log')
    head, tail = (text, '') if cut < 0 else (text[:cut], text[cut:])
    (folder / 'HANDOFF.md').write_text(
        head.rstrip('\n') + '\n\n' + extra + '\n' + tail)


def test_a_marker_mentioned_in_prose_does_not_refuse_unfiled(
        tmp_path, monkeypatch):
    """`<!-- hq:` inside the Task text is prose, not a block marker.

    Mutation: the below-marker guard searching for the raw marker prefix,
    so a cursor that documents the markers can never carry ## Unfiled.
    Oracle: finish exits 0 and the bullet reaches standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _cursor(folder, 'Document the `<!-- hq:` block markers.\n\n## Unfiled\n'
            '- decision: **Keep them** as they are.')
    rc, out = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0, out
    assert '**Keep them**' in (folder / 'standing.md').read_text()


def test_an_unfiled_bullet_with_an_empty_headline_is_refused(
        tmp_path, monkeypatch):
    """A `**  **` bullet is refused by finish as note refuses it.

    Mutation: the drain writing the item through _note_line with no headline
    check, so `**** body` lands in standing.md invisible to every reader and
    the next note reuses its id.
    Oracle: exit 1, the documented line, standing.md unchanged.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    before = (folder / 'standing.md').read_bytes()
    _cursor(folder, '## Unfiled\n- decision: **  ** the body survives')
    rc, out = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 1
    assert 'hq finish: Unfiled bullet has no headline:' in out
    assert (folder / 'standing.md').read_bytes() == before


def test_a_standing_file_missing_its_last_newline_takes_a_note_cleanly(
        tmp_path, monkeypatch):
    """A lost trailing newline in standing.md does not glue the next item.

    Mutation: the newline guard applied to ledger.tsv only, so a note
    appended after a truncated standing.md joins the last line and W2
    then fails a pure append.
    Oracle: two physical items after the note, and finish passes W2.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['note', _SLUG, 'decision', '--headline', 'First', 'one'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    standing = folder / 'standing.md'
    standing.write_bytes(standing.read_bytes()[:-1])
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['note', _SLUG, 'decision', '--headline', 'Second', 'two'])[0] == 0
    items = [ln for ln in standing.read_text().splitlines() if ln.startswith('- [')]
    assert len(items) == 2
    rc, out = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0, out


def test_status_archived_without_a_reason_is_a_usage_error(
        tmp_path, monkeypatch):
    """`--status archived` demands `--reason` exactly as `--archive` does.

    Mutation: the reason check keyed on the --archive flag alone, so the
    status spelling writes an unexplained archive of a non-gated row.
    Oracle: exit 2, the documented line, ledger unchanged; with a reason
    the row is archived and never.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'notes-x.md').write_text('# Notes\n\nx\n')
    assert _run(['stamp', _SLUG, 'notes-x.md'])[0] == 0
    before = (folder / 'ledger.tsv').read_bytes()
    rc, out = _run(['stamp', _SLUG, 'notes-x.md', '--status', 'archived'])
    assert rc == 2
    assert 'hq stamp: --status archived requires --reason' in out
    assert (folder / 'ledger.tsv').read_bytes() == before
    assert _run(['stamp', _SLUG, 'notes-x.md', '--status', 'archived',
                 '--reason', 'done'])[0] == 0
    rows = hq.latest_rows(hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS))
    row = rows['notes-x.md']
    assert (row['status'], row['read_before']) == ('archived', 'never')


def test_adopt_and_stamp_count_a_directory_the_same_way(
        tmp_path, monkeypatch):
    """A probe directory's `lines` is its regular non-dot file count on both paths.

    Mutation: adopt counting every directory entry while stamp counts
    regular files, so `when` shows the count change with nothing on disk.
    Oracle: two files, one dotfile, one subdirectory -> 2 from adopt and 2
    from the re-stamp.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'probes').mkdir()
    (folder / 'probes' / 'a.py').write_text('a\n')
    (folder / 'probes' / 'b.py').write_text('b\n')
    (folder / 'probes' / '.hidden').write_text('h\n')
    (folder / 'probes' / 'sub').mkdir()
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n'
        '## Task\nT.\n\n## Now\nN.\n')
    assert _run(['adopt', _SLUG])[0] == 0
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    assert [r['lines'] for r in rows if r['path'] == 'probes'] == ['2']
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'probes', '--label', 'probes'])[0] == 0
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    assert [r['lines'] for r in rows if r['path'] == 'probes'] == ['2', '2']


def test_a_carriage_return_in_a_path_token_is_refused(tmp_path, monkeypatch):
    r"""A path holding `\\r` is refused like one holding a tab or newline.

    Mutation: the guard testing tab and newline only, so a CR name is
    scrubbed to a space in the ledger and can never be found again.
    Oracle: exit 2, the documented line, nothing written.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    before = (folder / 'ledger.tsv').read_bytes()
    rc, out = _run(['stamp', _SLUG, 'a\rb.md', '--label', 'cr'])
    assert rc == 2
    assert 'hq stamp: path may not contain a tab or a line break' in out
    assert (folder / 'ledger.tsv').read_bytes() == before


def test_a_dash_inside_a_note_body_is_kept(tmp_path, monkeypatch):
    """`the range 1 - 5 holds` keeps its dash in the direct and batch forms.

    Mutation: the leftover rule dropping every bare `-` token, batch marker
    or not, so a body loses its dashes.
    Oracle: the standing lines carry `1 - 5`.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['note', _SLUG, 'decision', '--headline', 'Range',
                 'the', 'range', '1', '-', '5', 'holds'])[0] == 0
    monkeypatch.setattr(
        'sys.stdin',
        io.StringIO('decision --headline "Batch" the range 1 - 5 holds\n'))
    assert _run(['note', _SLUG, '--batch', '-'])[0] == 0
    text = (folder / 'standing.md').read_text()
    assert text.count('the range 1 - 5 holds') == 2


def test_a_fifo_is_named_unstampable_by_begin_and_finish(
        tmp_path, monkeypatch):
    """A non-regular entry is named by the work list, not only by artifacts.

    Mutation: begin and finish naming only tab or newline names, so a FIFO
    appears in `hq artifacts` and nowhere else.
    Oracle: the `unstampable name:` line from begin and the advisory from
    finish, both naming the FIFO.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    os.mkfifo(str(folder / 'pipe.md'))
    assert stat.S_ISFIFO(os.stat(str(folder / 'pipe.md')).st_mode)
    rc, out = _run(['begin', _SLUG])
    assert rc == 0
    assert 'unstampable name: pipe.md' in out
    rc, out = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    assert 'advisory: unstampable name: pipe.md' in out


def test_a_bad_cycle_on_begin_creates_no_handoff_directory(
        tmp_path, monkeypatch):
    """`begin --cycle abc` on a fresh root exits 2 and leaves the disk alone.

    Mutation: the resolver creating .handoff/ for begin before the anchors
    are validated, so an exit-2 run leaves a directory behind.
    Oracle: exit 2 and no .handoff/ under the root.
    """
    folder = _root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_CYCLE', 'abc')
    rc, out = _run(['begin', _SLUG])
    assert rc == 2
    assert 'hq: --cycle must be an integer' in out
    assert not (folder.parent).exists()


def test_collisions_ignore_stopwords_and_short_tokens():
    """`The` and `2` in the Now step do not collide with headings.

    Mutation: every capitalized or numeric token counted as a term, so a
    Now step starting with `The` collides with any heading holding `the`.
    Oracle: hand-computed - only `Warmup` survives as a term.
    """
    terms = hq._extract_terms(
        'Rework the Warmup path. The 2 steps, `2` arms, `ka` and Every case.')
    assert 'Warmup' in terms
    assert not {'The', 'Every', 'the', '2', 'ka'} & terms
    assert hq._extract_terms('the `Warmup` bucket') == {'Warmup'}


def test_extract_terms_drops_the_common_now_step_words():
    """State, Build, and Phase are Now-step furniture, not terms.

    Mutation: the stopword list missing the words a Now step always
    carries, so 'Phase 0' mid-sentence collides with any heading that
    holds the word 'phase'.
    Oracle: hand-computed - the bare words return no term, the backticked
    form still returns the token.
    """
    assert hq._extract_terms('Then build it, Phase 0.') == set()
    assert hq._extract_terms('Update the State machine.') == set()
    assert hq._extract_terms('Run the Build step.') == set()
    assert hq._extract_terms('Update the `State` machine.') == {'State'}


# --- final review round ------------------------------------------------------


def test_a_label_line_with_a_clause_grades_and_is_filed(tmp_path, monkeypatch):
    """`Read now, and note the cache module is tricky:` grades and is kept.

    Mutation: the clause form read as pure structure, so the clause's words
    leave the folder and conservation reports every line carried.
    Oracle: the bullet below graded always, the line under ## Unfiled, and
    adopt's conservation line reporting every line carried because the
    Unfiled bullet holds it.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'notes-a.md').write_text('# Notes\n\nfacts\n')
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n## Task\nT.\n\n'
        '## Now\nN.\n\n## Key files\n\n'
        'Read now, and note the cache module is the tricky one:\n'
        '- `notes-a.md` the facts\n')
    rc, out = _run(['adopt', _SLUG])
    assert rc == 0
    rows = {r['path']: r for r in hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)}
    assert rows['notes-a.md']['read_before'] == 'always'
    text = (folder / 'HANDOFF.md').read_text()
    assert '- unfiled: Read now, and note the cache module is the tricky one:' in text
    assert 'conservation: every original line carried' in out
    assert '- unfiled:' not in text.replace(
        '- unfiled: Read now, and note the cache module is the tricky one:', '')


def test_conservation_never_passes_an_empty_line_vacuously():
    """A line that normalizes to nothing is reported unless it is a bare label.

    Mutation: `'' in union_text` accepted as carried, so `- ****`, a bare
    pointer with no text, and every line under a heading they mask pass.
    Oracle: hand-computed - three original lines, one bare label; only the
    label is structure.
    """
    original = '## Key files\n\nRead now:\n- ****\n- SPEC.md\n'
    missing = hq.conservation(original, '## Task\nT.\n', '', [])
    assert missing == ['## Key files', '- ****', '- SPEC.md']
    carried = hq.conservation(original, '## Task\nT.\n', '', ['SPEC.md'])
    assert carried == ['- ****']


def test_a_loose_bullet_closes_the_open_pointer(tmp_path, monkeypatch):
    """A sub-bullet under a plain-word bullet joins its parent, not the pointer.

    Mutation: the loose branch leaving the pointer token open, so the next
    indented line appends to SPEC.md's label instead of the loose parent;
    or the indented line opens a new unfiled entry instead of joining the
    parent loose bullet.
    Oracle: SPEC.md's label is 'the spec label text'; the parent and child
    appear together in a single unfiled bullet; the child does not appear
    as a standalone unfiled entry.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'SPEC.md').write_text('# Spec\n\nbody\n')
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n## Task\nT.\n\n'
        '## Now\nN.\n\n## Key files\n\nRead now:\n'
        '- `SPEC.md` the spec label text\n'
        '- a loose word bullet\n'
        '  - the sub-bullet of the loose word bullet\n')
    assert _run(['adopt', _SLUG])[0] == 0
    rows = {r['path']: r for r in hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)}
    assert rows['SPEC.md']['label'] == 'the spec label text'
    text = (folder / 'HANDOFF.md').read_text()
    # Parent and child are joined into one unfiled bullet.
    assert '- unfiled: - a loose word bullet' in text
    assert 'the sub-bullet of the loose word bullet' in text
    # The child does not appear as a standalone unfiled entry.
    standalone = [
        ln for ln in text.splitlines()
        if ln.startswith('- unfiled:')
        and 'sub-bullet of the loose word bullet' in ln
        and 'a loose word bullet' not in ln
    ]
    assert not standalone, 'sub-bullet appeared as its own unfiled entry'


def test_bare_range_and_extensionless_pointers_are_pointers(tmp_path, monkeypatch):
    """`- SPEC.md:3-6 text` and `- Makefile text` seed rows with their text.

    Mutation: the path-like test rejecting a bare token with a range or a
    real file with no extension, so both land under ## Unfiled unlabeled.
    Oracle: the SPEC.md row's label leads with the range; the Makefile row
    carries its text; no `- unfiled:` bullet.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'SPEC.md').write_text('# Spec\n\n## 1. A\n\none\n\n## 2. B\n\ntwo\n')
    (folder / 'Makefile').write_text('all:\n\ttrue\n')
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n## Task\nT.\n\n'
        '## Now\nN.\n\n## Key files\n\n'
        '- SPEC.md:3-6 the numbered span\n'
        '- Makefile the build entry points\n')
    assert _run(['adopt', _SLUG])[0] == 0
    rows = {r['path']: r for r in hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)}
    assert rows['SPEC.md']['label'] == 'lines 3-6; the numbered span'
    assert rows['Makefile']['label'] == 'the build entry points'
    assert '- unfiled:' not in (folder / 'HANDOFF.md').read_text()


def test_note_line_joins_a_terminator_without_a_space():
    """`**Ship it**! Then go home.` keeps its wording through _note_line.

    Mutation: the joiner set missing `!` and `?`, so adopt inserts a space
    before the terminator and then reports the bullet not carried.
    Oracle: hand-computed lines for `!`, `?`, `,` and a plain word.
    """
    assert hq._note_line('', 'decision', 'Ship it', '! Then go home.', 2) == (
        '- [d01] (c2) **Ship it**! Then go home.')
    assert hq._note_line('', 'decision', 'Really', '? Yes.', 2) == (
        '- [d01] (c2) **Really**? Yes.')
    assert hq._note_line('', 'decision', 'Keep', 'the rest', 2) == (
        '- [d01] (c2) **Keep** the rest')


def test_stamp_on_an_unreadable_directory_does_not_raise(tmp_path, monkeypatch):
    """A probe directory with mode 000 stamps with `lines` 0, no traceback.

    Mutation: _do_stamp counting a directory itself instead of through
    _line_count, so the OSError escapes as a traceback.
    Oracle: exit 0 and lines '0' on the row; the directory is restored
    readable afterward so tmp_path can be removed.
    """
    if os.geteuid() == 0:
        return
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    locked = folder / 'locked'
    locked.mkdir()
    (locked / 'a.md').write_text('a\n')
    locked.chmod(0)
    try:
        rc, _ = _run(['stamp', _SLUG, 'locked', '--label', 'probes'])
    finally:
        locked.chmod(0o755)
    assert rc == 0
    rows = hq.latest_rows(hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS))
    row = rows['locked']
    assert row['lines'] == '0'


def test_a_marker_lookalike_inside_a_line_does_not_cut_the_cursor(
        tmp_path, monkeypatch):
    """A full block marker quoted mid-line is prose; the cursor runs on.

    Mutation: the block regexes unanchored, so the lookalike ends the
    cursor and finish silently drops every section below it.
    Oracle: ## Environment and ## Open questions survive finish verbatim.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _cursor(folder, '## State\nThe marker `<!-- hq:read abc123def456 -->` '
            'opens the read block.\n\n## Environment\n- env line\n\n'
            '## Open questions\n- still open?')
    rc, out = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0, out
    text = (folder / 'HANDOFF.md').read_text()
    assert '## Environment\n- env line' in text
    assert '## Open questions\n- still open?' in text


def test_restoring_a_lost_newline_is_announced(tmp_path, monkeypatch):
    """The append that puts a trailing newline back says so.

    Mutation: the restore silent, so a W2 break begin reported vanishes
    with no receipt at finish.
    Oracle: the advisory line from note after standing.md lost its last
    byte, and from stamp after ledger.tsv did.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['note', _SLUG, 'decision', '--headline', 'First', 'one'])[0] == 0
    cases = (
        ('standing.md', ['note', _SLUG, 'decision', '--headline', 'S', 'two']),
        ('ledger.tsv', ['stamp', _SLUG, 'HANDOFF.orig.md']))
    for name, argv in cases:
        target = folder / name
        target.write_bytes(target.read_bytes()[:-1])
        (folder / 'HANDOFF.orig.md').write_text('# old\n')
        rc, out = _run(argv)
        assert rc == 0, out
        assert f'advisory: {name} lacked its trailing newline; restored' in out


def test_a_punctuation_only_headline_is_refused(tmp_path, monkeypatch):
    """`- decision: . rest` has no word in its headline and is refused.

    Mutation: the guard testing for whitespace only, so `.` passes as a
    headline and `**.**` lands in standing.md.
    Oracle: finish exits 1 with the documented line; note refuses the
    same headline with its own line.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _cursor(folder, '## Unfiled\n- decision: . rest of the thing')
    rc, out = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 1
    assert 'hq finish: Unfiled bullet has no headline:' in out
    rc, out = _run(['note', _SLUG, 'decision', '--headline', '...', 'body'])
    assert rc == 2
    assert 'hq note: --headline is required' in out
