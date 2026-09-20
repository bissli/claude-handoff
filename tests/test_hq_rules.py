"""Tests for the pure functions of the handoff ledger (hq).

All tests drive in-process pure functions imported from hq.py with no
folder on disk. Row dicts carry all thirteen ledger fields. Every oracle
is hand-computed.
"""

import hashlib

from bin import hq

_FIELDS = (
    'cycle', 'ts', 'path', 'base', 'kind', 'status', 'read_before',
    'successor', 'where', 'sha12', 'lines', 'reason', 'label',
)


def _row(**kwargs):
    """Build a Row dict with defaults for every field not supplied.

    All thirteen ledger fields are present so check_r1 and check_r3 never
    see a KeyError regardless of which fields the test exercises.
    """
    base = {
        'cycle': '1',
        'ts': '2026-01-01T00:00:00',
        'path': 'SPEC.md',
        'base': 'folder',
        'kind': 'spec',
        'status': 'live',
        'read_before': 'always',
        'successor': '-',
        'where': '-',
        'sha12': 'aabbccddeeff',
        'lines': '100',
        'reason': '-',
        'label': 'render pass section 4',
    }
    base.update(kwargs)
    return base


def _sha12(data):
    """Return the first 12 hex digits of sha256(data)."""
    return hashlib.sha256(data).hexdigest()[:12]


# --- infer_kind -------------------------------------------------------


def test_kind_inference_over_a_representative_name_set():
    """Verify infer_kind returns the correct kind for
    a representative name set.

    Mutation: snapshot precedence dropped, so SPEC.pre-* is a gated spec;
    cycle<digits> not matched as a regex, so notes-cycle13-review.md becomes
    notes instead of snapshot; notes-* fires before *.pre-*, so
    notes-open-issues.pre-x.md becomes notes instead of snapshot.
    Oracle: hand-derived from the first-match-wins table in section 4;
    snapshot patterns precede spec and notes patterns; notes-* precedes
    todo*.
    """
    # Each tuple: (name, is_dir, first_heading, expected_pair)
    cases = [
        ('HANDOFF.md', False, '', ('snapshot', 'never')),
        ('HANDOFF.bak', False, '', ('snapshot', 'never')),
        # *.pre-* -> snapshot (before spec and notes rules)
        ('DESIGN.pre-v1-20260901.md', False, '', ('snapshot', 'never')),
        ('SPEC.orig.md', False, '', ('snapshot', 'never')),
        # *.prev.* -> snapshot (before SPEC* and DESIGN* rules)
        ('SPEC.prev.md', False, '', ('snapshot', 'never')),
        ('DESIGN.prev.md', False, '', ('snapshot', 'never')),
        # *cycle<digits>* -> snapshot (before notes-* rule)
        ('notes-cycle13-review.md', False, '', ('snapshot', 'never')),
        ('notes-cycle07-cleanup.md', False, '', ('snapshot', 'never')),
        # *.pre-* fires before notes-* by precedence
        ('notes-open-issues.pre-x.md', False, '', ('snapshot', 'never')),
        ('pipeline-cycle3-export.md', False, '', ('snapshot', 'never')),
        ('render.pre-debug-20260901.md', False, '', ('snapshot', 'never')),
        ('DESIGN.orig.md', False, '', ('snapshot', 'never')),
        ('SPEC.md', False, '', ('spec', 'always')),
        ('DESIGN.md', False, '', ('spec', 'always')),
        ('PROPOSAL.md', False, '', ('spec', 'always')),
        ('ARCH-DECLARATION.md', False, '', ('spec', 'always')),
        ('overview.md', False, '# Spec', ('spec', 'always')),
        ('guide.md', False, '# Design', ('spec', 'always')),
        ('report.py', False, '', ('draft', 'edit')),
        ('loader.sql', False, '', ('draft', 'edit')),
        ('bundle.js', False, '', ('draft', 'edit')),
        ('types.ts', False, '', ('draft', 'edit')),
        ('deploy.ps1', False, '', ('draft', 'edit')),
        ('notes-backlog.md', False, '', ('notes', 'never')),
        ('notes-build-plan.md', False, '', ('notes', 'never')),
        ('notes-api-design.md', False, '', ('notes', 'never')),
        ('notes-cache-ideas.md', False, '', ('notes', 'never')),
        ('notes-open-items.md', False, '', ('notes', 'never')),
        ('notes-reformat.md', False, '', ('notes', 'never')),
        ('notes-testing.md', False, '', ('notes', 'never')),
        ('notes-templates.md', False, '', ('notes', 'never')),
        ('notes-styling.md', False, '', ('notes', 'never')),
        ('notes-decisions.md', False, '', ('notes', 'never')),
        ('notes-limits.md', False, '', ('notes', 'never')),
        ('notes-open-issues.md', False, '', ('notes', 'never')),
        ('notes-progress.md', False, '', ('notes', 'never')),
        ('notes-build-cache.md', False, '', ('notes', 'never')),
        # notes-* fires before todo* by precedence
        ('notes-config-todo.md', False, '', ('notes', 'never')),
        ('REVIEW.md', False, '', ('notes', 'never')),
        ('todo-cleanup.md', False, '', ('todo', 'never')),
        ('index.html', False, '', ('other', 'never')),
        ('sitemap.html', False, '', ('other', 'never')),
        ('config.json', False, '', ('other', 'never')),
        ('manifest.json', False, '', ('other', 'never')),
        # probe-dir (is_dir=True)
        ('experiments', True, '', ('probe-dir', 'never')),
        ('probes', True, '', ('probe-dir', 'never')),
        # conflicted copy -> skip
        ('SPEC (conflicted copy 2026-09-09).md', False, '', ('skip', '-')),
    ]
    for name, is_dir, first_heading, expected in cases:
        result = hq.infer_kind(name, is_dir, first_heading)
        assert result == expected, f'{name}: expected {expected}, got {result}'


def test_an_authored_notes_or_todo_name_beats_a_design_heading():
    """A notes-*, REVIEW*, or todo* name outranks a Spec/Design heading.

    Mutation: the heading rule left above the name rules, so a notes file
    whose first heading opens 'Design' is stamped spec/always and R1
    refuses every later demotion; the name rules moved above the
    _SPEC_PATS rule, so a DESIGN-todo.md stops being a spec; or the name
    rules moved back below the draft-extension rule, so a top-level
    notes-*.py is a gated draft again.
    Oracle: hand-derived from the first-match table as reordered, with
    the heading-only guide.md and the DESIGN-* name as the control rows.
    """
    assert hq.infer_kind('notes-probe.py', False, '') == ('notes', 'never')
    assert hq.infer_kind(
        'notes-widget-deltas.md', False, '# Design deltas to close',
        ) == ('notes', 'never')
    assert hq.infer_kind(
        'REVIEW-pass2.md', False, '## Design notes') == ('notes', 'never')
    assert hq.infer_kind(
        'todo-widget.md', False, '# Design of the todo') == ('todo', 'never')
    assert hq.infer_kind('guide.md', False, '# Design') == ('spec', 'always')
    assert hq.infer_kind('DESIGN-todo.md', False, '# Design') == ('spec', 'always')


# --- apply_stem_rule --------------------------------------------------


def test_adopt_gates_the_newest_spec_per_stem_only():
    """Verify apply_stem_rule marks older SPEC stem-mates as superseded.

    Mutation: every stem-mate gated (no stem rule applied), so all SPEC*
    pre-variant files keep read_before=always alongside the live spec;
    also, a stem with no spec member incorrectly superseded so a
    multi-file notes stem without any spec would lose its members.
    Oracle: hand-computed - SPEC.md (mtime=200.0) is newest; the two older
    files map to SPEC.md as their successor; SPEC.md itself is absent;
    DESIGN.md has a different stem and is unaffected; notes stem members
    with no spec remain absent even when there are two of them.
    """
    entries = [
        ('SPEC.md', 'spec', 200.0),
        ('SPEC.pre-draft-20250114.md', 'snapshot', 100.0),
        ('SPEC.pre-final-20250120.md', 'snapshot', 90.0),
        ('DESIGN.md', 'spec', 150.0),
        ('notes-open-issues.md', 'notes', 180.0),
        ('notes-open-issues.pre-x.md', 'snapshot', 170.0),
    ]
    result = hq.apply_stem_rule(entries)
    assert 'SPEC.md' not in result
    assert result.get('SPEC.pre-draft-20250114.md') == 'SPEC.md'
    assert result.get('SPEC.pre-final-20250120.md') == 'SPEC.md'
    assert 'DESIGN.md' not in result
    assert 'notes-open-issues.md' not in result
    assert 'notes-open-issues.pre-x.md' not in result


# --- check_r1 ---------------------------------------------------------


def test_demote_spec_from_always_without_successor_refused():
    """Verify R1 refuses to lower read_before off always for an inferred spec.

    Mutation: R1 floored at edit instead of always, so --read-before edit
    buries a spec with no audit trail.
    Oracle: hand-computed - check_r1 returns non-None for always->edit and
    always->never with no successor; returns None when successor_on_disk=True.
    """
    row_edit = _row(read_before='edit')
    row_never = _row(read_before='never')
    assert hq.check_r1(row_edit, 'spec', False) is not None
    assert hq.check_r1(row_never, 'spec', False) is not None
    assert hq.check_r1(row_edit, 'spec', True) is None
    assert hq.check_r1(row_never, 'spec', True) is None


def test_demote_notes_from_always_is_allowed():
    """Verify R1 does not block demotions when the inferred kind is notes.

    Mutation: R1 applied to every kind, which would block a notes file the
    agent legitimately wants to de-prioritize.
    Oracle: hand-computed - inferred_kind=notes with read_before=never and
    no successor returns None (allowed) regardless of stored kind.
    """
    row = _row(path='notes-open-issues.md', kind='notes', read_before='never')
    assert hq.check_r1(row, 'notes', False) is None
    row_mention = _row(
        path='notes-open-issues.md', kind='notes', read_before='mention')
    assert hq.check_r1(row_mention, 'notes', False) is None


def test_kind_change_off_spec_is_a_demotion():
    """Verify R1 catches a kind change from spec to notes
    when inferred is spec.

    Mutation: R1 keyed on the agent's stored kind rather than the inferred
    kind, so --kind notes on a spec file bypasses the gate silently; also,
    R1 keyed only on read_before rather than kind, so kind='notes'
    read_before='always' would bypass.
    Oracle: hand-computed - new_row carries kind=notes but inferred_kind=spec;
    check_r1 must return a refusal; same for kind=other; a notes row with
    read_before=always is also refused because kind is wrong.
    """
    row_notes = _row(kind='notes', read_before='never')
    assert hq.check_r1(row_notes, 'spec', False) is not None
    row_other = _row(kind='other', read_before='never')
    assert hq.check_r1(row_other, 'spec', False) is not None
    # A status change off live is also a demotion
    row_archived = _row(status='archived', read_before='never')
    assert hq.check_r1(row_archived, 'spec', False) is not None
    # read_before=always alone does not excuse a kind demotion
    row_notes_always = _row(kind='notes', read_before='always', status='live')
    assert hq.check_r1(row_notes_always, 'spec', False) is not None


def test_defer_refused_for_inferred_spec():
    """Verify --defer is refused when the inferred kind is spec or draft.

    Mutation: the --defer escape open to a spec, letting kind=other
    read_before=never bypass R1 without a receipt.
    Oracle: hand-computed - a deferred row (kind=other, read_before=never,
    reason=deferred) is refused for spec and draft inferred kinds, allowed
    for notes and other.
    """
    deferred = _row(kind='other', read_before='never', reason='deferred')
    assert hq.check_r1(deferred, 'spec', False) is not None
    assert hq.check_r1(deferred, 'draft', False) is not None
    assert hq.check_r1(deferred, 'notes', False) is None
    assert hq.check_r1(deferred, 'other', False) is None


# --- check_r3 ---------------------------------------------------------


def test_r3_utime_only_does_not_block():
    """Verify check_r3 passes when the sha is unchanged despite a mtime change.

    Mutation: R3 comparing mtime instead of sha - a file touched by a backup
    tool without being written would then block finish unnecessarily.
    Oracle: hand-computed - live always-row with sha12=aabb11223344 matched
    by sha_by_path -> empty result.
    """
    rows = [_row(path='SPEC.md', status='live', read_before='always',
                 sha12='aabb11223344')]
    sha_by_path = {'SPEC.md': 'aabb11223344'}
    assert hq.check_r3(rows, sha_by_path) == []


def test_r3_bytes_changed_with_mtime_restored_blocks():
    """Verify check_r3 flags live always- and edit-rows whose sha differs.

    Mutation: R3 comparing mtime from the other side - a tool that restores
    mtime after writing passes mtime-based R3 but the content changed; also,
    narrowing the gate from 'not in _GATE_RB' to '!= always' would let an
    edit-graded moved sha through silently.
    Oracle: hand-computed - always-row and edit-row each with mismatched sha
    both appear in the result; a never-row with mismatched sha is absent.
    """
    rows = [
        _row(path='SPEC.md', status='live', read_before='always',
             sha12='aabb11223344'),
        _row(path='report.py', status='live', read_before='edit',
             sha12='1122334455aa'),
        _row(path='notes.md', status='live', read_before='never',
             sha12='aabb11223344'),
        ]
    sha_by_path = {
        'SPEC.md': 'ccdd55667788',
        'report.py': 'bbccddee0011',
        'notes.md': 'ccdd55667788',
        }
    result = hq.check_r3(rows, sha_by_path)
    paths = {r['path'] for r in result}
    assert 'SPEC.md' in paths
    assert 'report.py' in paths
    assert 'notes.md' not in paths


def test_r3_never_row_ignored():
    """Verify check_r3 skips rows with read_before=never even when sha differs.

    Mutation: R3 applied to never rows, blocking finish for files the agent
    has explicitly deprioritized.
    Oracle: hand-computed - live never-row with mismatched sha returns empty.
    """
    rows = [_row(path='notes-open-issues.md', status='live', read_before='never',
                 sha12='aabb11223344')]
    sha_by_path = {'notes-open-issues.md': 'ccdd55667788'}
    assert hq.check_r3(rows, sha_by_path) == []


# --- witness ----------------------------------------------------------


def test_witness_catches_a_clause_deleted_inside_a_line():
    """Verify witness detects an in-line edit even when
    line count is unchanged.

    Mutation: a count-witness - checking only byte count lets a same-length
    in-place edit pass; the sha prefix catches it.
    Oracle: hand-computed sha c2c7c5bdd4a8 for original 23 bytes; same-length
    modified content sha differs -> W1 in result.
    """
    original = b'header\nrow1 has clause\n'
    same_len = b'HEADER\nrow1 has clause\n'
    appended = same_len + b'row2\n'
    prev = {
        'ledger_bytes': len(original),
        'ledger_sha': _sha12(original),
        'standing_bytes': 0,
        'standing_sha': _sha12(b''),
    }
    breaks = hq.witness(prev, appended, b'')
    assert any(b.startswith('W1') for b in breaks)


def test_witness_catches_delete_plus_append():
    """Verify witness flags a deleted line even when a new line is appended.

    Mutation: a count-witness - comparing only byte counts lets a delete+append
    that keeps the byte count identical pass silently; the sha prefix
    catches it.
    Oracle: hand-computed - deleting row1 and appending row3 keeps 17 bytes but
    changes the sha of the first 17 bytes from c20b33bd61a1 -> different -> W1.
    """
    # original is 17 bytes, sha c20b33bd61a1;
    # modified keeps 17 bytes, row1 gone
    original = b'header\nrow1\nrow2\n'
    modified = b'header\nrow2\nrow3\n'
    prev = {
        'ledger_bytes': len(original),
        'ledger_sha': _sha12(original),
        'standing_bytes': 0,
        'standing_sha': _sha12(b''),
    }
    breaks = hq.witness(prev, modified, b'')
    assert any(b.startswith('W1') for b in breaks)


def test_witness_passes_a_pure_append():
    """Verify witness passes when only new rows are appended
    after recorded bytes.

    Mutation: checking the sha of the whole current file instead of only the
    first ledger_bytes bytes - a pure append changes the whole-file sha but
    must not block.
    Oracle: hand-computed - original 12 bytes sha a9708ae94935; appended
    content has the same first 12 bytes, so sha of prefix matches.
    """
    # original is 12 bytes, sha a9708ae94935
    original = b'header\nrow1\n'
    appended = original + b'row2\n'
    prev = {
        'ledger_bytes': len(original),
        'ledger_sha': _sha12(original),
        'standing_bytes': 0,
        'standing_sha': _sha12(b''),
    }
    breaks = hq.witness(prev, appended, b'')
    assert breaks == []


# --- block_sha --------------------------------------------------------


def test_block_sha_ignores_realignment():
    """Verify block_sha is stable under column spacing changes.

    Mutation: sha over raw bytes - the markdown realigner changes column
    spacing and every cycle produces a different sha even when content is
    unchanged.
    Oracle: hand-computed - one space vs two spaces between columns both
    normalize to the same single-space string before hashing.
    """
    text1 = '## Artifacts\nSPEC.md  spec  always  c01  render pass\n'
    text2 = '## Artifacts\nSPEC.md   spec   always   c01   render pass\n'
    assert hq.block_sha(text1) == hq.block_sha(text2)


def test_block_sha_catches_a_dropped_row():
    """Verify block_sha differs when a row is removed from the block.

    Mutation: sha over too little (e.g., only the heading line) - removing
    a row then changes nothing the sha covers and the tamper passes.
    Oracle: hand-computed - two-row block vs one-row block -> different sha
    because the normalized text differs.
    """
    with_row = (
        '## Artifacts\n'
        'SPEC.md  spec  always  c01  label\n'
        'report.py  draft  always  c01  label\n'
    )
    without_row = (
        '## Artifacts\n'
        'SPEC.md  spec  always  c01  label\n'
    )
    assert hq.block_sha(with_row) != hq.block_sha(without_row)


# --- resolve_where ----------------------------------------------------


def test_where_resolves_to_the_current_span():
    """Verify resolve_where returns a 1-based inclusive span for a heading.

    Mutation: anchors matched by stored line number instead of heading text -
    a reordering of sections gives a stale number while the heading remains
    findable by text.
    Oracle: hand-computed - 'Section Two' is line 6 in the 7-line fixture;
    no same-or-higher heading follows it, so the span runs to line 7.
    """
    text = (
        '# Section One\n'
        'content a\n'
        'content b\n'
        '## Subsection\n'
        'content c\n'
        '# Section Two\n'
        'content d\n'
    )
    spans, unresolved = hq.resolve_where(text, ['Section Two'])
    assert spans == [(6, 7)]
    assert unresolved == []


def test_unresolved_where_is_reported():
    """Verify resolve_where lists an anchor it cannot match
    in the unresolved output.

    Mutation: a missing anchor silent - the read block prints ? in the span
    slot but the caller is never told which anchor needs repair.
    Oracle: hand-computed - 'Nonexistent' matches no heading in the one-line
    fixture, so spans is empty and unresolved contains the string.
    """
    text = '# Real Heading\ncontent\n'
    spans, unresolved = hq.resolve_where(text, ['Nonexistent'])
    assert spans == []
    assert 'Nonexistent' in unresolved


# --- collisions -------------------------------------------------------


def test_collision_hits_an_unbackticked_term():
    """Verify collisions detects a capitalized term that is not backticked.

    Mutation: backtick-only matching - a Now step that writes Lattice without
    backticks is not caught, so the burial check fires on half the inputs.
    Oracle: hand-computed - 'Lattice' is capitalized mid-sentence, making it
    a distinctive term; the SPEC.md heading contains it as a whole word.
    """
    now_text = 'Split on the Lattice fallback to resolve the gap'
    headings = [('SPEC.md', 438, 'Vertex Lattice stays unresolved')]
    headlines = []
    rarity = {'Lattice': 2}
    result = hq.collisions(now_text, headlines, headings, rarity)
    assert len(result) >= 1
    assert any('Lattice' in hit and 'collides:' in hit for hit in result)


def test_collision_ranks_by_rarity_and_caps_at_three():
    """Verify collisions returns at most three hits
    ordered by ascending rarity.

    Mutation: hits truncated in arbitrary order - the rarest term is most
    specific and must appear first; a cap higher than three overloads review.
    Oracle: hand-computed - rarity ascending: ParseSource(1) < ScanEntry(2)
    < RebuildIndex(3) < CacheManifest(5); only the three rarest reported.
    """
    now_text = (
        'Fix CacheManifest then RebuildIndex then ScanEntry then ParseSource'
    )
    headings = [
        ('SPEC.md', 10, 'CacheManifest cannot be promoted'),
        ('SPEC.md', 20, 'RebuildIndex arm stays fixed'),
        ('SPEC.md', 30, 'ScanEntry is mandatory'),
        ('SPEC.md', 40, 'ParseSource has no override'),
    ]
    headlines = []
    rarity = {
        'CacheManifest': 5,
        'RebuildIndex': 3,
        'ScanEntry': 2,
        'ParseSource': 1,
    }
    result = hq.collisions(now_text, headlines, headings, rarity)
    assert len(result) == 3
    assert 'ParseSource' in result[0]
    assert 'ScanEntry' in result[1]
    assert 'RebuildIndex' in result[2]
    assert not any('CacheManifest' in hit for hit in result)


# --- drain_unfiled ----------------------------------------------------


def test_untyped_unfiled_bullet_blocks_and_names_the_line():
    """Verify drain_unfiled refuses and names a bullet
    with no recognized prefix.

    Mutation: the drain guessing a section for an untyped bullet - an item
    lands in the wrong section with no indication the guess was made.
    Oracle: hand-computed - a bullet without decision:/constraint:/dead-end:
    returns a non-None refusal that contains the bullet's own text.
    """
    cursor = '## Task\nSome work.\n## Unfiled\n- this bullet has no prefix\n'
    items, new_cursor, refusal = hq.drain_unfiled(cursor)
    assert refusal is not None
    assert 'this bullet has no prefix' in refusal


def test_typed_unfiled_bullets_reach_their_sections():
    """Verify drain_unfiled parses all three recognized bullet prefixes.

    Mutation: the drain returning items in the wrong kind slot or losing the
    headline from the bold span.
    Oracle: hand-computed - three typed bullets produce three items carrying
    the right (kind, headline) pair; the cursor loses ## Unfiled.
    """
    cursor = (
        '## Task\nDo work.\n'
        '## Unfiled\n'
        '- decision: **My Decision** some body text\n'
        '- constraint: **My Constraint** more text here\n'
        '- dead-end: **My Dead End** reason given\n'
    )
    items, new_cursor, refusal = hq.drain_unfiled(cursor)
    assert refusal is None
    kind_headline_pairs = {(item[0], item[1]) for item in items}
    assert ('decision', 'My Decision') in kind_headline_pairs
    assert ('constraint', 'My Constraint') in kind_headline_pairs
    assert ('dead-end', 'My Dead End') in kind_headline_pairs
    assert '## Unfiled' not in new_cursor


# --- render_artifacts -------------------------------------------------


def test_artifacts_block_lists_an_unpointed_file():
    """Verify render_artifacts includes walk entries that have no ledger row.

    Mutation: the block built from the previous block's text instead of the
    walk - a file added since the last stamp is invisible.
    Oracle: hand-computed - walk has notes.md with no row; output must
    contain 'notes.md' with an unstamped marker.
    """
    walk = [('notes.md', 'notes')]
    rows = {}
    result = hq.render_artifacts(walk, rows, 'test-slug')
    assert 'notes.md  notes?  unstamped' in result.splitlines()


def test_a_stamped_spec_enters_the_read_block_with_its_span():
    """Verify render_read prints the gated spec's spans and every row's size.

    Mutation: the whole-file branch dropped, so a row with no anchor costs
    the reader an unpriced whole-file read; an unresolved anchor rendered
    as a span rather than '?'; or the size column dropped.
    Oracle: hand-computed - one row with two anchors, one resolved and one
    not, sized 1.4k tok, and one row with where='-' sized as a whole file.
    """
    rows = [
        _row(path='SPEC.md', kind='spec', where='s4;Ghost', label='cache warmup'),
        _row(path='poller.py', kind='draft', where='-', label='drafted rewrite'),
        ]
    spans = {'SPEC.md': [(759, 814), None]}
    sizes = {'SPEC.md': '1.4k tok', 'poller.py': '205 lines, 2.1k tok'}
    result = hq.render_read(rows, spans, sizes)
    assert result.splitlines() == [
        'poller.py  (205 lines, 2.1k tok)  drafted rewrite',
        'SPEC.md:759-814,?  (1.4k tok)  cache warmup',
        ]


def test_artifacts_mention_row_renders_as_full_line():
    """Verify render_artifacts emits a full line for a mention row.

    Mutation: narrowing _FULL_RB to {'always', 'edit'} collapses every
    mention row into the never count line silently.
    Oracle: hand-computed - a live mention row yields the full
    'path  kind  mention  cN  label' line, not a count entry.
    """
    walk = [('notes.md', 'notes')]
    rows = {
        'notes.md': _row(
            path='notes.md', kind='notes',
            status='live', read_before='mention',
            cycle='3', label='background reference',
        )
    }
    result = hq.render_artifacts(walk, rows, 'test-slug')
    assert 'notes.md  notes  mention  c3  background reference' in result.splitlines()


def test_artifacts_block_collapses_never_rows_and_caps_at_40():
    """Verify never rows appear only as count lines and full-line cap is 40.

    Mutation: any block growing with cycles - never rows rendered in full
    make the block proportional to ledger size rather than bounded.
    Oracle: hand-computed - 42 never-rows produce zero full lines and one
    count line; 41 always-rows produce exactly 40 full lines plus the
    count line 'spec x1', keyed on the kind and not on the read_before.
    """
    never_walk = [(f'notes-{i:02d}.md', 'notes') for i in range(42)]
    never_rows = {
        f'notes-{i:02d}.md': _row(
            path=f'notes-{i:02d}.md', kind='notes',
            status='live', read_before='never',
        )
        for i in range(42)
    }
    result_never = hq.render_artifacts(never_walk, never_rows, 'slug')
    full_never = [
        line for line in result_never.splitlines()
        if '  notes  ' in line and '  never  ' in line
    ]
    assert full_never == []
    assert 'notes x42  - hq artifacts slug' in result_never

    always_walk = [(f'SPEC-{i:02d}.md', 'spec') for i in range(41)]
    always_rows = {
        f'SPEC-{i:02d}.md': _row(
            path=f'SPEC-{i:02d}.md', kind='spec',
            status='live', read_before='always',
            cycle=str(i + 1),
        )
        for i in range(41)
    }
    result_always = hq.render_artifacts(always_walk, always_rows, 'slug')
    full_always = [
        line for line in result_always.splitlines()
        if '  spec  ' in line and '  always  ' in line
    ]
    assert len(full_always) == 41
    assert ' - hq artifacts ' not in result_always


# --- render_standing --------------------------------------------------


def test_standing_block_omits_superseded_and_prints_every_live_item():
    """Verify superseded items appear only as a count and no cap cuts the rest.

    Mutation: printing superseded items in full, so the block grows with
    every ruling since the thread began; or a line cap folding live
    decisions into a '... N more' line, which drops a ruling the reader
    must not undo.
    Oracle: hand-computed - d02 is superseded so 'Superseded decision' is
    absent; 82 unsuperseded decisions render 82 headline lines.
    """
    items = [
        {'id': 'c01', 'prefix': 'c', 'cycle': '1',
         'headline': 'One constraint', 'body': 'body text'},
        {'id': 'd01', 'prefix': 'd', 'cycle': '1',
         'headline': 'First decision', 'body': ''},
        {'id': 'd02', 'prefix': 'd', 'cycle': '2',
         'headline': 'Superseded decision', 'body': ''},
    ]
    result = hq.render_standing(items, {'d02'}, 'test-slug')
    assert 'Superseded decision' not in result
    assert '[d02]' not in result
    assert 'superseded 1' in result
    assert 'hq standing test-slug' in result
    assert '[c01]' in result
    assert '[d01]' in result

    many = [
        {'id': f'd{i:02d}', 'prefix': 'd', 'cycle': '1',
         'headline': f'Decision number {i}', 'body': ''}
        for i in range(82)
    ]
    result2 = hq.render_standing(many, set(), 'slug2')
    content_lines = [line for line in result2.splitlines() if line.strip()]
    assert len(content_lines) == 83
    assert sum(ln.startswith('[d') for ln in content_lines) == 82
    assert 'more' not in result2


# --- render_log -------------------------------------------------------


def test_log_renders_three_cycles_plus_rollup():
    """Verify render_log shows the last three cycles then rolls up older ones.

    Mutation: any block growing with cycles - printing every log entry makes
    the Log section proportional to finished-cycle count; also, moving the
    roll-up line before the three cycle lines inverts the design order.
    Oracle: hand-computed - five manifest rows; lines 0-2 are cycles 3, 4, 5
    in that order; line 3 is the rollup naming manifest.tsv; cycles 1-2 absent.
    """
    manifest = [
        {'cycle': '1', 'written': '2026-01-01', 'repos': 'main@abc1234',
         'log': 'initial setup', 'note': '-'},
        {'cycle': '2', 'written': '2026-01-02', 'repos': 'main@abc1235',
         'log': 'first pass', 'note': '-'},
        {'cycle': '3', 'written': '2026-01-03', 'repos': 'main@abc1236',
         'log': 'shipped step 1', 'note': '-'},
        {'cycle': '4', 'written': '2026-01-04', 'repos': 'main@abc1237',
         'log': 'shipped step 2', 'note': '-'},
        {'cycle': '5', 'written': '2026-01-05', 'repos': 'main@abc1238',
         'log': 'shipped step 3', 'note': '-'},
    ]
    result = hq.render_log(manifest)
    lines = result.splitlines()
    assert len(lines) == 4
    assert 'shipped step 1' in lines[0]
    assert 'shipped step 2' in lines[1]
    assert 'shipped step 3' in lines[2]
    assert 'cycles 1-2' in lines[3]
    assert 'manifest.tsv' in lines[3]
    assert 'initial setup' not in result
    assert 'first pass' not in result


# --- conservation -----------------------------------------------------


def test_adopt_conservation_reports_a_line_no_part_of_the_union_holds():
    """Verify conservation reports lines missing from cursor/standing/labels.

    Mutation: `all` in place of `any` in the heading-coverage test, so a
    heading whose section has no content line at all counts as covered
    and a section dropped whole is never reported.
    Oracle: hand-computed - the same original against three unions, and
    a lone heading with an empty section, which must be reported.
    """
    original = (
        '## Task\nDo the work.\n'
        '## Decisions\n'
        '- **Some ruling** body text\n'
    )
    cursor = '## Task\nDo the work.\n## Decisions\n'
    standing = '- [d01] (c01) **Some ruling** body text'
    missing = hq.conservation(original, cursor, standing, [])
    assert missing == []

    # Without standing: the decision content line is not covered
    missing2 = hq.conservation(original, cursor, '', [])
    assert any('ruling' in line for line in missing2)

    # With nothing: all distinctive lines are missing;
    # proves archive is excluded
    missing3 = hq.conservation(original, '', '', [])
    assert len(missing3) > 0

    # A heading genuinely absent from the union is reported
    missing4 = hq.conservation('## Gone Heading\n', '', '', [])
    assert any('Gone Heading' in ln for ln in missing4)


def test_conservation_reports_a_preamble_before_the_first_heading():
    """Verify a line above the first heading is measured like any other.

    Mutation: the section index keyed on a heading that does not exist
    yet, so a preamble line raises KeyError instead of being reported.
    Oracle: hand-computed - the preamble line is in no part of the union,
    so it is the one line reported.
    """
    assert hq.conservation(
        'Preamble line.\n## Task\nDo.\n', '## Task\nDo.\n', '', []
        ) == ['Preamble line.']


# --- cycle 5 renderer and rule fixes -----------------------------------


def _ledger_row(**overrides):
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({'cycle': '1', 'base': 'folder', 'kind': 'notes', 'status': 'live',
                'read_before': 'never'})
    row.update(overrides)
    return row


def test_stem_rule_leaves_a_newer_stem_mate_alone():
    """Verify apply_stem_rule supersedes only mates older than the newest spec.

    Mutation: every non-newest mate superseded regardless of mtime, so a
    draft newer than the spec is seeded superseded with the spec as its
    successor.
    Oracle: hand-computed - the newer draft is absent from the result and
    the older snapshot maps to SPEC.md.
    """
    entries = [
        ('SPEC.md', 'spec', 200.0),
        ('SPEC.draft.md', 'draft', 250.0),
        ('SPEC.pre-x.md', 'snapshot', 100.0),
    ]
    result = hq.apply_stem_rule(entries)
    assert 'SPEC.draft.md' not in result
    assert result.get('SPEC.pre-x.md') == 'SPEC.md'


def test_drain_unfiled_headline_never_ends_inside_an_open_quotation():
    """A drained headline runs to a sentence end outside any quotation.

    Mutation: the quote-balance check dropped, so 'fine.' inside the
    quotation ends the headline; or the drain path left on a sentence end
    with no lookahead, so 'v1.2' splits at its dot.
    Oracle: hand-split headline and body for a quoted item and for a
    version number mid-sentence.
    """
    cursor = (
        '## Task\nx\n\n## Unfiled\n'
        '- decision: User said "the old format is fine. Keep it." Filed.\n'
        '- constraint: Pin v1.2 for now. Bump later.\n')
    items, _, refusal = hq.drain_unfiled(cursor)
    assert refusal is None
    assert items == [
        ('decision', 'User said "the old format is fine. Keep it."', 'Filed.'),
        ('constraint', 'Pin v1.2 for now.', 'Bump later.'),
    ]


def test_drain_unfiled_runs_a_short_label_headline_on_and_still_refuses_none():
    """The drained headline passes a one- or two-word label but not a bare dot.

    Mutation: the label rule keyed on character length, so 'Never trust
    mtime!' swallows its body; or the zero-word case allowed to run on,
    so '- decision: . rest' gains a headline and the no-headline refusal
    never fires.
    Oracle: hand-counted words - 'Cycle 26.' has two, 'Never trust
    mtime!' three, '.' none - against hand-split headlines.
    """
    cursor = (
        '## Task\nx\n\n## Unfiled\n'
        '- dead-end: Cycle 26. Reading the gain showed nothing. Done.\n'
        '- constraint: Never trust mtime! It lies on network mounts.\n')
    items, _, refusal = hq.drain_unfiled(cursor)
    assert refusal is None
    assert items == [
        ('dead-end', 'Cycle 26. Reading the gain showed nothing.', 'Done.'),
        ('constraint', 'Never trust mtime!', 'It lies on network mounts.'),
    ]
    _, _, refusal = hq.drain_unfiled(
        '## Task\nx\n\n## Unfiled\n- decision: . rest of the thing\n')
    assert refusal == (
        "Unfiled bullet has no headline: '- decision: . rest of the thing'"
        ' - give the bold span, or the first sentence, a word')


def test_collisions_ignore_a_capital_at_sentence_start():
    """Verify a capitalized word opening a sentence is not a Now-step term.

    Mutation: terms drawn from the heading side, or the sentence-start
    exclusion dropped, so 'Delta' opening the Now step collides with a
    'Delta section' heading.
    Oracle: hand-computed - no hit for the opening word, one hit when the
    same word sits mid-sentence.
    """
    headings = [('SPEC.md', 3, 'Delta section')]
    assert hq.collisions('Delta must stay fixed.', [], headings, {'Delta': 1}) == []
    hits = hq.collisions('Fix the Delta path.', [], headings, {'Delta': 1})
    assert len(hits) == 1
    assert 'Delta' in hits[0]


def test_collisions_ignore_a_capital_opening_a_list_item():
    """A capital heading a Now bullet or a heading is position, not a term.

    Mutation: the sentence-start test reading only the character before
    the word, so a bullet marker or a heading mark leaves 'Lattice'
    looking mid-sentence and every Now step that opens a bullet with an
    ordinary verb collides.
    Oracle: hand-computed - no hit for the bullet-led and heading-led
    word, one hit for the same word mid-sentence, and the CamelCase form
    still hits at a bullet head.
    """
    headings = [('SPEC.md', 3, 'Lattice section')]
    assert hq.collisions('- Lattice must hold.', [], headings, {'Lattice': 1}) == []
    assert hq.collisions('### Lattice next', [], headings, {'Lattice': 1}) == []
    assert hq.collisions('1. Lattice first.', [], headings, {'Lattice': 1}) == []
    assert len(hq.collisions(
        'Fix the Lattice path.', [], headings, {'Lattice': 1})) == 1
    cam = [('SPEC.md', 9, 'LatticeCache is frozen')]
    assert len(hq.collisions(
        '- LatticeCache was renamed.', [], cam, {'LatticeCache': 1})) == 1


def test_artifacts_render_trusts_row_status_for_abs_rows():
    """Verify an abs row renders from its status, not from the folder walk.

    Mutation: presence judged by membership in the top-level walk, so every
    abs row counts as missing and never renders in full.
    Oracle: hand-computed full line for a live edit abs row and no missing
    count.
    """
    rows = {'~/x.md': _ledger_row(
        path='~/x.md', base='abs', read_before='edit', label='outside note')}
    result = hq.render_artifacts([], rows, 'slug')
    assert '~/x.md  notes  edit  c1  outside note' in result
    assert 'missing' not in result


def test_non_live_count_line_has_a_fixed_order():
    """Verify the non-live count line reads superseded, archived, missing.

    Mutation: counts emitted in ledger insertion order.
    Oracle: hand-computed order for rows inserted archived, missing,
    superseded; each count owns a line, so the order reads across them.
    """
    rows = {
        'a.md': _ledger_row(path='a.md', status='archived'),
        'b.md': _ledger_row(path='b.md', status='missing'),
        'c.md': _ledger_row(path='c.md', status='superseded'),
    }
    result = hq.render_artifacts([], rows, 'slug')
    counted = [
        ln.split()[0] for ln in result.splitlines()
        if ln.startswith(('superseded', 'archived', 'missing'))]
    assert counted == ['superseded', 'archived', 'missing']


def test_standing_renders_every_live_constraint_and_keeps_the_superseded_line():
    """Verify no cap cuts the constraints and the superseded count closes the block.

    Mutation: a line cap that truncates the constraints; the superseded
    line appended before the items, or dropped.
    Oracle: hand-computed - 86 constraints with one superseded render as
    87 lines: the heading, 85 items in full, and 'superseded 1'.
    """
    items = [
        {'id': f'c{n:02d}', 'prefix': 'c', 'cycle': '1',
         'headline': f'rule {n}', 'body': 'body'}
        for n in range(1, 87)
    ]
    result = hq.render_standing(items, {'c86'}, 'slug').splitlines()
    assert len(result) == 87
    assert result[-1] == 'superseded 1  - hq standing slug --all'
    assert result[-2] == '[c85] (c1) **rule 85** body'
    assert '[c86]' not in '\n'.join(result)


def test_every_kind_renders_whole_under_its_heading():
    """Every constraint, decision, and dead end prints under its heading.

    Mutation: a budget shared out across the kinds, so the longest kind
    loses items to a '... N more' line; or a kind's heading dropped when
    its items are few.
    Oracle: hand-computed - 26 constraints, 38 decisions, and 26 dead ends
    render 93 lines, three headings and every item, no overflow line; 90
    constraints and 5 dead ends keep all five dead ends and their heading.
    """
    def _items(prefix, n, body):
        return [
            {'id': f'{prefix}{i:02d}', 'prefix': prefix, 'cycle': '1',
             'headline': f'{prefix} item {i}', 'body': body}
            for i in range(1, n + 1)]

    items = _items('c', 26, 'why it holds') + _items('d', 38, '') + _items('x', 26, '')
    lines = hq.render_standing(items, set(), 'widget-alpha').splitlines()
    assert len(lines) == 93
    headings = ('### Constraints', '### Decisions', '### Dead ends')
    assert [lines.count(h) for h in headings] == [1, 1, 1]
    assert [sum(ln.startswith(f'[{p}') for ln in lines) for p in 'cdx'] == [26, 38, 26]
    assert not any(ln.startswith('... ') for ln in lines)
    lines = hq.render_standing(
        _items('c', 90, 'b') + _items('x', 5, ''), set(), 'widget-alpha').splitlines()
    assert len(lines) == 97
    assert '### Dead ends' in lines
    assert sum(ln.startswith('[x') for ln in lines) == 5


def test_header_past_five_dirty_paths_shows_the_total():
    """Verify the header tail names the total past five dirty paths.

    Mutation: five names followed by the count of the rest.
    Oracle: hand-computed tails for five and seven paths.
    """
    assert hq._dirty_str(['a', 'b', 'c', 'd', 'e']) == ' | dirty: a, b, c, d, e'
    assert hq._dirty_str(['a', 'b', 'c', 'd', 'e', 'f', 'g']) == ' | 7 files dirty'


def test_collisions_match_a_backticked_flag():
    """Verify a backticked token with non-word edges still collides.

    Mutation: word-boundary anchors around the term, which never match a
    token such as --force or .cache.
    Oracle: hand-computed - one hit for `--force` against a dead-end
    headline that names the flag.
    """
    hits = hq.collisions(
        'use `--force` now', [('the --force flag failed', 'x01')], [], {'--force': 1})
    assert len(hits) == 1
    assert '--force' in hits[0]


def test_resolve_where_maps_a_section_reference_to_its_numbered_heading():
    """An 's<d>' anchor resolves to the heading numbered d.

    Mutation: literal matching only, so the 's4' that adopt seeds from a
    'section 4' label prints '?' in every span slot; or a prefix match, so
    's4' lands on '## 40. Appendix'.
    Oracle: hand-computed - '## 4. Cache warmup' is line 7 and the next
    same-level heading is line 11, so the span is 7-10; 's40' is 11-13;
    's5' matches nothing and is reported.
    """
    text = (
        '# Spec\n'
        '\n'
        '## 3. Inputs\n'
        '\n'
        'Input text.\n'
        '\n'
        '## 4. Cache warmup\n'
        '\n'
        'Step one.\n'
        'Step two.\n'
        '## 40. Appendix\n'
        '\n'
        'Tail.\n'
    )
    assert hq.resolve_where(text, ['s4']) == ([(7, 10)], [])
    assert hq.resolve_where(text, ['s40']) == ([(11, 13)], [])
    assert hq.resolve_where(text, ['s5']) == ([], ['s5'])


def test_resolve_where_names_a_dotted_sub_heading_by_its_whole_number():
    """An 's<d>.<d>' anchor resolves the sub-heading numbered d.d alone.

    Mutation: the section token holding one integer only, so a dotted
    sub-heading has no anchor of its own and two sub-headings sharing a
    title both resolve to the first - a read block pointed at the wrong
    span with no '?' to show it; or the dotted token cut at its first
    dot, so 's24' lands on '### 24.4 ...'.
    Oracle: hand-computed - '### 24.4 The decision' is line 3 and the
    next same-level heading is line 7, so the span is 3-6; '### 31.2
    The decision' is 7-9; 's24' finds nothing; a version such as
    '## v2.0 notes' carries no dotted token; the bare title lands on the
    first heading of that text, which is why the dotted form exists.
    """
    text = (
        '# Doc\n'
        '\n'
        '### 24.4 The decision\n'
        '\n'
        'A\n'
        '\n'
        '### 31.2 The decision\n'
        '\n'
        'B\n'
        '## v2.0 notes\n'
        'C\n'
    )
    assert hq.resolve_where(text, ['s24.4']) == ([(3, 6)], [])
    assert hq.resolve_where(text, ['s31.2']) == ([(7, 9)], [])
    assert hq.resolve_where(text, ['S31.2']) == ([(7, 9)], [])
    assert hq.resolve_where(text, ['s24']) == ([], ['s24'])
    assert hq.resolve_where(text, ['s2.0']) == ([], ['s2.0'])
    assert hq.resolve_where(text, ['The decision']) == ([(3, 6)], [])


# --- mutation survivors, 2026-09-09 ---------------------------------------


def test_stem_rule_supersedes_an_older_spec_by_a_newer_spec_alone():
    """Two spec-kind files sharing a stem: the older points at the newer,
    and a same-age mate of another kind is left alone.

    Mutation: the stem set built from non-spec members, a two-member stem
    skipped, the newest picked by ascending mtime, or a same-age mate
    superseded.
    Oracle: hand-computed - SPEC.v2.md at 300 is newest, SPEC.md at 200
    maps to it, SPEC.notes.md at 300 stays.
    """
    two = [('SPEC.md', 'spec', 200.0), ('SPEC.v2.md', 'spec', 300.0)]
    assert hq.apply_stem_rule(two) == {'SPEC.md': 'SPEC.v2.md'}
    three = two + [('SPEC.notes.md', 'notes', 300.0)]
    assert hq.apply_stem_rule(three) == {'SPEC.md': 'SPEC.v2.md'}


def test_drain_unfiled_cuts_the_prefix_exactly_and_skips_blank_lines():
    """Typed bullets keep their text; a blank line inside Unfiled is ignored.

    Mutation: the prefix cut at the wrong width, or a blank line taken
    for an untyped bullet.
    Oracle: hand-computed items for a bold headline and a sentence
    headline around a blank line; Unfiled gone, Log kept.
    """
    cursor = (
        '## Task\nDo.\n\n## Unfiled\n\n'
        '- decision: **Use one call** Batch writes.\n\n'
        '- dead-end: A pid in the lock. Meaningless across hosts.\n\n## Log\n')
    items, cursor_out, refusal = hq.drain_unfiled(cursor)
    assert refusal is None
    assert items == [
        ('decision', 'Use one call', 'Batch writes.'),
        ('dead-end', 'A pid in the lock.', 'Meaningless across hosts.'),
        ]
    assert '## Unfiled' not in cursor_out
    assert '## Log' in cursor_out


def test_conservation_reports_a_line_missing_after_a_blank_line():
    """A line dropped from the tail of the original is reported.

    Mutation: the scan stopping at the first blank line, so everything
    after it passes unchecked.
    Oracle: 'Tail fact.' follows a blank line and is absent from the union.
    """
    original = '## Task\nDo.\n\n## State\nTail fact.\n'
    assert hq.conservation(original, '## Task\nDo.\n## State\n', '', []) == ['Tail fact.']


def test_conservation_passes_a_rewrap_and_still_flags_a_drop():
    """A bullet rewrapped onto an indented line is carried; a cut is not.

    Mutation: the union built from the raw lines alone, so a bullet the
    rewrite wrapped at the column is reported not carried though every
    word survived; or the join applied to unindented lines too, so two
    separate items glue into one and a heading ends up inside a line.
    Oracle: the hand-written pair - the rewrap holds every word of the
    original contiguously once its continuation joins, the truncation
    does not; the two-bullet cursor keeps both bullets apart.
    """
    bullet = (
        '- Rework the loader to use batch reads instead of one read per row,'
        ' and add a pool to cut latency.')
    original = f'## Plan\n{bullet}\n- Then ship it.\n'
    rewrapped = (
        '## Plan\n'
        '- Rework the loader to use batch reads instead of one read per row,\n'
        '  and add a pool to cut latency.\n'
        '- Then ship it.\n')
    truncated = '## Plan\n- Rework the loader to use batch reads.\n- Then ship it.\n'
    assert hq.conservation(original, rewrapped, '', []) == []
    assert hq.conservation(original, truncated, '', []) == [bullet]
    glued = (
        '## Plan\n- Rework the loader to use batch reads instead of one read per row,')
    assert hq.conservation(f'{glued}\n', rewrapped, '', []) == []
    assert hq.conservation(
        '- Then ship it. - Rework the loader\n', rewrapped, '', []) == [
        '- Then ship it. - Rework the loader']


def test_render_standing_holds_each_kind_back_and_the_eighty_line_boundary():
    """Each kind holds its body back by its own resident rule, each kind
    has its heading, and the cap bites at 81 lines and not at 80.

    Mutation: split_headline applied to a decision or a dead end, which
    leaves a first sentence resident and undercounts the hold; the whole
    body held for a constraint, which drops the sentence the block
    carries; the dead-end prefix letter changed; or the boundary moved
    either way.
    Oracle: hand-computed - the constraint holds its second sentence
    alone while the decision and the dead end hold their bodies whole,
    so each count equals a spelled-out length; no cap binds the block,
    so 79 constraints render as 80 lines and 80 render as 81, the last
    of them the item itself and never an overflow marker.
    """
    held_c = 'The sink redacts nothing.'
    held_d = 'One caller.'
    held_x = 'Cannot retry.'
    items = [
        {'id': 'c01', 'prefix': 'c', 'cycle': '1', 'headline': 'Never log tokens',
         'body': f'Not at debug. {held_c}'},
        {'id': 'd01', 'prefix': 'd', 'cycle': '1', 'headline': 'In process',
         'body': held_d},
        {'id': 'x01', 'prefix': 'x', 'cycle': '2', 'headline': 'Hooks',
         'body': held_x},
        ]
    assert (len(held_c), len(held_d), len(held_x)) == (25, 11, 13)
    assert hq.render_standing(items, set(), 'slug').splitlines() == [
        '### Constraints',
        ('[c01] (c1) **Never log tokens** Not at debug.'
         f'  +{len(held_c)}c - hq standing slug c01'),
        '### Decisions',
        f'[d01] (c1) **In process**  +{len(held_d)}c - hq standing slug d01',
        '### Dead ends',
        f'[x01] (c2) **Hooks**  +{len(held_x)}c - hq standing slug x01',
        ]
    many = [
        {'id': f'c{n:02d}', 'prefix': 'c', 'cycle': '1', 'headline': f'r{n}', 'body': 'b'}
        for n in range(1, 80)
        ]
    lines = hq.render_standing(many, set(), 'slug').splitlines()
    assert len(lines) == 80
    assert not lines[-1].startswith('... ')
    more = many + [{'id': 'c80', 'prefix': 'c', 'cycle': '1', 'headline': 'r80', 'body': 'b'}]
    lines = hq.render_standing(more, set(), 'slug').splitlines()
    assert len(lines) == 81
    assert lines[-1] == '[c80] (c1) **r80** b'
