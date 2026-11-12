"""Read-only verbs: read, when, diff, arc, artifacts, standing, work-dir, list.
"""

import argparse
import datetime
import difflib
import os
import pathlib
import re
import shutil
import sys

from handoff import ledger, markdown, paths, render, standing, storage, threads


def verb_read(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run read: print resolved spans and record a receipt.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder whose ledger names the artifact.
    anch : dict
        Anchors dict from ``anchors()``; provides session and timestamp.
    argv : argparse.Namespace
        Parsed read arguments: path, ``--whole``, ``--section``.

    Returns
    -------
    int
        0 on success; 1 when the path is not in the ledger, not on disk,
        or neither the state directory nor the handoff folder takes the
        receipt; 2 when ``--whole`` and ``--section`` are passed
        together.

    Notes
    -----
    - The spans print before the receipt is written, so a receipt with
      nowhere to go costs the caller the exit code, never the content
      it asked for.
    - The receipt goes to ``hq-reads-<session>.txt`` in the state
      directory, else to ``.hq.reads-<session>`` in the handoff folder,
      and the gate reads both. A sandboxed shell can leave the state
      directory read-only while the repo stays writable.
    - The gate matches a receipt on the path and never on the span,
      and the receipt is written only where the read covered the row's
      required content: ``--whole`` covers any row, a ``--section``
      covers one whose required spans sit inside the span it printed,
      and a row whose ``where`` holds any unresolved anchor is covered
      by ``--whole`` alone. An uncovered read prints the remedy and
      exits 0.
    - The path keys as ``stamp`` stores it, else relative to the root or
      the pinned work dir (``ledger_key``), and the file opens where the
      stored path resolves; the receipt carries the stored path, which
      is what the gate matches.
    - A ``/`` in the session id becomes ``_``: the id names one receipt
      file, not a path under the state directory.
    """
    path = getattr(argv, 'path', '')
    whole = getattr(argv, 'whole', False)
    section = getattr(argv, 'section', None)
    if section is not None and not section:
        print(
            'hq read: --section requires an anchor'
            ' - pass a heading or use --whole for the file whole')
        return 2
    if whole and section:
        print(
            'hq read: --whole and --section name different reads'
            ' - pass one or the other')
        return 2
    rows = storage.read_tsv(folder / 'ledger.tsv', ledger.LEDGER_FIELDS)
    stored_path = paths.ledger_key(folder, path, {r['path'] for r in rows})
    if stored_path is None:
        # A store has no row to find - the artifact walk skips both -
        # so the bare refusal sent the reader to open the file the
        # gate reports them for opening. Name the verb instead.
        store = next((part for part in pathlib.PurePath(path).parts
                      if part in storage.STORE_VERBS), None)
        if store:
            print(f'hq read: {store} is dictated, not opened'
                  f' - run this instead:'
                  f' {storage.STORE_VERBS[store].format(slug=folder.name)}')
            return 1
        print(f'hq read: {path} not in ledger - read the span needed by hand')
        return 1
    file_path = paths.stored_path(folder, stored_path)[1]
    row = ledger.latest_rows(rows)[stored_path]
    if not file_path.exists():
        print(
            f'hq read: {path} not on disk'
            ' - restore the file, or stamp --successor or --archive'
            ' --reason at the next write')
        return 1
    file_text = file_path.read_text(encoding='utf-8', errors='replace')
    file_lines = file_text.splitlines()
    where = '-' if whole else (section or row['where'])
    if where != '-':
        anchor_list = markdown.split_where(where)
        spans, unresolved = markdown.resolve_where(file_text, anchor_list)
        printed = spans
        for span in spans:
            print('\n'.join(file_lines[span[0] - 1:span[1]]))
        for anchor_str in unresolved:
            print(
                f'? unresolved: {anchor_str}'
                ' - use --whole to read the whole file when no span printed above')
    else:
        printed = [(1, len(file_lines))]
        print('\n'.join(file_lines))
    # Notes:
    # - A receipt stands for the row's required content, so a read
    #   leaving any of it unprinted earns none and the gate keeps
    #   reporting the path.
    # - One unresolved anchor withholds the receipt even where its
    #   siblings printed: the row asks for every span it names.
    if whole or row['where'] == '-':
        covered, absent = where == '-', []
    else:
        required, absent = markdown.resolve_where(
            file_text, markdown.split_where(row['where']))
        covered = not absent and all(
            any(beg <= span[0] and span[1] <= end for beg, end in printed)
            for span in required)
    if not covered:
        recover = ' --whole' if absent else ''
        print(
            f'hq read: no receipt for {stored_path} - the gate still'
            f' reports it; run: hq read {folder.name} {stored_path}{recover}')
        return 0
    state_path = pathlib.Path(
        os.environ.get('HQ_STATE_DIR', str(threads.DEFAULT_STATE)))
    session_name = re.sub(r'[/\\]', '_', str(anch['session']))
    failures = []
    for receipt_path in (
            state_path / f'hq-reads-{session_name}.txt',
            folder / f'.hq.reads-{session_name}'):
        try:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            with receipt_path.open('a', encoding='utf-8') as fh:
                fh.write(f'{anch["now"]} {folder.name} {stored_path}\n')
            return 0
        except OSError as exc:
            failures.append(str(exc))
    print(
        f'hq read: receipt not written: {"; ".join(failures)}'
        ' - the gate will not credit this read')
    return 1


def verb_when(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run when: print ledger history for one path.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing ledger.tsv.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed when arguments: path.

    Returns
    -------
    int
        0 with every row for the path, oldest first, no cap, the seven
        columns; 1 when no spelling of the path has a row.

    Notes
    -----
    - The path keys as ``stamp`` stores it, so every spelling of one
      file - ``~``, its expansion, a dotted folder path - shows one
      history; a relative token the folder does not hold is tried
      against the project root, then the pinned work dir.
    """
    path = getattr(argv, 'path', '')
    rows = storage.read_tsv(folder / 'ledger.tsv', ledger.LEDGER_FIELDS)
    stored_path = paths.ledger_key(folder, path, {r['path'] for r in rows})
    if stored_path is None:
        print(
            f'hq when: {path} not in ledger'
            f' - hq artifacts {folder.name} lists the rows')
        return 1
    for row in rows:
        if row['path'] == stored_path:
            print('\t'.join([
                row['path'], row['cycle'], row['status'], row['read_before'],
                row['successor'], row['reason'], row['label']]))
    return 0


def verb_diff(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run diff: the cursor change between two cycles, by section.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder whose cycles/ directory holds the archives.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed diff arguments: ``c1`` and ``c2`` as ``5``, ``c5``, or
        ``c05``; ``section``, a heading to expand, matched without regard
        to case; ``--full``, every section expanded.

    Returns
    -------
    int
        0 with one line per cursor section in file order - ``## Now  +3
        -1``, ``## State  unchanged``, ``## Unfiled  added`` or
        ``removed`` - or nothing when the two cursors are identical
        once whitespace is normalized; with a section or ``--full``, the
        section's ``-`` and ``+`` lines as difflib orders them, no cap.
        1 when a cycle archive is absent or the section matches no
        heading; 2 when a cycle is not a number.

    Notes
    -----
    - A line that moved between sections counts in both, so the summary
      never reads identical when only the shape changed; identity is
      the whole cursor's, so the help text's "no output means identical"
      holds.
    """
    cycles: list[int] = []
    for token in (str(getattr(argv, 'c1', '')), str(getattr(argv, 'c2', ''))):
        digits = token[1:] if token[:1] in 'cC' else token
        if not digits.isdigit():
            print(
                f'hq diff: cycle {token!r} is not N, cN, or cNN'
                ' - name a finished cycle by its number')
            return 2
        cycles.append(int(digits))
    c1, c2 = cycles
    p1 = folder / 'cycles' / f'c{c1:02d}.md'
    p2 = folder / 'cycles' / f'c{c2:02d}.md'
    for p in (p1, p2):
        if not p.exists():
            print(f'hq diff: {p.name} not found - that cycle was never finished here')
            return 1
    cursors = [
        markdown.split_handoff(p.read_text(encoding='utf-8'))['cursor']
        for p in (p1, p2)]
    if ' '.join(cursors[0].split()) == ' '.join(cursors[1].split()):
        return 0
    parsed: list[dict[str, list[str]]] = []
    for cursor in cursors:
        sections: dict[str, list[str]] = {}
        heading = ''
        for line in cursor.splitlines():
            if line.startswith('## '):
                heading = line[3:].strip()
                sections[heading] = []
            elif heading:
                sections[heading].append(line)
        parsed.append(sections)
    old, new = parsed
    order = list(old) + [h for h in new if h not in old]
    wanted = getattr(argv, 'section', None)
    expand = bool(wanted) or getattr(argv, 'full', False)
    if wanted:
        match = next((h for h in order if h.lower() == wanted.lower()), None)
        if match is None:
            print(
                f'hq diff: no section {wanted} in c{c1} or c{c2}'
                f' - sections: {", ".join(order)}')
            return 1
        order = [match]
    for heading in order:
        if heading not in old or heading not in new:
            sign = '+' if heading not in old else '-'
            print(f'## {heading}  {"added" if sign == "+" else "removed"}')
            if expand:
                for line in (new if sign == '+' else old)[heading]:
                    print(f'{sign} {line}')
            continue
        changes = [
            line for line in difflib.ndiff(old[heading], new[heading])
            if line[:1] in '+-']
        if not changes:
            print(f'## {heading}  unchanged')
            continue
        counts = ' '.join(
            f'{sign}{n}' for sign, n in (
                ('+', sum(line[0] == '+' for line in changes)),
                ('-', sum(line[0] == '-' for line in changes)))
            if n)
        print(f'## {heading}  {counts}')
        if expand:
            print('\n'.join(changes))
    return 0


def verb_arc(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run arc: the whole thread in one read, a line a cycle.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding HANDOFF.md and cycles/.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed arc arguments; the slug alone.

    Returns
    -------
    int
        0 with ``render_arc``'s text; 1 when no cycle has finished, so
        the manifest holds no row.

    Notes
    -----
    - Read-only: it writes no receipt and no thread record, and arms
      no gate.
    """
    manifest = storage.read_tsv(
        folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
    if not manifest:
        print(f'hq arc: {folder.name} has no finished cycle - read HANDOFF.md')
        return 1
    first_path = folder / 'cycles' / f'c{int(manifest[0]["cycle"]):02d}.md'
    first_text = (
        first_path.read_text(encoding='utf-8', errors='replace')
        if first_path.exists() else '')
    handoff_text = (folder / 'HANDOFF.md').read_text(encoding='utf-8', errors='replace')
    sys.stdout.write(render.render_arc(folder.name, manifest, handoff_text, first_text))
    return 0


def verb_artifacts(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run artifacts: print live rows and unstamped walk entries, no cap.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to report on.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed artifacts arguments: ``--status``.

    Returns
    -------
    int
        0; one line per entry, or one per selected row where
        ``--status`` is given. A bare run closes with one count line
        per status it left out, naming the ``--status`` that prints
        them.

    Notes
    -----
    - Selection reads the path's current row, the ``latest_rows`` fold,
      so a superseded earlier row never answers ``--status``.
    - ``--status`` replaces the live-only default.
    - A row whose file is gone reads ``missing`` against the disk, so a
      path stamped by an absolute spelling that never arrived is
      counted rather than printed, and the count line is the only trace
      it leaves here.
    - No row matching is exit 0, not a refusal: the query answered.
    """
    rows = storage.read_tsv(folder / 'ledger.tsv', ledger.LEDGER_FIELDS)
    explicit_status = getattr(argv, 'status', None)
    wanted_status = explicit_status or 'live'
    live = ledger.latest_rows(rows)
    walk = ledger.walk_folder(folder)
    live = ledger.reconcile_missing(folder, live)
    # A walk entry with no row carries no status to select on, so a
    # filtered run reports rows alone.
    rows_only = explicit_status is not None
    printed = 0
    seen: set[str] = set()
    for name, kind in walk:
        seen.add(name)
        if kind == 'skip':
            if not rows_only:
                print(
                    f'{name}  conflicted copy' if 'conflicted copy' in name
                    else f'{name}  unstampable')
            continue
        row = live.get(name)
        if row is None:
            if not rows_only and not ledger.is_recorded(name, live):
                print(f'{name}  {kind}?  unstamped')
            continue
        if row['status'] == wanted_status:
            printed += 1
            print(
                f'{name}  {row["kind"]}  {row["read_before"]}'
                f'  c{row["cycle"]}  {row["label"]}')
    for path, row in live.items():
        if path in seen:
            continue
        if row['status'] == wanted_status:
            printed += 1
            print(
                f'{path}  {row["kind"]}  {row["read_before"]}'
                f'  c{row["cycle"]}  {row["label"]}')
    if not rows_only:
        held_back: dict[str, int] = {}
        for row in live.values():
            if row['status'] != 'live':
                held_back[row['status']] = held_back.get(row['status'], 0) + 1
        for status in [
                *ledger.NON_LIVE_ORDER,
                *sorted(s for s in held_back if s not in ledger.NON_LIVE_ORDER)]:
            if status in held_back:
                print(
                    f'{status} {held_back[status]}'
                    f'  - hq artifacts {folder.name} --status {status}')
    if rows_only and not printed:
        print(
            f'hq artifacts: no row has status {explicit_status}: {folder.name}'
            ' - run the verb bare for every live row')
    return 0


def verb_standing(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run standing: print unsuperseded items, all with --all, or named ids.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing standing.md.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed standing arguments; ``ids`` names items to print in full,
        ``--all`` includes superseded items in the listing, ``--grep``,
        ``--kind`` and ``--in-cycle`` select within a listing.

    Returns
    -------
    int
        0 with no id, one line per item; with ids, 0 once every id
        printed and 1 when any id is not in standing.md; 2 on a ``--grep``
        that is not a regular expression.

    Notes
    -----
    - A named id outranks every filter: ``ids`` and ``--kind`` together
      print the named items, never their intersection.
    - The block renders a decision, a dead end, or a cited evidence
      item by its headline, and a constraint by its headline and first
      sentence; the id form the block names is where the rest of a body
      is read.
    - A superseded item names the id now current and the cycle that
      made it current, never the first hop out of a chain whose own
      successor was superseded later.
    - The id form adds the whole chain, ``old -> mid -> current``,
      once it runs past one hop.
    - A retired item prints ``[retired in c<n>: <reason>]``; one
      superseded onto a later-retired item prints both marks.
    """
    standing_path = folder / 'standing.md'
    text = standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
    items, superseded_ids = standing.parse_standing(text)
    chains = standing.supersession_chains(text)
    show_all = getattr(argv, 'all', False)
    wanted = list(getattr(argv, 'ids', None) or [])
    by_id = {item['id']: item for item in items}
    grep = getattr(argv, 'grep', None)
    kind = getattr(argv, 'kind', None)
    in_cycle = getattr(argv, 'in_cycle', None)
    # A named id is explicit and outranks a filter, so filtering runs only
    # over a listing; by_id above already holds every item for the refusal.
    if not wanted:
        if grep:
            try:
                pattern = re.compile(grep, re.IGNORECASE)
            except re.error as exc:
                print(
                    f'hq standing: --grep is not a regular expression: {exc}'
                    ' - fix the pattern; hq standing --help lists the flags')
                return 2
            items = [
                i for i in items
                if pattern.search(f'{i["headline"]} {i["body"]}')
                ]
        if kind:
            items = [i for i in items if i['prefix'] == standing.KIND_PREFIX[kind]]
        if in_cycle:
            items = [i for i in items if str(i['cycle']) == str(in_cycle)]
    rc = 0
    for item_id in wanted:
        if item_id not in by_id:
            print(
                f'hq standing: {item_id} not in standing.md'
                f' - hq standing {folder.name} lists the ids')
            rc = 1
    for item in items:
        if wanted:
            if item['id'] not in wanted:
                continue
        elif not show_all and item['id'] in superseded_ids:
            continue
        chain_entry = chains.get(item['id'])
        sup = ''
        if chain_entry:
            chain, sup_in, retirement = chain_entry
            marks = []
            if len(chain) > 1:
                marks.append(f'superseded by {chain[-1]} in c{sup_in}')
            if retirement:
                retired_in, reason = retirement
                marks.append(f'retired in c{retired_in}: {reason}')
            hops = f' - {" -> ".join(chain)}' if wanted and len(chain) > 2 else ''
            sup = f' [{", ".join(marks)}{hops}]'
        elif item['id'] in superseded_ids:
            sup = ' [superseded]'
        print(f'[{item["id"]}] (c{item["cycle"]}) **{item["headline"]}** {item["body"]}{sup}')
    return rc


def verb_work_dir(folder: pathlib.Path, argv: argparse.Namespace) -> int:
    """Print where the thread's made files go, pin a directory, or clear it.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the pin is ``folder/work-dir``.
    argv : argparse.Namespace
        Parsed ``work-dir`` arguments: ``dir`` is the token to pin or
        None, ``clear`` removes the pin.

    Returns
    -------
    int
        0 with the resolved line, once the pin is written, or once it is
        cleared; 1 when the token is refused, the pin on disk fails its
        check, or a directory sits under the pin's name, with nothing
        written; 2 when ``dir`` and ``--clear`` are both given.

    Notes
    -----
    - No scan: whether the thread's work already lives in a project
      directory is a judgment over the layout at the first begin, and a
      name list would pin a dump in the wrong place. The agent judges
      and pins.
    """
    token = getattr(argv, 'dir', None)
    pin = folder / paths.WORK_PIN_NAME
    clear = getattr(argv, 'clear', False)
    if clear and token is not None:
        print('hq work-dir: --clear takes no directory - pass one or the other')
        return 2
    # A directory under the pin's name can be neither pinned over nor
    # cleared; the resolve line names the move, and the bare print
    # below carries it with the ruling.
    if pin.is_dir() and (clear or token is not None):
        for line in paths.resolve_work_dir(folder)[1]:
            print(line)
        return 1
    if clear:
        pin.unlink(missing_ok=True)
        print(paths.work_dir_line(folder, ''))
        return 0
    if token is not None:
        value, reason = paths.validate_work_dir(folder.parent.parent, token)
        if not value:
            print(
                f'hq work-dir: {token} {reason} - name a directory that'
                " already holds the thread's work; with none, leave the"
                ' folder unpinned')
            return 1
        pin.write_text(value + '\n', encoding='utf-8')
        print(
            f'work dir: {value} - pinned in'
            f' {storage.HANDOFF_DIRNAME}/{folder.name}/{paths.WORK_PIN_NAME}')
        return 0
    value, lines = paths.resolve_work_dir(folder)
    for line in lines:
        print(line)
    print(paths.work_dir_line(folder, value))
    return 1 if lines else 0


def verb_list(root: pathlib.Path, argv: argparse.Namespace) -> int:
    """Print one line per handoff folder, newest first.

    Parameters
    ----------
    root : pathlib.Path
        Project root; the handoff folders sit under ``root / HANDOFF_DIRNAME``.
    argv : argparse.Namespace
        Parsed ``list`` arguments; ``count`` caps the lines, None for all;
        ``--done`` lists the folders marked done instead of the open ones.

    Returns
    -------
    int
        0, also when no folder holds a ``HANDOFF.md`` and the one line is
        ``hq list: no handoff under <dir>``; 2 on a count below 1.

    Notes
    -----
    - A folder holding the ``.hq.done`` marker is left out, and the run
      closes with a ``<N> marked done`` line naming ``--done``, which
      lists those folders and leaves the open ones out.
    - The marker's existence is the whole test, as at ``begin``, so a
      marker no key=value reader can parse still hides its folder.
    - ``count`` caps what is shown, so ``hq list 5`` is the five most
      recent of whichever set the flag selects.
    - Order: newest ``Last worked``, else ``Written``, else mtime date;
      then newer mtime; then slug A to Z. A header date counts only as
      ``YYYY-MM-DD``.
    - The line is ``<slug>  <worked>  c<N>  <open> open  <task>``,
      ``<worked>`` the date it sorts by; ``<open>`` counting the
      ``- [ ]`` items: a done item moves out of the cursor, so a done
      count would read zero. A non-conforming header gives ``-`` for
      the cycle; a Plan with no checkbox item, or no Plan, gives ``-``
      for the progress; a missing Task line gives ``-``.
    - On a terminal, a Task wider than the columns left of the terminal
      width is cut to fit and ends in ``...``, so no row wraps. Piped
      output keeps the whole Task.
    - Only ``- [ ]``, ``- [x]``, and ``- [X]`` bullets under ``## Plan``
      are plan items; a checkbox under any other section is not.
    - A file the script cannot read prints its slug with ``-`` in every
      field and ``unreadable: <reason>`` as the Task; the survey goes on
      and the exit stays 0.
    """
    count = getattr(argv, 'count', None)
    if count is not None and count < 1:
        print('hq list: count must be a positive integer')
        return 2
    handoffs = root / storage.HANDOFF_DIRNAME
    files = []
    if handoffs.is_dir():
        files = sorted(
            (path for path in handoffs.glob('*/HANDOFF.md') if path.is_file()),
            key=lambda path: path.parent.name)
    only_done = getattr(argv, 'done', False)
    done_cnt = sum((path.parent / storage.DONE_NAME).exists() for path in files)
    files = [
        path for path in files
        if (path.parent / storage.DONE_NAME).exists() == only_done]
    if not files:
        if only_done:
            print(
                f'hq list: no handoff under {handoffs} is marked done'
                ' - run hq done <slug> to mark one')
        elif done_cnt:
            print(
                f'hq list: every handoff under {handoffs} is marked done'
                ' - run hq list --done to see them')
        else:
            print(f'hq list: no handoff under {handoffs}')
        return 0
    keyed_rows: list[tuple[tuple[str, float], tuple[str, str, str, str, str]]] = []
    for path in files:
        mtime, mtime_date = 0.0, ''
        try:
            mtime = path.stat().st_mtime
            mtime_date = datetime.date.fromtimestamp(mtime).isoformat()
            text = path.read_text(encoding='utf-8-sig', errors='replace')
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            keyed_rows.append((
                (mtime_date, mtime),
                (path.parent.name, '-', '-', '-', f'unreadable: {reason}')))
            continue
        parsed = markdown.split_handoff(text)
        cycle = '-'
        sort_date = mtime_date
        if parsed['cycle'] is not None:
            date_match = (
                markdown.WORKED_PAT.search(parsed['header'])
                or re.search(r'Written:\s*(\d{4}-\d{2}-\d{2})', parsed['header']))
            if date_match:
                sort_date = date_match.group(1)
            cycle = f'c{parsed["cycle"]}'
        task = '-'
        open_cnt = total = 0
        section = ''
        for line in text.splitlines():
            if line.startswith('## '):
                section = line[3:].strip()
                continue
            if line.startswith('<!-- hq:'):
                section = ''
                continue
            if section == 'Task' and task == '-' and line.strip():
                task = line.strip()
            elif section == 'Plan':
                box = re.match(r'\s*- \[([ xX])\]', line)
                if box:
                    total += 1
                    open_cnt += box.group(1) == ' '
        progress = f'{open_cnt} open' if total else '-'
        keyed_rows.append((
            (sort_date, mtime),
            (path.parent.name, sort_date, cycle, progress, task)))
    keyed_rows.sort(key=lambda pair: pair[0], reverse=True)
    rows = [row for _, row in keyed_rows[:count]]
    headers = ('SLUG', 'LAST WORKED', 'CYCLE', 'PROGRESS')
    col_widths = [
        max(*(len(row[i]) for row in rows), len(headers[i]))
        for i in range(4)
        ]
    header_line = (
        f'{headers[0].ljust(col_widths[0])}  '
        f'{headers[1].ljust(col_widths[1])}  '
        f'{headers[2].ljust(col_widths[2])}  '
        f'{headers[3].ljust(col_widths[3])}  '
        f'TASK'
        )
    print(header_line)
    print('-' * len(header_line))
    task_width = None
    if sys.stdout.isatty():
        prefix_width = len(header_line) - len('TASK')
        task_width = max(shutil.get_terminal_size().columns - prefix_width, 8)
    for slug, written, cycle, progress, task in rows:
        if task_width is not None and len(task) > task_width:
            task = task[:task_width - 3] + '...'
        print(
            f'{slug.ljust(col_widths[0])}  '
            f'{written.ljust(col_widths[1])}  '
            f'{cycle.ljust(col_widths[2])}  '
            f'{progress.ljust(col_widths[3])}  '
            f'{task}')
    if done_cnt and not only_done:
        print(f'{done_cnt} marked done - run hq list --done to see them')
    return 0
