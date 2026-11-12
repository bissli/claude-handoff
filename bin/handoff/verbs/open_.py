"""The open verb: status, drift, and stale paths for a thread.
"""

import argparse
import contextlib
import os
import pathlib
import re

from handoff import carry, help_text, ledger, markdown, paths, progress
from handoff import render, standing, storage, threads


def verb_open(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run open: print status, drift, W1/W2, sha-moved rows, and stale paths.

    A label the rendered block and the ledger disagree on is one of the
    drift classes reported. On the default path, where HANDOFF.md is a
    file, ``hq help read`` follows the findings, so the reading steps
    reach the agent when it reads rather than riding in the skill for
    the whole session; ``--not-carried`` prints its listing alone.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to inspect.
    anch : dict
        Anchors dict from ``anchors()``; provides current branch and sha.
    argv : argparse.Namespace
        Parsed open arguments (none beyond the common flags).

    Returns
    -------
    int
        0 always; findings print to stdout, never block.
    """
    walk, rows, live, sha_map, manifest, lb, sb = ledger.folder_state(folder)
    if getattr(argv, 'not_carried', False):
        hf_path = folder / 'HANDOFF.md'
        if not hf_path.is_file():
            print('not carried: no HANDOFF.md here - nothing to check')
            return 0
        cursor_now = markdown.split_handoff(
            hf_path.read_text(encoding='utf-8-sig', errors='replace'))['cursor']
        unfiled_items, cursor_drained, _ = standing.drain_unfiled(cursor_now)
        standing_path = folder / 'standing.md'
        standing_now = (
            standing_path.read_text(encoding='utf-8')
            if standing_path.exists() else '')
        # finish compares against standing.md with this cycle's Unfiled
        # items already filed, so the lister files them too; otherwise
        # it reports a line finish passes.
        for kind_str, headline, body in unfiled_items:
            line = standing.note_line(
                standing_now, kind_str, headline, body, int(anch['cycle']))
            if line is None:
                continue
            standing_now += line + '\n'
        if not manifest:
            print(
                'not carried: no finished cycle precedes this one'
                ' - nothing to compare against')
            return 0
        prev_cycle = int(manifest[-1]['cycle'])
        if not (folder / 'cycles' / f'c{prev_cycle:02d}.md').is_file():
            print(
                f'not carried: cycles/c{prev_cycle:02d}.md is absent, so'
                ' the comparand is gone - restore it before trusting a'
                ' count of zero')
            return 0
        _, dropped_now = carry.not_carried(
            folder, rows, ledger.reconcile_missing(folder, live), manifest,
            cursor_drained, standing_now, int(anch['cycle']))
        print(
            f'not carried from c{prev_cycle:02d}: {len(dropped_now)}'
            ' - carry each forward, or rehome each to a sibling and'
            ' stamp that sibling this cycle')
        for dropped in dropped_now:
            print(f'  not carried: {dropped.strip()}')
        return 0
    for b in storage.witness(manifest[-1] if manifest else None, lb, sb):
        print(f'WARNING: {b}')
    lock = storage.read_lock(folder)
    if lock:
        print(
            f'unfinished cycle {lock.get("cycle")} held by'
            f' {lock.get("session")} on {lock.get("host")}'
            ' - the file may be behind its stamps; report it')
    handoff_path = folder / 'HANDOFF.md'
    if handoff_path.is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    if not handoff_path.exists():
        return 0
    hf_text = handoff_path.read_text(encoding='utf-8-sig', errors='replace')
    parsed = markdown.split_handoff(hf_text)
    if manifest:
        file_cycle = parsed.get('cycle') or 0
        last_finished = int(manifest[-1]['cycle'])
        if file_cycle > last_finished:
            print(
                f'LEDGER BEHIND: file cycle {file_cycle}'
                f' > last finished {last_finished}'
                ' - the header was hand-edited; trust the ledger, report it')
    # Notes:
    # - A row re-stamped after the last finish leaves the rendered
    #   blocks holding a label the ledger has moved past. The block sha
    #   still matches, so no other check here tells.
    # - The cycle this session began is meant to run ahead of the
    #   blocks, so its own lock silences the comparison. A lock another
    #   session left is a cycle that died, and the desync it left is
    #   what this session has to know about.
    # - A row is judged by its rendered display path, so a display two
    #   live rows share, or one a work-dir repin has moved since the
    #   render, is passed over rather than guessed at.
    # - A block an older version rendered carries the stored path where
    #   this one prints the display, so the stored spelling is read
    #   when the display finds no line; the next finish leaves only
    #   the display.
    # - Both sides are compared stripped of trailing space: the block
    #   body loses it at its last line, and the ledger keeps it.
    # - The artifacts block carries a capped label and no label at all
    #   on an always row, so the comparand is the resident form and the
    #   holdback suffix comes off first. An always row is judged against
    #   the read block, the one place its label prints whole.
    shown = paths.shown_paths(folder, live)
    artifacts_body = parsed.get('blocks', {}).get('artifacts', '')
    read_body = parsed.get('blocks', {}).get('read', '')
    own_cycle = bool(lock) and lock.get('session') == anch['session']
    labeled = [] if own_cycle else [
        (p_key, row, shown.get(p_key, (p_key, ''))[0])
        for p_key, row in sorted(live.items())
        if row['status'] == 'live' and row['label'] != '-']
    displays = [display for _, _, display in labeled]
    for p_key, row, display in labeled:
        if displays.count(display) > 1:
            continue
        rendered: list[str] = []
        expected = render.resident_label(row['label'])
        for spelling in dict.fromkeys((display, p_key)):
            rendered = re.findall(
                '^' + re.escape(spelling) + r'  \S+  \S+  c\d+  (.*)$',
                artifacts_body, re.MULTILINE)
            if rendered:
                break
        if not rendered:
            expected = row['label']
            for spelling in dict.fromkeys((display, p_key)):
                rendered = re.findall(
                    '^' + re.escape(spelling)
                    + r'(?::[\d,?-]+)?  \([^)]*\)  (.*)$',
                    read_body, re.MULTILINE)
                if rendered:
                    break
        if len(rendered) == 1:
            block_label = rendered[0].rstrip()
            cut = block_label.rfind('  +')
            if cut != -1 and ' - hq when ' in block_label[cut:]:
                block_label = block_label[:cut]
            if block_label != expected.rstrip():
                print(
                    f'LEDGER BEHIND: block label differs: {p_key}'
                    ' - the block and the ledger disagree; trust the ledger,'
                    ' and the next finish re-renders the block')
    for row in ledger.check_r3(
        list(ledger.reconcile_missing(folder, live).values()), sha_map):
        print(
            f'sha moved since stamp: {row["path"]}'
            ' - read the file, not the span alone')
    # Git drift: compare header branch/sha against current git state.
    header_line = parsed.get('header', '')
    header_m = re.search(r'\|\s*(\S+)\s*@\s*([0-9a-f]+)', header_line)
    if header_m:
        h_branch, h_sha = header_m.group(1), header_m.group(2)
        cur_branch, cur_sha = anch['branch'], anch['sha']
        if cur_branch != '-' and (cur_branch != h_branch or cur_sha != h_sha):
            print(
                f'git drift: header {h_branch}@{h_sha}'
                f' -> now {cur_branch}@{cur_sha}'
                f' - run git log --oneline {h_sha}..HEAD')
    block_open_pat = re.compile(r'^<!-- hq:(\w+) ([0-9a-f]+) -->', re.MULTILINE)
    for m in block_open_pat.finditer(hf_text):
        bname, stored_sha = m.group(1), m.group(2)
        body = parsed.get('blocks', {}).get(bname, '')
        computed = markdown.block_sha(body)
        if computed != stored_sha:
            print(
                f'block sha mismatch: hq:{bname}'
                f' stored {stored_sha} computed {computed}'
                ' - a hand edit; trust hq artifacts and hq standing, not the block')
    # Re-resolved spans: report anchors that moved or became unresolved.
    for row in live.values():
        if row.get('status') != 'live' or row.get('read_before') != 'always':
            continue
        if row['where'] == '-':
            continue
        if row['base'] == 'abs':
            p = pathlib.Path(row['path']).expanduser()
        else:
            p = folder / row['path']
        if not p.is_file():
            continue
        file_text = p.read_text(encoding='utf-8', errors='replace')
        anchor_list = markdown.split_where(row['where'])
        new_spans, unresolved = markdown.resolve_where(file_text, anchor_list)
        for anchor_str in unresolved:
            print(
                f'unresolved anchor in {row["path"]}: {anchor_str!r}'
                ' - read the whole file; at write time re-stamp with a --where'
                ' that resolves (hq help anchors), then re-run the same open')
        # Report spans that moved since the stored read block, which
        # prints a path under the root or the pin relative to it.
        read_block_body = parsed.get('blocks', {}).get('read', '')
        display = shown.get(row['path'], (row['path'], ''))[0]
        # As at the label check, a read block an older version rendered
        # carries the stored path where this one prints the display.
        spellings = tuple(dict.fromkeys((display, row['path'])))
        for line in read_block_body.splitlines():
            if not line.startswith(tuple(f'{sp}:' for sp in spellings)):
                continue
            m2 = re.match(r'(.+?):([0-9?,\-]+)', line)
            if not m2 or m2.group(1) not in spellings:
                continue
            old_by_slot: list[tuple[int, int] | None] = []
            for ss in m2.group(2).split(','):
                parts = ss.split('-')
                try:
                    old_by_slot.append((int(parts[0]), int(parts[1])))
                except (IndexError, ValueError):
                    old_by_slot.append(None)
            old_spans = [span for span in old_by_slot if span]
            resolved_new = iter(new_spans)
            new_by_slot = [
                None if anchor in unresolved else next(resolved_new)
                for anchor in anchor_list]
            # Spans pair with anchors by position, and a ? slot on either
            # side pairs with nothing.
            pairs = [
                (old, new) for old, new in zip(old_by_slot, new_by_slot)
                if old and new]
            if (len(old_by_slot) == len(new_by_slot)
                    and any(old != new for old, new in pairs)):
                print(
                    f'span moved: {row["path"]} {old_spans} -> {new_spans}'
                    ' - the printed spans are current')
    # Notes:
    # - A `<dir>/<slug>/` mention under a directory an earlier plugin
    #   version used names where the folder was: the agent wrote the
    #   path before the rename, and no sha or row check reads prose.
    # - The scan is bound to those directory names: a source tree
    #   named after the slug, `src/<slug>/`, is a real place, and a
    #   stub left at the old location keeps the old directory on disk.
    # - Only the cursor and standing.md are scanned: the generated
    #   blocks are rewritten from standing.md at finish, and a notes
    #   sibling may quote where work used to live.
    # - A superseded or retired item and a retire line's reason are
    #   skipped: the file is append-only, so counting them would leave
    #   a line no move clears.
    # A work dir pinned under or inside a former name is a real place:
    # its per-thread paths are not stale, so that name leaves the scan.
    wd_value, _ = paths.resolve_work_dir(folder)
    pinned_parts = pathlib.PurePosixPath(wd_value).parts
    former = [d for d in storage.FORMER_HANDOFF_DIRNAMES if d not in pinned_parts]
    stale_pat = re.compile(
        r'(' + '|'.join(re.escape(d) for d in former) + r')/'
        + re.escape(folder.name) + r'/' if former else r'(?!)')
    standing_text = sb.decode('utf-8', 'replace')
    _, superseded_ids = standing.parse_standing(standing_text)
    standing_live_lines: list[str] = []
    for line in standing_text.splitlines():
        id_m = re.match(r'\s*- \[([dcxe]\d+)\]', line)
        if id_m and id_m.group(1) in superseded_ids:
            continue
        if re.match(r'\s*- \(c\d+\)\s+[dcxe]\d+\s*->\s*-(?!\S)', line):
            continue
        standing_live_lines.append(line)
    for name, scanned in (
        ('HANDOFF.md', parsed.get('cursor', '')),
        ('standing.md', '\n'.join(standing_live_lines)),
    ):
        counts: dict[str, int] = {}
        for m in stale_pat.finditer(scanned):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        for dirname in sorted(counts):
            print(
                f'stale folder path in {name}:'
                f' {dirname}/{folder.name}/ x{counts[dirname]}'
                ' - correct it at the next write, inside a cycle, never in'
                ' HANDOFF.md outside one; hq help stale-path has the steps')
    # launch removes the directory when the loop ends, so an HQ_LOOP
    # that outlives its loop reads as attended.
    loop_dir = os.environ.get('HQ_LOOP')
    loop_live = bool(loop_dir) and pathlib.Path(loop_dir).is_dir()
    if loop_live:
        print('hq loop: no one is watching this session')
        print(progress.LOOP_RULE)
    stall_result = progress.stall_count(manifest)
    probe = folder / 'probes' / 'hq-measure'
    has_probe = probe.is_file() and os.access(probe, os.X_OK)
    if has_probe and stall_result['reported'] >= 2:
        print(
            f'stall: {stall_result["reported"]} cycles without progress'
            f' ({stall_result["cause"] or "no measured gain"})'
            ' - close a unit on another workable item')
    own_loop = threads.own_loop_dir(folder.name)
    if own_loop is not None:
        with contextlib.suppress(OSError):
            hold_line = (own_loop / 'hold').read_text(encoding='utf-8').strip()
            print(f'{hold_line} {progress.HOLD_TEXT}')
    print(help_text.HELP_TOPICS['read'])
    return 0
