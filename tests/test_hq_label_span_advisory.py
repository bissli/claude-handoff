"""Third label advisory: carried label names sections outside the narrowed span.

The advisory fires when a stamp supplies --where but not --label, leaving the
previous label describing sections the new span does not cover. Each test
drives one condition of the gate or one edge of the token-resolution logic.
"""

import contextlib
import io
import pathlib

from scripts import hq

_SLUG = 'label-adv-slug'
_SESSION = 'session-label-adv'
_NOW = '2026-09-15T09:00:00'

_SPEC_TEXT = (
    '# 1. Scope\n'
    '\n'
    'Section one content.\n'
    '\n'
    '# 2. Method\n'
    '\n'
    'Section two content.\n'
    '\n'
    '# 3. Results\n'
    '\n'
    'Section three content.\n'
    )


def _env(tmp_path, monkeypatch):
    """Create an HQ_ROOT and set the HQ_* environment for one slug.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest temporary directory.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to set the hq environment for the run.

    Returns
    -------
    pathlib.Path
        The handoff folder root/.handoff/<slug>/, not yet created.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    return folder


def _run(argv):
    """Drive hq.main over one argument list and capture stdout.

    Returns
    -------
    tuple[int, str, str]
        Exit code, stdout, stderr.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _advisory_prefix():
    """Return the fixed prefix shared by all 'label names' advisory lines."""
    return 'advisory: label names '


def test_a_narrowing_with_a_carried_label_names_the_uncovered_sections(
        tmp_path, monkeypatch):
    """A re-stamp with --where narrowed but --label omitted reports the
    sections the carried label names that the new span does not cover.

    Mutation: the coverage test inverted so covered tokens are reported;
    or sorted() removed so the reported list order is nondeterministic.
    Oracle: hand-computed from the fixture headings - s1 falls inside
    resolve_where(text, ['s1'])[0] but s2 and s3 do not; s4, s5, and s6
    do not exist in the three-section file. All five appear in the
    advisory in sorted order ['s2', 's3', 's4', 's5', 's6']. With five
    tokens a random set ordering matches sorted order 1 in 120 runs, so
    the assertion reliably fails under the mutation across ten runs.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    assert _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 's1;s2;s3',
        '--label', 's1 scope, s2 facts, s3 rulings, s4 extra, s5 more, s6 last'])[0] == 0
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--where', 's1'])
    assert rc == 0
    lines = out.splitlines()
    advisory = next(
        (ln for ln in lines if ln.startswith(_advisory_prefix())), None)
    assert advisory is not None, f'no advisory in: {out!r}'
    assert "['s2', 's3', 's4', 's5', 's6']" in advisory
    assert 'SPEC.md' in advisory
    assert ' - ' in advisory


def test_a_label_given_with_the_narrowing_draws_no_advisory(
        tmp_path, monkeypatch):
    """A stamp that explicitly supplies --label draws no span advisory even
    when the new label still names out-of-span sections.

    Mutation: the label-not-given gate dropped, so every narrowing fires
    and the advisory becomes noise on the command that already rewrote the
    label.
    Oracle: the stdout of the supplied-label run contains no line starting
    with the advisory prefix, while the carried-label run does.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    assert _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 's1;s2;s3',
        '--label', 's1 scope, s2 facts, s3 rulings'])[0] == 0
    # Carried label: advisory fires.
    _, carried_out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--where', 's1'])
    assert any(
        ln.startswith(_advisory_prefix()) for ln in carried_out.splitlines())
    # Supplied label: no advisory.
    rc, supplied_out, _ = _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 's1',
        '--label', 's2 still documented elsewhere'])
    assert rc == 0
    assert not any(
        ln.startswith(_advisory_prefix())
        for ln in supplied_out.splitlines())


def test_a_token_naming_a_nonexistent_section_is_reported_in_the_advisory(
        tmp_path, monkeypatch):
    """A label token that resolves to no heading at all is reported in the
    same advisory as out-of-span tokens, not silently ignored.

    Mutation: the not-token_spans branch removed so a token that resolves
    to nothing is skipped, letting a label that names a nonexistent section
    pass without any advisory.
    Oracle: a three-section file labeled with 's1 scope, s10 docs'; s10
    resolves to nothing, so ['s10'] appears in the advisory when --where
    narrows to s1 without --label.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    assert _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 's1;s2;s3',
        '--label', 's1 scope, s10 docs'])[0] == 0
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--where', 's1'])
    assert rc == 0
    lines = out.splitlines()
    advisory = next(
        (ln for ln in lines if ln.startswith(_advisory_prefix())), None)
    assert advisory is not None, f'no advisory in: {out!r}'
    assert "['s10']" in advisory


def test_a_section_anchored_by_its_title_counts_as_covered(
        tmp_path, monkeypatch):
    """A --where given as a heading title covers the same section as its
    s<n> form, so a label naming s1 draws no advisory when --where is the
    title of section 1.

    Mutation: coverage decided by comparing label tokens with the where
    string textually, which reports s1 uncovered whenever the row anchors
    that section by its heading text - a spelling hq help anchors sanctions.
    Oracle: resolve_where(text, ['Scope']) and resolve_where(text, ['s1'])
    share the same start line, shown in the test body.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    # Confirm both anchors reach the same heading start.
    spans_title, _ = hq.resolve_where(_SPEC_TEXT, ['Scope'])
    spans_s1, _ = hq.resolve_where(_SPEC_TEXT, ['s1'])
    assert spans_title
    assert spans_s1
    assert spans_title[0][0] == spans_s1[0][0]
    assert _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 'Scope',
        '--label', 's1 the scope'])[0] == 0
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Scope'])
    assert rc == 0
    assert not any(
        ln.startswith(_advisory_prefix()) for ln in out.splitlines())


def test_the_advisory_leaves_the_row_written_and_the_exit_zero(
        tmp_path, monkeypatch):
    """The advisory is informational: the narrowed row lands in ledger.tsv
    with the new where and the carried label, and the exit code is 0.

    Mutation: the advisory turned into a refusal returning 1, or the check
    placed above _append_tsv so a fired advisory leaves no row.
    Oracle: hq.latest_rows(hq._read_tsv(...))['SPEC.md'] read directly
    for where and label; exit code captured from the run.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text(_SPEC_TEXT, encoding='utf-8')
    assert _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 's1;s2;s3',
        '--label', 's1 scope, s2 facts, s3 rulings'])[0] == 0
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--where', 's1'])
    assert rc == 0
    assert any(
        ln.startswith(_advisory_prefix()) for ln in out.splitlines())
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    row = hq.latest_rows(rows)['SPEC.md']
    assert row['where'] == 's1'
    assert row['label'] == 's1 scope, s2 facts, s3 rulings'
