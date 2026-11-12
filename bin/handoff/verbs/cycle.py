"""Verbs that write inside a cycle: begin, stamp, note, supersede, done.
"""

import argparse
import contextlib
import io
import os
import pathlib
import re
import shlex
import sys

from handoff import help_text, ledger, markdown, paths, standing, storage
from handoff import threads
from handoff.verbs import adopt

_NOTE_BATCH_LINE = re.compile(
    r'^(?P<kind>decision|constraint|dead-end|evidence)\s+'
    r'(?P<opts>(?:(?:--grade\s+\w+|--open|--contradicts\s+\w+)\s+)*)'
    r'--headline\s+'
    r'(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|(?P<bare>[^"\'\s]\S*))'
    r'(?P<body>.*)$')


# The characters str.splitlines() breaks on, plus tab. Any of these in a
# ledger field would yield phantom rows when read_tsv calls splitlines().
_SPLITLINES_CHARS = '\t\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029'


def _take_lock(
    folder: pathlib.Path,
    anch: dict,
    argv: argparse.Namespace,
    seeded_handoff: bool) -> int:
    """Acquire .hq.lock, refusing a young or unreadable foreign lock.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder the lock sits in.
    anch : dict
        Anchors for this invocation; supplies session, host, time, cycle.
    argv : argparse.Namespace
        Parsed flags; ``--force`` takes over any lock.
    seeded_handoff : bool
        True where this run seeded HANDOFF.md itself, which skips the
        rescue copy.

    Returns
    -------
    int
        0 once the lock is this session's, 1 on a refusal.

    Notes
    -----
    - Creation is ``O_CREAT | O_EXCL``, so of two begins racing for an
      absent lock exactly one creates it and the other re-reads and
      applies the same rules.
    - A lock this session already holds is rewritten, carrying its
      ``takeover`` forward, so a second begin still reaches finish with
      the takeover recorded.
    - A lock taken over but not writable is replaced whole, so a lock
      whose permissions were lost cannot hold the folder forever.
    - A taken lock prints the work list, then ``hq help write``: the
      writing rules reach the agent when it writes, a subagent that
      never loaded the skill included, and the skill need not carry
      them for the whole session.
    """
    lock_path = folder / '.hq.lock'
    session = anch['session']
    force = getattr(argv, 'force', False)
    created = True
    fd = -1
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        created = False
    takeover_from = ''
    if not created:
        refusal = storage.lock_refusal(folder, anch, force, 'begin')
        if refusal:
            print(refusal)
            return 1
        lock = storage.read_lock(folder)
        if lock.get('session') == session:
            takeover_from = lock.get('takeover', '')
        else:
            takeover_from = lock.get('session') or 'unknown'
            print(f'hq begin: took over from {takeover_from}')
            if storage.lock_age(lock, anch['now']) is not None:
                print(
                    f'cycle {lock.get("cycle")} begun by {takeover_from}'
                    f' on {lock.get("host")} at {lock.get("time")},'
                    ' never finished')
    fields: dict[str, str] = {
        'slug': folder.name,
        'session': session,
        'host': anch['host'],
        'time': anch['now'],
        'cycle': str(anch['cycle']),
        }
    if takeover_from:
        fields['takeover'] = takeover_from
    content = '\n'.join(f'{k}={v}' for k, v in fields.items()) + '\n'
    if created:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(content)
    else:
        try:
            lock_path.write_text(content, encoding='utf-8')
        except PermissionError:
            lock_path.unlink()
            lock_path.write_text(content, encoding='utf-8')

    # Notes:
    # - A hand edit since the last finish or adopt: HANDOFF.md no
    #   longer hashes to what that write left in it.
    # - live_sha reads the rewrite an adopt row records, not the
    #   archive its handoff_sha names.
    # - A file this run seeded is no hand edit: its stub carries
    #   nothing to fold back, and the name keys on the last finished
    #   cycle, whose genuine rescue it would overwrite.
    manifest_rows = storage.read_tsv(
        folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
    handoff = folder / 'HANDOFF.md'
    if manifest_rows and handoff.exists() and not seeded_handoff:
        last = manifest_rows[-1]
        if storage.sha12_path(handoff) != storage.live_sha(last):
            hand_path = folder / 'cycles' / f'c{int(last["cycle"]):02d}.hand.md'
            hand_path.write_bytes(handoff.read_bytes())
            print(
                f'hq begin: HANDOFF.md changed since last finish;'
                f' saved to {hand_path.name}'
                ' - fold anything wanted back into the cursor')

    _print_worklist(folder, anch)
    print(help_text.HELP_TOPICS['write'])
    return 0


def _print_worklist(folder: pathlib.Path, anch: dict) -> None:
    """Print unstamped files, sha-moved, and missing rows.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to report on.
    anch : dict
        Anchors for this invocation; supplies the closing cycle line.

    Notes
    -----
    - Every class stops at five names and closes with ``... and N more``,
      so a sixth item is counted rather than dropped.
    - A ``skip`` walk entry is a conflicted copy only when its name says
      so; every other ``skip`` entry - a name carrying a tab, carriage
      return, or newline, or a non-regular file - prints as unstampable,
      the separator escaped.
    - The closing lines name the cycle just opened and, where the file
      disagrees, the stale ``Cycle:`` field beside it: ``finish`` writes
      that field last, so the header reads the previous cycle for the
      whole write and is never the number to attribute work to.
    """
    walk, rows, live, sha_map, manifest, lb, sb = ledger.folder_state(folder)
    for b in storage.witness(manifest[-1] if manifest else None, lb, sb):
        print(f'  {b}')
    unstamped = [
        (n, k) for n, k in walk
        if not ledger.is_recorded(n, live) and k != 'skip']
    by_kind: dict[str, list[str]] = {}
    for n, k in unstamped:
        by_kind.setdefault(k, []).append(n)
    for k, names in sorted(by_kind.items()):
        shown = ', '.join(names[:5])
        tail = f' ... and {len(names) - 5} more' if len(names) > 5 else ''
        print(f'  unstamped {k} x{len(names)}: {shown}{tail} - stamp each')
    for n, k in walk:
        if k != 'skip':
            continue
        if 'conflicted copy' in n:
            print(f'  conflicted copy: {n} - a sync duplicate; resolve it by hand')
        else:
            shown = n.replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
            print(
                f'  unstampable name: {shown}'
                ' - rename or remove it, or leave it out of the ledger')
    moved = ledger.check_r3(
        list(ledger.reconcile_missing(folder, live).values()), sha_map)
    slug = folder.name
    for row in moved[:5]:
        print(
            f'  sha moved: {row["path"]}'
            f' - re-read it (hq read {slug} {row["path"]}), then re-stamp')
    if len(moved) > 5:
        print(f'  ... and {len(moved) - 5} more')
    missing_live = []
    for path, row in live.items():
        if row['status'] != 'live':
            continue
        if row['base'] == 'abs':
            if not pathlib.Path(path).expanduser().exists():
                missing_live.append(path)
        elif not (folder / path).exists():
            missing_live.append(path)
    for path in missing_live[:5]:
        print(f'  missing live: {path} - hq when {folder.name} {path}')
    if len(missing_live) > 5:
        print(f'  ... and {len(missing_live) - 5} more')
    # A row the ledger stores as missing - a Key files pointer adopt
    # found nowhere - is otherwise a count inside the Artifacts block; a
    # plain re-stamp carries the status forward, so the line names the
    # move that clears it.
    missing_rows = [p for p, r in live.items() if r['status'] == 'missing']
    for path in missing_rows[:5]:
        print(
            f'  missing: {path} - stamp --status live if it is back,'
            ' or --successor / --archive --reason')
    if len(missing_rows) > 5:
        print(f'  ... and {len(missing_rows) - 5} more')
    dangling = ledger.dangling_successors(folder, live)
    for path, successor in dangling[:5]:
        print(
            f'  successor missing: {path} -> {successor}'
            ' - name a new successor or archive the row')
    if len(dangling) > 5:
        print(f'  ... and {len(dangling) - 5} more')
    # An always row with no anchor is a whole file read at every
    # resume; stamp refuses its next re-stamp until it conforms, so an
    # older thread converges as its files are touched.
    for path, row in live.items():
        if (row['status'] == 'live' and row['read_before'] == 'always'
                and row['where'] == '-'):
            print(
                f'  {path} always with no anchor, {row["lines"]} lines'
                ' - stamp --where <heading> or --read-before edit')
    wd_value, wd_lines = paths.resolve_work_dir(folder)
    for line in wd_lines:
        print(line)
    print(paths.work_dir_line(folder, wd_value, first_cycle=anch['cycle'] == 1))
    print(f'cycle {anch["cycle"]} begun by {anch["session"]} on {anch["host"]}')
    # The Cycle line is the previous cycle's until finish rewrites it at
    # the end, so an agent that takes its number from the file credits
    # this cycle's work to the one that assigned it.
    handoff_path = folder / 'HANDOFF.md'
    if handoff_path.is_file():
        cycle = anch['cycle']
        header_cycle = markdown.split_handoff(
            handoff_path.read_text(encoding='utf-8-sig', errors='replace')
            ).get('cycle') or 0
        if header_cycle and header_cycle != cycle:
            print(
                f'header reads Cycle: {header_cycle}; this cycle is {cycle}'
                " - finish rewrites that line last; attribute this session's"
                f' work to {cycle}')


def verb_begin(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run begin: create the folder or take the lock on an existing one.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to begin; created when absent.
    anch : dict
        Anchors dict from ``anchors()``; provides session and host.
    argv : argparse.Namespace
        Parsed begin arguments; ``--force`` overrides a foreign lock.

    Returns
    -------
    int
        0 once the lock is held; 1 when a young foreign lock blocks it,
        or the thread is marked done.

    Notes
    -----
    - A folder missing both HANDOFF.md and ledger.tsv can still hold
      standing.md, the manifest, and its archives. Those survive, and
      the line printed reads ``rebuilt`` rather than ``created``.
    - A refusal writes nothing: the lock is read before the folder is
      seeded, so a refused run leaves no stub behind.
    """
    refusal = storage.done_refusal(folder, 'begin')
    if refusal:
        print(refusal)
        return 1
    if (folder / 'HANDOFF.md').is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    # A refused run leaves the folder as it found it, so the lock is
    # read before the first write rather than at _take_lock below.
    refusal = storage.lock_refusal(folder, anch, getattr(argv, 'force', False), 'begin')
    if refusal:
        print(refusal)
        return 1
    no_handoff = not (folder / 'HANDOFF.md').exists()
    no_ledger = not (folder / 'ledger.tsv').exists()
    seeded_handoff = no_handoff and no_ledger
    if seeded_handoff:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'cycles').mkdir(exist_ok=True)
        # Notes:
        # - The branch fires on a lost HANDOFF.md and ledger.tsv
        #   alone, and standing.md is append-only, so seeding it
        #   unconditionally destroys a store nothing rebuilds.
        # - The manifest indexes cycles/, so truncating it renumbers
        #   the next archive over one already on disk.
        # - An empty file is no survivor, and an archive outlives the
        #   manifest that indexed it, so content decides which line
        #   this prints.
        standing_path = folder / 'standing.md'
        manifest_rows = storage.read_tsv(
            folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
        survived: list[str] = []
        if standing_path.is_file() and standing_path.stat().st_size > 0:
            survived.append('standing.md')
        if manifest_rows:
            survived.append('the cycle manifest')
        if any((folder / 'cycles').glob('c*.md')):
            survived.append('archived cycles')
        # A handoff created while the session works another live thread
        # is a spin-off, which is housekeeping.
        held = pathlib.Path(
            threads.worked_thread(str(anch['session']), folder) or folder)
        spin_off = (
            (held / 'HANDOFF.md').is_file()
            and not (held / storage.DONE_NAME).exists())
        seed_worked = anch['now'][:10]
        if manifest_rows:
            seed_worked = manifest_rows[-1]['written']
        for name, seed in (
            ('HANDOFF.md',
             (f'# Handoff: {folder.name}\n\n'
              f'Written: {anch["now"][:10]} | Cycle: 1'
              f' | Last worked: {seed_worked}\n\n'
              '## Directives\n\n## Task\n\n## Now\n\n## Plan\n\n'
              '## State\n\n## Environment\n\n## Open questions\n\n'
              '## Log\n')),
            ('ledger.tsv', ledger.LEDGER_HEADER + '\n'),
            ('standing.md', ''),
            ('cycles/manifest.tsv', storage.MANIFEST_HEADER + '\n'),
            ):
            path = folder / name
            if not path.exists():
                path.write_text(seed, encoding='utf-8')
        if survived:
            print(
                f'hq begin: rebuilt {folder} around {", ".join(survived)}'
                ' - the folder kept a store the lost files do not index;'
                f' run hq standing {folder.name} to read what survived')
        else:
            print(f'hq begin: created {folder}')
            if not spin_off:
                threads.record_worked(str(anch['session']), folder)
    elif (folder / 'HANDOFF.md').exists() and not (folder / 'ledger.tsv').exists():
        rc = adopt.verb_adopt(folder, anch, argv)
        if rc != 0:
            return rc
        print('hq begin: ran adopt on existing HANDOFF.md')
        anch = storage.anchors(folder, argv)
    return _take_lock(folder, anch, argv, seeded_handoff)


def _do_stamp(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Write one ledger row after applying R1 checks.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing ledger.tsv.
    anch : dict
        Anchors dict from ``anchors()``; provides cycle and timestamp.
    argv : argparse.Namespace
        Parsed stamp arguments: path, kind, read_before, status, etc.

    Returns
    -------
    int
        0 on success; 1 when R1 refuses the stamp; 2 on bad usage.

    Notes
    -----
    - The path resolves against the folder, then the project root, then
      a pinned work dir, and the row stores the form it resolves to -
      folder-relative with base ``folder``, else ``~`` or absolute with
      base ``abs`` - so one file keeps one ledger key whichever
      spelling stamps it. ``--successor`` resolves the same way.
    - A refusal appends a receipt row; a successful stamp appends the
      row.
    - A row that would sit live at ``always`` with no ``--where`` is
      refused whatever its kind, and the file's headings print under
      the refusal so the next stamp can anchor it.
    - A written row shorter than its comparand, or missing one of its
      backticked tokens or ``s<n>`` references, draws an advisory
      here, where retyping the label costs nothing; the row stands
      either way, and both lines can fire at once.
    - The comparand is the last label an earlier cycle left, or the
      previous stamp of this cycle where the path has no earlier row.
    - A label naming an ``s<n>`` section the new spans do not cover
      draws a third advisory, on a stamp that gave ``--where`` and let
      ``--label`` carry forward.
    """
    path = getattr(argv, 'path', '') or ''
    if not path:
        print('hq stamp: path is required')
        return 2
    if any(c in path for c in _SPLITLINES_CHARS):
        print('hq stamp: path may not contain a tab or a line break')
        return 2
    for flag, value in (
            ('--label', getattr(argv, 'label', None) or ''),
            ('--where', getattr(argv, 'where', None) or ''),
            ('--reason', getattr(argv, 'reason', None) or '')):
        if any(c in value for c in _SPLITLINES_CHARS):
            print(
                f'hq stamp: {flag} may not contain a tab or a line break'
                ' - the ledger is tab-delimited and one row is one line;'
                ' retype it on one line')
            return 2
    ledger_path = folder / 'ledger.tsv'
    rows = storage.read_tsv(ledger_path, ledger.LEDGER_FIELDS)
    live = ledger.latest_rows(rows)
    # A rendered block prints a repo file root- or work-dir-relative,
    # and the hq when command it prints beside it takes that spelling,
    # so the write verb resolves it against the same bases. The search
    # is ungated by --status and --successor: a row retired by the printed
    # path must retire the row the absolute path opened, not a twin.
    stored_path, path_obj, base = paths.stamp_path(folder, path, rows)
    never_stamped = all(row['path'] != stored_path for row in rows)
    prev = live.get(stored_path, {})
    # Notes:
    # - An abs path not on disk is a file on another host or one still
    #   to come, and stays an advisory at begin and finish.
    # - A path gone on purpose is recorded with --status missing,
    #   --archive, or --successor, which pass; a path with a row already
    #   is a re-stamp, judged by R1 and R3.
    recorded_on_purpose = (
        getattr(argv, 'status', None) in {'missing', 'archived'}
        or getattr(argv, 'archive', False)
        or bool(getattr(argv, 'successor', None)))
    if (never_stamped and base == 'folder' and not path_obj.exists()
            and not recorded_on_purpose):
        if path.strip('`').startswith(('/', '~')):
            print(
                f'hq stamp: {stored_path}: no such file under {folder}'
                ' - create it first, or record it gone with --status missing')
        else:
            print(
                f'hq stamp: {stored_path}: no such file under {folder}'
                ' - not under the folder, the root, or the work dir;'
                ' create it first, or record it gone with --status missing')
        return 2
    if base == 'folder' and stored_path in ledger.KIND_DIRS and path_obj.is_dir():
        print(
            f'hq stamp: {stored_path} is a kind folder'
            ' - stamp the files under it, each by its own path')
        return 2
    is_dir = path_obj.is_dir()
    if not is_dir and path_obj.exists() and not path_obj.is_file():
        print(
            f'hq stamp: {path} is not a regular file or directory'
            ' - rename it or leave it out of the ledger')
        return 2
    first_heading = ''
    if not is_dir and path_obj.exists():
        try:
            with path_obj.open(encoding='utf-8', errors='replace') as fh:
                for ln in fh:
                    if ln.startswith('#'):
                        first_heading = ln.strip()
                        break
        except OSError:
            pass
    # The draft rule names the folder's top level alone: a .py nested in
    # the folder or outside it infers other, gated by --kind draft.
    top_level = base == 'folder' and '/' not in stored_path
    inferred_kind, seed_rb = ledger.infer_kind(
        path_obj.name, is_dir, first_heading, top_level,
        ledger.kind_dir_of(stored_path, base))
    history = [row for row in rows if row['path'] == stored_path]
    # Notes:
    # - R1 binds to every kind the path has ever carried, so an
    #   --archive that also passes --kind other cannot take a
    #   once-declared spec out of the gate.
    # - A kind the agent declares with --kind counts too: a file
    #   called a spec is gated from that stamp on.
    gate_kind = inferred_kind
    if gate_kind not in {'spec', 'draft'}:
        declared = [getattr(argv, 'kind', None)] + [row['kind'] for row in history]
        gate_kind = next(
            (k for k in declared if k in {'spec', 'draft'}), gate_kind)
    successor_given = getattr(argv, 'successor', None)
    successor = (
        paths.stamp_path(folder, successor_given, rows)[0] if successor_given
        else prev.get('successor', '-') or '-')
    successor_path = paths.successor_path(folder, successor)
    # Notes:
    # - R1 judges the resulting row, so a successor carried forward
    #   from the previous row counts, re-checked every stamp.
    # - A directory, or the artifact itself under any spelling, is
    #   not a successor.
    successor_on_disk = (
        successor != '-'
        and successor_path.is_file()
        and successor_path.resolve() != path_obj.resolve())
    # A refusal receipt records an attempt, not a state; its reason is
    # skipped when carrying reason forward.
    carried_reason = next(
        (row['reason'] for row in reversed(history)
         if not row['reason'].startswith('refused: ')),
        '-')
    if (getattr(argv, 'reason', None) or '').startswith('refused: '):
        print("hq stamp: --reason may not start with 'refused: ', the receipt prefix")
        return 2
    if getattr(argv, 'archive', False) and not getattr(argv, 'reason', None):
        print('hq stamp: --archive requires --reason')
        return 2
    if (getattr(argv, 'status', None) == 'archived'
            and not getattr(argv, 'reason', None)):
        print('hq stamp: --status archived requires --reason')
        return 2
    refusal_reason: str | None = None
    # A non-live successor would let two specs name each other and
    # leave nothing gated; an explicit one is refused.
    if successor_given:
        successor_row = live.get(successor)
        if successor_row is not None and successor_row['status'] != 'live':
            refusal_reason = (
                f'refused: R1: successor {successor} is'
                f' {successor_row["status"]}, not live')
    if refusal_reason is None:
        kind = getattr(argv, 'kind', None) or prev.get('kind', inferred_kind)
        explicit_rb = getattr(argv, 'read_before', None)
        carried_rb = prev.get('read_before', ledger.TIER_SEED.get(gate_kind, 'never'))
        # A re-stamp to spec or draft from a row at never takes the new
        # kind's tier: never carried forward leaves --kind draft refused
        # by R1. A tier above never is the row's own and stays.
        kind_changed = bool(prev) and kind != prev.get('kind')
        rb = explicit_rb or (
            ledger.TIER_SEED.get(
                kind, 'never') if kind_changed and carried_rb == 'never'
            else carried_rb)
        explicit_status = getattr(argv, 'status', None)
        status = explicit_status or prev.get('status', 'live')
        # --status archived and --archive are the same demotion and
        # must land the same row: the two spellings cannot drift.
        if getattr(argv, 'archive', False) or status == 'archived':
            status = 'archived'
            rb = 'never'
        elif successor_given and successor != '-':
            if not explicit_status:
                status = 'superseded'
            if not explicit_rb:
                rb = 'never'
        # A reason explains the state it was given with; it carries
        # only while kind, status, and read_before hold. A transition
        # needs its own reason.
        state_holds = (kind, status, rb) == (
            prev.get('kind', inferred_kind),
            prev.get('status', 'live'),
            prev.get('read_before', ledger.TIER_SEED.get(gate_kind, 'never')))
        reason = getattr(argv, 'reason', None) or (
            carried_reason if state_holds else '-')
    label = getattr(argv, 'label', None) or prev.get('label', '-')
    where = getattr(argv, 'where', None) or prev.get('where', '-')
    sha12 = storage.sha12_path(path_obj) if path_obj.is_file() else '-'
    n_lines = str(storage.line_count(path_obj)) if path_obj.exists() else '-'
    if refusal_reason is None:
        new_row: storage.Row = {
            'cycle': str(anch['cycle']), 'ts': anch['now'],
            'path': stored_path, 'base': base,
            'kind': kind, 'status': status, 'read_before': rb,
            'successor': successor, 'where': where, 'sha12': sha12,
            'lines': n_lines, 'reason': reason, 'label': label,
            }
        r1_result = ledger.check_r1(new_row, gate_kind, successor_on_disk)
        # R1 passes every row whose successor is on disk, so a stamp
        # that gave one and was refused was refused for that successor.
        if r1_result and successor_given and successor != '-':
            r1_result = f'R1: successor {successor} is not another file on disk'
        if r1_result:
            refusal_reason = f'refused: {r1_result}'
        elif status == 'live' and rb == 'always' and where == '-':
            # always is an anchored span read at every resume; a whole
            # file at that tier is an edit row read too early.
            refusal_reason = f'refused: {stored_path} always with no anchor'
    if refusal_reason:
        # Notes:
        # - A receipt verifies nothing: it keeps the previous sha so
        #   a failed stamp cannot clear the R3 gate.
        # - With no previous row it seeds read_before from the kind
        #   table, so a refusal never promotes an ungated file into
        #   the read block, and records the sha on disk so R3 covers
        #   the file from here on.
        refused_row: storage.Row = {
            'cycle': str(anch['cycle']), 'ts': anch['now'],
            'path': stored_path, 'base': base,
            'kind': prev.get('kind', inferred_kind),
            'status': prev.get('status', 'live'),
            'read_before': prev.get('read_before', seed_rb),
            'successor': prev.get('successor', '-'),
            'where': prev.get('where', '-'),
            'sha12': prev.get('sha12', sha12),
            'lines': prev.get('lines', n_lines),
            'reason': refusal_reason,
            'label': prev.get('label', '-'),
            }
        storage.append_tsv(
            ledger_path, ledger.LEDGER_FIELDS, refused_row, ledger.LEDGER_HEADER)
        if refusal_reason.endswith('always with no anchor'):
            print(
                f'hq stamp: {refusal_reason} - {n_lines} lines read whole at'
                ' every resume; stamp --where <heading> or --read-before edit')
            try:
                for ln in path_obj.read_text(
                        encoding='utf-8', errors='replace').splitlines():
                    if markdown.ATX_HEADING_PAT.match(ln):
                        print(f'  {ln.rstrip()}')
            except OSError:
                pass
        else:
            print(
                f'hq stamp: {refusal_reason}'
                ' - supply a live --successor or --archive --reason,'
                ' or leave the row gated')
        return 1
    storage.append_tsv(ledger_path, ledger.LEDGER_FIELDS, new_row, ledger.LEDGER_HEADER)
    # Notes:
    # - The gate is the command's own shape. Only a stamp that
    #   narrowed --where while carrying the label forward can leave a
    #   label its writer never re-read; a label typed in the same
    #   command was judged as it was typed.
    # - Coverage is the heading line, not the whole section, so an
    #   anchor named by its title covers what s<n> names.
    label_unread = (
        bool(getattr(argv, 'where', None))
        and not getattr(argv, 'label', None)
        and where != '-' and label != '-'
        and path_obj.is_file())
    if label_unread:
        file_text_for_advisory: str | None = None
        try:
            file_text_for_advisory = path_obj.read_text(encoding='utf-8', errors='replace')
        except OSError:
            pass
        if file_text_for_advisory is not None:
            spans, _ = markdown.resolve_where(
                file_text_for_advisory, markdown.split_where(where))
            uncovered = []
            for token in sorted({
                    m.lower() for m in re.findall(
                        r'(?<![0-9A-Za-z_])s\d+(?:\.\d+)*[a-z]?(?![0-9A-Za-z_])',
                        label)}):
                token_spans, _ = markdown.resolve_where(file_text_for_advisory, [token])
                # Notes:
                # - A token naming a section the file does not have (e.g.
                #   s11 of a nine-section file) resolves to nothing; report
                #   it alongside out-of-span tokens because the label still
                #   advertises a section the span will not print.
                # - This comparison uses the fuller anchor grammar
                #   (s\d+(?:\.\d+)*[a-z]?) while advisory 2 above keeps
                #   \bs\d+\b; they disagree on 's24.4' - advisory 2 sees
                #   's24', this one sees 's24.4'. Unifying them is wider
                #   than this ticket.
                if not token_spans or not any(
                        start <= token_spans[0][0] <= end
                        for start, end in spans):
                    uncovered.append(token)
            if uncovered:
                print(
                    f'advisory: label names {uncovered!r} outside the span:'
                    f' {stored_path} - rewrite it with --label in the same'
                    ' stamp')
    # Notes:
    # - The comparand is the last row of an earlier cycle, so two
    #   re-stamps in one cycle are both judged against what the
    #   previous cycle left; with none, against the earlier re-stamp.
    # - Backticks are markup, not content, so both tests read the
    #   label with them stripped: an identifier the new label spells
    #   plainly is neither a loss nor a shortening, and a formatting
    #   change reported as an omission cannot be told from a real one.
    # - A token counts as kept only where it stands on its own.
    #   Characters of it inside a longer identifier name something
    #   else, and the old name is gone.
    # - Both lines can fire on one re-stamp: the dropped line is what
    #   the operator retypes, the shorter line what is left unnamed.
    cycle_now = str(anch['cycle'])
    prev_row = next(
        (r for r in reversed(history) if r['cycle'] != cycle_now),
        history[-1] if history else None)
    prev_label = prev_row['label'] if prev_row else '-'
    curr_label = new_row['label']
    if prev_label == '-' or curr_label == '-':
        return 0
    prev_plain = prev_label.replace('`', '')
    curr_plain = curr_label.replace('`', '')
    if len(curr_plain) < len(prev_plain):
        print(
            f'advisory: label shorter than predecessor: {stored_path}'
            ' - check the new label still carries what the old one said')
    dropped = {
        f'`{token}`' for token in re.findall(r'`([^`]+)`', prev_label)
        if not re.search(
            r'(?<![0-9A-Za-z_])' + re.escape(token) + r'(?![0-9A-Za-z_])',
            curr_plain)
        }
    dropped |= (
        set(re.findall(r'\bs\d+\b', prev_label))
        - set(re.findall(r'\bs\d+\b', curr_label)))
    if dropped:
        print(
            f'advisory: label dropped {dropped!r}: {stored_path}'
            ' - put it back or accept the loss')
    return 0


def verb_stamp(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run stamp: record a ledger row for one artifact (or batch from stdin).

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing ledger.tsv.
    anch : dict
        Anchors dict from ``anchors()``; provides cycle and timestamp.
    argv : argparse.Namespace
        Parsed stamp arguments; ``--batch`` reads lines from stdin.

    Returns
    -------
    int
        0 when every row was written; 1 when any R1 refusal fires;
        2 on a bad path or unparseable batch line.
    """
    if getattr(argv, 'batch', False):
        # Imported here because cli imports this module at load.
        from handoff import cli
        rc = 0
        parser = cli.build_parser()
        slug = folder.name
        for line_no, raw_line in enumerate(sys.stdin, 1):
            line = raw_line.strip()
            if not line or line == '-':
                continue
            # Notes:
            # - The parser's own usage block is swallowed: the batch
            #   reports a bad line by number on stdout instead.
            # - Its last stderr line is the cause, and the report keeps
            #   it: a batch of artifact rows carries long labels, and a
            #   message naming only the line leaves the reader to guess
            #   between a quote, a flag and a missing path.
            usage_err = io.StringIO()
            quote_fault = ''
            with contextlib.redirect_stderr(usage_err):
                try:
                    sub = parser.parse_args(['stamp', slug] + shlex.split(line))
                except ValueError as exc:
                    sub = None
                    quote_fault = str(exc).lower()
                except SystemExit:
                    sub = None
            if sub is None or not getattr(sub, 'path', ''):
                if quote_fault:
                    cause = (
                        f'{quote_fault}: put the whole label in double'
                        ' quotes, since an apostrophe opens a quote the'
                        ' line never closes')
                elif sub is None:
                    usage_lines = usage_err.getvalue().strip().splitlines()
                    cause = (
                        usage_lines[-1].split('error: ')[-1]
                        if usage_lines else 'arguments not understood')
                else:
                    cause = 'path is required: it is the first word of the line'
                print(
                    f'hq stamp: batch line {line_no} not parsed: {line}'
                    f' - {cause} - re-run that line alone; the other lines ran')
                rc = max(rc, 2)
                continue
            rc = max(rc, _do_stamp(folder, anch, sub))
        return rc
    return _do_stamp(folder, anch, argv)


def verb_note(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run note: append one item to standing.md (or batch from stdin).

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding standing.md.
    anch : dict
        Anchors dict from anchors(); provides the current cycle number.
    argv : argparse.Namespace
        Parsed note arguments: note_kind, headline, body, batch, and the
        evidence flags grade, open, contradicts.

    Returns
    -------
    int
        0 when every item was appended, 2 when any was refused.

    Notes
    -----
    - Under ``--batch`` the kind slot holds the batch marker ``-`` (or
      nothing) and each stdin line reads ``<kind> --headline <headline>
      <body>``: the headline quoted or one bare word, the body the rest
      of the line verbatim. ``--grade <word>``, ``--open`` and
      ``--contradicts <id>`` may sit between the kind and ``--headline``,
      in any order and once each; after it they are body text. On the
      command line beside ``--batch`` they are refused, and nothing is
      written. Prose is not shell: an apostrophe or a
      parenthesis in the body carries no meaning here, and a body
      wrapped whole in one pair of quotes loses only that pair.
    - A batch line that does not parse, names no known kind, or carries
      no headline is reported by number; the rest still run.
    """
    if getattr(argv, 'batch', False):
        if (getattr(argv, 'grade', None) is not None
            or getattr(argv, 'open', False)
            or getattr(argv, 'contradicts', None) is not None):
            print(
                'hq note: --grade, --open and --contradicts go on each batch line'
                ' - move them there and re-run')
            return 2
        rc = 0
        for line_no, raw_line in enumerate(sys.stdin, 1):
            line = raw_line.strip()
            if not line or line == '-':
                continue
            match = _NOTE_BATCH_LINE.match(line)
            if match is None:
                print(
                    f'hq note: batch line {line_no} not parsed: {line}'
                    ' - write it as <kind> --headline "<headline>" <body>'
                    ' and re-run it alone; the other lines ran')
                rc = max(rc, 2)
                continue
            kind_str = match.group('kind')
            headline = next(
                g for g in match.group('dq', 'sq', 'bare') if g is not None)
            body = match.group('body').strip()
            if len(body) >= 2 and body[0] == body[-1] and body[0] in '"\'':
                if body[0] not in body[1:-1]:
                    body = body[1:-1]
            opts = match.group('opts')
            flags = re.findall(r'--(grade|open|contradicts)\b', opts)
            repeated = next((flag for flag in flags if flags.count(flag) > 1), None)
            grade_m = re.search(r'--grade\s+(\w+)', opts)
            contradicts_m = re.search(r'--contradicts\s+(\w+)', opts)
            if repeated:
                print(f'hq note: --{repeated} given twice - keep one and re-run')
                note_rc = 2
            else:
                note_rc = standing.do_note(
                    folder, anch, kind_str, headline, body.strip(),
                    grade_m.group(1) if grade_m else None,
                    re.search(r'--open\b', opts) is not None,
                    contradicts_m.group(1) if contradicts_m else None)
            if note_rc:
                print(
                    f'hq note: batch line {line_no} refused: {line}'
                    ' - fix it and re-run it alone; the other lines ran')
            rc = max(rc, note_rc)
        return rc
    return standing.do_note(
        folder, anch,
        getattr(argv, 'note_kind', '') or '',
        getattr(argv, 'headline', '') or '',
        getattr(argv, 'body', '') or '',
        getattr(argv, 'grade', None),
        getattr(argv, 'open', False),
        getattr(argv, 'contradicts', None))


def verb_supersede(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run supersede: append a supersession or retire line to standing.md.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing standing.md.
    anch : dict
        Anchors dict from ``anchors()``; provides the current cycle.
    argv : argparse.Namespace
        Parsed supersede arguments: old_id, and exactly one of new_id
        (empty counts as absent) and ``--reason``, which retires old_id
        with no successor. A newline in the reason becomes a space.

    Returns
    -------
    int
        0 on success; 1 on any refusal, with nothing written.

    Notes
    -----
    - Prints the old item as standing.md holds it, since it leaves the
      block and a ruling live in its body would otherwise go unseen.
    """
    old_id = getattr(argv, 'old_id', '')
    new_id = getattr(argv, 'new_id', '') or ''
    reason_raw = getattr(argv, 'reason', None)
    if new_id and reason_raw is not None:
        print(
            'hq supersede: give a new id or --reason, not both'
            ' - run hq supersede <slug> <old> <new>, or <old> --reason "<why>"')
        return 1
    if not new_id and reason_raw is None:
        print(
            'hq supersede: give a new id or --reason'
            ' - run hq supersede <slug> <old> <new>, or <old> --reason "<why>"')
        return 1
    reason = ' '.join((reason_raw or '').split())
    if reason_raw is not None and not re.search(r'\w', reason):
        print('hq supersede: --reason needs a word - say why the item no longer holds')
        return 1
    if new_id and (not old_id or old_id[0] != new_id[0]):
        print(f'hq supersede: ids must share a prefix ({old_id!r} vs {new_id!r})')
        return 1
    # An id superseded by itself leaves no id now current for hq standing
    # to name, and drops the item from the block with nothing replacing it.
    if new_id and old_id == new_id:
        print(
            f'hq supersede: {old_id} cannot supersede itself'
            ' - name the item that replaces it, or hq note one first')
        return 1
    standing_path = folder / 'standing.md'
    text = standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
    items, superseded_ids = standing.parse_standing(text)
    chains = standing.supersession_chains(text)
    all_ids = {item['id'] for item in items}
    if old_id not in all_ids:
        print(
            f'hq supersede: {old_id!r} not found in standing.md'
            f'; run hq standing {folder.name} to list the ids')
        return 1
    if new_id and new_id not in all_ids:
        print(
            f'hq supersede: {new_id!r} not found in standing.md'
            f'; run hq standing {folder.name} to list the ids')
        return 1
    # A second outgoing line would leave two ids to name as current.
    if old_id in superseded_ids:
        chain, _, retirement = chains.get(old_id, ([old_id], '', None))
        if len(chain) == 1 and retirement:
            print(
                f'hq supersede: {old_id} was already retired in c{retirement[0]}'
                f' - hq standing {folder.name} {old_id} shows it')
        elif retirement:
            print(
                f'hq supersede: {old_id} was already superseded by {chain[-1]},'
                f' retired in c{retirement[0]}'
                f' - hq standing {folder.name} {old_id} shows it')
        else:
            print(
                f'hq supersede: {old_id} was already superseded by {chain[-1]}'
                f' - supersede {chain[-1]} instead')
        return 1
    new_chain = chains.get(new_id)
    if new_chain and len(new_chain[0]) == 1 and new_chain[2]:
        print(
            f'hq supersede: {new_id} was retired in c{new_chain[2][0]}'
            ' - record the replacement with a note, then supersede onto its id')
        return 1
    # render_standing drops every id named as a source, so a
    # replacement already superseded leaves the kind with no live item:
    # c01 -> c02 followed by c02 -> c01 empties the block.
    if new_id in superseded_ids:
        print(
            f'hq supersede: {new_id!r} was itself superseded'
            f'; run hq standing {folder.name} to list the live ids'
            ' - record the replacement with a note, then supersede'
            ' onto its id')
        return 1
    line = f'- (c{anch["cycle"]}) {old_id} -> {new_id or "- " + reason}'
    storage.append_lines(standing_path, [line])
    # The item leaves the block entirely, so a ruling still live inside
    # its body would go unseen; the item is echoed as standing.md holds
    # it on its way out.
    old_item = next(item for item in items if item['id'] == old_id)
    pfx = f'(c{old_item["cycle"]}) ' if old_item['cycle'] else ''
    shown = f'[{old_id}] {pfx}**{old_item["headline"]}** {old_item["body"]}'
    print(f'dropped from the block: {shown.rstrip()}')
    return 0


def verb_done(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Mark the thread finished, or reopen it, by the ``.hq.done`` marker.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the marker is ``folder/.hq.done``.
    anch : dict
        Anchors dict from ``anchors()``; supplies the timestamp.
    argv : argparse.Namespace
        Parsed ``done`` arguments: ``reason`` records why, ``undo``
        removes the marker.

    Returns
    -------
    int
        0 once the marker is written, removed, or already as asked; 1
        when a cycle is open, or a directory stands under the marker's
        name, with nothing written; 2 when ``--undo`` is paired with
        ``--reason``.

    Notes
    -----
    - The marker's existence is the whole test, here as in ``list``,
      ``begin``, and ``adopt``, so a marker a hand edit left empty or
      unparseable still counts as marked and ``--undo`` clears it.
    - The marker is a dot file, so the folder walk skips it and no
      ledger row, work list, or conservation count sees it.
    - Marking is not idempotent in silence: a second ``done`` prints the
      time the first recorded and leaves it, so the finish date survives
      a repeat and a new ``--reason`` needs ``--undo`` first.
    - Nothing else changes. Every read verb still answers on a marked
      folder; only ``list`` hides it, and ``begin`` and ``adopt`` refuse
      it.
    """
    marker = folder / storage.DONE_NAME
    undo = getattr(argv, 'undo', False)
    if undo and getattr(argv, 'reason', None):
        print(
            'hq done: --undo takes no --reason'
            ' - reopen with --undo alone, or mark without it')
        return 2
    marked = marker.exists()
    state = storage.read_kv(marker)
    if undo:
        if marker.is_dir():
            print(
                f'hq done: {storage.HANDOFF_DIRNAME}/{folder.name}/{storage.DONE_NAME}'
                ' is a directory - remove it by hand; no verb can clear the mark'
                ' while a directory stands under the name')
            return 1
        if not marked:
            print(
                f'hq done: {folder.name} was not marked done'
                ' - nothing to reopen, and hq list already shows it')
            return 0
        marker.unlink()
        print(
            f'hq done: {folder.name} reopened'
            f' - run hq begin {folder.name} to open the next cycle')
        return 0
    if marked:
        print(
            f'hq done: {folder.name} was already marked done at'
            f' {state.get("time", "-")} in c{state.get("cycle", "-")}'
            f' - run hq done {folder.name} --undo to reopen it')
        return 0
    if (folder / '.hq.lock').exists():
        print(
            f'hq done: cycle {storage.read_lock(folder).get("cycle", "?")} is still'
            f' open - run hq finish {folder.name} --log "<line>" first')
        return 1
    manifest = storage.read_tsv(
        folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
    cycle = manifest[-1]['cycle'] if manifest else '-'
    reason = ' '.join((getattr(argv, 'reason', None) or '').split()) or '-'
    fields = {
        'slug': folder.name,
        'time': anch['now'],
        'cycle': cycle,
        'reason': reason,
        }
    marker.write_text(
        '\n'.join(f'{k}={v}' for k, v in fields.items()) + '\n',
        encoding='utf-8')
    print(
        f'hq done: {folder.name} marked done at c{cycle}'
        ' - it drops out of hq list from here')
    print(
        f'run hq list --done to see it; run hq done {folder.name} --undo'
        ' to reopen it')
    return 0
