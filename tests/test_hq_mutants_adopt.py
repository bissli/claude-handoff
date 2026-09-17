"""Mutation tests for _verb_adopt, _pointer_path, _walk_folder,
conservation, _parse_standing, and split_handoff.

Each test targets one or more surviving mutants from the run4-group-adopt
batch. Mandatory classes: logic and number. String and none classes are
counted at the end of this module.
"""

import pathlib

from bin import hq

_SLUG = 'mut-adopt'
_SESSION = 'session-mut'
_NOW = '2026-09-01T12:00:00'

_CURSOR = (
    '## Task\n\nDo the task.\n\n'
    '## Now\n\nNext step.\n\n'
    '## Plan\n\nPlan line.\n\n'
    '## State\n\nState line.\n\n'
    '## Environment\n\nEnv line.\n\n'
    '## Open questions\n\nNone.\n'
)


def _root(tmp_path, monkeypatch, slug=_SLUG, cycle=None):
    """Create HQ_ROOT with an empty handoff folder and set the env."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    if cycle is None:
        monkeypatch.delenv('HQ_CYCLE', raising=False)
    else:
        monkeypatch.setenv('HQ_CYCLE', cycle)
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _handoff(folder, key_files='', cycle=3, extra=''):
    """Write a conforming HANDOFF.md with an optional Key files section."""
    text = (
        f'# Handoff: {folder.name}\n\n'
        f'Written: 2026-09-01 | Cycle: {cycle}\n\n'
        + _CURSOR
    )
    if key_files:
        text += '\n## Key files\n\n' + key_files
    text += extra
    (folder / 'HANDOFF.md').write_text(text, encoding='utf-8')
    return text


def _ledger(folder):
    """Return the ledger rows as a list of dicts."""
    return hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)


def _manifest(folder):
    """Return the manifest rows as a list of dicts."""
    return hq._read_tsv(
        folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)


# --- split_handoff ---------------------------------------------------


def test_split_handoff_keys_are_lowercase():
    """split_handoff returns a dict with the six expected lowercase keys.

    Mutation: 'header', 'slug', 'cursor', or 'log' key changed to uppercase
    (mutmut_3, 6, 11, 16), or the slug written to 'SLUG' (mutmut_47),
    or the log written to 'LOG' (mutmut_86).
    Oracle: hand-checked key presence and slug value from a conforming text.
    """
    text = (
        '# Handoff: test-slug\n\n'
        'Written: 2026-09-01 | Cycle: 3\n\n'
        '## Task\n\nDo it.\n'
    )
    result = hq.split_handoff(text)
    assert set(result.keys()) == {'header', 'slug', 'cycle', 'cursor', 'blocks', 'log'}
    assert result['slug'] == 'test-slug'
    assert result['cycle'] == 3
    assert result['header'].startswith('Written:')


def test_split_handoff_slug_is_case_sensitive():
    """Title regex uses exact 'Handoff' casing; lowercase or uppercase miss.

    Mutation: pattern changed to '# handoff:' (mutmut_43) or '# HANDOFF:'
    (mutmut_44), so a conforming title no longer matches and slug is empty.
    Oracle: slug equals 'demo' from '# Handoff: demo'.
    """
    text = '# Handoff: demo\n\nWritten: 2026-01-01 | Cycle: 1\n\n## Task\n'
    result = hq.split_handoff(text)
    assert result['slug'] == 'demo'


def test_split_handoff_blocks_parsed_in_order():
    """block_open and block_close search from pos; each block found once.

    Mutation: pos dropped from block_open.search (mutmut_94), causing an
    infinite loop even for one block; or pos dropped from block_close.search
    (mutmut_104), causing an infinite loop with two blocks when the first
    close is re-found before the second open.
    Oracle: two distinct named blocks each have correct body; a 2-second
    timeout guards against infinite loops introduced by either mutant.
    """
    import signal

    text = (
        '# Handoff: s\n\nWritten: 2026-01-01 | Cycle: 1\n\n'
        '## Task\n\nDo it.\n\n'
        '<!-- hq:alpha a -->\nalpha body\n<!-- /hq:alpha -->\n'
        '<!-- hq:beta b -->\nbeta body\n<!-- /hq:beta -->\n'
    )

    def _alarm(signum, frame):
        raise TimeoutError('split_handoff timed out - mutmut_94 or mutmut_104 likely')

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(2)
    try:
        result = hq.split_handoff(text)
    finally:
        signal.alarm(0)
    assert result['blocks'].get('alpha') == 'alpha body'
    assert result['blocks'].get('beta') == 'beta body'


def test_split_handoff_log_captured_after_log_heading():
    """Text after ## Log is stored in the 'log' key.

    Mutation: 'log' key written as 'LOG' (mutmut_16 or 86) so callers
    reading result['log'] get the initial empty string.
    Oracle: log contains the text after '## Log'.
    """
    text = (
        '# Handoff: s\n\nWritten: 2026-01-01 | Cycle: 2\n\n'
        '## Task\n\nWork.\n\n'
        '## Log\n\n- 2026-01-01: started\n'
    )
    result = hq.split_handoff(text)
    assert '2026-01-01: started' in result['log']


# --- conservation ----------------------------------------------------


def test_conservation_indented_heading_covered_by_content():
    """An indented heading is covered when its content line is in the union.

    Mutation: lstrip changed to rstrip (mutmut_76 in the section-content
    builder, mutmut_108 in the result loop).
    - mutmut_76: the indented heading is not recorded in section_content, so
      _heading_covered returns False and the heading is reported missing.
    - mutmut_108: the indented heading is not detected in the result loop,
      falls through to the `norm in union_text` check; the heading text is
      not in the union so it is reported missing.
    Oracle: an original with '  ## Unique heading ABCDEF' and a cursor
    carrying only 'Covered content line ZZZZ.' (not the heading text)
    reports no missing lines under correct code.
    """
    original = (
        '# Handoff: s\n\n'
        'Written: 2026-01-01 | Cycle: 1\n\n'
        '  ## Unique heading ABCDEF\n\n'
        'Covered content line ZZZZ.\n'
    )
    cursor = '# Handoff: s\n\n## Task\n\nWork.\n\nCovered content line ZZZZ.'
    missing = hq.conservation(original, cursor, '', [])
    assert missing == []


# --- _walk_folder ---------------------------------------------------


def test_walk_folder_regular_file_and_dir_not_skipped():
    """Regular files and directories get inferred kinds, not 'skip'.

    Mutation: condition changed to `is_dir and not entry.is_file()`
    (mutmut_11) - marks directories as skip; or to
    `not is_dir and entry.is_file()` (mutmut_12) - marks regular files as skip.
    Oracle: spec.md has a spec kind; subdir has probe-dir kind; neither is skip.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp)
        (folder / 'spec.md').write_text('# Spec\n', encoding='utf-8')
        (folder / 'subdir').mkdir()
        results = dict(hq._walk_folder(folder))
        assert results['spec.md'] == 'spec'
        assert results['subdir'] == 'probe-dir'


def test_walk_folder_continue_after_skip_name():
    """Continue after a SKIP_NAME keeps the loop alive; break stops it.

    Mutation: continue changed to break for the dotfile/SKIP_NAME branch
    (mutmut_16 and mutmut_23 as applied by apply_mutant.py - both change
    the same `continue` at the dotfile/SKIP_NAME check). A SKIP_NAME file
    sorting before a regular file causes the loop to stop early.
    Oracle: a folder with 'HANDOFF.md' (a SKIP_NAME, sorts before 'z')
    and 'zzz-spec.md'; only zzz-spec.md appears in results (HANDOFF.md
    is skipped by the SKIP_NAMES guard, and the loop continues to zzz-spec.md).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp)
        (folder / 'HANDOFF.md').write_text('# Handoff: x\n', encoding='utf-8')
        (folder / 'zzz-spec.md').write_text('# Spec\n', encoding='utf-8')
        results = dict(hq._walk_folder(folder))
        assert 'zzz-spec.md' in results
        assert 'HANDOFF.md' not in results


# --- _parse_standing ------------------------------------------------


def test_parse_standing_break_on_non_bullet_stops_early():
    """Non-bullet lines are skipped (continue); break would stop parsing.

    Mutation: continue changed to break (mutmut_7) when a line does not
    start with '- ', so items after any blank or heading line are lost.
    Oracle: two items separated by a blank line; both appear in output.
    """
    text = (
        '- [d01] (c1) **First decision.** Body one.\n'
        '\n'
        '- [d02] (c1) **Second decision.** Body two.\n'
    )
    items, _ = hq._parse_standing(text)
    ids = [it['id'] for it in items]
    assert 'd01' in ids
    assert 'd02' in ids


def test_parse_standing_superseded_continue_processes_rest():
    """After a superseded line, continue keeps parsing further items.

    Mutation: continue changed to break (mutmut_20) after adding a
    superseded id, so items after the first superseded line are lost.
    Oracle: a superseded line followed by two items; both items present.
    """
    text = (
        '- (c1) d01 -> d02\n'
        '- [d02] (c1) **New decision.** Replaces first.\n'
        '- [c01] (c1) **Constraint one.** Always.\n'
    )
    items, superseded = hq._parse_standing(text)
    assert 'd01' in superseded
    ids = [it['id'] for it in items]
    assert 'd02' in ids
    assert 'c01' in ids


def test_parse_standing_cycle_field_uses_group_2():
    """Cycle comes from group(2) of the id regex, not group(3).

    Mutation: group(2) changed to group(3) (mutmut_43), which returns the
    headline instead of the cycle number; or or/and flipped (mutmut_41),
    which always returns '' even when the cycle matched.
    Oracle: an item with (c5) carries cycle='5', not the headline text.
    """
    text = '- [d01] (c5) **My headline.** Body text.\n'
    items, _ = hq._parse_standing(text)
    assert len(items) == 1
    assert items[0]['cycle'] == '5'
    assert items[0]['headline'] == 'My headline.'


# --- _pointer_path --------------------------------------------------


def test_pointer_path_returns_early_when_exists():
    """_pointer_path returns the folder's copy when the file exists there.

    Mutation: `or` changed to `and` (mutmut_6), so an existing relative
    path is not returned early; the search loop finds a decoy in the
    grandparent and returns base='abs' instead of base='folder'.
    Oracle: a decoy with the same name in the grandparent is never
    returned when the folder already has the file.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp) / '.handoff' / 'demo'
        folder.mkdir(parents=True)
        # Decoy in grandparent (folder.parent.parent = tmp); the search
        # loop would find this under mutmut_6.
        (pathlib.Path(tmp) / 'SPEC.md').write_text('decoy', encoding='utf-8')
        target = folder / 'SPEC.md'
        target.write_text('# Spec\n', encoding='utf-8')
        stored, path_obj, base = hq._pointer_path(folder, 'SPEC.md')
        assert stored == 'SPEC.md'
        assert base == 'folder'
        assert path_obj == target


# --- _verb_adopt (return code) ---------------------------------------


def test_adopt_returns_1_when_handoff_missing(tmp_path, monkeypatch):
    """Adopt exits 1 when HANDOFF.md is absent, not 2.

    Mutation: return 1 changed to return 2 (mutmut_13) for the missing
    HANDOFF.md case.
    Oracle: hq.main(['adopt', slug]) == 1.
    """
    _root(tmp_path, monkeypatch)
    assert hq.main(['adopt', _SLUG]) == 1


def test_adopt_returns_0_and_creates_ledger(tmp_path, monkeypatch):
    """Adopt exits 0 and creates ledger.tsv from a conforming HANDOFF.md.

    Mutation: any mutation that prevents ledger.tsv creation or produces
    a non-zero return code on a normal adopt call.
    Oracle: return code 0, ledger.tsv exists with at least a header row.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    assert (folder / 'ledger.tsv').exists()


# --- _verb_adopt (ledger row keys and values) -------------------------


def test_adopt_ledger_row_has_lowercase_keys_and_correct_values(
        tmp_path, monkeypatch):
    """Ledger rows use lowercase key names and correct string values.

    Mutation: any of cycle, ts, path, base, kind, status, read_before,
    successor, where, sha12, lines, reason, or label key set to uppercase
    (mutmut_421,423,427,429,431,437,441,443,445); or status='LIVE' instead
    of 'live' (mutmut_382); or read_before='NEVER' or 'ALWAYS' instead of
    lowercase (mutmut_379,396,473,480); or rb changed to 'always' for a
    non-spec/draft kind (mutmut_373); or filter inverted for skip
    (mutmut_89,91); or successor check inverted (mutmut_389).
    Oracle: a spec file row has status='live', read_before='always',
    base='folder'; a notes file row has status='live', read_before='never'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'notes.md').write_text('Notes text.\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'SPEC.md' in rows
    spec = rows['SPEC.md']
    assert spec['status'] == 'live'
    assert spec['read_before'] == 'always'
    assert spec['base'] == 'folder'
    assert spec['kind'] == 'spec'
    assert spec['successor'] == '-'
    assert spec['reason'] == '-'
    notes = rows['notes.md']
    assert notes['status'] == 'live'
    assert notes['read_before'] == 'never'


def test_adopt_spec_and_draft_get_always_others_get_never(
        tmp_path, monkeypatch):
    """Spec and draft kinds get read_before=always; others get never.

    Mutation: condition inverted so non-spec/draft get always (mutmut_373);
    or 'always' changed to 'ALWAYS' (mutmut_372,557); or 'never' changed
    to 'NEVER' (mutmut_379,396); or kind membership check uses wrong case
    (mutmut_375,377,560,562).
    Oracle: spec row has always; a .json (other kind) has never.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'data.json').write_text('{}', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['read_before'] == 'always'
    assert rows['data.json']['read_before'] == 'never'


def test_adopt_superseded_file_gets_never_rb(tmp_path, monkeypatch):
    """A snapshot older than the spec gets status=superseded and read_before=never.

    Mutation: successor check inverted (mutmut_389) so live files become
    superseded; or 'superseded' changed to 'SUPERSEDED' (mutmut_393);
    or 'never' changed to 'NEVER' (mutmut_396) in the superseded branch.
    Oracle: SPEC.md.pre-2 is older than SPEC.md; the pre-2 row has
    status=superseded, read_before=never, successor=SPEC.md.
    """
    import time
    folder = _root(tmp_path, monkeypatch)
    pre = folder / 'SPEC.md.pre-2'
    pre.write_text('# Old Spec\n\nOld content.\n', encoding='utf-8')
    time.sleep(0.01)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md.pre-2']['status'] == 'superseded'
    assert rows['SPEC.md.pre-2']['read_before'] == 'never'
    assert rows['SPEC.md.pre-2']['successor'] == 'SPEC.md'
    assert rows['SPEC.md']['status'] == 'live'


def test_adopt_kf_rb_edit_applies_only_to_notes_kind(tmp_path, monkeypatch):
    """'Reference only' sets rb=edit for notes files, never for specs.

    Mutation: condition `kf_rb != 'edit' or kind == 'notes'` inverted
    (mutmut_412) so edit applies to specs; or 'edit' changed to 'EDIT'
    (mutmut_414) so the check never matches and edit is always applied.
    Oracle: a notes-bg.md file (notes kind) gets edit; a spec file ignores
    the edit override and keeps always.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'notes-bg.md').write_text('Background notes.\n', encoding='utf-8')
    _handoff(folder, key_files=(
        '- Reference only:\n'
        '  - `SPEC.md` the spec\n'
        '  - `notes-bg.md` background notes\n'
    ))
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['read_before'] == 'always'
    assert rows['notes-bg.md']['read_before'] == 'edit'


def test_adopt_skip_entries_excluded_from_ledger(tmp_path, monkeypatch):
    """Skip entries are not seeded in the ledger.

    Mutation: `k != 'skip'` changed to `k == 'skip'` (mutmut_89), which
    reverses the filter and processes only skip entries; or to
    `k != 'SKIP'` (mutmut_91), which always passes (since no kind is
    'SKIP'), letting skip entries through.
    Oracle: a conflicted-copy file creates no ledger row; a regular file does.

    Notes
    -----
    - A conflicted copy is used instead of a FIFO to avoid blocking on sha12
      when the skip check is bypassed (FIFO reads block until a writer joins).
    """
    folder = _root(tmp_path, monkeypatch)
    cc = folder / 'SPEC (conflicted copy 2023-01-01).md'
    cc.write_text('# Spec\n\nOld.\n', encoding='utf-8')
    (folder / 'data.json').write_text('{}', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'SPEC (conflicted copy 2023-01-01).md' not in rows
    assert 'data.json' in rows


def test_adopt_conflicted_copy_message(tmp_path, monkeypatch, capsys):
    """A file with 'conflicted copy' in its name prints the right message.

    Mutation: `'conflicted copy' in name` changed to
    `'CONFLICTED COPY' in name` (mutmut_356) so the wrong branch runs and
    'unstampable name' prints instead; or inverted (mutmut_357) so a
    conflicted file prints 'unstampable name'.
    Oracle: capsys.out contains 'skipped conflicted copy' for a file named
    with 'conflicted copy'.
    """
    folder = _root(tmp_path, monkeypatch)
    conflict = folder / 'SPEC (conflicted copy 2026-09-01).md'
    conflict.write_text('# Spec\n\nOld.\n', encoding='utf-8')
    _handoff(folder)
    hq.main(['adopt', _SLUG])
    out = capsys.readouterr().out
    assert 'skipped conflicted copy' in out
    assert 'unstampable name' not in out


def test_adopt_skip_continue_not_break(tmp_path, monkeypatch):
    """After a skip entry, the loop continues to process later entries.

    Mutation: continue changed to break (mutmut_360) after a skip entry,
    stopping the walk so subsequent files are not seeded.
    Oracle: a file sorted after a skip-kind file appears in the ledger.

    Notes
    -----
    - A conflicted copy is used instead of a FIFO to avoid blocking on sha12
      when the skip check is bypassed (FIFO reads block until a writer joins).
    """
    folder = _root(tmp_path, monkeypatch)
    cc = folder / 'aaa (conflicted copy 2023-01-01).md'
    cc.write_text('# old\n\nContent.\n', encoding='utf-8')
    (folder / 'zzz-data.json').write_text('{}', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'zzz-data.json' in rows


# --- _verb_adopt (cycle and prev archiving) ---------------------------


def test_adopt_archives_prev_only_when_cycle_gt_1(tmp_path, monkeypatch):
    """HANDOFF.prev.md is archived only when cycle > 1, not >= 1.

    Mutation: `cycle > 1` changed to `cycle >= 1` (mutmut_67), so at
    cycle=1 the prev file is archived even though there is no prior cycle;
    or to `cycle > 2` (mutmut_68), so at cycle=2 the prev is not archived.
    Oracle: at cycle=1 with a prev file, no c00.md in cycles/;
    at cycle=2 with a prev file, c01.md exists.
    """
    folder = _root(tmp_path, monkeypatch)
    prev = folder / 'HANDOFF.prev.md'
    prev.write_text(
        '# Handoff: old\n\nWritten: 2026-01-01 | Cycle: 1\n\n## Task\n\nOld.\n',
        encoding='utf-8')
    _handoff(folder, cycle=1)
    assert hq.main(['adopt', _SLUG]) == 0
    assert not (folder / 'cycles' / 'c00.md').exists()

    folder2 = _root(tmp_path, monkeypatch, slug='mut-adopt2')
    prev2 = folder2 / 'HANDOFF.prev.md'
    prev2.write_text(
        '# Handoff: old2\n\nWritten: 2026-01-01 | Cycle: 1\n\n## Task\n\nOld.\n',
        encoding='utf-8')
    _handoff(folder2, cycle=2)
    assert hq.main(['adopt', 'mut-adopt2']) == 0
    assert (folder2 / 'cycles' / 'c01.md').exists()


def test_adopt_prev_archived_to_correct_cycle_number(tmp_path, monkeypatch):
    """HANDOFF.prev.md is archived to c{cycle-1:02d}.md, not cycle+1 or cycle-2.

    Mutation: `cycle - 1` changed to `cycle + 1` (mutmut_71) or to
    `cycle - 2` (mutmut_72), putting the archive in the wrong file.
    Oracle: at cycle=3, c02.md exists; c04.md and c01.md do not (from prev).
    """
    folder = _root(tmp_path, monkeypatch)
    prev = folder / 'HANDOFF.prev.md'
    prev.write_text(
        '# Handoff: prev-demo\n\nWritten: 2026-01-01 | Cycle: 2\n\n## Task\n\nPrev.\n',
        encoding='utf-8')
    _handoff(folder, cycle=3)
    assert hq.main(['adopt', _SLUG]) == 0
    cycles_dir = folder / 'cycles'
    assert (cycles_dir / 'c02.md').exists()
    assert not (cycles_dir / 'c04.md').exists()
    assert not (cycles_dir / 'c01.md').exists()


def test_adopt_not_overwrite_existing_prev_archive(tmp_path, monkeypatch):
    """Prev archive is not written when c{cycle-1:02d}.md already exists.

    Mutation: `if not prev_arch.exists()` changed to `if prev_arch.exists()`
    (mutmut_73), so the existing archive is overwritten.
    Oracle: pre-create c02.md with sentinel text; after adopt at cycle=3
    the sentinel text remains.
    """
    folder = _root(tmp_path, monkeypatch)
    cycles_dir = folder / 'cycles'
    cycles_dir.mkdir()
    sentinel = '# Handoff: sentinel\n\nWritten: 2026-01-01 | Cycle: 2\n'
    (cycles_dir / 'c02.md').write_text(sentinel, encoding='utf-8')
    prev = folder / 'HANDOFF.prev.md'
    prev.write_text(
        '# Handoff: new-prev\n\nWritten: 2026-09-01 | Cycle: 2\n\n## Task\n\nNew.\n',
        encoding='utf-8')
    _handoff(folder, cycle=3)
    assert hq.main(['adopt', _SLUG]) == 0
    assert (cycles_dir / 'c02.md').read_text(encoding='utf-8') == sentinel


def test_adopt_cycles_dir_exist_ok(tmp_path, monkeypatch):
    """cycles/ is created with exist_ok=True; pre-existing dir is not an error.

    Mutation: exist_ok=True changed to exist_ok=False (mutmut_51), so
    a pre-existing cycles/ directory raises FileExistsError.
    Oracle: adopt succeeds when cycles/ already exists before the call.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'cycles').mkdir()
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0


def test_adopt_prev_path_filename_is_uppercase(tmp_path, monkeypatch):
    """HANDOFF.prev.md uses the exact case; lowercase variants are not found.

    Mutation: 'HANDOFF.prev.md' changed to 'handoff.prev.md' (mutmut_64)
    or 'HANDOFF.PREV.MD' (mutmut_65), so the prev file is not detected.
    Oracle: create HANDOFF.prev.md at cycle=3; c02.md appears after adopt.
    """
    folder = _root(tmp_path, monkeypatch)
    prev = folder / 'HANDOFF.prev.md'
    prev.write_text(
        '# Handoff: prev\n\nWritten: 2026-01-01 | Cycle: 2\n\n## Task\n\nOld.\n',
        encoding='utf-8')
    _handoff(folder, cycle=3)
    assert hq.main(['adopt', _SLUG]) == 0
    assert (folder / 'cycles' / 'c02.md').exists()


# --- _verb_adopt (manifest row keys and values) ----------------------


def test_adopt_manifest_row_has_lowercase_keys_and_correct_date(
        tmp_path, monkeypatch):
    """Manifest row keys are lowercase; written is the archive's YYYY-MM-DD.

    Mutation: manifest row keys written as 'CYCLE', 'WRITTEN', 'LOG',
    'NOTE' etc. (mutmut_888,893,949,952), so the column reads `-`;
    written taken from the adopt timestamp rather than the archived
    header; or the provenance date cut as ts[:11] instead of ts[:10]
    (mutmut_950), giving 'adopted YYYY-MM-DDT by'.
    Oracle: a header dated 2026-08-22 adopted at HQ_NOW 2026-09-01 gives
    written '2026-08-22', cycle '3', session '-', and the note
    `adopted 2026-09-01 by <session>`.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\nWritten: 2026-08-22 | Cycle: 3\n\n' + _CURSOR,
        encoding='utf-8')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _manifest(folder)
    assert len(rows) == 1
    row = rows[0]
    assert row['written'] == '2026-08-22'
    assert row['cycle'] == '3'
    assert row['session'] == '-'
    assert row['note'] == f'adopted 2026-09-01 by {_SESSION}'
    assert row['log'].startswith('adopted')


def test_adopt_payload_tokens_uses_integer_division(tmp_path, monkeypatch):
    """payload_tokens is len(archive) // 4 (int), not / 4 (float).

    Mutation: `// 4` changed to `/ 4` (mutmut_943), producing a float like
    '512.0' instead of '512'; or to `// 5` (mutmut_944), underestimating
    by 20 percent; or the count taken on the rewritten HANDOFF.md, which
    the row does not index.
    Oracle: payload_tokens is a string of digits with no decimal point;
    its value equals len(cycles/c03.md) // 4, and the rewrite differs.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    manifest_rows = _manifest(folder)
    token_str = manifest_rows[0]['payload_tokens']
    assert '.' not in token_str
    archive_text = (folder / 'cycles' / 'c03.md').read_text(encoding='utf-8')
    handoff_text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    assert int(token_str) == len(archive_text) // 4
    assert len(archive_text) // 4 != len(handoff_text) // 4


def test_adopt_anchor_cycle_uses_parsed_cycle(tmp_path, monkeypatch):
    """The anchor cycle is set from the parsed handoff header, not default.

    Mutation: `anch['cycle'] = cycle` written to `anch['CYCLE'] = cycle`
    (mutmut_136), leaving anch['cycle'] at the env default while standing
    items carry cycle '0' or a stale value.
    Oracle: standing items seeded from a cycle=5 handoff carry (c5).
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, cycle=5, extra=(
        '\n## Decisions\n\n'
        '- **Keep the floor.** Never below one.\n'
    ))
    assert hq.main(['adopt', _SLUG]) == 0
    standing = (folder / 'standing.md').read_text(encoding='utf-8')
    assert '(c5)' in standing


def test_adopt_branch_and_sha_in_header_line(tmp_path, monkeypatch):
    """Branch and sha are read from anch['branch'] and anch['sha'].

    Mutation: `anch['branch']` read as `anch['BRANCH']` (mutmut_861) or
    `anch['sha']` read as `anch['SHA']` (mutmut_863), producing KeyError
    or an empty repos string.
    Oracle: with HQ_GIT=0, the adopted HANDOFF.md header contains no @ sign
    (repos is '-'); no KeyError is raised.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    header = [ln for ln in text.splitlines() if ln.startswith('Written:')][0]
    assert '2026-09-01' in header


# --- _verb_adopt (rb_count and gated logic) ---------------------------


def test_adopt_rb_count_correct_for_one_and_two_files(tmp_path, monkeypatch,
                                                      capsys):
    """rb_count increments by 1 per file, starting from 0.

    Mutation: `+= 1` changed to `-= 1` (mutmut_496,652) so count decreases;
    or get default 0 changed to 1 (mutmut_503,659) so each first entry
    starts at 2; or `+= 1` changed to `+= 2` (mutmut_504,660) doubling counts.
    Oracle: two spec files give read_before=always: 2; no others.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'DESIGN.md').write_text('# Design\n\nContent.\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'read_before=always: 2' in out


def test_adopt_gated_check_uses_live_and_gate_rb(tmp_path, monkeypatch,
                                                 capsys):
    """A file is gated only when status=live and read_before in _GATE_RB.

    Mutation: `and` changed to `or` (mutmut_505,661) - all live or all
    gated-rb files counted; `==` changed to `!=` (mutmut_508,664) - only
    non-live files gated; 'live' changed to 'LIVE' (mutmut_510,666) - never
    matches; `in` changed to `not in` (mutmut_513,669) - never-rb gated.
    Oracle: one spec file (always=gated); gated (1) appears in output.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'gated (1)' in out


def test_adopt_gated_names_shows_at_most_five(tmp_path, monkeypatch, capsys):
    """Gated names are truncated to the first 5 in the summary.

    Mutation: `gated_names[:5]` changed to `gated_names[:6]` (mutmut_1007),
    so the sixth gated name appears in output even when there are exactly 5.
    Oracle: with exactly 5 gated files, the gated line shows all 5 without
    a 6th; with 6 files, only 5 appear.
    """
    folder = _root(tmp_path, monkeypatch)
    spec_names = [f'SPEC{i}.md' for i in range(1, 7)]
    for name in spec_names:
        (folder / name).write_text(f'# Spec {name}\n\nContent.\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    gated_line = [ln for ln in out.splitlines() if ln.startswith('  gated')]
    assert gated_line
    names_in_line = gated_line[0].split(': ', 1)[1]
    count = names_in_line.count(',') + 1
    assert count == 5


def test_adopt_never_cnt_key_is_lowercase(tmp_path, monkeypatch):
    """never_cnt uses rb_count.get('never', 0) with a lowercase key.

    Mutation: 'never' changed to 'NEVER' (mutmut_997), or get key changed
    to integer 0 (mutmut_994), or default changed to 1 (mutmut_998), or
    default argument dropped (mutmut_995). These produce a wrong count but
    adopt still exits 0; the manifest log remains 'adopted'.
    Oracle: adopt exits 0 and the manifest log is 'adopted' - proving no
    exception from a bad get call (get(0) would return None, not raise).
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'data.json').write_text('{}', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    manifest_rows = _manifest(folder)
    assert manifest_rows[0]['log'] == 'adopted'


# --- _verb_adopt (Unfiled and section filtering) ----------------------


def test_adopt_unfiled_section_case_exact(tmp_path, monkeypatch):
    """## Unfiled uses exact title case, not unfiled or UNFILED.

    Mutation: '## Unfiled' changed to '## unfiled' (mutmut_835) or
    '## UNFILED' (mutmut_836) in the cursor; or 'Unfiled' section
    recognized by 'unfiled' (mutmut_800) or 'UNFILED' (mutmut_801),
    dropping its content into the loop body.
    Oracle: an original with ## Unfiled body ends up under ## Unfiled
    in the adopted HANDOFF.md (not merged into another section).
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra='\n## Unfiled\n\n- loose line\n')
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    assert '## Unfiled' in text


def test_adopt_known_sections_skipped_in_unfiled_loop(tmp_path, monkeypatch):
    """Log, Read first, Artifacts, Standing sections do not become Unfiled.

    Mutation: any of the section names changed to wrong case (mutmut_819-829):
    'log', 'LOG', 'read first', 'READ FIRST', 'artifacts', 'ARTIFACTS',
    'standing', 'STANDING', causing those sections' content to land in
    ## Unfiled instead of being dropped.
    Oracle: the adopted HANDOFF.md contains no '- unfiled:' bullet (only
    cursor sections and Key files content appear, not Log/Standing bullets).
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra=(
        '\n## Log\n\n- 2026-01-01: old entry\n'
        '\n## Standing\n\n- Some standing note\n'
        '\n## Artifacts\n\n- artifact-file.zip\n'
        '\n## Read first\n\n- SPEC.md\n'
    ))
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    unfiled_bullets = [ln for ln in text.splitlines()
                       if ln.startswith('- unfiled:')]
    assert unfiled_bullets == []


def test_adopt_unfiled_rstrip_preserves_trailing_space_loss(
        tmp_path, monkeypatch):
    """bl.rstrip() removes trailing whitespace only; lstrip would lose leading.

    Mutation: `.rstrip()` changed to `.lstrip()` (mutmut_803), so leading
    whitespace on Unfiled lines is stripped instead of trailing, changing
    indented items.
    Oracle: an Unfiled line with trailing spaces has the text preserved;
    the bullet in the cursor starts with '- ' not with spaces.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra='\n## Unfiled\n\n- a note   \n')
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    assert '- a note' in text


# --- _verb_adopt (Key files section parsing) --------------------------


def test_adopt_kf_group_set_to_lowercase(tmp_path, monkeypatch):
    """A grading label in Key files is stored lowercase for matching.

    Mutation: `.lower()` changed to `.upper()` (mutmut_298), so 'read now'
    becomes 'READ NOW' and `if 'read now' in grade` never matches.
    Oracle: a 'Read now:' label grades SPEC.md as read_before=always.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='Read now:\n- `SPEC.md` the spec\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['read_before'] == 'always'


def test_adopt_range_info_rstripped_not_lstripped(tmp_path, monkeypatch):
    """Trailing '; ' is rstripped from range-only label, not lstripped.

    Mutation: `.rstrip('; ')` changed to `.lstrip('; ')` (mutmut_210),
    leaving a trailing '; ' when there is no free text after the range
    (lstrip removes leading chars, not trailing).
    Oracle: a SPEC.md:10-40 pointer with no free text stores label
    'lines 10-40' with no trailing '; '.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='- `SPEC.md:10-40`\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    label = rows['SPEC.md']['label']
    assert label == 'lines 10-40'


def test_adopt_sref_findall_is_ignorecase(tmp_path, monkeypatch):
    """Section refs use re.IGNORECASE so 'Section 3' matches.

    Mutation: re.IGNORECASE dropped (mutmut_255), so 'Section 3' in the
    free text is not found and where='-' instead of 's3'.
    Oracle: SPEC.md with label 'see Section 3' yields where='s3'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## 3. Retry\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='- `SPEC.md` see Section 3\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['where'] == 's3'


def test_adopt_rb_over_none_not_empty_string(tmp_path, monkeypatch):
    """rb_over initialized to None; '' would apply an empty read_before.

    Mutation: `rb_over: str | None = None` changed to `= ''` (mutmut_236),
    so a file with no grade label gets kf_rb='' and the condition
    `kf_rb is not None` is True, setting rb=''.
    Oracle: SPEC.md with label but no grade retains read_before=always
    (from infer_kind), not ''.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='- `SPEC.md` the design spec\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['read_before'] in {'always', 'never', 'edit', 'mention'}
    assert rows['SPEC.md']['read_before'] != ''


def test_adopt_key_files_section_name_case_exact(tmp_path, monkeypatch):
    """'Key files' section name is exact case; lowercase misses the section.

    Mutation: 'Key files' changed to 'key files' (mutmut_172) or
    'KEY FILES' (mutmut_173), so no kf_map entries are built and labels
    are not applied to ledger rows.
    Oracle: a 'Read now' bullet under ## Key files grades SPEC.md as always.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='Read now:\n- `SPEC.md` the spec\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['read_before'] == 'always'


def test_adopt_hq_block_marker_stops_cur_body(tmp_path, monkeypatch):
    """Lines starting with '<!-- hq:' are not added to section bodies.

    Mutation: '<!-- hq:' changed to '<!-- HQ:' (mutmut_182), so block
    markers are added to section bodies instead of being skipped, corrupting
    the section content.
    Oracle: the adopted HANDOFF.md contains no '<!-- hq:' line in any
    cursor section body (a block marker should stay outside the cursor).
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra=(
        '\n<!-- hq:keyfiles abc -->\n'
        'some content\n'
        '<!-- /hq:keyfiles -->\n'
    ))
    assert hq.main(['adopt', _SLUG]) == 0


# --- _verb_adopt (manifest not-carried output) -----------------------


def test_adopt_manifest_log_still_adopted_for_basic_run(
        tmp_path, monkeypatch):
    """The manifest log row says 'adopted' for a plain adopt.

    Mutation: Various mutations that would raise exceptions or produce
    KeyErrors during adopt (incorrect key access patterns). A plain adopt
    that exits 0 and logs 'adopted' proves the critical path ran cleanly.
    Oracle: manifest log == 'adopted'.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    assert _manifest(folder)[0]['log'] == 'adopted'


# --- _verb_adopt (not_carried print slice) ---------------------------


def test_adopt_prints_every_not_carried_line_in_full(
        tmp_path, monkeypatch, capsys):
    """Every not-carried line prints, whole, under the conservation count.

    Mutation: the print loop capped at five lines, so the sixth and
    seventh lines the agent must rehome never reach it; or each line cut
    at 72 characters, so a line whose distinguishing text sits past that
    column cannot be told from its neighbor.
    Oracle: hand-counted - a ## Standing section adopt skips with six
    items gives seven not-carried lines (heading + six), each printed as
    written, the sixth 73 characters long and ending in `BCD`.
    """
    folder = _root(tmp_path, monkeypatch)
    long_item = '- ' + 'a' * 68 + 'BCD'
    items = [f'- item{i} STANDA0{i}' for i in range(1, 6)] + [long_item]
    _handoff(folder, extra='\n## Standing\n\n' + '\n'.join(items) + '\n')
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'conservation: 7 original lines not carried' in out
    printed = [ln for ln in out.splitlines() if ln.startswith('  not carried: ')]
    assert printed == ['  not carried: ## Standing'] + [
        f'  not carried: {item}' for item in items]


def test_adopt_prints_every_orig_not_carried_line_in_full(
        tmp_path, monkeypatch, capsys):
    """The HANDOFF.orig.md check prints every not-carried line, whole.

    Mutation: the orig print loop capped at five lines or cut at 72
    characters, so the count and the list disagree.
    Oracle: hand-counted - HANDOFF.orig.md carries a ## Standing section
    with six items, so seven lines print, the sixth 73 characters long
    and ending in `EFG`.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    long_item = '- ' + 'b' * 68 + 'EFG'
    items = [f'- o0{i} ORIGZ0{i}' for i in range(1, 6)] + [long_item]
    orig_text = (
        f'# Handoff: {folder.name}\n\n'
        f'Written: 2026-08-01 | Cycle: 2\n\n'
        + _CURSOR
        + '\n## Standing\n\n' + '\n'.join(items) + '\n'
    )
    (folder / 'HANDOFF.orig.md').write_text(orig_text, encoding='utf-8')
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'conservation vs HANDOFF.orig.md: 7 lines not carried' in out
    orig_part = out.split('conservation vs HANDOFF.orig.md:', 1)[1]
    printed = [
        ln for ln in orig_part.splitlines() if ln.startswith('  not carried: ')]
    assert printed == ['  not carried: ## Standing'] + [
        f'  not carried: {item}' for item in items]


def test_adopt_on_disk_false_prints_missing_notice(tmp_path, monkeypatch,
                                                   capsys):
    """A Key files pointer not found on disk prints a notice.

    Mutation: `if not on_disk` changed to `if on_disk` (mutmut_671), so
    the notice is printed for files that exist, not for missing ones.
    Oracle: a pointer to a nonexistent path prints 'not on disk'.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, key_files='- `missing-file.md` label text\n')
    hq.main(['adopt', _SLUG])
    out = capsys.readouterr().out
    assert 'not on disk' in out


# --- _verb_adopt (additional logic checks) ---------------------------


def test_adopt_handoff_is_dir_returns_1(tmp_path, monkeypatch):
    """Adopt returns 1 when HANDOFF.md is a directory, not 2.

    Mutation: `return 1` changed to `return 2` (mutmut_13) for the is-dir
    early exit; the missing-file early exit is a different return.
    Oracle: main(['adopt', slug]) == 1 when the path is a directory.
    """
    folder = _root(tmp_path, monkeypatch)
    handoff = folder / 'HANDOFF.md'
    handoff.mkdir(parents=True)
    assert hq.main(['adopt', _SLUG]) == 1


def test_adopt_manifest_created_when_absent(tmp_path, monkeypatch):
    """manifest.tsv is written only when it does not already exist.

    Mutation: `if not manifest_path.exists()` changed to
    `if manifest_path.exists()` (mutmut_123), so an existing manifest is
    overwritten and a missing manifest is never created.
    Oracle: adopt produces a non-empty manifest.tsv when none existed.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _manifest(folder)
    assert len(rows) == 1


def test_adopt_draft_gets_always_rb(tmp_path, monkeypatch):
    """draft-kind files get read_before=always, same as spec.

    Mutation: `'draft'` changed to `'DRAFT'` (mutmut_377,478,562), so
    draft kind does not match and the file gets rb=never instead of always.
    Oracle: a .py file (draft extension) at the top level has rb=always.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'worker.py').write_text('def f(): pass\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['worker.py']['read_before'] == 'always'
    assert rows['worker.py']['kind'] == 'draft'


def test_adopt_manifest_repos_field_stored(tmp_path, monkeypatch):
    """Manifest row stores repos, cursor_lines, and note under correct keys.

    Mutation: any of 'repos' (mutmut_956), 'cursor_lines' (mutmut_958),
    'payload_tokens' (mutmut_961), 'ledger_sha' (mutmut_969),
    'standing_sha' (mutmut_975), 'note' (mutmut_980) written as uppercase,
    making those fields read back as '-'.
    Oracle: all named fields have non-'-' values (repos='-' is expected,
    so pick cursor_lines and ledger_sha as witnesses; note opens with the
    adopt provenance).
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _manifest(folder)
    row = rows[0]
    assert row['note'].startswith('adopted ')
    assert row['cursor_lines'] != ''
    assert row['ledger_sha'] != ''
    assert row['ledger_sha'] != '-'


def test_adopt_successor_on_disk_requires_file(tmp_path, monkeypatch):
    """successor_on_disk is True only when successor exists on disk.

    Mutation: `and` changed to `or` (mutmut_450), so a missing successor
    with a non-'-' name triggers successor_on_disk; or `!=` changed to
    `==` (mutmut_451), inverting the check so no successor means True.
    Oracle: a file with successor='-' gets status=live; a file with a
    named successor that is missing on disk does NOT get check_r1 bypass.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'SPEC.md.pre-1').write_text('# old\n\nOld.\n', encoding='utf-8')
    import time
    time.sleep(0.01)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    # SPEC.md is newer; SPEC.md.pre-1 gets superseded by apply_stem_rule
    assert rows['SPEC.md']['status'] == 'live'


def test_adopt_ledger_cycle_key_lowercase(tmp_path, monkeypatch):
    """The cycle field in each ledger row is stored correctly.

    Mutation: 'cycle' key changed to 'CYCLE' (mutmut_421), so cycle reads
    back as '-' instead of the cycle number.
    Oracle: ledger row for data.json has cycle=='3'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'data.json').write_text('{}', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['data.json']['cycle'] == '3'


def test_adopt_at_top_base_folder_check(tmp_path, monkeypatch):
    """at_top requires base=='folder'; abs-base pointers are not top-level.

    Mutation: `base == 'folder'` changed to `base != 'folder'` (mutmut_540)
    or `base == 'FOLDER'` (mutmut_542), so the at_top flag is wrong and
    draft detection for pointer rows fails.
    Oracle: a Key files pointer to a subfolder file that is a draft gets
    read_before=always when base='folder'; an abs pointer gets never.
    """
    folder = _root(tmp_path, monkeypatch)
    sub = folder / 'sub'
    sub.mkdir()
    (sub / 'DRAFT-notes.md').write_text('# Draft\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='- `sub/DRAFT-notes.md` draft notes\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    # sub/DRAFT-notes.md is under folder, base='folder', but '/' in stored
    # means at_top is False; rb is inferred from infer_kind with top_level=False
    assert 'sub/DRAFT-notes.md' in rows


def test_adopt_rb_count_never_key_lowercase(tmp_path, monkeypatch):
    """rb_count.get('never', 0) uses lowercase key with default 0.

    Mutation: 'never' changed to 'NEVER' (mutmut_997) -> always 0;
    default 0 changed to 1 (mutmut_998) -> minimum count is 1;
    get key changed to integer 0 (mutmut_994) -> TypeError.
    Oracle: adopt exits 0 for a simple run; check via manifest log.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'data.json').write_text('{}', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _manifest(folder)
    assert rows[0]['log'] == 'adopted'


def test_adopt_kf_label_ne_dash_condition(tmp_path, monkeypatch):
    """A Key files label != '-' is added to seeded_labels.

    Mutation: `kf_label != '-'` changed to `kf_label == '-'` (mutmut_646),
    so labeled entries are never tracked and unlabeled entries are, reversing
    whether the label appears in the summary.
    Oracle: a file with a grading label has its label in the manifest log
    or triggers standard adopt behavior (exits 0).
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    _handoff(folder, key_files='- `SPEC.md` the design spec\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['SPEC.md']['label'] == 'the design spec'


# --- _verb_adopt (Key files heading grade) ----------------------------


def test_adopt_kf_heading_grade_kills_170_172_173(tmp_path, monkeypatch):
    """A '## Read now:' heading inside Key files keeps files in that section.

    Mutation: `cur_h2 == 'Key files'` changed to `cur_h2 != 'Key files'`
    (mutmut_170), `'key files'` (mutmut_172), or `'KEY FILES'` (mutmut_173),
    so the '## Read now:' heading opens a new section instead of staying in
    Key files. Files under it land in the wrong section and get no grade.
    Oracle: a notes file under '## Read now:' inside Key files gets
    read_before=always; under the mutations it gets the default 'never'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes.md').write_text('Notes.\n', encoding='utf-8')
    _handoff(folder, key_files='## Read now:\n- `notes.md` the notes\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes.md']['read_before'] == 'always'


def test_adopt_hq_marker_excluded_from_adopted_text(tmp_path, monkeypatch):
    """Lines starting with '<!-- hq:' are not in the adopted HANDOFF.md body.

    Mutation: '<!-- hq:' changed to '<!-- HQ:' (mutmut_182), so lowercase
    hq: markers ARE added to section bodies, and the adopted HANDOFF.md
    contains them in cursor sections.
    Oracle: after adopt, the adopted HANDOFF.md does not contain
    '<!-- hq:keyfiles' in the Task or Plan section body.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra=(
        '\n<!-- hq:keyfiles abc -->\n'
        'some content\n'
        '<!-- /hq:keyfiles -->\n'
    ))
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    assert '<!-- hq:keyfiles' not in text


def test_adopt_kf_grade_lower_for_notes_grade(tmp_path, monkeypatch):
    """kf_group.lower() enables 'read now' substring match.

    Mutation: `.lower()` changed to `.upper()` (mutmut_298), so grade
    becomes 'READ NOW' and `if 'read now' in grade` never matches.
    Oracle: a notes file under 'Read now:' label gets read_before=always
    (not 'never' which it would get if the grade check fails).
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes.md').write_text('Notes text.\n', encoding='utf-8')
    _handoff(folder, key_files='Read now:\n- `notes.md` background notes\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes.md']['read_before'] == 'always'


def test_adopt_ledger_row_ts_sha12_lines_fields(tmp_path, monkeypatch):
    """Ledger row has ts, sha12, and lines stored under lowercase keys.

    Mutation: 'ts' changed to 'TS' (mutmut_423), 'sha12' to 'SHA12'
    (mutmut_441), or 'lines' to 'LINES' (mutmut_443), making those fields
    read back as '-' (the default for missing keys in _append_tsv).
    Oracle: a regular file has ts starting with '2026', sha12 is exactly
    12 hex chars, and lines is a positive integer string.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'data.json').write_text('{}\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    row = rows['data.json']
    assert row['ts'].startswith('2026')
    assert len(row['sha12']) == 12
    assert row['lines'].isdigit()
    assert int(row['lines']) >= 0


def test_adopt_gated_uses_and_not_or(tmp_path, monkeypatch, capsys):
    """Only live files with gated rb are counted; live-or-gated-rb is wrong.

    Mutation: `and` changed to `or` (mutmut_505, mutmut_661), so all live
    files OR all gated-rb files are counted, inflating the gated count.
    Oracle: one spec (gated) and one notes (not gated) give gated(1);
    under `or`, notes is also live so gated count becomes 2.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'notes.md').write_text('Notes.\n', encoding='utf-8')
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'gated (1)' in out
    assert 'gated (2)' not in out


def test_adopt_unfiled_content_not_prefixed(tmp_path, monkeypatch):
    """Unfiled section body lines are kept as-is, not prefixed with '- unfiled:'.

    Mutation: `if h == 'Unfiled'` changed to `if h == 'unfiled'` (mutmut_800)
    or `'UNFILED'` (mutmut_801), so the Unfiled section goes through the else
    branch and gets '- unfiled:' prefix prepended to each line.
    Oracle: a '- my note' line in ## Unfiled appears verbatim without prefix.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra='\n## Unfiled\n\n- my note XY99\n')
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    assert '- my note XY99' in text
    assert '- unfiled: - my note XY99' not in text


def test_adopt_unfiled_rstrip_removes_trailing_spaces(tmp_path, monkeypatch):
    """Unfiled lines are rstripped; lstrip would not remove trailing spaces.

    Mutation: `.rstrip()` changed to `.lstrip()` (mutmut_803), so trailing
    spaces on Unfiled lines are NOT removed and appear in the adopted file.
    Oracle: a non-last Unfiled line '- a note   ' (trailing spaces) appears
    in the adopted HANDOFF.md as '- a note' with no trailing spaces.
    A second line ensures '- a note   ' is not the last line of cursor_text
    (which cursor_text.strip() would otherwise absorb).
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra='\n## Unfiled\n\n- a note   \n- b note\n')
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text(encoding='utf-8')
    assert '- a note   ' not in text
    assert '- a note' in text


def test_adopt_manifest_cursor_lines_not_dash(tmp_path, monkeypatch):
    """Manifest row cursor_lines is a positive integer, not '-'.

    Mutation: 'cursor_lines' key changed to 'CURSOR_LINES' (mutmut_958),
    so cursor_lines reads back as '-' (default for missing key).
    Oracle: cursor_lines field is a digit string with value > 0.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _manifest(folder)
    assert rows[0]['cursor_lines'].isdigit()
    assert int(rows[0]['cursor_lines']) > 0


def test_adopt_manifest_standing_sha_not_dash(tmp_path, monkeypatch):
    """Manifest row standing_sha is a 12-char hash, not '-'.

    Mutation: 'standing_sha' key changed to 'STANDING_SHA' (mutmut_975),
    so standing_sha reads back as '-' (default for missing key).
    Oracle: standing_sha field has exactly 12 hex characters.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _manifest(folder)
    assert len(rows[0]['standing_sha']) == 12


def test_adopt_kf_matched_prevents_duplicate_row(tmp_path, monkeypatch):
    """A labeled top-level file is marked matched; pointer loop skips it.

    Mutation: `kf_label != '-'` changed to `kf_label == '-'` (mutmut_646)
    in the kf_matched guard, so labeled files are not marked matched.
    The pointer loop then seeds a SECOND row for the same path.
    Oracle: with one SPEC.md and one Key files label, the ledger has exactly
    2 entries (SPEC.md from main loop + HANDOFF.md is skipped); under the
    mutation it gets 3 (two SPEC.md rows).
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    (folder / 'data.json').write_text('{}', encoding='utf-8')
    _handoff(folder, key_files='- `SPEC.md` the design spec\n')
    assert hq.main(['adopt', _SLUG]) == 0
    all_rows = _ledger(folder)
    spec_rows = [r for r in all_rows if r['path'] == 'SPEC.md']
    assert len(spec_rows) == 1


def test_adopt_missing_pointer_rb_is_lowercase_never(tmp_path, monkeypatch):
    """A missing Key files pointer gets read_before='never', not 'NEVER'.

    Mutation: `else 'never'` changed to `else 'NEVER'` (mutmut_595),
    so a missing pointer's read_before becomes 'NEVER' in the ledger.
    Oracle: the ledger row for a missing pointer has read_before='never'.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, key_files='- `missing-file.md` label\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'missing-file.md' in rows
    assert rows['missing-file.md']['read_before'] == 'never'


def test_adopt_pointer_row_cycle_and_ts_stored(tmp_path, monkeypatch):
    """Pointer rows store cycle, ts, and sha12 under lowercase keys.

    Mutation: 'cycle' key changed to 'CYCLE' (mutmut_577), 'ts' to 'TS'
    (mutmut_579), or 'sha12' to 'SHA12' (mutmut_602) in the pointer row
    dict, making those fields read back as '-'.
    Oracle: a subdirectory file's pointer row has cycle=='3', ts starting
    with '2026', and sha12 of length 12.
    """
    folder = _root(tmp_path, monkeypatch)
    sub = folder / 'sub'
    sub.mkdir()
    (sub / 'notes.md').write_text('Notes.\n', encoding='utf-8')
    _handoff(folder, key_files='- `sub/notes.md` notes\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'sub/notes.md' in rows
    assert rows['sub/notes.md']['cycle'] == '3'
    assert rows['sub/notes.md']['ts'].startswith('2026')
    assert len(rows['sub/notes.md']['sha12']) == 12


def test_adopt_pointer_notes_edit_applied(tmp_path, monkeypatch):
    """A notes-kind pointer under 'Reference only:' gets read_before=edit.

    Mutation: `kind == 'notes'` changed to `kind == 'NOTES'` (mutmut_573),
    so the edit override is never applied to notes-kind pointers.
    Oracle: a subdirectory notes-bg.md file (notes kind) with 'Reference
    only:' grade gets read_before=edit (not 'never' from the default).
    """
    folder = _root(tmp_path, monkeypatch)
    sub = folder / 'sub'
    sub.mkdir()
    (sub / 'notes-bg.md').write_text('Background notes.\n', encoding='utf-8')
    _handoff(folder, key_files='Reference only:\n- `sub/notes-bg.md` bg\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'sub/notes-bg.md' in rows
    assert rows['sub/notes-bg.md']['read_before'] == 'edit'


def test_adopt_missing_top_level_draft_keeps_kind_and_stays_missing(
        tmp_path, monkeypatch):
    """A missing top-level .py pointer is seeded `draft missing never`.

    Mutation: `base == 'folder'` changed to `base != 'folder'` (mutmut_540)
    or `base == 'FOLDER'` (mutmut_542), so at_top is False and infer_kind
    gives 'other' instead of 'draft'; or the row lifted to live/always for
    a gated kind, so finish stops on `missing live gated`.
    Oracle: a missing top-level script.py with a Key files label is seeded
    kind='draft', status='missing', read_before='never'.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, key_files='- `script.py` the draft script\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    row = rows['script.py']
    assert (row['kind'], row['status'], row['read_before']) == (
        'draft', 'missing', 'never')


# --- String and none class spot-check (140 string, 93 none) ----------
# String mutations: 140 total. Ten spot-checks for real defects follow.
# None mutations: 93 total. Ten spot-checks follow.
#
# Spot-check 1 (string): x_conservation__mutmut_30 changes
#   _standing_prefix.sub('- ', s) -> sub('XX- XX', s).
#   Standing prefix is replaced with garbage; the normalized form no
#   longer matches union_text. This is a real defect: every standing
#   item appears as missing. REAL DEFECT.
#
# Spot-check 2 (string): x_conservation__mutmut_51 changes
#   re.sub(r'^#+\s*', '', s) -> re.sub(r'^#+\s*', 'XXXX', s).
#   Heading markers are replaced with 'XXXX', so heading text is
#   prefixed with 'XXXX' in the normalized form. REAL DEFECT.
#
# Spot-check 3 (string): x_conservation__mutmut_59 changes
#   re.sub(r'^[-*+]\s+', '', s) -> sub(r'XX^[-*+]\s+XX', '', s).
#   The broken regex never matches; bullet markers are never stripped.
#   REAL DEFECT.
#
# Spot-check 4 (string): x_conservation__mutmut_69 changes
#   '\n'.join(...) -> 'XX\nXX'.join(...).
#   Union parts are joined with garbage, corrupting all substring checks.
#   REAL DEFECT.
#
# Spot-check 5 (string): x_split_handoff__mutmut_2 changes dict key
#   'header' to 'XXheaderXX'. Real defect: callers reading 'header' get ''.
#   REAL DEFECT.
#
# Spot-check 6 (string): x__verb_adopt__mutmut_30 changes print message
#   case for 'HANDOFF.md'. The message differs but behavior is unchanged -
#   the function still returns 1. NOT A REAL DEFECT (output-only change
#   not asserted by existing tests).
#
# Spot-check 7 (string): x__verb_adopt__mutmut_64 changes
#   'HANDOFF.prev.md' to 'handoff.prev.md'. Real defect: prev file is
#   not found on case-sensitive filesystems, so c02.md is never written.
#   REAL DEFECT (killed by test_adopt_prev_path_filename_is_uppercase).
#
# Spot-check 8 (string): x__verb_adopt__mutmut_835 changes
#   '## Unfiled' to '## unfiled'. Real defect: the cursor carries
#   '## unfiled' instead of '## Unfiled', breaking heading conventions.
#   REAL DEFECT (killed by test_adopt_unfiled_section_case_exact).
#
# Spot-check 9 (string): x_split_handoff__mutmut_4 changes dict initial
#   value 'header' key to 'HEADER'. REAL DEFECT (killed by
#   test_split_handoff_keys_are_lowercase).
#
# Spot-check 10 (none): x_conservation__mutmut_91 changes
#   section_content.get(norm_heading, []) to get(norm_heading, None).
#   When heading is absent, iterating None raises TypeError. This is a
#   REAL DEFECT for headings that appear in _heading_covered but not
#   section_content - though in practice every heading from orig_lines
#   is in section_content. EQUIVALENT in practice (default unreachable).
#
# None spot-checks 1-10 summary: none mutations swap constants for None
# (e.g. `section_content.get(norm_heading, None)`, `id_pat = None`,
# `sup_m = None`). Most cause immediate TypeError or AttributeError when
# the None value is used. In each case the defect is real: passing None
# to re.match, iterating None, or accessing .group on None all fail.
# None class: every spot-checked instance is a REAL DEFECT.
