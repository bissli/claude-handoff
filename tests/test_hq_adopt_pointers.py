"""Adoption of Key files pointers: merged rows, comma lists, loose
bullets, section-letter seeds, the Log roll-up, and the top-level
HANDOFF rule.
"""

import os
import pathlib
import shutil

import pytest

from bin import hq

FIXTURES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'handoff')
_SLUG = 'ptr-test'
_SESSION = 'session-ptr'
_NOW = '2026-09-01T12:00:00'

_CURSOR = (
    '## Task\n\nDo the work.\n\n'
    '## Now\n\nNext step.\n\n'
    '## Plan\n\nPlan line.\n\n'
    '## State\n\nState line.\n\n'
    '## Environment\n\nEnv line.\n\n'
    '## Open questions\n\nNone.\n'
)


def _root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, slug: str = _SLUG,
) -> pathlib.Path:
    """Create HQ_ROOT and set required env vars.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _handoff(
    folder: pathlib.Path, key_files: str = '', cycle: int = 3, extra: str = '',
) -> str:
    """Write a conforming HANDOFF.md with an optional Key files section.
    """
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


def _ledger(folder: pathlib.Path) -> list[dict]:
    """Return ledger rows as a list of dicts.
    """
    return hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)


def _manifest(folder: pathlib.Path) -> list[dict]:
    """Return manifest rows as a list of dicts.
    """
    return hq._read_tsv(
        folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)


# --- Two pointers to one path ---


def test_two_pointers_to_same_path_merge_label_where_and_rb(
        tmp_path, monkeypatch):
    """Two Key files bullets to one path merge label, where, and read_before.

    Mutation: kf_map[stored] overwritten by second pointer, so first label
    text and first where anchor are silently dropped.
    Oracle: single DESIGN.md ledger row whose label contains both pointer
    texts joined with '; ' in order, where='s2;s3', read_before='always';
    the Read first block in HANDOFF.md contains two comma-separated spans.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'DESIGN.md').write_text(
        '# Design\n\n## 2 Overview\n\ncontent\n\n## 3 Detail\n\ncontent\n')
    _handoff(folder, key_files=(
        'Read now:\n'
        '- `DESIGN.md` section 2 overview\n'
        '- `DESIGN.md` section 3 detail\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert 'DESIGN.md' in rows
    r = rows['DESIGN.md']
    assert 'section 2 overview' in r['label']
    assert 'section 3 detail' in r['label']
    assert r['where'] == 's2;s3'
    assert r['read_before'] == 'always'
    hf_text = (folder / 'HANDOFF.md').read_text()
    # Both spans appear in the Read first block, comma separates them.
    assert 'DESIGN.md:' in hf_text
    assert ',' in hf_text.split('DESIGN.md:')[1].split('\n')[0]


def test_two_pointers_different_rb_grade_merges_to_always(
        tmp_path, monkeypatch):
    """A Read now and a bare pointer to the same path both yield always.

    Mutation: rb_over merge drops the always override, falling back to the
    grade of whichever pointer is stored last.
    Oracle: notes-x.md read_before='always' and label contains both texts.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-x.md').write_text('# X\n')
    _handoff(folder, key_files=(
        'Read now:\n'
        '- `notes-x.md` first pointer\n'
        '- `notes-x.md` second pointer\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-x.md']['read_before'] == 'always'
    assert 'first pointer' in rows['notes-x.md']['label']
    assert 'second pointer' in rows['notes-x.md']['label']


# --- Several paths on one bullet ---


def test_multi_path_bullet_seeds_one_row_per_path(tmp_path, monkeypatch):
    """A comma-separated list of paths on one bullet seeds a row per path.

    Mutation: only the first path token stored, so subsequent paths
    produce no ledger row and the first path's label begins with a comma.
    Oracle: three rows each with label 'shipped in v0.2.0'; first path's
    label does not start with a comma or backtick.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'hq.py').write_text('# hq\n')
    (folder / 'gate.py').write_text('# gate\n')
    (folder / 'cfg.json').write_text('{}')
    _handoff(folder, key_files=(
        '- `hq.py`, `gate.py`, `cfg.json` - shipped in v0.2.0\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    for path in ('hq.py', 'gate.py', 'cfg.json'):
        assert path in rows, f'expected row for {path}'
        assert rows[path]['label'] == 'shipped in v0.2.0', (
            f'{path} label: {rows[path]["label"]!r}')
    assert not rows['hq.py']['label'].startswith(',')
    assert not rows['hq.py']['label'].startswith('`')


def test_multi_range_pointer_seeds_the_bare_path_with_the_ranges_in_its_label(
        tmp_path, monkeypatch, capsys):
    """A multi-range pointer seeds the bare path with the ranges in its label.

    Mutation: the range group accepting one segment only, so the comma
    token resolves nowhere and is seeded missing/never under a junk path;
    or the bare form failing the pointer test, so the bullet lands in
    Unfiled and the path is never seeded.
    Oracle: the file on disk at the fixture path - one live always row
    keyed on the bare name, the ranges in its label, and no not-on-disk
    line.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'loader.py').write_text('# loader\n')
    (folder / 'saver.py').write_text('# saver\n')
    _handoff(folder, key_files=(
        'Read now:\n'
        '- `loader.py:10-25,40` the loader\n'
        '- saver.py:12-30,44-46 the saver\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    out = capsys.readouterr().out
    rows = {r['path']: r for r in _ledger(folder)}
    assert 'loader.py:10-25,40' not in rows
    assert (rows['loader.py']['read_before'], rows['loader.py']['status']) == (
        'always', 'live')
    assert rows['loader.py']['label'] == 'lines 10-25,40; the loader'
    assert rows['saver.py']['label'] == 'lines 12-30,44-46; the saver'
    assert 'Key files pointer not on disk' not in out
    assert '- unfiled:' not in (folder / 'HANDOFF.md').read_text()


def test_paths_joined_by_and_each_take_a_row_sharing_the_text(
        tmp_path, monkeypatch):
    """Two paths joined by `and` on one bullet seed two rows with one label.

    Mutation: the list continued on a comma alone, so the second path
    takes no row and stays inside the first path's label as
    'and `stack-b.yaml` - both stacks'; or the separator made optional,
    so the prose bullet splits at 'and parser'.
    Oracle: hand-computed - two paths under one 'Read now:' bullet, so
    two always rows sharing the bullet text; the prose bullet keeps one
    row and its whole label; the ', and' spelling seeds the same rows.
    """
    folder = _root(tmp_path, monkeypatch)
    for name in ('stack-a.yaml', 'stack-b.yaml', 'notes-x.md', 'one.md', 'two.md'):
        (folder / name).write_text('x\n')
    _handoff(folder, key_files=(
        'Read now:\n'
        '- `stack-a.yaml` and `stack-b.yaml` - both stacks\n'
        '- `notes-x.md` - the loader and parser notes\n'
        '- `one.md`, and `two.md` the pair\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert [rows[p]['read_before'] for p in ('stack-a.yaml', 'stack-b.yaml')] == [
        'always', 'always']
    assert [rows[p]['label'] for p in ('stack-a.yaml', 'stack-b.yaml')] == [
        'both stacks', 'both stacks']
    assert rows['notes-x.md']['label'] == 'the loader and parser notes'
    assert [rows[p]['label'] for p in ('one.md', 'two.md')] == ['the pair', 'the pair']


def test_a_continuation_line_keeps_a_leading_dash_used_as_punctuation(
        tmp_path, monkeypatch):
    """A wrapped line opening with `- ` keeps its dash in label and Unfiled.

    Mutation: the continuation stripped of a leading list marker, so a
    dash used as punctuation is deleted and the two clauses weld into a
    sentence the author never wrote - 'is deliberate it holds' - in the
    ledger label and in the loose bullet alike.
    Oracle: hand-computed - the bullet's free text, one space, and the
    continuation line exactly as written, for a pointer and for a loose
    bullet.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'module-map.md').write_text('# Map\n')
    _handoff(folder, key_files=(
        '- `module-map.md` - the rotation path is deliberate\n'
        '  - it holds the client secret\n'
        '- Earlier material is parked\n'
        '  - never read it first\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['module-map.md']['label'] == (
        'the rotation path is deliberate - it holds the client secret')
    text = (folder / 'HANDOFF.md').read_text()
    assert '- unfiled: - Earlier material is parked - never read it first' in text


def test_an_unindented_paragraph_under_key_files_or_the_header_is_one_bullet(
        tmp_path, monkeypatch, capsys):
    """A run of unindented plain lines joins into one Unfiled bullet.

    Mutation: each unindented non-blank line flushing the open loose item
    and opening a new one, so a wrapped paragraph becomes one untyped
    bullet per physical line under Key files and in the preamble alike;
    or the blank-line close dropped, so two paragraphs merge; or a bullet
    line joined onto the paragraph above it, so two loose bullets become
    one.
    Oracle: hand-counted bullets - one for the three-line Key files
    paragraph, two for the two preamble paragraphs, one each for two
    consecutive loose bullets; the pointer below the paragraph still
    reads always, and conservation reports every line carried.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-cfg.md').write_text('# Cfg\n')
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\n'
        'Written: 2026-09-01 | Cycle: 3\n\n'
        'First paragraph opens here\nand wraps onto a second line.\n\n'
        'Second paragraph stands alone.\n\n'
        + _CURSOR
        + '\n## Key files\n\n'
        'This context note spans\nthree physical lines without\n'
        'any indentation below.\n\n'
        'Read now:\n\n'
        '- `notes-cfg.md` the config notes\n'
        '- Parked material one\n'
        '- Parked material two\n', encoding='utf-8')

    assert hq.main(['adopt', _SLUG]) == 0

    text = (folder / 'HANDOFF.md').read_text()
    assert [ln for ln in text.splitlines() if ln.startswith('- unfiled: ')] == [
        '- unfiled: First paragraph opens here and wraps onto a second line.',
        '- unfiled: Second paragraph stands alone.',
        ('- unfiled: This context note spans three physical lines without'
         ' any indentation below.'),
        '- unfiled: - Parked material one',
        '- unfiled: - Parked material two',
    ]
    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-cfg.md']['read_before'] == 'always'
    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_a_label_wrapped_over_two_lines_grades_the_bullets_below_it(
        tmp_path, monkeypatch, capsys):
    """A `Read now, <clause>` label wrapped onto a second line still grades.

    Mutation: the loose item filed without re-testing the label pattern
    on the joined text, so a wrapped label grades nothing and the pointer
    under it keeps the grade above; or the joined label dropped from
    Unfiled, so its clause leaves the file.
    Oracle: hand-computed - 'Read now' grades always, so the one pointer
    under it is always while the one above stays edit; the two physical
    lines are one Unfiled bullet holding both halves; conservation
    reports every line carried.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-reg.md').write_text('# Reg\n')
    (folder / 'notes-old.md').write_text('# Old\n')
    _handoff(folder, key_files=(
        'Reference only:\n\n- `notes-old.md` the old notes\n\n'
        'Read now, because the register template is the open work,\n'
        'and the billing item follows it:\n\n'
        '- `notes-reg.md` the register notes\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-reg.md']['read_before'] == 'always'
    assert rows['notes-old.md']['read_before'] == 'edit'
    text = (folder / 'HANDOFF.md').read_text()
    assert [ln for ln in text.splitlines() if ln.startswith('- unfiled: ')] == [
        ('- unfiled: Read now, because the register template is the open work,'
         ' and the billing item follows it:')]
    assert 'conservation: every original line carried' in capsys.readouterr().out


def test_a_colon_line_that_is_no_label_opens_an_ungraded_mention_group(
        tmp_path, monkeypatch):
    """A `Read before touching the gateway:` line ends the group above it.

    Mutation: the unrecognized colon line leaving the grade above in
    force, so the pointer under it is gated always across a boundary the
    author drew; or the ungraded group seeding never, so its labels fold
    into a count and leave the Artifacts block; or the mention grade
    applied to a spec, which R1 then refuses.
    Oracle: hand-computed - only Read now grades always; the notes
    pointer under the unrecognized header reads mention with its label
    kept; the spec under it stays always with no refusal; the header
    line lands under Unfiled; prose that ends no group leaves the grade
    in force.
    """
    folder = _root(tmp_path, monkeypatch)
    for name in ('notes-a.md', 'notes-b.md', 'notes-gate.md', 'SPEC-gate.md'):
        (folder / name).write_text('# x\n')
    _handoff(folder, key_files=(
        'Read now:\n\n- `notes-a.md` the a notes\n\n'
        'Some prose that ends no group.\n\n- `notes-b.md` the b notes\n\n'
        'Read before touching the gateway:\n\n'
        '- `notes-gate.md` the gateway notes\n'
        '- `SPEC-gate.md` the gateway spec\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert [rows[p]['read_before'] for p in ('notes-a.md', 'notes-b.md')] == [
        'always', 'always']
    assert rows['notes-gate.md']['read_before'] == 'mention'
    assert rows['notes-gate.md']['label'] == 'the gateway notes'
    assert (rows['SPEC-gate.md']['read_before'], rows['SPEC-gate.md']['reason']) == (
        'always', '-')
    text = (folder / 'HANDOFF.md').read_text()
    assert '- unfiled: Read before touching the gateway:' in text


def test_reference_only_grades_a_non_notes_row_mention_so_its_label_shows(
        tmp_path, monkeypatch):
    """Under `Reference only:` a notes file reads edit, any other file mention.

    Mutation: the mention fallback dropped, so a .yaml or a nested .py
    stays never and its label folds into the Artifacts count unseen; or
    the fallback applied without the never guard, so the spec is demoted
    to mention and R1 refuses it with an advisory.
    Oracle: hand-computed from the kind table - notes to edit, other to
    mention with its label kept, spec to always with no refusal, and a
    directory with no pointer stays never.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n')
    (folder / 'notes-bg.md').write_text('# Bg\n')
    (folder / 'stack.yaml').write_text('a: 1\n')
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'helper.py').write_text('x = 1\n')
    (folder / 'probes').mkdir()
    _handoff(folder, key_files=(
        'Reference only:\n'
        '- `SPEC.md` the spec\n'
        '- `notes-bg.md` background notes\n'
        '- `stack.yaml` the stack config\n'
        '- `sub/helper.py` the nested helper\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-bg.md']['read_before'] == 'edit'
    assert (rows['stack.yaml']['read_before'], rows['stack.yaml']['label']) == (
        'mention', 'the stack config')
    assert rows['sub/helper.py']['read_before'] == 'mention'
    assert (rows['SPEC.md']['read_before'], rows['SPEC.md']['reason']) == (
        'always', '-')
    assert rows['probes']['read_before'] == 'never'


def test_an_ungraded_header_that_mentions_a_label_word_still_grades_mention(
        tmp_path, monkeypatch):
    """A colon line saying 'read now' mid-sentence grades mention, not always.

    Mutation: the grade test a substring match, so the header text an
    ungraded group carries as its grade matches 'read now' or 'reference
    only' inside it and gates or edit-grades the pointers below.
    Oracle: hand-computed - only an exact label grades always or edit;
    the two pointers under the two mid-sentence headers read mention.
    """
    folder = _root(tmp_path, monkeypatch)
    for name in ('alpha.md', 'stack.yaml', 'notes-beta.md'):
        (folder / name).write_text('x\n')
    _handoff(folder, key_files=(
        'Read now:\n\n- `alpha.md` the loader\n\n'
        'Files to read now, before touching the gateway:\n\n'
        '- `stack.yaml` the config\n\n'
        'Kept here for reference only:\n\n'
        '- `notes-beta.md` the notes\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['alpha.md']['read_before'] == 'always'
    assert rows['stack.yaml']['read_before'] == 'mention'
    assert rows['notes-beta.md']['read_before'] == 'mention'


def test_a_bare_multi_range_pointer_after_a_separator_keeps_its_ranges(
        tmp_path, monkeypatch):
    """A bare `mod.py:12-40,57` reached through a list keeps its whole range.

    Mutation: the list's bare token cut at the first comma, so the second
    range leaks into the shared label as '57 the ranges' and the first
    path's label opens with it.
    Oracle: hand-computed - the same labels the backticked spelling gives:
    'lines 12-40,57; the ranges' on the ranged path, 'the ranges' on the
    other.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'zeta.md').write_text('x\n')
    (folder / 'mod.py').write_text('x = 1\n')
    _handoff(folder, key_files=(
        'Read now:\n- `zeta.md`, mod.py:12-40,57 the ranges\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['mod.py']['label'] == 'lines 12-40,57; the ranges'
    assert rows['zeta.md']['label'] == 'the ranges'


def test_two_bullets_to_one_path_under_an_ungraded_group_merge_to_mention(
        tmp_path, monkeypatch):
    """Two bullets to one path under an ungraded group keep the mention grade.

    Mutation: the merge carrying always and edit only, so two mention
    grades collapse to none and the merged row reads never with its label
    folded into a count.
    Oracle: hand-computed - the once-named path and the twice-named path
    both read mention, the latter with the two labels joined.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'zeta.md').write_text('x\n')
    (folder / 'stack.yaml').write_text('a: 1\n')
    _handoff(folder, key_files=(
        'Gateway notes:\n\n- `zeta.md` the loader\n- `zeta.md` also the cache\n'
        '- `stack.yaml` the config\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['stack.yaml']['read_before'] == 'mention'
    assert (rows['zeta.md']['read_before'], rows['zeta.md']['label']) == (
        'mention', 'the loader; also the cache')


# --- The conservation witness ---


def test_witnessed_pairs_filters_by_written_label():
    """witnessed_pairs keeps records whose label_part is in written_labels.

    Mutation: filter removed, so overwritten-pointer records count as
    carried and mask a true conservation miss.
    Oracle: three records - '-' part always kept, matching part kept,
    non-matching part excluded; result preserves insertion order.
    """
    records = [
        ('a.md', 'raw a', '-'),
        ('b.md', 'raw b', 'the detail'),
        ('c.md', 'raw c', 'missing part'),
        ]
    written_labels = {
        'a.md': 'anything goes here',
        'b.md': 'the detail and more text',
        'c.md': 'entirely different text',
        }
    result = hq.witnessed_pairs(records, written_labels)
    assert 'raw a' in result
    assert 'raw b' in result
    assert 'raw c' not in result
    assert result.index('raw a') < result.index('raw b')


def test_witnessed_pairs_absent_path_excluded_unless_dash():
    """A record whose stored path is absent from written_labels is excluded.

    Mutation: missing-key lookup returns a default that always passes, so
    ghost witnesses pollute the union.
    Oracle: record for 'd.md' not in written_labels is excluded; '-' part
    for 'e.md' is kept regardless.
    """
    records = [
        ('d.md', 'raw d', 'some label'),
        ('e.md', 'raw e', '-'),
        ]
    written_labels: dict = {}
    result = hq.witnessed_pairs(records, written_labels)
    assert 'raw d' not in result
    assert 'raw e' in result


def test_multi_path_bullet_first_line_reported_carried(
        tmp_path, monkeypatch, capsys):
    """The multi-path bullet's original line is covered by the witness.

    Mutation: witnesses for multi-path paths carry only 'path free' (the
    individual token + free text), not the full original line, so the
    comma-separated line fails the conservation substring check.
    Oracle: capsys output contains 'every original line carried'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'hq.py').write_text('# hq\n')
    (folder / 'gate.py').write_text('# gate\n')
    _handoff(folder, key_files=(
        '- `hq.py`, `gate.py` - shared label\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    out = capsys.readouterr().out
    assert 'every original line carried' in out


# --- A loose bullet and its continuation lines ---


def test_loose_key_files_bullet_with_continuation_is_one_unfiled(
        tmp_path, monkeypatch):
    """A loose Key files bullet joined with indented lines is one unfiled.

    Mutation: indented lines after a loose bullet each open their own
    unfiled entry, producing three bullets instead of one.
    Oracle: exactly one unfiled bullet whose text contains all three parts;
    the continuation text does not appear as a standalone unfiled entry.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-a.md').write_text('# A\n')
    _handoff(folder, key_files=(
        '- `notes-a.md` the live sketch\n'
        '- Earlier cycles material: `evidence/old/`,\n'
        '  data from prior runs\n'
        '  and archived notes\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    hf_text = (folder / 'HANDOFF.md').read_text()
    assert '- unfiled: - Earlier cycles material:' in hf_text
    assert 'data from prior runs' in hf_text
    assert 'and archived notes' in hf_text
    standalone_continuation = [
        ln for ln in hf_text.splitlines()
        if ln.startswith('- unfiled:')
        and 'data from prior runs' in ln
        and 'Earlier cycles' not in ln
    ]
    assert not standalone_continuation, (
        'continuation text appeared as its own unfiled entry')


# --- The where seed and section letters ---


def test_where_seed_accepts_trailing_letter_on_section_number(
        tmp_path, monkeypatch):
    """'section 11b' seeds 's11b' and 'section 12, Migration' seeds 's12'.

    Mutation: regex without [a-z]? suffix on the number group truncates
    'section 11b' to 's11'.
    Oracle: ledger where field is 's11b;s12' for a pointer whose free text
    cites 'section 11b' and 'section 12, Migration'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'DESIGN.md').write_text(
        '# Design\n\n## 11b. Proof\n\nx\n\n## 12. Migration\n\ny\n')
    _handoff(folder, key_files=(
        '- `DESIGN.md` covers section 11b and section 12, Migration\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['DESIGN.md']['where'] == 's11b;s12'


def test_where_seed_keeps_a_dotted_section_number_whole(tmp_path, monkeypatch):
    """'section 24.4' seeds 's24.4', the sub-heading, not 's24', its parent.

    Mutation: the label scan's number group cut at the first dot, so the
    seed is 's24' and the read block points at the parent heading, or at
    nothing when no heading is numbered 24.
    Oracle: ledger where field is 's24.4;s7' for a pointer whose free
    text cites 'section 24.4' and 's7', against a file with both
    headings.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'DESIGN.md').write_text(
        '# Design\n\n## 7. Scope\n\nx\n\n## 24. Parent\n\n'
        '### 24.4 The decision\n\ny\n')
    _handoff(folder, key_files=(
        '- `DESIGN.md` covers section 24.4 and s7\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['DESIGN.md']['where'] == 's24.4;s7'


def test_where_seed_falls_back_to_the_parent_section_of_a_dotted_number(
        tmp_path, monkeypatch, capsys):
    """'section 3.2' seeds 's3' when the file numbers only '## 3.'.

    Mutation: the dotted anchor dropped outright when no heading carries
    it, so a pointer that used to read at its parent section loses its
    span and the read block falls back to the whole file.
    Oracle: ledger where field 's3' against a file with '## 3. Retry'
    and no '3.2' heading, no 'where dropped' line; the same label
    against a file that has '### 3.2 Backoff' seeds 's3.2'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'DESIGN.md').write_text(
        '# Design\n\n## 3. Retry\n\nbody\n\n## 4. Other\n\nx\n')
    (folder / 'notes-b.md').write_text(
        '# Notes\n\n## 3. Retry\n\n### 3.2 Backoff\n\nbody\n')
    _handoff(folder, key_files=(
        '- `DESIGN.md` the retry rule, see section 3.2\n'
        '- `notes-b.md` the backoff, see section 3.2\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['DESIGN.md']['where'] == 's3'
    assert rows['notes-b.md']['where'] == 's3.2'
    assert 'where dropped' not in capsys.readouterr().out


def test_where_seed_plain_number_unchanged(tmp_path, monkeypatch):
    """'section 5' still seeds 's5' when no trailing letter is present.

    Mutation: [a-z]? pattern breaks the digit-only case so 's5' is not
    matched or is corrupted.
    Oracle: ledger where field is 's5'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'DESIGN.md').write_text('# Design\n\n## 5. Details\n\nx\n')
    _handoff(folder, key_files='- `DESIGN.md` see section 5 for details\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['DESIGN.md']['where'] == 's5'


def test_where_seed_keeps_only_anchors_the_file_resolves(
        tmp_path, monkeypatch, capsys):
    """An `s<n>` word whose section is not in the pointed file is not seeded.

    Mutation: the mined anchors written unfiltered, so `s1` from prose
    naming another file's step seeds `where=s1`, the read block renders
    `?`, and `open` reports it every cycle; or the dropped anchor not
    printed, so the loss is silent at adopt time.
    Oracle: notes-cache.md has headings 2 and 4 only; the label cites
    `s1`, `s2`, and `section 4`, so the row's where is `s2;s4` and the
    summary prints `where dropped: notes-cache.md 's1'`.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-cache.md').write_text(
        '# Cache\n\n## 2. Layout\n\nx\n\n## 4. Eviction\n\ny\n')
    _handoff(folder, key_files=(
        '- `notes-cache.md` Read now: layout (s2), the S1 premise, and\n'
        '  eviction under section 4\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-cache.md']['where'] == 's2;s4'
    out = capsys.readouterr().out
    assert "  where dropped: notes-cache.md 's1'" in out


# --- Pointers not on disk ---


def test_begin_names_each_row_adopt_seeded_as_missing(
        tmp_path, monkeypatch, capsys):
    """Begin lists every ledger row stored as missing, with its move.

    Mutation: the work list reading status='live' rows alone, so a Key
    files pointer adopt seeded as missing is never named again and the
    agent meets it only as a count inside the Artifacts block; or the
    class never clearing, so an archived row is still listed.
    Oracle: hand-computed - two pointers off disk seed two missing rows,
    so begin prints two 'missing:' lines naming those paths; archiving
    one leaves one line at the next begin.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder, key_files=(
        'Read now:\n\n- `SPEC-widget.md` - the lane spec\n'
        '- `docs/ghost-notes.md` - background\n'))
    assert hq.main(['adopt', _SLUG]) == 0
    capsys.readouterr()

    assert hq.main(['begin', _SLUG]) == 0

    move = 'stamp --status live if it is back, or --successor / --archive --reason'
    lines = [ln for ln in capsys.readouterr().out.splitlines() if 'missing:' in ln]
    assert lines == [
        f'  missing: SPEC-widget.md - {move}',
        f'  missing: docs/ghost-notes.md - {move}',
    ]
    assert hq.main(['stamp', _SLUG, 'docs/ghost-notes.md', '--archive',
                    '--reason', 'never written']) == 0
    assert hq.main(['begin', _SLUG]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if 'missing:' in ln]
    assert lines == [f'  missing: SPEC-widget.md - {move}']


def test_missing_draft_pointer_stays_missing_never_and_finish_passes(
        tmp_path, monkeypatch, capsys):
    """A `.py` pointer not on disk is seeded `draft missing never`, not gated.

    Mutation: R1 run on the seeded row and its refusal branch rewriting
    the row to `live/always`, so the ledger holds a live gated row for a
    file that does not exist, the summary lists it under `gated (n)`, and
    the next finish stops on `missing live gated`.
    Oracle: the ledger row for smooth.py reads kind draft, status missing,
    read_before never, reason `-`; the summary has no `gated (` line; and
    `begin` then `finish` exit 0 with no `missing live gated` line.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'panels.json').write_text('{}\n')
    _handoff(folder, key_files='- panels.json, smooth.py - paper kit.\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    row = rows['smooth.py']
    assert (row['kind'], row['status'], row['read_before'], row['reason']) == (
        'draft', 'missing', 'never', '-')
    out = capsys.readouterr().out
    assert 'gated (' not in out
    assert 'Key files pointer not on disk: smooth.py' in out
    monkeypatch.setenv('HQ_CYCLE', '4')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['finish', _SLUG, '--log', 'kit']) == 0
    assert 'missing live gated' not in capsys.readouterr().out


def test_bare_continuation_token_resolves_beside_the_first_path(
        tmp_path, monkeypatch):
    """`- sub/a.py, b.py` reads b.py as sub/b.py when that file exists.

    Mutation: every continuation token resolved against the folder, the
    root, the cwd, and home alone, so a sibling listed by bare name is
    seeded as a missing top-level draft.
    Oracle: the ledger holds a live row for sub/b.py and no row for b.py.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'a.py').write_text('a = 1\n')
    (folder / 'sub' / 'b.py').write_text('b = 2\n')
    _handoff(folder, key_files='- sub/a.py, b.py - the pair\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert 'b.py' not in rows
    assert rows['sub/b.py']['status'] == 'live'
    assert rows['sub/b.py']['label'] == 'the pair'


def test_unreadable_pointed_file_leaves_adopt_whole_and_drops_the_anchor(
        tmp_path, monkeypatch, capsys):
    """A pointed file adopt cannot read costs its anchor, never the adoption.

    Mutation: the anchor filter's read_text unguarded, so a permission
    error aborts adopt after ledger.tsv and the archive are written and
    the folder can never be adopted again.
    Oracle: a stub raising PermissionError for notes-locked.md alone;
    adopt exits 0, the row is seeded with where `-`, and the summary
    prints `where dropped: notes-locked.md 's2'`.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-locked.md').write_text('# N\n\n## 2. A\n\nx\n')
    _handoff(folder, key_files='- `notes-locked.md` see s2\n')
    real_read_text = pathlib.Path.read_text

    def _locked(self, *args, **kwargs):
        if self.name == 'notes-locked.md':
            raise PermissionError(13, 'Permission denied', str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, 'read_text', _locked)

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-locked.md']['where'] == '-'
    assert "  where dropped: notes-locked.md 's2'" in capsys.readouterr().out


def test_an_anchor_cited_twice_on_one_bullet_is_seeded_once(
        tmp_path, monkeypatch):
    """`s2 ... s2` on one bullet seeds `where=s2`, as two bullets would.

    Mutation: the mined anchors passed through without de-duplication,
    so the row reads `s2;s2` and the read block renders the same span
    twice.
    Oracle: the ledger where field is exactly `s2` and the read block
    line carries one span.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-x.md').write_text('# N\n\n## 2. A\n\nx\n\n## 3. B\n\ny\n')
    _handoff(folder, key_files='- `notes-x.md` Read now: s2 covers it, and s2 again\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-x.md']['where'] == 's2'
    assert 'notes-x.md:3-6  ' in (folder / 'HANDOFF.md').read_text()


def test_a_label_less_pointer_to_a_walked_file_seeds_one_row(
        tmp_path, monkeypatch, capsys):
    r"""`- \`SPEC.md\`` with no text grades the walk row and adds no second row.

    Mutation: the walk marking a pointer as matched only when it carries
    a label, so a bare pointer seeds a duplicate row and the summary
    counts and lists the file twice.
    Oracle: one SPEC.md row, read_before always; the summary reads
    `seeded 2 entries` for SPEC.md and notes-a.md and `gated (1)`.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'SPEC.md').write_text('# Spec\n')
    (folder / 'notes-a.md').write_text('# A\n')
    _handoff(folder, key_files='Read now:\n- `SPEC.md`\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = _ledger(folder)
    assert [r['path'] for r in rows].count('SPEC.md') == 1
    assert {r['path']: r for r in rows}['SPEC.md']['read_before'] == 'always'
    out = capsys.readouterr().out
    assert f'hq adopt: seeded 2 entries in {_SLUG}' in out
    assert '  gated (1): SPEC.md' in out


# --- The adopt Log roll-up ---


def test_adopt_log_field_is_summary_not_full_lines(tmp_path, monkeypatch):
    """Adopt manifest log carries a line-count summary, not the line text.

    Mutation: adopt_log inlines all legacy log lines into the log field,
    so the manifest log field grows unbounded with prior text.
    Oracle: log == 'adopted; prior Log: 2 lines in cycles/c02.md'; note
    field carries the joined lines; HANDOFF.md Log section has the
    summary, not the prior lines verbatim.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    log_lines = [
        '- 2026-08-30 (cycle 1): cache layout drafted.',
        '- 2026-08-31 (cycle 2): generator wired to the layout.',
        ]
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\nWritten: 2026-08-31 | Cycle: 2\n\n'
        '## Task\nx\n\n## Now\ny\n\n## Log\n' + '\n'.join(log_lines) + '\n')

    assert hq.main(['adopt', _SLUG]) == 0

    manifest = _manifest(folder)
    last = manifest[-1]
    assert last['log'] == 'adopted; prior Log: 2 lines in cycles/c02.md'
    assert '2026-08-30' in last['note']
    assert '2026-08-31' in last['note']
    hf_text = (folder / 'HANDOFF.md').read_text()
    assert 'adopted; prior Log: 2 lines in cycles/c02.md' in hf_text
    assert 'cache layout drafted' not in hf_text


def test_adopt_note_joins_wrapped_log_items_before_separating_them(
        tmp_path, monkeypatch):
    """A wrapped Log bullet is one note item, and items are separated by ` | `.

    Mutation: every Log line joined with the separator, so a bullet
    wrapped mid-sentence splits into two items and a sha list wrapped
    after its own ` / ` reads ` / / `; or the separator left as ` / `,
    which the lines themselves carry.
    Oracle: hand-computed - two bullets, the first wrapped over three
    lines, give the note `- day one: shipped a / b / c; the readout count
    2^16 | - day two: x`, and the log field still counts the 4 lines the
    archive holds.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\nWritten: 2026-09-01 | Cycle: 1\n\n'
        '## Task\nt\n\n## Now\nn\n\n## Log\n'
        '- day one: shipped a / b /\n'
        '  c; the readout\n'
        '  count 2^16\n'
        '- day two: x\n')

    assert hq.main(['adopt', _SLUG]) == 0

    last = _manifest(folder)[-1]
    assert last['note'] == (
        f'adopted 2026-09-01 by {_SESSION}'
        ' | - day one: shipped a / b / c; the readout count 2^16 | - day two: x')
    assert last['log'] == 'adopted; prior Log: 4 lines in cycles/c01.md'


def test_adopt_log_field_is_bare_adopted_with_no_log_section(
        tmp_path, monkeypatch):
    """Adopt sets log='adopted' with no suffix when the file has no Log.

    Mutation: legacy_log condition missing, so the summary suffix is
    appended even when there are no legacy lines (e.g. '0 lines in ...');
    or the note joining an empty Log with a bar, leaving a trailing ` | `.
    Oracle: manifest log == 'adopted' exactly; note is the adopt-time
    provenance alone, `adopted <date> by <session>`.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\nWritten: 2026-09-01 | Cycle: 1\n\n'
        '## Task\nx\n\n## Now\ny\n')

    assert hq.main(['adopt', _SLUG]) == 0

    manifest = _manifest(folder)
    last = manifest[-1]
    assert last['log'] == 'adopted'
    assert last['note'] == f'adopted 2026-09-01 by {_SESSION}'


def test_adopt_row_describes_the_archive_and_notes_its_own_provenance(
        tmp_path, monkeypatch):
    """The adopt row for cycle N carries cycles/cNN.md's own facts.

    Mutation: `written` taken from the adopt date rather than the
    archived `Written:`; `repos` from the adopt-time git state rather
    than the header's `branch @ sha`; `session` credited to the adopting
    session; `cursor_lines`, `payload_tokens`, or `handoff_sha` measured
    on the rewritten HANDOFF.md rather than the archive; `rewrite_sha`
    left blank, so the next begin archives the rewrite as a hand edit.
    Oracle: hand-computed from a header dated ten days before HQ_NOW on
    a branch git cannot report (HQ_GIT=0): written `2026-08-22`, repos
    `trunk@9abc123`, session `-`, 12 cursor lines (three sections of
    four lines each, the blank before `## Log` included) and
    len(archive) // 4 tokens counted on cycles/c04.md, handoff_sha the
    digest of that file
    and rewrite_sha the digest of HANDOFF.md, the two unequal; the note
    leads with `adopted 2026-09-01 by <session>` and the rendered Log
    line dates the cycle `2026-08-22 (cycle 4, trunk@9abc123)`.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\n'
        'Written: 2026-08-22 | Cycle: 4 | trunk @ 9abc123 | clean\n\n'
        '## Task\n\nDo the work.\n\n'
        '## Now\n\nNext step.\n\n'
        '## State\n\nState line.\n\n'
        '## Log\n- 2026-08-22: started\n', encoding='utf-8')

    assert hq.main(['adopt', _SLUG]) == 0

    archive = folder / 'cycles' / 'c04.md'
    handoff = folder / 'HANDOFF.md'
    row = _manifest(folder)[-1]
    assert row['cycle'] == '4'
    assert row['written'] == '2026-08-22'
    assert row['repos'] == 'trunk@9abc123'
    assert row['session'] == '-'
    assert row['cursor_lines'] == '12'
    assert row['payload_tokens'] == str(len(archive.read_text(encoding='utf-8')) // 4)
    assert row['handoff_sha'] == hq._sha12_path(archive)
    assert row['rewrite_sha'] == hq._sha12_path(handoff)
    assert row['handoff_sha'] != row['rewrite_sha']
    assert row['note'] == f'adopted 2026-09-01 by {_SESSION} | - 2026-08-22: started'
    log_body = handoff.read_text(encoding='utf-8').split('## Log\n', 1)[1]
    assert log_body.strip() == (
        '- 2026-08-22 (cycle 4, trunk@9abc123): adopted; prior Log: 1 lines'
        ' in cycles/c04.md')


def test_adopt_row_written_is_the_date_alone_or_the_adopt_date(
        tmp_path, monkeypatch):
    """`written` on the adopt row is the header's YYYY-MM-DD, else the adopt date.

    Mutation: taking the first token after `Written:` whole, so a
    timestamped header persists `2026-08-31T09:15:00` and an empty one
    persists `|`, each rendered into every later Log line; or the
    fallback dropped, so an empty date crashes adopt.
    Oracle: hand-computed - `Written: 2026-08-31T09:15:00 | Cycle: 2`
    gives `2026-08-31`; `Written: | Cycle: 2`, which the header pattern
    accepts, gives HQ_NOW's date `2026-09-01`.
    """
    stamped = _root(tmp_path, monkeypatch, slug='ptr-stamped')
    (stamped / 'HANDOFF.md').write_text(
        f'# Handoff: {stamped.name}\n\n'
        'Written: 2026-08-31T09:15:00 | Cycle: 2\n\n## Task\n\nx\n',
        encoding='utf-8')
    undated = _root(tmp_path, monkeypatch, slug='ptr-undated')
    (undated / 'HANDOFF.md').write_text(
        f'# Handoff: {undated.name}\n\nWritten: | Cycle: 2\n\n## Task\n\nx\n',
        encoding='utf-8')

    assert hq.main(['adopt', 'ptr-stamped']) == 0
    assert hq.main(['adopt', 'ptr-undated']) == 0

    assert _manifest(stamped)[-1]['written'] == '2026-08-31'
    assert _manifest(undated)[-1]['written'] == '2026-09-01'


def test_adopt_note_names_the_adopting_git_state_and_the_row_keeps_the_archive(
        tmp_path, monkeypatch):
    """Under git the note ends ` at <branch>@<sha>` while repos stays the archive's.

    Mutation: the ` at` clause dropped, so a real adopt records no sha of
    its own; or repos taken from the adopt-time git state rather than
    the archived header.
    Oracle: a stub _git_state answering ('trunk', 'abc1234', []) against
    a header naming `old @ 1111111`: note `adopted 2026-09-01 by
    <session> at trunk@abc1234`, repos `old@1111111`.
    """
    folder = _root(tmp_path, monkeypatch)
    monkeypatch.setenv('HQ_GIT', '1')
    monkeypatch.setattr(hq, '_git_state', lambda root: ('trunk', 'abc1234', []))
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {folder.name}\n\n'
        'Written: 2026-08-22 | Cycle: 3 | old @ 1111111 | clean\n\n## Task\n\nx\n',
        encoding='utf-8')

    assert hq.main(['adopt', _SLUG]) == 0

    row = _manifest(folder)[-1]
    assert row['note'] == f'adopted 2026-09-01 by {_SESSION} at trunk@abc1234'
    assert row['repos'] == 'old@1111111'


def test_adopt_row_describes_an_archive_already_on_disk(tmp_path, monkeypatch):
    """A cycles/cNN.md already present is kept, and the row describes it.

    Mutation: the row measured on the text adopt read rather than the
    archive read back, so written, repos, and handoff_sha describe
    HANDOFF.md while cycles/c03.md holds another file.
    Oracle: a pre-created cycles/c03.md headed `Written: 2026-01-05 |
    Cycle: 3 | old @ 1111111`: row written `2026-01-05`, repos
    `old@1111111`, handoff_sha its digest and not HANDOFF.md's.
    """
    folder = _root(tmp_path, monkeypatch)
    _handoff(folder)
    (folder / 'cycles').mkdir()
    archive = folder / 'cycles' / 'c03.md'
    archive.write_text(
        f'# Handoff: {folder.name}\n\n'
        'Written: 2026-01-05 | Cycle: 3 | old @ 1111111 | clean\n\n## Task\n\nold\n',
        encoding='utf-8')
    archive_sha = hq._sha12_path(archive)

    assert hq.main(['adopt', _SLUG]) == 0

    row = _manifest(folder)[-1]
    assert row['written'] == '2026-01-05'
    assert row['repos'] == 'old@1111111'
    assert row['handoff_sha'] == archive_sha
    assert row['handoff_sha'] != hq._sha12_path(folder / 'HANDOFF.md')


# --- HANDOFF*.md at the top level only ---


def test_infer_kind_handoff_md_at_top_level_is_snapshot():
    """HANDOFF.md at top_level=True is classified snapshot (preserved behavior).

    Mutation: HANDOFF*.md check removed from _SNAPSHOT_PATS without a
    top_level-gated replacement, so top-level HANDOFF.md is no longer snapshot.
    Oracle: infer_kind('HANDOFF.md', False, '', top_level=True) ==
    ('snapshot', 'never').
    """
    assert hq.infer_kind('HANDOFF.md', False, '', top_level=True) == (
        'snapshot', 'never')


def test_infer_kind_handoff_md_nested_is_other():
    """HANDOFF.md at top_level=False is 'other', not snapshot.

    Mutation: HANDOFF*.md check fires regardless of top_level, so an
    outside HANDOFF.md pointer is seeded kind 'snapshot' instead of 'other'.
    Oracle: infer_kind('HANDOFF.md', False, '# Handoff: x', top_level=False)
    == ('other', 'never').
    """
    assert hq.infer_kind(
        'HANDOFF.md', False, '# Handoff: x', top_level=False) == (
        'other', 'never')


def test_other_snapshot_patterns_are_level_independent():
    """Patterns *.pre-*, *.bak, etc. yield snapshot at any top_level value.

    Mutation: all snapshot patterns gated on top_level, so nested *.pre-*
    and *.bak files no longer classify as snapshot.
    Oracle: notes.bak and SPEC.prev.md return ('snapshot', 'never') at
    top_level=False.
    """
    assert hq.infer_kind('notes.bak', False, '', top_level=False) == (
        'snapshot', 'never')
    assert hq.infer_kind('SPEC.prev.md', False, '', top_level=False) == (
        'snapshot', 'never')


def test_adopt_outside_handoff_pointer_seeds_kind_other(
        tmp_path, monkeypatch):
    """A Read now pointer to an outside HANDOFF.md seeds kind 'other'.

    Mutation: HANDOFF*.md check fires for any path, so the outside handoff
    is seeded kind 'snapshot' instead of 'other'.
    Oracle: ledger row for ~/OTHER/HANDOFF.md has kind='other',
    read_before='always'.
    """
    folder = _root(tmp_path, monkeypatch)
    home = pathlib.Path(tmp_path) / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    other_dir = home / 'OTHER'
    other_dir.mkdir()
    other_hf = other_dir / 'HANDOFF.md'
    other_hf.write_text(
        '# Handoff: other\n\nWritten: 2026-09-01 | Cycle: 1\n')
    _handoff(folder,
             key_files='Read now:\n- `~/OTHER/HANDOFF.md` ref handoff\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    r = rows.get('~/OTHER/HANDOFF.md')
    assert r is not None
    assert r['kind'] == 'other'
    assert r['read_before'] == 'always'


def test_multi_path_bullet_wrapped_over_lines_shares_the_whole_text(
        tmp_path, monkeypatch, capsys):
    """A comma list that wraps onto indented lines still seeds every path.

    Mutation: the comma list split on the first physical line before its
    continuation lines join, so the shared text is that line's tail (a
    bare comma) and the wrapped paths land under Unfiled.
    Oracle: five rows carrying the hand-written shared label, no Unfiled
    section, and 'every original line carried' printed.
    """
    folder = _root(tmp_path, monkeypatch)
    for name in ('a.py', 'b.py', 'c.py', 'd.json', 'e.md'):
        (folder / name).write_text('x\n')
    _handoff(folder, key_files=(
        '- `a.py`, `b.py`, `c.py`,\n'
        '  `d.json`, `e.md` - shipped in v0.2.0; the\n'
        '  installed copies live under\n'
        '  `~/x/`.\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    expected = 'shipped in v0.2.0; the installed copies live under `~/x/`.'
    for name in ('a.py', 'b.py', 'c.py', 'd.json', 'e.md'):
        assert rows[name]['label'] == expected, name
    hf_text = (folder / 'HANDOFF.md').read_text()
    assert '## Unfiled' not in hf_text
    assert 'every original line carried' in capsys.readouterr().out


def test_separator_pointer_is_reported_carried(tmp_path, monkeypatch, capsys):
    """A pointer written as path, ` - `, text passes conservation.

    Mutation: the witness text built after the separator strip, so the
    original line with its ` - ` is a substring of nothing in the union.
    Oracle: 'every original line carried' printed and the label is the
    text alone.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-a.md').write_text('# A\n')
    _handoff(folder, key_files='- `notes-a.md` - the sketch, kept live\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-a.md']['label'] == 'the sketch, kept live'
    assert 'every original line carried' in capsys.readouterr().out


def test_merged_labels_join_after_a_sentence_with_a_space(
        tmp_path, monkeypatch):
    """Two labels for one path join with a space when the first ends a sentence.

    Mutation: the join fixed to '; ', producing 'first clause.; second'.
    Oracle: the hand-written merged label 'first clause. second clause'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes-x.md').write_text('# X\n')
    _handoff(folder, key_files=(
        '- `notes-x.md` first clause.\n'
        '- `notes-x.md` second clause\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    assert rows['notes-x.md']['label'] == 'first clause. second clause'


def test_bare_first_token_with_glued_comma_opens_the_list(
        tmp_path, monkeypatch):
    """A bare first path with a glued comma still seeds every later path.

    Mutation: the glued comma cut from the first token without being
    handed back to the list walk, so the second path never opens and its
    text sinks into the first path's label.
    Oracle: two rows, both read_before always under Read now, both with
    the hand-written label; the second path is not inside the first's
    label.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'a.md').write_text('a\n')
    (folder / 'b.md').write_text('b\n')
    _handoff(folder, key_files='Read now:\n- a.md, b.md - bare tokens\n')

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    for name in ('a.md', 'b.md'):
        assert rows[name]['label'] == 'bare tokens', name
        assert rows[name]['read_before'] == 'always', name


def test_list_ending_in_a_word_drops_the_leading_comma(tmp_path, monkeypatch):
    """A comma list closed by a word keeps that word, not the comma before it.

    Mutation: the separator strip applied only to ` - `, so a walk that
    halts on a word leaves a label that opens with a comma.
    Oracle: both rows carry the hand-written label 'and their tests - the
    shared text'.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'a.md').write_text('a\n')
    (folder / 'b.md').write_text('b\n')
    _handoff(folder, key_files=(
        '- `a.md`, `b.md`, and their tests - the shared text\n'))

    assert hq.main(['adopt', _SLUG]) == 0

    rows = {r['path']: r for r in _ledger(folder)}
    for name in ('a.md', 'b.md'):
        assert rows[name]['label'] == 'and their tests - the shared text', name


def test_a_pointer_the_ledger_absorbed_is_not_a_dropped_line(
        tmp_path, monkeypatch, capsys):
    """The first finish after an adopt passes on the absorbed pointers.

    Mutation: the witness reduced to the bare ledger label, so the
    path each Key files pointer carries in front of its text finds no
    home and the first finish after every adoption refuses.
    Oracle: the orbit fixture's two pointer rows, whose labels are
    'cache schema and field contracts; s4 field-to-path mapping' and
    'background on incremental diffing strategies'; only the
    path-and-label witness carries the normalized line, which keeps the
    path in front.
    """
    folder = _root(tmp_path, monkeypatch, slug='orbit-cache-rewrite')
    shutil.rmtree(folder)
    shutil.copytree(os.path.join(FIXTURES, 'orbit-cache-rewrite'), folder)
    assert hq.main(['adopt', 'orbit-cache-rewrite']) == 0
    assert hq.main(['begin', 'orbit-cache-rewrite']) == 0
    capsys.readouterr()
    rc = hq.main(['finish', 'orbit-cache-rewrite', '--log', 'after adopt'])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert 'not carried' not in out
