"""Tests for residency in the rendered blocks (FEATURE-standing-residency).

A resident line carries an index of the item and the command that prints
the rest. The store keeps every byte, so each test here pins what the
render holds back and what the expand command says, never what the store
drops.
"""

import pathlib
from typing import Any

from bin import hq

_SLUG = 'test-slug'
_NOW = '2026-09-09T12:00:00'


def _folder(tmp_path: Any, monkeypatch: Any, cycle: str | None = '1') -> pathlib.Path:
    """Create the handoff folder and set every env var; return its path.

    Parameters
    ----------
    tmp_path : Any
        Pytest temporary path; HQ_ROOT is placed at tmp_path / 'root'.
    monkeypatch : Any
        Active pytest monkeypatch fixture.
    cycle : str | None, default '1'
        HQ_CYCLE override, or None to leave the cycle to the manifest so a
        test can run more than one cycle.

    Returns
    -------
    pathlib.Path
        The handoff folder, created on disk.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    if cycle is not None:
        monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', 'session-abc')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    return folder


def _item(item_id, prefix, headline, body, cycle='1'):
    """Build one standing item dict for the renderer.
    """
    return {
        'id': item_id, 'prefix': prefix, 'cycle': cycle,
        'headline': headline, 'body': body,
        }


def _row(**kwargs):
    """Build a ledger Row with every field defaulted.
    """
    base = {
        'cycle': '1', 'ts': _NOW, 'path': 'notes/n.md', 'base': 'folder',
        'kind': 'notes', 'status': 'live', 'read_before': 'edit',
        'successor': '-', 'where': '-', 'sha12': 'aabbccddeeff',
        'lines': '100', 'reason': '-', 'label': 'a note',
        }
    base.update(kwargs)
    return base


def test_a_constraint_body_past_its_first_sentence_is_held_back():
    """Verify a long constraint body renders one sentence and names its id.

    Mutation: the constraint row rendered with its whole body, as the old
    include_body=True did; or the holdback command naming a literal
    '<id>' placeholder, which no reader can fill from the file.
    Oracle: hand-computed - a two-sentence body renders the first
    sentence alone, the held count equals the second sentence's exact
    length, and the command's last token is the item's own id.
    """
    first = 'The processor treats a bare retry as a new charge.'
    second = 'Cycle 2 reproduced a double charge at 1,400 ms.'
    lines = hq.render_standing(
        [_item('c07', 'c', 'Never retry without the key', f'{first} {second}')],
        set(), 'demo').splitlines()
    assert lines == [
        '### Constraints',
        (f'[c07] (c1) **Never retry without the key** {first}'
         f'  +{len(second)}c - hq standing demo c07'),
        ]
    assert lines[1].split()[-1] == 'c07'
    assert '<id>' not in lines[1]


def test_a_constraint_body_with_no_sentence_end_stays_resident_whole():
    """Verify a body the split cannot cut renders whole with no suffix.

    Mutation: truncating at a fixed width instead of a sentence end,
    which would cut such a body mid-word; or emitting a '+0c' suffix for
    a body that holds nothing back.
    Oracle: hand-computed - a 167-character body with no terminal
    punctuation renders byte for byte after the headline, and the line
    carries no '+'. The length is what makes the test bite: a body under
    any fixed cut would render whole under a width rule too.
    """
    body = (
        'read the quirks note section four before changing any retry path, '
        'and the runbook step eleven which repeats that section word for '
        'word and is the one an operator opens')
    lines = hq.render_standing(
        [_item('c01', 'c', 'Read the quirks note first', body)],
        set(), 'demo').splitlines()
    assert len(body) == 167
    assert lines[1] == f'[c01] (c1) **Read the quirks note first** {body}'
    assert '+' not in lines[1]


def test_an_edit_label_is_resident_to_the_cap_and_names_hq_when():
    """Verify a long label renders capped, at a space, naming its path.

    Mutation: the cap raised or dropped, so the artifacts block grows
    with the label mean again; the cut taken mid-word; or the command
    naming 'hq artifacts <slug>', which prints every row rather than
    this path's own history.
    Oracle: hand-computed against literal numbers, never against the
    constant under test - 60 four-letter words separated by single spaces
    make a 299-character label whose last space inside 120 characters
    sits at index 119, so 119 characters are resident and 180 are held.
    A cap at any other width lands on a different space.
    """
    label = ' '.join(['word'] * 60)
    assert len(label) == 299
    lines = hq.render_artifacts(
        [('notes/n.md', 'notes')],
        {'notes/n.md': _row(label=label)},
        'demo').splitlines()
    row = next(ln for ln in lines if ln.startswith('notes/n.md'))
    assert row == (
        f'notes/n.md  notes  edit  c1  {label[:119]}'
        '  +180c - hq when demo notes/n.md')
    assert hq.resident_label(label) == label[:119]


def test_a_label_whose_only_space_is_early_keeps_the_whole_width():
    """Verify the word-boundary cut yields to the width when it would gut it.

    Mutation: cutting at the last space unconditionally, so a label whose
    only space sits near its start renders three characters instead of
    120; or the half-width guard written with >= so a space at exactly
    half the width still takes the cut.
    Oracle: hand-computed - 'abc ' then 236 unbroken characters keeps 120
    characters, not the 3 before its only space.
    """
    label = 'abc ' + 'x' * 236
    assert hq.resident_label(label) == label[:hq._LABEL_RESIDENT]
    boundary = 'y' * (hq._LABEL_RESIDENT // 2) + ' ' + 'z' * 120
    assert hq.resident_label(boundary) == boundary[:hq._LABEL_RESIDENT]


def test_open_stays_quiet_on_a_label_the_render_capped(tmp_path, monkeypatch, capsys):
    """Verify the label-drift guard compares the resident form, not the whole.

    Mutation: the guard comparing the block's label to the whole ledger
    label, which reports LEDGER BEHIND on every row whose label runs past
    the cap - on every open, for a block the renderer wrote correctly.
    Oracle: a 299-character label stamped and rendered by the script
    itself, asserted present in the block as a capped line before the
    check; open over that untouched block prints no LEDGER BEHIND.
    """
    folder = _folder(tmp_path, monkeypatch)
    (folder / 'notes').mkdir()
    (folder / 'notes' / 'n.md').write_text('# N\n\nbody\n', encoding='utf-8')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'stamp', _SLUG, 'notes/n.md', '--read-before', 'edit',
        '--label', ' '.join(['word'] * 60)]) == 0
    assert hq.main(['finish', _SLUG, '--log', 'one']) == 0
    # A row seeded 'never' collapses to a count and gives the guard
    # nothing to compare, so the capped line has to be on the block.
    art = hq.split_handoff(
        (folder / 'HANDOFF.md').read_text())['blocks']['artifacts']
    assert '+180c - hq when' in art
    capsys.readouterr()
    monkeypatch.setenv('HQ_SESSION', 'session-xyz')
    assert hq.main(['open', _SLUG]) == 0
    assert 'LEDGER BEHIND' not in capsys.readouterr().out


def test_standing_filters_select_and_a_named_id_outranks_them(
        tmp_path, monkeypatch, capsys):  # noqa: PLR0915
    """Verify --kind, --grep and --in-cycle select, and ids beat a filter.

    Mutation: --kind mapped to the wrong prefix letter; --grep searching
    the headline alone, so a body-only match is lost; --in-cycle matching
    a substring so cycle 1 also returns cycle 11; or the filters applied
    to a named id, which would print nothing for an id the caller asked
    for by name.
    Oracle: items seeded across cycles 1 and 11 with hand-computed
    expected id sets. The cycle pair is what bites: '1' is a substring of
    '11', so a containment test returns both where equality returns one.
    """
    folder = _folder(tmp_path, monkeypatch)
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'note', _SLUG, 'constraint', '--headline', 'Never log the token',
        'Not even at debug level.']) == 0
    assert hq.main([
        'note', _SLUG, 'decision', '--headline', 'Refresh in process',
        'One caller, and the latency is fine.']) == 0
    assert hq.main([
        'note', _SLUG, 'dead-end', '--headline', 'Event hooks',
        'A hook cannot retry the request.']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'one']) == 0
    monkeypatch.setenv('HQ_CYCLE', '11')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'note', _SLUG, 'constraint', '--headline', 'Never reuse a nonce',
        'The verifier caches each nonce it accepts.']) == 0
    capsys.readouterr()

    def ids(argv):
        assert hq.main(['standing', _SLUG] + argv) == 0
        out = capsys.readouterr().out
        return [ln[1:ln.index(']')] for ln in out.splitlines() if ln.startswith('[')]

    assert ids(['--kind', 'constraint']) == ['c01', 'c02']
    assert ids(['--kind', 'decision']) == ['d01']
    assert ids(['--kind', 'dead-end']) == ['x01']
    # 'retry' appears in the dead end's body and in no headline.
    assert ids(['--grep', 'retry']) == ['x01']
    assert ids(['--in-cycle', '1']) == ['c01', 'd01', 'x01']
    assert ids(['--in-cycle', '11']) == ['c02']
    assert ids(['--in-cycle', '2']) == []
    assert ids(['--kind', 'constraint', '--in-cycle', '11']) == ['c02']
    assert ids(['d01', '--kind', 'constraint']) == ['d01']
    assert (folder / 'standing.md').exists()


def test_standing_refuses_a_grep_that_is_not_a_pattern(tmp_path, monkeypatch, capsys):
    """Verify a bad --grep exits 2 and names the move.

    Mutation: re.error escaping as a traceback, or the refusal returning
    0 so a caller reads an empty listing as 'no items match'.
    Oracle: '[' is an unterminated character class; the exit code and the
    move clause in the printed line.
    """
    _folder(tmp_path, monkeypatch)
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()
    assert hq.main(['standing', _SLUG, '--grep', '[']) == 2
    out = capsys.readouterr().out
    assert 'hq standing: --grep is not a regular expression' in out
    assert ' - fix the pattern' in out


def test_finish_prints_the_payload_delta_against_the_last_cycle(
        tmp_path, monkeypatch, capsys):
    """Verify finish prints the signed token change, and none on cycle one.

    Mutation: the sign dropped, so a cut in the file reads as growth; the
    previous figure read from the first manifest row instead of the last;
    or the line printed on the first cycle, where there is nothing to
    compare and the figure would be the whole file.
    Oracle: the two size lines finish prints itself - cycle 2's delta
    equals its own token count minus cycle 1's, recomputed from the
    printed numbers.
    """
    _folder(tmp_path, monkeypatch, cycle=None)
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['finish', _SLUG, '--log', 'one']) == 0
    first = capsys.readouterr().out
    assert 'tok since c' not in first
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'note', _SLUG, 'constraint', '--headline', 'Never log the token',
        'Not even at debug level.']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'two']) == 0
    middle = capsys.readouterr().out
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'note', _SLUG, 'constraint', '--headline', 'Never reuse a nonce',
        'The verifier caches every nonce it has accepted.']) == 0
    assert hq.main(['finish', _SLUG, '--log', 'three']) == 0
    second = capsys.readouterr().out

    def tokens(text):
        line = next(ln for ln in text.splitlines() if ' cursor lines  ' in ln)
        return int(line.split(' cursor lines  ')[1].split(' ')[0])

    delta = next(ln for ln in second.splitlines() if 'tok since c' in ln)
    assert delta == f'{tokens(second) - tokens(middle):+d} tok since c2'
    assert delta.startswith('+')
    # The first row of the manifest is cycle 1, so a guard that read it
    # rather than the last would print this larger figure instead.
    assert delta != f'{tokens(second) - tokens(first):+d} tok since c2'


def test_the_standing_superseded_count_names_a_command_that_returns_them(
        tmp_path, monkeypatch, capsys):
    """Verify the superseded count names a command printing those items.

    Mutation: the count line ending in 'hq standing <slug>' without
    --all, which exits 0 and lists the live items instead, so a count
    points at text its own command never returns.
    Oracle: the command parsed out of the rendered line and run as
    printed against a store whose c01 really is superseded; its output
    must carry [c01].
    """
    _folder(tmp_path, monkeypatch)
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main([
        'note', _SLUG, 'constraint', '--headline', 'Never reuse a nonce',
        'The verifier caches each nonce it accepts.']) == 0
    assert hq.main([
        'note', _SLUG, 'constraint', '--headline', 'Never reuse a key',
        'The signer caches each key it accepts.']) == 0
    assert hq.main(['supersede', _SLUG, 'c01', 'c02']) == 0
    capsys.readouterr()

    items = [
        _item('c01', 'c', 'Never reuse a nonce', 'The verifier caches it.'),
        _item('c02', 'c', 'Never reuse a key', 'The signer caches it.'),
        ]
    line = hq.render_standing(items, {'c01'}, _SLUG).splitlines()[-1]
    assert line.startswith('superseded 1')

    argv = line.split('- ', 1)[1].split()
    assert '<' not in ' '.join(argv)
    assert hq.main(argv[1:]) == 0
    assert '[c01]' in capsys.readouterr().out


def test_every_non_live_artifact_count_names_a_command_that_runs_as_printed(
        tmp_path, monkeypatch, capsys):
    """Verify each non-live count ends in a command returning that status.

    Mutation: the three counts joined into one line ending in
    'hq when <slug> <path>', a placeholder no reader can fill and a verb
    that answers for one path rather than for a status.
    Oracle: each command parsed out of its own line and run; the path
    stamped at that status appears in its output and the paths stamped at
    the other two do not.
    """
    folder = _folder(tmp_path, monkeypatch)
    at_status = {
        'superseded': 'old.md',
        'archived': 'done.md',
        'missing': 'gone.md',
        }
    rows = {}
    for status, path in at_status.items():
        rows[path] = _row(path=path, status=status, label=f'{status} row')
        hq._append_tsv(
            folder / 'ledger.tsv', hq.LEDGER_FIELDS, rows[path],
            hq._LEDGER_HEADER)
    capsys.readouterr()

    block = hq.render_artifacts([], rows, _SLUG)
    counted = [
        ln for ln in block.splitlines() if ln.startswith(tuple(at_status))]
    assert len(counted) == len(at_status)

    for line in counted:
        status = line.split()[0]
        argv = line.split('- ', 1)[1].split()
        assert '<' not in ' '.join(argv)
        assert hq.main(argv[1:]) == 0
        out = capsys.readouterr().out
        assert at_status[status] in out
        for other, other_path in at_status.items():
            if other != status:
                assert other_path not in out
