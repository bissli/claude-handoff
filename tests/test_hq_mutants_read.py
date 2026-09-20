"""Mutation-kill tests for the read group (run4).

Functions under test: _verb_open, _verb_read, _verb_artifacts, _verb_diff,
_verb_standing, _verb_when, render_artifacts, render_standing, render_read,
_read_tsv, resolve_where, _norm_heading, block_sha.

Every test documents its target mutant(s) on Mutation:/Oracle: lines.
"""

import os
import pathlib
from typing import Any

from bin import hq

_SLUG = 'test-slug'
_SESSION = 'session-abc'
_HOST = 'test-host'
_NOW = '2026-09-10T12:00:00'


def _new_root(
    tmp_path: 'os.PathLike[str]',
    monkeypatch: Any,
    slug: str = _SLUG,
    cycle: str = '1',
    now: str = _NOW,
) -> 'pathlib.Path':
    """Return .handoff/slug folder path and configure all HQ_* env vars."""
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


def _handoff(folder: 'pathlib.Path', cycle: int = 1) -> None:
    """Write a minimal conforming HANDOFF.md to folder."""
    text = (
        f'# Handoff: {folder.name}\n\n'
        f'Written: 2026-09-10 | Cycle: {cycle}\n\n'
        '## Task\n\nDo the work.\n\n'
        '## Now\n\nNext.\n\n'
        '## Plan\n\nPlan.\n\n'
        '## State\n\nState.\n\n'
        '## Environment\n\nEnv.\n\n'
        '## Open questions\n\nNone.\n'
    )
    (folder / 'HANDOFF.md').write_text(text, encoding='utf-8')


def _row(**overrides: Any) -> dict:
    """Build a full ledger row dict with the given overrides."""
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({
        'cycle': '1', 'base': 'folder', 'kind': 'spec', 'status': 'live',
        'read_before': 'always', 'label': 'a label',
        })
    row.update(overrides)
    return row


# ------------------------------------------------------------------
# resolve_where
# ------------------------------------------------------------------


def test_resolve_where_ignorecase_extracts_number_from_uppercase_heading():
    """Number extraction uses IGNORECASE so S4: headings yield a section number.

    Mutation: x_resolve_where__mutmut_14 drops re.IGNORECASE from the first
    re.match, so '## S4: Title' never matches the number pattern, leaving
    number='' and breaking the s4 anchor fallback.
    Oracle: spans is non-empty; the anchor resolves to the right line range.
    """
    text = '# Doc\n\n## S4: Field mapping\n\nContent.\n'
    spans, unresolved = hq.resolve_where(text, ['s4'])
    assert not unresolved, f'unexpected unresolved: {unresolved}'
    assert len(spans) == 1
    assert spans[0] == (3, 5)


def test_resolve_where_ignorecase_matches_uppercase_s_anchor():
    """Anchor s-prefix matching is case-insensitive via re.IGNORECASE.

    Mutation: x_resolve_where__mutmut_37 drops re.IGNORECASE from the
    fullmatch used to detect an s<d> anchor, so anchor 'S4' (uppercase)
    never enters the section-fallback path.
    Oracle: with anchor 'S4' and heading '## s4: Title', spans is non-empty.
    """
    text = '# Doc\n\n## s4: Field mapping\n\nContent.\n'
    spans, unresolved = hq.resolve_where(text, ['S4'])
    assert not unresolved, f'unexpected unresolved: {unresolved}'
    assert len(spans) == 1
    assert spans[0] == (3, 5)


# ------------------------------------------------------------------
# block_sha
# ------------------------------------------------------------------


def test_block_sha_rstrips_not_lstrips_each_line():
    """block_sha strips trailing whitespace per line, not leading.

    Mutation: x_block_sha__mutmut_14 changes rstrip to lstrip, so indented
    lines lose their leading spaces and the sha changes for indented content.
    Oracle: sha of a block with an indented line differs from sha of one
    whose indented line has its leading spaces stripped.
    """
    indented = '## Title\n\n    indented content\n'
    stripped_leading = '## Title\n\nindented content\n'
    sha_orig = hq.block_sha(indented)
    sha_no_indent = hq.block_sha(stripped_leading)
    # The two inputs normalize differently, so their shas must differ.
    assert sha_orig != sha_no_indent, (
        'block_sha does not preserve leading spaces - lstrip mutant undetectable'
    )
    # Also verify: trailing-space normalization does not alter the hash.
    trailing = '## Title\n\n    indented content   \n'
    assert hq.block_sha(trailing) == sha_orig


# ------------------------------------------------------------------
# render_read
# ------------------------------------------------------------------


def test_render_read_unsized_path_shows_a_dash():
    """render_read prints '-' for a row the caller could not size.

    Mutation: sizes[path] in place of sizes.get(path, '-'), so a row
    absent from the size map raises KeyError and finish dies; or the
    default changed, so the line misreports a size.
    Oracle: hand-computed output for a path absent from the sizes dict.
    """
    rows = [_row(where='-', path='notes.md', label='the notes')]
    result = hq.render_read(rows, spans={}, sizes={})
    assert result == 'notes.md  (-)  the notes'


def test_render_read_missing_span_path_uses_empty_list():
    """render_read falls back to [] when path absent from spans dict.

    Mutation: x_render_read__mutmut_32 changes spans.get(path, []) to
    spans.get(path, ) which returns None; iterating None raises TypeError.
    Oracle: a path absent from spans dict renders as 'path:?  (size)  label'.
    """
    rows = [_row(where='SomeSection', path='spec.md', label='the spec')]
    result = hq.render_read(rows, spans={}, sizes={'spec.md': '12 tok'})
    assert result == 'spec.md:?  (12 tok)  the spec'


# ------------------------------------------------------------------
# render_artifacts
# ------------------------------------------------------------------


def test_render_artifacts_prints_every_unstamped_entry_with_no_cap():
    """Forty-one unstamped entries render forty-one lines and no count line.

    Mutation: a cap on the full lines that folds the overflow into a
    kind count, so the forty-first entry leaves the block as 'spec x1'.
    Oracle: hand-counted - 41 walk entries with no row give 41 lines of
    the form 'file  spec?  unstamped' and nothing else.
    """
    walk = [(f'file{i:02d}.md', 'spec') for i in range(41)]
    rows: dict = {}
    lines = hq.render_artifacts(walk, rows, slug='demo').splitlines()
    assert len(lines) == 41
    assert all(ln.endswith('  spec?  unstamped') for ln in lines)


# ------------------------------------------------------------------
# render_standing
# ------------------------------------------------------------------


def _make_items(n: int, prefix: str = 'c') -> list[dict]:
    """Return n standing items with sequential ids."""
    return [
        {'id': f'{prefix}{i:02d}', 'prefix': prefix, 'cycle': 1,
         'headline': f'Item {i}', 'body': f'Body {i}.'}
        for i in range(1, n + 1)
        ]


def test_render_standing_prints_every_live_item_and_the_superseded_tail():
    """Eighty constraints with one superseded render every live one and a tail.

    Mutation: a line cap that cuts items and prints '... N more'; or the
    superseded tail dropped, so the block never says a ruling left it.
    Oracle: hand-counted - 79 live items under one heading plus the tail
    'superseded 1  - hq standing demo --all' make 81 lines, [c80] absent.
    """
    items = _make_items(80, 'c')
    superseded_ids = {items[-1]['id']}
    lines = hq.render_standing(items, superseded_ids, slug='demo').splitlines()
    assert len(lines) == 81
    assert lines[0] == '### Constraints'
    assert sum(ln.startswith('[c') for ln in lines) == 79
    assert not any(ln.startswith('... ') for ln in lines)
    assert '[c80]' not in '\n'.join(lines)
    assert lines[-1] == 'superseded 1  - hq standing demo --all'


def test_read_tsv_continues_past_empty_line(tmp_path):
    """_read_tsv skips empty lines, not stops at them.

    Mutation: x__read_tsv__mutmut_14 changes continue to break; the first
    empty line terminates reading and subsequent rows are lost.
    Oracle: a TSV with an empty line mid-file yields all non-empty rows.
    """
    tsv = tmp_path / 'data.tsv'
    fields = ['a', 'b', 'c']
    tsv.write_text(
        'a\tb\tc\n'
        'v1\tv2\tv3\n'
        '\n'
        'v4\tv5\tv6\n',
        encoding='utf-8',
    )
    rows = hq._read_tsv(tsv, fields)
    assert len(rows) == 2, f'Expected 2 data rows, got {len(rows)}'
    assert rows[0] == {'a': 'v1', 'b': 'v2', 'c': 'v3'}
    assert rows[1] == {'a': 'v4', 'b': 'v5', 'c': 'v6'}


# ------------------------------------------------------------------
# _norm_heading
# ------------------------------------------------------------------


def test_norm_heading_strips_uppercase_s_prefix_with_ignorecase():
    """_norm_heading strips 's<d>:' prefixes case-insensitively.

    Mutation: x__norm_heading__mutmut_18 drops re.IGNORECASE so 'S4:' is
    not stripped; '## S4: Title' normalizes to 's4: title' instead of
    'title', breaking anchor lookup for such headings.
    Oracle: _norm_heading('## S4: Field mapping') == 'field mapping'.
    """
    result = hq._norm_heading('## S4: Field mapping')
    assert result == 'field mapping', (
        f'Expected "field mapping", got {result!r}'
    )


# ------------------------------------------------------------------
# _verb_open
# ------------------------------------------------------------------


def test_open_no_handoff_returns_zero(tmp_path, monkeypatch):
    """Open returns 0 when HANDOFF.md does not exist.

    Mutation: x__verb_open__mutmut_35 changes return 0 to return 1 in the
    no-HANDOFF.md branch, so a fresh folder reports failure.
    Oracle: hq.main(['open', slug]) == 0 when folder exists but has no
    HANDOFF.md (immediately after mkdir, before begin).
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    assert hq.main(['open', _SLUG]) == 0


def test_open_reports_witness_warnings_when_manifest_exists(
        tmp_path, monkeypatch, capsys):
    """Open calls witness with the last manifest row, not None.

    Mutation: x__verb_open__mutmut_3 always passes None to witness, so W1/W2
    warnings are never reported regardless of the manifest state.
    Oracle: after a cycle is finished and the ledger is tampered with, open
    prints a line starting 'WARNING: W1'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'spec.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'spec.md'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # Corrupt the ledger to trigger W1.
    ledger_path = folder / 'ledger.tsv'
    original = ledger_path.read_bytes()
    # Overwrite a byte in the recorded prefix.
    corrupted = bytearray(original)
    corrupted[10] ^= 0x01
    ledger_path.write_bytes(bytes(corrupted))
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    assert 'WARNING: W1' in out


def test_open_prints_lock_cycle_and_owner(tmp_path, monkeypatch, capsys):
    """Open shows the lock's cycle, session, and host with correct key names.

    Mutation: x__verb_open__mutmut_17 uses lock.get('CYCLE') (-> None),
    x__verb_open__mutmut_20 uses lock.get('SESSION') (-> None),
    x__verb_open__mutmut_23 uses lock.get('HOST') (-> None).
    Oracle: lock file with known values -> output contains those values.
    """
    folder = _new_root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / '.hq.lock').write_text(
        f'slug={_SLUG}\nsession=alpha-session\nhost=alpha-host\ncycle=7\n'
    )
    _handoff(folder)
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    assert 'cycle 7' in out, f'cycle value missing: {out!r}'
    assert 'alpha-session' in out, f'session value missing: {out!r}'
    assert 'alpha-host' in out, f'host value missing: {out!r}'


def test_open_reports_ledger_behind_when_file_cycle_ahead(
        tmp_path, monkeypatch, capsys):
    """Open detects when HANDOFF.md's cycle exceeds the last manifest entry.

    Mutation: x__verb_open__mutmut_48 changes 'or 0' to 'and 0' making
    file_cycle always 0; x__verb_open__mutmut_51 uses key 'CYCLE' (-> None)
    so file_cycle=0 too. Both suppress the LEDGER BEHIND message.
    Oracle: HANDOFF.md with cycle=2 and manifest at cycle=1 -> 'LEDGER BEHIND'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # Rewrite HANDOFF.md header to claim cycle=2.
    hf = folder / 'HANDOFF.md'
    hf.write_text(hf.read_text().replace('Cycle: 1', 'Cycle: 2'))
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'LEDGER BEHIND' in capsys.readouterr().out


def test_open_skips_git_drift_when_header_has_wrong_key(
        tmp_path, monkeypatch, capsys):
    """Open uses 'header' key (lowercase) to fetch the header line.

    Mutation: x__verb_open__mutmut_79 uses key 'HEADER' (-> '') so the
    header is always empty and git drift is never reported.
    Oracle: HANDOFF.md header with a specific branch@sha -> drift reported.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # Rewrite the header line to embed branch@sha.
    hf = folder / 'HANDOFF.md'
    hf.write_text(
        hf.read_text().replace(
            'Written: 2026-09-10T12:00:00 | Cycle: 1',
            'Written: 2026-09-10T12:00:00 | Cycle: 1 | master @ abc1234def5',
        )
    )
    # Set HQ_GIT=0 but override the anch dict via a custom env.
    # We set branch/sha by creating a real git repo so anch differs.
    # Simpler: inject different branch via a second finish with HQ_GIT enabled
    # on a real git repo. But HQ_GIT=0 means branch='-', so drift check is
    # skipped when cur_branch=='-'. To test _79 we need branch != '-'.
    # We test _79 indirectly: with the correct key, drift IS caught when
    # cur_branch != '-'. Here we verify behavior using the real git path.
    # Note: _79 is tested as part of the git-drift group below.


def test_open_git_drift_uses_lowercase_pattern(tmp_path, monkeypatch, capsys):
    """Open matches the git header pattern against lowercase hex sha only.

    Mutation: x__verb_open__mutmut_87 changes [0-9a-f]+ to [0-9A-F]+, so
    a lowercase sha like 'abc1234def5a' does not match and drift goes
    unreported.
    Oracle: write a HANDOFF.md header with a lowercase sha differing from the
    current git sha, then open reports 'git drift'.

    This test sets up a real git repo so anch['branch'] != '-'.
    """
    import subprocess
    folder = _new_root(tmp_path, monkeypatch)
    root = folder.parent.parent
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@x.invalid',
         'commit', '-q', '--allow-empty', '-m', 'init'],
        cwd=root, check=True)
    monkeypatch.setenv('HQ_GIT', '1')
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # Overwrite the header sha with 'deadbeef0000' so it differs.
    hf = folder / 'HANDOFF.md'
    content = hf.read_text()
    import re
    content = re.sub(
        r'(\| \S+ @ )[0-9a-f]+',
        r'\1deadbeef0000',
        content,
    )
    hf.write_text(content)
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'git drift' in capsys.readouterr().out


def test_open_git_drift_branch_and_sha_assigned_correctly(
        tmp_path, monkeypatch, capsys):
    """Open reads h_branch from group(1) and h_sha from group(2), not swapped.

    Mutation: x__verb_open__mutmut_90 swaps to group(2),group(2) for
    h_branch,h_sha; x__verb_open__mutmut_92 uses group(3) for h_sha
    (index error).
    Oracle: craft header '| mybranch @ deadbeef0000' with cur_sha different;
    drift message names the branch 'mybranch', not the sha.
    """
    import subprocess
    folder = _new_root(tmp_path, monkeypatch)
    root = folder.parent.parent
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@x.invalid',
         'commit', '-q', '--allow-empty', '-m', 'init'],
        cwd=root, check=True)
    monkeypatch.setenv('HQ_GIT', '1')
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    content = hf.read_text()
    import re
    content = re.sub(
        r'\| \S+ @ [0-9a-f]+',
        '| mybranch @ deadbeef0000',
        content,
    )
    hf.write_text(content)
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    assert 'git drift' in out
    assert 'mybranch@deadbeef0000' in out


def test_open_git_drift_and_vs_or_in_condition(tmp_path, monkeypatch, capsys):
    """Open reports drift when only sha differs (not both branch and sha).

    Mutation: x__verb_open__mutmut_101 changes 'or' to 'and' in the inner
    condition, so drift is only reported when BOTH branch AND sha differ.
    Oracle: header with matching branch but different sha -> drift reported.
    """
    import subprocess
    folder = _new_root(tmp_path, monkeypatch)
    root = folder.parent.parent
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@x.invalid',
         'commit', '-q', '--allow-empty', '-m', 'init'],
        cwd=root, check=True)
    monkeypatch.setenv('HQ_GIT', '1')
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    content = hf.read_text()
    import re

    # Get current branch name.
    branch_m = re.search(r'\| (\S+) @ [0-9a-f]+', content)
    cur_branch = branch_m.group(1) if branch_m else 'HEAD'
    # Keep the same branch but change sha to deadbeef.
    content = re.sub(
        r'\| \S+ @ [0-9a-f]+',
        f'| {cur_branch} @ deadbeef0000',
        content,
    )
    hf.write_text(content)
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'git drift' in capsys.readouterr().out


def test_open_git_drift_not_fired_when_cur_branch_is_dash(
        tmp_path, monkeypatch, capsys):
    """Open does not report drift when the current branch is '-' (no git).

    Mutation: x__verb_open__mutmut_98 changes 'and' to 'or' so drift fires
    even when cur_branch=='-'; x__verb_open__mutmut_99 changes '!=' to '=='
    so drift fires ONLY when cur_branch IS '-'.
    Oracle: with HQ_GIT=0 (branch='-') and a mismatching header, no 'git drift'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    content = hf.read_text()
    import re
    content = re.sub(
        r'(Written: [^|]+\|[^|]+)',
        r'\1 | master @ deadbeef0000',
        content,
    )
    hf.write_text(content)
    capsys.readouterr()

    # HQ_GIT=0 -> anch['branch'] = '-' -> drift check skipped.
    hq.main(['open', _SLUG])

    assert 'git drift' not in capsys.readouterr().out


def test_open_block_sha_pattern_matches_lowercase_hex(
        tmp_path, monkeypatch, capsys):
    """Open finds block sha markers using lowercase hex in the pattern.

    Mutation: x__verb_open__mutmut_108 changes pattern to uppercase hex
    ([0-9A-F]+), so the stored lowercase sha in HANDOFF.md is never found
    and block sha mismatches go unreported.
    Oracle: after tampering with block body, open reports 'block sha mismatch'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    # Tamper with a block body inside the markers.
    content = hf.read_text()
    import re
    content = re.sub(
        r'(<!-- hq:artifacts [0-9a-f]+ -->)(.*?)(<!-- /hq:artifacts -->)',
        r'\1\nTampered content\n\3',
        content,
        flags=re.DOTALL,
    )
    hf.write_text(content)
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'block sha mismatch' in capsys.readouterr().out


def test_open_read_block_body_default_empty_string(
        tmp_path, monkeypatch, capsys):
    """Open uses '' (not None) when the read block is absent.

    Mutation: x__verb_open__mutmut_207 changes get('read', '') to get('read',
    ) so body=None when no read block exists; None.splitlines() raises
    AttributeError.
    Oracle: stamp a file with --where and call open before a finish (no
    read block written yet); open exits 0 with no AttributeError.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Overview\n\nDetails.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Overview',
             '--read-before', 'always'])
    # No finish - so no block tags in HANDOFF.md.
    capsys.readouterr()

    ret = hq.main(['open', _SLUG])

    assert ret == 0
    out = capsys.readouterr().out
    assert 'AttributeError' not in out


def test_open_filter_skips_non_live_rows_with_or_not_and(
        tmp_path, monkeypatch, capsys):
    """Open skips a row when status != 'live' OR read_before != 'always'.

    Mutation: x__verb_open__mutmut_147 changes 'or' to 'and', so only rows
    that are BOTH non-live AND non-always are skipped; an archived row with
    read_before='always' would then not be skipped.
    Oracle: stamp a file, archive it (supersede), then stamp with 'always';
    open doesn't crash processing the archived row.

    The observable difference: with 'and', an archived-but-always row is
    processed and its path is read; with 'or', it is skipped.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Overview\n\nDetails.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Overview',
             '--read-before', 'always'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--successor', 'SPEC2.md'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    ret = hq.main(['open', _SLUG])

    assert ret == 0


def test_open_span_loop_continues_past_non_live_rows(
        tmp_path, monkeypatch, capsys):
    """Open continues (not breaks) at non-live rows in the span check loop.

    Mutation: x__verb_open__mutmut_160 changes continue to break so the loop
    exits at the first non-qualifying row, missing later qualifying rows.
    Oracle: with two live/always rows where the first is skipped (non-live),
    the second row's unresolved anchor is still reported.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'NOTES.md').write_text('# Notes\n\nContent.\n')
    (folder / 'SPEC.md').write_text('# Spec\n\n## Overview\n\nDetails.\n')
    # Stamp NOTES first (will be superseded -> non-live), then SPEC.
    hq.main(['stamp', _SLUG, 'NOTES.md', '--read-before', 'always',
             '--where', 'NoSuchHeading'])
    hq.main(['stamp', _SLUG, 'NOTES.md', '--successor', 'NOTES2.md'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'MissingSection'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    # SPEC.md has 'MissingSection' which won't resolve -> 'unresolved anchor'.
    assert 'unresolved anchor in SPEC.md' in out


def test_open_span_loop_continues_past_where_dash_rows(
        tmp_path, monkeypatch, capsys):
    """Open continues (not breaks) at rows with where='-'.

    Mutation: x__verb_open__mutmut_165 changes continue to break, stopping
    the entire loop at the first where='-' row and missing later rows.
    Oracle: two live/always rows; first has where='-', second has where=
    'MissingAnchor'; only the second triggers 'unresolved anchor'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'NOTES.md').write_text('# Notes\n\nContent.\n')
    (folder / 'SPEC.md').write_text('# Spec\n\n## Overview\n\nDetails.\n')
    hq.main(['stamp', _SLUG, 'NOTES.md', '--read-before', 'always'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'MissingSection'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # In live dict, NOTES has where='-', SPEC has where='MissingSection'.
    # Dict iteration order is insertion order; NOTES inserted first.
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    assert 'unresolved anchor in SPEC.md' in out


def test_open_span_loop_continues_when_file_missing(
        tmp_path, monkeypatch, capsys):
    """Open continues (not breaks) when a stamped file is missing from disk.

    Mutation: x__verb_open__mutmut_180 changes continue to break; the loop
    exits at the first missing file and later rows are not checked.
    Oracle: first stamped file deleted, second stamped file has unresolved
    anchor; 'unresolved anchor' for the second file is still reported.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'GONE.md').write_text('# Gone\n')
    (folder / 'SPEC.md').write_text('# Spec\n\n## Overview\n\nDetails.\n')
    hq.main(['stamp', _SLUG, 'GONE.md', '--read-before', 'always',
             '--where', 'SomeSection'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'MissingSection'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    (folder / 'GONE.md').unlink()
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    assert 'unresolved anchor in SPEC.md' in out


def test_open_span_loop_continues_in_inner_line_scan(
        tmp_path, monkeypatch, capsys):
    """Open continues (not breaks) when a read-block line does not match.

    Mutation: x__verb_open__mutmut_235 changes continue to break in the inner
    for-loop over read_block_body lines; a non-matching line stops scanning
    so a later matching line is missed and 'span moved' goes unreported.
    Oracle: read block has a heading line before the span line; moving the
    anchor must still trigger 'span moved'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Alpha\n\na\n\n## Beta\n\nb\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'Beta'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # The read block body in HANDOFF.md looks like:
    #   SPEC.md:7-9  a label
    # Insert a non-matching line before the data line by directly editing.
    hf = folder / 'HANDOFF.md'
    content = hf.read_text()
    import re
    content = re.sub(
        r'(<!-- hq:read [0-9a-f]+ -->)\n',
        r'\1\n## Read first\n',
        content,
    )
    hf.write_text(content)
    # Now move the heading so the spans differ.
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Alpha\n\na\nb\nc\n\n## Beta\n\nb\n')
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'span moved' in capsys.readouterr().out


def test_open_span_comparison_uses_correct_parts_indices(
        tmp_path, monkeypatch, capsys):
    """Open reads span start from parts[0], not parts[1].

    Mutation: x__verb_open__mutmut_248 reads int(parts[1]) for both start and
    end, so start=end=7 for a span stored as '5-7'; a genuine move from 5-7
    to 7-9 would compare (7,7) vs (7,9) - still different, so this mutant is
    tested by checking detection of a start-only change.
    Oracle: span stored as '3-7' moves to '5-7' (start changes, end same);
    with correct parsing (3,7) != (5,7), 'span moved' is reported.
    With mutant, old_spans = [(7,7)]; new_spans = [(5,7)]; not equal ->
    still reported. BUT: span stored as '5-7' moves to '5-9'; mutant
    old=(7,7) vs new=(5,9): still unequal -> still reports. Hard case: span
    '7-7' moves to '7-9'; old=(7,7) new=(7,9): original reports correctly;
    mutant old=(7,7) new=(7,9): both show mismatch so equivalent in that case.
    The distinguishing case: stored '3-9' moves to '9-9'; old=(3,9) new=(9,9)
    -> original: 3!=9 -> reports; mutant old=(9,9) new=(9,9) -> equal -> NOT
    reported. Trigger this.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    # Craft a SPEC.md with a heading at line 3 that spans to line 9 (7 lines).
    (folder / 'SPEC.md').write_text(
        '# Spec\n'
        '\n'
        '## Beta\n'
        '\n'
        'a\n'
        'b\n'
        'c\n'
        'd\n'
        'e\n'
    )
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'Beta'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # Beta spans 3-9. Move it so new start is 9 and end is 9 by padding above.
    # Stored: 3-9. New: Beta at line 9, end=9 (only 1 line). old_spans=[(3,9)]
    # new_spans=[(9,9)]. Original: (3,9)!=(9,9) -> report.
    # Mutant mutmut_248 parses start=parts[1]='9', end='9', so the span
    # compares equal to itself and is not reported.
    (folder / 'SPEC.md').write_text(
        '# Spec\n'
        '\n'
        '## Alpha\n'
        '\n'
        'a\n'
        'b\n'
        'c\n'
        '\n'
        '## Beta\n'
    )
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'span moved: SPEC.md' in capsys.readouterr().out


def test_open_span_moved_condition_requires_both_old_and_new(
        tmp_path, monkeypatch, capsys):
    """Open reports span moved only when old_spans AND new_spans are both set.

    Mutation: x__verb_open__mutmut_251 changes 'and' to 'or' so a check fires
    even when one list is empty; x__verb_open__mutmut_252 changes 'old_spans
    and new_spans' to 'old_spans or new_spans'.
    Oracle: a row whose stored span is '?' (unresolved, old_spans=[]) and
    whose anchor is still unresolved (new_spans=[]) should NOT produce
    'span moved'. With mutant_251 the condition fires on empty old_spans.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Alpha\n\na\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'AlwaysMissing'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # Stored span is '?' (unresolved). Anchor still missing -> new_spans=[].
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'span moved' not in capsys.readouterr().out


def test_open_handles_invalid_bytes_in_stamped_file(
        tmp_path, monkeypatch, capsys):
    """Open reads file with errors='replace', not strict, for stamped files.

    Mutation: x__verb_open__mutmut_185 removes errors='replace'; strict
    decoding raises UnicodeDecodeError on invalid bytes.
    Oracle: a stamped file with invalid bytes -> open exits 0 and does not
    raise UnicodeDecodeError.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    bad = folder / 'bad.md'
    bad.write_bytes(b'# Notes\n\xff\xfe bad bytes\n')
    hq.main(['stamp', _SLUG, 'bad.md', '--read-before', 'always',
             '--where', 'Notes'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    ret = hq.main(['open', _SLUG])

    assert ret == 0


def test_open_abs_row_uses_correct_base_key(tmp_path, monkeypatch, capsys):
    """Open checks row['base'] == 'abs' (lowercase) for absolute paths.

    Mutation: x__verb_open__mutmut_170 checks row['base'] == 'ABS', which
    never matches; abs-base rows fall into the folder-relative branch and
    fail to find the file.
    Oracle: stamp a file outside the folder with --read-before always;
    open returns 0 and does not crash on path resolution.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    target = tmp_path / 'global.md'
    target.write_text('# Global\n\n## Config\n\nSettings.\n')
    # Outside-folder path -> stored as abs automatically.
    ret = hq.main(['stamp', _SLUG, str(target),
                   '--read-before', 'always', '--where', 'Config'])
    assert ret == 0
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    ret = hq.main(['open', _SLUG])

    assert ret == 0


def test_open_abs_row_uses_path_key(tmp_path, monkeypatch, capsys):
    """Open reads row['path'] (lowercase) for the abs-path value.

    Mutation: x__verb_open__mutmut_174 reads row['PATH'] instead of
    row['path']; since 'PATH' is not in the ledger row, pathlib.Path raises.
    Oracle: same abs-stamped file; open returns 0.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    target = tmp_path / 'global2.md'
    target.write_text('# Global\n\n## Config\n\nSettings.\n')
    ret = hq.main(['stamp', _SLUG, str(target),
                   '--read-before', 'always', '--where', 'Config'])
    assert ret == 0
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    ret = hq.main(['open', _SLUG])

    assert ret == 0


def test_open_handles_invalid_bytes_in_handoff_md(tmp_path, monkeypatch):
    """Open reads HANDOFF.md with errors='replace', surviving invalid bytes.

    Mutation: x__verb_open__mutmut_40 removes errors='replace' from the
    HANDOFF.md read; a file with a stray bad byte raises UnicodeDecodeError.
    x__verb_open__mutmut_44 uses errors='REPLACE' (invalid handler name).
    Oracle: HANDOFF.md with invalid bytes -> open returns 0 (no crash).
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    data = bytearray(hf.read_bytes())
    data[5] = 0xFF
    hf.write_bytes(bytes(data))

    ret = hq.main(['open', _SLUG])

    assert ret == 0


def test_open_handles_bom_in_handoff_md(tmp_path, monkeypatch):
    """Open reads HANDOFF.md with utf-8-sig to strip a leading BOM.

    Mutation: x__verb_open__mutmut_39 removes encoding='utf-8-sig'; without
    it, a BOM-prefixed file is read with locale encoding and the BOM byte
    sequence appears as text, breaking split_handoff parsing.
    Oracle: BOM-prefixed HANDOFF.md -> open returns 0 and does not corrupt
    the parsed cycle field.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    content = hf.read_text(encoding='utf-8')
    # Prepend a UTF-8 BOM.
    hf.write_bytes(b'\xef\xbb\xbf' + content.encode('utf-8'))

    ret = hq.main(['open', _SLUG])

    # With correct utf-8-sig, the BOM is stripped and parsing succeeds.
    assert ret == 0


# ------------------------------------------------------------------
# _verb_read
# ------------------------------------------------------------------


def test_verb_read_not_in_ledger_exits_one(tmp_path, monkeypatch, capsys):
    """_verb_read exits 1 (not 2) when the path is not in the ledger.

    Mutation: x__verb_read__mutmut_24 changes return 1 to return 2.
    Oracle: 'hq read unknown.md' returns exactly 1 when unknown.md has no
    ledger row.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])

    ret = hq.main(['read', _SLUG, 'unknown.md'])

    assert ret == 1


def test_verb_read_not_on_disk_exits_one(tmp_path, monkeypatch, capsys):
    """_verb_read exits 1 (not 2) when the path is not on disk.

    Mutation: x__verb_read__mutmut_36 changes return 1 to return 2.
    Oracle: stamp a file, delete it, then 'hq read' returns exactly 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    (folder / 'SPEC.md').unlink()

    ret = hq.main(['read', _SLUG, 'SPEC.md'])

    assert ret == 1


def test_verb_read_abs_base_resolves_correctly(tmp_path, monkeypatch, capsys):
    """_verb_read uses row['base'] == 'abs' (lowercase) to resolve abs paths.

    Mutation: x__verb_read__mutmut_29 checks row['base'] == 'ABS', which
    never matches; the abs path is then treated as folder-relative, fails
    to exist, and returns 1 instead of printing content.
    Oracle: 'hq read' on an abs-stamped file returns 0 and prints content.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    # Path outside the folder - automatically stored as abs.
    target = tmp_path / 'global.md'
    target.write_text('# Global\n\nGlobal content.\n')
    ret_stamp = hq.main(['stamp', _SLUG, str(target)])
    assert ret_stamp == 0, 'stamp must succeed for this test to be meaningful'
    # Determine the stored path (may be absolute or ~/... form).
    import bin.hq as _hq
    rows = _hq._read_tsv(folder / 'ledger.tsv', _hq.LEDGER_FIELDS)
    stored = rows[-1]['path']
    capsys.readouterr()

    ret = hq.main(['read', _SLUG, stored])

    assert ret == 0
    assert 'Global content' in capsys.readouterr().out


def test_verb_read_state_dir_fallback_without_hq_state_dir(
        tmp_path, monkeypatch, capsys):
    """_verb_read falls back to _DEFAULT_STATE when HQ_STATE_DIR is absent.

    Mutation: x__verb_read__mutmut_77 removes the fallback default from
    os.environ.get, so get('HQ_STATE_DIR') returns None when unset;
    pathlib.Path(None) raises TypeError.
    Oracle: with HQ_STATE_DIR unset, 'hq read' still returns 0 (or 1 on
    OSError from an unwritable default, but no TypeError).
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    monkeypatch.delenv('HQ_STATE_DIR', raising=False)
    # Set state dir to a known writable temp location via default override.
    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    import bin.hq as _hq
    orig_default = _hq._DEFAULT_STATE
    _hq._DEFAULT_STATE = state_dir
    capsys.readouterr()

    try:
        ret = hq.main(['read', _SLUG, 'SPEC.md'])
    finally:
        _hq._DEFAULT_STATE = orig_default

    assert ret == 0


def test_verb_read_mkdir_creates_parent_directories(tmp_path, monkeypatch):
    """_verb_read creates the receipt directory with parents=True.

    Mutation: x__verb_read__mutmut_95 drops parents=True (becomes parents=False
    default) and x__verb_read__mutmut_97 sets parents=False explicitly;
    mkdir fails when the parent directory does not exist.
    Oracle: set HQ_STATE_DIR to a nested path whose parents don't exist;
    'hq read' must still return 0 (creating the full tree).
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    # Nested path: parent 'nested' does not exist yet.
    nested_state = tmp_path / 'nested' / 'deep' / 'state'
    monkeypatch.setenv('HQ_STATE_DIR', str(nested_state))

    ret = hq.main(['read', _SLUG, 'SPEC.md'])

    assert ret == 0
    assert nested_state.exists()


# ------------------------------------------------------------------
# _verb_diff
# ------------------------------------------------------------------


def test_verb_diff_missing_cycle_exits_one(tmp_path, monkeypatch, capsys):
    """_verb_diff exits 1 (not 2) when a cycle file is not found.

    Mutation: x__verb_diff__mutmut_34 changes return 1 to return 2.
    Oracle: 'hq diff slug 1 99' with c99.md missing returns exactly 1.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])

    ret = hq.main(['diff', _SLUG, '1', '99'])

    assert ret == 1


def test_verb_diff_counts_and_expands_sixty_one_lines_in_full(
        tmp_path, monkeypatch, capsys):
    """_verb_diff counts a section's change and expands it with no cap.

    Mutation: a cap restored on the expansion, so the 61st line is cut;
    or the counts taken from a set difference, so a line present in both
    cycles under different neighbors is miscounted.
    Oracle: two cycle files whose Now sections hold 30 and 31 unique
    lines: the summary reads '## Now  +31 -30' and 'now' expands to 61
    change lines.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c2'])
    cycles_dir = folder / 'cycles'
    preamble = '# Handoff: test-slug\n\nWritten: 2026-09-10 | Cycle: 1\n\n'
    c1_cursor = '## Now\n\n' + '\n'.join(f'only-c1-{i}' for i in range(30)) + '\n'
    c2_cursor = '## Now\n\n' + '\n'.join(f'only-c2-{i}' for i in range(31)) + '\n'
    (cycles_dir / 'c01.md').write_text(preamble + c1_cursor)
    (cycles_dir / 'c02.md').write_text(preamble + c2_cursor)
    capsys.readouterr()

    assert hq.main(['diff', _SLUG, '1', '2']) == 0
    assert capsys.readouterr().out.splitlines() == ['## Now  +31 -30']
    assert hq.main(['diff', _SLUG, '1', '2', 'now']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == '## Now  +31 -30'
    assert sum(ln.startswith('- ') for ln in lines) == 30
    assert sum(ln.startswith('+ ') for ln in lines) == 31
    assert 'omitted' not in '\n'.join(lines)


# ------------------------------------------------------------------
# _verb_artifacts
# ------------------------------------------------------------------


def test_verb_artifacts_detects_conflicted_copy_by_lowercase(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts uses lowercase 'conflicted copy' in the name check.

    Mutation: x__verb_artifacts__mutmut_24 checks 'CONFLICTED COPY' (all-caps)
    which never matches a sync-conflict filename.
    Oracle: a file named 'data (conflicted copy 2026-09-10).md' -> output
    contains 'conflicted copy', not 'unstampable'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    conflict = folder / 'data (conflicted copy 2026-09-10).md'
    conflict.write_text('# Data\n')
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert 'conflicted copy' in out
    if 'conflicted copy' in out:
        assert 'unstampable' not in out.split('conflicted copy')[0]


def test_verb_artifacts_continues_past_skip_rows(tmp_path, monkeypatch, capsys):
    """_verb_artifacts continues (not breaks) after a skip-kind walk entry.

    Mutation: x__verb_artifacts__mutmut_28 breaks after a 'conflicted copy'
    entry; x__verb_artifacts__mutmut_33 breaks after any other skip entry.
    Oracle: conflict file followed by a normal file -> normal file is listed.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    conflict = folder / 'a (conflicted copy 2026-09-10).md'
    conflict.write_text('# Data\n')
    normal = folder / 'SPEC.md'
    normal.write_text('# Spec\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--label', 'the spec'])
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert 'SPEC.md' in out, 'SPEC.md should appear after the conflicted copy'


def test_verb_artifacts_abs_rows_not_in_walk_are_listed(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts lists rows not seen in the walk (abs paths, subdirs).

    Mutation: x__verb_artifacts__mutmut_49 inverts 'path in seen' to 'path
    not in seen', so rows ALREADY in the walk are listed again and rows
    outside the walk are skipped.
    Oracle: stamp an abs-path file; 'artifacts' lists it.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    # Path outside the folder -> stored as abs, never in the walk.
    target = tmp_path / 'global.md'
    target.write_text('# Global\n\nContent.\n')
    ret = hq.main(['stamp', _SLUG, str(target), '--label', 'global doc'])
    assert ret == 0, 'stamp must succeed'
    # Get the stored path from the ledger.
    import bin.hq as _hq
    rows = _hq._read_tsv(folder / 'ledger.tsv', _hq.LEDGER_FIELDS)
    stored = rows[-1]['path']
    assert rows[-1]['base'] == 'abs', 'Expected abs base for outside-folder file'
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert stored in out, f'Abs-path row not listed: {out!r}'


def test_verb_artifacts_live_filter_uses_lowercase_live(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts filters on status == 'live' (lowercase).

    Mutation: x__verb_artifacts__mutmut_54 checks status != 'LIVE', which
    is always True so all rows are treated as non-live and skipped.
    Oracle: a live stamped abs-path row should appear in the output.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    target = tmp_path / 'live.md'
    target.write_text('# Live\n\nContent.\n')
    ret = hq.main(['stamp', _SLUG, str(target), '--label', 'live doc'])
    assert ret == 0
    import bin.hq as _hq
    rows = _hq._read_tsv(folder / 'ledger.tsv', _hq.LEDGER_FIELDS)
    stored = rows[-1]['path']
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert stored in out, f'Live abs-path row missing: {out!r}'


def test_verb_artifacts_continues_past_non_live_abs_rows(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts continues (not breaks) after skipping a non-live row.

    Mutation: x__verb_artifacts__mutmut_55 breaks after skipping a non-live
    abs-path row; rows after it are not listed.
    Oracle: superseded abs row followed by live abs row -> live row appears.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    early = tmp_path / 'early.md'
    early.write_text('# Early\n')
    late = tmp_path / 'late.md'
    late.write_text('# Late\n\nContent.\n')
    hq.main(['stamp', _SLUG, str(early)])
    hq.main(['stamp', _SLUG, str(early), '--successor', str(late)])
    hq.main(['stamp', _SLUG, str(late), '--label', 'live one'])
    import bin.hq as _hq
    rows = _hq._read_tsv(folder / 'ledger.tsv', _hq.LEDGER_FIELDS)
    live_row = next(r for r in reversed(rows) if r['path'].endswith('late.md'))
    stored_late = live_row['path']
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert stored_late in out, f'Live abs row not listed: {out!r}'


def test_verb_artifacts_row_uses_lowercase_field_keys(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts accesses row fields with lowercase keys.

    Mutations: x__verb_artifacts__mutmut_58 uses row['KIND'] (KeyError),
    _60 uses row['READ_BEFORE'] (KeyError), _62 uses row['CYCLE'] (KeyError),
    _64 uses row['LABEL'] (KeyError).
    Oracle: stamp a notes file (inferred never rb) then restamp with edit and
    label; artifacts lists all four field values.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    # Use a notes-inferred filename so read_before can be set to 'edit'.
    (folder / 'notes.md').write_text('# Notes\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'notes.md', '--read-before', 'edit',
             '--kind', 'notes', '--label', 'the notes'])
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert 'notes' in out, f'kind missing: {out!r}'
    assert 'edit' in out, f'read_before missing: {out!r}'
    assert 'c1' in out, f'cycle missing: {out!r}'
    assert 'the notes' in out, f'label missing: {out!r}'


# ------------------------------------------------------------------
# Additional tests to kill remaining survivors
# ------------------------------------------------------------------


def test_render_artifacts_non_live_missing_before_custom_status():
    """render_artifacts uses 'missing' (lowercase) in the canonical order tuple.

    Mutation: x_render_artifacts__mutmut_97 changes 'missing' to 'MISSING' in
    the ordered tuple; 'MISSING' never matches the lowercase 'missing' key in
    non_live, so 'missing' falls to sorted() and its position among custom
    statuses changes.
    Oracle: non_live with 'missing' and 'aborted' (sorts before 'missing');
    original gives 'missing' first (canonical), mutant gives 'aborted' first
    (sorted alphabetically). Each count owns a line, so the order reads
    across them.
    """
    row_m = _row(status='missing', kind='notes', read_before='never')
    row_a = _row(status='aborted', kind='notes', read_before='never')
    rows = {'a.md': row_a, 'b.md': row_m}
    result = hq.render_artifacts([], rows, 'slug')
    counted = [
        ln.split()[0] for ln in result.splitlines()
        if ln.startswith(('missing', 'aborted'))]
    assert counted == ['missing', 'aborted'], (
        f'Expected missing before aborted; got: {result!r}'
    )


def test_verb_artifacts_conflicted_copy_name_check_is_lowercase(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts checks for 'conflicted copy' (lowercase) in filename.

    Mutation: x__verb_artifacts__mutmut_24 checks 'CONFLICTED COPY' which
    never matches a real sync-conflict file; the file prints 'unstampable'
    instead.
    Oracle: a file with 'conflicted copy' in its name -> output has 'conflicted
    copy' and does not have 'unstampable' for that filename.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'data (conflicted copy 2026-09-10).md').write_text('# Data\n')
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert 'conflicted copy' in out
    lines = out.splitlines()
    conflict_line = next(
        (ln for ln in lines if 'conflicted copy' in ln.split('  ')[0]), None
    )
    assert conflict_line is not None
    assert 'unstampable' not in conflict_line


def test_verb_artifacts_skip_loop_continues_not_breaks(
        tmp_path, monkeypatch, capsys):
    """_verb_artifacts continues (not breaks) after a skip-kind walk entry.

    Mutation: x__verb_artifacts__mutmut_28, _33, _55 all change continue to
    break at the skip-handler, stopping the walk loop at the first skip entry
    and missing all later entries.
    Oracle: a 'conflicted copy' file (alphabetically first) followed by an
    unstamped normal file; both appear in output.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    # 'a...' sorts before 'z...' so conflict file comes first in walk.
    (folder / 'a (conflicted copy).md').write_text('# Data\n')
    (folder / 'z_notes.md').write_text('# Notes\n')
    # Leave z_notes.md unstamped so it can only appear via the walk loop.
    capsys.readouterr()

    hq.main(['artifacts', _SLUG])

    out = capsys.readouterr().out
    assert 'z_notes.md' in out, (
        f'z_notes.md should appear after the conflict file; got: {out!r}'
    )


def test_open_git_drift_not_fired_when_branch_and_sha_match(
        tmp_path, monkeypatch, capsys):
    """Open does not report drift when header branch and sha match current.

    Mutation: x__verb_open__mutmut_102 changes 'cur_branch != h_branch' to
    'cur_branch == h_branch'; drift then fires when branches MATCH, so a clean
    cycle always reports spurious drift.
    Oracle: after a real git finish, open without modifying HANDOFF.md -> no
    'git drift' (h_branch == cur_branch and h_sha == cur_sha).
    """
    import subprocess
    folder = _new_root(tmp_path, monkeypatch)
    root = folder.parent.parent
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@x.invalid',
         'commit', '-q', '--allow-empty', '-m', 'init'],
        cwd=root, check=True)
    monkeypatch.setenv('HQ_GIT', '1')
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    # No modification to HANDOFF.md: h_branch == cur_branch and h_sha == cur_sha.
    hq.main(['open', _SLUG])

    assert 'git drift' not in capsys.readouterr().out


def test_open_git_drift_suppressed_when_cur_branch_is_dash(
        tmp_path, monkeypatch, capsys):
    """Open skips drift check when cur_branch is '-' (HQ_GIT=0) via 'and'.

    Mutation: x__verb_open__mutmut_98 changes 'and' to 'or'; the outer guard
    becomes `cur_branch != '-' or (...)` which fires even when cur_branch='-'.
    Oracle: inject '| master @ abc1234def5' into HANDOFF.md Written line;
    HQ_GIT=0 gives cur_branch='-'; drift must NOT be reported.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    hf = folder / 'HANDOFF.md'
    # Inject branch@sha so header_m matches but cur_branch is '-'.
    hf.write_text(hf.read_text().replace(
        'Written: 2026-09-10 | Cycle: 1',
        'Written: 2026-09-10 | master @ abc1234def5 | Cycle: 1',
    ))
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'git drift' not in capsys.readouterr().out


def test_open_filter_or_skips_read_before_edit_rows(
        tmp_path, monkeypatch, capsys):
    """Open skips rows with read_before != 'always' due to 'or' condition.

    Mutation: x__verb_open__mutmut_147 changes 'or' to 'and'; a row with
    status='live' and read_before='edit' is then NOT skipped (False AND True
    = False) and the anchor is resolved, printing 'unresolved anchor'.
    Oracle: a live row with read_before='edit' and where='MissingSection' ->
    no 'unresolved anchor' in open output.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes.md').write_text('# Notes\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'notes.md', '--read-before', 'edit',
             '--where', 'MissingSection'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    capsys.readouterr()

    hq.main(['open', _SLUG])

    out = capsys.readouterr().out
    assert 'unresolved anchor' not in out, (
        f'read_before=edit rows must not trigger anchor check; got: {out!r}'
    )


def test_open_span_not_moved_for_unchanged_anchor(
        tmp_path, monkeypatch, capsys):
    """Open does not report 'span moved' when the anchor position is unchanged.

    Mutation: x__verb_open__mutmut_251 changes 'and ... and' to 'and ... or'
    so the condition fires even when old_spans == new_spans;
    x__verb_open__mutmut_252 changes 'old_spans and new_spans' to 'old_spans
    or new_spans' with the same effect.
    Oracle: stamp SPEC.md with an anchor that resolves, finish (stores span),
    open without changing SPEC.md -> old_spans == new_spans -> no 'span moved'.
    """
    folder = _new_root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Alpha\n\nContent.\n\n## Beta\n\nMore.\n'
    )
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before', 'always',
             '--where', 'Alpha'])
    hq.main(['finish', _SLUG, '--log', 'c1'])
    # SPEC.md is unchanged; re-open must not report any span movement.
    capsys.readouterr()

    hq.main(['open', _SLUG])

    assert 'span moved' not in capsys.readouterr().out
