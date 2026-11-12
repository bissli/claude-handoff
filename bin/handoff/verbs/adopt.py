"""The adopt verb: seeds the ledger and standing.md from an existing HANDOFF.md.
"""

import argparse
import pathlib
import re

from handoff import carry, ledger, markdown, paths, render, standing, storage

# A sentence ends at `.`, `!`, or `?` and any closers that follow it,
# which is what tells an item wrapped at the column from a section of
# plain one-line items: the wrap breaks mid-sentence.
_SENTENCE_END = re.compile(r'[.!?]["\')\]]*$')


def witnessed_pairs(
    records: list[tuple[str, str, str]],
    written_labels: dict[str, str],
) -> list[str]:
    """Filter pointer records to those whose label was written to the ledger.

    Parameters
    ----------
    records : list[tuple[str, str, str]]
        Each tuple is ``(stored_path, raw_bullet_text, label_part)``.
        ``raw_bullet_text`` is the bullet's own text after its ``- ``
        marker with any indented continuation lines joined by single
        spaces. ``label_part`` is the label text this pointer contributes.
    written_labels : dict[str, str]
        Map from stored path to the label written in the ledger row.

    Returns
    -------
    list[str]
        ``raw_bullet_text`` for each record whose ``label_part`` appears
        in the written label for its stored path.

    Notes
    -----
    - A ``label_part`` of ``'-'`` always passes; it represents a pointer
      whose label was already ``'-'`` (no free text).
    - Comparison collapses whitespace on both sides, so minor spacing
      differences do not cause a spurious miss.
    - A stored path absent from ``written_labels`` is treated as having
      an empty label, so only a ``'-'`` part passes for it.
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


def verb_adopt(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run adopt: seed ledger and standing from an existing HANDOFF.md.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding HANDOFF.md to adopt.
    anch : dict
        Anchors dict from ``anchors()``; provides cycle, session, timestamp.
    argv : argparse.Namespace
        Parsed adopt arguments.

    Returns
    -------
    int
        0 on success; 1 when HANDOFF.md is absent, non-conforming,
        ledger.tsv already exists, or the thread is marked done.

    Notes
    -----
    - Writes ledger.tsv, standing.md, cycles/manifest.tsv, cycles/c<n>.md,
      and rewrites HANDOFF.md; a failed header check writes nothing.
    - A marked folder is refused here as well as at ``begin``. Adoption
      opens a ledger and a cycle on a folder that has none, which is
      reopening the thread by another road.
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
    # --- Step 1: archive ---
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
    stem_entries = [
        (n, k, (folder / n).stat().st_mtime if (folder / n).exists() else 0.0)
        for n, k in walk if k != 'skip'
        ]
    stem_successors = ledger.apply_stem_rule(stem_entries)
    ledger_path = folder / 'ledger.tsv'
    standing_path = folder / 'standing.md'
    if not ledger_path.exists():
        ledger_path.write_text(ledger.LEDGER_HEADER + '\n', encoding='utf-8')
    if not standing_path.exists():
        standing_path.write_text('', encoding='utf-8')
    manifest_path = cycles_dir / 'manifest.tsv'
    if not manifest_path.exists():
        manifest_path.write_text(storage.MANIFEST_HEADER + '\n', encoding='utf-8')
    # Seed anch cycle from the header so standing items carry
    # the right cycle.
    anch = dict(anch)
    anch['cycle'] = cycle
    ts = anch['now']
    cycle_str = str(cycle)

    # --- Collect sections (single pass) ---
    sections_raw: dict[str, list[str]] = {}
    preamble: list[str] = []
    header_tail: list[str] = []
    cur_h2 = ''
    cur_body: list[str] = []

    def _flush_section() -> None:
        """Flush the current section body into sections_raw.
        """
        if cur_h2:
            sections_raw.setdefault(cur_h2, []).extend(cur_body)

    header_text = parsed.get('header') or ''
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
            # The header is a paragraph: a `Written:` line wrapped at the
            # column runs on to the next blank line or heading. The
            # script rewrites the header at finish, so the tail rides in
            # the manifest note rather than the cursor.
            if line.strip() == header_text.strip():
                in_header = True
            elif not line.strip() or line.startswith('#'):
                in_header = False
            elif in_header:
                header_tail.append(line.strip())
            # A paragraph wrapped at the column is one item, as under
            # Key files: a plain line continues the open run, a bullet
            # or a heading opens a new one, a blank line ends it.
            if line.strip() and not line.startswith('# ') and not in_header:
                if preamble_open and not item_start.match(line):
                    preamble[-1] += ' ' + line.strip()
                else:
                    preamble.append(line.strip())
                preamble_open = True
            elif not line.strip():
                preamble_open = False
    _flush_section()

    # --- Step 3: parse Key files ---
    kf_map: dict[str, tuple[str, str | None, str]] = {}
    seeded_records: list[tuple[str, str, str]] = []
    kf_path_token = ''
    kf_free_text = ''
    kf_raw_text = ''
    # Notes:
    # - A list runs on `, path`, `and path`, or `, and path`; a
    #   separator is never optional, so ` - the two stacks` is text,
    #   and `and` needs its trailing space, so `android.yaml` is a path.
    # - A bare token may carry a comma-separated range list; that form
    #   is tried first, so the comma inside `mod.py:12-40,57` does not
    #   end the token, and any other bare token still ends at a comma.
    kf_more_path = re.compile(
        r'^\s*(?:,\s*(?:and\s+)?|and\s+)'
        r'([^\s,:]+:\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*|`[^`]+`|[^\s,]+)(.*)$',
        re.DOTALL)
    where_dropped: list[tuple[str, str]] = []

    def _flush_kf_pointer(path_tok: str, free: str, group: str, raw: str) -> None:
        """Record the current Key files pointer into kf_map.

        Parameters
        ----------
        path_tok : str
            Raw path token, backticked or bare; a trailing comma is cut.
        free : str
            Free text following the path on the bullet, continuation
            lines joined.
        group : str
            Grade inherited from the nearest label line above the pointer.
        raw : str
            The bullet's text as written, marker cut and continuation
            lines joined; the conservation witness compares it against
            the original line.
        """
        if not path_tok:
            return
        # A bare first token swallows its glued comma, so the comma is
        # handed back to the list walk below.
        if path_tok.endswith(','):
            free = ',' + free
        # Several paths on one bullet share its text: `, path` or
        # `and path` repeats while the next token names a file.
        path_toks = [path_tok.rstrip(',')]
        more = kf_more_path.match(free)
        while more and _is_pointer(more.group(1).rstrip(',')):
            path_toks.append(more.group(1).rstrip(','))
            free = more.group(2)
            more = kf_more_path.match(free)
        # The comma before a word that ends the list, and a ` - `
        # between the path and its text, are separators, not text.
        free = re.sub(r'^-\s+', '', re.sub(r'^\s*,\s*', '', free.strip()))
        # An inline label grades the row like a label line above it;
        # it is structure and never reaches the label text.
        inline_m = re.match(r'^\s*(read now|reference only)\s*:?\s*', free,
                            re.IGNORECASE)
        if inline_m:
            free = free[inline_m.end():]
        grade = (inline_m.group(1) if inline_m else group).lower()
        rb_over: str | None = None
        # The tests are exact: an ungraded group carries its header's
        # whole text as the grade, and 'read now' inside that text is
        # prose, not the label.
        if grade == 'read now':
            rb_over = 'always'
        elif grade == 'reference only':
            # edit lands on a notes file; any other kind keeps its
            # seeded tier.
            rb_over = 'edit'
        # A section number may carry one letter, `section 11b`, or be
        # dotted, `section 24.4`; the whole token is the anchor.
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
            # A `path:12-40` or `path:12-40,57` pointer names lines; the
            # ledger has no range column, so the ranges lead the label
            # and the path stays bare.
            range_m = re.match(
                r'^`?([^`]+?):(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)`?$', tok)
            if range_m:
                tok = range_m.group(1)
                tok_free = f'lines {range_m.group(2)}; {tok_free}'.rstrip('; ')
            # A bare name after the first path lists a sibling of it, so
            # it is tried beside that path before the folder and the
            # search bases.
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
            # An anchor is seeded only where it resolves: a bare `s1` in
            # prose most often names a step of another file, and a seed
            # that cannot resolve prints `?` in the read block every
            # cycle.
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
                        # A dotted number the file does not carry falls
                        # back to its parent section, the span the
                        # sub-point sits in.
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
                # A second bullet naming the same path adds to its row
                # instead of replacing it.
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

    # Notes:
    # - The label test runs first: `- Read now:` is a label bullet, and
    #   reading it as a pointer would name a file `Read` and cost the
    #   bullets under it their grade.
    # - A label line, bare, as a heading, or as a bullet, grades every
    #   pointer below it until the next label, nesting included; a
    #   label whose clause wraps onto further lines grades once the
    #   lines are joined, at the point the loose item closes. Any other
    #   line ending in a colon ends the group too and opens an ungraded
    #   one.
    # - `-`, `*`, and `+` open a pointer bullet when the first token is
    #   a path: backticked, `~`/`/`/`.` prefixed, holding `/`, or ending
    #   in an extension. A bullet whose first token is a plain
    #   word is text: the pointer's free text when indented under one,
    #   Unfiled otherwise.
    # - An indented line continues the open pointer or, when none is
    #   open, the loose bullet above it, as written, so a wrapped bullet
    #   stays one item either way. An unindented plain line runs the
    #   open loose item on too, so a paragraph is one item, and a blank
    #   line ends it; a pointer is never continued unindented.
    # - A ` - ` separator between the path and its text is structure.
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
        """Return True when a bullet's first token names a file.
        """
        if kf_path_like.match(token):
            return True
        clean = token.strip('`')
        return (folder / clean).exists() or (folder.parent.parent / clean).exists()

    def _close_loose() -> None:
        """File the open loose item under Unfiled, grading when it is a label.
        """
        nonlocal kf_group, kf_loose_current
        if not kf_loose_current:
            return
        # A label wrapped at the column matches only once its lines are
        # joined; it grades the bullets below like one written on a
        # single line, and its clause is filed as such a clause is.
        joined_m = markdown.KF_LABEL_PAT.match(kf_loose_current)
        if joined_m:
            kf_group = joined_m.group(1).lower()
        elif kf_loose_current.endswith(':'):
            # A header the parser does not know, `Read before touching
            # the gateway:`, still ends the group above it: the bullets
            # under it take the header's own text as their group, which
            # carries no grade, never the grade of a label the author
            # closed.
            kf_group = kf_loose_current.lower()
        kf_loose.append(kf_loose_current)
        kf_loose_current = ''

    for line in sections_raw.get('Key files', []):
        gm = markdown.KF_LABEL_PAT.match(line)
        bm = re.match(r'^\s*[-*+]\s+(`[^`]+`|\S+)\s*(.*)', line)
        if gm:
            _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
            kf_path_token = ''
            kf_free_text = ''
            kf_raw_text = ''
            _close_loose()
            kf_group = gm.group(1).lower()
            # The label grades the bullets below; a clause riding on it
            # is content the agent must place, so it is filed as well.
            if not kf_bare_label.match(line):
                kf_loose.append(line.strip())
        elif bm and _is_pointer(bm.group(1).rstrip(',')):
            _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
            _close_loose()
            kf_path_token = bm.group(1)
            kf_free_text = bm.group(2)
            kf_raw_text = re.sub(r'^\s*[-*+]\s+', '', line).strip()
        elif line.startswith(' ') and line.strip() and kf_path_token:
            # A dash opening a wrapped line is kept: as punctuation it
            # is the author's wording, and as a nested marker it costs
            # one `- ` in the label, where stripping it would weld two
            # clauses into a sentence never written.
            kf_free_text += ' ' + line.strip()
            kf_raw_text += ' ' + line.strip()
        elif line.startswith(' ') and line.strip() and kf_loose_current:
            kf_loose_current += ' ' + line.strip()
        elif line.strip():
            _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
            kf_path_token = ''
            kf_free_text = ''
            kf_raw_text = ''
            # A plain line runs the open loose item on, as a wrapped
            # paragraph does; a bullet opens an item of its own.
            if kf_loose_current and not bm:
                kf_loose_current += ' ' + line.strip()
            else:
                _close_loose()
                kf_loose_current = line.strip()
        else:
            _close_loose()
    _flush_kf_pointer(kf_path_token, kf_free_text, kf_group, kf_raw_text)
    _close_loose()

    # --- Step 2: walk and seed ledger ---
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

    # Notes:
    # - A pointer outside the top-level walk - an abs path or a file in
    #   a subdirectory - keeps its label in a row of its own.
    # - A path that is not on disk is seeded missing/never whatever its
    #   kind: R1 governs transitions of an existing row, and the `not on
    #   disk` line below is the signal. A row on disk is live and a
    #   gated kind reads always, so R1 has nothing to refuse here.
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
        # The draft rule is top-level: a nested or outside .py is not
        # this folder's draft; inferring one would gate it under R1.
        at_top = base == 'folder' and '/' not in stored_again
        kind, _ = ledger.infer_kind(
            path_obj.name, is_dir, first_heading, top_level=at_top,
            kind_dir=ledger.kind_dir_of(stored_again, base))
        on_disk = path_obj.exists()
        rb = 'always' if kind in {'spec', 'draft'} else 'never'
        # The grade applies exactly as the walk branch applies it.
        if kf_rb == 'always' or (kf_rb == 'edit' and kind == 'notes'):
            rb = kf_rb
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

    # --- Step 4: seed standing ---
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
        # the bullets rather than being one; standing.md has no delete,
        # so filing it would cost a block line for good.
        first_bullet = next(
            (i for i, ln in enumerate(section_lines) if item_marker.match(ln)),
            None)

        def _flush_bullet(lines: list[str]) -> None:
            """Format and append one standing bullet to standing.md.
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

        # Notes:
        # - An indented line always continues the item above it.
        # - An unindented line carrying no marker continues it too,
        #   unless the line above ended a sentence and this one opens
        #   another. A hand wrapping one item at the column breaks
        #   mid-sentence, where a section of plain one-line items does
        #   not, and the two are otherwise identical text.
        # - A blank line closes the open item, which is what keeps a
        #   paragraph below a bullet from joining it.
        # - A lead-in wrapped at the column joins as a paragraph does.
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

    # --- Step 5: rewrite HANDOFF.md ---
    _cursor_headings = {
        'Directives', 'Task', 'Now', 'Plan', 'State', 'Environment',
        'Open questions',
        }
    cursor_parts: list[str] = []
    for h in [
            'Directives', 'Task', 'Now', 'Plan', 'State', 'Environment',
            'Open questions']:
        body = '\n'.join(sections_raw.get(h, [])).strip()
        # The header tail is a repo state the thread wrote by hand;
        # finish rewrites the header, so the cursor is its live home and
        # the manifest note its archive.
        if h == 'Environment' and header_tail:
            tail_body = '\n'.join(
                ln if re.match(r'^[-*+]\s', ln) else f'- {ln}' for ln in header_tail)
            body = f'{body}\n{tail_body}' if body else tail_body
        cursor_parts.append(f'## {h}\n{body}' if body else f'## {h}')

    unfiled_parts: list[str] = [
        f'- unfiled: {ln}' for ln in preamble + kf_loose + lead_ins]
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

    fresh_rows = storage.read_tsv(ledger_path, ledger.LEDGER_FIELDS)
    fresh_live = ledger.latest_rows(fresh_rows)
    fresh_standing_text = (
        standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
        )
    fresh_manifest = storage.read_tsv(manifest_path, storage.MANIFEST_FIELDS)
    repos = f'{anch["branch"]}@{anch["sha"]}' if anch['branch'] != '-' else '-'
    # Notes:
    # - The row indexes cycles/c<N>.md, so its date, repos, sizes, and
    #   handoff_sha are read off that file, not the rewrite made today.
    # - The archive is read back rather than reusing `text`: a c<N>.md
    #   already on disk is kept, and the row must describe that file.
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
    # The Log block renders this row's log field, so it carries a
    # one-line roll-up; the legacy Log lines ride in the row's note
    # field, and the archived file keeps them as written.
    legacy_log = [ln.strip() for ln in sections_raw.get('Log', []) if ln.strip()]
    # A Log item starts at an unindented line and runs on through the
    # indented lines below it, so a bullet wrapped at the column joins
    # back onto one line before the items are separated; ` / ` is not
    # the separator because a sha list carries it at a line end.
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
    # The row adopt appends belongs in the Log it renders, the same way
    # finish renders its own row; the file would otherwise carry an
    # empty ## Log the cycle it was adopted.
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
    # The ledger and standing digests stay adopt-time: witness() checks
    # the next finish against the files as this write left them.
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

    # --- Summary ---
    gated_cnt = len(gated_names)
    never_cnt = rb_count.get('never', 0)
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
    # Conservation counts a line parked under ## Unfiled as carried,
    # yet every such bullet blocks finish until it is retyped; the
    # count is the agent's one notice of that work.
    if unfiled_parts:
        print(f'  unfiled: {len(unfiled_parts)} bullets to rehome')
    _ = never_cnt
    # Notes:
    # - The first line measures the file adopt read: every line must
    #   reach the cursor, standing.md, a label, or the manifest log.
    # - HANDOFF.orig.md, when present, is a hand-rewritten foreign file;
    #   the second line measures that rewrite so the agent sees what
    #   its own step dropped.
    # - The adopted file reproduces its own title line, which is not
    #   part of the cursor, so the union must carry it too.
    union_cursor = f'# Handoff: {folder.name}\n{cursor_text}'
    union_standing = standing_path.read_text(encoding='utf-8', errors='replace')
    # A pointer counts as carried only through a label the ledger
    # holds; the parse alone is no witness.
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
