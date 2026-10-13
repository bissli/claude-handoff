"""Tests for the finish advisory on cursor citations of superseded ids."""

import contextlib
import io
import pathlib
import re

from bin import hq

_SLUG = 'cited'


def _root(tmp_path, monkeypatch):
    """Create an HQ_ROOT with the HQ_* environment set; return the folder."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', '2026-10-13T12:00:00')
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


def _thread(tmp_path, monkeypatch, kind, count, supersessions):
    """Begin a cycle holding count items of kind and the given supersessions.

    Returns the folder, with Task set and the lock held for finish.
    """
    folder = _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    for n in range(1, count + 1):
        assert _run([
            'note', _SLUG, kind, '--headline', f'Retry rule {n}',
            f'The poller retries {n} times.'])[0] == 0
    for old_id, new_id in supersessions:
        assert _run(['supersede', _SLUG, old_id, new_id])[0] == 0
    _set(folder, 'Task', 'Probe the cited-id advisory.')
    return folder


def _advisories(out):
    """The cited-id advisory lines finish printed, in order."""
    return [ln for ln in out.splitlines() if ln.startswith('advisory: cited id')]


def test_a_two_hop_chain_names_the_id_now_current(tmp_path, monkeypatch):
    """Verify the advisory names the id at the end of the chain.

    Mutation: the advisory reporting chain[1], the first successor, which
    is itself superseded.
    Oracle: d01 -> d02 -> d03 built by two supersede calls; d03 is the
    one live id.
    """
    folder = _thread(
        tmp_path, monkeypatch, 'decision', 3, [('d01', 'd02'), ('d02', 'd03')])
    _set(folder, 'Now', 'Apply d01 to the poller.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _advisories(out) == [
        'advisory: cited id superseded: d01 -> d03 - cite d03 in its place']


def test_a_bare_constraint_id_reads_as_a_cycle(tmp_path, monkeypatch):
    """Verify a bare c id draws nothing and a bracketed one draws the advisory.

    Mutation: c ids scanned bare like d ids, so a cycle number such as
    c01 in prose reads as a citation.
    Oracle: the same superseded c01, bare in one cycle and bracketed in
    the next.
    """
    folder = _thread(tmp_path, monkeypatch, 'constraint', 2, [('c01', 'c02')])
    _set(folder, 'Now', 'Since c01 the poller has retried; (c01) too.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _advisories(out) == []
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert _run(['begin', _SLUG])[0] == 0
    _set(folder, 'Now', 'Obey [c01] in the poller.')
    rc, out = _run(['finish', _SLUG, '--log', 'c2'])
    assert rc == 0
    assert _advisories(out) == [
        'advisory: cited id superseded: c01 -> c02 - cite c02 in its place']


def test_a_bracket_group_checks_its_standing_ids_alone(tmp_path, monkeypatch):
    """Verify a group mixing a foreign token and a standing id checks the id.

    Mutation: a group skipped when any token is not a standing id, or a
    group of id-shaped tokens refused for the m prefix.
    Oracle: [m14, c01] with c01 superseded by c02; m14 is no kind.
    """
    folder = _thread(tmp_path, monkeypatch, 'constraint', 2, [('c01', 'c02')])
    _set(folder, 'Now', 'Measure the rate per [m14, c01].')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _advisories(out) == [
        'advisory: cited id superseded: c01 -> c02 - cite c02 in its place']


def test_a_superseded_id_in_directives_draws_nothing(tmp_path, monkeypatch):
    """Verify the scan skips the Directives section.

    Mutation: the scan reading the whole cursor, Directives included.
    Oracle: a superseded d01 cited in Directives alone and nowhere else.
    """
    folder = _thread(tmp_path, monkeypatch, 'decision', 2, [('d01', 'd02')])
    _set(folder, 'Directives', '- Follow [d01] for every retry.')
    _set(folder, 'Now', 'Wire the retry into the poller.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _advisories(out) == []


def test_each_id_reports_once_in_order_of_first_citation(tmp_path, monkeypatch):
    """Verify a repeated citation reports once, ordered by first citation.

    Mutation: one line per citation; or the ids reported in id order or
    set order.
    Oracle: Now cites d03 then d01, Plan cites d01 again; d01 -> d02 and
    d03 -> d04.
    """
    folder = _thread(
        tmp_path, monkeypatch, 'decision', 4, [('d01', 'd02'), ('d03', 'd04')])
    _set(folder, 'Now', 'Apply d03, then d01.')
    _set(folder, 'Plan', '- [ ] Recheck d01 against the poller')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _advisories(out) == [
        'advisory: cited id superseded: d03 -> d04 - cite d04 in its place',
        'advisory: cited id superseded: d01 -> d02 - cite d02 in its place']


def test_a_looping_chain_draws_nothing(tmp_path, monkeypatch):
    """Verify a chain that loops back on itself names no live end.

    Mutation: the advisory naming the last id the walk reached, d02,
    which is itself superseded.
    Oracle: d01 -> d02 by supersede and d02 -> d01 written by hand, as a
    hand edit of the append-only file can.
    """
    folder = _thread(tmp_path, monkeypatch, 'decision', 2, [('d01', 'd02')])
    with (folder / 'standing.md').open('a') as standing:
        standing.write('- (c1) d02 -> d01\n')
    _set(folder, 'Now', 'Apply d01 to the poller.')
    rc, out = _run(['finish', _SLUG, '--log', 'c1'])
    assert rc == 0
    assert _advisories(out) == []
