"""hq arc: the whole thread in one read, and the lines that route to it.

The arc prints where the thread began, one History line per finished
cycle, and where it stands: Now, the open questions, and the open Plan
items, each by its first sentence. The Log block's rollup line names
it, and the help text sends a question about the thread overall to it.
"""
import contextlib
import io
import pathlib

from bin import hq

_SLUG = 'arc-slug'


def _root(tmp_path, monkeypatch):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', '2026-09-24T12:00:00')
    monkeypatch.setenv('HQ_SESSION', 'session-arc')
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    return root


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


def _snapshot(base):
    return {
        path: path.read_bytes() for path in sorted(base.rglob('*'))
        if path.is_file()}


def test_arc_prints_each_section_by_its_first_sentence():
    """render_arc prints the title, Began as, History, Now, questions, open items.

    Mutation: Began as printed when the Task is unchanged or dropped when
    it moved; a wrapped Task, question, or Plan item cut at its first
    line; a done item listed as open; a nested item losing its indent;
    bold markup kept; a block marker read as a question; or a History row
    dropped or reordered.
    Oracle: the hand-written arc of a three-cycle thread.
    """
    manifest = [
        {'cycle': '1', 'written': '2026-09-01', 'log': 'token store written'},
        {'cycle': '2', 'written': '2026-09-02', 'log': 'refresh shipped in 1a2b3c4'},
        {'cycle': '3', 'written': '2026-09-03', 'log': 'backoff capped at 60s'},
        ]
    first_text = (
        '# Handoff: poller-auth\n\nWritten: 2026-09-01 | Cycle: 1\n\n'
        '## Task\nStore OAuth tokens on disk. Encrypt them later.\n\n'
        '## Now\nWrite the store.\n')
    handoff_text = (
        '# Handoff: poller-auth\n\nWritten: 2026-09-03 | Cycle: 3\n\n'
        '## Task\nRefresh expired OAuth tokens in the poller instead of\n'
        'failing the run. The store stays on disk.\n\n'
        '## Now\nWire **refresh_token()** into poll() at scripts/auth.py:88.\n'
        'Then run the unit tests.\n\n'
        '## Plan\n'
        '- [x] Token store and refresh endpoint. Shipped.\n'
        '- [ ] Wire refresh into the poll() 401 branch, keeping the\n'
        '      retry schedule. Tests follow.\n'
        '  - [ ] Cover the expired refresh token.\n'
        '- [ ] Integration test against the staging IdP.\n\n'
        '## Open questions\n'
        '- Cap retry backoff at 60s, or give up after five\n'
        '  tries? Blocks the integration test.\n'
        '- Which IdP tenant hosts staging?\n\n'
        '<!-- hq:read 0123456789ab -->\n'
        '## Read first\n'
        '- [ ] not a plan item.\n'
        '<!-- /hq:read -->\n')
    assert hq.render_arc('poller-auth', manifest, handoff_text, first_text) == (
        '# Arc: poller-auth  cycles 1-3, 2026-09-01 to 2026-09-03\n'
        'Began as (c1): Store OAuth tokens on disk.\n'
        'Task: Refresh expired OAuth tokens in the poller instead of failing'
        ' the run.\n'
        'History:\n'
        '- c1 2026-09-01  token store written\n'
        '- c2 2026-09-02  refresh shipped in 1a2b3c4\n'
        '- c3 2026-09-03  backoff capped at 60s\n'
        'Now: Wire refresh_token() into poll() at scripts/auth.py:88.\n'
        'Open questions (2):\n'
        '- Cap retry backoff at 60s, or give up after five tries?\n'
        '- Which IdP tenant hosts staging?\n'
        'Plan, open (3):\n'
        '- Wire refresh into the poll() 401 branch, keeping the retry'
        ' schedule.\n'
        '  - Cover the expired refresh token.\n'
        '- Integration test against the staging IdP.\n')


def test_an_adopted_thread_prints_its_prior_log_before_the_history():
    """The prior Log of an adopted thread prints, and only that section.

    Mutation: Before omitted for an adopted thread or printed without the
    adoption log; blank Log lines kept; the section after ## Log read as
    Log; the archive named cycles/c2.md; Began as printed for an
    unchanged Task; or an Open questions header printed for a section
    holding no item.
    Oracle: the hand-written arc of a thread adopted at cycle 2.
    """
    manifest = [
        {'cycle': '2', 'written': '2026-08-20',
         'log': 'adopted; prior Log: 2 lines in cycles/c02.md'},
        {'cycle': '3', 'written': '2026-08-21', 'log': 'second sync run'},
        ]
    first_text = (
        '# Handoff: legacy-sync\n\nWritten: 2026-08-20 | Cycle: 2\n\n'
        '## Task\nSync the legacy rows.\n\n'
        '## Log\n- 2026-08-18: schema mapped\n\n- 2026-08-19: first sync run\n'
        '## Notes\n- not a log line\n')
    handoff_text = (
        '# Handoff: legacy-sync\n\nWritten: 2026-08-21 | Cycle: 3\n\n'
        '## Task\nSync the legacy rows.\n\n'
        '## Now\nRun the third sync.\n\n'
        '## Open questions\n\n')
    assert hq.render_arc('legacy-sync', manifest, handoff_text, first_text) == (
        '# Arc: legacy-sync  cycles 2-3, 2026-08-20 to 2026-08-21\n'
        'Task: Sync the legacy rows.\n'
        'Before c2 (the prior Log, cycles/c02.md):\n'
        '- 2026-08-18: schema mapped\n'
        '- 2026-08-19: first sync run\n'
        'History:\n'
        '- c2 2026-08-20  adopted; prior Log: 2 lines in cycles/c02.md\n'
        '- c3 2026-08-21  second sync run\n'
        'Now: Run the third sync.\n'
        'Plan, open (0):\n')


def test_the_rollup_line_names_an_arc_command_that_runs_and_writes_nothing(
        tmp_path, monkeypatch):
    """The Log's rollup line runs as printed, lists every cycle, and writes nothing.

    Mutation: finish passing a name other than the folder's to the rollup
    line; arc added to the thread verbs, so a second session's arc writes
    a thread record; arc writing a receipt or any file; or History
    dropping a cycle.
    Oracle: four hand-run cycles, the command cut from the rendered line,
    and a byte snapshot of the root and the state directory.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    for cycle in range(1, 5):
        assert _run(['begin', _SLUG])[0] == 0
        _cursor(folder, f'## Task\nShip the poller.\n\n## Now\nStep {cycle}.\n')
        assert _run(['finish', _SLUG, '--log', f'step {cycle} shipped'])[0] == 0
    log = hq.split_handoff((folder / 'HANDOFF.md').read_text())['log']
    rollup = log.strip().splitlines()[-1]
    assert rollup == f'- cycles 1-1 - hq arc {_SLUG}'
    command = rollup.split(' - ')[-1].split()
    assert command[0] == 'hq'
    monkeypatch.setenv('HQ_SESSION', 'session-other')
    before = _snapshot(tmp_path)
    rc, out = _run(command[1:])
    assert rc == 0
    assert _snapshot(tmp_path) == before
    history = out[out.index('History:\n'):out.index('Now:')].splitlines()[1:]
    assert history == [
        f'- c{n} 2026-09-24  step {n} shipped' for n in range(1, 5)]


def test_arc_on_a_thread_with_no_finished_cycle_exits_1(tmp_path, monkeypatch):
    """A begun thread with no manifest row gets a refusal naming HANDOFF.md.

    Mutation: the empty-manifest guard dropped, so the first row's lookup
    raises; or the guard returning 0 with no text.
    Oracle: a thread whose one cycle is begun and never finished.
    """
    _root(tmp_path, monkeypatch)
    assert _run(['begin', _SLUG])[0] == 0
    rc, out = _run(['arc', _SLUG])
    assert rc == 1
    assert out == f'hq arc: {_SLUG} has no finished cycle - read HANDOFF.md\n'


def test_the_help_text_routes_the_thread_overall_to_arc():
    """Help read sends a named item to hq standing and the overall arc to arc;
    help write and the finish epilog make --log the arc's entry.

    Mutation: the routing bullet dropped, or its item clause dropped so
    every question about the past goes to hq arc; or the --log rule
    dropped from help write or the finish epilog.
    Oracle: the clauses of the routing bullet and of the --log rule.
    """
    read_text = ' '.join(hq.HELP_TOPICS['read'].split())
    assert (
        'An item the Standing block names prints whole with'
        ' hq standing <slug> <id>. Where the thread started, how it got here,'
        ' or where it stands overall goes to hq arc <slug> first') in read_text
    parser = hq._build_parser()
    finish = next(
        action for action in parser._subparsers._group_actions
        ).choices['finish']
    for text in (hq.HELP_TOPICS['write'], finish.epilog):
        assert 'whole entry in hq arc' in ' '.join(text.split())
        assert 'check: <n> findings applied - <what they changed>' in (
            ' '.join(text.split()))
