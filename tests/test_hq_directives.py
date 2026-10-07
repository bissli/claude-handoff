"""Tests for the cursor's ``## Directives`` section and its finish check."""

import contextlib
import io
import pathlib
import re

from bin import hq

_SLUG = 'directives'
_RULE = '- Run the integration suite with --db staging-copy.'


def _root(tmp_path, monkeypatch):
    """Create an HQ_ROOT with the HQ_* environment set; return the folder."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', '2026-10-07T12:00:00')
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


def _set(folder, heading, body):
    """Replace the body of one cursor section in HANDOFF.md."""
    path = folder / 'HANDOFF.md'
    text = path.read_text()
    text = re.sub(
        rf'^## {heading}\n.*?(?=^## |^<!-- hq:)', f'## {heading}\n{body}\n\n',
        text, count=1, flags=re.MULTILINE | re.DOTALL)
    path.write_text(text)


def _cycle_one(tmp_path, monkeypatch, directives):
    """Finish cycle 1 with the given Directives body, then begin cycle 2."""
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _set(folder, 'Directives', directives)
    _set(folder, 'Task', 'Probe the directives check.')
    _set(folder, 'Now', 'Step one.')
    assert _run(['finish', _SLUG, '--log', 'c1'])[0] == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert _run(['begin', _SLUG])[0] == 0
    _set(folder, 'Now', 'Step two.')
    return folder


def _snapshot(folder):
    """Return every regular file's relative path and bytes in a thread."""
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in folder.rglob('*') if path.is_file()
        }


def test_a_changed_directive_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """A reworded directive without the flag refuses before any write.

    Mutation: the directives check dropped, or moved after the archive
    and standing.md writes.
    Oracle: rc 1, the refusal naming the flag, every thread file
    byte-identical, the lock included.
    """
    folder = _cycle_one(tmp_path, monkeypatch, _RULE)
    _set(folder, 'Directives', '- Run the integration suite with --db prod.')
    before = _snapshot(folder)
    rc, out = _run(['finish', _SLUG, '--log', 'c2'])
    assert rc == 1
    assert 'hq finish: ## Directives changed since c01' in out
    assert '--replace-directives' in out
    assert _snapshot(folder) == before


def test_an_added_directive_refuses(tmp_path, monkeypatch):
    """A directive added to an empty section needs the flag too.

    Mutation: the check comparing only lines the old section held, so
    an addition passes.
    Oracle: rc 1 on a cycle 2 that adds the first directive.
    """
    folder = _cycle_one(tmp_path, monkeypatch, '')
    _set(folder, 'Directives', _RULE)
    assert _run(['finish', _SLUG, '--log', 'c2'])[0] == 1


def test_a_directive_moved_to_environment_refuses(tmp_path, monkeypatch):
    """A directive that leaves the section counts as changed.

    Mutation: the check reading not_carried's witnesses, where the
    moved line still appears.
    Oracle: rc 1 with the line present word for word under Environment.
    """
    folder = _cycle_one(tmp_path, monkeypatch, _RULE)
    _set(folder, 'Directives', '')
    _set(folder, 'Environment', _RULE)
    assert _run(['finish', _SLUG, '--log', 'c2'])[0] == 1


def test_a_rewrapped_directive_passes(tmp_path, monkeypatch):
    """A bullet re-wrapped at the column is the same directive.

    Mutation: the section compared line by line without collapsing
    whitespace.
    Oracle: rc 0 on the same words split over two lines.
    """
    folder = _cycle_one(tmp_path, monkeypatch, _RULE)
    _set(
        folder, 'Directives',
        '- Run the integration suite\n  with --db staging-copy.')
    assert _run(['finish', _SLUG, '--log', 'c2'])[0] == 0


def test_replace_directives_records_the_old_section(tmp_path, monkeypatch):
    """The flag passes a change and files a decision quoting the old text.

    Mutation: the decision not written, or written without the old
    section, so the old line drops out of the record.
    Oracle: rc 0, standing.md holding the reason and the old line, and
    no not-carried advisory for it.
    """
    folder = _cycle_one(tmp_path, monkeypatch, _RULE)
    _set(folder, 'Directives', '')
    rc, out = _run([
        'finish', _SLUG, '--log', 'c2',
        '--replace-directives', 'the user lifted it after the merge'])
    assert rc == 0
    standing = (folder / 'standing.md').read_text()
    assert '**Directives replaced** the user lifted it after the merge.' in standing
    assert 'Run the integration suite with --db staging-copy.' in standing
    assert 'not carried' not in out


def test_replace_directives_on_an_unchanged_section_refuses(
        tmp_path, monkeypatch):
    """The flag on an unchanged section refuses instead of filing a decision.

    Mutation: the flag accepted on any cycle, filing a decision that
    records no change.
    Oracle: rc 1, the unchanged refusal, and no new standing.md line.
    """
    folder = _cycle_one(tmp_path, monkeypatch, _RULE)
    before = (folder / 'standing.md').read_bytes()
    rc, out = _run([
        'finish', _SLUG, '--log', 'c2', '--replace-directives', 'no reason'])
    assert rc == 1
    assert '## Directives is unchanged since c01' in out
    assert (folder / 'standing.md').read_bytes() == before


def test_the_first_cycle_sets_directives_without_the_flag(
        tmp_path, monkeypatch):
    """Cycle 1 has no archive to compare, so its directives pass as written.

    Mutation: a missing archive read as an empty section, which refuses
    every first cycle that writes a directive.
    Oracle: rc 0 on cycle 1 with one directive and no flag.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    _set(folder, 'Directives', _RULE)
    _set(folder, 'Task', 'Probe the directives check.')
    _set(folder, 'Now', 'Step one.')
    assert _run(['finish', _SLUG, '--log', 'c1'])[0] == 0


def test_the_first_finish_after_adopt_compares_nothing(tmp_path, monkeypatch):
    """An adopted file's Directives below its Log does not read as a change.

    Mutation: the adopt row's archive, the foreign file, compared as a
    cursor, so the merged section differs from the archive's empty one.
    Oracle: rc 0 on the first finish after adopt with no cursor edit.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-10-01 | Cycle: 3\n\n'
        '## Task\n\nDo the work.\n\n## Now\n\nNext step.\n\n'
        '## Log\n\n- c3 wrote the plan\n\n'
        f'## Directives\n\n{_RULE}\n')
    assert _run(['adopt', _SLUG])[0] == 0
    monkeypatch.setenv('HQ_CYCLE', '4')
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'c4'])[0] == 0
