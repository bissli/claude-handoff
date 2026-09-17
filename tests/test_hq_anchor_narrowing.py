"""Narrowing an anchor set on a live spec row, end to end.

A spec row gated at ``always`` is read in full at every resume, so its
``--where`` is the only control over what a resume costs. Trimming a
completed section out of that set is the one lever R1 leaves open. These
tests drive the trim through a real cycle and pin what it moves: the
stored row, the rendered read block, what ``hq read`` prints, what ``hq
open`` reports, and what R3 still catches.

Notes
-----
- The fixture mirrors the shape the trim is aimed at: numbered sections
  of unequal weight, one of them carrying sub-headings a narrower anchor
  can keep on their own.
"""

import contextlib
import io
import pathlib

from bin import hq

_SLUG = 'anchor-slug'
_SESSION = 'session-narrow'

_BODY = 'x' * 39


def _section(heading, body_lines):
    """Return one heading and its body, every body line 39 characters.

    Parameters
    ----------
    heading : str
        Heading line, written without its trailing newline.
    body_lines : int
        Number of identical body lines to place under the heading.

    Returns
    -------
    str
        The section text, newline-terminated.
    """
    return heading + '\n' + '\n'.join([_BODY] * body_lines) + '\n'


# Headings land at 1, 2, 5, 26, 29, 46, 49, 52 over 56 lines. Section 4
# swallows its two sub-headings, which a narrower anchor names directly.
_SPEC_TEXT = (
    '# SPEC: anchor fixture\n'
    + _section('## 1. Scope', 2)
    + _section('## 2. Framework facts', 20)
    + _section('## 3. Rulings', 2)
    + _section('## 4. Case catalog', 16)
    + _section('### Deferred', 2)
    + _section('### Not planned', 2)
    + _section('## 5. CI', 4)
    )

_WIDE = 's1;s2;s3;s4;s5'
_NARROW = 's1;s3;Deferred;Not planned;s5'


def _env(tmp_path, monkeypatch):
    """Create a project root holding one empty handoff folder.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary directory.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to set the hq environment for the run.

    Returns
    -------
    pathlib.Path
        The handoff folder, ready for ``hq begin``.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', '2026-09-15T12:00:00')
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    return folder


def _run(argv):
    """Drive hq.main over one argument list and return its exit and stdout.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue()


def _stamped_wide(tmp_path, monkeypatch):
    """Run one cycle that stamps the fixture spec at all five sections.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary directory.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to set the hq environment for the run.

    Returns
    -------
    tuple[pathlib.Path, str]
        The handoff folder, and the stdout of the finish that closed the
        cycle.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    assert _run([
        'stamp', _SLUG, 'SPEC.md', '--where', _WIDE,
        '--label', 'every section of the build checklist'])[0] == 0
    rc, out = _run(['finish', _SLUG, '--log', 'stamped wide'])
    assert rc == 0
    return folder, out


def _latest(folder, path):
    """Return the newest ledger row for one stored path.
    """
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    return hq.latest_rows(rows)[path]


def test_narrowing_an_anchor_set_keeps_the_row_live_and_gated(
        tmp_path, monkeypatch):
    """A re-stamp naming fewer anchors is accepted and the spec stays gated.

    Mutation: R1 reading the where field - comparing the new anchor set
    against the stored one and refusing any that shrinks - which leaves a
    spec row that can never be trimmed once a section is added to it.
    Oracle: the ledger row after the re-stamp, field by field against the
    five values a live gated spec must carry.
    """
    folder, _ = _stamped_wide(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    rc, out = _run(['stamp', _SLUG, 'SPEC.md', '--where', _NARROW])
    assert (rc, 'refused' in out) == (0, False)
    row = _latest(folder, 'SPEC.md')
    assert row['where'] == _NARROW
    assert (row['status'], row['read_before'], row['kind']) == (
        'live', 'always', 'spec')


def test_the_read_block_total_falls_to_the_narrowed_spans(
        tmp_path, monkeypatch):
    """The rendered read-first size counts only the anchored spans.

    Mutation: read_first_data sizing from the row's stored lines field or
    from the whole file rather than the resolved spans, so a narrowing
    saves nothing and the rendered total never moves.
    Oracle: hand-computed over the fixture. Wide spans hold 2020 chars
    (91 + 821 + 93 + 847 + 168), narrowed spans 539 (91 + 93 + 92 + 95 +
    168); hq sizes at chars // 4, giving 505 and 134.
    """
    folder, wide_out = _stamped_wide(tmp_path, monkeypatch)
    assert 'read first: 1 rows, 505 tok (1 anchored, 0 whole)' in wide_out
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', _NARROW])[0] == 0
    rc, narrow_out = _run(['finish', _SLUG, '--log', 'narrowed'])
    assert rc == 0
    assert 'read first: 1 rows, 134 tok (1 anchored, 0 whole)' in narrow_out
    assert 'SPEC.md:2-4,26-28,46-48,49-51,52-56' in (
        (folder / 'HANDOFF.md').read_text(encoding='utf-8'))


def test_hq_read_prints_only_the_spans_the_narrowed_anchor_names(
        tmp_path, monkeypatch):
    """After a narrowing, hq read no longer prints the dropped sections.

    Mutation: the anchored branch of hq read dropped, so the verb falls
    through to the whole file and hands back the sections the narrowing
    removed - the trim then saves nothing a reader actually pays.
    Oracle: the four heading texts, checked both ways. Section 2 and
    section 4 print before the narrowing and are absent after it, while
    section 3 and the Deferred sub-heading print in both.
    """
    _stamped_wide(tmp_path, monkeypatch)
    rc, wide_read = _run(['read', _SLUG, 'SPEC.md'])
    assert rc == 0
    assert '## 2. Framework facts' in wide_read
    assert '## 4. Case catalog' in wide_read
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', _NARROW])[0] == 0
    rc, narrow_read = _run(['read', _SLUG, 'SPEC.md'])
    assert rc == 0
    assert '## 2. Framework facts' not in narrow_read
    assert '## 4. Case catalog' not in narrow_read
    assert '## 3. Rulings' in narrow_read
    assert '### Deferred' in narrow_read


def test_open_reports_the_narrowing_once_and_quiets_after_the_finish(
        tmp_path, monkeypatch):
    """Hq open names the moved spans until the next finish re-renders them.

    Mutation: the span comparison keyed on the count of anchors rather
    than their values, which stays silent on any trim that also drops a
    section, leaving the next session a read block that disagrees with
    the ledger it was rendered from.
    Oracle: the literal 'span moved:' line, present between the stamp and
    the finish and absent once the finish has re-rendered the block.
    """
    _stamped_wide(tmp_path, monkeypatch)
    assert 'span moved:' not in _run(['open', _SLUG])[1]
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', _NARROW])[0] == 0
    rc, mid_cycle = _run(['open', _SLUG])
    assert rc == 0
    assert 'span moved: SPEC.md' in mid_cycle
    assert _run(['finish', _SLUG, '--log', 'narrowed'])[0] == 0
    assert 'span moved:' not in _run(['open', _SLUG])[1]


def test_an_edit_inside_a_dropped_section_still_blocks_finish(
        tmp_path, monkeypatch):
    """R3 keeps its teeth over a section the narrowed anchor no longer names.

    Mutation: R3 hashing the anchored spans instead of the whole file, so
    an edit outside the anchor slips through finish unrecorded and the
    next resume reads a stale sha for a file that moved.
    Oracle: line 10 sits inside section 2, which the narrowed anchor set
    drops; the documented R3 refusal and exit 1, cleared by a re-stamp
    alone.
    """
    folder, _ = _stamped_wide(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', _NARROW])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'narrowed'])[0] == 0
    lines = _SPEC_TEXT.splitlines()
    lines[9] = 'y' * 39
    (folder / 'SPEC.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert _run(['begin', _SLUG])[0] == 0
    rc, out = _run(['finish', _SLUG, '--log', 'edited a dropped section'])
    assert (rc, out.strip()) == (
        1, 'R3: SPEC.md sha moved; re-stamp before finish')
    assert _run(['stamp', _SLUG, 'SPEC.md'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'edited a dropped section'])[0] == 0


def test_a_re_stamp_that_omits_where_keeps_the_stored_anchor_set(
        tmp_path, monkeypatch):
    """Omitting --where carries the anchors forward rather than clearing them.

    Mutation: where defaulting to '-' when the flag is absent, which
    un-anchors a spec on any re-stamp that only moves a label - R1's
    no-anchor refusal is judged before the carry-forward, so the row
    would go quietly back to a whole-file read at every resume.
    Oracle: the stored where after a label-only re-stamp, and the read
    block still sized at the narrowed 134 tok rather than the fixture's
    whole-file size.
    """
    folder, _ = _stamped_wide(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', _NARROW])[0] == 0
    assert _run([
        'stamp', _SLUG, 'SPEC.md',
        '--label', 'contract only; sections 2 and 4 sit outside the span'])[0] == 0
    assert _latest(folder, 'SPEC.md')['where'] == _NARROW
    rc, out = _run(['finish', _SLUG, '--log', 'relabeled'])
    assert rc == 0
    assert 'read first: 1 rows, 134 tok (1 anchored, 0 whole)' in out


def test_a_sub_heading_anchors_without_its_parent_section(
        tmp_path, monkeypatch):
    """A '###' anchor resolves on its own, and its parent swallows it.

    Mutation: a span ending at the next heading of any level rather than
    the next of the same or higher level, which cuts section 4 short at
    its first sub-heading and makes 'keep the subsection, drop the cases'
    impossible to express.
    Oracle: hand-computed against the fixture's heading lines - section 4
    runs 29-51, through both sub-headings to the line before '## 5. CI',
    while Deferred alone runs 46-48 and Not planned 49-51.
    """
    spans, unresolved = hq.resolve_where(_SPEC_TEXT, ['s4'])
    assert (spans, unresolved) == ([(29, 51)], [])
    spans, unresolved = hq.resolve_where(
        _SPEC_TEXT, ['Deferred', 'Not planned'])
    assert (spans, unresolved) == ([(46, 48), (49, 51)], [])
