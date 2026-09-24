"""Section anchors that carry a letter, and the headline-body join in
the rendered standing block.
"""

from bin import hq

# Numbered headings, one with a letter suffix, and two headings whose
# first word merely starts with a digit.
_TEXT = (
    '# 11. Implementation plan\n'
    '\n'
    'Implementation content.\n'
    '\n'
    '# 11b. Proof\n'
    '\n'
    'Proof content.\n'
    '\n'
    '# 12. Migration\n'
    '\n'
    'Migration content.\n'
    '\n'
    '## S3 bucket layout\n'
    '\n'
    'S3 content.\n'
    '\n'
    '## 3D printing\n'
    '\n'
    '3D content.\n'
)

# --- resolve_where ---


def test_s11_resolves_to_plain_section_not_letter_suffixed():
    """s11 resolves to '# 11. Implementation plan', not '# 11b. Proof'.

    Mutation: prefix match on section number instead of equality, so s11
    lands on 11b (the first heading whose number starts with '11').
    Oracle: hand-computed span (1, 4) for '# 11. Implementation plan'.
    """
    spans, unresolved = hq.resolve_where(_TEXT, ['s11'])
    assert not unresolved
    assert spans == [(1, 4)]


def test_s11b_resolves_to_letter_suffixed_heading():
    """s11b resolves to '# 11b. Proof' and not to '# 11. Implementation plan'.

    Mutation: letter suffix stripped from the extracted number token, so 11b
    becomes 11 and both s11 and s11b resolve to the same section 11 heading.
    Oracle: hand-computed span (5, 8) for '# 11b. Proof'.
    """
    spans, unresolved = hq.resolve_where(_TEXT, ['s11b'])
    assert not unresolved
    assert spans == [(5, 8)]


def test_s0_resolves_a_dotted_s_prefixed_heading():
    """`s0` resolves `## S0. Warmup checks (before rollout)` like the literal form.

    Mutation: the section index accepting `s<n>:` alone while _norm_heading
    strips `s<n>.` too, so the literal anchor resolves and the number
    anchor prints `?`.
    Oracle: hand-computed - the heading is line 3 and the next same-level
    heading is line 6, so both anchor forms give the span 3-5.
    """
    text = (
        '# T\n'
        '\n'
        '## S0. Warmup checks (before rollout)\n'
        'body\n'
        '\n'
        '## S1. Pool selection\n'
        'more\n'
    )
    assert hq.resolve_where(text, ['s0']) == ([(3, 5)], [])
    assert hq.resolve_where(
        text, ['Warmup checks (before rollout)']) == ([(3, 5)], [])
    assert hq.resolve_where(text, ['s1']) == ([(6, 7)], [])


def test_dash_separated_number_is_a_section_token():
    """`N - Title` and `Na - Title` headings resolve by title and by `s<n>`.

    Mutation: `.` and `:` the only delimiters after a section number in
    _norm_heading and the resolve_where index, so the title anchor keeps
    `- ` or `a - ` and `s2a` never reads `2a` as a number.
    Oracle: hand-computed spans on the fixture - `### 2 - ...` is line 3
    and runs to line 8, `#### 2a - ...` is lines 5-6; `3D printing`,
    `S4 notes`, and `2a-b range` keep their current normalized forms.
    """
    text = (
        '# Retry design - open items\n'
        '\n'
        '### 2 - The retry window\n'
        'body\n'
        '#### 2a - The backoff basis\n'
        'body\n'
        '#### 2c - Cache path\n'
        'body\n'
        '### 3 - Next\n'
        'body\n'
    )
    assert hq.resolve_where(
        text, ['The backoff basis']) == ([(5, 6)], [])
    assert hq.resolve_where(text, ['s2a']) == ([(5, 6)], [])
    assert hq.resolve_where(
        text, ['The retry window']) == ([(3, 8)], [])
    assert hq.resolve_where(text, ['s2']) == ([(3, 8)], [])
    assert hq._norm_heading('## 3D printing') == 'd printing'
    assert hq._norm_heading('## S4 notes') == 's4 notes'
    assert hq._norm_heading('## 2a-b range') == 'a-b range'
    assert hq._norm_heading('# 11b. Proof') == 'proof'
    assert hq._norm_heading('## s11b: Proof') == 'proof'


def test_letter_led_section_id_resolves_by_its_id():
    """`F7`, `f7`, and `sF7` resolve `## F7. Title`; `Q3` resolves `## Q3: Title`.

    Mutation: the section index accepting digit-led tokens alone, so a
    heading keyed `F<n>` has no short anchor and only its full title
    resolves; or _norm_heading leaving `F7.` in place, so the title alone
    does not resolve either; or the id compared without its letters, so
    `F7` lands on `## 7. Seven`.
    Oracle: hand-computed spans on the fixture - `## 7. Seven` is lines
    3-4, `## F7. ...` lines 5-6, `## Q3: ...` lines 7-8.
    """
    text = (
        '# Findings\n'
        '\n'
        '## 7. Seven\n'
        'body\n'
        '## F7. Cache hit ratio holds (n>=2)\n'
        'body\n'
        '## Q3: Open question\n'
        'body\n'
    )
    for anchor in ('F7', 'f7', 'sF7'):
        assert hq.resolve_where(text, [anchor]) == ([(5, 6)], []), anchor
    assert hq.resolve_where(
        text, ['Cache hit ratio holds (n>=2)']) == ([(5, 6)], [])
    assert hq.resolve_where(text, ['q3']) == ([(7, 8)], [])
    assert hq.resolve_where(text, ['s7']) == ([(3, 4)], [])
    assert hq._norm_heading(
        '## F7. Cache hit ratio holds (n>=2)') == 'cache hit ratio holds (n>=2)'


def test_a_digit_bearing_first_word_and_a_dotted_number_keep_the_title_whole():
    """`Log4j: notes` is a title, and `3.1 Cache warmup` strips to `cache warmup`.

    Mutation: the letter-led id taking any number of letters, so
    `Log4j:` is stripped as an id and the anchor `Log4j: notes` lands on
    the earlier `## Notes`; or the optional-letter number branch taking
    `3.` before the dotted-number fallback, so `3.1 Cache warmup`
    normalizes to `1 cache warmup` and its title never resolves.
    Oracle: hand-computed spans - `## Log4j: notes` is lines 3-4 after
    `## Notes` at 1-2; `## 3.1 Cache warmup` is lines 6-7 and
    `## 3.2. Retry budget` lines 8-9.
    """
    text = (
        '## Notes\n'
        'body one\n'
        '## Log4j: notes\n'
        'body two\n'
        '# Spec\n'
        '## 3.1 Cache warmup\n'
        'body\n'
        '## 3.2. Retry budget\n'
        'body\n'
    )
    assert hq.resolve_where(text, ['Log4j: notes']) == ([(3, 4)], [])
    assert hq.resolve_where(text, ['Notes']) == ([(1, 2)], [])
    assert hq.resolve_where(text, ['Cache warmup']) == ([(6, 7)], [])
    assert hq.resolve_where(text, ['Retry budget']) == ([(8, 9)], [])
    assert hq._norm_heading('## OAuth2: token flow') == 'oauth2: token flow'
    assert hq._norm_heading('## v2.0 release') == 'v2.0 release'
    assert hq._norm_heading('## Q3: Open') == 'open'


def test_literal_proof_resolves_to_11b_heading_by_text_match():
    """Literal anchor 'Proof' resolves to '# 11b. Proof' via normalized text.

    Mutation: _norm_heading fails to strip the '11b.' token from the heading,
    leaving the heading's norm as '11b. proof' instead of 'proof', so the
    literal match fails.
    Oracle: hand-computed span (5, 8) for '# 11b. Proof'.
    """
    spans, unresolved = hq.resolve_where(_TEXT, ['Proof'])
    assert not unresolved
    assert spans == [(5, 8)]


def test_anchor_with_section_number_and_title_resolves_by_normalization():
    """Anchor '11b. Proof' normalizes to 'proof' and matches '# 11b. Proof'.

    Mutation: _norm_heading does not strip the '11b.' token from the anchor,
    so the normalized anchor is '11b. proof' and the equality check fails.
    Oracle: hand-computed span (5, 8).
    """
    spans, unresolved = hq.resolve_where(_TEXT, ['11b. Proof'])
    assert not unresolved
    assert spans == [(5, 8)]


def test_s11b_anchor_is_case_insensitive():
    """Uppercase 'S11B' resolves to '# 11b. Proof' case-insensitively.

    Mutation: re.IGNORECASE dropped from the s-anchor fullmatch, so 'S11B'
    does not enter the section-fallback path and goes unresolved.
    Oracle: hand-computed span (5, 8) for '# 11b. Proof'.
    """
    spans, unresolved = hq.resolve_where(_TEXT, ['S11B'])
    assert not unresolved
    assert spans == [(5, 8)]


def test_s3d_stays_unresolved_because_3d_heading_has_no_section_number():
    """Anchor 's3d' is unresolved because '## 3D printing' bears no section token.

    Mutation: number regex matches '3' from '3D printing' and a bare 's3d'
    resolves to that heading incorrectly.
    Oracle: unresolved=['s3d'] since '## 3D printing' has no dot or colon
    after the digit, so no section number is extracted.
    """
    spans, unresolved = hq.resolve_where(_TEXT, ['s3d'])
    assert spans == []
    assert unresolved == ['s3d']


# --- _norm_heading ---


def test_norm_heading_strips_letter_suffixed_section_token():
    """_norm_heading strips '11b.' as a section token, returning 'proof'.

    Mutation: the token regex matches only pure digits, leaving '11b. proof'
    instead of stripping the whole token.
    Oracle: _norm_heading('# 11b. Proof') == 'proof'.
    """
    assert hq._norm_heading('# 11b. Proof') == 'proof'


def test_norm_heading_s3_bucket_stays_as_word():
    """_norm_heading preserves 'S3' in '## S3 bucket layout' as a word.

    Mutation: the token regex strips 'S3' because it starts with 's' followed
    by a digit, dropping the leading word of the heading.
    Oracle: _norm_heading('## S3 bucket layout') == 's3 bucket layout'.
    """
    assert hq._norm_heading('## S3 bucket layout') == 's3 bucket layout'


def test_norm_heading_3d_printing_pins_current_output():
    """A digit-led word with no dot or colon is not a section token.

    Mutation: the letter-suffix strip made to fire without a following
    `.` or `:`, so `3D` is cut whole and the heading normalizes to
    'printing'.
    Oracle: the pinned output 'd printing' - the plain-digit fallback cuts
    the `3` alone and leaves the `d`.
    """
    assert hq._norm_heading('## 3D printing') == 'd printing'


# --- render_standing joiner ---


def test_render_standing_body_opening_with_comma_joins_without_space():
    """A constraint body opening with ',' renders with no space after the headline.

    Mutation: joiner fixed to ' ', so '[c01] (c8) **X** , and keep it'
    appears instead of '[c01] (c8) **X**, and keep it'.
    Oracle: hand-written expected line '[c01] (c8) **X**, and keep it'.
    """
    items = [{
        'id': 'c01',
        'prefix': 'c',
        'cycle': '8',
        'headline': 'X',
        'body': ', and keep it',
        }]
    lines = hq.render_standing(items, set(), 'slug', whole_ids={'c01'}).splitlines()
    assert '[c01] (c8) **X**, and keep it' in lines


def test_render_standing_body_opening_with_word_joins_with_space():
    """A constraint body opening with a word renders with a single space.

    Mutation: joiner set to '' for all bodies, so the space is dropped.
    Oracle: hand-written expected line '[c01] (c8) **X** Not even at debug.'.
    """
    items = [{
        'id': 'c01',
        'prefix': 'c',
        'cycle': '8',
        'headline': 'X',
        'body': 'Not even at debug.',
        }]
    lines = hq.render_standing(items, set(), 'slug', whole_ids={'c01'}).splitlines()
    assert '[c01] (c8) **X** Not even at debug.' in lines


def test_render_standing_empty_body_renders_headline_only():
    """A constraint with an empty body renders just the bold headline.

    Mutation: joiner applied to an empty body, adding a trailing space after
    the headline that rstrip does not reach when include_body is True.
    Oracle: hand-written expected line '[c01] (c8) **X**' with nothing after.
    """
    items = [{
        'id': 'c01',
        'prefix': 'c',
        'cycle': '8',
        'headline': 'X',
        'body': '',
        }]
    lines = hq.render_standing(items, set(), 'slug', whole_ids={'c01'}).splitlines()
    assert '[c01] (c8) **X**' in lines
    target = next(ln for ln in lines if '**X**' in ln)
    assert target == '[c01] (c8) **X**'
