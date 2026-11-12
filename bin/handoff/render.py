"""The rendered blocks of HANDOFF.md.
"""

import pathlib
import re

from handoff import ledger, markdown, paths, standing, storage

_LABEL_RESIDENT = 120


def render_read(
    rows: list[storage.Row],
    spans: dict[str, list[tuple[int, int] | None]],
    sizes: dict[str, str],
    shown: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Render the hq:read block body.

    Parameters
    ----------
    rows : list[Row]
        Live rows with ``read_before='always'``.
    spans : dict[str, list[tuple[int, int] | None]]
        Resolved spans per path; ``None`` for an unresolved anchor.
    sizes : dict[str, str]
        Size text per path, as ``read_first_data`` returns it.
    shown : dict[str, tuple[str, str]] | None, default None
        Display path and base phrase per stored path, as ``shown_paths``
        returns it. A path absent from it prints as stored.

    Returns
    -------
    str
        One line per row, sorted by kind then path:
        ``path:11-13  (320 tok)  label`` for an anchored row and
        ``path  (186 lines, 8.2k tok)  label`` for a whole file. A first
        line names every base the printed paths resolve from.
    """
    out = []
    bases: set[str] = set()
    for row in sorted(rows, key=lambda r: (r['kind'], r['path'])):
        path = row['path']
        display, base = (shown or {}).get(path, (path, ''))
        if base:
            bases.add(base)
        size = sizes.get(path, '-')
        label = row['label']
        if row['where'] == '-':
            out.append(f'{display}  ({size})  {label}')
        else:
            parts = [
                '?' if span is None else f'{span[0]}-{span[1]}'
                for span in spans.get(path, [])
                ]
            out.append(f'{display}:{",".join(parts) or "?"}  ({size})  {label}')
    if bases:
        out.insert(0, '; '.join(sorted(bases)))
    return '\n'.join(out)


def artifact_lines(
    walk: list[tuple[str, str]],
    rows: dict[str, storage.Row],
    slug: str,
    whole_paths: set[str],
    shown: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[str], list[str], dict[str, int], set[str]]:
    """Sort artifacts into full lines, folded paths, a non-live count, bases.

    Parameters
    ----------
    walk : list[tuple[str, str]]
        Top-level entries as ``(path, inferred_kind)``, ``'skip'`` entries
        already excluded.
    rows : dict[str, Row]
        Latest ledger row per path, with the caller's ``missing`` marks
        applied. The disk is never consulted.
    slug : str
        Handoff slug for the ``hq when`` command a capped label names.
    whole_paths : set[str]
        Stored paths that print a full line when their row is live.
    shown : dict[str, tuple[str, str]] | None, default None
        Display path and base phrase per stored path, as ``shown_paths``
        returns it. A path absent from it prints as stored.

    Returns
    -------
    tuple[list[str], list[str], dict[str, int], set[str]]
        The full lines and the display paths of the other live rows,
        both uncapped and in walk order, then ledger order for the
        rows the walk cannot see; the rows no longer live counted by
        status; the base phrases the emitted lines print against.

    Notes
    -----
    - A full line is ``path  kind  read_before  cN  label`` for a live row
      in ``whole_paths``, or ``path  kind?  unstamped`` for a walk entry
      with no row.
    - An ``always`` row prints no label, since ``render_read`` already
      prints it for every live ``always`` row.
    - A base appears only where an emitted line resolves from it.
    """
    full_lines: list[str] = []
    folded_paths: list[str] = []
    non_live: dict[str, int] = {}
    bases: set[str] = set()
    seen: set[str] = set()

    def _classify(path: str, row: storage.Row | None, inferred: str) -> None:
        """Emit one line for a walk entry or an out-of-walk row.

        Parameters
        ----------
        path : str
            Relative or stored path of the artifact.
        row : Row | None
            Latest ledger row for the path, ``None`` when unstamped.
        inferred : str
            Kind inferred from the walk, used only when ``row`` is None.
        """
        display, base = (shown or {}).get(path, (path, ''))
        if row is None:
            full_lines.append(f'{display}  {inferred}?  unstamped')
            if base:
                bases.add(base)
            return
        status, rb = row['status'], row['read_before']
        if status != 'live':
            non_live[status] = non_live.get(status, 0) + 1
        else:
            if path not in whole_paths:
                folded_paths.append(display)
            else:
                stem = f'{display}  {row["kind"]}  {rb}  c{row["cycle"]}'
                if rb == 'always':
                    full_lines.append(stem)
                else:
                    label = row['label']
                    resident = resident_label(label)
                    full_lines.append(holdback_line(
                        f'{stem}  {resident}',
                        len(label) - len(resident),
                        f'hq when {slug} {display}'))
            if base:
                bases.add(base)

    for path, inferred in walk:
        seen.add(path)
        if rows.get(path) is None and ledger.is_recorded(path, rows):
            continue
        _classify(path, rows.get(path), inferred)

    # Rows the walk cannot see: abs paths and subdirectory paths.
    for path, row in rows.items():
        if path not in seen:
            _classify(path, row, row.get('kind', 'other'))
    return full_lines, folded_paths, non_live, bases


def render_artifacts(
    walk: list[tuple[str, str]],
    rows: dict[str, storage.Row],
    slug: str,
    whole_paths: set[str],
    shown: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Render the hq:artifacts block body.

    Parameters
    ----------
    walk : list[tuple[str, str]]
        Top-level entries as ``(path, inferred_kind)``, ``'skip'`` entries
        already excluded.
    rows : dict[str, Row]
        Latest ledger row per path, with the caller's ``missing`` marks
        applied. The disk is never consulted.
    slug : str
        Handoff slug for the hq command in count lines.
    whole_paths : set[str]
        Stored paths that print a full line when their row is live.
    shown : dict[str, tuple[str, str]] | None, default None
        Display path and base phrase per stored path, as ``shown_paths``
        returns it. A path absent from it prints as stored.

    Returns
    -------
    str
        Block body lines joined with newlines: every live row in
        ``whole_paths`` and every unstamped entry as a full line, then
        every other live row as its path alone and one closing line
        naming ``hq when <slug> <path>``, then the rows no longer live
        counted by status. A first line names every base the printed
        paths resolve from.

    Notes
    -----
    - The closing line keeps ``<path>`` as a placeholder, since it serves
      every path-only line above it.
    """
    full_lines, folded_paths, non_live, bases = artifact_lines(
        walk, rows, slug, whole_paths, shown)
    out = ['; '.join(sorted(bases))] if bases else []
    out += full_lines + folded_paths
    if folded_paths:
        out.append(f'hq when {slug} <path> prints any row above whole')
    if non_live:
        ordered = [k for k in ledger.NON_LIVE_ORDER if k in non_live]
        ordered += sorted(k for k in non_live if k not in ordered)
        for status in ordered:
            out.append(
                f'{status} {non_live[status]}'
                f'  - hq artifacts {slug} --status {status}')
    return '\n'.join(out)


def resident_label(label: str) -> str:
    """The label text an index line carries, cut at a word boundary.

    Parameters
    ----------
    label : str
        The label as ledger.tsv stores it, of any length.

    Returns
    -------
    str
        The label unchanged within ``_LABEL_RESIDENT`` characters, else
        its head cut at the last space inside that width, or at the width
        itself when that space would leave under half of it.
    """
    if len(label) <= _LABEL_RESIDENT:
        return label
    head = label[:_LABEL_RESIDENT]
    cut = head.rfind(' ')
    return (head[:cut] if cut > _LABEL_RESIDENT // 2 else head).rstrip()


def holdback_line(resident: str, held_chars: int, command: str) -> str:
    """Join resident text to its held-back count and the command for the rest.

    Parameters
    ----------
    resident : str
        The text the block carries, with no trailing whitespace.
    held_chars : int
        Characters of the item left unprinted; 0 or less prints no suffix.
    command : str
        The command that prints the held text, runnable exactly as
        written. A placeholder in it is a defect, since no reader can
        fill one from the rendered file.

    Returns
    -------
    str
        ``<resident>  +<held>c - <command>``, or ``resident`` unchanged
        when nothing is held back.
    """
    if held_chars <= 0:
        return resident
    return f'{resident}  +{held_chars}c - {command}'


def render_standing(
    items: list[dict],
    superseded_ids: set[str],
    slug: str,
    whole_ids: set[str],
) -> str:
    """Render the hq:standing block body.

    Parameters
    ----------
    items : list[dict]
        All standing items with keys: id, prefix, cycle, headline, body.
    superseded_ids : set[str]
        Item ids targeted by a supersession line.
    slug : str
        Handoff slug for the hq command a held-back line ends in.
    whole_ids : set[str]
        Ids of the items that render whole.

    Returns
    -------
    str
        Block body, each kind under its heading and newest cycle first
        within it. A live item in ``whole_ids`` prints whole, and every
        other live item as ``[id] headline``. Evidence outside
        ``whole_ids`` prints nothing and is counted on one closing line
        of its group. A closing line naming ``hq standing <slug> <id>``
        follows when any item printed as ``[id] headline``.

    Notes
    -----
    - The sort keys on the cycle alone and is stable, so a cycle's own
      items stay in the order they were recorded.
    - The closing line keeps ``<id>`` as a placeholder, since it serves
      every ``[id] headline`` line above it.
    - A printed evidence item that a live evidence item's body
      contradicts, ``contradicts [<id>]``, carries ``contradicted by
      [<id>, ...]`` after its headline.
    """
    live = [i for i in items if i['id'] not in superseded_ids]
    contradicted_by: dict[str, list[str]] = {}
    for item in sorted(live, key=lambda i: int(i['id'][1:])):
        if item['prefix'] == 'e':
            targets = re.findall(r'contradicts \[([dcxe]\d+)\]', item['body'])
            for target in dict.fromkeys(targets):
                contradicted_by.setdefault(target, []).append(item['id'])
    out: list[str] = []
    folded = False
    for prefix, heading, body_mode in (
        ('c', '### Constraints', 'sentence'),
        ('d', '### Decisions', 'headline'),
        ('x', '### Dead ends', 'headline'),
        ('e', '### Evidence', 'headline'),
    ):
        group = [i for i in live if i['prefix'] == prefix]
        if not group:
            continue
        group.sort(key=lambda i: -int(i.get('cycle') or 0))
        out.append(heading)
        uncited_cnt = 0
        for item in group:
            if item['id'] not in whole_ids and prefix == 'e':
                uncited_cnt += 1
                continue
            if item['id'] not in whole_ids:
                out.append(f'[{item["id"]}] {item["headline"]}')
                folded = True
                continue
            pfx = f'(c{item["cycle"]}) ' if item.get('cycle') else ''
            line = f'[{item["id"]}] {pfx}**{item["headline"]}**'
            if prefix == 'e' and item['id'] in contradicted_by:
                line += f' contradicted by [{", ".join(contradicted_by[item["id"]])}]'
            body = item.get('body', '')
            if body:
                if body_mode == 'sentence':
                    resident, held = standing.split_headline(body)
                    line = standing.join_headline_body(line, resident).rstrip()
                else:
                    held = body
                line = holdback_line(
                    line.rstrip(), len(held),
                    f'hq standing {slug} {item["id"]}')
            out.append(line.rstrip())
        if uncited_cnt:
            noun = 'item' if uncited_cnt == 1 else 'items'
            out.append(
                f'+{uncited_cnt} live evidence {noun}'
                f' - hq standing {slug} --kind evidence --grep <word>')
    if folded:
        out.append(f'hq standing {slug} <id> prints any item above whole')
    return '\n'.join(out)


def render_log(manifest: list[dict], slug: str, n: int = 3) -> str:
    """Render the ## Log block body.

    Parameters
    ----------
    manifest : list[dict]
        All finished cycles in order, each a dict keyed by MANIFEST_FIELDS.
    slug : str
        Folder name the rollup line's ``hq arc`` command names.
    n : int, default 3
        Recent cycles to show in full.

    Returns
    -------
    str
        One line per recent cycle. The older cycles collapse into one
        ``- cycles <a>-<b>`` line ending in the ``hq arc <slug>`` command.
    """
    if not manifest:
        return ''
    older = manifest[:-n] if len(manifest) > n else []
    recent = manifest[-n:]
    out: list[str] = []
    for row in recent:
        repos = row.get('repos', '-')
        if repos == '-':
            out.append(f'- {row["written"]}: {row["log"]}')
        else:
            out.append(
                f'- {row["written"]} (cycle {row["cycle"]}, {repos}): {row["log"]}')
    if older:
        first = manifest[0]['cycle']
        last_old = older[-1]['cycle']
        out.append(f'- cycles {first}-{last_old} - hq arc {slug}')
    return '\n'.join(out)


def render_arc(
    slug: str,
    manifest: list[dict],
    handoff_text: str,
    first_text: str) -> str:
    """Render the whole-thread arc: how it began, each cycle, where it stands.

    Parameters
    ----------
    slug : str
        Folder name the title line carries.
    manifest : list[dict]
        Every finished cycle in order, keyed by MANIFEST_FIELDS. Not empty.
    handoff_text : str
        The live HANDOFF.md, source of Task, Now, Open questions, Plan.
    first_text : str
        The first finished cycle's archive, ``''`` where it is absent.
        Source of Began as and the prior Log.

    Returns
    -------
    str
        The arc, newline-terminated::

            # Arc: <slug>  cycles <a>-<b>, <first written> to <last written>
            Began as (c<a>): <first sentence of the first cycle's Task>
            Task: <first sentence of ## Task>
            Before c<a> (the prior Log, cycles/c<a>.md):
            <every non-blank line of that archive's ## Log>
            History:
            - c<N> <written>  <manifest log>
            Now: <first sentence of ## Now>
            Open questions (<n>):
            - <first sentence of each item>
            Plan, open (<n>):
            - <first sentence of each open item>

    Notes
    -----
    - Began as prints only when it differs from Task. Before prints only
      for an adopted thread, whose first manifest log names the prior
      Log. Open questions print only when the section holds an item.
    - Every manifest row and every open item prints, with no cap.
    - A nested open Plan item keeps one level of indent.
    """
    parsed: list[dict[str, list[str]]] = []
    for text in (handoff_text, first_text):
        sections: dict[str, list[str]] = {}
        heading = ''
        for line in text.splitlines():
            if line.startswith('## '):
                heading = line[3:].strip()
                sections.setdefault(heading, [])
            elif line.startswith('<!-- hq:'):
                heading = ''
            elif heading:
                sections[heading].append(line)
        parsed.append(sections)
    current, first_cycle = parsed

    def first_sentence(lines: list[str]) -> str:
        """First sentence of wrapped lines joined, with bold markup dropped.
        """
        joined = ' '.join(ln.strip() for ln in lines if ln.strip())
        return standing.split_headline(joined.replace('**', ''))[0]

    first, last = manifest[0], manifest[-1]
    task = first_sentence(current.get('Task', []))
    began = first_sentence(first_cycle.get('Task', []))
    out = [
        (f'# Arc: {slug}  cycles {first["cycle"]}-{last["cycle"]},'
         f' {first["written"]} to {last["written"]}')
        ]
    if began and began != task:
        out.append(f'Began as (c{first["cycle"]}): {began}')
    out.append(f'Task: {task}')
    if first['log'].startswith('adopted; prior Log:'):
        out.append(
            f'Before c{first["cycle"]} (the prior Log,'
            f' cycles/c{int(first["cycle"]):02d}.md):')
        out.extend(ln for ln in first_cycle.get('Log', []) if ln.strip())
    out.append('History:')
    out.extend(f'- c{row["cycle"]} {row["written"]}  {row["log"]}' for row in manifest)
    out.append(f'Now: {first_sentence(current.get("Now", []))}')
    # An item opens at a bullet, a bold span, or a number at column
    # one, or after a blank line; an indented line continues it.
    asked: list[str] = []
    item: list[str] = []
    for line in current.get('Open questions', []) + ['']:
        if (re.match(r'(- |\*\*|\d+[.)] )', line) or not line.strip()) and item:
            asked.append(first_sentence(item))
            item = []
        if line.strip():
            item.append(re.sub(r'^(- |\d+[.)] )', '', line))
    if asked:
        out.append(f'Open questions ({len(asked)}):')
        out.extend(f'- {q}' for q in asked)
    opens: list[str] = []
    plan_item: list[str] | None = None
    depth = ''
    for line in current.get('Plan', []) + ['']:
        box = re.match(r'(\s*)- \[([ xX])\] (.*)', line)
        if plan_item is not None and not box and line.startswith(' ') and line.strip():
            plan_item.append(line)
            continue
        if plan_item is not None:
            opens.append(f'{depth}- {first_sentence(plan_item)}')
            plan_item = None
        if box and box.group(2) == ' ':
            depth = '  ' if box.group(1) else ''
            plan_item = [box.group(3)]
    out.append(f'Plan, open ({len(opens)}):')
    out.extend(opens)
    return '\n'.join(out) + '\n'


def read_first_data(
    folder: pathlib.Path,
    live: dict[str, storage.Row],
) -> tuple[
    list[storage.Row],
    dict[str, list[tuple[int, int] | None]],
    dict[str, str],
    dict[str, int],
]:
    """Resolve what ``hq read`` prints for each live ``always`` row.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder that folder-base paths are relative to.
    live : dict[str, Row]
        Latest ledger row per path, with any ``missing`` marks applied.

    Returns
    -------
    tuple
        ``(rows, spans, sizes, tokens)``: the live ``always`` rows; per
        anchored path, one span per anchor, ``None`` for one that matches
        no heading; per path, the size text of the read (``320 tok`` or
        ``1.2k tok`` for an anchored row, ``186 lines, 8.2k tok`` for a
        whole file, ``35 KB`` for a file that is not UTF-8 text,
        ``3 files`` for a directory); per path, the tokens of the read,
        ``len(text) // 4``.

    Notes
    -----
    - A row whose anchors all miss is read whole, so it sizes as a whole
      file.
    - A file gone from disk sizes by the ledger's line count. It and a
      non-text file or directory count no tokens.
    """
    rows = [
        r for r in live.values()
        if r['status'] == 'live' and r['read_before'] == 'always'
        ]
    spans: dict[str, list[tuple[int, int] | None]] = {}
    sizes: dict[str, str] = {}
    tokens: dict[str, int] = {}
    for row in rows:
        path = row['path']
        fp = pathlib.Path(path).expanduser() if row['base'] == 'abs' else folder / path
        tokens[path] = 0
        if fp.is_dir():
            sizes[path] = f'{storage.line_count(fp)} files'
            continue
        try:
            raw = fp.read_bytes()
        except OSError:
            sizes[path] = f'{row["lines"]} lines'
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            sizes[path] = f'{-(-len(raw) // 1024)} KB'
            continue
        if row['where'] != '-':
            anchor_list = markdown.split_where(row['where'])
            resolved, unresolved = markdown.resolve_where(text, anchor_list)
            bad = set(unresolved)
            resolved_iter = iter(resolved)
            spans[path] = [
                None if a in bad else next(resolved_iter) for a in anchor_list]
            if resolved:
                file_lines = text.splitlines()
                tokens[path] = sum(
                    len('\n'.join(file_lines[a - 1:b])) for a, b in resolved) // 4
                sizes[path] = ''
        if path not in sizes:
            tokens[path] = len(text) // 4
            line_cnt = raw.count(b'\n')
            sizes[path] = f'{line_cnt} lines, '
        tok = tokens[path]
        sizes[path] += f'{tok / 1000:.1f}k tok' if tok >= 1000 else f'{tok} tok'
    return rows, spans, sizes, tokens


def assemble_handoff(
    folder: pathlib.Path,
    cursor_text: str,
    header_line: str,
    log_body: str,
    live: dict[str, storage.Row],
    walk: list[tuple[str, str]],
    standing_text: str,
    cycle: int,
) -> str:
    """Render the generated blocks and return the full HANDOFF.md text.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder. Its name is the slug.
    cursor_text : str
        Cursor text, ``## Directives`` through ``## Open questions``.
    header_line : str
        The header line, as ``finish`` writes it.
    log_body : str
        Rendered log body from render_log.
    live : dict[str, Row]
        Latest ledger row per path, with in-memory missing marks applied.
    walk : list[tuple[str, str]]
        Top-level entries as (path, inferred_kind); skip entries included.
    standing_text : str
        Current text of standing.md.
    cycle : int
        Cycle being filed, whose items and stamps render whole.

    Returns
    -------
    str
        Complete HANDOFF.md text.
    """
    always_rows, read_spans, read_sizes, _ = read_first_data(folder, live)
    art_walk = [(n, k) for n, k in walk if k != 'skip']
    shown = paths.shown_paths(folder, live, tuple(n for n, _ in art_walk))
    read_body = render_read(always_rows, read_spans, read_sizes, shown)
    _rh = '## Read first\n' + read_body
    read_block = (
        f'<!-- hq:read {markdown.block_sha(_rh)} -->\n'
        f'## Read first\n{read_body}\n<!-- /hq:read -->'
    )
    # A name matches as a plain substring, so a short basename inside a
    # longer word keeps its row whole, at the cost of one line.
    whole_paths = {
        path for path, row in live.items()
        if row['read_before'] == 'always'
        or row['cycle'].strip() == str(cycle)
        or path in cursor_text
        or pathlib.PurePath(path).name in cursor_text}
    artifacts_body = render_artifacts(
        art_walk, live, folder.name, whole_paths, shown)
    _ah = '## Artifacts\n' + artifacts_body
    artifacts_block = (
        f'<!-- hq:artifacts {markdown.block_sha(_ah)} -->\n'
        f'## Artifacts\n{artifacts_body}\n<!-- /hq:artifacts -->'
    )
    s_items, s_sup = standing.parse_standing(standing_text)
    cited = set(standing.CITED_ID.findall(cursor_text))
    # A cycle that records many claims would print them all at the next
    # resume, so evidence expands only when cited.
    whole_ids = {
        item['id'] for item in s_items
        if item['id'] in cited
        or (item.get('cycle') == str(cycle) and item['prefix'] != 'e')}
    standing_body = render_standing(s_items, s_sup, folder.name, whole_ids)
    _sh = '## Standing\n' + standing_body
    standing_block = (
        f'<!-- hq:standing {markdown.block_sha(_sh)} -->\n'
        f'## Standing\n{standing_body}\n<!-- /hq:standing -->'
    )
    return (
        f'# Handoff: {folder.name}\n\n'
        f'{header_line}\n\n'
        f'{cursor_text.strip()}\n\n'
        f'{read_block}\n\n'
        f'{artifacts_block}\n\n'
        f'{standing_block}\n\n'
        f'## Log\n{log_body}\n'
    )
