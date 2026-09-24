"""The named view: which Standing items and Artifacts rows render whole.

An item the cursor cites or the cycle recorded renders whole, and every
other live item renders as its headline. A row graded always, stamped
this cycle, or named by the cursor prints in full, and every other live
row prints as its path alone.
"""
import contextlib
import io
import pathlib

from bin import hq

_SLUG = 'fold-slug'


def _root(tmp_path, monkeypatch):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', '2026-09-24T12:00:00')
    monkeypatch.setenv('HQ_SESSION', 'session-fold')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    return folder


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue()


def _cursor(folder, body):
    """Overwrite the cursor, keeping the header line the script owns.
    """
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text()
    handoff.write_text(text[:text.index('\n## ') + 1] + body)


def _blocks(folder):
    """Return the rendered Standing and Artifacts block bodies as lines.
    """
    blocks = hq.split_handoff((folder / 'HANDOFF.md').read_text())['blocks']
    return blocks['standing'].splitlines(), blocks['artifacts'].splitlines()


def test_a_cited_id_or_this_cycles_item_renders_whole(tmp_path):
    """Only an id cited as its own word, or recorded this cycle, is whole.

    Mutation: the lookbehind dropped, so an id inside a path cites its
    item; the lookahead dropped, so an id inside a hyphenated name does;
    the recorded-this-cycle clause dropped; or the cycle compared as an
    int against the stored string, so it never matches.
    Oracle: hand-listed ids for a cursor holding one plain citation, one
    in parentheses, one inside a path, and one inside a hyphenated name.
    """
    folder = tmp_path / 'slug'
    folder.mkdir()
    (folder / 'ledger.tsv').write_text(hq._LEDGER_HEADER + '\n')
    standing = ''.join(
        f'- [{item_id}] (c{cycle}) **Item {item_id}** Body.\n'
        for item_id, cycle in (
            ('c01', 1), ('c05', 5), ('d02', 1), ('d06', 3), ('x03', 2),
            ('x04', 1)))
    text = hq._assemble_handoff(
        folder,
        cursor_text=(
            '## Task\nKeep c01 in force (x04).\n\n'
            '## Now\nSee notes/d02.md and the x03-retry branch.\n'),
        header_line='Written: 2026-09-24 | Cycle: 5',
        log_body='',
        live={},
        walk=[],
        standing_text=standing,
        cycle=5)
    lines = hq.split_handoff(text)['blocks']['standing'].splitlines()
    whole = {ln[1:4] for ln in lines if ln.startswith('[') and '**' in ln}
    folded = {ln[1:4] for ln in lines if ln.startswith('[') and '**' not in ln}
    assert whole == {'c01', 'c05', 'x04'}
    assert folded == {'d02', 'd06', 'x03'}


def test_a_row_is_whole_when_always_stamped_this_cycle_or_named(
        tmp_path, monkeypatch):
    """Four live rows print in full, one prints its path, one is counted.

    Mutation: the whole-path test placed ahead of the status test, so a
    row archived this cycle prints as live; the basename clause dropped,
    so a row the cursor names by file name alone folds; the this-cycle
    clause dropped, so a row stamped this cycle folds; or the always
    clause dropped.
    Oracle: two hand-built cycles, one row straddling each clause.
    """
    folder = _root(tmp_path, monkeypatch)
    for name in ('specs', 'notes', 'outputs'):
        (folder / name).mkdir()
    (folder / 'specs' / 'SPEC.md').write_text('# Spec\n\nThe contract.\n')
    for name in ('brief', 'idp', 'old', 'gone'):
        (folder / 'notes' / f'{name}.md').write_text(f'# {name}\n')
    (folder / 'outputs' / 'new.md').write_text('# New\n')
    assert _run(['begin', _SLUG])[0] == 0
    assert _run([
        'stamp', _SLUG, 'specs/SPEC.md', '--where', 'Spec',
        '--label', 'the contract'])[0] == 0
    for name, grade in (
            ('brief', 'edit'), ('idp', 'never'), ('old', 'edit'),
            ('gone', 'never')):
        assert _run([
            'stamp', _SLUG, f'notes/{name}.md', '--read-before', grade,
            '--label', f'note {name}'])[0] == 0
    _cursor(folder, '## Task\nRefresh the token.\n\n## Now\nWrite it.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0

    assert _run(['begin', _SLUG])[0] == 0
    assert _run([
        'stamp', _SLUG, 'outputs/new.md', '--read-before', 'never',
        '--label', 'new output'])[0] == 0
    assert _run([
        'stamp', _SLUG, 'notes/gone.md', '--archive',
        '--reason', 'folded into brief'])[0] == 0
    _cursor(
        folder,
        '## Task\nRefresh the token.\n\n'
        '## Now\nRead notes/brief.md and the quirks in idp.md.\n')
    assert _run(['finish', _SLUG, '--log', 'two'])[0] == 0
    _, artifacts = _blocks(folder)
    base = f'.handoff/{_SLUG}'
    assert artifacts[2:] == [
        f'{base}/notes/brief.md  notes  edit  c1  note brief',
        f'{base}/notes/idp.md  notes  never  c1  note idp',
        f'{base}/outputs/new.md  other  never  c2  new output',
        f'{base}/specs/SPEC.md  spec  always  c1',
        f'{base}/notes/old.md',
        f'hq when {_SLUG} <path> prints any row above whole',
        f'archived 1  - hq artifacts {_SLUG} --status archived',
        ]


def test_the_closing_line_follows_only_a_block_with_a_folded_item():
    """A block whose items all render whole prints no closing line.

    Mutation: the closing line printed unconditionally, or printed inside
    the first kind group instead of after the last one.
    Oracle: the same two items rendered with both ids whole, then with
    one folded, against hand-written lines.
    """
    items = [
        {'id': 'c01', 'prefix': 'c', 'cycle': '1',
         'headline': 'Never log token values', 'body': ''},
        {'id': 'd01', 'prefix': 'd', 'cycle': '1',
         'headline': 'Refresh in-process', 'body': ''},
        ]
    assert hq.render_standing(items, set(), 's', {'c01', 'd01'}).splitlines() == [
        '### Constraints',
        '[c01] (c1) **Never log token values**',
        '### Decisions',
        '[d01] (c1) **Refresh in-process**',
        ]
    assert hq.render_standing(items, set(), 's', {'d01'}).splitlines() == [
        '### Constraints',
        '[c01] Never log token values',
        '### Decisions',
        '[d01] (c1) **Refresh in-process**',
        'hq standing s <id> prints any item above whole',
        ]


def test_every_folded_row_prints_its_path_whatever_its_tier():
    """Folded rows of three tiers each print a path; none prints a count.

    Mutation: a folded row counted by kind instead of listed, so its name
    leaves the block; a never row dropped from the list; or the closing
    line printed when no row folded.
    Oracle: three live rows of three tiers rendered with none whole and
    then all whole, against hand-written lines.
    """
    rows = {
        'notes/a.md': {'status': 'live', 'read_before': 'mention',
                       'kind': 'notes', 'cycle': '1', 'label': 'a'},
        'notes/b.md': {'status': 'live', 'read_before': 'never',
                       'kind': 'notes', 'cycle': '1', 'label': 'b'},
        'outputs/c.md': {'status': 'live', 'read_before': 'edit',
                         'kind': 'other', 'cycle': '1', 'label': 'c'},
        }
    assert hq.render_artifacts([], rows, 's', set()).splitlines() == [
        'notes/a.md',
        'notes/b.md',
        'outputs/c.md',
        'hq when s <path> prints any row above whole',
        ]
    assert hq.render_artifacts([], rows, 's', set(rows)).splitlines() == [
        'notes/a.md  notes  mention  c1  a',
        'notes/b.md  notes  never  c1  b',
        'outputs/c.md  other  edit  c1  c',
        ]


def test_finish_keeps_a_cited_item_and_a_named_row_whole_next_cycle(
        tmp_path, monkeypatch):
    """Cycle 2 folds c01 and notes/a.md and keeps d01 and notes/b.md whole.

    Mutation: finish rendering against the cursor before its Unfiled
    drain or against the previous cycle's number, so what this cycle
    recorded folds; or the fold ignoring the cursor's citation, so d01
    folds too.
    Oracle: two hand-built cycles - both items and rows recorded in
    cycle 1, the cursor citing d01 and naming notes/b.md.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'notes').mkdir()
    (folder / 'notes' / 'a.md').write_text('# A\n')
    (folder / 'notes' / 'b.md').write_text('# B\n')
    assert _run(['begin', _SLUG])[0] == 0
    assert _run([
        'note', _SLUG, 'constraint', '--headline', 'Never log token values',
        'Not even at debug.'])[0] == 0
    assert _run([
        'stamp', _SLUG, 'notes/a.md', '--read-before', 'never',
        '--label', 'note a'])[0] == 0
    assert _run([
        'stamp', _SLUG, 'notes/b.md', '--read-before', 'edit',
        '--label', 'note b'])[0] == 0
    cursor = '## Task\nRefresh the token.\n\n## Now\nApply d01 per notes/b.md.\n'
    _cursor(
        folder,
        cursor + '\n## Unfiled\n- decision: **Refresh in-process** One caller.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    standing, artifacts = _blocks(folder)
    assert '[c01] (c1) **Never log token values** Not even at debug.' in standing
    assert f'[d01] (c1) **Refresh in-process**  +11c - hq standing {_SLUG} d01' in (
        standing)
    assert f'.handoff/{_SLUG}/notes/a.md' not in artifacts

    assert _run(['begin', _SLUG])[0] == 0
    _cursor(folder, cursor)
    assert _run(['finish', _SLUG, '--log', 'two'])[0] == 0
    standing, artifacts = _blocks(folder)
    assert standing == [
        '## Standing',
        '### Constraints',
        '[c01] Never log token values',
        '### Decisions',
        f'[d01] (c1) **Refresh in-process**  +11c - hq standing {_SLUG} d01',
        f'hq standing {_SLUG} <id> prints any item above whole',
        ]
    assert f'.handoff/{_SLUG}/notes/a.md' in artifacts
    assert (
        f'.handoff/{_SLUG}/notes/b.md  notes  edit  c1  note b' in artifacts)
