"""Tests for the stall count: the summary grammar, the progress test, and
how finish and open store and report it.
"""

import contextlib
import decimal
import io
import pathlib

from handoff import cli, progress, storage

_SLUG = 'stall-slug'
_SESSION = 'session-st'
_HOST = 'test-host'
_NOW = '2026-11-10T12:00:00'
_OPEN_LINE = (
    'stall: {n} cycles without progress ({cause})'
    ' - close a unit on another workable item')
D = decimal.Decimal


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
            rc = cli.main(argv)
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
    return storage.read_tsv(folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)


def _stalls(logs):
    """The stored stall of each row, as finish computes it row by row.

    A log with no bar is composed with an empty log text.
    """
    rows = [
        {'cycle': str(i + 1), 'log': log if ' | ' in log else f'{log} | -'}
        for i, log in enumerate(logs)]
    return [progress.stall_count(rows[:i + 1])['stall'] for i in range(len(rows))]


def _counting(series, name='a', goal='0'):
    return [f'summary: {name}={value}->{goal} | -' for value in series]


def _probe(folder, body):
    """Write an executable probes/hq-measure running body under sh."""
    probe = folder / 'probes' / 'hq-measure'
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(f'#!/bin/sh\n{body}\n')
    probe.chmod(0o755)


def _measured(folder, lines, log='work'):
    """Finish one cycle per probe token line, each with the same log."""
    out = ''
    for line in lines:
        _probe(folder, f"echo '{line}'")
        assert _run(['begin', _SLUG])[0] == 0
        rc, out, _ = _run(['finish', _SLUG, '--log', log])
        assert rc == 0, out
    return out


_STREAK_THREE = _counting([5, 5, 5, 5])


# --- grammar ---------------------------------------------------------


def test_each_of_the_four_forms_parses():
    """Verify counting parses with and without a step, a tally's stored step
    is ignored, and gate and spend fail.

    Mutation: the tally form read as counting, the step dropped from the
    counting form or kept on a tally, or a removed gate or spend form
    left in.
    Oracle: hand-written token map for a line holding all four forms.
    """
    tokens, free_text, error = progress.parse_summary(
        'summary: a=31->0 s=72->80@1 t=+45 u=+45@5 | fixed x')
    assert error is None
    assert free_text == 'fixed x'
    assert tokens == {
        'a': ('counting', (D(31), D(0), None)),
        's': ('counting', (D(72), D(80), D(1))),
        't': ('tally', (D(45), None)),
        'u': ('tally', (D(45), None)),
        }
    for gone in ('g=MET', 'h=OPEN', 's=40/300'):
        assert progress.parse_summary(f'summary: a=1->0 {gone} | x')[0] is None


def test_a_failing_token_voids_the_row():
    """Verify a failed probe row and a token matching no form both void it.

    Mutation: the probe-failed prefix read as a token, or a failing token
    skipped, so open_sites=12 passes.
    Oracle: a failed row with its cause, and a bare count beside a good
    token.
    """
    assert progress.parse_summary('summary: probe-failed: exited 2: boom | x') == (
        None, 'x', 'probe-failed: exited 2: boom')
    tokens, _, error = progress.parse_summary('summary: a=1->0 open_sites=12 | done')
    assert tokens is None
    assert error == 'token open_sites=12 matches no summary form'


def test_the_prefix_matches_any_case_after_leading_space():
    """Verify '  Summary:' starts a summary row.

    Mutation: a case-sensitive match, or no leading space allowed.
    Oracle: the capitalized prefix after two spaces.
    """
    tokens, _, error = progress.parse_summary('  Summary: a=1->0')
    assert error is None
    assert set(tokens) == {'a'}
    assert progress.parse_summary('a=1->0')[2] == 'the row does not start with summary:'


def test_spend_and_tally_alone_do_not_parse():
    """Verify a row of tallies alone fails, and parses beside a counting token.

    Mutation: a tally counted toward the one counting token a row needs.
    Oracle: two tallies, one stepped, then the same tally beside a=1->0.
    """
    tokens, _, error = progress.parse_summary('summary: t=+4 u=+2@1 | x')
    assert tokens is None
    assert error == 'the summary holds no counting token'
    assert progress.parse_summary('summary: t=+4 a=1->0 | x')[2] is None


def test_the_last_occurrence_of_a_name_wins():
    """Verify a repeated name keeps its last token.

    Mutation: the first occurrence wins.
    Oracle: a=5->0 then a=3->0, and a=5->0 then a=3->0@1.
    """
    assert progress.parse_summary('summary: a=5->0 a=3->0')[0] == {
        'a': ('counting', (D(3), D(0), None))}
    assert progress.parse_summary('summary: a=5->0 a=3->0@1')[0] == {
        'a': ('counting', (D(3), D(0), D(1)))}


def test_the_counting_check_reads_the_tokens_last_wins_leaves():
    """Verify a counting token a later tally of its name replaces voids the row.

    Mutation: the counting check run over every token the row names,
    before a repeated name keeps its last token.
    Oracle: a=5->0 a=+3 leaves the tally alone; a=+3 a=5->0 leaves the
    counting token.
    """
    tokens, _, error = progress.parse_summary('summary: a=5->0 a=+3')
    assert tokens is None
    assert error == 'the summary holds no counting token'
    assert progress.parse_summary('summary: a=+3 a=5->0')[0] == {
        'a': ('counting', (D(5), D(0), None))}


def test_goals_compare_as_numbers():
    """Verify a goal written 95 then 95.0 is one goal.

    Mutation: goals compared as text, so the second row reads as a
    changed goal and adds 1.
    Oracle: 96->95 then 95.5->95.0 moves half a unit toward one goal.
    """
    assert _stalls(['summary: a=96->95', 'summary: a=95.5->95.0']) == [0, 0]


def test_pre_grammar_rows():
    """Verify a bare count voids the row.

    Mutation: a bare number accepted as counting.
    Oracle: the pre-grammar shape beside a good token.
    """
    assert progress.parse_summary('summary: open_sites=12 a=1->0')[0] is None


def test_tokens_end_at_the_first_bar():
    """Verify the tokens stop at the first bar.

    Mutation: tokens read past the bar.
    Oracle: b=9->0 sits after the bar.
    """
    tokens, free_text, _ = progress.parse_summary('summary: a=1->0 | b=9->0')
    assert set(tokens) == {'a'}
    assert free_text == 'b=9->0'


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
        {'cycle': '1', 'log': 'summary: a=94.9->95 | -'},
        {'cycle': '2', 'log': 'summary: a=95.2->95 | -'},
        ]
    assert progress.stall_count(rows)['stall'] == 0


def test_stepped_gains_add_up_to_a_credit():
    """Verify a stepped name credits once its gains since the best reach the
    step.

    Mutation: '<' for '<=', or best moved on a stuck vote.
    Oracle: score=72->80@1, then 72.5 stuck, 73 improved.
    """
    logs = [f'summary: score={value}->80@1' for value in ('72', '72.5', '73')]
    assert _stalls(logs) == [0, 1, 0]


def test_a_stored_tally_step_is_ignored():
    """Verify a tally stored with a step credits any rise.

    Mutation: the stored step kept on the tally rule, so 46 reads silent.
    Oracle: done=+45@5, then 46 improved, beside a stuck a.
    """
    logs = [f'summary: a=5->0 done=+{value}@5' for value in (45, 46)]
    assert _stalls(logs) == [0, 0]


def test_a_changed_step_restarts_the_name():
    """Verify a changed step votes stuck.

    Mutation: the step left out of the restart test.
    Oracle: @1 then @2 on one name, the value a full step better.
    """
    assert _stalls(['summary: a=5->0@1', 'summary: a=3->0@2']) == [0, 1]


def test_side_waits_for_the_first_entry_off_goal():
    """Verify side is set at the first entry off goal, not at an at-goal one.

    Mutation: side set at an at-goal first entry.
    Oracle: a=0->0, then a=-2->0 is no progress.
    """
    assert _stalls(['summary: a=0->0', 'summary: a=-2->0']) == [0, 1]


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
    """Verify a composed ruling stores 0 at any streak and restarts the bests.

    Mutation: the streak reset with the state kept, so a value worse
    than the old best reads as stuck after the ruling; or a carried-streak
    guard on the ruling.
    Oracle: best 2 before the ruling, 8 at it, then 6; and a ruling at a
    carried 1.
    """
    stuck = _counting([10, 2, 5, 5, 5])
    parsed = stuck + ['summary: a=8->0 | stall review: split it', 'summary: a=6->0']
    assert _stalls(parsed)[-3:] == [3, 0, 0]
    early = _counting([5, 5]) + ['summary: a=8->0 | stall review: x', 'summary: a=6->0']
    assert _stalls(early) == [0, 1, 0, 0]
    unparsed = stuck + [
        'summary: open_sites=12 | stall review: drop it',
        'summary: a=9->0', 'summary: a=8->0']
    assert _stalls(unparsed)[-3:] == [0, 0, 0]


def test_a_ruling_reads_at_any_carried_streak():
    """Verify a ruling on a measured, unmeasured or failed row resets at 1.

    Mutation: the carried >= 3 or tokens != {} guard kept, or a failed
    ruling row counted as no progress.
    Oracle: a streak of 1, then each kind of ruling row stores 0.
    """
    for row in ('a=5->0', 'unmeasured', 'probe-failed: exited 2: x'):
        logs = _counting([5, 5]) + [f'summary: {row} | stall review: go']
        assert _stalls(logs) == [0, 1, 0], row


def test_stall_review_after_the_second_bar_is_text():
    """Verify stall review: after the second bar is no ruling.

    Mutation: the ruling read anywhere in the row.
    Oracle: three stuck rows, then the marker after a second bar.
    """
    logs = _STREAK_THREE + ['summary: a=5->0 | x | stall review: y']
    assert _stalls(logs)[-1] == 4


def test_a_failed_row_is_no_progress():
    """Verify a failed probe row adds one to the streak.

    Mutation: the failed row read as neutral.
    Oracle: a measured row, then two failed rows store 1 and 2.
    """
    logs = _counting([5]) + ['summary: probe-failed: ran past 60 s | -'] * 2
    assert _stalls(logs) == [0, 1, 2]


def test_rows_without_a_bar_are_history():
    """Verify a row with no bar is skipped and the first composed row is
    the window's first row.

    Mutation: an old typed row parsed as composed.
    Oracle: three stuck old typed rows, then a composed row stores 0 and
    is the first measured cycle.
    """
    rows = [{'cycle': str(i), 'log': 'summary: a=5->0'} for i in (1, 2, 3)]
    rows.append({'cycle': '4', 'log': 'summary: a=5->0 | x'})
    result = progress.stall_count(rows)
    assert (result['stall'], result['measured']) == (0, 4)


def test_a_stray_marker_resets_nothing():
    """Verify a marker mid free text is text.

    Mutation: the marker matched anywhere after the first bar.
    Oracle: the streak counted on past the stray marker.
    """
    mid = _STREAK_THREE + ['summary: a=5->0 | note stall review: x']
    assert _stalls(mid)[-1] == 4


def test_an_unparsed_row_stores_nothing_and_leaves_the_streak():
    """Verify an unparsed row stores None and the next row counts on.

    Mutation: an unparsed row resets the streak or adds to it.
    Oracle: streak 1, a composed row that does not parse, then one more
    stuck row.
    """
    logs = _counting([5, 5]) + ['summary: open_sites=12 | wrote', 'summary: a=5->0']
    assert _stalls(logs) == [0, 1, None, 2]
    result = progress.stall_count([
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
        'summary: a=5->0 | stall review: keep going', 'summary: a=5->0 t=+3']
    assert _stalls(ruled)[-2:] == [0, 1]


def test_a_no_probe_ruling_row_resets_the_streak():
    """Verify the stored no-probe ruling row zeroes the count for a later probe.

    Mutation: a ruling read only on a row with tokens, so the unmeasured
    ruling row is skipped and a restored probe's first row continues the
    streak.
    Oracle: hand-counted stuck rows 0,1,2,3 on one name, the ruling row,
    then the same stuck value again.
    """
    ruled = _STREAK_THREE + [
        'summary: unmeasured | stall review: drop the probe | work',
        'summary: a=5->0 | work']
    assert _stalls(ruled) == [0, 1, 2, 3, 0, 0]


def test_a_name_that_changes_kind_is_stuck():
    """Verify a kind change is stuck in both directions and restarts the name.

    Mutation: a tally over counting state classed silent, or the kind
    test dropped, so a counting token read over tally state keeps it.
    Oracle: a held z beside b changing kind each way.
    """
    assert _stalls(['summary: z=0->0 b=4->0', 'summary: z=0->0 b=+9']) == [0, 1]
    assert _stalls(['summary: z=0->0 b=+9', 'summary: z=0->0 b=4->0']) == [0, 1]


# --- finish and open -------------------------------------------------


def test_finish_counts_its_pending_log(tmp_path, monkeypatch):
    """Verify finish stores the streak with the pending row counted.

    Mutation: the result computed over the stored rows alone, so the
    progress row stores the streak of 3 before it.
    Oracle: three stuck rows then a new low stores 0.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    out = _measured(folder, ['a=5->0'] * 4 + ['a=4->0'])
    assert [row['stall'] for row in _manifest(folder)] == ['0', '1', '2', '3', '0']
    lines = out.splitlines()
    path_idx = next(i for i, ln in enumerate(lines) if 'cursor lines' in ln)
    assert lines[path_idx + 1] == 'stall: 0'


def test_open_recomputes_the_stall_line(tmp_path, monkeypatch):
    """Verify open prints the carried streak over a newest row with no probe.

    Mutation: open reads the newest row's stored field in place of
    recomputing, so the '-' row hides the streak.
    Oracle: rows store 0, 1, 2 and '-'; the streak is 2.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    _measured(folder, ['a=5->0'] * 3)
    (folder / 'probes' / 'hq-measure').unlink()
    _cycles(['wrote notes'])
    assert [row['stall'] for row in _manifest(folder)] == ['0', '1', '2', '-']
    _probe(folder, "echo 'a=5->0'")
    _, out, _ = _run(['open', _SLUG])
    assert _OPEN_LINE.format(n=2, cause='no measured gain') in out.splitlines()


# --- old manifest ----------------------------------------------------


def test_old_manifests_take_the_new_columns_by_position(tmp_path, monkeypatch):
    """Verify rewrite_sha stays field 14 and stall lands at 15.

    Mutation: stall placed before rewrite_sha, or a row with the old
    window column read past its stall.
    Oracle: an adopt row read by live_sha, and finish over a manifest cut
    to 13 and to 14 fields, then one with a sixteenth field.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    (folder / 'HANDOFF.md').write_text(
        f'# Handoff: {_SLUG}\n\nWritten: 2026-10-01 | Cycle: 3\n\n'
        '## Task\n\nDo the work.\n\n## Now\n\nNext step.\n')
    assert _run(['adopt', _SLUG])[0] == 0
    manifest_path = folder / 'cycles' / 'manifest.tsv'
    adopt_fields = manifest_path.read_text().splitlines()[1].split('\t')
    assert len(adopt_fields) == 15
    assert adopt_fields[14:] == ['-']
    assert storage.live_sha(_manifest(folder)[0]) == adopt_fields[13]
    assert storage.sha12((folder / 'HANDOFF.md').read_bytes()) == adopt_fields[13]
    _probe(folder, "echo 'a=5->0'")
    for width, stall in ((13, '0'), (14, '1'), (16, '2')):
        rows = manifest_path.read_text().splitlines()
        old_rows = []
        for row in rows[1:]:
            row_fields = row.split('\t')[:width]
            if width == 16 and len(row_fields) == 15:
                row_fields.append('9')
            old_rows.append('\t'.join(row_fields))
        manifest_path.write_text('\n'.join([rows[0]] + old_rows) + '\n')
        _cycles(['work'])
        fields = manifest_path.read_text().splitlines()[-1].split('\t')
        assert len(fields) == 15
        assert fields[14:] == [stall]


# --- unmeasured rows -------------------------------------------------


def test_unmeasured_parses_with_no_tokens():
    """Verify summary: unmeasured parses empty and its free text follows it.

    Mutation: the word read as a token, so the row is unparsed; or the
    match made case-sensitive.
    Oracle: hand-written parse of two unmeasured lines.
    """
    assert progress.parse_summary('summary: unmeasured') == ({}, '', None)
    assert progress.parse_summary('Summary: UNMEASURED | stall review: x') == (
        {}, 'stall review: x', None)


def test_unmeasured_rows_leave_the_streak():
    """Verify unmeasured rows leave the streak, and store - before a measure.

    Mutation: unmeasured classed non-progress, so a costly measure
    stalls the thread; or it takes the window's first slot, so the first
    measured row votes against nothing.
    Oracle: hand-computed rows 0, 0, 0, 1 and -, -, 0, 1.
    """
    assert _stalls(
        ['summary: a=5->0'] + ['summary: unmeasured'] * 2 + ['summary: a=5->0']
        ) == [0, 0, 0, 1]
    assert _stalls(
        ['summary: unmeasured'] * 2 + ['summary: a=5->0', 'summary: a=5->0']
        ) == [None, None, 0, 1]


def test_a_row_with_no_numeric_cycle_counts_toward_nothing():
    """Verify a row whose cycle is '-' or empty is skipped whole.

    Mutation: such a row read as cycle 0, so it opens the window or adds
    to the streak.
    Oracle: stuck rows at cycles '-' and '' after c1 leave the streak at
    0; the same rows before c7 leave c7 the window's first row.
    """
    stuck = 'summary: a=5->0 | -'
    skipped = [{'cycle': '-', 'log': stuck}, {'cycle': '', 'log': stuck}]
    after = progress.stall_count([{'cycle': '1', 'log': stuck}] + skipped)
    assert (after['stall'], after['measured']) == (0, 1)
    before = progress.stall_count(skipped + [{'cycle': '7', 'log': stuck}])
    assert (before['measured'], before['stall']) == (7, 0)
