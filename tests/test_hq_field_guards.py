"""Control-character guards on --label and --reason in hq stamp.

A tab or any character str.splitlines() breaks on would silently corrupt
the tab-delimited ledger. The guard fires before any write so ledger.tsv is
byte-identical before and after a refused stamp.
"""

import contextlib
import io
import pathlib

from scripts import hq

_SLUG = 'field-guard-slug'
_SESSION = 'session-field-guard'
_NOW = '2026-09-15T10:00:00'


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


def test_a_control_character_in_label_or_reason_is_refused_before_any_write(
        tmp_path, monkeypatch):
    """Each of tab, newline, and carriage return in --label or --reason
    exits 2 with the guard message before writing anything to ledger.tsv.

    Mutation: the guard placed after _append_tsv so the row lands with the
    character silently folded to a space; or only one of the three characters
    tested; or only --label guarded while --reason still reaches the write.
    Oracle: ledger.tsv bytes captured before each call and compared after;
    exit code 2 for all six flag-character pairs.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    assert _run([
        'stamp', _SLUG, 'SPEC.md', '--where', 'Spec',
        '--reason', 'initial stamp'])[0] == 0
    ledger = folder / 'ledger.tsv'
    for flag, arg in (('--label', '--label'), ('--reason', '--reason')):
        for char, name in (('\t', 'tab'), ('\n', 'newline'), ('\r', 'cr')):
            before = ledger.read_bytes()
            rc, out, _ = _run([
                'stamp', _SLUG, 'SPEC.md',
                arg, f'bad{char}value'])
            assert rc == 2, f'{flag} with {name}: expected exit 2, got {rc}'
            assert f'hq stamp: {flag} may not contain' in out, (
                f'{flag} with {name}: message missing from {out!r}')
            assert ' - ' in out, f'{flag} with {name}: move tail missing'
            assert ledger.read_bytes() == before, (
                f'{flag} with {name}: ledger changed after refused stamp')


def test_a_label_with_punctuation_still_stamps(tmp_path, monkeypatch):
    """A label carrying apostrophes, hyphens, semicolons, and backticks
    passes the guard and lands in ledger.tsv exactly as typed.

    Mutation: the guard widened from the three control characters to any
    punctuation or non-alphanumeric, which would refuse the live label shape
    the plugin-eval-suite uses.
    Oracle: the typed label string, compared with the label field of the
    ledger's last row.
    """
    folder = _env(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n', encoding='utf-8')
    rich_label = "s1 scope; s2 facts - don't drop `config.py` or `run.sh`"
    rc, _, _ = _run([
        'stamp', _SLUG, 'SPEC.md',
        '--where', 'Spec',
        '--label', rich_label])
    assert rc == 0
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    row = hq.latest_rows(rows)['SPEC.md']
    assert row['label'] == rich_label
