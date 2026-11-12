"""The finish verb: checks every condition, then writes the cycle.
"""

import argparse
import contextlib
import os
import pathlib
import re

from handoff import carry, ledger, markdown, paths, progress, render, standing
from handoff import storage, threads

_TYPED_PREFIX = re.compile(r'(?:summary|stall review):\s*', re.IGNORECASE)


def _advise_collisions(
    folder: pathlib.Path,
    walk: list[tuple[str, str]],
    live: dict[str, storage.Row],
    cursor_clean: str,
) -> None:
    """Print each term of the Now step that matches a live dead end or a
    spec heading.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder.
    walk : list[tuple[str, str]]
        ``(name, kind)`` per entry of ``ledger.walk_folder``.
    live : dict[str, storage.Row]
        Latest ledger row by stored path.
    cursor_clean : str
        The cursor with ``## Unfiled`` drained. Without a ``## Now``
        section nothing prints.
    """
    standing_path = folder / 'standing.md'
    now_m = re.search(r'^## Now\s*$', cursor_clean, re.MULTILINE)
    if now_m:
        rest = cursor_clean[now_m.end():]
        nm2 = re.search(r'^## ', rest, re.MULTILINE)
        now_text = rest[:nm2.start()] if nm2 else rest
        standing_text_adv = (
            standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
            )
        s_items_adv, s_sup_adv = standing.parse_standing(standing_text_adv)
        dead_end_headlines = [
            (i['headline'], i['id']) for i in s_items_adv
            if i['prefix'] == 'x' and i['id'] not in s_sup_adv
            ]
        spec_headings: list[tuple[str, int, str]] = []
        now_terms = carry.extract_terms(now_text)
        rarity: dict[str, int] = dict.fromkeys(now_terms, 0)
        for name, kind in walk:
            p = folder / name
            if not p.is_file():
                continue
            try:
                file_text = p.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for term in now_terms:
                if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', file_text):
                    rarity[term] += 1
            row = live.get(name)
            is_live_spec = (
                row['kind'] == 'spec' and row['status'] == 'live' if row
                else kind == 'spec')
            if not is_live_spec:
                continue
            for i, ln in enumerate(file_text.splitlines()):
                if ln.startswith('#'):
                    spec_headings.append((name, i + 1, ln))
        for hit in carry.collisions(
            now_text, dead_end_headlines, spec_headings, rarity):
            print(
                f'advisory: {hit}'
                ' - check that the Now step does not retry a rejected idea')


def _advise_files(
    folder: pathlib.Path,
    walk: list[tuple[str, str]],
    rows: list[storage.Row],
    live: dict[str, storage.Row],
    cycle: int,
) -> str:
    """Print the folder's unstamped, misplaced, unstampable, missing and
    dangling entries.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder.
    walk : list[tuple[str, str]]
        ``(name, kind)`` per entry of ``ledger.walk_folder``.
    rows : list[storage.Row]
        Every ledger row, oldest first.
    live : dict[str, storage.Row]
        Latest ledger row by stored path.
    cycle : int
        This cycle. Only a path first stamped in it counts as misplaced.

    Returns
    -------
    str
        The pinned work dir, or ``''`` without a valid pin.
    """
    unstamped_entries = [
        (n, k) for n, k in walk if not ledger.is_recorded(n, live) and k != 'skip']
    if unstamped_entries:
        by_kind: dict[str, list[str]] = {}
        for n, k in unstamped_entries:
            by_kind.setdefault(k, []).append(n)
        for k, names in sorted(by_kind.items()):
            shown = ', '.join(names[:5])
            tail = f' ... and {len(names) - 5} more' if len(names) > 5 else ''
            print(f'advisory: unstamped {k} x{len(names)}: {shown}{tail} - stamp each')
    # --- Advisory: made files placed against the work dir ---
    # Notes:
    # - Only a path first stamped this cycle is named: an older row sat
    #   under an earlier ruling, and moving it is the agent's call.
    # - A directory is an organized unit under either ruling, so only a
    #   loose top-level file is named. Under a pin, a file in specs/,
    #   drafts/ or outputs/ is named too, since the pin claims them.
    wd_value, wd_lines = paths.resolve_work_dir(folder)
    for line in wd_lines:
        print(line)
    first_cycle: dict[str, int] = {}
    for row in rows:
        if row['cycle'].strip().isdigit():
            first_cycle.setdefault(row['path'], int(row['cycle']))
    claimed_dirs = {'specs', 'drafts', 'outputs'} if wd_value else set()
    misplaced = [
        path for path, row in live.items()
        if row['base'] == 'folder' and row['status'] == 'live'
        and row['kind'] in {'spec', 'draft', 'other'}
        and first_cycle.get(path) == cycle
        and ('/' not in path or ledger.kind_dir_of(path, 'folder') in claimed_dirs)]
    if misplaced:
        shown = ', '.join(misplaced[:5])
        tail = f' ... and {len(misplaced) - 5} more' if len(misplaced) > 5 else ''
        if wd_value:
            print(
                f'advisory: made in the folder x{len(misplaced)}: {shown}{tail}'
                f' - move each to {wd_value}, or under notes/ when it is'
                ' evidence, then re-stamp with --successor and the'
                " file's ~ or absolute path")
        else:
            print(
                f'advisory: made at the top level x{len(misplaced)}: {shown}{tail}'
                ' - move each under specs/, drafts/, notes/, or outputs/'
                ' and re-stamp with --successor')
    for n, k in walk:
        if k != 'skip':
            continue
        if 'conflicted copy' in n:
            print(
                f'advisory: conflicted copy: {n}'
                ' - a sync duplicate; resolve it by hand')
        else:
            shown = n.replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
            print(
                f'advisory: unstampable name: {shown}'
                ' - rename or remove it, or leave it out of the ledger')
    # --- Advisory: missing abs ---
    for p_key, row in live.items():
        if row['base'] == 'abs' and row['status'] == 'missing':
            print(
                f'advisory: abs path not on disk: {p_key}'
                ' - re-stamp the right path, or --successor / --archive --reason')
    for p_key, successor in ledger.dangling_successors(folder, live):
        print(
            f'advisory: successor missing: {p_key} -> {successor}'
            ' - name a new successor or archive the row')
    return wd_value


def _advise_one_line_spans(
    folder: pathlib.Path,
    live: dict[str, storage.Row],
) -> None:
    """Print each one-line span of a live ``always`` row whose next line
    is a heading of the same level.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; a ``folder`` row resolves against it.
    live : dict[str, storage.Row]
        Latest ledger row by stored path.
    """
    # Markdown reads each `##` line as a heading, so a heading wrapped
    # onto a second marker line ends its own span after one line.
    for p_key, row in live.items():
        if (row['status'] != 'live' or row['read_before'] != 'always'
                or row['where'] == '-'):
            continue
        target = (
            pathlib.Path(p_key).expanduser() if row['base'] == 'abs'
            else folder / p_key)
        if not target.is_file():
            continue
        try:
            span_text = target.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        span_lines = span_text.splitlines()
        spans, _ = markdown.resolve_where(span_text, markdown.split_where(row['where']))
        for start, end in spans:
            if start != end or end >= len(span_lines):
                continue
            heading_line, next_line = span_lines[start - 1], span_lines[end]
            level = len(heading_line) - len(heading_line.lstrip('#'))
            if (next_line.startswith('#')
                    and len(next_line) - len(next_line.lstrip('#')) == level):
                print(
                    f'advisory: one-line span at {p_key}:{start}; a wrapped heading?'
                    ' - anchor the last wrapped line, or join the heading'
                    ' where the file may be edited')


def _advise_cited_ids(cursor_clean: str, standing_text_new: str) -> None:
    """Print each standing id the cursor cites that is superseded or
    retired.

    Parameters
    ----------
    cursor_clean : str
        The cursor with ``## Unfiled`` drained. ``## Directives`` is not
        searched.
    standing_text_new : str
        standing.md with this cycle's lines appended.
    """
    # A Directives line changes only under --replace-directives, so an
    # advisory on one would repeat at every finish.
    cited_text = re.sub(
        r'^## Directives[ \t]*$.*?(?=^## |\Z)', '', cursor_clean,
        flags=re.MULTILINE | re.DOTALL)
    cited_at = [
        (m.start(1), m.group(1)) for m in standing.CITED_ID.finditer(cited_text)
        if m.group(1)[0] in 'de'
        ]
    # hq spells a cycle as c12 and x86 is a common word, so a c or x id
    # counts only inside a bracket group.
    for group_m in re.finditer(r'\[([^\[\]]*)\]', cited_text):
        tokens = [token.strip() for token in group_m.group(1).split(',')]
        if all(re.fullmatch(r'[A-Za-z]+\d+', token) for token in tokens):
            cited_at += [
                (group_m.start(), token) for token in tokens
                if re.fullmatch(r'[cdxe]\d{2,}', token)
                ]
    chains = standing.supersession_chains(standing_text_new)
    not_live_ids = standing.parse_standing(standing_text_new)[1]
    cited_ids = [item_id for _, item_id in sorted(cited_at, key=lambda pair: pair[0])]
    for item_id in dict.fromkeys(cited_ids):
        chain_entry = chains.get(item_id)
        if not chain_entry:
            continue
        chain, _, retirement = chain_entry
        if retirement:
            hops = f'{item_id} -> {chain[-1]}' if len(chain) > 1 else item_id
            print(
                f'advisory: cited id retired: {hops} in c{retirement[0]}'
                ' - drop the citation or cite what replaced it')
            continue
        # A chain that loops ends on a superseded id, so it has no live
        # end to name.
        if chain[-1] in not_live_ids:
            continue
        current_id = chain[-1]
        print(
            f'advisory: cited id superseded: {item_id} -> {current_id}'
            f' - cite {current_id} in its place')


def _advise_contradictions(
    folder: pathlib.Path,
    standing_text_new: str,
    cycle: int,
) -> None:
    """Print each live evidence item that contradicts another live item.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder, named in the count line.
    standing_text_new : str
        standing.md with this cycle's lines appended.
    cycle : int
        This cycle. A pair from an earlier cycle prints only in the count
        line.
    """
    standing_items_new, not_live_ids = standing.parse_standing(standing_text_new)
    live_ids = {i['id'] for i in standing_items_new} - not_live_ids
    older_pair_cnt = 0
    pair_cnt = 0
    for item in standing_items_new:
        if item['prefix'] != 'e' or item['id'] not in live_ids:
            continue
        targets = re.findall(r'contradicts \[([dcxe]\d+)\]', item['body'])
        for target in dict.fromkeys(t for t in targets if t in live_ids):
            pair_cnt += 1
            if item['cycle'] != str(cycle):
                older_pair_cnt += 1
                continue
            print(
                f'advisory: contradiction: {item["id"]} contradicts {target},'
                ' both live - re-check both; supersede or retire one only if'
                ' it no longer holds')
    if older_pair_cnt:
        noun = 'contradiction' if pair_cnt == 1 else 'contradictions'
        print(
            f'advisory: {pair_cnt} live {noun} - hq standing'
            f' {folder.name} --kind evidence --grep "contradicts \\["')


def verb_finish(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Close the cycle: refuse on any failed check, else write it all.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to close the cycle in.
    anch : dict
        Anchors for this invocation.
    argv : argparse.Namespace
        Parsed flags. ``--log`` is required. ``--acknowledge`` passes a
        witness break. ``--replace-directives`` passes a changed
        ``## Directives`` section and records its reason in a decision
        that supersedes every live ``Directives replaced`` decision.

    Returns
    -------
    int
        0 once everything is written. 1 when a check refuses, with
        nothing written.
    """
    if (folder / 'HANDOFF.md').is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    lock = storage.read_lock(folder)
    session = anch['session']
    if lock.get('session') != session:
        print(
            f'hq finish: lock held by {lock.get("session", "none")}; this is {session}'
            f' - run hq begin {folder.name} first')
        return 1
    ledger_path = folder / 'ledger.tsv'
    standing_path = folder / 'standing.md'
    manifest_path = folder / 'cycles' / 'manifest.tsv'
    blocked = []
    for target in (
            folder / 'HANDOFF.md',
            folder / 'cycles' / f'c{anch["cycle"]:02d}.md', manifest_path):
        # Notes:
        # - A missing target is created, so the nearest existing
        #   directory must be writable: `cycles/` is made here.
        # - An existing target must be a regular file, since a retry
        #   cannot move a directory aside.
        # - standing.md takes no entry: its append runs only where a
        #   note drained and reports its own failure.
        base = target
        while not base.exists():
            base = base.parent
        if not os.access(base, os.W_OK) or (base == target
                                            and not target.is_file()):
            blocked.append(target)
    if blocked:
        for target in blocked:
            print(
                f'hq finish: cannot write {target.relative_to(folder)}'
                ' - move it aside or restore its permissions, then re-run')
        return 1
    walk, rows, live, sha_map, manifest, lb, sb = ledger.folder_state(folder)
    ack = ' '.join((getattr(argv, 'acknowledge', None) or '').split())
    breaks = storage.witness(manifest[-1] if manifest else None, lb, sb)
    if breaks and not ack:
        for b in breaks:
            print(b)
        return 1
    if breaks:
        for b in breaks:
            print(f'acknowledged: {b}')
    elif ack:
        print('advisory: --acknowledge given, no witness broke')
    absent = [
        r for r in live.values()
        if r['status'] == 'live' and r['read_before'] in ledger.GATE_RB
        and r['base'] == 'folder' and not (folder / r['path']).exists()
        ]
    # A row lifted from missing back to live faces R3 like any other.
    live = ledger.reconcile_missing(folder, live)
    r3_rows = ledger.check_r3(list(live.values()), sha_map)
    if r3_rows:
        for row in r3_rows:
            print(f'R3: {row["path"]} sha moved; re-stamp before finish')
        return 1
    if absent:
        for row in absent:
            print(f'missing live gated: {row["path"]}; use --successor or --archive --reason')
        return 1
    handoff_path = folder / 'HANDOFF.md'
    handoff_text = handoff_path.read_text(encoding='utf-8')
    marker_m = re.search(r'^<!-- hq:\w+ \S+ -->', handoff_text, re.MULTILINE)
    first_marker = marker_m.start() if marker_m else -1
    if first_marker >= 0 and re.search(
            r'^## Unfiled\s*$', handoff_text[first_marker:], re.MULTILINE):
        print(
            "hq finish: '## Unfiled' sits below the first hq: marker;"
            ' move it above')
        return 1
    parsed = markdown.split_handoff(handoff_text)
    items, cursor_clean, refusal = standing.drain_unfiled(parsed['cursor'])
    if refusal:
        print(f'hq finish: {refusal}')
        return 1
    # --- Refusal: directives changed ---
    replace_why = ' '.join((getattr(argv, 'replace_directives', None) or '').split())
    directives_old: str | None = None
    if manifest and manifest[-1].get('rewrite_sha', '-') in {'', '-'}:
        prev_archive = folder / 'cycles' / f'c{int(manifest[-1]["cycle"]):02d}.md'
        if prev_archive.is_file():
            directives_old = markdown.directives_text(markdown.split_handoff(
                prev_archive.read_text(encoding='utf-8', errors='replace'))['cursor'])
    directives_new = markdown.directives_text(cursor_clean)
    if directives_old is None:
        if replace_why:
            print(
                'advisory: --replace-directives given, no earlier cycle to'
                ' compare - drop the flag')
        replace_why = ''
    elif directives_old != directives_new and not replace_why:
        print(
            f'hq finish: ## Directives changed since c{int(manifest[-1]["cycle"]):02d}'
            ' - carry it unchanged, or pass --replace-directives "<why>" when'
            ' the user changed it')
        return 1
    elif directives_old == directives_new and replace_why:
        print(
            'hq finish: --replace-directives given but ## Directives is unchanged'
            f' since c{int(manifest[-1]["cycle"]):02d} - drop the flag')
        return 1
    _advise_collisions(folder, walk, live, cursor_clean)
    wd_value = _advise_files(folder, walk, rows, live, int(anch['cycle']))
    _advise_one_line_spans(folder, live)
    standing_text_new = (
        standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
        )
    new_note_lines: list[str] = []
    for kind_str, headline, body in items:
        line = standing.note_line(
            standing_text_new, kind_str, headline, body, anch['cycle'])
        if line is None:
            print(f'hq finish: unknown item kind {kind_str!r}')
            return 1
        new_note_lines.append(line)
        standing_text_new += line + '\n'
    if replace_why:
        standing_items_old, not_live_old = standing.parse_standing(standing_text_new)
        line = standing.note_line(
            standing_text_new, 'decision', 'Directives replaced',
            f'{replace_why.rstrip(".")}. Previous: {directives_old or "none"}',
            anch['cycle'])
        new_id = standing.parse_standing(line)[0][0]['id']
        new_note_lines.append(line)
        standing_text_new += line + '\n'
        # A supersession line leaves the old decision's text in
        # standing.md, where not_carried still reads it.
        for item in standing_items_old:
            if (item['prefix'] == 'd'
                and item['headline'] == 'Directives replaced'
                and item['id'] not in not_live_old):
                retire = f'- (c{anch["cycle"]}) {item["id"]} -> {new_id}'
                new_note_lines.append(retire)
                standing_text_new += retire + '\n'
    # --- Advisory: cursor lines not carried ---
    # Every line prints: once this cycle files, hq open --not-carried
    # compares against it and lists none of them.
    prev_cycle, dropped_lines = carry.not_carried(
        folder, rows, live, manifest, cursor_clean, standing_text_new,
        int(anch['cycle']))
    if dropped_lines:
        print(
            f'advisory: {len(dropped_lines)} cursor lines from'
            f' c{prev_cycle:02d} not carried - cycles/c{prev_cycle:02d}.md'
            ' keeps each; carry back any a later session needs')
        for dropped in dropped_lines:
            print(f'  not carried: {dropped.strip()}')
    _advise_cited_ids(cursor_clean, standing_text_new)
    _advise_contradictions(folder, standing_text_new, int(anch['cycle']))
    log_text = ' '.join((getattr(argv, 'log', '') or '').split())
    typed_ruling = False
    while prefix_m := _TYPED_PREFIX.match(log_text):
        typed_ruling = typed_ruling or prefix_m.group(0).lower().startswith('stall')
        log_text = log_text[prefix_m.end():]
    probe = folder / 'probes' / 'hq-measure'
    has_probe = probe.is_file() and os.access(probe, os.X_OK)
    loop_dir = threads.own_loop_dir(folder.name)
    stored_log = log_text
    ruling_text = None
    met, met_reason = False, ''
    if loop_dir is not None:
        with contextlib.suppress(OSError):
            ruling_text = ' '.join((loop_dir / 'ruling').read_text(
                encoding='utf-8', errors='replace').split())
    if has_probe:
        root = folder.parent.parent
        probe_row, met, met_reason = progress.run_probe(
            probe, root / pathlib.Path(wd_value).expanduser() if wd_value else root)
        if (loop_dir is None and typed_ruling
            and progress.stall_count(manifest)['reported'] >= 3):
            ruling_text = log_text
        if ruling_text is None:
            stored_log = f'summary: {probe_row} | {log_text or "-"}'
        elif loop_dir is None:
            stored_log = f'summary: {probe_row} | stall review: {log_text or "-"}'
        else:
            ruling_text = ruling_text.replace('|', '/') or '-'
            stored_log = (
                f'summary: {probe_row} | stall review: {ruling_text}'
                f' | {log_text or "-"}')
    elif ruling_text is not None:
        ruling_text = ruling_text.replace('|', '/') or '-'
        stored_log = (
            f'summary: unmeasured | stall review: {ruling_text}'
            f' | {log_text or "-"}')
    stall_result = progress.stall_count(
        manifest + [{'cycle': str(anch['cycle']), 'log': stored_log}])
    last_worked = anch['now'][:10]
    if threads.worked_thread(str(session), folder) != str(folder.resolve()):
        prior_m = (
            markdown.WORKED_PAT.search(parsed['header'])
            or re.search(r'Written:\s*(\d{4}-\d{2}-\d{2})', parsed['header']))
        if prior_m:
            last_worked = prior_m.group(1)
        elif manifest:
            last_worked = manifest[-1]['written']
    branch, sha7, dirty = anch['branch'], anch['sha'], anch['dirty']
    header_line = (
        f'Written: {anch["now"][:10]} | Cycle: {anch["cycle"]}'
        f' | Last worked: {last_worked}')
    if branch != '-':
        header_line += f' | {branch} @ {sha7}{storage.dirty_str(dirty)}'
    repos = f'{branch}@{sha7}' if branch != '-' else '-'
    if dirty:
        repos += f' +{len(dirty)}'
    log_body = render.render_log(manifest + [{
        'cycle': str(anch['cycle']),
        'written': anch['now'][:10],
        'repos': repos,
        'log': stored_log,
        }], folder.name)
    new_handoff = render.assemble_handoff(
        folder, cursor_clean, header_line, log_body,
        live, walk, standing_text_new, int(anch['cycle']))
    (folder / 'cycles').mkdir(exist_ok=True)
    # The archive precedes HANDOFF.md, so a failed HANDOFF.md write
    # leaves the new text whole in cycles/.
    cycle_archive = folder / 'cycles' / f'c{anch["cycle"]:02d}.md'
    cycle_archive.write_text(new_handoff, encoding='utf-8')
    if new_note_lines:
        storage.append_lines(standing_path, new_note_lines)
    handoff_path.write_text(new_handoff, encoding='utf-8')
    lb_final = ledger_path.read_bytes()
    sb_final = standing_path.read_bytes() if standing_path.exists() else b''
    handoff_sha = storage.sha12(new_handoff.encode())
    cursor_lines = len(cursor_clean.splitlines())
    payload_tokens = len(new_handoff) // 4
    blocks_new = markdown.split_handoff(new_handoff).get('blocks', {})
    token_split = ', '.join(
        f'{name} {len(blocks_new.get(name, "")) // 4}'
        for name in ('read', 'artifacts', 'standing'))
    takeover = ' '.join(lock.get('takeover', '').split())
    manifest_session = f'{session} took over {takeover}' if takeover else session
    note_parts: list[str] = []
    if breaks and ack:
        note_parts.append(f'acknowledged {"; ".join(breaks)}; {ack}')
    if dropped_lines:
        note_parts.append(
            f'{len(dropped_lines)} not carried from c{prev_cycle:02d}')
    note_val = ' | '.join(note_parts) or '-'
    manifest_row: dict = {
        'cycle': str(anch['cycle']),
        'written': anch['now'][:10],
        'session': manifest_session,
        'repos': repos,
        'cursor_lines': str(cursor_lines),
        'payload_tokens': str(payload_tokens),
        'handoff_sha': handoff_sha,
        'ledger_bytes': str(len(lb_final)),
        'ledger_sha': storage.sha12(lb_final),
        'standing_bytes': str(len(sb_final)),
        'standing_sha': storage.sha12(sb_final),
        'log': stored_log,
        'note': note_val,
        'stall': (
            str(stall_result['stall'])
            if has_probe and stall_result['stall'] is not None else '-'),
        }
    storage.append_tsv(
        manifest_path, storage.MANIFEST_FIELDS, manifest_row, storage.MANIFEST_HEADER)
    (folder / '.hq.lock').unlink(missing_ok=True)
    print(
        f'{handoff_path}  {cursor_lines} cursor lines  {payload_tokens} tokens'
        f' (cursor {len(cursor_clean) // 4}, {token_split})')
    if has_probe:
        print(f'stall: {manifest_row["stall"]}')
    if loop_dir is not None and ruling_text is not None:
        (loop_dir / 'hold').unlink(missing_ok=True)
        (loop_dir / 'ruling').unlink(missing_ok=True)
    elif loop_dir is not None and has_probe:
        if met:
            hold_line = (
                f'finish line met: {met_reason}. To go past it, the ruling'
                ' gives a new goal for probes/hq-measure.')
        elif (stall_result['stall'] or 0) >= progress.STALL_PARK:
            hold_line = (
                f'stall: held at {stall_result["stall"]}'
                f' ({stall_result["cause"] or "no measured gain"}).')
        else:
            hold_line = ''
        if hold_line:
            (loop_dir / 'hold').write_text(hold_line + '\n', encoding='utf-8')
            print(f'{hold_line} {progress.HOLD_TEXT}')
    # The change shows what the level erases, every reset. This is the
    # only place a session sees the erasure while it can still act.
    prev_tokens = manifest[-1].get('payload_tokens', '') if manifest else ''
    if prev_tokens.isdigit():
        print(
            f'{payload_tokens - int(prev_tokens):+d} tok'
            f' since c{manifest[-1]["cycle"]}')
    rf_rows, rf_spans, _, rf_tokens = render.read_first_data(folder, live)
    anchored = sum(
        1 for r in rf_rows
        if any(s is not None for s in rf_spans.get(r['path'], [])))
    print(
        f'read first: {len(rf_rows)} rows, {sum(rf_tokens.values())} tok'
        f' ({anchored} anchored, {len(rf_rows) - anchored} whole)')
    print(f'resume: /handoff {folder.name}')
    return 0
