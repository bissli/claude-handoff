"""Contract tests that hold the handoff skill to bin/hq.py.

The skill is prose an agent follows; the script is what it drives. The
corpus the agent can reach is SKILL.md, the reference files beside it,
and the help text hq.py prints on request. Each test pins one seam
between the sides: the verbs and flags each names, the command
examples, the lines the script prints, the help topics, and the example
file.
"""

import argparse
import ast
import pathlib
import re
import shlex

HERE = pathlib.Path(__file__).resolve().parent
BIN = HERE.parent / 'bin'
SKILL_DIR = HERE.parent / 'skills' / 'handoff'
SKILL = SKILL_DIR / 'SKILL.md'
REFERENCE_DIR = SKILL_DIR / 'reference'
EXAMPLE = REFERENCE_DIR / 'example-handoff.md'

from bin import hq

_HQ_CALL = 'hq'
# Flags in the skill that belong to other tools, never to hq.py.
_FOREIGN_FLAGS = {
    '--help', '--no-check', '--oneline', '--porcelain', '--show-toplevel'}
# Exit-2 usage slips whose text is the whole remedy: they name the
# token the parser rejected, on a command that wrote nothing.
_USAGE_ONLY_PRINTS = {
    'hq: HQ_CYCLE must be an integer',
    'hq: HQ_NOW must be an ISO 8601 timestamp',
    'hq: root is not a directory',
    'hq: ambiguous slug',
    'hq stamp: path is required',
    'hq stamp: path may not contain a tab',
    'hq stamp: --reason may not start with',
    'hq stamp: --archive requires --reason',
    'hq stamp: --status archived requires --reason',
    'hq note: kind must be decision, constraint, or dead-end',
    'hq note: --headline is required',
    'hq supersede: ids must share a prefix',
    'hq list: count must be a positive integer',
    'hq help: no topic',
    }
# Lines that state a fact and call for no move, or an internal guard
# the agent never meets.
_TERMINAL_PRINTS = {
    'lacked its trailing newline; restored',
    'hq begin: took over from',
    ', never finished',
    'hq begin: created',
    'dropped from the block:',
    'acknowledged:',
    'advisory: --acknowledge given, no witness broke',
    'hq list: no handoff under',
    'hq: cannot access',
    'hq finish: unknown item kind',
    }
# Claude Code re-attaches a skill after an auto-compaction keeping its
# first 5,000 tokens, about four characters a token. The whole write
# cycle has to sit inside that.
_REATTACH_BUDGET_CHARS = 20_000
# A printed line that carries its own move: ' - ' or '; ' and then the
# move, at least six characters of it.
_TAIL = re.compile(r'(?: - |; )\S.{5,}')


def _norm(text):
    """Collapse whitespace runs so wrapped prose compares as one line."""
    return ' '.join(text.split())


def _parser_surface():
    """Return ({verb: {flags}}, {global flags}, help text) from argparse."""
    parser = hq._build_parser()
    sub = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    verbs = {}
    help_text = [parser.description or '', parser.epilog or '']
    for name, sp in sub.choices.items():
        verbs[name] = {
            opt for act in sp._actions for opt in act.option_strings
            if opt.startswith('--') and opt != '--help'
            }
        help_text += [sp.description or '', sp.epilog or '']
    global_flags = {
        opt for act in parser._actions for opt in act.option_strings
        if opt.startswith('--') and opt != '--help'
        }
    help_text += list(hq.HELP_TOPICS.values())
    return verbs, global_flags, '\n'.join(help_text)


def _corpus():
    """Return the whitespace-normalized text an agent can reach."""
    _, _, help_text = _parser_surface()
    parts = [SKILL.read_text()]
    parts += [p.read_text() for p in sorted(REFERENCE_DIR.glob('*.md'))]
    parts.append(help_text)
    return _norm('\n'.join(parts))


def _fenced_commands(text):
    """Yield each hq command line from fenced blocks, joined across
    backslash continuations and cut before any heredoc marker.

    A ```markdown block holds a rendered HANDOFF.md, whose closing lines
    name a command before their prose, so it yields nothing.
    """
    in_fence = False
    rendered = False
    buf = ''
    for line in text.splitlines():
        if line.startswith('```'):
            in_fence = not in_fence
            rendered = in_fence and line[3:].strip() == 'markdown'
            continue
        if not in_fence or rendered or line.startswith('#'):
            continue
        buf += line.rstrip()
        if buf.endswith('\\'):
            buf = buf[:-1] + ' '
            continue
        cmd, buf = buf, ''
        if cmd.split()[:1] == [_HQ_CALL]:
            yield cmd.split('<<')[0].strip()


def _texts():
    """Return (joined static text, pieces) for every string in hq.py.

    Notes
    -----
    - An f-string yields one entry: its static pieces joined with a
      space, so a prefix and its tail read as one line. Its child
      constants are not yielded again on their own.
    - A plain string yields itself as its one piece.
    """
    tree = ast.parse((BIN / 'hq.py').read_text())
    children = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            children |= {id(v) for v in node.values}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            pieces = [
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            out.append((_norm(' '.join(pieces)), [_norm(p) for p in pieces]))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in children):
            out.append((_norm(node.value), [_norm(node.value)]))
    return out


def _print_calls():
    """Return (lineno, full static text, longest piece) per hq.py print.

    Notes
    -----
    - A print whose argument is a bare name (``print(refusal)``) has no
      static text and is skipped; the lock refusals are pinned by their
      own test. A print whose static text is only a ``hq <verb>:``
      prefix wraps a refusal the refusal test pins, and is skipped too.
    - Pieces under eight characters after stripping are noise (a
      separator, a label) and are not candidates for the longest piece.
    """
    tree = ast.parse((BIN / 'hq.py').read_text())
    calls = []
    for node in ast.walk(tree):
        is_print = (
            isinstance(node, ast.Call)
            and getattr(node.func, 'id', '') == 'print' and node.args)
        if not is_print:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            pieces = [arg.value]
        elif isinstance(arg, ast.JoinedStr):
            pieces = [
                v.value for v in arg.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        else:
            continue
        pieces = [_norm(p) for p in pieces]
        full = _norm(' '.join(pieces))
        if re.fullmatch(r'hq \w+:', full):
            continue
        long_pieces = [p for p in pieces if len(p.strip()) >= 8]
        if long_pieces:
            calls.append((node.lineno, full, max(long_pieces, key=len)))
    return calls


def test_the_skill_and_the_parser_name_the_same_verbs():
    """Every hq.py verb the skill names exists, and every parser verb is named.

    Mutation: a verb renamed in _build_parser, a verb misspelled in the
    skill, or a verb added with no skill text.
    Oracle: the argparse subcommand table.
    """
    verbs, _, _ = _parser_surface()
    named = set(re.findall(r'\bhq (\w[\w-]*)(?![\w:-])', SKILL.read_text()))
    assert named <= set(verbs), named - set(verbs)
    assert set(verbs) <= named, set(verbs) - named


def test_every_verb_but_help_carries_an_epilog():
    """Each verb's --help says what the verb does, not only its usage.

    Mutation: a verb registered by a bare add_parser call, so its --help
    prints the usage line and nothing else.
    Oracle: the argparse subparser table, help alone exempt since it
    prints the topics itself.
    """
    parser = hq._build_parser()
    sub = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    bare = [
        name for name, sp in sub.choices.items()
        if not sp.epilog and name != 'help']
    assert bare == []


def test_every_flag_named_by_either_side_is_known_to_the_other():
    """Flags in the skill exist on some hq.py verb; verb flags appear in
    the corpus.

    Mutation: a flag invented in the skill, or a parser option added with
    no line anywhere the agent can reach saying what it does.
    Oracle: the argparse option strings of every verb, against the skill
    alone in one direction and the whole corpus in the other.
    """
    verbs, global_flags, _ = _parser_surface()
    skill_flags = set(re.findall(r'--[a-z][a-z-]*', SKILL.read_text()))
    skill_flags -= _FOREIGN_FLAGS
    verb_flags = set().union(*verbs.values()) | global_flags
    assert skill_flags <= verb_flags, skill_flags - verb_flags
    corpus_flags = set(re.findall(r'--[a-z][a-z-]*', _corpus()))
    assert verb_flags <= corpus_flags, verb_flags - corpus_flags


def test_every_command_example_in_the_skill_parses():
    """Each fenced hq.py example in the skill or a reference file parses
    as written, and the skill keeps one for each verb of the write path.

    Mutation: an example such as `note <slug> --batch -`, which the parser
    rejects while the kind slot carries choices, or a verb shown with a
    flag it lacks, or the begin/stamp/note/supersede/finish examples
    trimmed out of the skill so the agent composes them unaided.
    Oracle: parse_known_args on the exact tokens after the slug
    placeholder is filled; the only leftover is a note body main() has an
    empty slot for.
    """
    parser = hq._build_parser()
    texts = [SKILL.read_text()] + [
        p.read_text() for p in sorted(REFERENCE_DIR.glob('*.md'))]
    commands = [c for t in texts for c in _fenced_commands(t)]
    skill_verbs = {c.split()[1] for c in _fenced_commands(SKILL.read_text())}
    assert {'begin', 'stamp', 'note', 'supersede', 'finish'} <= skill_verbs
    for cmd in commands:
        argv = shlex.split(cmd.replace('<slug>', 'demo'))[1:]
        try:
            args, rest = parser.parse_known_args(argv)
        except SystemExit as exc:
            raise AssertionError(f'does not parse: {cmd}') from exc
        # Notes:
        # - The bare '-' batch marker binds to a positional on 3.13 and
        #   later and is left over on 3.11; it is not a dropped token.
        # - 3.11 argparse will not read a positional that follows an
        #   option, so a note body arrives as a leftover there. main()
        #   puts it in the body slot, which is why the slot must be free.
        leftover = [tok for tok in rest if tok != '-']
        assert not [tok for tok in leftover if tok.startswith('-')], cmd
        assert not leftover or (
            args.verb == 'note' and args.body is None), cmd


def test_every_message_carries_its_move_or_is_named_in_the_corpus():
    """Each message hq.py prints tells the agent what to do about it: it
    carries a move clause, or the corpus names it, or it is a usage slip
    or a terminal fact on the allowlists.

    Mutation: a print added or reworded with neither a move clause nor a
    line in the skill, a reference file, or the help text; or an
    allowlist entry left behind after its print was reworded or its text
    reached the corpus, which would hide the next such print.
    Oracle: the longest static piece of every print in hq.py's source,
    eight characters or more, against the corpus; the joined static text
    against the tail pattern; the allowlists against both.
    """
    corpus = _corpus()
    calls = _print_calls()
    assert len(calls) >= 40
    allow = _USAGE_ONLY_PRINTS | _TERMINAL_PRINTS
    missing = [
        (line, piece) for line, full, piece in calls
        if piece not in corpus
        and not _TAIL.search(full)
        and not any(full.startswith(u) or u in full for u in allow)]
    assert missing == []
    tailed = [full for _, full, _ in calls if _TAIL.search(full)]
    assert len(tailed) >= 30, len(tailed)
    fulls = [full for _, full, _ in calls]
    dead = [
        u for u in allow
        if not any(full.startswith(u) or u in full for full in fulls)
        or u in corpus]
    assert dead == [], dead
    assert len(_USAGE_ONLY_PRINTS) <= 18
    assert len(_TERMINAL_PRINTS) <= 12


def test_every_message_prefix_the_skill_quotes_is_still_printed():
    """Each `hq <verb>: ...` message the skill quotes opens a line hq.py
    still prints.

    Mutation: a message reworded in hq.py while the skill keeps quoting
    the old text, so the agent waits for a line that never comes.
    Oracle: every backticked span in the skill opening `hq <verb>:`, its
    static prefix before the first placeholder, against the joined static
    text of hq.py's strings.
    """
    constants = [full for full, _ in _texts()]
    quoted = re.findall(r'`(hq \w+: [^`]+)`', _norm(SKILL.read_text()))
    assert len(quoted) >= 2, quoted
    for span in quoted:
        prefix = _norm(re.split(r'<|\.\.\.', span)[0]).rstrip(' :;')
        assert len(prefix) >= 8, span
        assert any(prefix in c for c in constants), span


def test_every_pointer_line_the_script_renders_is_named_in_the_skill():
    """Each `- hq <verb> ...` pointer hq.py renders names a real verb the
    skill shows verbatim.

    Mutation: a pointer's verb renamed in a rendered block or the begin
    work list, with the skill still showing the old word, so the agent is
    told to type a verb the file never names; or a pointer built from a
    word that is no parser verb at all.
    Oracle: every string constant in hq.py holding `- hq <word>`, checked
    against the argparse verb table and the whitespace-normalized skill.
    """
    verbs, _, _ = _parser_surface()
    skill_text = _norm(SKILL.read_text())
    pointers = []
    for _, pieces in _texts():
        for piece in pieces:
            pointers += re.findall(r'- hq (\w+)', piece)
    assert len(pointers) >= 4
    assert set(pointers) <= set(verbs), set(pointers) - set(verbs)
    assert [v for v in pointers if f'- hq {v} ' not in skill_text] == []


def test_every_refusal_string_in_the_script_carries_its_move():
    """Each refusal text hq.py builds carries a move clause or appears in
    the corpus.

    Mutation: a refusal reworded in code with its move clause dropped
    and no corpus line taking over, including the three R1 texts whose
    static prefix is under eight characters and the two Unfiled
    refusals drain_unfiled returns.
    Oracle: the joined static text of every string opening `refused: `,
    `R1: `, `untyped Unfiled bullet`, or `Unfiled bullet has no
    headline`, against the tail pattern and the corpus.
    """
    corpus = _corpus()
    prefixes = (
        'refused: ', 'R1: ', 'untyped Unfiled bullet',
        'Unfiled bullet has no headline')
    builders = [
        (full, max((p for p in pieces if len(p.strip()) >= 8), key=len, default=''))
        for full, pieces in _texts() if full.startswith(prefixes)]
    assert len(builders) >= 7, builders
    # Notes:
    # - The R1 reasons are printed through `hq stamp: {reason}` with the
    #   move clause at the print site, so the builder's own text has to
    #   be in the corpus: hq help rules lists the refusal lines.
    missing = [
        full for full, longest in builders
        if not _TAIL.search(full) and longest not in corpus]
    assert missing == [], missing


def test_the_lock_stop_stays_in_the_skill():
    """The two lock refusals, and the --force clause, are quoted in the
    skill itself.

    Mutation: the user-confirmation rule trimmed out of the skill during
    a length pass. The printed line ends `use --force to take over` with
    no condition, so an agent acting on the line alone takes over a live
    session's cycle; the restraint has to precede the line, and these
    prints pass a name, so the print test never sees them.
    Oracle: every hq.py string piece holding `--force`, against the
    whitespace-normalized skill.
    """
    skill_text = _norm(SKILL.read_text())
    pieces = [
        p for full, parts in _texts() if len(full) <= 100
        for p in parts if 'use --force' in p]
    assert len(pieces) >= 2, pieces
    assert [p for p in pieces if p.strip() not in skill_text] == []
    assert 'hq begin: lock file unreadable' in skill_text
    assert 'hq begin: lock held by' in skill_text


def test_every_reviewer_seat_names_the_host_tier_before_a_literal_model():
    """Each Reviewer-pass seat names the host's own tier, then a literal model.

    Mutation: a seat's host-tier clause dropped, so a host whose agent
    rules pin tiers leaves the agent with one literal type or model and
    no alternative it may use.
    Oracle: the skill's Skeptic bullet - it carries the word 'host'
    before its literal `model` name.
    """
    text = _norm(SKILL.read_text())
    seats = re.findall(r'- (Skeptic) \((.*?)\)', text)
    assert [name for name, _ in seats] == ['Skeptic'], seats
    for name, seat in seats:
        assert 'host' in seat and 'model `' in seat, (name, seat)
        assert seat.index('host') < seat.index('model `'), (name, seat)


def test_every_help_topic_named_anywhere_exists_and_the_skill_names_each():
    """Each `hq help <topic>` the skill, a reference file, or hq.py names
    is a topic the verb serves, and the skill names every topic.

    Mutation: a pointer at a topic that does not exist, or a topic added
    to HELP_TOPICS that no skill line sends the agent to.
    Oracle: the keys of hq.HELP_TOPICS.
    """
    texts = [SKILL.read_text()] + [
        p.read_text() for p in sorted(REFERENCE_DIR.glob('*.md'))]
    texts += [full for full, _ in _texts()]
    named = set()
    for text in texts:
        named |= set(re.findall(r'hq help ([a-z][a-z-]*)', text))
    topics = set(hq.HELP_TOPICS)
    assert named <= topics, named - topics
    in_skill = set(re.findall(r'hq help ([a-z][a-z-]*)', SKILL.read_text()))
    assert topics <= in_skill, topics - in_skill
    for topic, body in hq.HELP_TOPICS.items():
        assert body.startswith(f'hq help {topic} - '), topic


def test_every_reference_file_is_named_by_the_skill_and_exists():
    """Each file under reference/ is named in the skill, and each
    `reference/<name>.md` the skill or hq.py names is on disk.

    Mutation: a reference file added that no skill line points at, a
    pointer left behind after its file was renamed, or the adopt message
    naming a file that is not shipped.
    Oracle: the directory listing against the names in the skill and in
    hq.py's strings.
    """
    on_disk = {p.name for p in REFERENCE_DIR.glob('*.md')}
    assert on_disk, REFERENCE_DIR
    skill_text = SKILL.read_text()
    named = set(re.findall(r'reference/([\w-]+\.md)', skill_text))
    fulls = [full for full, _ in _texts()]
    for value in fulls:
        named |= set(re.findall(r'reference/([\w-]+\.md)', value))
    assert on_disk <= named, on_disk - named
    assert named <= on_disk, named - on_disk
    assert any('reference/adoption.md' in full for full in fulls)


def test_the_anchor_topic_matches_the_resolver():
    """Every heading-to-anchor form the anchors topic advertises resolves
    to that heading.

    Mutation: a form dropped from resolve_where - the letter-led id, the
    dotted sub-heading, the `s<n>` prefix - while the topic still
    advertises it, so the agent types an anchor that lands on `?`.
    Oracle: resolve_where run on a one-heading fixture built from each
    table row of hq.HELP_TOPICS['anchors'].
    """
    body = hq.HELP_TOPICS['anchors']
    rows = []
    in_table = False
    for line in body.splitlines():
        if line.strip().startswith('heading') and 'accepted anchors' in line:
            in_table = True
            continue
        if in_table and not line.strip():
            break
        if not in_table:
            continue
        cells = re.split(r'\s{2,}', line.strip())
        if cells[0].startswith('#'):
            rows.append([cells[0], ' '.join(cells[1:])])
        elif rows:
            rows[-1][1] += ' ' + ' '.join(cells)
    assert len(rows) >= 6, rows
    for heading, forms in rows:
        fixture = f'{heading}\n\nbody\n'
        for form in forms.split('|'):
            form = re.sub(r'\s*\(.*?\)\s*', '', form).strip()
            spans, unresolved = hq.resolve_where(fixture, [form])
            assert spans and not unresolved, (heading, form, unresolved)
            assert spans[0][0] == 1, (heading, form, spans)


def test_the_skill_example_cursor_matches_the_reference_example():
    """The skill's example cursor is byte-identical to the cursor of the
    full rendered example in reference/example-handoff.md.

    Mutation: a section renamed or a line edited in one file only, so the
    template the agent writes from drifts from the file the golden test
    replays.
    Oracle: the slice from `# Handoff: auth-token-refresh` to the first
    `<!-- hq:read` marker in each file.
    """
    def _cursor(text):
        start = text.index('# Handoff: auth-token-refresh')
        return text[start:text.index('<!-- hq:read', start)]
    skill_cursor = _cursor(SKILL.read_text())
    assert '## Task' in skill_cursor
    assert '## Open questions' in skill_cursor
    assert skill_cursor == _cursor(EXAMPLE.read_text())


def test_the_write_cycle_fits_the_reattach_budget():
    """The skill fits whole inside the re-attach cut.

    Mutation: a section regrown past the budget, so a compacted session
    loses whatever falls beyond the cut; or the reviewer pass, the lock
    stop, or the report dropped from the skill.
    Oracle: the file length against the budget constant, and each
    required literal present in the file.
    """
    text = SKILL.read_text()
    for literal in (
            'hq finish', 'Guess neither', 'never opens', 'Skeptic (',
            '\n### Report\n'):
        assert literal in text, literal
    assert len(text) <= _REATTACH_BUDGET_CHARS, len(text)
