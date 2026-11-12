"""standing.md items: parsing, ids, and supersession.
"""

import pathlib
import re

from handoff import storage

KIND_PREFIX = {'decision': 'd', 'constraint': 'c', 'dead-end': 'x', 'evidence': 'e'}


def join_headline_body(headline: str, body: str) -> str:
    """A bold headline joined to its body.

    Parameters
    ----------
    headline : str
        Headline text, typically bold-formatted.
    body : str
        Body text following the headline; may be empty.

    Returns
    -------
    str
        The headline alone for an empty body; a body opening with one of
        ``, . ; : ) ! ?`` joins with no separator, any other with one
        space.
    """
    if not body:
        return headline
    # A space before leading punctuation would alter the adopted
    # wording.
    joiner = '' if body[:1] in {',', '.', ';', ':', ')', '!', '?'} else ' '
    return f'{headline}{joiner}{body}'


def split_headline(content: str) -> tuple[str, str]:
    """Headline and body of one plain item's text.

    Parameters
    ----------
    content : str
        The item's text with its marker and any ``kind:`` prefix removed,
        holding no bold span.

    Returns
    -------
    tuple[str, str]
        The headline and the body, both stripped; the body is empty when
        the headline is the whole text. A first sentence with no word is
        returned as the headline, so the caller's refusal still runs.
    """
    for m in re.finditer(r'[.!?]"?(?=\s|$)', content):
        head, rest = content[:m.end()], content[m.end():]
        if head.count('"') % 2:
            continue
        if rest.strip() and len(re.findall(r'\w+', head)) in {1, 2}:
            continue
        return head.strip(), rest.strip()
    return content.strip(), ''


# A standing id standing alone as a word, outside a path or a hyphenated
# name.
CITED_ID = re.compile(r'(?<![\w/.-])([cdxe]\d{2,})(?![\w-])')


def drain_unfiled(
    cursor_text: str,
) -> tuple[list[tuple[str, str, str]], str, str | None]:
    """Typed bullets of the ## Unfiled section, and the cursor without it.

    Parameters
    ----------
    cursor_text : str
        The full cursor text including an optional ``## Unfiled`` section.

    Returns
    -------
    tuple[list[tuple[str, str, str]], str, str | None]
        A triple of: typed items as ``(kind, headline, body)``, the cursor
        text with ``## Unfiled`` removed, and a refusal string naming the
        problem line or ``None`` when all bullets are typed.

    Notes
    -----
    - A bold span after the first word is body text, as ``adopt`` reads
      it, so no word ahead of it is dropped.
    """
    unfiled_pat = re.compile(r'^## Unfiled\s*$', re.MULTILINE)
    next_h2 = re.compile(r'^## ', re.MULTILINE)
    match = unfiled_pat.search(cursor_text)
    if not match:
        return [], cursor_text, None
    rest = cursor_text[match.end():]
    nm = next_h2.search(rest)
    unfiled_body = rest[:nm.start()] if nm else rest
    after = rest[nm.start():] if nm else ''
    cursor_out = cursor_text[:match.start()] + after
    items: list[tuple[str, str, str]] = []
    bullets: list[str] = []
    for raw_line in unfiled_body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if raw_line[:1].isspace() and bullets:
            bullets[-1] += ' ' + stripped
            continue
        if not stripped.startswith('- '):
            return (
                [], cursor_text,
                (f'untyped Unfiled bullet: {stripped!r}'
                 ' - prefix it decision:, constraint:, or dead-end:,'
                 ' move it into a cursor section, or rehome it to a sibling'))
        bullets.append(stripped)
    for stripped in bullets:
        content = stripped[2:]
        kind = None
        for prefix in ('decision', 'constraint', 'dead-end'):
            if content.lower().startswith(f'{prefix}: '):
                kind = prefix
                content = content[len(prefix) + 2:].lstrip()
                break
        # Evidence needs a grade, so its refusal points to hq note.
        if kind is None and content.lower().startswith('evidence:'):
            return (
                [], cursor_text,
                (f'untyped Unfiled bullet: {stripped!r}'
                 ' - record evidence with hq note <slug> evidence --grade ...,'
                 ' then delete the bullet'))
        if kind is None:
            return (
                [], cursor_text,
                (f'untyped Unfiled bullet: {stripped!r}'
                 ' - prefix it decision:, constraint:, or dead-end:,'
                 ' move it into a cursor section, or rehome it to a sibling'))
        bold = re.match(r'\*\*(.+?)\*\*', content)
        if bold:
            headline = bold.group(1)
            body = content[bold.end():].strip()
        else:
            headline, body = split_headline(content)
        if not re.search(r'\w', headline):
            return (
                [], cursor_text,
                (f'Unfiled bullet has no headline: {stripped!r}'
                 ' - give the bold span, or the first sentence, a word'))
        items.append((kind, headline, body.replace('\n', ' ')))
    return items, cursor_out.rstrip() + '\n', None


def parse_standing(text: str) -> tuple[list[dict], set[str]]:
    """Items of standing.md and the ids that are no longer live.

    Parameters
    ----------
    text : str
        Raw text of ``standing.md``.

    Returns
    -------
    tuple[list[dict], set[str]]
        Every item, and the set of ids that are the old side of a
        supersession or retire line. Each item dict has keys ``id``,
        ``prefix`` (first character of the id), ``cycle`` (``''`` when
        absent), ``headline`` and ``body``.
    """
    items: list[dict] = []
    superseded_ids: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('- '):
            continue
        content = stripped[2:]
        sup_m = re.match(r'\(c\d+\)\s+([dcxe]\d+)\s*->\s*(?:[dcxe]\d+|-(?!\S))', content)
        if sup_m:
            superseded_ids.add(sup_m.group(1))
            continue
        id_pat = r'\[([dcxe]\d+)\]\s+(?:\(c(\d+)\)\s+)?\*\*(.+?)\*\*\s*(.*)'
        id_m = re.match(id_pat, content)
        if id_m:
            items.append({
                'id': id_m.group(1),
                'prefix': id_m.group(1)[0],
                'cycle': id_m.group(2) or '',
                'headline': id_m.group(3),
                'body': id_m.group(4),
                })
    return items, superseded_ids


def supersession_chains(
        text: str) -> dict[str, tuple[list[str], str, tuple[str, str] | None]]:
    """Each superseded or retired id's chain to the id now current.

    Parameters
    ----------
    text : str
        Raw text of ``standing.md``.

    Returns
    -------
    dict[str, tuple[list[str], str, tuple[str, str] | None]]
        Per id with an outgoing supersession or retire line: the chain
        ``[old, mid, current]``, the last hop's cycle ('' when retired
        directly), and the end id's (cycle, reason) retirement or None.
        A looping chain stops before the repeat, so its end is itself a
        key. An id superseded onto itself has no entry.
    """
    successors = {
        m.group(2): (m.group(3), m.group(1))
        for m in re.finditer(
            r'^\s*- \(c(\d+)\)\s+([dcxe]\d+)\s*->\s*([dcxe]\d+)', text, re.MULTILINE)
        }
    retirements = {
        m.group(2): (m.group(1), m.group(3).strip())
        for m in re.finditer(
            r'^[ \t]*- \(c(\d+)\)[ \t]+([dcxe]\d+)[ \t]*->[ \t]*-(?!\S)(.*)$', text, re.MULTILINE)
        }
    chains: dict[str, tuple[list[str], str, tuple[str, str] | None]] = {}
    for start in successors:
        chain = [start]
        seen = {start}
        while chain[-1] in successors:
            next_id = successors[chain[-1]][0]
            if next_id in seen:
                break
            seen.add(next_id)
            chain.append(next_id)
        if len(chain) > 1:
            chains[start] = (
                chain, successors[chain[-2]][1], retirements.get(chain[-1]))
    for retired_id, retirement in retirements.items():
        chains.setdefault(retired_id, ([retired_id], '', retirement))
    return chains


def _next_id(items: list[dict], prefix: str) -> str:
    """The lowest unused id for a prefix, two digits at least.
    """
    existing = {item['id'] for item in items if item['prefix'] == prefix}
    n = 1
    while True:
        candidate = f'{prefix}{n:02d}' if n <= 99 else f'{prefix}{n}'
        if candidate not in existing:
            return candidate
        n += 1


def note_line(
    standing_text: str,
    kind_str: str,
    headline: str,
    body: str,
    cycle: int | str,
) -> str | None:
    """One standing item line with the next free id for its kind.

    Parameters
    ----------
    standing_text : str
        Current text of standing.md, which fixes the ids in use.
    kind_str : str
        Item kind: a key of ``KIND_PREFIX``.
    headline : str
        Headline text. A tab or newline becomes a space and ``**`` markers
        are dropped.
    body : str
        Body text following the headline; may be empty. A newline becomes
        a space.
    cycle : int | str
        Cycle number recorded on the item.

    Returns
    -------
    str | None
        The item line without its newline, or ``None`` for an unknown kind.
    """
    prefix = KIND_PREFIX.get(kind_str)
    if not prefix:
        return None
    items, _ = parse_standing(standing_text)
    item_id = _next_id(items, prefix)
    headline_clean = ' '.join(headline.split()).replace('**', '')
    body_clean = body.replace('\n', ' ')
    bold = f'**{headline_clean}**'
    return f'- [{item_id}] (c{cycle}) {join_headline_body(bold, body_clean)}'.rstrip()


def do_note(
    folder: pathlib.Path,
    anch: dict,
    kind_str: str,
    headline: str,
    body: str,
    grade: str | None = None,
    is_open: bool = False,
    contradicts: str | None = None,
) -> int:
    """Append one standing item to standing.md.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing standing.md.
    anch : dict
        Anchors dict from anchors(); provides the current cycle number.
    kind_str : str
        Item kind: a key of ``KIND_PREFIX``.
    headline : str
        Bold-span headline text for the item.
    body : str
        Body text following the headline; may be empty except for
        evidence, whose body names its source.
    grade : str | None, default None
        Evidence only, and required there: measured, inferred, or
        asserted.
    is_open : bool, default False
        Evidence only: marks the evidence as still open.
    contradicts : str | None, default None
        Evidence only: a live standing id of any kind that the evidence
        contradicts.

    Returns
    -------
    int
        0 on success; 2 on an unknown kind, an empty headline, an
        evidence flag on another kind, or evidence with no grade or no
        body; 1 when ``contradicts`` names no live item. A refusal
        writes nothing.
    """
    standing_path = folder / 'standing.md'
    text = (
        standing_path.read_text(encoding='utf-8', errors='replace')
        if standing_path.exists() else ''
        )
    if kind_str not in KIND_PREFIX:
        print(
            'hq note: kind must be decision, constraint, dead-end, or evidence;'
            f' got {kind_str!r}')
        return 2
    is_evidence = kind_str == 'evidence'
    if not is_evidence and (grade is not None or is_open or contradicts is not None):
        print(
            'hq note: --grade, --open and --contradicts apply to evidence only'
            ' - drop them or note evidence')
        return 2
    if is_evidence and grade not in {'measured', 'inferred', 'asserted'}:
        print(
            'hq note: evidence needs --grade measured, inferred, or asserted'
            ' - add it and re-run')
        return 2
    if not re.search(r'\w', headline):
        print('hq note: --headline is required')
        return 2
    if is_evidence and not re.search(r'\w', body):
        print(
            'hq note: evidence needs its source as the body - name the file,'
            ' run, or wording it rests on and re-run')
        return 2
    if contradicts is not None:
        items, not_live_ids = parse_standing(text)
        if contradicts not in {i['id'] for i in items} - not_live_ids:
            print(
                f'hq note: --contradicts names {contradicts}, which is not a live'
                f' standing item - hq standing {folder.name} lists the ids')
            return 1
        body = f'{body.rstrip()}; contradicts [{contradicts}]'
    if is_evidence:
        headline = f'{grade}, open: {headline}' if is_open else f'{grade}: {headline}'
    line = note_line(text, kind_str, headline, body, anch['cycle'])
    storage.append_lines(standing_path, [line])
    item_id = parse_standing(line)[0][0]['id']
    print(f'{item_id} recorded in c{anch["cycle"]}')
    return 0
