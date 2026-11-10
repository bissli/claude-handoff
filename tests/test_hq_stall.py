"""Tests for the stall count: the summary grammar, the progress test, and
how finish, open and begin store, enforce and report it.
"""

import contextlib
import decimal
import io
import pathlib

from bin import hq

_SLUG = 'stall-slug'
_SESSION = 'session-st'
_HOST = 'test-host'
_NOW = '2026-11-10T12:00:00'
_STALL_DUE = (
    'hq finish: the stall question is due - 3 cycles without progress and'
    ' this one shows none. Put the question to the user, then carry stall'
    ' review: <ruling> first after the summary tokens')
_OPEN_LINE = (
    'stall: {n} cycles without progress since c{k}'
    f' - hq arc {_SLUG} shows the summaries')
_SUFFIX_TWO = (
    ' - close a unit on another workable - [ ] this cycle where one exists')
_SUFFIX_THREE = (
    ' - work this cycle first; put the stall question the Stall Directive'
    " names only if hq finish refuses, or once the cycle's measure shows"
    ' no progress')
_BEGIN_LINE = (
    "  stall: 3 cycles without progress - this cycle's --log shows progress,"
    ' or carries stall review: <ruling> first after the summary tokens once'
    ' the user has answered the stall question')


def _root(tmp_path, monkeypatch):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    monkeypatch.delenv('HQ_LOOP', raising=False)
    return root / '.handoff' / _SLUG


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _cycles(logs):
    out = ''
    for log in logs:
        assert _run(['begin', _SLUG])[0] == 0
        rc, out, _ = _run(['finish', _SLUG, '--log', log])
        assert rc == 0, out
    return out


def _manifest(folder):
    return hq._read_tsv(folder / 'cycles' / 'manifest.tsv', hq.MANIFEST_FIELDS)


def _live_loop(tmp_path, monkeypatch):
    loop_dir = tmp_path / 'loop'
    loop_dir.mkdir()
    monkeypatch.setenv('HQ_LOOP', str(loop_dir))


def _stalls(logs):
    """The stored stall of each row, as finish computes it row by row."""
    rows = [{'cycle': str(i + 1), 'log': log} for i, log in enumerate(logs)]
    return [hq.stall_count(rows[:i + 1])['stall'] for i in range(len(rows))]


def _counting(series, name='a', goal='0'):
    return [f'summary: {name}={value}->{goal}' for value in series]


_STREAK_THREE = _counting([5, 5, 5, 5])


# --- grammar ---------------------------------------------------------


def test_each_of_the_four_forms_parses():
    """Verify counting, gate, tally and spend each parse to their kind.

    Mutation: the tally form read as counting, or one form's pattern
    dropped.
    Oracle: hand-written token map for a line holding all four forms.
    """
    tokens, free_text, error = hq.parse_summary(
        'summary: a=31->0 g=MET h=OPEN t=+45 s=40/300')
    D = decimal.Decimal
    assert error is None
    assert free_text == ''
    assert tokens == {
        'a': ('counting', (D(31), D(0))),
        'g': ('gate', True),
        'h': ('gate', False),
        't': ('tally', D(45)),
        's': ('spend', None),
        }


def test_trailing_punctuation_is_stripped():
    """Verify a trailing '.', ',' or ';' leaves the token parsing.

    Mutation: the strip dropped, so tests=MET. voids the row.
    Oracle: the same tokens written with each trailing mark.
    """
    tokens, _, error = hq.parse_summary('summary: a=1->0, b=+2; tests=MET.')
    assert error is None
    assert set(tokens) == {'a', 'b', 'tests'}


def test_a_failing_token_voids_the_row():
    """Verify a token with '=' that matches no form makes the row unparsed.

    Mutation: the failing token skipped as free text, so open_sites=12
    passes.
    Oracle: a pre-grammar bare count beside a good token.
    """
    tokens, _, error = hq.parse_summary('summary: a=1->0 open_sites=12 done')
    assert tokens is None
    assert error == '--log token open_sites=12 matches no summary form'


def test_free_text_starts_at_the_first_word_without_equals():
    """Verify a token-shaped word inside the free text is ignored.

    Mutation: tokens read across the whole line.
    Oracle: x=1->0 sits after the word 'then'.
    """
    tokens, free_text, error = hq.parse_summary('summary: a=5->0 then x=1->0')
    assert error is None
    assert set(tokens) == {'a'}
    assert free_text == 'then x=1->0'


def test_the_prefix_matches_any_case_after_leading_space():
    """Verify '  Summary:' starts a summary row.

    Mutation: a case-sensitive match, or no leading space allowed.
    Oracle: the capitalized prefix after two spaces.
    """
    tokens, _, error = hq.parse_summary('  Summary: a=1->0')
    assert error is None
    assert set(tokens) == {'a'}
    assert hq.parse_summary('a=1->0')[2] == (
        'under hq loop --log starts with summary: and its tokens')


def test_spend_and_tally_alone_do_not_parse():
    """Verify a row needs one counting or gate token, wherever spend sits.

    Mutation: spend or tally counted toward the one counting or gate
    token a row needs.
    Oracle: a spend-and-tally row, and the same spend first beside a
    counting token.
    """
    tokens, _, error = hq.parse_summary('summary: s=40/300 t=+4')
    assert tokens is None
    assert error == '--log summary holds no counting or gate token'
    assert hq.parse_summary('summary: s=40/300 a=1->0')[2] is None


def test_the_last_occurrence_of_a_name_wins():
    """Verify a repeated name keeps its last token, across kinds.

    Mutation: the first occurrence wins.
    Oracle: a=5->0 then a=3->0, and a=5->0 then a=MET.
    """
    D = decimal.Decimal
    assert hq.parse_summary('summary: a=5->0 a=3->0')[0] == {
        'a': ('counting', (D(3), D(0)))}
    assert hq.parse_summary('summary: a=5->0 a=MET')[0] == {'a': ('gate', True)}


def test_goals_compare_as_numbers():
    """Verify a goal written 95 then 95.0 is one goal.

    Mutation: goals compared as text, so the second row reads as a
    changed goal and adds 1.
    Oracle: 96->95 then 95.5->95.0 moves half a unit toward one goal.
    """
    assert _stalls(['summary: a=96->95', 'summary: a=95.5->95.0']) == [0, 0]


def test_pre_grammar_rows():
    """Verify spend=40/300 reads as spend and a bare count voids the row.

    Mutation: a bare number accepted as counting.
    Oracle: the two pre-grammar shapes the spec names.
    """
    tokens, _, error = hq.parse_summary('summary: tests=MET spend=40/300')
    assert error is None
    assert tokens['spend'] == ('spend', None)
    assert hq.parse_summary('summary: open_sites=12 tests=MET')[0] is None


# --- progress test ---------------------------------------------------


def test_a_falling_series_is_progress_at_any_length():
    """Verify every new low resets the streak, however long the run.

    Mutation: the distance comparison reversed.
    Oracle: 300 falling by 50 to 0, all stored 0.
    """
    assert _stalls(_counting(range(300, -1, -50))) == [0] * 7


def test_an_orbit_counts_against_the_best():
    """Verify 12, 11, 12, 11 stores 0, 0, 1, 2.

    Mutation: the comparison made against the previous row in place of
    the best, so 11 after 12 reads as progress.
    Oracle: the spec's hand-computed series.
    """
    assert _stalls(_counting([12, 11, 12, 11])) == [0, 0, 1, 2]


def test_a_value_crossing_the_goal_is_progress():
    """Verify 94.9->95 then 95.2->95 is progress.

    Mutation: a two-sided distance, so 0.2 past the goal reads worse
    than 0.1 short of it.
    Oracle: the side fixed at the first entry, below the goal.
    """
    rows = [
        {'cycle': '1', 'log': 'summary: a=94.9->95'},
        {'cycle': '2', 'log': 'summary: a=95.2->95'},
        ]
    assert hq.stall_count(rows)['row_class'] == 'progress'


def test_a_held_row_leaves_the_streak():
    """Verify a row of held 0->0 tokens neither adds nor resets.

    Mutation: held classed as stuck, or as progress.
    Oracle: a streak of 1 from a stuck b, then rows of a held a alone.
    """
    logs = ['summary: a=0->0 b=5->0', 'summary: a=0->0 b=5->0']
    assert _stalls(logs + ['summary: a=0->0'] * 2) == [0, 1, 1, 1]


def test_a_changing_goal_adds_one_per_row():
    """Verify 10->0, 10->5, 10->9 adds 1 per row.

    Mutation: the goal ignored once state exists, so a goal moved
    toward the value reads as improved.
    Oracle: the value never moves; only the goal does.
    """
    assert _stalls(['summary: a=10->0', 'summary: a=10->5', 'summary: a=10->9']) == [
        0, 1, 2]


def test_renamed_coordinates_add_one_per_row():
    """Verify a row of new names after the first parsed row is non-progress.

    Mutation: a row of new tokens classed neutral.
    Oracle: three rows, each under a fresh name.
    """
    assert _stalls(['summary: a=5->0', 'summary: b=5->0', 'summary: c=5->0']) == [
        0, 1, 2]


def test_a_ruling_row_resets_the_streak_and_every_best():
    """Verify a ruling row stores 0, parsed or not, and restarts the bests.

    Mutation: the streak reset with the state kept, so a value worse
    than the old best reads as stuck after the ruling.
    Oracle: best 2 before the ruling, 8 at it, then 6.
    """
    stuck = _counting([10, 2, 5, 5, 5])
    parsed = stuck + ['summary: a=8->0 stall review: split it', 'summary: a=6->0']
    assert _stalls(parsed)[-3:] == [3, 0, 0]
    unparsed = stuck + [
        'summary: open_sites=12 stall review: drop it',
        'summary: a=9->0', 'summary: a=8->0']
    assert _stalls(unparsed)[-3:] == [0, 0, 0]


def test_a_stray_marker_resets_nothing():
    """Verify a marker mid free text, or under a carried streak of 2, is text.

    Mutation: the marker matched anywhere in the log, or the
    carried-streak condition dropped.
    Oracle: the streak counted on past each stray marker.
    """
    mid = _STREAK_THREE + ['summary: a=5->0 note stall review: x']
    assert _stalls(mid)[-1] == 4
    early = _counting([5, 5, 5]) + ['summary: a=5->0 stall review: x']
    assert _stalls(early)[-1] == 3


def test_an_unparsed_row_stores_nothing_and_leaves_the_streak():
    """Verify an unparsed row stores None and the next row counts on.

    Mutation: an unparsed row resets the streak or adds to it.
    Oracle: streak 1, a plain-text row, then one more stuck row.
    """
    logs = _counting([5, 5]) + ['wrote the plan', 'summary: a=5->0']
    assert _stalls(logs) == [0, 1, None, 2]
    result = hq.stall_count([
        {'cycle': str(i + 1), 'log': log} for i, log in enumerate(logs[:3])])
    assert result['reported'] == 1


def test_a_settling_growth_phase_is_progress_in_every_row():
    """Verify 5 units closing per cycle beats growth in R.

    Mutation: the tally vote dropped, so R rising reads as stuck.
    Oracle: the spec's R and T series.
    """
    remaining = [20, 30, 37, 40, 39, 34]
    tally = [0, 5, 10, 15, 20, 25]
    logs = [f'summary: r={r}->0 t=+{t}' for r, t in zip(remaining, tally)]
    assert _stalls(logs) == [0] * 6


def test_churn_never_beats_the_tally_maximum():
    """Verify close-then-reopen churn votes once.

    Mutation: the tally compared against the previous row in place of
    the window maximum.
    Oracle: T 5, 6, 5, 6, 5, 6 beside R 5, 4, 5, 4, 5, 4 from the spec.
    """
    logs = [
        f'summary: r={r}->0 t=+{t}'
        for r, t in zip([5, 4, 5, 4, 5, 4], [5, 6, 5, 6, 5, 6])]
    assert _stalls(logs) == [0, 0, 1, 2, 3, 4]


def test_a_pure_discovery_row_is_non_progress():
    """Verify R rising with T flat adds 1.

    Mutation: '>=' in place of '>' on the tally.
    Oracle: T at its maximum, R up by two.
    """
    assert _stalls(['summary: r=5->0 t=+3', 'summary: r=7->0 t=+3']) == [0, 1]


def test_finished_items_are_neutral_unless_beside_a_stuck_one():
    """Verify 0->0 with a flat tally is neutral, and beside a stuck item is not.

    Mutation: a silent tally counted against the neutral test, or a row
    of silent tallies beside a stuck token classed neutral.
    Oracle: the same finished item with and without a stuck b.
    """
    done = ['summary: a=0->0 t=+5'] * 3
    assert _stalls(done) == [0, 0, 0]
    beside = ['summary: a=0->0 t=+5 b=3->0'] * 3
    assert _stalls(beside) == [0, 1, 2]


def test_a_tally_first_entry_is_silent():
    """Verify a tally's first entry casts no vote, after a ruling as well.

    Mutation: the first entry compared against 0, so t=+3 reads as
    improved.
    Oracle: a stuck a beside a tally seen for the first time.
    """
    assert _stalls(['summary: a=5->0', 'summary: a=5->0 t=+3']) == [0, 1]
    ruled = _STREAK_THREE + [
        'summary: a=5->0 stall review: keep going', 'summary: a=5->0 t=+3']
    assert _stalls(ruled)[-2:] == [0, 1]


def test_a_name_that_changes_kind_is_stuck():
    """Verify a kind change is stuck in both directions.

    Mutation: a tally over counting state classed silent.
    Oracle: a held gate g beside b changing kind.
    """
    assert _stalls(['summary: g=MET b=4->0', 'summary: g=MET b=+9']) == [0, 1]
    assert _stalls(['summary: g=MET b=+9', 'summary: g=MET b=4->0']) == [0, 1]


# --- finish, open, begin ---------------------------------------------


def test_finish_counts_its_pending_log(tmp_path, monkeypatch):
    """Verify finish stores the streak with the pending log counted.

    Mutation: the result computed over the stored rows alone, so the
    progress row stores the streak of 3 before it.
    Oracle: three stuck rows then a new low stores 0.
    """
    folder = _root(tmp_path, monkeypatch)
    _cycles(_STREAK_THREE)
    out = _cycles(['summary: a=4->0'])
    assert [row['stall'] for row in _manifest(folder)] == ['0', '1', '2', '3', '0']
    lines = out.splitlines()
    path_idx = next(i for i, ln in enumerate(lines) if 'cursor lines' in ln)
    assert lines[path_idx + 1] == 'stall: 0'


def test_open_recomputes_the_stall_line(tmp_path, monkeypatch):
    """Verify open prints the carried streak over an unparsed newest row.

    Mutation: open reads the newest row's stored field in place of
    recomputing, so the '-' row hides the streak.
    Oracle: rows store 0, 1, 2 and '-'; the streak since c1 is 2.
    """
    folder = _root(tmp_path, monkeypatch)
    _cycles(_counting([5, 5, 5]) + ['wrote notes'])
    assert [row['stall'] for row in _manifest(folder)] == ['0', '1', '2', '-']
    _, out, _ = _run(['open', _SLUG])
    assert _OPEN_LINE.format(n=2, k=1) in out.splitlines()


def test_open_thresholds_and_suffixes(tmp_path, monkeypatch):
    """Verify no line at 1, the first suffix at 2, the second at 3.

    Mutation: a threshold off by one, or the suffixes swapped.
    Oracle: the spec's three suffix cases, read with and without a live
    HQ_LOOP.
    """
    _root(tmp_path, monkeypatch)
    _cycles(_counting([5, 5]))
    _, out, _ = _run(['open', _SLUG])
    assert 'stall:' not in out
    _cycles(_counting([5]))
    _live_loop(tmp_path, monkeypatch)
    _, out, _ = _run(['open', _SLUG])
    assert _OPEN_LINE.format(n=2, k=1) + _SUFFIX_TWO in out.splitlines()
    monkeypatch.delenv('HQ_LOOP')
    _cycles(_counting([5]))
    _, out, _ = _run(['open', _SLUG])
    assert _OPEN_LINE.format(n=3, k=1) in out.splitlines()
    monkeypatch.setenv('HQ_LOOP', str(tmp_path / 'loop'))
    _, out, _ = _run(['open', _SLUG])
    lines = out.splitlines()
    assert _OPEN_LINE.format(n=3, k=1) + _SUFFIX_THREE in lines
    assert lines.index(_OPEN_LINE.format(n=3, k=1) + _SUFFIX_THREE) > lines.index(
        'hq loop: no one is watching this session')


def test_begin_prints_its_line_on_an_armed_thread_at_three(tmp_path, monkeypatch):
    """Verify begin's stall line needs an armed thread and a streak of 3.

    Mutation: the armed test dropped, or the threshold moved.
    Oracle: the same logs with the stall column blanked, which leaves
    the thread unarmed, and a streak of 2.
    """
    folder = _root(tmp_path, monkeypatch)
    _cycles(_counting([5, 5, 5]))
    _, out, _ = _run(['begin', _SLUG])
    assert 'stall:' not in out
    _run(['finish', _SLUG, '--log', 'summary: a=5->0'])
    _, out, _ = _run(['begin', _SLUG])
    lines = out.splitlines()
    assert lines[lines.index(_BEGIN_LINE) + 1].startswith('cycle 5 begun by')
    _run(['finish', _SLUG, '--log', 'summary: a=5->0'])
    manifest_path = folder / 'cycles' / 'manifest.tsv'
    rows = manifest_path.read_text().splitlines()
    blanked = [rows[0]] + [
        '\t'.join(row.split('\t')[:14] + ['-', '-']) for row in rows[1:]]
    manifest_path.write_text('\n'.join(blanked) + '\n')
    _, out, _ = _run(['begin', _SLUG])
    assert 'stall:' not in out


# --- refusals --------------------------------------------------------


def test_an_unparsed_log_is_refused_under_the_loop(tmp_path, monkeypatch):
    """Verify each unparsed cause exits 1, keeps the lock and writes nothing.

    Mutation: the refusal placed after the first write, or the lock
    removed.
    Oracle: the folder's bytes before and after each refused finish.
    """
    folder = _root(tmp_path, monkeypatch)
    _cycles(['summary: a=5->0'])
    _live_loop(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    before = {
        p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    forms = ' - forms: name=31->0 name=MET name=OPEN name=+45 name=40/300'
    for log, cause in (
            ('wrote the plan',
             'under hq loop --log starts with summary: and its tokens'),
            ('summary: a=4->0 open_sites=12',
             '--log token open_sites=12 matches no summary form'),
            ('summary: s=40/300', '--log summary holds no counting or gate token'),
            ):
        rc, out, _ = _run(['finish', _SLUG, '--log', log])
        assert rc == 1
        assert out.splitlines()[-1] == f'hq finish: {cause}{forms}'
        after = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        assert after == before
    assert (folder / '.hq.lock').is_file()


def test_no_refusal_off_the_loop_or_unarmed(tmp_path, monkeypatch):
    """Verify finish refuses nothing unarmed, without HQ_LOOP, or with a gone dir.

    Mutation: the armed test dropped, or HQ_LOOP read as a plain flag;
    or finish prints no stall line for an unparsed row on an armed thread.
    Oracle: rc 0 in each of the three cases with an unparsed log.
    """
    folder = _root(tmp_path, monkeypatch)
    _live_loop(tmp_path, monkeypatch)
    assert 'stall:' not in _cycles(['wrote the plan', 'more notes'])
    monkeypatch.delenv('HQ_LOOP')
    assert 'stall: -' in _cycles(['summary: a=5->0', 'plain']).splitlines()
    monkeypatch.setenv('HQ_LOOP', str(tmp_path / 'gone'))
    _cycles(['plain again'])
    assert [row['stall'] for row in _manifest(folder)] == ['-', '-', '0', '-', '-']


def test_the_stall_question_is_due_at_three(tmp_path, monkeypatch):
    """Verify the refusal at a carried 3, the ruling and progress that pass it.

    Mutation: the threshold moved, the marker accepted in any position,
    or a progress row refused.
    Oracle: streak 3 stored by four stuck rows; a carried 2 stores 3.
    """
    folder = _root(tmp_path, monkeypatch)
    _cycles(_counting([5, 5, 5]))
    _live_loop(tmp_path, monkeypatch)
    _cycles(['summary: a=5->0'])
    assert _manifest(folder)[-1]['stall'] == '3'
    assert _run(['begin', _SLUG])[0] == 0
    for log in ('summary: a=5->0', 'summary: a=5->0 note stall review: x'):
        rc, out, _ = _run(['finish', _SLUG, '--log', log])
        assert rc == 1
        assert out.splitlines()[-1] == _STALL_DUE
    rc, out, _ = _run([
        'finish', _SLUG, '--log', 'summary: a=5->0 stall review: split a'])
    assert rc == 0, out
    assert _manifest(folder)[-1]['stall'] == '0'
    _cycles(_counting([5, 5, 5]))
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'summary: a=4->0'])[0] == 0
    assert _manifest(folder)[-1]['stall'] == '0'


def test_a_neutral_row_at_three_is_refused(tmp_path, monkeypatch):
    """Verify a held-only pending row at a carried 3 is refused.

    Mutation: the refusal tests non-progress in place of not progress.
    Oracle: b held at 0->0 alone, after a stuck a built the streak.
    """
    folder = _root(tmp_path, monkeypatch)
    _cycles(['summary: a=5->0 b=0->0'] * 4)
    assert _manifest(folder)[-1]['stall'] == '3'
    _live_loop(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'summary: b=0->0'])
    assert rc == 1
    assert out.splitlines()[-1] == _STALL_DUE


# --- old manifest ----------------------------------------------------


def test_old_manifests_take_the_new_columns_by_position(tmp_path, monkeypatch):
    """Verify rewrite_sha stays field 14 and stall and window land at 15, 16.

    Mutation: stall placed before rewrite_sha.
    Oracle: an adopt row read by live_sha, and finish over a manifest cut
    to 13 and to 14 fields, whose window opens at the adopt cycle.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-10-01 | Cycle: 3\n\n'
        '## Task\n\nDo the work.\n\n## Now\n\nNext step.\n')
    assert _run(['adopt', _SLUG])[0] == 0
    manifest_path = folder / 'cycles' / 'manifest.tsv'
    adopt_fields = manifest_path.read_text().splitlines()[1].split('\t')
    assert len(adopt_fields) == 16
    assert adopt_fields[14:] == ['-', '-']
    assert hq.live_sha(_manifest(folder)[0]) == adopt_fields[13]
    assert hq._sha12((folder / 'HANDOFF.md').read_bytes()) == adopt_fields[13]
    for width, stall in ((13, '0'), (14, '1')):
        rows = manifest_path.read_text().splitlines()
        manifest_path.write_text('\n'.join(
            [rows[0]] + ['\t'.join(row.split('\t')[:width]) for row in rows[1:]])
            + '\n')
        _cycles(['summary: a=5->0'])
        fields = manifest_path.read_text().splitlines()[-1].split('\t')
        assert len(fields) == 16
        assert fields[14:] == [stall, adopt_fields[0]]
