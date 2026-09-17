"""Ledger invariants checked over exhaustive flag grids and random sequences.

The rule tests pin one defect each. These tests instead state what must
hold whatever the agent does and sweep the state space for a violation:
every stamp on a gated artifact leaves it covered or is refused with a
receipt, the ledger and standing.md only ever grow, finish writes all or
nothing, and begin and finish name every dangling successor.
"""

import contextlib
import io
import itertools
import pathlib
import random

from bin import hq

_SLUG = 'fuzz-slug'
_GATED = {'spec', 'draft'}
_CONTENT = {
    'SPEC.md': '# Spec\n\n## 1. Scope\n\nOne.\n',
    'DRAFT.py': 'def run():\n    return 1\n',
    'NEXT.md': '# Spec\n\nTwo.\n',
    'notes-a.md': '# Notes\n\nThree.\n',
    'other.txt': 'four\n',
    }
_FILES = list(_CONTENT)


def _env(tmp_path, monkeypatch):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(parents=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_SESSION', 'fuzz-session')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_NOW', '2026-09-09T12:00:00')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    for name, content in _CONTENT.items():
        (folder / name).write_text(content)
    return folder


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = hq.main(argv)
    return rc, out.getvalue()


def _rows(folder):
    lines = (folder / 'ledger.tsv').read_text().splitlines()[1:]
    return [dict(zip(hq.LEDGER_FIELDS, ln.split('\t'))) for ln in lines if ln.strip()]


def _covered(folder, live, path):
    """A gated path keeps its tier - a spec always, a draft edit or always -
    names a successor on disk, or is archived with a reason; a successor's
    own state is the dangling-link check's business.
    """
    row = live.get(path)
    tiers = {'always'} if _gate_kind(folder, live, path) == 'spec' else {'edit', 'always'}
    if row is None or row['status'] == 'live' and row['read_before'] in tiers:
        return True
    if row['status'] == 'archived' and row['reason'] not in {'-', ''}:
        return True
    return row['successor'] != '-' and (folder / row['successor']).is_file()


def _gate_kind(folder, live, path):
    p = folder / path
    heading = ''
    if p.is_file():
        heading = next((ln.strip() for ln in p.read_text().splitlines()
                        if ln.startswith('#')), '')
    inferred, _ = hq.infer_kind(path, p.is_dir(), heading)
    stored = live.get(path, {}).get('kind', '')
    return next((k for k in (inferred, stored) if k in _GATED), '')


def _check_stamp(folder, before, path, argv, rc, ctx=None):
    """Assert the stamp outcome against the row that preceded it; ctx names
    the case in a failure message.
    """
    case = (ctx, argv)
    rows = _rows(folder)
    live = hq.latest_rows(rows)
    prev = hq.latest_rows(before).get(path)
    if rc == 2:
        assert rows == before, case
        return
    assert rc in {0, 1}, case
    assert len(rows) == len(before) + 1, case
    new = rows[-1]
    assert new['path'] == path, case
    if rc == 1:
        assert new['reason'].startswith('refused: '), case
        if prev is not None:
            for field in ('kind', 'status', 'read_before', 'successor',
                          'where', 'sha12', 'lines', 'label'):
                assert new[field] == prev[field], (field, case)
        return
    if _gate_kind(folder, live, path):
        assert _covered(folder, live, path), (case, new)
    if len(argv) == 3 and prev is not None:
        for field in ('kind', 'status', 'read_before', 'successor', 'where', 'label'):
            assert new[field] == prev[field], (field, case)


def test_every_flag_combination_on_a_fresh_spec_is_covered_or_refused(
        tmp_path, monkeypatch):
    """Over 1,920 stamp flag combinations, a fresh spec never ends ungated.

    Mutation: any R1 branch dropped - the read_before floor, the status
    floor, the kind floor, the --defer refusal, the on-disk check of a
    successor, the archive-needs-reason rule - lets one combination
    through with exit 0 and a row that is live and not always, or
    superseded toward a file that is not on disk.
    Oracle: _covered, an independent statement of the design's survival
    guarantee, evaluated on the row the stamp wrote; a refusal must leave
    only a receipt row and exit 2 must leave nothing.
    """
    folder = _env(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    header = (folder / 'ledger.tsv').read_text()
    grid = itertools.product(
        (None, 'always', 'edit', 'mention', 'never'),
        (None, 'live', 'superseded', 'archived'),
        (None, 'spec', 'notes'),
        (None, 'NEXT.md', 'gone.md', 'SPEC.md'),
        (False, True),
        (None, 'why'),
        (False, True))
    count = 0
    for rb, status, kind, successor, archive, reason, defer in grid:
        (folder / 'ledger.tsv').write_text(header)
        argv = ['stamp', _SLUG, 'SPEC.md', '--where', 's1']
        for flag, value in (('--read-before', rb), ('--status', status),
                            ('--kind', kind), ('--successor', successor),
                            ('--reason', reason)):
            if value is not None:
                argv += [flag, value]
        argv += ['--archive'] * archive + ['--defer'] * defer
        rc, _ = _run(argv)
        _check_stamp(folder, [], 'SPEC.md', argv, rc)
        count += 1
    assert count == 1920


def test_every_flag_combination_on_a_stamped_spec_is_covered_or_refused(
        tmp_path, monkeypatch):
    """From four prior states, no re-stamp combination uncovers a spec.

    Mutation: carry-forward of a stale reason excusing --status archived,
    a carried successor judged by the flag alone, the stored kind ignored
    once the heading no longer reads # Spec, or the --archive exit code.
    Oracle: _covered on the resulting row, per prior state; receipts
    equal their predecessor in every field but reason.
    """
    folder = _env(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    header = (folder / 'ledger.tsv').read_text()
    priors = {
        'gated': ['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--reason', 'wip'],
        'superseded': ['stamp', _SLUG, 'SPEC.md', '--successor', 'NEXT.md'],
        'archived': ['stamp', _SLUG, 'SPEC.md', '--archive', '--reason', 'old'],
        'edit-with-successor': [
            'stamp', _SLUG, 'SPEC.md', '--successor', 'NEXT.md',
            '--status', 'live', '--read-before', 'edit'],
        }
    grid = list(itertools.product(
        (None, 'always', 'never'),
        (None, 'live', 'archived'),
        (None, 'notes'),
        (None, 'NEXT.md', 'gone.md'),
        ((False, None), (True, 'why'), (False, 'why')),
        (False, True)))
    for prior_name, prior in priors.items():
        for heading in ('# Spec\n', '# Plan\n'):
            for rb, status, kind, successor, (archive, reason), defer in grid:
                (folder / 'ledger.tsv').write_text(header)
                (folder / 'SPEC.md').write_text('# Spec\n\nOne.\n')
                assert _run(prior)[0] == 0, prior_name
                (folder / 'SPEC.md').write_text(heading + '\nOne.\n')
                before = _rows(folder)
                argv = ['stamp', _SLUG, 'SPEC.md']
                for flag, value in (('--read-before', rb), ('--status', status),
                                    ('--kind', kind), ('--successor', successor),
                                    ('--reason', reason)):
                    if value is not None:
                        argv += [flag, value]
                argv += ['--archive'] * archive + ['--defer'] * defer
                rc, _ = _run(argv)
                _check_stamp(folder, before, 'SPEC.md', argv, rc, (prior_name, heading))


def test_random_verb_sequences_hold_the_ledger_invariants(tmp_path, monkeypatch):
    """Sixty seeded sequences of thirty verbs never break an invariant.

    Mutation: a ledger or standing line rewritten in place (the witnesses
    then pass a size-preserving edit), a finish that writes the file before
    a check fails, a dangling successor begin or finish stays silent on, or
    a stamp that leaves a gated file uncovered after a file deletion and
    restore.
    Oracle: byte-prefix growth of ledger.tsv and standing.md, unchanged
    bytes of every owned file after a failed finish, cycles/cNN.md equal to
    HANDOFF.md after a successful one with open silent, and one
    'successor missing' line per dangling superseded row.
    """
    for seed in range(60):
        rng = random.Random(seed)
        folder = _env(tmp_path / f's{seed}', monkeypatch)
        owned = ['HANDOFF.md', 'ledger.tsv', 'standing.md', 'cycles/manifest.tsv']
        hq.main(['begin', _SLUG])
        for step in range(30):
            ledger_before = (folder / 'ledger.tsv').read_bytes()
            standing_before = (folder / 'standing.md').read_bytes()
            snapshot = {
                name: (folder / name).read_bytes() for name in owned
                if (folder / name).exists()}
            rows_before = _rows(folder)
            roll = rng.random()
            where = (seed, step)
            if roll < 0.5:
                path = rng.choice(_FILES)
                argv = ['stamp', _SLUG, path]
                if rng.random() < 0.3:
                    argv += ['--read-before',
                             rng.choice(['always', 'edit', 'mention', 'never'])]
                if rng.random() < 0.2:
                    argv += ['--status', rng.choice(['live', 'superseded', 'archived'])]
                if rng.random() < 0.2:
                    argv += ['--kind', rng.choice(['spec', 'draft', 'notes', 'other'])]
                if rng.random() < 0.3:
                    argv += ['--successor', rng.choice(_FILES + ['gone.md'])]
                if rng.random() < 0.15:
                    argv += ['--archive']
                if rng.random() < 0.3:
                    argv += ['--reason', f'r{rng.randint(1, 9)}']
                if rng.random() < 0.1:
                    argv += ['--defer']
                if rng.random() < 0.3:
                    argv += ['--label', f'l{rng.randint(1, 99)}']
                if rng.random() < 0.3:
                    argv += ['--where', 's1']
                rc, _ = _run(argv)
                _check_stamp(folder, rows_before, path, argv, rc, where)
            elif roll < 0.6:
                kind = rng.choice(['decision', 'constraint', 'dead-end'])
                rc, _ = _run(['note', _SLUG, kind, '--headline',
                              f'H{step}', f'body {step}'])
                assert rc == 0, where
            elif roll < 0.68:
                path = rng.choice(_FILES)
                (folder / path).unlink(missing_ok=True)
            elif roll < 0.76:
                path = rng.choice(_FILES)
                (folder / path).write_text(_CONTENT[path])
            elif roll < 0.83:
                path = rng.choice(_FILES)
                if (folder / path).exists():
                    with (folder / path).open('a') as fh:
                        fh.write(f'edit {step}\n')
            elif roll < 0.9:
                rc, out = _run(['begin', _SLUG])
                assert rc == 0, where
                _assert_dangling_named(folder, out, where)
            else:
                rc, out = _run(['finish', _SLUG, '--log', f'step {step}'])
                if rc != 0:
                    for name, data in snapshot.items():
                        assert (folder / name).read_bytes() == data, (where, name, out)
                else:
                    _assert_dangling_named(folder, out, where)
                    manifest = hq._read_tsv(
                        folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)
                    cycle = int(manifest[-1]['cycle'])
                    archive = folder / 'cycles' / f'c{cycle:02d}.md'
                    assert archive.read_bytes() == (folder / 'HANDOFF.md').read_bytes(), where
                    assert not (folder / '.hq.lock').exists(), where
                    _, opened = _run(['open', _SLUG])
                    for bad in ('WARNING', 'LEDGER BEHIND', 'block sha mismatch'):
                        assert bad not in opened, (where, opened)
                    hq.main(['begin', _SLUG])
            assert (folder / 'ledger.tsv').read_bytes().startswith(ledger_before), where
            assert (folder / 'standing.md').read_bytes().startswith(standing_before), where


def _assert_dangling_named(folder, out, where):
    live = hq.latest_rows(_rows(folder))
    for path, row in live.items():
        successor = row['successor']
        if row['status'] == 'superseded' and successor != '-' \
                and not (folder / successor).is_file():
            assert f'successor missing: {path} -> {successor}' in out, (where, out)


def test_live_sha_prefers_the_rewrite_and_falls_back_to_the_archive():
    """live_sha() returns rewrite_sha where set, else handoff_sha.

    Mutation: the precedence flipped, so an adopt row answers with the
    archive digest and every begin after adopt saves a .hand.md copy; or
    the fallback dropped, so a finish row or a row written before the
    column existed answers `-` and every begin after finish saves one.
    Oracle: hand-computed - three rows, one per branch.
    """
    assert hq.live_sha({'handoff_sha': 'a' * 12, 'rewrite_sha': 'b' * 12}) == 'b' * 12
    assert hq.live_sha({'handoff_sha': 'a' * 12, 'rewrite_sha': '-'}) == 'a' * 12
    assert hq.live_sha({'handoff_sha': 'a' * 12}) == 'a' * 12
