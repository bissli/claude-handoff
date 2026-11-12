"""Adopt verb: seeds ledger and standing from a foreign HANDOFF.md.
"""

import argparse
import pathlib
import re

from handoff import carry, ledger, markdown, paths, render, standing, storage

# A sentence end tells an item wrapped at the column, which breaks
# mid-sentence, from a section of plain one-line items.
_SENTENCE_END = re.compile(r'[.!?]["\')\]]*$')


def witnessed_pairs(
    records: list[tuple[str, str, str]],
    written_labels: dict[str, str],
) -> list[str]:
    """Bullet text of each pointer whose label reached the ledger.

    Parameters
    ----------
    records : list[tuple[str, str, str]]
        Each tuple is ``(stored_path, raw_bullet_text, label_part)``.
        ``raw_bullet_text`` is the bullet after its ``- `` marker, with
        continuation lines joined by single spaces. A ``label_part`` of
        ``'-'`` marks a pointer with no free text and always passes.
    written_labels : dict[str, str]
        Label written in the ledger row, by stored path. An absent path
        counts as an empty label.

    Returns
    -------
    list[str]
        ``raw_bullet_text`` of each record whose ``label_part`` appears
        in its path's written label, whitespace collapsed.
    """
    def _ws(s: str) -> str:
        return ' '.join(s.split())

    result = []
    for stored, raw, part in records:
        if part == '-':
            result.append(raw)
        elif _ws(part) in _ws(written_labels.get(stored, '')):
            result.append(raw)
    return result


def _collect_sections(
    text: str,
    header_text: str,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Split a foreign HANDOFF.md into its sections and the text above them.

    Parameters
    ----------
    text : str
        The whole file.
    header_text : str
        The ``Written:`` header line as ``markdown.split_handoff`` found
        it, or ``''``.

    Returns
    -------
    tuple[dict[str, list[str]], list[str], list[str]]
        ``(sections_raw, preamble, header_tail)``: the body lines of each
        ``## `` section by heading, with a repeated heading's bodies
        joined; each paragraph or item above the first section, wrapped
        lines joined; and the lines a wrapped header ran on to.
    """
    sections_raw: dict[str, list[str]] = {}
    preamble: list[str] = []
    header_tail: list[str] = []
    cur_h2 = ''
    cur_body: list[str] = []

    def _flush_section() -> None:
        """Append the open section's body to sections_raw.
        """
        if cur_h2:
            sections_raw.setdefault(cur_h2, []).extend(cur_body)

    in_header = False
    preamble_open = False
    item_start = re.compile(r'^\s*(?:[-*+]|\d+[.)]|#+)\s+')
    for line in text.splitlines():
        if line.startswith('## ') and not (
                cur_h2 == 'Key files' and markdown.KF_LABEL_PAT.match(line)):
            _flush_section()
            cur_h2 = line[3:].strip()
            cur_body = []
        elif cur_h2 and not line.startswith('<!-- hq:'):
            cur_body.append(line)
        elif not cur_h2:
            # A `Written:` line wrapped at the column runs on to the
            # next blank line or heading.
            if line.strip() == header_text.strip():
                in_header = True
            elif not line.strip() or line.startswith('#'):
                in_header = False
            elif in_header:
                header_tail.append(line.strip())
            # A paragraph wrapped at the column is one item, as under
            # Key files.
            if line.strip() and not line.startswith('# ') and not in_header:
                if preamble_open and not item_start.match(line):
                    preamble[-1] += ' ' + line.strip()
                else:
                    preamble.append(line.strip())
                preamble_open = True
            elif not line.strip():
                preamble_open = False
    _flush_section()
    return sections_raw, preamble, header_tail


def _parse_key_files(
    folder: pathlib.Path,
    kf_lines: list[str],
) -> tuple[
    dict[str, tuple[str, str | None, str]],
    list[tuple[str, str, str]],
    list[str],
    list[tuple[str, str]],
]:
    """Read the pointers, grades and loose text of a ``## Key files`` body.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; a pointer resolves against it.
    kf_lines : list[str]
        The section's body lines.

    Returns
    -------
    tuple
        ``(kf_map, seeded_records, kf_loose, where_dropped)``:

        - ``kf_map``: ``(label, read_before or None, where)`` by stored
          path, merged across bullets that name one path.
        - ``seeded_records``: ``(stored_path, raw_bullet_text,
          label_part)`` per pointer bullet, for ``witnessed_pairs``.
        - ``kf_loose``: each item that names no file, wrapped lines
          joined, to file under ``## Unfiled``.
        - ``where_dropped``: ``(stored_path, anchor)`` per section
          reference the pointed file does not resolve.
    """
    kf_map: dict[str, tuple[str, str | None, str]] = {}
    seeded_records: list[tuple[str, str, str]] = []
    kf_path_token = ''
    kf_free_text = ''
    kf_raw_text = ''
    # A separator is never optional, so ` - the two stacks` is text, and
    # `and` needs its trailing space, so `android.yaml` is a path.
    kf_more_path = re.compile(
        r'^\s*(?:,\s*(?:and\s+)?|and\s+)'
        r'([^\s,:]+:\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*|`[^`]+`|[^\s,]+)(.*)$',
        re.DOTALL)
    where_dropped: list[tuple[str, str]] = []

    def _flush_kf_pointer(path_tok: str, free: str, group: str, raw: str) -> None:
        """Add one Key files pointer to kf_map.

        Parameters
        ----------
        path_tok : str
            Path token, backticked or bare. A bare token may end in a
            glued comma.
        free : str
            Text after the path, continuation lines joined.
        group : str
            Grade from the nearest label line above the pointer.
        raw : str
            The bullet as written, marker cut and continuation lines
            joined. The conservation witness compares it to the original.
        """
        if not path_tok:
            return
        # The glued comma returns to the list walk below.
        if path_tok.endswith(','):
            free = ',' + free
        # Several paths on one bullet share its text.
        path_toks = [path_tok.rstrip(',')]
        more = kf_more_path.match(free)
        while more and _is_pointer(more.group(1).rstrip(',')):
            path_toks.append(more.group(1).rstrip(','))
            free = more.group(2)
            more = kf_more_path.match(free)
        # A comma that ends the list and a ` - ` after the path are
        # separators.
        free = re.sub(r'^-\s+', '', re.sub(r'^\s*,\s*', '', free.strip()))
        # An inline label grades the row like a label line and stays
        # out of the label text.
        inline_m = re.match(r'^\s*(read now|reference only)\s*:?\s*', free,
                            re.IGNORECASE)
        if inline_m:
            free = free[inline_m.end():]
        grade = (inline_m.group(1) if inline_m else group).lower()
        rb_over: str | None = None
        # An ungraded group's grade is its header's whole text, so only
        # an exact match grades, and 'read now' inside prose does not.
        if grade == 'read now':
            rb_over = 'always'
        elif grade == 'reference only':
            rb_over = 'edit'
        sref_m = re.findall(
            r'\bs(\d+(?:\.\d+)*[a-z]?)\b|section\s+(\d+(?:\.\d+)*[a-z]?)',
            free, re.IGNORECASE)
        where_val = ';'.join(
            f's{a or b}' for a, b in sref_m if (a or b)
            ) or '-'
        first_stored = ''
        first_part = '-'
        first_dir: pathlib.Path | None = None
        for tok in path_toks:
            tok_free = free
            range_m = re.match(
                r'^`?([^`]+?):(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)`?$', tok)
            if range_m:
                tok = range_m.group(1)
                tok_free = f'lines {range_m.group(2)}; {tok_free}'.rstrip('; ')
            # A bare name after the first path lists a sibling of it, so
            # it is tried beside that path first.
            clean_tok = tok.strip('`')
            sibling = first_dir / clean_tok if first_dir is not None else None
            if (sibling is not None and '/' not in clean_tok
                    and not clean_tok.startswith(('~', '.'))
                    and sibling.exists()):
                stored, path_obj, base = paths.stored_path(folder, str(sibling))
            else:
                stored, path_obj, base = paths.pointer_path(folder, tok)
            if first_dir is None:
                first_dir = path_obj.parent
            tok_where = '-'
            if where_val != '-':
                candidates = list(dict.fromkeys(where_val.split(';')))
                kept: list[str] = []
                dropped = list(candidates)
                if path_obj.is_file():
                    try:
                        pointed_text = path_obj.read_text(
                            encoding='utf-8', errors='replace')
                    except OSError:
                        pointed_text = ''
                    dropped = []
                    for anchor in candidates:
                        parent_m = re.fullmatch(r's(\d+)\.[\d.]+', anchor)
                        choices = [anchor] + (
                            [f's{parent_m.group(1)}'] if parent_m else [])
                        found = [
                            c for c in choices
                            if not markdown.resolve_where(pointed_text, [c])[1]]
                        if not found:
                            dropped.append(anchor)
                        elif found[0] not in kept:
                            kept.append(found[0])
                tok_where = ';'.join(kept) or '-'
                for anchor in dropped:
                    if (stored, anchor) not in where_dropped:
                        where_dropped.append((stored, anchor))
            label = tok_free.strip() or '-'
            if stored in kf_map:
                prev_label, prev_rb, prev_where = kf_map[stored]
                parts = [p for p in (prev_label, label) if p != '-']
                if len(parts) == 2:
                    sep = ' ' if prev_label.endswith(('.', '!', '?')) else '; '
                    label = sep.join(parts)
                elif parts:
                    label = parts[0]
                if 'always' in {prev_rb, rb_over}:
                    rb_merged: str | None = 'always'
                elif 'edit' in {prev_rb, rb_over}:
                    rb_merged = 'edit'
                else:
                    rb_merged = None
                anchors_seen = [p for p in prev_where.split(';') if p and p != '-']
                for anchor in tok_where.split(';'):
                    if anchor and anchor != '-' and anchor not in anchors_seen:
                        anchors_seen.append(anchor)
                kf_map[stored] = (label, rb_merged, ';'.join(anchors_seen) or '-')
            else:
                kf_map[stored] = (label, rb_over, tok_where)
            if not first_stored:
                first_stored = stored
                first_part = tok_free.strip() or '-'
        seeded_records.append((first_stored, raw, first_part))

    kf_group = ''
    kf_loose: list[str] = []
    kf_loose_current = ''
    kf_path_like = re.compile(
        r'^(?:`[^`]+`|[~./]\S*|\S*/\S+'
        r'|\S+\.\w{1,5}(?::\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)?)$')
    kf_bare_label = re.compile(
        r'^\s*(?:[-*+]\s+|#+\s*)?(?:read now|reference only)\s*:?\s*$',
        re.IGNORECASE)

    def _is_pointer(token: str) -> bool:
        """True when a bullet's first token names a file.
        """
        if kf_path_like.match(token):
            return True
        clean = token.strip('`')
        return (folder / clean).exists() or (folder.parent.parent / clean).exists()

    def _close_loose() -> None:
        """Move the open loose item to kf_loose; a label sets the group.
        """
        nonlocal kf_group, kf_loose_current
        if not kf_loose_current:
            return
        # A label wrapped at the column matches once its lines are
        # joined.
        joined_m = markdown.KF_LABEL_PAT.match(kf_loose_current)
        if joined_m:
            kf_group = joined_m.group(1).lower()
        elif kf_loose_current.endswith(':'):
            kf_group = kf_loose_current.lower()
        kf_loose.append(kf_loose_current)
        kf_loose_current = ''

    for line in kf_lines:
        # The label test runs first: `- Read now:` is a label bullet,
        # and read as a pointer it would name a file `Read` and cost the
        # bullets under it their grade.
        gm = markdown.KF_LABEL_PAT.match(line)
        bm = re.match(r'^\s*[-*+]\s+(`[^`]+`|\S+)\s*(.*)', line)
        if gm:
            _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
            kf_path_token = ''
            kf_free_text = ''
            kf_raw_text = ''
            _close_loose()
            kf_group = gm.group(1).lower()
            # A clause riding on a label is content to place, so it is
            # filed too.
            if not kf_bare_label.match(line):
                kf_loose.append(line.strip())
        elif bm and _is_pointer(bm.group(1).rstrip(',')):
            _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
            _close_loose()
            kf_path_token = bm.group(1)
            kf_free_text = bm.group(2)
            kf_raw_text = re.sub(r'^\s*[-*+]\s+', '', line).strip()
        elif line.startswith(' ') and line.strip() and kf_path_token:
            # A dash opening a wrapped line stays: stripping it would
            # weld two clauses into a sentence never written.
            kf_free_text += ' ' + line.strip()
            kf_raw_text += ' ' + line.strip()
        elif line.startswith(' ') and line.strip() and kf_loose_current:
            kf_loose_current += ' ' + line.strip()
        elif line.strip():
            _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
            kf_path_token = ''
            kf_free_text = ''
            kf_raw_text = ''
            if kf_loose_current and not bm:
                kf_loose_current += ' ' + line.strip()
            else:
                _close_loose()
                kf_loose_current = line.strip()
        else:
            _close_loose()
    _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
    _close_loose()
    return kf_map, seeded_records, kf_loose, where_dropped


def _seed_ledger(
    folder: pathlib.Path,
    walk: list[tuple[str, str]],
    kf_map: dict[str, tuple[str, str | None, str]],
    cycle_str: str,
    ts: str,
) -> tuple[list[str], dict[str, int], list[str]]:
    """Append a ledger row per walked file, then per Key files pointer the
    walk missed.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding ``ledger.tsv``.
    walk : list[tuple[str, str]]
        ``(name, kind)`` per entry of ``ledger.walk_folder``; a ``skip``
        kind prints a line and gets no row.
    kf_map : dict[str, tuple[str, str | None, str]]
        Pointers from ``_parse_key_files``.
    cycle_str : str
        The header's cycle, for every row.
    ts : str
        Timestamp for every row.

    Returns
    -------
    tuple[list[str], dict[str, int], list[str]]
        ``(seeded_labels, rb_count, gated_names)``: each label written,
        rows per read_before value, and the stored path of each live
        gated row.
    """
    stem_entries = [
        (n, k, (folder / n).stat().st_mtime if (folder / n).exists() else 0.0)
        for n, k in walk if k != 'skip'
        ]
    stem_successors = ledger.apply_stem_rule(stem_entries)
    ledger_path = folder / 'ledger.tsv'
    kf_matched: set[str] = set()
    seeded_labels: list[str] = []
    rb_count: dict[str, int] = {}
    gated_names: list[str] = []
    for name, kind in walk:
        if kind == 'skip':
            if 'conflicted copy' in name:
                print(
                    f'  skipped conflicted copy: {name}'
                    ' - a sync duplicate; resolve it by hand')
            else:
                shown = name.replace('\t', '\\t').replace('\n', '\\n')
                print(
                    f'  unstampable name: {shown.replace(chr(13), chr(92) + "r")}'
                    ' - rename the file before stamping it')
            continue
        path_obj = folder / name
        is_dir = path_obj.is_dir()
        sha12 = '-' if is_dir else storage.sha12_path(path_obj)
        n_lines = str(storage.line_count(path_obj))
        rb = 'always' if kind in {'spec', 'draft'} else 'never'
        status = 'live'
        successor = stem_successors.get(name, '-')
        if successor != '-':
            status = 'superseded'
            rb = 'never'
        kf_label, kf_rb, kf_where = kf_map.get(name, ('-', None, '-'))
        if name in kf_map:
            kf_matched.add(name)
        # always applies to every kind and edit only to a notes file, so
        # a grade never demotes a gated kind.
        if successor == '-' and (
                kf_rb == 'always' or (kf_rb == 'edit' and kind == 'notes')):
            rb = kf_rb
        row: storage.Row = {
            'cycle': cycle_str, 'ts': ts, 'path': name, 'base': 'folder',
            'kind': kind, 'status': status, 'read_before': rb,
            'successor': successor, 'where': kf_where, 'sha12': sha12,
            'lines': n_lines, 'reason': '-', 'label': kf_label,
            }
        successor_on_disk = successor != '-' and (folder / successor).is_file()
        refusal = ledger.check_r1(row, kind, successor_on_disk)
        if refusal:
            row['reason'] = f'refused: {refusal}'
            row['status'] = 'live'
            row['read_before'] = 'always' if kind in {'spec', 'draft'} else 'never'
            print(f'  advisory: R1 {refusal}')
        storage.append_tsv(ledger_path, ledger.LEDGER_FIELDS, row, ledger.LEDGER_HEADER)
        if kf_label != '-':
            seeded_labels.append(kf_label)
        rb_count[row['read_before']] = rb_count.get(row['read_before'], 0) + 1
        if row['status'] == 'live' and row['read_before'] in ledger.GATE_RB:
            gated_names.append(name)

    for stored in kf_map:
        if stored in kf_matched:
            continue
        kf_label, kf_rb, kf_where = kf_map[stored]
        stored_again, path_obj, base = paths.pointer_path(folder, stored)
        is_dir = path_obj.is_dir()
        first_heading = ''
        if path_obj.is_file():
            try:
                with path_obj.open(encoding='utf-8', errors='replace') as fh:
                    for ln in fh:
                        if ln.startswith('#'):
                            first_heading = ln.strip()
                            break
            except OSError:
                pass
        # Only a top-level .py is this folder's draft; a nested or
        # outside one would be gated under R1.
        at_top = base == 'folder' and '/' not in stored_again
        kind, _ = ledger.infer_kind(
            path_obj.name, is_dir, first_heading, top_level=at_top,
            kind_dir=ledger.kind_dir_of(stored_again, base))
        on_disk = path_obj.exists()
        rb = 'always' if kind in {'spec', 'draft'} else 'never'
        if kf_rb == 'always' or (kf_rb == 'edit' and kind == 'notes'):
            rb = kf_rb
        # A path not on disk is seeded missing and never, whatever its
        # kind: R1 governs transitions of an existing row, and the `not
        # on disk` line below is the signal.
        row = {
            'cycle': cycle_str, 'ts': ts, 'path': stored_again, 'base': base,
            'kind': kind, 'status': 'live' if on_disk else 'missing',
            'read_before': rb if on_disk else 'never',
            'successor': '-', 'where': kf_where,
            'sha12': storage.sha12_path(path_obj) if path_obj.is_file() else '-',
            'lines': (
                str(storage.line_count(path_obj)) if is_dir
                else str(storage.line_count(path_obj)) if on_disk else '-'),
            'reason': '-', 'label': kf_label,
            }
        storage.append_tsv(ledger_path, ledger.LEDGER_FIELDS, row, ledger.LEDGER_HEADER)
        if kf_label != '-':
            seeded_labels.append(kf_label)
        rb_count[row['read_before']] = rb_count.get(row['read_before'], 0) + 1
        if row['status'] == 'live' and row['read_before'] in ledger.GATE_RB:
            gated_names.append(stored_again)
        if not on_disk:
            print(
                f'  Key files pointer not on disk: {stored_again}'
                ' - stamp the path that exists and archive this row')
    return seeded_labels, rb_count, gated_names


def _seed_standing(
    folder: pathlib.Path,
    anch: dict,
    sections_raw: dict[str, list[str]],
) -> list[str]:
    """Note each bullet of the Decisions, Constraints and Dead ends sections.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding ``standing.md``.
    anch : dict
        Anchors with ``cycle`` set to the header's cycle.
    sections_raw : dict[str, list[str]]
        Section bodies by heading, from ``_collect_sections``.

    Returns
    -------
    list[str]
        Each sentence that opens a section ahead of its first bullet,
        wrapped lines joined, to file under ``## Unfiled``.
    """
    section_kind_map = {
        'Decisions': 'decision',
        'Constraints': 'constraint',
        'Dead ends': 'dead-end',
        }
    lead_ins: list[str] = []
    item_marker = re.compile(r'^(?:[-*]\s+|\d+[.)]\s+)')
    for section_name, kind_str in section_kind_map.items():
        current_kind = kind_str
        bullet_lines: list[str] = []
        section_lines = sections_raw.get(section_name, [])
        # A bulleted section may open with a sentence that introduces
        # the bullets. standing.md has no delete, so filing it would
        # cost a block line for good.
        first_bullet = next(
            (i for i, ln in enumerate(section_lines) if item_marker.match(ln)),
            None)

        def _flush_bullet(lines: list[str]) -> None:
            """File one bullet as a standing item.
            """
            if not lines:
                return
            raw = ' '.join(ln.strip() for ln in lines)
            content = re.sub(r'^(?:[-*]\s+|\d+[.)]\s+)', '', raw).strip()
            if not content:
                return
            bold_m = re.match(r'\*\*(.+?)\*\*\s*(.*)', content)
            if bold_m:
                headline, body = bold_m.group(1).strip(), bold_m.group(2).strip()
            else:
                headline, body = standing.split_headline(content)
            standing.do_note(folder, anch, current_kind, headline, body)

        lead_in_open = False
        for idx, line in enumerate(section_lines):
            if not line.strip():
                _flush_bullet(bullet_lines)
                bullet_lines = []
                lead_in_open = False
                continue
            if bullet_lines and not item_marker.match(line):
                wrapped = not (_SENTENCE_END.search(bullet_lines[-1].rstrip())
                               and line[:1].isupper())
                if line.startswith((' ', '\t')) or wrapped:
                    bullet_lines.append(line)
                    continue
            _flush_bullet(bullet_lines)
            bullet_lines = []
            if (first_bullet is not None and idx < first_bullet
                    and not item_marker.match(line)):
                if lead_in_open:
                    lead_ins[-1] += ' ' + line.strip()
                else:
                    lead_ins.append(line.strip())
                lead_in_open = True
                continue
            bullet_lines = [line]
        _flush_bullet(bullet_lines)
    return lead_ins


def _adopted_cursor(
    sections_raw: dict[str, list[str]],
    header_tail: list[str],
    loose: list[str],
) -> tuple[str, list[str]]:
    """The standard cursor sections, then ``## Unfiled`` for the rest.

    Parameters
    ----------
    sections_raw : dict[str, list[str]]
        Section bodies by heading, from ``_collect_sections``.
    header_tail : list[str]
        Lines a wrapped header ran on to; they join ``## Environment``.
    loose : list[str]
        Lines no section placed, each filed as one ``- unfiled:`` bullet.

    Returns
    -------
    tuple[str, list[str]]
        ``(cursor_text, unfiled_parts)``. A section absent from the file
        is written as a bare heading.
    """
    _cursor_headings = {
        'Directives', 'Task', 'Now', 'Plan', 'State', 'Environment',
        'Open questions',
        }
    cursor_parts: list[str] = []
    for h in [
            'Directives', 'Task', 'Now', 'Plan', 'State', 'Environment',
            'Open questions']:
        body = '\n'.join(sections_raw.get(h, [])).strip()
        # finish rewrites the header, so the cursor is the tail's live
        # home and the manifest note its archive.
        if h == 'Environment' and header_tail:
            tail_body = '\n'.join(
                ln if re.match(r'^[-*+]\s', ln) else f'- {ln}' for ln in header_tail)
            body = f'{body}\n{tail_body}' if body else tail_body
        cursor_parts.append(f'## {h}\n{body}' if body else f'## {h}')

    unfiled_parts: list[str] = [
        f'- unfiled: {ln}' for ln in loose]
    for h, lines in sections_raw.items():
        if h in _cursor_headings:
            continue
        if h == 'Unfiled':
            unfiled_parts.extend(bl.rstrip() for bl in lines if bl.strip())
            continue
        if h in {'Decisions', 'Constraints', 'Dead ends', 'Key files',
                 'Log', 'Read first', 'Artifacts', 'Standing'}:
            continue
        unfiled_parts.extend(f'- unfiled: {bl.strip()}' for bl in lines if bl.strip())
    if unfiled_parts:
        cursor_parts.append('## Unfiled\n' + '\n'.join(unfiled_parts))
    cursor_text = '\n\n'.join(cursor_parts)
    return cursor_text, unfiled_parts


def _write_adoption(
    folder: pathlib.Path,
    anch: dict,
    parsed: dict,
    sections_raw: dict[str, list[str]],
    header_tail: list[str],
    cursor_text: str,
    walk: list[tuple[str, str]],
) -> tuple[dict[str, storage.Row], list[str]]:
    """Rewrite HANDOFF.md around the adopted cursor and append the
    adoption's manifest row.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder. Its ledger, standing.md and archive are read.
    anch : dict
        Anchors with ``cycle`` set to the header's cycle.
    parsed : dict
        ``markdown.split_handoff`` of the foreign file.
    sections_raw : dict[str, list[str]]
        Section bodies by heading; the ``Log`` body goes to the manifest
        note.
    header_tail : list[str]
        Lines a wrapped header ran on to, kept in the manifest note.
    cursor_text : str
        From ``_adopted_cursor``.
    walk : list[tuple[str, str]]
        ``(name, kind)`` per entry of ``ledger.walk_folder``.

    Returns
    -------
    tuple[dict[str, storage.Row], list[str]]
        ``(fresh_live, legacy_log)``: the latest ledger row by stored
        path, and the nonblank lines of the old ``## Log``.
    """
    cycle = anch['cycle']
    cycle_str = str(cycle)
    ts = anch['now']
    handoff_path = folder / 'HANDOFF.md'
    ledger_path = folder / 'ledger.tsv'
    standing_path = folder / 'standing.md'
    manifest_path = folder / 'cycles' / 'manifest.tsv'
    archive = folder / 'cycles' / f'c{cycle:02d}.md'
    fresh_rows = storage.read_tsv(ledger_path, ledger.LEDGER_FIELDS)
    fresh_live = ledger.latest_rows(fresh_rows)
    fresh_standing_text = (
        standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
        )
    fresh_manifest = storage.read_tsv(manifest_path, storage.MANIFEST_FIELDS)
    repos = f'{anch["branch"]}@{anch["sha"]}' if anch['branch'] != '-' else '-'
    # Notes:
    # - A c<N>.md already on disk is kept, so the row's date, repos,
    #   sizes and handoff_sha are read from that file, not from `text`.
    # - The header carries no session, so that column stays `-` and the
    #   adopting session rides in the note with the adopt date and sha.
    archive_text = archive.read_text(encoding='utf-8-sig', errors='replace')
    archive_parsed = markdown.split_handoff(archive_text)
    written_m = re.search(r'Written:\s*(\d{4}-\d{2}-\d{2})', archive_parsed['header'])
    archive_written = written_m.group(1) if written_m else ts[:10]
    repos_m = re.search(r'\|\s*(\S+)\s*@\s*([0-9a-f]+)', archive_parsed['header'])
    archive_repos = f'{repos_m.group(1)}@{repos_m.group(2)}' if repos_m else '-'
    adopt_provenance = f'adopted {ts[:10]} by {anch["session"]}'
    if repos != '-':
        adopt_provenance += f' at {repos}'
    # The Log block renders the log field, so it holds a one-line
    # roll-up.
    legacy_log = [ln.strip() for ln in sections_raw.get('Log', []) if ln.strip()]
    # ` / ` is no item separator, since a sha list carries it at a line
    # end.
    legacy_items: list[str] = []
    for ln in sections_raw.get('Log', []):
        if not ln.strip():
            continue
        if ln.startswith(' ') and legacy_items:
            legacy_items[-1] += ' ' + ln.strip()
        else:
            legacy_items.append(ln.strip())
    adopt_log = 'adopted'
    if legacy_log:
        adopt_log = (
            f'adopted; prior Log: {len(legacy_log)} lines'
            f' in cycles/c{cycle:02d}.md'
        )
    note_items = [adopt_provenance]
    if header_tail:
        note_items.append(f'header: {" ".join(header_tail)}')
    adopt_note = ' | '.join(note_items + legacy_items)
    # The Log includes the adopt row, as at finish, so the file has no
    # empty ## Log.
    log_body_f = render.render_log(fresh_manifest + [{
        'cycle': cycle_str,
        'written': archive_written,
        'repos': archive_repos,
        'log': adopt_log,
        }], folder.name)
    branch_a = anch['branch']
    sha7_a = anch['sha']
    header_line_a = f'Written: {ts[:10]} | Cycle: {cycle}'
    worked_m = markdown.WORKED_PAT.search(parsed['header'])
    if worked_m:
        header_line_a += f' | Last worked: {worked_m.group(1)}'
    if branch_a != '-':
        header_line_a += f' | {branch_a} @ {sha7_a}{storage.dirty_str(anch["dirty"])}'
    new_handoff = render.assemble_handoff(
        folder, cursor_text, header_line_a, log_body_f,
        ledger.reconcile_missing(folder, fresh_live), walk, fresh_standing_text,
        cycle)
    handoff_path.write_text(new_handoff, encoding='utf-8')
    lb_final = ledger_path.read_bytes()
    sb_final = standing_path.read_bytes() if standing_path.exists() else b''
    adopt_manifest_row: dict = {
        'cycle': cycle_str,
        'written': archive_written,
        'session': '-',
        'repos': archive_repos,
        'cursor_lines': str(len(archive_parsed['cursor'].splitlines())),
        'payload_tokens': str(len(archive_text) // 4),
        'handoff_sha': storage.sha12_path(archive),
        'ledger_bytes': str(len(lb_final)),
        'ledger_sha': storage.sha12(lb_final),
        'standing_bytes': str(len(sb_final)),
        'standing_sha': storage.sha12(sb_final),
        'log': adopt_log,
        'note': adopt_note,
        'rewrite_sha': storage.sha12(new_handoff.encode()),
        }
    storage.append_tsv(
        manifest_path, storage.MANIFEST_FIELDS, adopt_manifest_row,
        storage.MANIFEST_HEADER)
    return fresh_live, legacy_log


def verb_adopt(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Adopt a conforming HANDOFF.md that has no ledger.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding the HANDOFF.md.
    anch : dict
        Current ``now``, ``session``, ``branch``, ``sha`` and ``dirty``.
        ``cycle`` is replaced by the header's cycle.
    argv : argparse.Namespace
        Parsed adopt arguments.

    Returns
    -------
    int
        0 on success. 1, with nothing written, when HANDOFF.md is absent,
        a directory or non-conforming, ledger.tsv exists, or the thread
        is marked done.
    """
    refusal = storage.done_refusal(folder, 'adopt')
    if refusal:
        print(refusal)
        return 1
    handoff_path = folder / 'HANDOFF.md'
    if handoff_path.is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    if not handoff_path.exists():
        print(f'hq adopt: no HANDOFF.md in {folder}')
        return 1
    text = handoff_path.read_text(encoding='utf-8-sig', errors='replace')
    parsed = markdown.split_handoff(text)
    if parsed['cycle'] is None:
        ref = (
            pathlib.Path(__file__).resolve().parents[3]
            / 'skills' / 'handoff' / 'reference' / 'adoption.md')
        print(
            'hq adopt: non-conforming header; write a conforming HANDOFF.md first'
            f' - the conversion is in {ref.parent.parent}/'
            'reference/adoption.md')
        for line in text.splitlines():
            if line.startswith('#'):
                print(f'  {line}')
        return 1
    cycle = parsed['cycle']
    if (folder / 'ledger.tsv').exists():
        print('hq adopt: already adopted; ledger.tsv exists')
        return 1
    cycles_dir = folder / 'cycles'
    cycles_dir.mkdir(exist_ok=True)
    # --- Archive ---
    archive = cycles_dir / f'c{cycle:02d}.md'
    if not archive.exists():
        archive.write_text(text, encoding='utf-8')
    prev_path = folder / 'HANDOFF.prev.md'
    if prev_path.exists() and cycle > 1:
        prev_arch = cycles_dir / f'c{cycle - 1:02d}.md'
        if not prev_arch.exists():
            prev_arch.write_text(
                prev_path.read_text(encoding='utf-8'), encoding='utf-8')
    walk = ledger.walk_folder(folder)
    ledger_path = folder / 'ledger.tsv'
    standing_path = folder / 'standing.md'
    if not ledger_path.exists():
        ledger_path.write_text(ledger.LEDGER_HEADER + '\n', encoding='utf-8')
    if not standing_path.exists():
        standing_path.write_text('', encoding='utf-8')
    manifest_path = cycles_dir / 'manifest.tsv'
    if not manifest_path.exists():
        manifest_path.write_text(storage.MANIFEST_HEADER + '\n', encoding='utf-8')
    # Standing items take their cycle from anch, so it holds the
    # header's cycle.
    anch = dict(anch)
    anch['cycle'] = cycle
    ts = anch['now']
    cycle_str = str(cycle)

    sections_raw, preamble, header_tail = _collect_sections(
        text, parsed.get('header') or '')
    kf_map, seeded_records, kf_loose, where_dropped = _parse_key_files(
        folder, sections_raw.get('Key files', []))
    seeded_labels, rb_count, gated_names = _seed_ledger(
        folder, walk, kf_map, cycle_str, ts)
    lead_ins = _seed_standing(folder, anch, sections_raw)
    cursor_text, unfiled_parts = _adopted_cursor(
        sections_raw, header_tail, preamble + kf_loose + lead_ins)
    fresh_live, legacy_log = _write_adoption(
        folder, anch, parsed, sections_raw, header_tail, cursor_text, walk)

    # --- Summary ---
    gated_cnt = len(gated_names)
    seeded_cnt = len(storage.read_tsv(ledger_path, ledger.LEDGER_FIELDS))
    print(f'hq adopt: seeded {seeded_cnt} entries in {folder.name}')
    for lb in seeded_labels:
        print(f'  label: {lb}')
    for rb_val, cnt in sorted(rb_count.items()):
        print(f'  read_before={rb_val}: {cnt}')
    if gated_cnt:
        print(f'  gated ({gated_cnt}): {", ".join(gated_names[:5])}')
    for path_dropped, anchor_dropped in where_dropped:
        print(f'  where dropped: {path_dropped} {anchor_dropped!r}')
    # Conservation counts a line under ## Unfiled as carried, yet each
    # such bullet blocks finish until retyped, so this count is the
    # agent's one notice.
    if unfiled_parts:
        print(f'  unfiled: {len(unfiled_parts)} bullets to rehome')
    # The adopted file reproduces its own title line, which is outside
    # the cursor, so the union carries it.
    union_cursor = f'# Handoff: {folder.name}\n{cursor_text}'
    union_standing = standing_path.read_text(encoding='utf-8', errors='replace')
    witnessed = witnessed_pairs(
        seeded_records, {path: row['label'] for path, row in fresh_live.items()})
    not_carried = carry.conservation(
        text, union_cursor, union_standing,
        seeded_labels + witnessed + legacy_log + header_tail)
    if not_carried:
        print(f'conservation: {len(not_carried)} original lines not carried')
        for line in not_carried:
            print(f'  not carried: {line.strip()}')
    else:
        print('conservation: every original line carried')
    orig_path = folder / 'HANDOFF.orig.md'
    if orig_path.is_file():
        orig_missing = carry.conservation(
            orig_path.read_text(encoding='utf-8-sig', errors='replace'),
            union_cursor, union_standing,
            seeded_labels + witnessed + legacy_log + header_tail
            + carry.sibling_texts(folder, fresh_live))
        if orig_missing:
            print(f'conservation vs HANDOFF.orig.md: {len(orig_missing)} lines not carried')
            for line in orig_missing:
                print(f'  not carried: {line.strip()}')
        else:
            print('conservation vs HANDOFF.orig.md: every line carried')
    return 0
