"""Cycle ownership and the paths finish writes.

The tests use real files and hq.main, with no simulated lock or write
implementation: a foreign session's mutation refused whole, and a path
finish cannot write refusing it before the first write.
"""

import contextlib
import io

import pytest

from bin import hq

_SLUG = 'cycle-isolation'
_SESSION = 'session-owner'
_NOW = '2026-09-21T12:00:00'
_CURSOR = (
    '## Task\nKeep the retry bounded.\n\n'
    '## Now\nWire the retry into the caller.\n')


def _run(argv):
    """Call hq.main and return its exit code and captured stdout."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = hq.main(argv)
    return rc, out.getvalue()


def _new_root(tmp_path, monkeypatch):
    """Configure isolated CLI state and return the new handoff folder."""
    root = tmp_path / 'root'
    root.mkdir()
    for key, value in {
        'HQ_ROOT': str(root), 'HQ_STATE_DIR': str(tmp_path / 'state'),
        'HQ_SESSION': _SESSION, 'HQ_NOW': _NOW, 'HQ_HOST': 'test-host',
        'HQ_GIT': '0',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    assert _run(['begin', _SLUG])[0] == 0
    folder = root / '.handoff' / _SLUG
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-09-21 | Cycle: 1\n\n'
        + _CURSOR, encoding='utf-8')
    return folder


def _snapshot(folder):
    """Return every regular file's relative path and bytes in a thread."""
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in folder.rglob('*') if path.is_file()
    }


@pytest.mark.parametrize('verb', ['note', 'stamp', 'supersede'])
def test_a_session_refused_begin_cannot_append_to_the_owners_cycle(
        tmp_path, monkeypatch, verb):
    """A foreign writer cannot bypass begin's refusal with a mutation verb.

    Mutation: a verb dropped from the ownership gate, letting another
    session append notes, stamps, or supersessions inside the first
    session's cycle.
    Oracle: begin and the mutation both refuse, each naming the owning
    session; every thread file, the lock included, stays byte-identical.
    """
    folder = _new_root(tmp_path, monkeypatch)
    (folder / 'drafts').mkdir()
    (folder / 'drafts' / 'app.py').write_text('value = 1\n', encoding='utf-8')
    before = _snapshot(folder)
    foreign = ['--session', 'session-foreign']
    rc, out = _run([*foreign, 'begin', _SLUG])
    assert rc == 1
    assert _SESSION in out
    assert _snapshot(folder) == before
    args = {
        'note': ['note', _SLUG, 'decision', '--headline', 'Foreign choice',
                 'Keep it.'],
        'stamp': ['stamp', _SLUG, 'drafts/app.py'],
        'supersede': ['supersede', _SLUG, 'd01', 'd02'],
        }[verb]

    rc, out = _run([*foreign, *args])

    assert rc == 1, f'foreign {verb} unexpectedly succeeded: {out}'
    assert _SESSION in out
    assert _snapshot(folder) == before


@pytest.mark.parametrize(('finished_cycles', 'obstruct'), [
    (0, 'archive'), (1, 'archive'), (0, 'handoff'), (0, 'manifest'),
    (0, 'cycles'),
], ids=['first-cycle', 'later-cycle', 'unwritable-handoff',
        'unwritable-manifest', 'unwritable-cycles-dir'])
def test_an_unwritable_path_leaves_finish_unchanged(
        tmp_path, monkeypatch, finished_cycles, obstruct):
    """A path finish cannot write refuses it before the first write.

    Mutation: the preflight covers the archive alone, so an unwritable
    HANDOFF.md still drains Unfiled into standing.md and the retry
    records the same decision a second time under a fresh id.
    Oracle: every file matches its pre-finish bytes and the lock
    survives; clearing the obstruction then yields one completed cycle
    carrying the decision once, with earlier archives untouched.
    """
    folder = _new_root(tmp_path, monkeypatch)
    if finished_cycles:
        assert _run(['finish', _SLUG, '--log', 'seed cycle'])[0] == 0
        assert _run(['begin', _SLUG])[0] == 0
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text(encoding='utf-8')
    unfiled = '\n## Unfiled\n- decision: **Keep the bound** Five tries only.\n\n'
    marker = text.find('<!-- hq:')
    if marker == -1:
        text += unfiled
    else:
        text = text[:marker] + unfiled + text[marker:]
    handoff.write_text(text, encoding='utf-8')
    cycle = finished_cycles + 1
    archive = folder / 'cycles' / f'c{cycle:02d}.md'
    blocked = {
        'handoff': handoff,
        'manifest': folder / 'cycles' / 'manifest.tsv',
        'cycles': archive,
        }.get(obstruct, archive)
    if obstruct == 'archive':
        archive.mkdir()
    elif obstruct == 'cycles':
        (folder / 'cycles').chmod(0o555)
    else:
        blocked.chmod(0o444)
    before = _snapshot(folder)

    rc, out = _run(['finish', _SLUG, '--log', 'record the bound'])

    after = _snapshot(folder)
    changed = sorted(
        name for name in before.keys() | after.keys()
        if before.get(name) != after.get(name))
    assert changed == [], f'refused finish changed files: {changed}'
    assert rc == 1
    assert blocked.name in out
    assert (folder / '.hq.lock').is_file()

    if obstruct == 'archive':
        archive.rmdir()
    elif obstruct == 'cycles':
        (folder / 'cycles').chmod(0o755)
    else:
        blocked.chmod(0o644)
    assert _run(['finish', _SLUG, '--log', 'record the bound'])[0] == 0
    assert not (folder / '.hq.lock').exists()
    assert archive.read_bytes() == handoff.read_bytes()
    standing = (folder / 'standing.md').read_text(encoding='utf-8')
    assert standing.count('**Keep the bound**') == 1
    assert 'Five tries only.' in standing
    rows = hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
    assert [row['cycle'] for row in rows] == [str(n) for n in range(1, cycle + 1)]
    for name, data in before.items():
        if name.startswith('cycles/c') and name.endswith('.md'):
            assert (folder / name).read_bytes() == data
