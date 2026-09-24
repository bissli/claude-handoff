"""Adoption, read, note, and retrieval paths of hq.py.

Each test drives one seam of the adopt/read/note/retrieval group: the
Key files grader, the anchor resolver, the slug guard, the walk, the
note parser, and the printed spans and counts of read, when, diff,
artifacts, and standing.
"""

import io
import os
import pathlib
import signal

import pytest

from bin import hq

_SLUG = 'demo'
_SESSION = 'session-abc'
_NOW = '2026-09-01T12:00:00'

_CURSOR = (
    '## Task\n\nDo the work.\n\n'
    '## Now\n\nNext step.\n\n'
    '## Plan\n\nPlan line.\n\n'
    '## State\n\nState line.\n\n'
    '## Environment\n\nEnv line.\n\n'
    '## Open questions\n\nNone.\n'
)

_SPEC = (
    '# Spec\n'
    '\n'
    '## s4: Field-to-path mapping\n'
    '\n'
    'Each entry maps a sha to a path.\n'
    'The sha covers source and template.\n'
    '\n'
    '## Cache invalidation\n'
    '\n'
    'A page is stale when its sha moved.\n'
)


def _root(tmp_path, monkeypatch, slug=_SLUG, cycle=None):
    """Create an HQ_ROOT with an empty handoff folder and set the env."""
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
    """Return the ledger rows of the folder as a list of dicts."""
    return hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)


def _row(**overrides):
    """Build a full thirteen-field ledger row with the given overrides."""
    row = dict.fromkeys(hq.LEDGER_FIELDS, '-')
    row.update({
        'cycle': '1', 'base': 'folder', 'kind': 'spec', 'status': 'live',
        'read_before': 'always', 'label': 'a label',
        })
    row.update(overrides)
    return row


# --- item 1: the note parser ------------------------------------------


def test_note_takes_a_dash_batch_marker_and_a_body_positional(
        tmp_path, monkeypatch):
    """`note <slug> --batch -` reads stdin, and a bare body is a positional.

    Mutation: choices restored on note_kind, so the dash marker exits 2 and
    writes nothing; or the body positional dropped, so the body is lost.
    Oracle: standing.md after the run - one batched item and one direct
    item, each with its own body.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    monkeypatch.setattr('sys.stdin', io.StringIO(
        'constraint --headline "Keep the floor" "Never below one."\n'))
    assert hq.main(['note', _SLUG, '--batch', '-']) == 0
    assert hq.main([
        'note', _SLUG, 'decision', '--headline', 'Ship it', 'Body text.']) == 0
    standing = (folder / 'standing.md').read_text()
    assert '- [c01] (c1) **Keep the floor** Never below one.' in standing
    assert '- [d01] (c1) **Ship it** Body text.' in standing


def test_note_refuses_an_unknown_kind_and_a_missing_headline(
        tmp_path, monkeypatch, capsys):
    """An unknown kind and an empty headline each exit 2 and write nothing.

    Mutation: the kind guard dropped once choices leave the parser, so
    `note <slug> wish body` appends nothing yet exits 0; or the headline
    left unvalidated, so `note <slug> decision body` writes `****`.
    Oracle: the two documented lines, exit 2 each, and an unchanged
    standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    before = (folder / 'standing.md').read_text()
    assert hq.main(['note', _SLUG, 'wish', '--headline', 'H', 'body']) == 2
    assert (
        "hq note: kind must be decision, constraint, or dead-end; got 'wish'"
        in capsys.readouterr().out)
    assert hq.main(['note', _SLUG, 'decision', 'body']) == 2
    assert 'hq note: --headline is required' in capsys.readouterr().out
    assert (folder / 'standing.md').read_text() == before


def test_note_folds_a_newline_in_the_headline_to_one_space(
        tmp_path, monkeypatch):
    """A headline carrying a newline still writes one physical item line.

    Mutation: the headline written raw, so the item spans two lines and
    _parse_standing, standing, and supersede never see it.
    Oracle: standing.md has one item line and _parse_standing finds it.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    assert hq.main([
        'note', _SLUG, 'decision',
        '--headline', 'Two\nlines\there', 'Body.']) == 0
    text = (folder / 'standing.md').read_text()
    assert '- [d01] (c1) **Two lines here** Body.' in text
    items, _ = hq._parse_standing(text)
    assert [i['headline'] for i in items] == ['Two lines here']


def test_note_rejects_an_unknown_flag_instead_of_swallowing_it(
        tmp_path, monkeypatch):
    """An unknown flag is a usage error, never body text.

    Mutation: main collecting parse_known_args leftovers into the body, so
    `--bogus` lands in standing.md at exit 0.
    Oracle: exit 2 and no item in standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    assert hq.main([
        'note', _SLUG, 'decision', '--headline', 'H', '--bogus', 'b']) == 2
    assert '[d01]' not in (folder / 'standing.md').read_text()


def test_note_batch_names_the_bad_line_and_runs_the_rest(
        tmp_path, monkeypatch, capsys):
    """A batch line missing its headline is named; the other lines still run.

    Mutation: the batch aborting on the first bad line, or a headline-less
    line writing `****` at exit 0.
    Oracle: exit 2, the documented line naming line 1, and only the second
    item in standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    monkeypatch.setattr('sys.stdin', io.StringIO(
        'decision "no headline here"\n'
        'constraint --headline "Keep" "the floor"\n'))
    assert hq.main(['note', _SLUG, '--batch', '-']) == 2
    out = capsys.readouterr().out
    assert 'hq note: batch line 1 not parsed: decision "no headline here"' in out
    standing = (folder / 'standing.md').read_text()
    assert '**Keep** the floor' in standing
    assert 'no headline here' not in standing


# --- item 2: the Key files grader -------------------------------------


def test_adopt_grades_nested_star_and_inline_labeled_pointers(
        tmp_path, monkeypatch, capsys):
    """A label bullet grades its nested bullets; star bullets are pointers.

    Mutation: the bullet regex tested before the group-label regex, so
    `- Read now:` becomes a pointer path named `Read`; or `*` bullets left
    as loose lines under ## Unfiled; or an inline `Reference only:` kept in
    the label text.
    Oracle: the ledger rows - no `Read` row, SPEC.md always with its label
    and s4 seed, notes-algos.md edit with the label alone - and a
    conservation line reporting nothing lost, since a bare label bullet
    carries no content.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text(_SPEC, encoding='utf-8')
    (folder / 'notes-algos.md').write_text('# Notes\n\nBackground.\n')
    _handoff(folder, key_files=(
        '- Read now:\n'
        '  - `SPEC.md` cache schema; s4 field-to-path mapping\n'
        '* `notes-algos.md` Reference only: diffing background\n'
        ))
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'Read' not in rows
    assert rows['SPEC.md']['read_before'] == 'always'
    assert rows['SPEC.md']['label'] == 'cache schema; s4 field-to-path mapping'
    assert rows['SPEC.md']['where'] == 's4'
    assert rows['notes-algos.md']['read_before'] == 'edit'
    assert rows['notes-algos.md']['label'] == 'diffing background'
    assert '- unfiled:' not in (folder / 'HANDOFF.md').read_text()
    assert 'conservation: every original line carried' in capsys.readouterr().out


# --- item 3: a pointer the walk cannot see ----------------------------


def test_adopt_infers_a_nested_pointer_below_the_top_level(
        tmp_path, monkeypatch):
    """A nested .py pointer is not a draft, and `Reference only` grades it
    `edit` only when its kind is notes.

    Mutation: infer_kind called without top_level=False, so sub/deep.py is a
    draft and R1 locks it at always; or `Reference only` applied as edit
    whatever the kind.
    Oracle: the sub/deep.py row - kind other, read_before never (the
    Reference only grade never applies to a non-notes file), no refusal.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'deep.py').write_text('x = 1\n')
    _handoff(folder, key_files=(
        '- `sub/deep.py` Reference only: nested helper\n'
        ))
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['sub/deep.py']['kind'] == 'other'
    assert rows['sub/deep.py']['read_before'] == 'never'
    assert rows['sub/deep.py']['reason'] == '-'


# --- item 4: the adopted Log ------------------------------------------


def test_adopt_renders_the_log_row_it_appends(tmp_path, monkeypatch):
    """The adopted HANDOFF.md carries its own manifest row in ## Log.

    Mutation: the Log rendered from the manifest read before the adopt row
    is appended, leaving ## Log empty in the file adopt just wrote.
    Oracle: hand-computed - render_log of one repo-less row is
    `- 2026-09-01: adopted`.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    text = (folder / 'HANDOFF.md').read_text()
    log_body = text.split('## Log\n', 1)[1]
    assert log_body.strip() == '- 2026-09-01: adopted'


# --- item 5: s<d> anchors ---------------------------------------------


def test_section_anchor_accepts_every_leading_token_form():
    """`s<d>` resolves against `4`, `4.`, `4:`, `s4.`, and `s4:`, not `S4 x`.

    Mutation: the number fallback matching only a bare leading digit, so the
    `s4` adopt seeds never resolves against `## s4: Title` and the read block
    prints `SPEC.md:?` every cycle; the `s<d>` alternative taking a colon
    alone, so `## S4. Title` is unresolved; or matching a bare `s<d>` word,
    so `## S3 bucket layout` answers for section 3.
    Oracle: hand-computed spans - each two-line fixture puts the heading on
    line 1 and its body on line 2; the bare-word heading stays unresolved.
    """
    for heading in ('## 4 Title', '## 4. Title', '## 4: Title', '## s4: Title',
                    '## S4. Title'):
        text = f'{heading}\nBody.\n'
        assert hq.resolve_where(text, ['s4']) == ([(1, 2)], []), heading
    assert hq.resolve_where('## S3 bucket layout\nBody.\n', ['s3']) == ([], ['s3'])
    assert hq.resolve_where(
        '## S3 bucket layout\nBody.\n## Bucket layout\nOther.\n',
        ['Bucket layout']) == ([(3, 4)], [])


def test_literal_anchor_ignores_the_leading_section_token():
    """The literal heading text resolves even when the heading is numbered.

    Mutation: _norm_heading stripping only a bare leading number, so the
    literal form of `## s4: Field-to-path mapping` never matches.
    Oracle: hand-computed - the heading is line 3 and the next same-level
    heading is line 8, so the span is 3-7.
    """
    assert hq.resolve_where(_SPEC, ['Field-to-path mapping']) == ([(3, 7)], [])


def test_literal_anchor_matches_a_whole_heading_not_a_prefix():
    """An anchor matches a heading by equality, never as a substring.

    Mutation: `in` in place of `==` in the literal match, so `Retry` lands on
    `## Retry budget` and prints the wrong span.
    Oracle: hand-computed - `## Retry budget` is line 1 and `## Retry` is
    line 4, so equality gives 4-5.
    """
    text = '## Retry budget\nBudget.\n\n## Retry\nRetry.\n'
    assert hq.resolve_where(text, ['Retry']) == ([(4, 5)], [])


def test_adopt_seeds_an_anchor_the_read_block_can_resolve(
        tmp_path, monkeypatch):
    """The `s4` adopt seeds from a pointer resolves in the rendered block.

    Mutation: either half of the s<d> fix reverted, so the read block prints
    `SPEC.md:?` and `open` prints an unresolved anchor every cycle.
    Oracle: hand-computed - `## s4: Field-to-path mapping` is line 3 and the
    next same-level heading is line 8, so the block reads SPEC.md:3-7.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text(_SPEC, encoding='utf-8')
    _handoff(folder, key_files='- `SPEC.md` Read now: mapping, section 4\n')
    assert hq.main(['adopt', _SLUG]) == 0
    assert 'SPEC.md:3-7' in (folder / 'HANDOFF.md').read_text()


# --- item 6: the slug guard -------------------------------------------


def test_a_traversing_or_empty_slug_is_refused(tmp_path, monkeypatch, capsys):
    """`begin ..`, `begin ../../victim`, and `begin ''` write nothing.

    Mutation: the slug joined onto .handoff/ unchecked, so `begin ..` turns
    the project root into a handoff folder.
    Oracle: exit 2 with the documented line, and no HANDOFF.md anywhere
    outside .handoff/.
    """
    folder = _root(tmp_path, monkeypatch)
    root = folder.parent.parent
    for bad in ('..', '../../victim', '', 'nested/slug', '.hidden'):
        with pytest.raises(SystemExit) as exc:
            hq.main(['begin', bad])
        assert exc.value.code == 2
        assert f'hq: invalid slug {bad!r}' in capsys.readouterr().out
    assert not (root / 'HANDOFF.md').exists()
    assert list(root.parent.glob('*/HANDOFF.md')) == []


def test_a_read_only_verb_creates_no_handoff_directory(tmp_path, monkeypatch):
    """A miss on a root with no .handoff/ leaves the disk untouched.

    Mutation: .handoff.mkdir before the resolve, so any typo on any verb
    creates directories under a wrong root.
    Oracle: root/.handoff does not exist after the failed lookup.
    """
    root = pathlib.Path(tmp_path) / 'bare'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_GIT', '0')
    with pytest.raises(SystemExit) as exc:
        hq.main(['open', 'ghost'])
    assert exc.value.code == 2
    assert not (root / '.handoff').exists()


# --- item 7: the walk skips what it cannot stamp ----------------------


def test_walk_skips_a_fifo_without_opening_it(tmp_path, monkeypatch):
    """A FIFO in the folder is skipped, not read.

    Mutation: the walk opening every non-directory entry, so a FIFO with no
    writer blocks every verb forever.
    Oracle: _walk_folder returns the FIFO as kind 'skip' and the call
    returns at all.
    """
    folder = _root(tmp_path, monkeypatch)
    os.mkfifo(folder / 'pipe')
    assert ('pipe', 'skip') in hq._walk_folder(folder)


def test_walk_skips_a_name_no_ledger_row_can_hold(
        tmp_path, monkeypatch, capsys):
    """A file name carrying a tab is skipped and named as unstampable.

    Mutation: the tab-name test dropped, so the row stores a scrubbed name
    that never matches the file again.
    Oracle: _walk_folder returns kind 'skip'; artifacts prints the
    unstampable marker, not the conflicted-copy one.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'bad\tname.md').write_text('# Notes\n')
    assert ('bad\tname.md', 'skip') in hq._walk_folder(folder)
    hq.main(['begin', _SLUG])
    capsys.readouterr()
    assert hq.main(['artifacts', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'bad\tname.md  unstampable' in out
    assert 'conflicted copy' not in out


# --- item 8: no traceback reaches the user ----------------------------


def test_a_root_that_is_a_file_is_named_not_raised(
        tmp_path, monkeypatch, capsys):
    """HQ_ROOT pointing at a regular file exits 2 naming the root.

    Mutation: the root used unchecked, so NotADirectoryError reaches the
    user; or the miss reported as a missing slug, which sends the reader
    after the wrong thing.
    Oracle: exit 2 and the documented line naming the root path.
    """
    bogus = pathlib.Path(tmp_path) / 'not-a-dir'
    bogus.write_text('x\n')
    monkeypatch.setenv('HQ_ROOT', str(bogus))
    monkeypatch.setenv('HQ_GIT', '0')
    with pytest.raises(SystemExit) as exc:
        hq.main(['open', _SLUG])
    assert exc.value.code == 2
    assert f'hq: root is not a directory: {bogus}' in capsys.readouterr().out


def test_a_handoff_that_is_a_directory_is_named_not_raised(
        tmp_path, monkeypatch, capsys):
    """A HANDOFF.md directory refuses adopt and open with one line.

    Mutation: read_text called on the path unchecked, so IsADirectoryError
    reaches the user.
    Oracle: exit 1 and the documented line from both verbs.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'HANDOFF.md').mkdir()
    assert hq.main(['adopt', _SLUG]) == 1
    assert 'hq: HANDOFF.md is a directory' in capsys.readouterr().out
    assert hq.main(['open', _SLUG]) == 1
    assert 'hq: HANDOFF.md is a directory' in capsys.readouterr().out


def test_undecodable_bytes_do_not_stop_a_read_or_a_ledger(
        tmp_path, monkeypatch):
    """A ledger row and a stamped file holding non-UTF-8 bytes still print.

    Mutation: any reader left at strict UTF-8, so one stray byte makes every
    verb raise UnicodeDecodeError.
    Oracle: when and read both exit 0 on a folder holding both.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_bytes(b'# Spec\n\n## Design\n\n\xff\xfe raw\n')
    assert hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Design']) == 0
    with (folder / 'ledger.tsv').open('ab') as fh:
        fh.write(b'9\t2026-09-01\tSPEC.md\tfolder\tspec\tlive\tnever\t-\t-\t'
                 b'aa\t1\t-\t\xff bad label\n')
    assert hq.main(['when', _SLUG, 'SPEC.md']) == 0
    assert hq.main(['read', _SLUG, 'SPEC.md']) == 0


def test_a_receipt_that_cannot_be_written_is_reported_after_the_spans(
        tmp_path, monkeypatch, capsys):
    """A session id with a slash is sanitized; a receipt neither the state
    dir nor the folder takes is reported, not raised.

    Mutation: the session id used raw in the receipt name, so
    HQ_SESSION='a/b' raises FileNotFoundError; or the receipt write left
    unguarded, so a state dir that is a file raises FileExistsError.
    Oracle: the sanitized receipt file exists; with the state dir a file
    and the folder receipt a directory, the second run prints the span
    first, then the documented line, and exits 1.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Design\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Design'])
    monkeypatch.setenv('HQ_SESSION', 'a/b')
    assert hq.main(['read', _SLUG, 'SPEC.md']) == 0
    assert (pathlib.Path(tmp_path) / 'hq-reads-a_b.txt').exists()
    blocked = pathlib.Path(tmp_path) / 'blocked-state'
    blocked.write_text('x\n')
    monkeypatch.setenv('HQ_STATE_DIR', str(blocked))
    (folder / '.hq.reads-a_b').mkdir()
    capsys.readouterr()
    assert hq.main(['read', _SLUG, 'SPEC.md']) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == '## Design'
    assert lines[-1].startswith('hq read: receipt not written: ')


def test_a_read_only_state_dir_moves_the_receipt_into_the_folder(
        tmp_path, monkeypatch, capsys):
    """A state dir the receipt cannot reach sends it to the handoff folder.

    Mutation: the folder fallback dropped, so the read exits 1 and no
    folder receipt exists; or the fallback written even when the state
    dir took the receipt.
    Oracle: a writable state dir holds the line and the folder holds
    none; a state dir that is a file leaves the same line in
    .hq.reads-<session>, and the read exits 0 with no failure line.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\n## Design\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Design'])
    monkeypatch.setenv('HQ_SESSION', 's1')
    assert hq.main(['read', _SLUG, 'SPEC.md']) == 0
    assert (pathlib.Path(tmp_path) / 'hq-reads-s1.txt').read_text().endswith(
        f' {_SLUG} SPEC.md\n')
    assert not (folder / '.hq.reads-s1').exists()
    blocked = pathlib.Path(tmp_path) / 'blocked-state'
    blocked.write_text('x\n')
    monkeypatch.setenv('HQ_STATE_DIR', str(blocked))
    capsys.readouterr()
    assert hq.main(['read', _SLUG, 'SPEC.md']) == 0
    assert 'receipt not written' not in capsys.readouterr().out
    assert (folder / '.hq.reads-s1').read_text().endswith(
        f' {_SLUG} SPEC.md\n')


# --- item 9: a byte-order mark ----------------------------------------


def test_adopt_strips_a_byte_order_mark_from_the_title(tmp_path, monkeypatch):
    """A BOM before the title does not file the title under ## Unfiled.

    Mutation: HANDOFF.md read as plain utf-8, so the BOM keeps the title line
    from matching and adopt files it as `- unfiled: <BOM># Handoff: ...`.
    Oracle: the adopted file carries no ## Unfiled section.
    """
    folder = _root(tmp_path, monkeypatch)
    text = _handoff(folder)
    (folder / 'HANDOFF.md').write_text('\ufeff' + text, encoding='utf-8')
    assert hq.main(['adopt', _SLUG]) == 0
    assert '## Unfiled' not in (folder / 'HANDOFF.md').read_text()


# --- item 10: the printed span ----------------------------------------


def test_read_prints_the_anchor_span_verbatim(tmp_path, monkeypatch, capsys):
    """`read` prints the heading line through the line before the next
    same-or-higher heading, and nothing else.

    Mutation: span[0] + 1 in the slice, dropping the heading line; or the
    span run to the next heading inclusive.
    Oracle: hand-computed - the anchor heading is line 3 and `## Other` is
    line 8, so lines 3-7 print verbatim.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text(
        '# Spec\n\n## Design\n\nAlpha.\nBeta.\n\n## Other\n\nMore.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md', '--where', 'Design'])
    capsys.readouterr()
    assert hq.main(['read', _SLUG, 'SPEC.md']) == 0
    assert capsys.readouterr().out == '## Design\n\nAlpha.\nBeta.\n\n'


# --- item 11: the read block sort -------------------------------------


def test_read_block_sorts_by_kind_then_path():
    """The read block orders rows by kind first, path second.

    Mutation: the sort key reduced to the path alone, so a draft sorts after
    an alphabetically earlier spec.
    Oracle: hand-computed - draft/zeta.py precedes spec/alpha.md by kind,
    and the reverse by path.
    """
    rows = [
        _row(path='alpha.md', kind='spec', label='the spec'),
        _row(path='zeta.py', kind='draft', label='the draft'),
        ]
    body = hq.render_read(rows, {}, {'alpha.md': 10, 'zeta.py': 20})
    assert [ln.split(' ')[0] for ln in body.splitlines()] == [
        'zeta.py', 'alpha.md']


# --- item 12: standing --all ------------------------------------------


def test_standing_all_marks_the_superseded_item(tmp_path, monkeypatch, capsys):
    """`--all` adds the superseded item and marks it; the default omits it.

    Mutation: --all ignored, so the superseded item never prints; or the
    marker inverted onto the live item.
    Oracle: d01 is superseded by d02 - with --all d01 carries the marker and
    d02 does not; without it d01 is absent.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'Old way', 'First.'])
    hq.main(['note', _SLUG, 'decision', '--headline', 'New way', 'Second.'])
    hq.main(['supersede', _SLUG, 'd01', 'd02'])
    capsys.readouterr()
    assert hq.main(['standing', _SLUG, '--all']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln for ln in lines if ln.startswith('[d01]')][0].endswith(
        ' [superseded by d02 in c1]')
    assert [ln for ln in lines if ln.startswith('[d02]')][0].endswith('Second.')
    assert hq.main(['standing', _SLUG]) == 0
    assert '[d01]' not in capsys.readouterr().out


def _chain_of_three(tmp_path, monkeypatch):
    """Build a d01 -> d02 -> d03 supersession chain and return the folder."""
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    for headline, body in (
        ('Store totals per region', 'First.'),
        ('Store totals per city', 'Second.'),
        ('Store totals per block', 'Third.'),
    ):
        hq.main(['note', _SLUG, 'decision', '--headline', headline, body])
    hq.main(['supersede', _SLUG, 'd01', 'd02'])
    # The second edge lands in a later cycle than the first, so an
    # assertion on the cycle number tells the edge that made d03
    # current from the edge that opened the chain.
    monkeypatch.setenv('HQ_CYCLE', '2')
    hq.main(['supersede', _SLUG, 'd02', 'd03'])
    return folder


def test_standing_id_names_the_end_of_the_chain(tmp_path, monkeypatch, capsys):
    """A lookup past one hop names the id now current and every hop to it.

    Mutation: stopping the walk at the first edge, so d01 reports d02 -
    itself superseded - as current; or the cycle read off the first
    edge rather than off the edge that made d03 current.
    Oracle: a hand-built d01 -> d02 -> d03 chain whose second edge lands
    in c2, where the lookup of d01 must name d03 in c2 and spell the
    chain d01 -> d02 -> d03.
    """
    _chain_of_three(tmp_path, monkeypatch)
    capsys.readouterr()
    assert hq.main(['standing', _SLUG, 'd01']) == 0
    line = capsys.readouterr().out.splitlines()[0]
    assert line.endswith(' [superseded by d03 in c2 - d01 -> d02 -> d03]')


def test_standing_id_leaves_the_end_of_the_chain_live(tmp_path, monkeypatch, capsys):
    """The id at the end of the chain reports no successor of its own.

    Mutation: following an edge backwards, so a lookup of d03 reports
    itself superseded by d01 or d02.
    Oracle: the same d01 -> d02 -> d03 chain - d03 carries no marker, and
    the default listing shows d03 and neither of the other two.
    """
    _chain_of_three(tmp_path, monkeypatch)
    capsys.readouterr()
    assert hq.main(['standing', _SLUG, 'd03']) == 0
    assert 'superseded' not in capsys.readouterr().out
    assert hq.main(['standing', _SLUG]) == 0
    ids = [ln[:5] for ln in capsys.readouterr().out.splitlines()]
    assert ids == ['[d03]']


def test_standing_all_names_the_end_of_the_chain(tmp_path, monkeypatch, capsys):
    """Every superseded item in the listing names the id now current.

    Mutation: the listing printing a bare [superseded] with no id, or
    naming each item's own first hop, so d01 points at the dead d02.
    Oracle: the same d01 -> d02 -> d03 chain - both d01 and d02 name d03,
    and the listing spells no chain.
    """
    _chain_of_three(tmp_path, monkeypatch)
    capsys.readouterr()
    assert hq.main(['standing', _SLUG, '--all']) == 0
    marked = {
        ln[:5]: ln[ln.rindex(' ['):] for ln in capsys.readouterr().out.splitlines()
        if ln.endswith(']')
        }
    assert marked['[d01]'] == ' [superseded by d03 in c2]'
    assert marked['[d02]'] == ' [superseded by d03 in c2]'


def test_standing_chain_that_loops_back_stops(tmp_path, monkeypatch, capsys):
    """A cycle in the hand-written edges ends the walk instead of looping.

    Mutation: dropping the seen-set guard, so d01 -> d02 -> d01 walks
    forever; or adding the repeated id before the guard reads it, so the
    printed chain doubles back to d01.
    Oracle: a hand-written standing.md whose edges form a cycle - the walk
    ends at d02 under a 2-second alarm.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'standing.md').write_text(
        '- [d01] (c1) **Store totals per region** First.\n'
        '- [d02] (c1) **Store totals per city** Second.\n'
        '- (c1) d01 -> d02\n'
        '- (c2) d02 -> d01\n',
        encoding='utf-8')
    capsys.readouterr()

    def _alarm(signum, frame):
        raise TimeoutError('standing timed out - the seen-set guard is gone')

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(2)
    try:
        assert hq.main(['standing', _SLUG, 'd01']) == 0
    finally:
        signal.alarm(0)
    line = capsys.readouterr().out.splitlines()[0]
    assert line.endswith(' [superseded by d02 in c1]')


# --- item 13: the diff cap --------------------------------------------


def test_diff_summarizes_a_section_and_expands_it_without_a_cap(
        tmp_path, monkeypatch, capsys):
    """The summary counts a section's change; the expansion prints every line.

    Mutation: a line cap on the expansion, so a 61-line drop loses its
    last line; a set difference for the count, so a removed line that
    also stands elsewhere goes uncounted; or the summary printing the
    lines themselves.
    Oracle: hand-counted - cycle 1 carries 61 Task lines cycle 2 drops:
    the summary is the one line '## Task  -61', and 'task' expands to
    that line and 61 '- ' lines in file order.
    """
    folder = _root(tmp_path, monkeypatch, cycle='1')
    hq.main(['begin', _SLUG])
    body = '\n'.join(f'Line {i:02d}' for i in range(61))
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 1\n\n'
        f'## Task\n{body}\n')
    hq.main(['finish', _SLUG, '--log', 'one'])
    monkeypatch.setenv('HQ_CYCLE', '2')
    hq.main(['begin', _SLUG])
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-01 | Cycle: 2\n\n## Task\n')
    hq.main(['finish', _SLUG, '--log', 'two'])
    capsys.readouterr()
    assert hq.main(['diff', _SLUG, '1', '2']) == 0
    assert capsys.readouterr().out.splitlines() == ['## Task  -61']
    assert hq.main(['diff', _SLUG, '1', '2', 'task']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == '## Task  -61'
    assert lines[1:] == [f'- Line {i:02d}' for i in range(61)]


# --- item 14: the when cap --------------------------------------------


def _seed_when(folder, count):
    """Write a ledger holding `count` rows for SPEC.md, cycle 1 upward."""
    rows = ['\t'.join(hq.LEDGER_FIELDS)]
    for i in range(1, count + 1):
        row = _row(cycle=str(i), ts='2026-09-01T12:00:00', path='SPEC.md',
                   label=f'label {i}')
        rows.append('\t'.join(row[f] for f in hq.LEDGER_FIELDS))
    (folder / 'ledger.tsv').write_text('\n'.join(rows) + '\n')


def test_when_prints_every_row_oldest_first(tmp_path, monkeypatch, capsys):
    """31 rows print 31 lines, oldest first, with no cut.

    Mutation: a row cap restored, so the oldest row leaves the output
    and a count line stands in for it; or the rows reversed so the
    newest prints first.
    Oracle: hand-counted - 31 seeded rows, cycle 1 first and cycle 31
    last, no 'omitted' line.
    """
    folder = _root(tmp_path, monkeypatch)
    _seed_when(folder, 31)
    capsys.readouterr()
    assert hq.main(['when', _SLUG, 'SPEC.md']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 31
    assert lines[0].split('\t')[1] == '1'
    assert lines[-1].split('\t')[1] == '31'
    assert not any('omitted' in ln for ln in lines)


# --- item 15: the artifacts overflow count ----------------------------


def test_artifacts_block_prints_every_full_row_with_no_cap():
    """43 full rows render 43 lines and no count line.

    Mutation: a cap on the full lines, folding the rows past it into a
    kind count the reader never sees a label for.
    Oracle: hand-counted - 43 live always spec rows give 43 full lines
    and no '- hq artifacts' count line.
    """
    walk = [(f'SPEC-{i:02d}.md', 'spec') for i in range(43)]
    rows = {
        f'SPEC-{i:02d}.md': _row(path=f'SPEC-{i:02d}.md', cycle=str(i + 1))
        for i in range(43)
        }
    body = hq.render_artifacts(walk, rows, 'demo', set(rows)).splitlines()
    assert len(body) == 43
    assert sum(1 for ln in body if '  spec  always  ' in ln) == 43
    assert not any(' - hq artifacts ' in ln for ln in body)


def test_walk_skips_dotfiles_and_infers_a_spec_from_its_heading(
        tmp_path, monkeypatch, capsys):
    """A dotfile never enters the walk; a heading makes overview.md a spec.

    Mutation: the dotfile test dropped, so .hq.lock siblings become rows; or
    the first-heading read guarded by `if is_dir`, so a spec heading on an
    unnamed file is never seen.
    Oracle: _walk_folder over a folder holding both; artifacts prints the
    spec marker for overview.md.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / '.hidden.md').write_text('# Spec\n')
    (folder / 'overview.md').write_text('# Spec\n\nBody.\n')
    walk = hq._walk_folder(folder)
    assert [n for n, _ in walk] == ['overview.md']
    assert walk == [('overview.md', 'spec')]
    hq.main(['begin', _SLUG])
    capsys.readouterr()
    assert hq.main(['artifacts', _SLUG]) == 0
    assert 'overview.md  spec?  unstamped' in capsys.readouterr().out


def test_an_exact_folder_name_beats_a_longer_prefix_match(
        tmp_path, monkeypatch):
    """A slug matching one folder exactly never resolves to a prefix mate.

    Mutation: the exact-name test removed, so `demo` is ambiguous between
    demo and demo-extra and exits 2.
    Oracle: the resolved folder is .handoff/demo.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder.parent / 'demo-extra').mkdir()
    assert hq._find_folder(folder.parent.parent, 'demo') == folder


# --- addendum A: the conservation judgment line -----------------------


def test_adopt_reports_original_lines_the_rewrite_dropped(
        tmp_path, monkeypatch, capsys):
    """Adopt measures the file it read, then the hand rewrite against HANDOFF.orig.md.

    Mutation: the conservation call dropped, or run against the conforming
    file instead of the original, so a section the rewrite lost is never
    named.
    Oracle: HANDOFF.orig.md holds one ## Summary line absent from the
    rewrite and one the rewrite kept; the faithful copy reports every
    line carried.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    (folder / 'HANDOFF.orig.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-08-01 | Cycle: 2\n\n'
        '## Summary\n\n'
        'Do the work.\n'
        'The pipeline drops empty rows.\n')
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'conservation: every original line carried' in out
    assert 'conservation vs HANDOFF.orig.md: 1 lines not carried' in out
    assert '  not carried: The pipeline drops empty rows.' in out

    other = _root(tmp_path, monkeypatch, slug='faithful')
    text = _handoff(other)
    (other / 'HANDOFF.orig.md').write_text(text)
    capsys.readouterr()
    assert hq.main(['adopt', 'faithful']) == 0
    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_orig_conservation_counts_content_rehomed_into_a_stamped_sibling(
        tmp_path, monkeypatch, capsys):
    """A line moved whole into a notes sibling counts as carried for HANDOFF.orig.md.

    Mutation: the orig union built from the cursor, standing.md, and the
    labels alone, so every line the agent rehomed into a sibling as the
    skill instructs prints as not carried; or the substring test run on
    raw whitespace, so a line re-spaced in the sibling fails.
    Oracle: HANDOFF.orig.md holds two ## Summary lines the rewrite moved
    to notes-summary.md (one with a doubled space there) and one line
    nobody kept; the orig line reports exactly that one.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-summary.md').write_text(
        '# Summary\n\nThe pipeline  drops empty rows.\n'
        'Retries are capped at three.\n')
    _handoff(folder, key_files=(
        '- `notes-summary.md` Reference only: rehomed summary\n'))
    (folder / 'HANDOFF.orig.md').write_text(
        f'# Handoff: {_SLUG}\n\n'
        'Written: 2026-08-01 | Cycle: 2\n\n'
        '## Summary\n\n'
        'The pipeline drops empty rows.\n'
        'Retries are capped at three.\n'
        'The cache is warmed at start.\n')
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'conservation vs HANDOFF.orig.md: 1 lines not carried' in out
    assert '  not carried: The cache is warmed at start.' in out


# --- addendum B: slug lines on stdout ---------------------------------


def test_slug_resolution_lines_print_on_stdout(tmp_path, monkeypatch, capsys):
    """The ambiguous-slug and no-match lines go to stdout, not stderr.

    Mutation: either line written to stderr, where the agent's own capture
    of the command output never shows it.
    Oracle: capsys - the text is in .out and .err is empty.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder.parent / 'demo-extra').mkdir()
    with pytest.raises(SystemExit):
        hq.main(['open', 'dem'])
    captured = capsys.readouterr()
    assert "hq: ambiguous slug 'dem': " in captured.out
    assert captured.err == ''
    with pytest.raises(SystemExit):
        hq.main(['open', 'zzz'])
    captured = capsys.readouterr()
    assert "hq: no folder matching 'zzz' under " in captured.out
    assert captured.err == ''


# --- a repo-relative pointer, and the seeded count --------------------------


def test_adopt_resolves_a_repo_relative_pointer_against_the_root(
        tmp_path, monkeypatch, capsys):
    """A Key files pointer the folder lacks is found under the project root.

    Mutation: adopt resolving pointers through _stored_path alone, so a
    repo-relative pointer in a foreign file becomes a missing folder-base
    row instead of the abs row pointing at the real file; or the seeded
    count reporting the walk's length, skip entries included.
    Oracle: the docs/guide.md row - base abs, read_before always from
    `Read now`, the file's own line count - and the printed count equal
    to the ledger's row count.
    """
    folder = _root(tmp_path, monkeypatch)
    root = folder.parent.parent
    (root / 'docs').mkdir()
    (root / 'docs' / 'guide.md').write_text('# Guide\n\none\ntwo\n')
    _handoff(folder, key_files='- `docs/guide.md` Read now: the field guide\n')
    assert hq.main(['adopt', _SLUG]) == 0
    rows = _ledger(folder)
    guide = [r for r in rows if r['path'].endswith('docs/guide.md')]
    assert len(guide) == 1
    assert guide[0]['base'] == 'abs'
    assert guide[0]['status'] == 'live'
    assert guide[0]['read_before'] == 'always'
    assert guide[0]['lines'] == '4'
    assert guide[0]['label'] == 'the field guide'
    out = capsys.readouterr().out
    assert f'hq adopt: seeded {len(rows)} entries in {_SLUG}' in out


def test_the_root_flag_beats_the_environment(tmp_path, monkeypatch, capsys):
    """`--root DIR` names the project root even when HQ_ROOT says otherwise.

    Mutation: `_resolve_root` reading only the environment, so the flag
    the interface documents is dead.
    Oracle: two roots; only the flagged one holds the slug, and `open`
    finds it while HQ_ROOT points at the empty one.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    assert hq.main(['adopt', _SLUG]) == 0
    other = pathlib.Path(tmp_path) / 'other'
    (other / '.handoff').mkdir(parents=True)
    monkeypatch.setenv('HQ_ROOT', str(other))
    with pytest.raises(SystemExit) as exc:
        hq.main(['open', _SLUG])
    assert exc.value.code == 2
    assert hq.main(['--root', str(folder.parent.parent), 'open', _SLUG]) == 0


# --- wording kept on adoption, and a label with a clause --------------------


def test_adopt_keeps_the_wording_of_an_item_whose_body_opens_with_a_comma(
        tmp_path, monkeypatch, capsys):
    """A `**bold**, rest` item reaches standing.md without an inserted space.

    Mutation: the headline and body joined with an unconditional space, so
    `**Keep the signature**, since ...` becomes `**Keep the signature** ,
    since ...` and conservation reports every such item as not carried.
    Oracle: the standing line ends with the original text after the bold
    span, and adopt's conservation line reports every original line
    carried.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra=(
        '\n## Decisions\n\n'
        '- **Keep the signature**, since every caller passes the literal\n'
        '  and no test changes.\n'))
    assert hq.main(['adopt', _SLUG]) == 0
    standing = (folder / 'standing.md').read_text()
    assert ('**Keep the signature**, since every caller passes the literal '
            'and no test changes.') in standing
    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_a_key_files_label_with_a_trailing_clause_is_structure(
        tmp_path, monkeypatch):
    """`Read now, under x/ unless noted:` grades the bullets and is filed.

    Mutation: the label regex admitting only `Read now:`, so the clause
    form never grades and its bullets keep the kind table's read_before;
    or the clause form read as pure structure, so its words leave the
    folder.
    Oracle: notes-a.md graded always by the clause-form label, and the
    line itself under ## Unfiled for the agent to place.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-a.md').write_text('# Notes\n\nfacts\n')
    _handoff(folder, key_files=(
        'Read now, under `.handoff/demo/` unless noted:\n\n'
        '- `notes-a.md` the facts\n'))
    assert hq.main(['adopt', _SLUG]) == 0
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-a.md']['read_before'] == 'always'
    assert ('- unfiled: Read now, under `.handoff/demo/` unless noted:'
            in (folder / 'HANDOFF.md').read_text())


def test_adopt_conservation_reads_through_bold_and_the_moved_log(
        tmp_path, monkeypatch, capsys):
    """A first-sentence headline and a moved Log line both count as carried.

    Mutation: conservation comparing with the bold markers left in, so an
    item adoption bolded reads as changed; or the legacy Log lines left out
    of the union, so every Log line reads as lost although the manifest
    row carries it.
    Oracle: adopt prints `conservation: every original line carried` for
    an original holding a plain-sentence dead end and a two-line Log.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, extra=(
        '\n## Dead ends\n\n'
        'Every closed dead end is in the notes file. Those that bear on the\n'
        'plan are repeated here.\n'
        '\n## Log\n\n'
        '- 2026-08-01 (cycle 1): the taxonomy and the baseline;\n'
        '  the first measurement.\n'))
    assert hq.main(['adopt', _SLUG]) == 0
    out = capsys.readouterr().out
    assert 'conservation: every original line carried' in out, out


def test_adopt_splits_a_line_range_off_a_pointer_path(tmp_path, monkeypatch):
    r"""`- \`docs/guide.md:3-4\` - the field guide` keys on the file, not the range.

    Mutation: the pointer token stored whole, so `docs/guide.md:3-4` is a
    path that never exists and the row is seeded missing with the file's
    own text unread.
    Oracle: the row's path ends in docs/guide.md, is live, and its label
    leads with the range.
    """
    folder = _root(tmp_path, monkeypatch)
    root = folder.parent.parent
    (root / 'docs').mkdir()
    (root / 'docs' / 'guide.md').write_text('# Guide\n\none\ntwo\n')
    _handoff(folder, key_files='- `docs/guide.md:3-4` - the field guide\n')
    assert hq.main(['adopt', _SLUG]) == 0
    guide = [r for r in _ledger(folder) if r['path'].endswith('docs/guide.md')]
    assert len(guide) == 1
    assert guide[0]['status'] == 'live'
    assert guide[0]['label'] == 'lines 3-4; the field guide'
