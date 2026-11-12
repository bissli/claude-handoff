"""HANDOFF.md and artifact text: sections, headings, and anchor spans.
"""

import hashlib
import re

HEADER_PAT = re.compile(r'Written:\s*.+?\s*\|\s*Cycle:\s*(\d+)')


WORKED_PAT = re.compile(r'Last worked:\s*(\d{4}-\d{2}-\d{2})')


ATX_HEADING_PAT = re.compile(r'#{1,6}(?:[ \t]|$)')


# Notes:
# - Under Key files a grading label written as a heading is structure
#   and opens no section, for the adopt parser and conservation alike.
# - A label may carry a clause, `Read now, under x/ unless noted:`, and
#   is structure when the whole line ends with a colon.
KF_LABEL_PAT = re.compile(
    r'^\s*(?:[-*+]\s+|#+\s*)?(read now|reference only)\b(?:[^\n]*:)?\s*$',
    re.IGNORECASE)


def resolve_where(
    text: str,
    anchors: list[str],
) -> tuple[list[tuple[int, int]], list[str]]:
    """Resolve anchor strings to 1-based inclusive line spans in a file.

    Parameters
    ----------
    text : str
        Full content of the target file.
    anchors : list[str]
        Heading texts from a row's ``where`` field. Backticks, ``#``
        markers, numbering and surrounding whitespace drop before the
        comparison, which ignores case.

    Returns
    -------
    tuple[list[tuple[int, int]], list[str]]
        One span per matched anchor, and the anchors that matched nothing.

    Notes
    -----
    - ``s<d>`` or ``s<d><letter>`` resolves to the first heading whose
      leading token matches, from the token set ``_norm_heading`` strips.
      The whole token must match, so ``s11`` never lands on
      ``# 11b. Proof``. A bare ``S3`` with no dot or colon is no token.
    - A dotted number is one token: ``s24.4`` names ``### 24.4 The
      decision`` and ``s24`` does not. The dotted form names a
      sub-heading whose title repeats.
    - A letter-led id is a token too: ``F65``, ``f65`` and ``sF65`` all
      resolve ``## F65. Title``. The letters count, so ``F7`` never
      lands on ``## 7. Seven``, and a word such as ``Log4j:`` is title
      text.
    - Two anchors naming one heading return that span twice.
    """
    file_lines = text.splitlines()
    heading_info: list[tuple[int, int, str, set[str]]] = []
    fence = ''
    for i, line in enumerate(file_lines):
        # Notes:
        # - A closing fence carries nothing after the marker, so a
        #   ```python line inside a block closes nothing. Reading it
        #   as a close inverts the fence state for the rest of the file.
        # - A closing fence repeats the opening character at its own
        #   length or longer, so a ``` line inside a ~~~ block is
        #   content.
        # - The indent is spaces, and four columns of either spaces or a
        #   tab make the line code.
        fence_m = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence_m:
            marker, tail = fence_m.group(1), fence_m.group(2).strip()
            if not fence:
                fence = marker
            elif not tail and marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ''
            continue
        if fence:
            continue
        if ATX_HEADING_PAT.match(line):
            level = len(line) - len(line.lstrip('#'))
            # The dotted branch leads so `24.4` is one token, never
            # the integer `24` with `.4` left over.
            token_m = re.match(
                r'^#+\s*(?:(?P<dotted>\d+(?:\.\d+)+)[.:]?'
                r'|(?P<numlet>\d+[a-z])(?:[.:]|\s+-(?=\s))|(?P<num>\d+)[.:]?'
                r'|(?P<letters>[a-z]{1,2})(?P<iddigits>\d+[a-z]?)'
                r'(?:[.:]|\s+-(?=\s)))(?=\s|$)',
                line, re.IGNORECASE)
            tokens: set[str] = set()
            if token_m:
                letters = token_m.group('letters')
                if letters is None:
                    tokens.add((
                        token_m.group('dotted') or token_m.group('numlet')
                        or token_m.group('num')).lower())
                else:
                    id_digits = token_m.group('iddigits')
                    tokens.add((letters + id_digits).lower())
                    # `s4:` numbers the heading 4 as `4.` does.
                    if letters.lower() == 's':
                        tokens.add(id_digits.lower())
            heading_info.append((i + 1, level, _norm_heading(line), tokens))
    spans: list[tuple[int, int]] = []
    unresolved: list[str] = []
    total = len(file_lines)
    for part in anchors:
        # _norm_heading strips the token that names the section, so
        # the s<d> and id forms are read before normalizing.
        anchor_keys: list[str] = []
        section_m = re.fullmatch(
            r's(\d+(?:\.\d+)*[a-z]?)', part.strip(), re.IGNORECASE)
        if section_m:
            anchor_keys.append(section_m.group(1).lower())
        ident_m = re.fullmatch(r's?([a-z]+\d+[a-z]?)', part.strip(), re.IGNORECASE)
        if ident_m:
            anchor_keys.extend([ident_m.group(1).lower(), part.strip().lower()])
        norm_part = _norm_heading(part)
        match_idx = None
        for j, (_, _, norm_h, _) in enumerate(heading_info):
            if norm_part and norm_h == norm_part:
                match_idx = j
                break
        if match_idx is None and anchor_keys:
            for j, (_, _, _, tokens) in enumerate(heading_info):
                if tokens.intersection(anchor_keys):
                    match_idx = j
                    break
        if match_idx is None:
            unresolved.append(part)
            continue
        start_line, level, _, _ = heading_info[match_idx]
        end_line = total
        for k in range(match_idx + 1, len(heading_info)):
            if heading_info[k][1] <= level:
                end_line = heading_info[k][0] - 1
                break
        spans.append((start_line, end_line))
    # Notes:
    # - A parent's tail past its last selected child has no child to
    #   hold it, so the innermost span reaches the widest enclosing
    #   end and the trimmed spans still cover every line.
    # - Only a strictly later start trims, so two anchors landing on
    #   one heading leave each other alone.
    starts = [beg for beg, _ in spans]
    trimmed: list[tuple[int, int]] = []
    for beg, end in spans:
        cover = max(far for near, far in spans if near <= beg <= far)
        inner = [b for b in starts if beg < b <= cover]
        trimmed.append((beg, min(inner) - 1 if inner else cover))
    return trimmed, unresolved


def block_sha(heading_and_body: str) -> str:
    """First 12 hex digits of the sha256 of the block text, normalized.
    """
    normalized = re.sub(r' +', ' ', heading_and_body)
    # An empty body leaves a trailing newline that the reader's stripped
    # block text lacks, so it must not count.
    normalized = '\n'.join(line.rstrip() for line in normalized.split('\n')).rstrip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def directives_text(cursor: str) -> str:
    """Body of the cursor's ``## Directives`` section, whitespace collapsed.

    Parameters
    ----------
    cursor : str
        Cursor text as ``split_handoff`` returns it.

    Returns
    -------
    str
        The lines up to the next ``## `` heading, each whitespace run
        collapsed to one space, so a re-wrapped bullet compares equal.
        Empty when the section is absent or holds nothing.
    """
    section_m = re.search(r'^## Directives\s*$', cursor, re.MULTILINE)
    if not section_m:
        return ''
    rest = cursor[section_m.end():]
    next_m = re.search(r'^## ', rest, re.MULTILINE)
    return ' '.join((rest[:next_m.start()] if next_m else rest).split())


def split_handoff(text: str) -> dict:
    """Parse HANDOFF.md text into its parts.

    Parameters
    ----------
    text : str
        Raw text of a ``HANDOFF.md`` file.

    Returns
    -------
    dict
        Keys: ``header`` (str), ``slug`` (str), ``cycle`` (int or None),
        ``cursor`` (str), ``blocks`` (block name to stripped body text),
        ``log`` (str). A part the text lacks is ``''``, ``None`` or empty.
    """
    result: dict = {'header': '', 'slug': '', 'cycle': None,
                    'cursor': '', 'blocks': {}, 'log': ''}
    for line in text.splitlines():
        m = HEADER_PAT.search(line)
        if m:
            result['header'] = line.strip()
            result['cycle'] = int(m.group(1))
            break
    tm = re.search(r'^# Handoff:\s*(.+)', text, re.MULTILINE)
    if tm:
        result['slug'] = tm.group(1).strip()
    block_open = re.compile(r'^<!-- hq:(\w+) \S+ -->', re.MULTILINE)
    block_close = re.compile(r'^<!-- /hq:(\w+) -->', re.MULTILINE)
    cs = re.search(r'^## ', text, re.MULTILINE)
    lm = re.search(r'^## Log\s*$', text, re.MULTILINE)
    fb = block_open.search(text)
    if cs:
        ends = [m.start() for m in (lm, fb) if m]
        cursor_end = min(ends) if ends else len(text)
        result['cursor'] = text[cs.start():cursor_end]
    if lm:
        result['log'] = text[lm.end():]
    pos = 0
    while True:
        om = block_open.search(text, pos)
        if not om:
            break
        name = om.group(1)
        cm = block_close.search(text, om.end())
        if not cm:
            break
        result['blocks'][name] = text[om.end():cm.start()].strip()
        pos = cm.end()
    return result


def _norm_heading(text: str) -> str:
    """Normalize a heading string for anchor matching.

    Parameters
    ----------
    text : str
        A heading line or an anchor string.

    Returns
    -------
    str
        Lower-cased text with the ``#`` markers, the leading section
        token, backticks, and surrounding whitespace removed.

    Notes
    -----
    - The leading section token is ``<d>``, ``<d>.``, ``<d>:``, ``<d> -``,
      a dotted number ``<d>.<d>``, ``<d><letter>`` or a letter-led id
      ``<letters><d>[<letter>]``, each with the same delimiters. So
      ``## 11b. Proof``, ``## s11b: Proof``, ``## 2a - Basis``,
      ``## 3.1 Warmup`` and ``## F65. Title`` all lose their token. A
      bare ``S3`` with no dot or colon is a word and stays.
    - An id takes one or two letters and its dot is not followed by a
      digit, so ``Log4j:``, ``OAuth2:`` and ``v2.0`` are title words.
    - The dash delimiter needs whitespace on both sides, and a letter
      suffix needs a ``.``, ``:`` or `` - `` after it. So ``2a-b`` and
      ``3D`` lose only their plain digit.
    """
    text = re.sub(r'^[#\s]+', '', text)
    text = re.sub(
        r'^(?:[a-z]{1,2}\d+[a-z]?(?:[.:](?!\d)|\s+-(?=\s))'
        r'|\d+[a-z](?:[.:]|\s+-(?=\s))|\d[\d.]*(?::|\s+-(?=\s))?)\s*',
        '', text, flags=re.IGNORECASE)
    text = re.sub(r'`', '', text)
    return text.strip().lower()


def split_where(where: str) -> list[str]:
    r"""Split a ledger ``where`` field into its anchors.

    Parameters
    ----------
    where : str
        The field as stored: anchors joined with ``;``, or ``-``.

    Returns
    -------
    list[str]
        The anchors, stripped, with empty parts dropped. ``-`` gives none.

    Notes
    -----
    - A ``;`` inside a heading's text is written ``\\;`` in the field.
      The split is on an unescaped ``;``, and each anchor gets its ``;``
      back.
    """
    if where in {'-', ''}:
        return []
    parts = re.split(r'(?<!\\);', where)
    return [part.replace('\\;', ';').strip() for part in parts if part.strip()]
