"""What the cursor carries from one cycle to the next.
"""

import pathlib
import re

from handoff import markdown, storage

_STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'into', 'then',
    'than', 'when', 'what', 'which', 'where', 'while', 'about', 'after',
    'before', 'over', 'under', 'only', 'also', 'each', 'every', 'other',
    'there', 'their', 'these', 'those', 'have', 'has', 'had', 'not',
    'but', 'are', 'was', 'were', 'been', 'being', 'will', 'would', 'should',
    'could', 'must', 'may', 'can', 'its', 'our', 'your', 'they', 'them',
    'step', 'next', 'first', 'last', 'now', 'here', 'state', 'build',
    'phase',
    }


def collisions(
    now_text: str,
    headlines: list[tuple[str, str]],
    headings: list[tuple[str, int, str]],
    rarity: dict[str, int],
) -> list[str]:
    """Find terms in the Now step that collide with dead-end or spec headings.

    Parameters
    ----------
    now_text : str
        The Now section text.
    headlines : list[tuple[str, str]]
        Dead-end item pairs of ``(headline_text, source_id)``.
    headings : list[tuple[str, int, str]]
        Spec heading triples of ``(file_path, line_number, heading_text)``.
    rarity : dict[str, int]
        Maps each term to the count of top-level files containing it;
        lower means rarer.

    Returns
    -------
    list[str]
        Hit strings as ``'collides: <term> <- <where> "<text>"'``, sorted
        by ascending rarity, at most three.

    Notes
    -----
    - The quoted text is the heading's own words: leading ``#`` markers
      and every backtick are stripped, so the hit reads as the design
      writes it.
    """
    now_terms = extract_terms(now_text)
    hits: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()

    sources: list[tuple[str, str]] = (
        [(f'{fp}:{ln}', ht) for fp, ln, ht in headings]
        + [(src_id, hl) for hl, src_id in headlines]
    )
    for where, text in sources:
        for term in sorted(now_terms):
            if term in seen:
                continue
            pattern = r'(?<!\w)' + re.escape(term) + r'(?!\w)'
            if re.search(pattern, text, re.IGNORECASE):
                seen.add(term)
                hits.append((rarity.get(term, 0), term, where, text))

    hits.sort(key=lambda x: x[0])
    out: list[str] = []
    for _, term, where, text in hits[:3]:
        shown = re.sub(r'^#+', '', text).replace('`', '').strip()
        out.append(f'collides: {term} <- {where} "{shown}"')
    return out


def conservation(
    original: str,
    cursor: str,
    standing: str,
    labels: list[str],
) -> list[str]:
    """Return lines of the original handoff missing from the adopted union.

    Parameters
    ----------
    original : str
        Content of the original ``HANDOFF.md``.
    cursor : str
        Content of the adopted cursor.
    standing : str
        Content of ``standing.md`` after adoption.
    labels : list[str]
        Ledger label strings written during adoption.

    Returns
    -------
    list[str]
        Lines of ``original`` absent from the union as a substring match.

    Notes
    -----
    - Union membership uses substring containment, so a wrapped bullet
      stored as one line in standing.md still passes, and so does a line
      the union wraps onto indented continuation lines: they join back
      onto the line above before the comparison.
    - Whitespace runs collapse on both sides, so a line re-spaced in the
      union still passes.
    - ``labels`` may carry any extra union text; adoption passes the
      legacy Log lines and the wrapped header's tail it moved into the
      manifest row and, for the ``HANDOFF.orig.md`` line, the text of
      every live notes row and every row graded ``edit``.
    - A heading is covered when its normalized text is in the union OR
      when at least one content line from its section is in the union,
      so drained sections whose bullets moved to standing are not
      reported as missing.
    - The header line, wherever ``Written: ... | Cycle: N`` sits on it,
      is the script's, and never reported.
    - A checkbox after the bullet marker (``[ ]``, ``[x]``) is structure:
      a Plan item ticked done still counts as carried.
    """
    _standing_prefix = re.compile(r'^- \[[dcxe]\d+\] \(c\d+\) ')
    _kf_grade = re.compile(r'^(read now|reference only)\s*:\s*', re.IGNORECASE)
    _kf_label_only = re.compile(
        r'^(?:[-*+]\s+|#+\s*)?(?:read now|reference only)\s*:?\s*$', re.IGNORECASE)

    def _normalize(line: str) -> str:
        """Return a normalized form for union membership comparison.
        """
        s = ' '.join(line.split())
        if _kf_label_only.match(s):
            return ''
        # Notes:
        # - Standing prefix (``- [dcx<n>] (c<n>) ``) is collapsed to
        #   ``- `` so a bullet already filed compares equal to its
        #   original.
        # - Pointer prefix, grade label, and leading markers are
        #   stripped so the label text alone is compared.
        s = _standing_prefix.sub('- ', s)
        s = re.sub(r'^#+\s*', '', s)
        s = re.sub(r'^[-*+]\s+', '', s)
        # A ticked box is the same item: the checkbox is structure.
        s = re.sub(r'^\[[ xX]\]\s*', '', s)
        s = re.sub(r'^\d+[.)]\s+', '', s)
        s = re.sub(r'^unfiled:\s+', '', s)
        s = s.replace('**', '').replace('`', '')
        # Notes:
        # - The bold span, backticks, the ``unfiled:`` tag, and a grade
        #   label - leading, or inline after a pointer's path - are
        #   structure on both sides of the comparison, never wording.
        # - A pointer keeps its path, so the union must carry the
        #   pointer as written, ``<path> <text>``, for the line to
        #   count.
        s = _kf_grade.sub('', s)
        return re.sub(
            r'^(\S+)\s+(?:read now|reference only)\s*:?\s*', r'\1 ', s,
            flags=re.IGNORECASE)

    def _joined(part: str) -> list[str]:
        """Join each indented line onto the line above and return the lines.
        """
        out_lines: list[str] = []
        for raw in part.splitlines():
            if not raw.strip():
                continue
            if raw[:1].isspace() and out_lines:
                out_lines[-1] += ' ' + raw.strip()
            else:
                out_lines.append(raw)
        return out_lines

    # A line the rewrite wrapped at the column joins back before the
    # comparison, as a Log item or a standing bullet does; the raw lines
    # stay in the union too, so nothing that passed before fails.
    union_text = '\n'.join(
        _normalize(ln)
        for part in [cursor, standing] + labels
        for ln in part.splitlines() + _joined(part)
        if ln.strip())

    # Build per-section content index for heading coverage checks.
    orig_lines = original.splitlines()
    section_content: dict[str, list[str]] = {}
    current_heading: str | None = None
    for raw in orig_lines:
        if raw.lstrip().startswith('#') and not (
                current_heading == 'Key files'
                and markdown.KF_LABEL_PAT.match(raw)):
            current_heading = _normalize(raw)
            section_content.setdefault(current_heading, [])
        elif current_heading is not None and raw.strip():
            section_content[current_heading].append(_normalize(raw))

    def _heading_covered(norm_heading: str) -> bool:
        """Return True if any content line from the section is in union_text.
        """
        return any(c and c in union_text for c in section_content.get(norm_heading, []))

    result = []
    in_key_files = False
    for ln in orig_lines:
        if not ln.strip():
            continue
        if markdown.HEADER_PAT.search(ln):
            continue
        is_heading = ln.lstrip().startswith('#')
        # A heading-form grade label is structure only under Key files;
        # anywhere else a heading adopt drops is a lost line.
        kf_label = in_key_files and bool(markdown.KF_LABEL_PAT.match(ln.lstrip()))
        if is_heading and not kf_label:
            in_key_files = _normalize(ln) == 'Key files'
        norm = _normalize(ln)
        if not norm:
            # Only a bare grade label normalizes to nothing and is
            # structure; any other empty form is content the union
            # lacks.
            if _kf_label_only.match(ln.strip()):
                continue
            result.append(ln)
            continue
        # An item carried but annotated keeps every word of the
        # original and appends after its final mark, so that mark is
        # the only thing blocking the match.
        trimmed = norm.rstrip('.,;:')
        if norm in union_text or (
                trimmed != norm and trimmed and trimmed in union_text):
            continue
        if is_heading and (kf_label or _heading_covered(norm)):
            continue
        result.append(ln)
    return result


def sibling_texts(folder: pathlib.Path, live: dict[str, storage.Row]) -> list[str]:
    """Return the text of every live notes or edit-graded sibling on disk.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder the folder-relative rows resolve against.
    live : dict[str, Row]
        Live ledger rows keyed by path.

    Returns
    -------
    list[str]
        File contents, in ``live`` order, of each row whose status is
        ``live`` and whose kind is ``notes`` or whose read obligation is
        ``edit``; a row whose file is absent or unreadable is skipped.

    Notes
    -----
    - The skill rehomes what fits nowhere into a sibling stamped notes
      or edit, so a conservation check counts those files as carrying
      the lines moved into them.
    """
    texts: list[str] = []
    for row in live.values():
        if row['status'] != 'live' or (
                row['kind'] != 'notes' and row['read_before'] != 'edit'):
            continue
        sibling = (
            pathlib.Path(row['path']).expanduser() if row['base'] == 'abs'
            else folder / row['path'])
        if not sibling.is_file():
            continue
        try:
            texts.append(sibling.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            continue
    return texts


def not_carried(
    folder: pathlib.Path,
    rows: list[storage.Row],
    live: dict[str, storage.Row],
    manifest: list[dict],
    cursor: str,
    standing: str,
    cycle: int,
) -> tuple[int, list[str]]:
    """Return the last finished cycle and its cursor lines nothing carries.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder, whose ``cycles/`` archive supplies the comparand.
    rows : list[Row]
        Every ledger row, read for the paths this cycle stamped.
    live : dict[str, Row]
        Live rows keyed by path, supplying the label and sibling witnesses.
    manifest : list[dict]
        Cycle manifest; its last row names the cycle compared against.
    cursor : str
        Cursor of the cycle being filed, with Unfiled already drained.
    standing : str
        ``standing.md`` text, including the items this cycle drained.
    cycle : int
        Cycle being filed, which gates the stamped-this-cycle witnesses.

    Returns
    -------
    tuple[int, list[str]]
        The previous cycle number, and its cursor lines that the new
        cursor, ``standing.md``, the live labels and the stamped
        siblings no longer carry. The list is empty where no archive
        precedes this cycle.

    Notes
    -----
    - A heading carries no fact, so a section omitted as empty is never
      reported by its heading.
    - A Now step is spent by design, since the cycle completing it
      writes the next one in its place. That exemption reaches only
      lines appearing nowhere else in the previous cursor, so a Plan
      item the Now step quotes is still reported when it is dropped.
    - A file stamped this cycle carries the lines rehomed into it. A
      sibling stamped in an earlier cycle carries them only when its
      kind is ``notes`` or its read obligation is ``edit``.
    """
    if not manifest:
        return 0, []
    prev_cycle = int(manifest[-1]['cycle'])
    prev_archive = folder / 'cycles' / f'c{prev_cycle:02d}.md'
    if not prev_archive.is_file():
        return prev_cycle, []
    prev_cursor = markdown.split_handoff(
        prev_archive.read_text(encoding='utf-8', errors='replace'))['cursor']
    witnesses: list[str] = []
    for path, row in live.items():
        if row['status'] != 'live' or row.get('label', '-') == '-':
            continue
        witnesses.extend((row['label'], f'{path} {row["label"]}'))
    # The folder's own bookkeeping is no witness: cycles/cNN.md holds
    # the very cursor being compared against and HANDOFF.md holds the
    # one being filed, so stamping either would pass every line at once.
    stamped_now = {
        r['path'] for r in rows
        if r['cycle'].strip() == str(cycle)
        and 'cycles' not in pathlib.PurePath(r['path']).parts
        and not pathlib.PurePath(r['path']).name.startswith('HANDOFF')}
    for path in stamped_now:
        row = live.get(path)
        if row is None or row['status'] != 'live':
            continue
        target = (
            pathlib.Path(path).expanduser() if row['base'] == 'abs'
            else folder / path)
        if not target.is_file():
            continue
        try:
            witnesses.append(target.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            continue
    now_m = re.search(r'^## Now\s*$', prev_cursor, re.MULTILINE)
    spent = ''
    outside = prev_cursor
    if now_m:
        rest = prev_cursor[now_m.end():]
        nxt = re.search(r'^## ', rest, re.MULTILINE)
        spent = rest[:nxt.start()] if nxt else rest
        outside = (
            prev_cursor[:now_m.start()]
            + prev_cursor[now_m.end() + len(spent):])
    # A line the Now step shares with another section is not spent: a
    # Now step written as the Plan item it names must not exempt that
    # item's own drop.
    spent_lines = {
        ' '.join(ln.split()) for ln in spent.splitlines() if ln.strip()}
    spent_lines -= {
        ' '.join(ln.split()) for ln in outside.splitlines() if ln.strip()}
    return prev_cycle, [
        ln for ln in conservation(
            prev_cursor, cursor, standing,
            witnesses + sibling_texts(folder, live))
        if not ln.lstrip().startswith('#')
        and ' '.join(ln.split()) not in spent_lines]


def extract_terms(text: str) -> set[str]:
    """Extract backticked tokens and distinctive terms from text.

    Parameters
    ----------
    text : str
        Source text to scan.

    Returns
    -------
    set[str]
        Backtick-enclosed tokens and distinctive words from the text.
    """
    terms: set[str] = set()
    # Notes:
    # - Backtick-enclosed tokens are taken verbatim.
    # - Outside backticks: CamelCase words, snake_case identifiers, and
    #   capitalized words not at a sentence start are extracted.
    # Notes:
    # - A backticked `2` or `ka` is a marker in prose, not a term worth
    #   a collision line: three characters and a letter are its floor.
    # - A plain word needs four characters and must not be a stopword,
    #   so `The` and `Step` at the head of a Now step stay quiet, and
    #   so do `State`, `Build`, and `Phase` mid-sentence.
    # - A word opening a line, a bullet, an enumerator, a heading, or a
    #   quote is capitalized by position, so it reads as a sentence
    #   start whatever character precedes it.
    terms.update(
        m.group(1) for m in re.finditer(r'`([^`]+)`', text)
        if len(m.group(1)) >= 3 and not m.group(1).isdigit())
    stripped = re.sub(r'`[^`]+`', '', text)
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:[A-Z][a-z]*)+|[a-z]+(?:_[a-z]\w*)+|[A-Z][a-z]{2,})\b',
        stripped,
    ):
        term = m.group(1)
        before = stripped[:m.start()].rstrip()
        line_head = re.fullmatch(
            r'\s*(?:[-*+]|\d+[.)]|#+|>)?\s*',
            stripped[stripped.rfind('\n', 0, m.start()) + 1:m.start()])
        sentence_start = not before or before[-1] in '.!?' or bool(line_head)
        if re.fullmatch(r'[A-Z][a-z]{2,}', term) and sentence_start:
            continue
        if len(term) < 4 or term.lower() in _STOPWORDS:
            continue
        terms.add(term)
    return terms
