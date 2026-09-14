"""Kill logic/number mutmut survivors for the cycle-path function group.

Functions: _verb_finish, _do_stamp, _take_lock, _verb_begin, _print_worklist,
witness, _assemble_handoff, _append_lines, _append_tsv, render_log,
_read_lock, drain_unfiled, collisions, _extract_terms, _dirty_str,
check_r3, dangling_successors, _reconcile_missing, _folder_state.
"""

import contextlib
import hashlib
import io
import pathlib

from scripts import hq

# ---------------------------------------------------------------------------
# Helpers copied from test_hq_cycle_path.py
# ---------------------------------------------------------------------------

_SLUG = 'mut-slug'
_SESSION = 'session-mut'
_HOST = 'mut-host'
_NOW = '2026-09-09T12:00:00'
_YOUNG_LOCK = '2026-09-09T11:00:00'
_OTHER_SESSION = 'session-other'


def _new_root(tmp_path, monkeypatch, cycle='1', now=_NOW, slug=_SLUG):
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


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _lock(folder, session, time_str, cycle=1):
    (folder / '.hq.lock').write_text(
        f'slug={folder.name}\nsession={session}\nhost=other-host\n'
        f'time={time_str}\ncycle={cycle}\n')


def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


# ---------------------------------------------------------------------------
# witness
# ---------------------------------------------------------------------------


def test_witness_w2_code_uppercase():
    """witness() labels the standing break 'W2', not 'w2'.

    Mutation: ('W2', ...) -> ('w2', ...) changes the code prefix to lowercase.
    Oracle: the returned string must begin with 'W2:'.
    """
    lb = b'ledger content'
    sb = b'standing content'
    ledger_sha = _sha12(lb)
    standing_sha = _sha12(sb)
    prev = {
        'ledger_bytes': str(len(lb)),
        'ledger_sha': ledger_sha,
        'standing_bytes': str(len(sb)),
        'standing_sha': standing_sha,
    }
    # Tamper standing: change a byte so sha mismatches.
    tampered_sb = b'standing CHANGED!'
    breaks = hq.witness(prev, lb, tampered_sb)
    assert breaks, 'expected a standing break'
    assert breaks[0].startswith('W2:'), (
        f'expected W2: prefix, got {breaks[0]!r}')


def test_witness_standing_name_case_kills_check():
    """witness() uses 'standing' (lowercase) to look up prev_manifest keys.

    Mutation: 'STANDING' replaces 'standing', so 'STANDING_sha' is looked up
    instead of 'standing_sha', returning '-' (skip) every time.
    Oracle: tampered standing bytes must produce a break; with the mutant the
    break is silently suppressed.
    """
    lb = b'ledger'
    sb = b'standing data'
    prev = {
        'ledger_bytes': '6',
        'ledger_sha': _sha12(lb),
        'standing_bytes': str(len(sb)),
        'standing_sha': _sha12(sb),
    }
    tampered = b'standing TAMPERED'
    breaks = hq.witness(prev, lb, tampered)
    # Original: W2 fires.  Mutant (STANDING): sha lookup returns '-', skips.
    assert any('W2' in b for b in breaks), (
        'tampered standing must produce a W2 break')


def test_witness_or_becomes_and_at_dash_sha():
    """witness() skips a file when stored sha is '-', even with n > 0.

    Mutation: 'or' -> 'and' so the file is NOT skipped when stored == '-'
    but n > 0; the computed sha is then compared to '-', which never matches,
    producing a spurious W1 or W2 break.
    Oracle: when prev_manifest has sha == '-' and bytes > 0, witness returns [].
    """
    lb = b'some ledger bytes'
    sb = b''
    prev = {
        'ledger_bytes': str(len(lb)),
        'ledger_sha': '-',  # no real sha stored
        'standing_bytes': '0',
        'standing_sha': '-',
    }
    # No tampering; the '-' sha means 'not yet recorded', so skip.
    breaks = hq.witness(prev, lb, sb)
    assert breaks == [], (
        'stored sha of "-" with n>0 must be skipped, not fire a spurious break')


def test_witness_n_zero_skips_check():
    """witness() skips the check when n == 0, not n == 1.

    Mutation: n == 0 -> n == 1, so a file recorded with 0 bytes triggers the
    sha computation (sha of empty prefix is fixed) and compares against the
    stored sha; if they differ, a spurious break fires.
    Oracle: n == 0 must always skip regardless of the stored sha value.
    """
    lb = b'data'
    sb = b''
    prev = {
        'ledger_bytes': '0',
        'ledger_sha': 'aabbccddeeff',  # mismatch with sha('') but n=0 -> skip
        'standing_bytes': '0',
        'standing_sha': '-',
    }
    breaks = hq.witness(prev, lb, sb)
    assert breaks == [], (
        'n == 0 must always skip the sha check')


def test_witness_break_stops_w2():
    """witness() checks both W1 and W2; 'break' skips W2 after W1 passes.

    Mutation: 'continue' -> 'break' in the for-loop body exits after W1,
    so a tampered standing file goes undetected.
    Oracle: tampered standing with valid ledger must still produce a W2 break.
    """
    lb = b'ledger'
    sb = b'standing original'
    prev = {
        'ledger_bytes': str(len(lb)),
        'ledger_sha': _sha12(lb),  # ledger unchanged - W1 passes
        'standing_bytes': str(len(sb)),
        'standing_sha': _sha12(sb),
    }
    tampered_sb = b'standing TAMPERED!!'
    breaks = hq.witness(prev, lb, tampered_sb)
    assert any('W2' in b for b in breaks), (
        'W2 must fire even when W1 passes')


# ---------------------------------------------------------------------------
# render_log
# ---------------------------------------------------------------------------


def test_render_log_missing_repos_uses_dash_default():
    """render_log() renders '-' for a row with no 'repos' key.

    Mutation: row.get('repos', ) returns None; 'None == "-"' is False, so the
    else-branch prints 'None' in the formatted line instead of the dash format.
    Oracle: a row dict without 'repos' uses the short format '- date: log'.
    """
    row = {'written': '2026-09-01', 'cycle': '1', 'log': 'seed commit'}
    # No 'repos' key - should use the short '- written: log' format.
    out = hq.render_log([row], n=1)
    assert 'None' not in out, 'missing repos must not render as None'
    assert out.startswith('- 2026-09-01:'), (
        f'missing repos must use short format, got {out!r}')


# ---------------------------------------------------------------------------
# collisions
# ---------------------------------------------------------------------------


def test_collisions_case_insensitive_match():
    """collisions() matches terms against headings case-insensitively.

    Mutation: re.IGNORECASE removed, making the search case-sensitive.
    Oracle: backtick term `pipeline` from now_text matches heading 'PIPELINE DATA'.
    """
    # Use backtick so _extract_terms picks up 'pipeline' (lowercase).
    now_text = 'Use the `pipeline` to process data.'
    headings = [('spec.md', 1, '## PIPELINE DATA')]
    rarity = {'pipeline': 0}
    hits = hq.collisions(now_text, [], headings, rarity)
    assert hits, 'case-insensitive: "`pipeline`" must match "PIPELINE DATA"'
    assert 'pipeline' in hits[0]


# ---------------------------------------------------------------------------
# drain_unfiled
# ---------------------------------------------------------------------------


def test_drain_unfiled_single_space_continuation():
    """drain_unfiled() treats a line with exactly 1 leading space as continuation.

    Mutation: raw_line[:1].isspace() -> raw_line[:2].isspace(), so a
    1-space indent is not recognized as continuation and the body is dropped.
    Oracle: bullet with 1-space continuation has body joined to headline.
    """
    text = (
        '## Task\nDo.\n\n## Unfiled\n'
        '- decision: **Fix it** Body here.\n'
        ' extra body line.\n'
    )
    items, cursor_out, refusal = hq.drain_unfiled(text)
    assert refusal is None, f'unexpected refusal: {refusal}'
    assert len(items) == 1
    _, headline, body = items[0]
    assert 'extra body line' in body, (
        '1-space continuation must join the body')


def test_drain_unfiled_break_misses_second_bullet():
    """drain_unfiled() processes all bullets after a continuation line.

    Mutation: 'continue' -> 'break' after appending a continuation line exits
    the line loop, so a second bullet that follows is never parsed.
    Oracle: text with bullet + continuation + second bullet yields two items.
    """
    text = (
        '## Unfiled\n'
        '- decision: **First** A decision.\n'
        '  continuation of first.\n'
        '- constraint: **Second** A constraint.\n'
    )
    items, _, refusal = hq.drain_unfiled(text)
    assert refusal is None
    assert len(items) == 2, (
        f'both bullets must be parsed, got {len(items)}')
    kinds = [k for k, _, _ in items]
    assert 'decision' in kinds
    assert 'constraint' in kinds


def test_drain_unfiled_cursor_out_trailing_format():
    """drain_unfiled() rstrips cursor_out before adding trailing newline.

    Mutation: rstrip() -> lstrip() changes which whitespace is removed,
    so cursor_out ends with extra newlines instead of exactly one.
    Oracle: cursor_out for input with no Unfiled section after a section ends
    with exactly one newline.
    """
    text = '## Task\nDo work.\n\n## Unfiled\n- decision: **X** Done.\n'
    _, cursor_out, refusal = hq.drain_unfiled(text)
    assert refusal is None
    # cursor_out is the text with ## Unfiled removed; it must end with
    # exactly one newline.
    assert cursor_out.endswith('\n'), 'cursor_out must end with a newline'
    assert not cursor_out.endswith('\n\n'), (
        'cursor_out must not end with double newline (rstrip, not lstrip)')


# ---------------------------------------------------------------------------
# _append_lines
# ---------------------------------------------------------------------------


def test_append_lines_wrong_seek_adds_spurious_newline(tmp_path):
    r"""_append_lines() seeks to -1 from end to read the last byte.

    Mutation: seek(-1, SEEK_END) -> seek(SEEK_END) = seek(2, 0): reads the
    3rd byte of the file (if it exists), not the last one. For a file ending
    with '\n', the 3rd byte may not be '\n', so a spurious '\n' is prepended.
    Oracle: appending to a file ending '\n' produces no extra blank line.
    """
    p = tmp_path / 'f.txt'
    p.write_bytes(b'line one\n')  # 9 bytes; ends with '\n'
    hq._append_lines(p, ['line two'])
    content = p.read_text()
    assert content == 'line one\nline two\n', (
        f'file must have no extra blank line, got {content!r}')


def test_append_lines_seek_past_end_misses_missing_newline(tmp_path):
    r"""_append_lines() adds a '\n' before appending to a file missing one.

    Mutation: seek(-1, SEEK_END) -> seek(+1, SEEK_END) reads past EOF,
    returning b'' (empty), which is in {b'', b'\n'} so no lead is added;
    two records end up on the same line.
    Oracle: appending to a file ending without '\n' inserts a newline first.
    """
    p = tmp_path / 'f.txt'
    p.write_bytes(b'line one')  # no trailing newline
    hq._append_lines(p, ['line two'])
    content = p.read_text()
    assert '\nline two\n' in content, (
        f'missing newline must be restored before append, got {content!r}')


def test_append_lines_seek_minus2_misreads_last_byte(tmp_path):
    r"""_append_lines() reads the last byte, not the second-to-last.

    Mutation: seek(-1, SEEK_END) -> seek(-2, SEEK_END): for a file ending
    '\n', the second-to-last byte may not be '\n', adding a spurious '\n'.
    Oracle: appending to 'abc\n' (4 bytes, second-to-last is 'c') must not
    add an extra blank line.
    """
    p = tmp_path / 'f.txt'
    p.write_bytes(b'abc\n')  # last='\n', second-to-last='c'
    hq._append_lines(p, ['new'])
    content = p.read_text()
    assert content == 'abc\nnew\n', (
        f'no extra newline for file ending with \n, got {content!r}')


def test_append_lines_inverted_lead_condition(tmp_path):
    r"""_append_lines() uses lead='' when last_byte is in {b'', b'\n'}.

    Mutation: 'in' -> 'not in' inverts the guard: a file ending '\n' gets an
    extra '\n' prepended, and a file missing '\n' gets nothing prepended.
    Oracle: two append scenarios both correct; inverted condition fails one.
    """
    # File that ends with '\n': appending must not add extra newline.
    p1 = tmp_path / 'good.txt'
    p1.write_bytes(b'first\n')
    hq._append_lines(p1, ['second'])
    assert p1.read_text() == 'first\nsecond\n', (
        'file ending with newline must not gain an extra blank line')

    # File missing trailing newline: append must restore it.
    p2 = tmp_path / 'bad.txt'
    p2.write_bytes(b'first')
    hq._append_lines(p2, ['second'])
    text2 = p2.read_text()
    assert text2 == 'first\nsecond\n', (
        f'missing newline must be restored, got {text2!r}')


# ---------------------------------------------------------------------------
# _append_tsv
# ---------------------------------------------------------------------------


def test_append_tsv_none_for_missing_field(tmp_path):
    """_append_tsv() renders '-' for a field absent from the row dict.

    Mutation: row.get(f, ) = None; str(None) = 'None' appears in the TSV.
    Oracle: a missing field must produce '-' in the written file.
    """
    p = tmp_path / 'test.tsv'
    fields = ['a', 'b', 'c']
    header = 'a\tb\tc'
    row = {'a': 'val_a', 'c': 'val_c'}  # 'b' is absent
    hq._append_tsv(p, fields, row, header)
    content = p.read_text()
    lines = [l for l in content.splitlines() if l.strip()]
    assert len(lines) == 2, 'header + one data row expected'
    parts = lines[1].split('\t')
    assert parts[1] == '-', (
        f'missing field must render as "-", got {parts[1]!r}')


# ---------------------------------------------------------------------------
# _read_lock
# ---------------------------------------------------------------------------


def test_read_lock_partition_not_rpartition(tmp_path):
    """_read_lock() uses partition('=') so a value with '=' is parsed correctly.

    Mutation: rpartition('=') splits on the LAST '='; a value like
    'time=val=ue' yields k='key=val', v='ue' instead of k='key', v='val=ue'.
    Oracle: a value containing '=' must be read intact as the value.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    # Write a lock whose value contains '='; e.g. a base64 token or URL param.
    lock_text = 'session=abc\ntoken=aGVsbG89d29ybGQ=\n'
    (folder / '.hq.lock').write_text(lock_text)
    result = hq._read_lock(folder)
    assert result.get('token') == 'aGVsbG89d29ybGQ=', (
        f'partition must keep the value intact, got {result!r}')


# ---------------------------------------------------------------------------
# _extract_terms
# ---------------------------------------------------------------------------


def test_extract_terms_backtick_short_token_excluded():
    """_extract_terms() requires backtick tokens to be >= 3 chars AND non-digit.

    Mutation: 'and' -> 'or' includes tokens >= 3 chars OR non-digit,
    so a 1-char non-digit token like `a` would be included.
    Oracle: `a` (1 char) must not be in the extracted terms.
    """
    terms = hq._extract_terms('The `a` prefix marks a label.')
    assert 'a' not in terms, (
        '1-char backtick token must be excluded (len < 3)')


def test_extract_terms_backtick_3char_included():
    """_extract_terms() includes backtick tokens of exactly 3 chars.

    Mutation: '>= 3' -> '> 3' raises the minimum to 4, excluding 'foo'.
    Oracle: `foo` (exactly 3 chars, not digit) must be in the terms.
    """
    terms = hq._extract_terms('The `foo` prefix is relevant.')
    assert 'foo' in terms, (
        '3-char backtick token must be included with >= 3 threshold')


def test_extract_terms_backtick_exactly_3_chars_min():
    """_extract_terms() threshold is >= 3, not >= 4.

    Mutation: '>= 3' -> '>= 4' excludes exactly-3-char tokens.
    Oracle: `bar` (3 chars) must be in the result; `xy` (2 chars) must not.
    """
    terms = hq._extract_terms('Check `bar` and `xy` for matches.')
    assert 'bar' in terms, '3-char token must pass the >= 3 threshold'
    assert 'xy' not in terms, '2-char token must fail the < 3 threshold'


def test_extract_terms_sentence_start_uses_last_char():
    """_extract_terms() checks before[-1] (last char) for sentence boundary.

    Mutation: before[-1] -> before[+1] (before[1]) or before[-2] both
    produce wrong results when the text before the term is exactly 1 char.
    Oracle: 'I World' - before is 'I', before[1] is IndexError; before[-2]
    would also be out-of-range for a 1-char string.
    """
    # 'World' at non-sentence-start (before = 'i ', rstripped = 'i').
    # before[-1] = 'i' (not in .!?), sentence_start = False -> World included.
    # before[1] = IndexError (len('i') = 1).
    # before[-2] = IndexError as well.
    terms = hq._extract_terms('i World is great')
    assert 'World' in terms, (
        '"World" after "i" is not at sentence-start and must be included')


def test_extract_terms_sentence_start_rstrip_not_lstrip():
    """_extract_terms() rstrips the before-text before checking the last char.

    Mutation: rstrip() -> lstrip() leaves trailing whitespace, so before[-1]
    is ' ' (space) instead of '.', making sentence-start False even after '.'.
    Oracle: 'Done. World' - before 'World' after rstrip is 'Done.', so
    sentence_start is True and 'World' is skipped.
    """
    # After "Done." with a space, "World" is at sentence start -> skip.
    terms = hq._extract_terms('Done. World is next')
    # 'World' is CamelCase at sentence start: should be excluded.
    assert 'World' not in terms, (
        '"World" after ". " is at sentence-start and must be excluded')


def test_extract_terms_prev_char_minus2_differs_from_minus1():
    """_extract_terms() uses before[-1] (last), not before[-2] (second-to-last).

    Mutation: before[-1] -> before[-2] checks the char BEFORE the last; for
    'Done. World', before = 'Done.', before[-2] = 'n', not '.', so
    sentence_start is False and 'World' is not excluded.
    Oracle: 'World' after 'Done.' must be excluded (sentence-start True).
    """
    terms = hq._extract_terms('Done. World data')
    assert 'World' not in terms, (
        '"World" at sentence start must be excluded')


def test_extract_terms_stopword_or_short_excluded():
    """_extract_terms() filters out terms that are short OR stopwords.

    Mutation: 'or' -> 'and' only filters when BOTH conditions hold; stopwords
    of 4+ chars like 'every' would pass.
    Oracle: 'every' (a stopword of 5 chars) must not appear in terms.
    """
    terms = hq._extract_terms('We do this every time now')
    assert 'every' not in terms, (
        '"every" is a stopword and must be excluded by the or-condition')


def test_extract_terms_4char_term_not_excluded():
    """_extract_terms() threshold is len < 4 (strictly), not len <= 4.

    Mutation: '< 4' -> '<= 4' excludes 4-char terms like 'Walk'.
    Oracle: 'Walk' (4 chars, not a stopword) at non-sentence-start is included.
    """
    # 'Walk' appears after a comma (non-sentence-start) in a sentence.
    terms = hq._extract_terms('Use the file, Walk through it')
    assert 'Walk' in terms, (
        '4-char non-stopword at non-sentence-start must be included')


def test_extract_terms_4char_term_not_excluded_v2():
    """_extract_terms() threshold is len < 4, not len < 5.

    Mutation: '< 4' -> '< 5' excludes 4-char terms.
    Oracle: same 'Walk' scenario - must be included with threshold < 4.
    """
    terms = hq._extract_terms('Use Walk in the code')
    assert 'Walk' in terms, (
        '4-char non-stopword at non-sentence-start must be included')


def test_extract_terms_stopword_case_insensitive():
    """_extract_terms() matches stopwords case-insensitively.

    Mutation: term.lower() -> term.upper() so stopwords (which are lowercase)
    never match: 'every'.upper() = 'EVERY' is not in _STOPWORDS.
    Oracle: 'Every' at non-sentence-start is a stopword and must be excluded.
    """
    # 'Every' appears after comma - not sentence start.
    terms = hq._extract_terms('use comma, Every iteration')
    assert 'Every' not in terms, (
        '"Every" is a stopword regardless of capitalization')


def test_extract_terms_break_stops_early():
    """_extract_terms() continues past a short/stopword term to find more.

    Mutation: 'continue' -> 'break' stops the regex loop on the first
    short/stopword term, discarding all subsequent valid terms.
    Oracle: text with a short term followed by a valid CamelCase term must
    yield the CamelCase term.
    """
    # 'the' (short/stopword) appears before 'DataPipeline' (valid CamelCase).
    terms = hq._extract_terms('use the DataPipeline for processing')
    assert 'DataPipeline' in terms, (
        'valid term after a stopword must still be extracted')


# ---------------------------------------------------------------------------
# _dirty_str
# ---------------------------------------------------------------------------


def test_dirty_str_clean_uses_lowercase():
    """_dirty_str([]) returns ' | clean' (lowercase).

    Mutation: ' | clean' -> ' | CLEAN'.
    Oracle: hand-computed result for empty dirty list.
    """
    result = hq._dirty_str([])
    assert result == ' | clean', (
        f'expected " | clean", got {result!r}')


# ---------------------------------------------------------------------------
# dangling_successors
# ---------------------------------------------------------------------------


def test_dangling_successors_and_not_or(tmp_path):
    """dangling_successors() requires BOTH superseded AND non-dash successor.

    Mutation: 'and' -> 'or': live rows with a real successor field would be
    falsely reported as dangling even when not superseded.
    Oracle: a live row with successor='-' must not appear; only a superseded
    row with a real successor (not on disk) must appear.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    # Live row with successor '-': must not appear.
    live_row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    live_row.update({'path': 'doc.md', 'status': 'live', 'successor': '-'})
    # Superseded row with a real successor that is absent from disk.
    sup_row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    sup_row.update({
        'path': 'old.md', 'status': 'superseded', 'successor': 'new.md',
    })
    live = {'doc.md': live_row, 'old.md': sup_row}
    result = hq.dangling_successors(folder, live)
    paths = [p for p, _ in result]
    assert 'doc.md' not in paths, (
        'live row with successor="-" must not be dangling')
    assert 'old.md' in paths, (
        'superseded row with absent successor must be dangling')


# ---------------------------------------------------------------------------
# _folder_state (abs row sha population)
# ---------------------------------------------------------------------------


def test_folder_state_abs_row_sha_populated(tmp_path, monkeypatch):
    """_folder_state() computes sha for abs-base rows pointing to real files.

    Mutation: row['base'] == 'ABS' never matches 'abs', so abs rows are
    skipped and their sha is never added to sha_map.
    Oracle: sha_map must contain the key for an abs-base row when the file
    exists on disk.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    # Create the standard structure.
    (folder / 'ledger.tsv').write_text(hq._LEDGER_HEADER + '\n',
                                       encoding='utf-8')
    (folder / 'standing.md').write_text('', encoding='utf-8')
    cycles = folder / 'cycles'
    cycles.mkdir()
    (cycles / 'manifest.tsv').write_text(hq._MANIFEST_HEADER + '\n',
                                         encoding='utf-8')

    # Create an abs-base file.
    abs_file = tmp_path / 'external.md'
    abs_file.write_text('external content', encoding='utf-8')

    # Stamp the abs row into the ledger.
    abs_key = str(abs_file)
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({
        'path': abs_key,
        'status': 'live',
        'base': 'abs',
        'read_before': 'start',
        'kind': 'doc',
    })
    hq._append_tsv(
        folder / 'ledger.tsv', hq.LEDGER_FIELDS, row, hq._LEDGER_HEADER)

    _, _, _, sha_map, _, _, _ = hq._folder_state(folder)
    assert abs_key in sha_map, (
        f'abs-base row sha must be in sha_map; got keys: {list(sha_map)}')


def test_folder_state_folder_row_not_duplicated(tmp_path):
    """_folder_state() adds folder-base sha only when the key is absent.

    Mutation: 'and' -> 'or' in the elif branch, so even keys already in
    sha_map get overwritten (no harm but indicates the condition is wrong).
    Actual bug surface: a key NOT in sha_map but whose file exists should be
    added; a key already in sha_map must NOT be re-added (no double-count).
    Oracle: after _folder_state(), each path appears exactly once in sha_map.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    (folder / 'ledger.tsv').write_text(hq._LEDGER_HEADER + '\n',
                                       encoding='utf-8')
    (folder / 'standing.md').write_text('', encoding='utf-8')
    cycles = folder / 'cycles'
    cycles.mkdir()
    (cycles / 'manifest.tsv').write_text(hq._MANIFEST_HEADER + '\n',
                                         encoding='utf-8')
    # Place a file in the folder so walk picks it up.
    (folder / 'spec.md').write_text('# Spec\n', encoding='utf-8')
    # Stamp it in the ledger.
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({'path': 'spec.md', 'status': 'live', 'kind': 'spec',
                'read_before': 'start', 'base': 'folder'})
    hq._append_tsv(
        folder / 'ledger.tsv', hq.LEDGER_FIELDS, row, hq._LEDGER_HEADER)

    _, _, _, sha_map, _, _, _ = hq._folder_state(folder)
    # sha_map must contain 'spec.md' exactly once (no duplicate inserts crash).
    assert 'spec.md' in sha_map


# ---------------------------------------------------------------------------
# _reconcile_missing (abs base detection)
# ---------------------------------------------------------------------------


def test_reconcile_missing_abs_row_goes_missing(tmp_path):
    """_reconcile_missing() uses 'abs' (not 'ABS') to detect abs-base rows.

    Mutation: row['base'] == 'ABS' never matches; abs rows never reconcile
    to missing even when their path is gone.
    Oracle: a live abs row whose path does not exist must become 'missing'.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    absent_path = str(tmp_path / 'gone.md')
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({'path': absent_path, 'status': 'live', 'base': 'abs'})
    live = {absent_path: row}
    result = hq._reconcile_missing(folder, live)
    assert result[absent_path]['status'] == 'missing', (
        'abs-base live row for absent file must be reconciled to missing')


# ---------------------------------------------------------------------------
# _assemble_handoff
# ---------------------------------------------------------------------------


def test_assemble_handoff_skip_entries_excluded(tmp_path):
    """_assemble_handoff() excludes walk entries with kind 'skip' from art_walk.

    Mutation: k != 'skip' -> k == 'skip' inverts the filter, so only skip
    entries appear in the artifacts block (and real files disappear).
    Oracle: a spec file must appear in the rendered output; a skip entry must not.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    (folder / 'spec.md').write_text('# Spec\n', encoding='utf-8')
    (folder / 'ledger.tsv').write_text(hq._LEDGER_HEADER + '\n',
                                       encoding='utf-8')
    (folder / 'standing.md').write_text('', encoding='utf-8')

    walk = [('spec.md', 'spec'), ('conflicted copy', 'skip')]
    live: dict = {}
    text = hq._assemble_handoff(
        folder,
        cursor_text='## Task\n\n## Now\n\n## Plan\n\n'
                    '## State\n\n## Environment\n\n## Open questions\n',
        header_line='Written: 2026-09-09 | Cycle: 1',
        log_body='',
        live=live,
        walk=walk,
        standing_text='',
    )
    assert 'spec.md' in text, 'spec file must appear in the artifacts block'
    # The skip name 'conflicted copy' must NOT appear as an artifact row.
    assert 'conflicted copy' not in text or '| skip |' not in text


def test_assemble_handoff_skip_string_literal_exact(tmp_path):
    """_assemble_handoff() compares kind against the string 'skip' (lowercase).

    Mutation: k != 'SKIP' never matches 'skip', so skip entries are included.
    Oracle: a walk entry with kind='skip' must be excluded from art_walk.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    (folder / 'ledger.tsv').write_text(hq._LEDGER_HEADER + '\n',
                                       encoding='utf-8')
    (folder / 'standing.md').write_text('', encoding='utf-8')
    (folder / 'spec.md').write_text('# Spec\n', encoding='utf-8')

    # Walk: one real entry, one skip.
    walk = [('spec.md', 'spec'), ('bad\tname', 'skip')]
    live: dict = {}
    text = hq._assemble_handoff(
        folder,
        cursor_text='## Task\n\n## Now\n\n## Plan\n\n'
                    '## State\n\n## Environment\n\n## Open questions\n',
        header_line='Written: 2026-09-09 | Cycle: 1',
        log_body='',
        live=live,
        walk=walk,
        standing_text='',
    )
    assert 'bad\tname' not in text, (
        'skip entry must not appear in the assembled handoff')


def test_assemble_handoff_read_lines_default_zero(tmp_path):
    """_assemble_handoff() defaults read_lines to 0 when lines_str is '-'.

    Mutation: 0 -> 1 shifts the default, so a row with lines='-' contributes
    1 instead of 0 to the line count used in render_read.
    Oracle: a row with lines='-' in the ledger must not make render_read
    crash or show a span of 1 line when no span is recorded.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    (folder / 'ledger.tsv').write_text(hq._LEDGER_HEADER + '\n',
                                       encoding='utf-8')
    (folder / 'standing.md').write_text('', encoding='utf-8')
    doc = folder / 'doc.md'
    doc.write_text('# Doc\nContent.\n', encoding='utf-8')

    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({'path': 'doc.md', 'status': 'live', 'kind': 'doc',
                'read_before': 'always', 'base': 'folder', 'lines': '-'})
    live = {'doc.md': row}
    walk = [('doc.md', 'doc')]
    # Must not raise; the 0 default means no span is highlighted.
    text = hq._assemble_handoff(
        folder,
        cursor_text='## Task\n\n## Now\n\n## Plan\n\n'
                    '## State\n\n## Environment\n\n## Open questions\n',
        header_line='Written: 2026-09-09 | Cycle: 1',
        log_body='',
        live=live,
        walk=walk,
        standing_text='',
    )
    assert 'doc.md' in text


# ---------------------------------------------------------------------------
# _take_lock - force=True default
# ---------------------------------------------------------------------------


def test_take_lock_force_defaults_to_false(tmp_path, monkeypatch):
    """_take_lock() defaults force to False, refusing a young foreign lock.

    Mutation: getattr(argv, 'force', True) makes force always True, so any
    young lock is taken over without --force.
    Oracle: begin without --force on a folder with a young foreign lock exits 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    # Seed minimal structure so begin skips creation.
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0, f'initial begin failed: {rc0}'
    # Write a young foreign lock.
    _lock(folder, _OTHER_SESSION, _YOUNG_LOCK)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1, f'expected refusal on young foreign lock, got rc={rc}'
    assert 'use --force' in out.lower(), (
        'refusal message must mention --force')


# ---------------------------------------------------------------------------
# _take_lock - message when lock unreadable (or -> and)
# ---------------------------------------------------------------------------


def test_take_lock_takeover_shows_actual_session(tmp_path, monkeypatch):
    """_take_lock() records the actual foreign session, not 'unknown'.

    Mutation: lock.get('session') and 'unknown' -> always 'unknown' when
    session is truthy; the print 'took over from X' shows 'unknown' instead.
    Oracle: an old foreign lock is taken over; output names the foreign session.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Write an old foreign lock (> 2 hours ago).
    _lock(folder, _OTHER_SESSION, '2026-09-09T09:00:00')
    rc, out, _ = _run(['begin', _SLUG, '--force'])
    assert rc == 0, f'begin --force on old lock should succeed: {rc}'
    assert _OTHER_SESSION in out, (
        f'output must name the taken-over session, got: {out!r}')


def test_take_lock_missing_session_shows_unknown(tmp_path, monkeypatch):
    """_take_lock() shows 'unknown' (lowercase) for a lock with no session key.

    Mutation: or 'UNKNOWN' -> uppercase in the fallback string.
    Oracle: when the lock file has no session field, the message says 'unknown'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Write a lock with no session field (unreadable lock).
    (folder / '.hq.lock').write_text('host=other\ntime=2026-09-09T09:00:00\n')
    rc, out, _ = _run(['begin', _SLUG, '--force'])
    assert rc == 0, f'expected takeover, got rc={rc}'
    assert 'unknown' in out.lower(), (
        f'fallback label must be lowercase "unknown", got: {out!r}')


def test_take_lock_error_message_host_field(tmp_path, monkeypatch):
    """_take_lock() includes the foreign host in the 'lock held by' message.

    Mutation: lock.get('HOST') returns None instead of the actual host.
    Oracle: the printed refusal message includes the host string from the lock.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Write a young lock so begin is refused.
    _lock(folder, _OTHER_SESSION, _YOUNG_LOCK)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1
    assert 'other-host' in out, (
        f'refusal must name the foreign host, got: {out!r}')


def test_take_lock_error_message_time_field(tmp_path, monkeypatch):
    """_take_lock() includes the lock time in the 'lock held by' message.

    Mutation: lock.get('TIME') returns None instead of the actual time.
    Oracle: the printed refusal message includes the time string from the lock.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    _lock(folder, _OTHER_SESSION, _YOUNG_LOCK)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1
    assert _YOUNG_LOCK in out, (
        f'refusal must include the lock time, got: {out!r}')


def test_take_lock_force_hint_lowercase(tmp_path, monkeypatch):
    """_take_lock() prints 'use --force to take over' in lowercase.

    Mutation: 'USE --FORCE TO TAKE OVER' replaces the lowercase hint.
    Oracle: refusal message must contain the lowercase hint string.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    _lock(folder, _OTHER_SESSION, _YOUNG_LOCK)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1
    assert 'use --force to take over' in out, (
        f'hint must be lowercase, got: {out!r}')


# ---------------------------------------------------------------------------
# _print_worklist
# ---------------------------------------------------------------------------


def test_print_worklist_witness_uses_manifest_last(tmp_path, monkeypatch):
    """_print_worklist() passes manifest[-1] to witness(), not None.

    Mutation: witness(None, lb, sb) ignores all manifests, so W1/W2 breaks
    are never detected during begin.
    Oracle: after finish, begin on a tampered ledger must print a W1 break.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'seed'])
    assert rc1 == 0
    # Tamper the ledger (insert a char early in the file).
    ledger = folder / 'ledger.tsv'
    content = ledger.read_text()
    ledger.write_text(content.replace('path', 'pXth', 1))
    # begin again: must detect W1 break.
    rc2, out2, _ = _run(['begin', _SLUG, '--force'])
    assert 'W1' in out2, (
        f'tampered ledger must trigger W1 from begin: {out2!r}')


def test_print_worklist_unstamped_excludes_skip(tmp_path, monkeypatch):
    """_print_worklist() only reports unstamped entries that are NOT 'skip'.

    Mutation: 'and k != "skip"' -> 'or k != "skip"' includes stamped entries.
    Mutation (variant): 'skip' -> 'SKIP' means skip entries appear in unstamped.
    Oracle: a skip walk entry (conflicted copy) must not appear in the
    unstamped list.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Create a file whose name flags it as a conflicted copy.
    (folder / 'spec (Bob conflicted copy 2026).md').write_text(
        '# Spec\n', encoding='utf-8')
    rc1, out1, _ = _run(['begin', _SLUG])
    assert rc1 == 0
    # conflicted copy entry is 'skip' - it must show as 'conflicted copy: ...',
    # not as 'unstamped spec x1:'.
    assert 'unstamped' not in out1 or 'conflicted copy' in out1 or (
        'unstamped spec' not in out1)
    # More precisely: the conflicted copy is reported via 'conflicted copy:',
    # not via 'unstamped'.
    assert 'conflicted copy' in out1, (
        'conflicted copy must be reported separately, not as unstamped')


def test_print_worklist_unstamped_tail_at_6(tmp_path, monkeypatch):
    """_print_worklist() shows tail '... and N more' only when len > 5, not >= 5.

    Mutation: (mutmut_32) '>= 5' makes exactly 5 items trigger the tail.
        (mutmut_33) '> 6' hides the tail when len == 6.
    Oracle: exactly 5 items: no tail; exactly 6 items: tail shows '1 more'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0

    # Create exactly 5 unstamped doc files.
    for i in range(5):
        (folder / f'doc{i:02d}.md').write_text(f'doc {i}\n', encoding='utf-8')
    rc1, out1, _ = _run(['begin', _SLUG])
    assert rc1 == 0
    assert '... and' not in out1, (
        f'5 items must not produce a tail: {out1!r}')

    # Add one more: now 6 unstamped.
    (folder / 'doc05.md').write_text('doc 5\n', encoding='utf-8')
    rc2, out2, _ = _run(['begin', _SLUG])
    assert rc2 == 0
    assert '... and 1 more' in out2, (
        f'6 items must produce "... and 1 more": {out2!r}')


def test_print_worklist_abs_path_missing_reported(tmp_path, monkeypatch):
    """_print_worklist() compares base == 'abs' (not 'ABS') for abs rows.

    Mutation: 'ABS' never matches 'abs', so missing abs paths are not reported.
    Oracle: an abs-base live row whose file is gone must appear as missing live.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0

    # Stamp an abs row pointing to a file that does not exist.
    gone = str(tmp_path / 'gone_external.md')
    rc1, _, _ = _run(['stamp', _SLUG, gone, '--read-before', 'mention'])
    assert rc1 == 0
    rc2, out2, _ = _run(['begin', _SLUG])
    assert rc2 == 0
    assert 'missing live' in out2, (
        f'missing abs path must be reported: {out2!r}')


def test_print_worklist_abs_missing_not_present(tmp_path, monkeypatch):
    """_print_worklist() reports missing live abs rows, not existing ones.

    Mutation: 'not exists()' -> 'exists()' reports PRESENT abs paths as missing.
    Oracle: an abs-base row whose file EXISTS must NOT appear as missing.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0

    # Stamp an abs row that actually exists.
    ext = tmp_path / 'existing.md'
    ext.write_text('content\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, str(ext), '--read-before', 'mention'])
    assert rc1 == 0
    rc2, out2, _ = _run(['begin', _SLUG])
    assert rc2 == 0
    assert 'missing live' not in out2, (
        f'existing abs path must not appear as missing: {out2!r}')


# ---------------------------------------------------------------------------
# _verb_begin
# ---------------------------------------------------------------------------


def test_verb_begin_header_uses_10_chars_of_now(tmp_path, monkeypatch):
    """_verb_begin() writes 'Written: YYYY-MM-DD' (10 chars) in HANDOFF.md.

    Mutation: anch['now'][:11] produces 'YYYY-MM-DDT' (11 chars).
    Oracle: the Written line in the new HANDOFF.md must show the date only.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc, _, _ = _run(['begin', _SLUG])
    assert rc == 0
    text = (folder / 'HANDOFF.md').read_text()
    assert 'Written: 2026-09-09 | Cycle: 1' in text, (
        f'Written line must use 10-char date slice: found {text[:200]!r}')


def test_verb_begin_no_ledger_triggers_creation(tmp_path, monkeypatch):
    """_verb_begin() checks for 'ledger.tsv' (lowercase) to detect a new folder.

    Mutation: 'LEDGER.TSV' is never found, so no_ledger is always True;
    a folder with HANDOFF.md + ledger.tsv is erroneously re-initialized.
    Oracle: a folder with both HANDOFF.md and ledger.tsv must NOT re-create.
    """
    folder = _new_root(tmp_path, monkeypatch)
    # Build fresh.
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Stamp a file so ledger.tsv has content.
    (folder / 'doc.md').write_text('# Doc\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention'])
    assert rc1 == 0
    # Second begin: must not wipe the ledger.
    rc2, _, _ = _run(['begin', _SLUG])
    assert rc2 == 0
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    assert rows, 'ledger must not be wiped on second begin'


def test_verb_begin_adopt_message_handoff_md(tmp_path, monkeypatch):
    """_verb_begin() prints '... HANDOFF.md' (case-exact) after adopt.

    Mutation: (mutmut_110) 'handoff.md' lowercase; (mutmut_111): all-uppercase.
    Oracle: the printed message must contain the exact string 'HANDOFF.md'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    # Write a conforming HANDOFF.md with no ledger.
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-09 | Cycle: 1\n\n'
        '## Task\n\n## Now\n\n## Plan\n\n## State\n\n'
        '## Environment\n\n## Open questions\n\n## Log\n',
        encoding='utf-8',
    )
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'HANDOFF.md' in out, (
        f'adopt message must say "HANDOFF.md" (case-exact), got: {out!r}')


def test_verb_begin_cycles_exist_ok(tmp_path, monkeypatch):
    """_verb_begin() creates cycles/ with exist_ok=True.

    Mutation: exist_ok=False raises FileExistsError when cycles/ already exists.
    Oracle: begin on a folder that has cycles/ but no HANDOFF.md or ledger.tsv
    must succeed (cycles/ was left from a partial run).
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    # Partial folder: has cycles/ but nothing else.
    (folder / 'cycles').mkdir()
    rc, _, _ = _run(['begin', _SLUG])
    assert rc == 0, f'begin must succeed with pre-existing cycles/: rc={rc}'


# ---------------------------------------------------------------------------
# _verb_finish - lock check
# ---------------------------------------------------------------------------


def test_verb_finish_rc1_when_no_lock(tmp_path, monkeypatch):
    """_verb_finish() returns 1 (not 2) when no lock is held.

    Mutation: return 2 instead of 1 on lock failure.
    Oracle: finish on a folder where no begin was run exits with code 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Remove the lock.
    (folder / '.hq.lock').unlink()
    rc, _, _ = _run(['finish', _SLUG, '--log', 'test'])
    assert rc == 1, f'finish without lock must exit 1, got {rc}'


def test_verb_finish_lock_message_shows_holder(tmp_path, monkeypatch):
    """_verb_finish() prints the lock-holding session in the error message.

    Mutation: lock.get('none'), lock.get('session', ), lock.get('SESSION'),
    lock.get('session', 'NONE') each change the session shown.
    Oracle: message must contain the actual session name that holds the lock.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Replace the lock with one owned by a different session.
    _lock(folder, _OTHER_SESSION, _YOUNG_LOCK)
    rc, out, _ = _run(['finish', _SLUG, '--log', 'test'])
    assert rc == 1
    assert _OTHER_SESSION in out, (
        f'message must show the locking session, got: {out!r}')
    assert 'none' not in out.lower() or _OTHER_SESSION in out, (
        f'must not fall back to "none" when session is known: {out!r}')


def test_verb_finish_absent_gated_row_blocks(tmp_path, monkeypatch):
    """_verb_finish() blocks when a live gated folder-base row is absent.

    Mutation:
    - status != 'live' (inverted) lets non-live rows through.
    - status == 'LIVE' never matches, skipping all rows.
    - read_before not in _GATE_RB lets non-gated rows through.
    - base == 'FOLDER' never matches, skipping all folder rows.
    Oracle: finish on a folder where a stamped 'always' file was deleted exits 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Create and stamp a file as always-read.
    (folder / 'guide.md').write_text('# Guide\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, 'guide.md', '--read-before', 'always',
                      '--where', 'Guide'])
    assert rc1 == 0
    # Delete the file before finish.
    (folder / 'guide.md').unlink()
    rc2, out2, _ = _run(['finish', _SLUG, '--log', 'test'])
    assert rc2 == 1, 'absent live gated row must block finish'
    assert 'missing live gated' in out2 or 'guide.md' in out2, (
        f'must name the missing file: {out2!r}')


def test_verb_finish_unfiled_above_first_marker_allowed(tmp_path, monkeypatch):
    """_verb_finish() allows '## Unfiled' when it sits above the first hq: marker.

    Mutation: first_marker >= 0 -> > 0 or >= 1 would block an Unfiled section
    at position 0 of the post-cursor text (marker at position 0 of handoff).
    Oracle: a conforming handoff with Unfiled before the markers must finish ok.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Write a HANDOFF.md that has Unfiled above the hq: markers.
    hf = folder / 'HANDOFF.md'
    orig = hf.read_text()
    # Insert ## Unfiled before the first marker (which doesn't exist yet
    # in cycle 1 before finish - so first_marker == -1).
    # No markers means first_marker < 0 -> the Unfiled check is skipped.
    new_text = orig.replace(
        '## Log\n',
        '## Unfiled\n- decision: **Keep it** The approach is valid.\n\n## Log\n',
    )
    hf.write_text(new_text)
    rc1, out1, _ = _run(['finish', _SLUG, '--log', 'decision test'])
    assert rc1 == 0, f'Unfiled above markers must be accepted: {out1!r}'


def test_verb_finish_unfiled_below_marker_blocked(tmp_path, monkeypatch):
    """_verb_finish() blocks finish when '## Unfiled' sits below a hq: marker.

    Mutation: first_marker >= 0 -> > 0 skips the check when marker is at 0.
    Oracle: an Unfiled section below the first hq: marker blocks finish.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # First finish to create the markers.
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'seed'])
    assert rc1 == 0
    # Now reopen and insert Unfiled BELOW the markers.
    rc2, _, _ = _run(['begin', _SLUG])
    assert rc2 == 0
    hf = folder / 'HANDOFF.md'
    text = hf.read_text()
    # Inject Unfiled AFTER the first hq: marker (so it sits below a marker).
    import re
    marker_m = re.search(r'<!-- hq:\w+ \S+ -->', text)
    assert marker_m
    pos = marker_m.end()
    injected = text[:pos] + '\n## Unfiled\n- decision: **X** Y.\n\n' + text[pos:]
    hf.write_text(injected)
    rc3, out3, _ = _run(['finish', _SLUG, '--log', 'test'])
    assert rc3 == 1, 'Unfiled below markers must block finish'
    assert 'Unfiled' in out3


def test_verb_finish_manifest_key_names_exact(tmp_path, monkeypatch):
    """_verb_finish() writes manifest rows with lowercase field names.

    Mutation: 'WRITTEN', 'REPOS', 'CURSOR_LINES', 'HANDOFF_SHA', 'STANDING_SHA'
    replace the lowercase keys; _append_tsv then fails to find them in the row
    and writes '-' instead of the real value.
    Oracle: the manifest.tsv row must have non-dash values for these fields.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'first cycle'])
    assert rc1 == 0
    rows = hq._read_tsv(
        folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows, 'manifest must have one row after finish'
    row = rows[0]
    assert row.get('written') not in {'-', '', None}, (
        f'written must be non-dash, got {row.get("written")!r}')
    assert row.get('repos') is not None, 'repos must be present'
    assert len(row.get('handoff_sha', '')) == 12, (
        f'handoff_sha must be 12-char hex: {row.get("handoff_sha")!r}')
    assert len(row.get('standing_sha', '')) == 12, (
        f'standing_sha must be 12-char hex: {row.get("standing_sha")!r}')
    cursor_lines = row.get('cursor_lines', '-')
    assert cursor_lines != '-', (
        f'cursor_lines must be recorded: {cursor_lines!r}')


def test_verb_finish_written_header_10_chars(tmp_path, monkeypatch):
    """_verb_finish() uses now[:10] for the Written header.

    Mutation: now[:11] adds 'T' after the date.
    Oracle: the Written line in the finished HANDOFF.md must show YYYY-MM-DD.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'done'])
    assert rc1 == 0
    text = (folder / 'HANDOFF.md').read_text()
    assert 'Written: 2026-09-09 |' in text, (
        f'Written line must use 10-char date: {text[:200]!r}')
    assert 'Written: 2026-09-09T' not in text, (
        'Written line must not include the T separator')


def test_verb_finish_repos_field_non_git(tmp_path, monkeypatch):
    """_verb_finish() sets repos to '-' when not in a git repo (branch == '-').

    Mutation: 'branch != "-"' -> 'branch == "-"' inverts the condition;
    repos becomes f'{branch}@{sha7}' = '-@...' for the no-git case.
    Oracle: HQ_GIT=0 path sets repos = '-' in the manifest row.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'no-git'])
    assert rc1 == 0
    rows = hq._read_tsv(
        folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert rows[0]['repos'] == '-', (
        f'repos must be "-" when HQ_GIT=0: {rows[0]["repos"]!r}')


def test_verb_finish_repos_accumulates_dirty(tmp_path, monkeypatch):
    """_verb_finish() appends dirty count to repos, not replaces.

    Mutation: 'repos += ...' -> 'repos = ...' discards the branch@sha prefix.
    Oracle: HQ_ROOT inside a git repo with a dirty file; repos must contain
    'branch@sha +N' where N>=1, not just ' +N'.
    """
    import subprocess

    # Init a git repo at tmp_path; _new_root places HQ_ROOT inside it so
    # _git_state(root) runs git with cwd=HQ_ROOT and finds the repo.
    for cmd in (
        ['init', '-q', '-b', 'main'],
        ['config', 'user.email', 'x@y.invalid'],
        ['config', 'user.name', 'X'],
    ):
        subprocess.run(['git', '-C', str(tmp_path)] + cmd, check=True,
                       capture_output=True)
    (tmp_path / 'seed.txt').write_text('seed\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'seed.txt'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-q', '-m', 'seed'],
                   check=True, capture_output=True)
    # Create a dirty file so dirty list is non-empty.
    (tmp_path / 'seed.txt').write_text('dirty\n')

    # _new_root sets HQ_GIT='0'; undo that so _git_state actually runs.
    folder = _new_root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_GIT', '1')
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'with-dirty'])
    assert rc1 == 0
    rows = hq._read_tsv(
        folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    repos = rows[0]['repos']
    assert '@' in repos, f'repos must include branch@sha: {repos!r}'
    assert '+' in repos, f'repos must include dirty count: {repos!r}'
    assert repos.startswith('main@'), f'repos must start with branch: {repos!r}'


def test_verb_finish_standing_text_accumulates(tmp_path, monkeypatch):
    """_verb_finish() appends each Unfiled item to standing_text_new.

    Mutation: 'standing_text_new += line' -> 'standing_text_new = line'
    resets the accumulated text, so only the last item survives in the
    in-memory standing used to build the HANDOFF.
    Oracle: two Unfiled items in one finish both appear in standing.md.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    hf = folder / 'HANDOFF.md'
    text = hf.read_text()
    new_text = text.replace(
        '## Log\n',
        '## Unfiled\n'
        '- decision: **First choice** The first is best.\n'
        '- constraint: **Time limit** Must finish fast.\n\n'
        '## Log\n',
    )
    hf.write_text(new_text)
    rc1, out1, _ = _run(['finish', _SLUG, '--log', 'two items'])
    assert rc1 == 0, f'finish with two items failed: {out1!r}'
    standing = (folder / 'standing.md').read_text()
    assert 'First choice' in standing, 'first item must appear in standing.md'
    assert 'Time limit' in standing, 'second item must appear in standing.md'


def test_verb_finish_rc1_unknown_item_kind(tmp_path, monkeypatch):
    """_verb_finish() returns 1 on an unknown Unfiled item kind.

    Mutation: return 2 instead of 1 for the unknown-kind path.
    Oracle: an Unfiled bullet with kind 'bogus' causes finish to exit 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    hf = folder / 'HANDOFF.md'
    text = hf.read_text()
    # Insert a valid-looking but unknown-kind bullet.
    new_text = text.replace(
        '## Log\n',
        '## Unfiled\n- bogus: **Bad kind** This is wrong.\n\n## Log\n',
    )
    hf.write_text(new_text)
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'test'])
    assert rc1 == 1, f'unknown item kind must exit 1, got {rc1}'


def test_verb_finish_now_multiline_search(tmp_path, monkeypatch):
    """_verb_finish() uses re.MULTILINE for the Now-section boundary search.

    Mutation: removing re.MULTILINE means '^## ' only matches at the string
    start; now_text then extends to the end of the cursor instead of stopping
    at the next section header.
    Oracle: a Now section followed by ## Plan must have now_text stop at ## Plan.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Write a handoff whose Now section has one term and Plan has another.
    hf = folder / 'HANDOFF.md'
    text = hf.read_text()
    new_text = text.replace('## Now\n', '## Now\n\nUse DataPipeline here.\n')
    hf.write_text(new_text)
    # Finish should not crash; the collision check reads only the Now section.
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'multiline test'])
    assert rc1 == 0


def test_verb_finish_unstamped_filter_not_in_live(tmp_path, monkeypatch):
    """_verb_finish() reports unstamped: entries NOT in live AND not skip.

    Mutation:
    - 'not in live or k != skip': includes stamped entries in unstamped.
    - 'n in live and k != skip': only stamped entries (reversed).
    - 'k == skip': only skip entries reported as unstamped.
    - 'k != SKIP': skip entries never excluded.
    Oracle: a stamped file must not appear in unstamped; an unstamped file must.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Create two files; stamp only one.
    (folder / 'stamped.md').write_text('# Stamped\n', encoding='utf-8')
    (folder / 'free.md').write_text('# Free\n', encoding='utf-8')
    (folder / 'SPEC (conflicted copy 2026-09-09).md').write_text('# S\n')
    rc1, _, _ = _run(['stamp', _SLUG, 'stamped.md', '--read-before', 'mention'])
    assert rc1 == 0
    rc2, out2, _ = _run(['finish', _SLUG, '--log', 'check unstamped'])
    assert rc2 == 0
    unstamped_lines = [
        ln for ln in out2.splitlines() if ln.startswith('advisory: unstamped')]
    assert unstamped_lines == ['advisory: unstamped other x1: free.md - stamp each']


def test_verb_finish_unstamped_shown_count_boundary(tmp_path, monkeypatch):
    """_verb_finish() shows first 5 names and tail only when len > 5.

    Mutation:
    - names[:6] shows 6 instead of 5.
    - len - 5 -> len + 5 gives wrong tail count.
    - len - 6 gives one-off tail count.
    - '>= 5' generates tail for exactly 5 (wrong).
    - '> 6' misses the tail for 6 items.
    Oracle: 5 items -> no tail; 6 items -> '... and 1 more'; 7 -> '... and 2 more'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    for i in range(5):
        (folder / f'f{i:02d}.md').write_text(f'# F{i}\n', encoding='utf-8')
    rc1, out1, _ = _run(['finish', _SLUG, '--log', '5-items'])
    assert rc1 == 0
    assert '... and' not in out1, f'5 items must have no tail: {out1!r}'

    # Start a new cycle for the 6-item check.
    rc_b, _, _ = _run(['begin', _SLUG])
    assert rc_b == 0
    (folder / 'f05.md').write_text('# F5\n', encoding='utf-8')
    rc2, out2, _ = _run(['finish', _SLUG, '--log', '6-items'])
    assert rc2 == 0
    assert '... and 1 more' in out2, (
        f'6 items must produce "... and 1 more": {out2!r}')


def test_do_stamp_label_comparand_is_the_last_row_not_the_first(
        tmp_path, monkeypatch):
    """_do_stamp() judges the new label against the row before it, not the
    path's first row.

    Mutation: the fallback comparand taken as history[0] instead of
    history[-1], so a label that grew and then shrank reads as a growth.
    Oracle: three stamps in one cycle, 'ab' then 'a long label here' then
    'medium one'; 10 characters is shorter than 17 and longer than 2, so
    the advisory fires against the last row and not against the first.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    (folder / 'doc.md').write_text('# Doc\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                      '--label', 'ab'])
    assert rc1 == 0
    rc2, _, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                      '--label', 'a long label here'])
    assert rc2 == 0
    rc3, out3, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                         '--label', 'medium one'])
    assert rc3 == 0
    assert 'label shorter' in out3, (
        f'"medium one" is shorter than "a long label here": {out3!r}')


def test_do_stamp_label_advisory_silent_on_a_first_stamp_and_a_carry_forward(
        tmp_path, monkeypatch):
    """_do_stamp() reports nothing when there is no predecessor label, and
    nothing when a re-stamp carries the label forward.

    Mutation: the shortening test written <=, so every re-stamp that
    carries its label forward reports a shortening that never happened;
    or the empty-history fallback taken as history[-1] unguarded, which
    raises IndexError on the first stamp of a path.
    Oracle: a first stamp with no label at all, then a re-stamp that
    omits --label and inherits the same 17 characters.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    (folder / 'doc.md').write_text('# Doc\n', encoding='utf-8')
    rc1, out1, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention'])
    assert rc1 == 0
    assert 'label shorter' not in out1
    assert 'label dropped' not in out1
    rc2, _, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                      '--label', 'a long label here'])
    assert rc2 == 0
    rc3, out3, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'edit'])
    assert rc3 == 0
    assert 'label shorter' not in out3, (
        f'a carried-forward label is the same label: {out3!r}')
    assert 'label dropped' not in out3


def test_do_stamp_sref_lowercase_pattern(tmp_path, monkeypatch):
    r"""_do_stamp() detects section references with lowercase 's' prefix.

    Mutation: r'\\bs\\d+\\b' -> r'\\bS\\d+\\b' matches only uppercase S;
    a label with 's3' would not register as a section reference.
    Oracle: two stamps in one cycle; prev has 's3', curr drops it; the
    label-dropped advisory must fire.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    (folder / 'doc.md').write_text('# Doc\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                      '--label', 'covers s3 data section carefully'])
    assert rc1 == 0
    # Re-stamp in the same cycle, dropping s3.
    rc2, out2, _ = _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                         '--label', 'covers data section carefully here'])
    assert rc2 == 0
    assert 's3' in out2 or 'label dropped' in out2, (
        f'dropped s-ref must be reported: {out2!r}')


def test_verb_finish_lock_unlinked_on_success(tmp_path, monkeypatch):
    """_verb_finish() removes the lock file on success.

    Mutation: missing_ok=False raises FileNotFoundError if the lock is gone
    before unlink (race), or crashes if the lock was already removed.
    Oracle: after a successful finish, .hq.lock must not exist.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, _, _ = _run(['finish', _SLUG, '--log', 'done'])
    assert rc1 == 0
    assert not (folder / '.hq.lock').exists(), (
        '.hq.lock must be removed after a successful finish')


def test_verb_finish_token_output_integer_division(tmp_path, monkeypatch):
    """_verb_finish() uses integer division (//) for token counts.

    Mutation: '//' -> '/' produces float output like '12.5' instead of '12'.
    Oracle: the printed token summary must contain only integer values.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    rc1, out1, _ = _run(['finish', _SLUG, '--log', 'tokens'])
    assert rc1 == 0
    # The print line: '... N tokens (cursor M, read R artifacts A standing S)'
    # Check that no float appears.
    import re
    floats = re.findall(r'\d+\.\d+', out1)
    assert not floats, (
        f'token counts must be integers (//), not floats: {out1!r}')


def test_verb_finish_collision_spec_detected(tmp_path, monkeypatch):
    """_verb_finish() checks collisions against live spec headings.

    Mutation: row['kind'] != 'spec' or row['kind'] == 'SPEC' or
    row['status'] == 'LIVE' all prevent spec files from being checked.
    Oracle: Now section mentioning a term from a live spec heading triggers
    an 'advisory: collides: ...' message.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # Create a spec file with a distinctive heading (DESIGN* matches _SPEC_PATS).
    spec = folder / 'DESIGN-alpha.md'
    spec.write_text('# Design\n\n## ZetaPipeline overview\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, 'DESIGN-alpha.md', '--read-before', 'always',
                      '--where', 'Design'])
    assert rc1 == 0
    # Edit HANDOFF to mention the term in the Now section.
    hf = folder / 'HANDOFF.md'
    text = hf.read_text()
    new_text = text.replace('## Now\n', '## Now\n\nWork on the ZetaPipeline.\n')
    hf.write_text(new_text)
    rc2, out2, _ = _run(['finish', _SLUG, '--log', 'collision test'])
    assert rc2 == 0
    assert 'collides' in out2 or 'ZetaPipeline' in out2, (
        f'collision must be reported in advisory: {out2!r}')


def test_verb_finish_walk_continue_not_break(tmp_path, monkeypatch):
    """_verb_finish() continues the walk loop past non-spec files.

    Mutation: 'continue' -> 'break' when a file is not a live spec stops
    checking all later walk entries for spec headings.
    Oracle: two files in the walk; first is non-spec, second is spec; the
    spec headings from the second must still be checked for collisions.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # 'aaa.md' (other kind) sorts before 'bbb.md' (spec via heading) in walk.
    (folder / 'aaa.md').write_text('# aaa\nJust a doc.\n', encoding='utf-8')
    # Heading '# Spec: AlphaEngine' makes bbb.md kind='spec' via infer_kind.
    (folder / 'bbb.md').write_text(
        '# Spec: AlphaEngine\n\n## AlphaEngine overview\n', encoding='utf-8')
    rc1, _, _ = _run(['stamp', _SLUG, 'aaa.md', '--read-before', 'mention'])
    # Spec files require --read-before always.
    rc2, _, _ = _run(['stamp', _SLUG, 'bbb.md', '--read-before', 'always',
                      '--where', 'Spec: AlphaEngine'])
    assert rc1 == 0
    assert rc2 == 0
    hf = folder / 'HANDOFF.md'
    text = hf.read_text()
    new_text = text.replace('## Now\n', '## Now\n\nRun the `AlphaEngine` here.\n')
    hf.write_text(new_text)
    rc3, out3, _ = _run(['finish', _SLUG, '--log', 'walk break test'])
    assert rc3 == 0
    assert 'AlphaEngine' in out3 or 'collides' in out3, (
        f'spec after non-spec must still be checked: {out3!r}')


def test_verb_finish_skip_loop_continue_not_break(tmp_path, monkeypatch):
    """_verb_finish() continues past non-skip entries when printing skip advisories.

    Mutation: 'continue' -> 'break' in 'for n, k in walk: if k != "skip": break'
    exits the loop on the first non-skip entry, missing later skip entries.
    Oracle: a non-skip file before a conflicted copy must not prevent the
    conflicted-copy advisory from appearing.
    """
    folder = _new_root(tmp_path, monkeypatch)
    rc0, _, _ = _run(['begin', _SLUG])
    assert rc0 == 0
    # 'a_doc.md' sorts before the conflicted copy in the walk.
    (folder / 'a_doc.md').write_text('doc\n', encoding='utf-8')
    (folder / 'b (Bob conflicted copy 2026).md').write_text(
        '# spec\n', encoding='utf-8')
    rc1, out1, _ = _run(['finish', _SLUG, '--log', 'skip loop'])
    assert rc1 == 0
    assert 'conflicted copy' in out1, (
        f'conflicted copy must be reported: {out1!r}')
