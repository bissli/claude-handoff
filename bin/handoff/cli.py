"""The argument parser and main, which dispatches each verb.
"""

import argparse
import os
import pathlib
import shlex

from handoff import help_text, standing, storage, threads
from handoff.verbs import adopt, cycle, finish, open_, query

# The verbs that put a session on a thread, each recorded only once it
# exits 0: a refused begin entered nothing. done clears the record
# instead, and a query against another thread - when, diff, artifacts,
# standing, read - leaves the session where it was.
_THREAD_VERBS = ('adopt', 'begin', 'open')


# The verbs that write inside an open cycle. Each answers to the lock
# begin took, on the same terms begin itself applies: no lock, this
# session's lock, and a lock past two hours all pass.
_CYCLE_WRITE_VERBS = ('note', 'stamp', 'supersede')


def _claude_command(verb: str) -> list[str] | None:
    """Command that hq run and hq loop start claude with.

    Parameters
    ----------
    verb : str
        Verb named in the error line.

    Returns
    -------
    list[str] | None
        HQ_CLAUDE_CMD split as a shell splits it, ``['claude']`` where it
        is unset or blank, or None after printing one error line for a
        value that does not split.
    """
    try:
        return shlex.split(os.environ.get('HQ_CLAUDE_CMD', '')) or ['claude']
    except ValueError as exc:
        print(f'hq {verb}: HQ_CLAUDE_CMD: {exc}')
        return None


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser for every verb.
    """
    _top_epilog = (
        'Every verb but list and help takes the slug first. A slug\n'
        'resolves to an exact folder name under .handoff/, or else to a\n'
        'unique prefix of one.\n'
        'Two flags before the verb, --root DIR and --session ID, override\n'
        'the HQ_ROOT and HQ_SESSION environment values the script\n'
        'otherwise reads. A session never needs them. HQ_CYCLE, HQ_NOW,\n'
        'and HQ_HOST override the cycle, clock, and hostname the same way,\n'
        'from the environment alone. A ~ in --root or HQ_ROOT is expanded.\n'
        'Exit 0 means done. Exit 1 means a refusal or a blocking finding,\n'
        'and nothing is written, except that a refused stamp appends its\n'
        'receipt row. Exit 2 means a usage error. All output goes to\n'
        'stdout, one fact per line.\n'
        'Reference: hq help write | read | anchors | kinds | rules |\n'
        'stale-path | merge.'
    )
    p = argparse.ArgumentParser(
        prog='hq',
        description='Handoff ledger manager',
        epilog=_top_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', help='project root (HQ_ROOT)')
    p.add_argument('--session', help='override session id (HQ_SESSION)')
    sub = p.add_subparsers(dest='verb', required=True)

    _adopt_epilog = (
        'Converts a HANDOFF.md that hq did not write into a ledger thread.\n'
        'The file needs a conforming Written: <date> | Cycle: <N> header;\n'
        'one without it is refused with its headings and the path of\n'
        'reference/adoption.md, and nothing is written. The Decisions,\n'
        'Constraints, and Dead ends sections become standing.md items; each\n'
        'file in the folder, and each Key files bullet that names a path,\n'
        'becomes a ledger row; the file is archived as cycles/c<N>.md, and\n'
        'HANDOFF.md is rewritten in hq form. adopt takes no lock: begin\n'
        'runs it on such a folder by itself, then opens the next cycle. A\n'
        'folder that already holds ledger.tsv, or is marked done, is\n'
        'refused.'
    )
    adp = sub.add_parser(
        'adopt',
        epilog=_adopt_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    adp.add_argument('slug')

    _begin_epilog = (
        'The cycle this opens is the number printed here, never the Cycle\n'
        'field in HANDOFF.md: finish rewrites that field last, so it reads\n'
        'the previous cycle for the whole write. The cycle= field of\n'
        '.hq.lock carries the same number. Every note, stamp, and cursor\n'
        'line this session writes belongs to it.\n'
        "--worked makes the handoff this session's worked thread, so its\n"
        'finish moves Last worked: pass it when the session did the\n'
        "thread's task without hq open. Filing into a thread never passes it."
    )
    beg = sub.add_parser(
        'begin',
        epilog=_begin_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    beg.add_argument('slug')
    beg.add_argument('--force', action='store_true')
    beg.add_argument('--worked', action='store_true')

    _stamp_epilog = (
        'The path is relative to the handoff folder: ./SPEC.md and\n'
        'sub/../SPEC.md are the row SPEC.md. A path outside it - a repo file,\n'
        'a ~ path, an absolute path - is stored whole and gated the same way.\n'
        'A relative token is tried against the folder, then the project root,\n'
        'then a pinned work dir, and stores the form it resolves to; a token\n'
        'none of the three holds exits 2. The cwd and home are never\n'
        'searched. An outside .py/.sql/.js/.ts/.ps1 infers other/never: pass\n'
        '--kind draft to gate it at edit.\n'
        'A live always row needs --where: a stamp that would leave one with\n'
        "no anchor is refused and prints the file's headings.\n"
        '--kind, --read-before, --status, --where, and --label default to the\n'
        "previous row's value; omit them on a re-stamp to carry them forward.\n"
        'A --kind spec or draft on a row at never seeds --read-before from\n'
        'that kind instead.\n'
        '--reason carries only while kind, status, and read_before all hold.\n'
        'A label shorter than its comparand, or missing one of its backticked\n'
        'tokens or s<n> references, draws an advisory on the stamp that wrote\n'
        'it; the row stands. The comparand is the last label an earlier cycle\n'
        'left, or the previous stamp of this cycle where there is no earlier\n'
        'row. Backticks are markup: an identifier the new label still spells\n'
        'plainly is neither a loss nor a shortening.\n'
        'A stamp that gives --where and lets --label carry forward draws a\n'
        'third advisory where the label names an s<n> section the new spans\n'
        'do not cover.\n'
        '--successor P resolves as the path does and sets status=superseded\n'
        'read_before=never unless the stamp says otherwise; --archive sets\n'
        'status=archived read_before=never and requires --reason.\n'
        '--batch reads one stamp per stdin line, the same\n'
        'arguments minus the slug, split like a shell line.\n'
        'See hq help kinds (inference and fields), hq help anchors (--where\n'
        'forms), hq help rules (R1, R2, R3, W1, W2).'
    )
    stmp = sub.add_parser(
        'stamp',
        epilog=_stamp_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    stmp.add_argument('slug')
    stmp.add_argument('path', nargs='?')
    stmp.add_argument(
        '--kind',
        choices=['spec', 'draft', 'notes', 'todo', 'snapshot', 'probe-dir', 'other'])
    stmp.add_argument(
        '--read-before',
        dest='read_before',
        choices=['always', 'edit', 'never'])
    stmp.add_argument(
        '--status',
        choices=['live', 'superseded', 'archived', 'missing'])
    stmp.add_argument('--successor')
    stmp.add_argument('--archive', action='store_true')
    stmp.add_argument('--where')
    stmp.add_argument('--label')
    stmp.add_argument('--reason')
    stmp.add_argument('--batch', action='store_true')

    _note_epilog = (
        'Kinds are decision, constraint, dead-end, evidence. Each item prints\n'
        'the id it was given. --headline is required and one line; the body\n'
        'follows it as the last argument. --batch reads one note per stdin\n'
        'line: <kind> --headline "<h>" <body>, the body taken verbatim to the\n'
        'end of the line, quotes and apostrophes included; a body wrapped\n'
        'whole in one pair of quotes loses the pair.\n'
        'Evidence takes --grade measured|inferred|asserted, required, written\n'
        'at the head of the headline; --open marks a claim not yet settled;\n'
        '--contradicts <id> names a live item it conflicts with. Its body,\n'
        'required, names the source. In a batch line the three flags sit\n'
        'between the kind and --headline.'
    )
    # The kind slot carries no choices: the batch marker '-' lands
    # here on 3.13 and later, and verb_note names an unknown kind.
    nt = sub.add_parser(
        'note',
        epilog=_note_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    nt.add_argument('slug')
    nt.add_argument('note_kind', nargs='?')
    nt.add_argument('body', nargs='?')
    nt.add_argument('--headline', default='')
    nt.add_argument('--batch', action='store_true')
    nt.add_argument('--grade')
    nt.add_argument('--open', action='store_true')
    nt.add_argument('--contradicts')

    _supersede_epilog = (
        'Two ids of one kind, old then new: d17 d23. The old item leaves the\n'
        'rendered Standing block and stays in standing.md. hq standing <slug>\n'
        'lists the ids.\n'
        'One id and --reason "<why>" retires an item of any kind that no\n'
        'longer holds and has no replacement: d17 --reason "the vendor\n'
        'dropped the limit". hq standing <slug> d17 prints it with\n'
        '[retired in cN: <why>].'
    )
    sp = sub.add_parser(
        'supersede',
        epilog=_supersede_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument('slug')
    sp.add_argument('old_id')
    sp.add_argument('new_id', nargs='?', default='')
    sp.add_argument('--reason')

    _finish_epilog = (
        '--log is the one line the Log keeps for this cycle and its whole\n'
        'entry in hq arc; hq help write says what it holds, and a check\n'
        'writes check: <n> findings applied - <what they changed>.\n'
        '--acknowledge "<reason>" turns a W1 or W2 witness break into an\n'
        'acknowledged line and records the break and the reason in the\n'
        'manifest.\n'
        '--replace-directives "<why>" passes a changed ## Directives\n'
        'section and records the old section and the reason in one\n'
        'decision, which supersedes the last such decision; without it\n'
        'a changed section refuses, and with it an unchanged one does.\n'
        'The cursor lines from the last finished cycle that nothing now\n'
        'carries print as an advisory, and the manifest records their\n'
        'count; they never block the write, and the Now step is exempt.\n'
        'hq open <slug> --not-carried lists every such line before finish.\n'
        'After the size line the print carries +N tok since cNN, this\n'
        "cycle's payload against the last one the manifest recorded: a\n"
        'fall shows content being erased, cycle by cycle, while a session\n'
        'can still act on it. It is absent on the first cycle.'
    )
    fin = sub.add_parser(
        'finish',
        epilog=_finish_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    fin.add_argument('slug')
    fin.add_argument('--log', required=True)
    fin.add_argument('--acknowledge')
    fin.add_argument('--replace-directives')

    _done_epilog = (
        'finish ends a cycle and the thread goes on; done ends the thread.\n'
        'hq list stops showing the folder and hq begin refuses it; every\n'
        'read verb still answers, and nothing is deleted. The mark is one\n'
        'file, .hq.done in the folder, holding the slug, the time, the\n'
        'last finished cycle, and the reason.\n'
        '--reason "<line>" records why; whitespace in it collapses to single\n'
        'spaces. --undo removes the mark and the thread runs on. done\n'
        'refuses a folder whose cycle is still open; finish it first.\n'
        'A second done leaves the first mark and prints its date, so the\n'
        'first done date survives a repeat; change the reason by --undo and\n'
        'a fresh done. hq list --done lists the marked folders.'
    )
    dn = sub.add_parser(
        'done',
        epilog=_done_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    dn.add_argument('slug')
    dn.add_argument('--reason')
    dn.add_argument('--undo', action='store_true')

    _open_epilog = (
        '--not-carried lists every cursor line from the last finished\n'
        'cycle that the file on disk, standing.md, a live label, and the\n'
        'stamped siblings no longer carry, and writes nothing.\n'
        "--housekeeping reads the thread without making it the session's\n"
        "worked thread, so its finish keeps the thread's Last worked date."
    )
    op = sub.add_parser(
        'open',
        epilog=_open_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    op.add_argument('slug')
    op.add_argument('--not-carried', action='store_true')
    op.add_argument('--housekeeping', action='store_true')

    _read_epilog = (
        "The row's anchored spans, or the whole file where the row has no\n"
        'anchor, then the receipt the gate credits. Gate credit is keyed on\n'
        'the path, and the receipt is recorded where the read covered the\n'
        "row's required content: a read leaving any required anchor\n"
        'unresolved, or printing a different section, prints the remedy\n'
        'instead.\n'
        '--whole prints the file whole whatever the row anchors. --section\n'
        '<anchor> resolves an anchor of its own instead, in the hq help\n'
        "anchors grammar and joined with ';' for several, whether or not the\n"
        "row's where carries it: the sanctioned read of a section dropped\n"
        'from --where. The two name different reads and are refused\n'
        'together. An anchor matching no heading prints ? unresolved:\n'
        '<anchor>; resolved anchors in the same list still print above it.'
    )
    rd = sub.add_parser(
        'read',
        epilog=_read_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    rd.add_argument('slug')
    rd.add_argument('path')
    rd.add_argument('--whole', action='store_true')
    rd.add_argument('--section', metavar='ANCHOR')

    _when_epilog = (
        'Every ledger row for the path, oldest first, no cap, seven\n'
        'tab-separated columns: path cycle status read_before successor\n'
        'reason label. The path keys as stamp stores it; a relative token\n'
        'the folder does not hold is tried against the project root, then\n'
        'the pinned work dir. A path with no row exits 1.'
    )
    wh = sub.add_parser(
        'when',
        epilog=_when_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    wh.add_argument('slug')
    wh.add_argument('path')

    _diff_epilog = (
        'Cycles are named N, cN, or cNN. With no section, one line per\n'
        'cursor section in file order - "## Now  +3 -1", "## State\n'
        'unchanged", "## Unfiled  added" or "removed" - and no output when\n'
        'the two cursors are identical. A line that moved between sections\n'
        'counts in both. With a section name, matched without regard to\n'
        "case, that section's line diff in file order, '- line' and '+ line'\n"
        'as difflib orders them, no cap; --full prints every section this\n'
        'way. A name matching no section exits 1 and lists the sections.'
    )
    df = sub.add_parser(
        'diff',
        epilog=_diff_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    df.add_argument('slug')
    df.add_argument('c1')
    df.add_argument('c2')
    df.add_argument('section', nargs='?')
    df.add_argument('--full', action='store_true')

    _arc_epilog = (
        'The whole thread in one read: the cycle range and dates, Began as\n'
        "where the first cycle's Task differs, Task, one History line\n"
        "per finished cycle - c<N> <written>  <that cycle's --log> - then\n"
        'Now, the open questions, and the open Plan items of HANDOFF.md,\n'
        'each by its first sentence. A thread adoption began prints its\n'
        'prior Log before the History. No cap: it grows one line a cycle.\n'
        'It writes nothing. A folder with no finished cycle exits 1.'
    )
    arc = sub.add_parser(
        'arc',
        epilog=_arc_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arc.add_argument('slug')

    _artifacts_epilog = (
        'Every live row as a full line - path kind read_before cN label - with\n'
        'no cap, then <name>  <kind>?  unstamped for a file with no row,\n'
        '<name>  conflicted copy for a sync duplicate, and <name>  unstampable\n'
        'for a name holding a tab or newline or an entry that is not a regular\n'
        'file. A row that is not live is counted, not printed: the run closes\n'
        'with superseded N, archived N, or missing N, each naming the --status\n'
        'that prints those rows. A file gone from the disk reads missing here\n'
        'whatever the ledger stored, so a path stamped by an absolute spelling\n'
        'that never arrived is counted under missing.\n'
        "--status selects on that field of the path's current row, never a\n"
        'superseded earlier one, replacing the live-only default. It reports\n'
        'rows alone, the unstamped and unstampable entries dropped for want\n'
        'of a field to select on, and prints hq artifacts: no row has status\n'
        '<status> when none does, at exit 0.'
    )
    art = sub.add_parser(
        'artifacts',
        epilog=_artifacts_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    art.add_argument('slug')
    art.add_argument(
        '--status',
        choices=['live', 'superseded', 'archived', 'missing'])

    _standing_epilog = (
        'Every unsuperseded item in full; --all adds the superseded ones.\n'
        'With ids, the named items in full. A superseded item names the id\n'
        'now current and the cycle that made it current, following the\n'
        'old -> new lines to the end of the chain: [d18] ... [superseded by\n'
        'd21 in c5]. Past one hop the id form adds the chain itself:\n'
        '[superseded by d21 in c5 - d18 -> d20 -> d21]. An id not in\n'
        'standing.md prints hq standing: <id> not in standing.md and exits 1.\n'
        '--grep PATTERN keeps the items whose headline or body matches the\n'
        'pattern, case-insensitively, and exits 2 when the pattern is not a\n'
        'regular expression; --kind constraint|decision|dead-end|evidence\n'
        'keeps one kind; --in-cycle N keeps the items recorded in cycle N.\n'
        'The three compose, and a named id outranks all of them.\n'
        'A retired item prints [retired in cN: <why>].'
    )
    st = sub.add_parser(
        'standing',
        epilog=_standing_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    st.add_argument('slug')
    st.add_argument('ids', nargs='*')
    st.add_argument('--all', action='store_true')
    st.add_argument('--grep')
    st.add_argument('--kind', choices=tuple(standing.KIND_PREFIX))
    st.add_argument('--in-cycle')

    _list_epilog = (
        'One line per folder under .handoff/ holding a HANDOFF.md:\n'
        '<slug>  <last worked>  c<N>  <N> open  <Task line>. Newest first\n'
        'by the Last worked date, else Written, else mtime; then newer\n'
        'mtime; then slug A to Z. A date counts only as YYYY-MM-DD. The\n'
        'date shown is the one it sorts by. <N> open counts the - [ ]\n'
        "items under ## Plan alone, '-' when the Plan has none; a missing\n"
        "cycle shows '-'; an unreadable file shows\n"
        "'-  -  -  unreadable: <reason>'. A bare list shows every one;\n"
        'list 5 the five most recent.\n'
        'A folder marked by hq done is left out, and a closing line counts\n'
        'them: --done lists those folders instead, the open ones left out,\n'
        'and prints no count line. A count caps whichever set is shown.'
    )
    lst = sub.add_parser(
        'list',
        epilog=_list_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    lst.add_argument('count', nargs='?', type=int)
    lst.add_argument('--done', action='store_true')

    _work_dir_epilog = (
        "With the slug alone, prints where the thread's made files go:\n"
        'work dir: .handoff/<slug>/ (default) - the kind folders specs/,\n'
        'drafts/, notes/, outputs/ - or work dir: <dir> (pin) when the pin\n'
        '.handoff/<slug>/work-dir names a directory. With a directory,\n'
        'checks it - already on disk, not the root, not under .handoff/,\n'
        'not under the system temp directory unless the root itself is;\n'
        'inside the root or out - and writes it to the pin, root-relative\n'
        'under the root, else by ~ or absolutely; a refused token writes\n'
        'nothing (exit 1). --clear removes the pin.\n'
        "Pin only when the thread's spec and experiments already live in a\n"
        'project directory, judged at the first begin: the specs, drafts,\n'
        'and outputs the thread makes then go there, stamped by their ~ or\n'
        'absolute path, and notes stay in the folder.')
    wd = sub.add_parser(
        'work-dir',
        epilog=_work_dir_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    wd.add_argument('slug')
    wd.add_argument('dir', nargs='?')
    wd.add_argument('--clear', action='store_true')

    _run_epilog = (
        'Starts claude at the project root on /handoff <slug>. The slug is\n'
        'the exact folder name: a prefix of one exits 2. HQ_CLAUDE_CMD,\n'
        'split as a shell splits it, replaces claude, and /handoff <slug>\n'
        'goes last; a value that does not split exits 2. Where the\n'
        'command is not on PATH, it prints hq run: no <command> on PATH\n'
        'and exits 1.')
    rn = sub.add_parser(
        'run',
        epilog=_run_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    rn.add_argument('slug')

    _loop_epilog = (
        'hq loop <slug> starts claude in the current tmux pane at the\n'
        'project root, with a driver pane below it. The driver types\n'
        '/handoff read <slug>, then a write when the context notice asks\n'
        'for one, /clear, and the read again. It parks and rings when the\n'
        'session asks a question, a write fails to finish, or a thread\n'
        'with a measure probe stalls or meets its finish line. It ends,\n'
        'and closes claude, when the thread is done, after --max-cycles\n'
        'written cycles (default 10), or at hq loop stop <slug>, which has\n'
        'the running loop write the next handoff first. HQ_CLAUDE_CMD\n'
        'replaces claude as it does for hq run, with --settings <file>\n'
        'last in place of /handoff <slug>; a value that does not split\n'
        'exits 2. Needs bash 4 or later, tmux, jq, and the command on\n'
        'PATH.')
    lp = sub.add_parser(
        'loop',
        usage='hq loop [--max-cycles N] slug | hq loop stop slug',
        epilog=_loop_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    lp.add_argument('words', nargs='+', metavar='[stop] slug')
    lp.add_argument('--max-cycles', default='10')

    hlp = sub.add_parser('help')
    hlp.add_argument('topic', nargs='?')

    return p


def main(argv: list[str]) -> int:
    """Entry point callable in-process from tests.

    Parameters
    ----------
    argv : list[str]
        Command-line arguments, excluding the script name.

    Returns
    -------
    int
        0 on success, 1 on a refusal or blocking finding, 2 on bad usage.

    Notes
    -----
    - A bare ``-`` is the batch marker. Python 3.13 and later bind it to
      the kind slot; 3.11 leaves it over. It is a leftover either way and
      never a usage error.
    - Any other leftover is a usage error, except a note body: 3.11
      argparse will not read a positional that follows an option, so the
      body of ``note <slug> decision --headline H <body>`` arrives as a
      leftover there and in the body slot on 3.13.
    """
    parser = build_parser()
    try:
        args, rest = parser.parse_known_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    leftover = (
        [tok for tok in rest if tok != '-'] if getattr(args, 'batch', False)
        else list(rest))
    is_note_body = (
        bool(leftover)
        and getattr(args, 'verb', '') == 'note'
        and not any(tok.startswith('-') and tok != '-' for tok in leftover)
        )
    if leftover and not is_note_body:
        try:
            parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2
        return 2
    if is_note_body:
        args.body = ' '.join([getattr(args, 'body', None) or ''] + leftover).strip()
    try:
        verb = args.verb
        if verb == 'help':
            topic = getattr(args, 'topic', None)
            if topic is None:
                for key, body in help_text.HELP_TOPICS.items():
                    lines = body.splitlines()
                    first_line = lines[0].split(' - ', 1)[1]
                    print(f'hq help {key}: {first_line}')
                return 0
            if topic not in help_text.HELP_TOPICS:
                keys = ', '.join(help_text.HELP_TOPICS)
                print(
                    f'hq help: no topic {topic!r};'
                    f' topics are {keys}')
                return 2
            print(help_text.HELP_TOPICS[topic])
            return 0
        root = storage.resolve_root(args)
        if verb == 'list':
            return query.verb_list(root, args)
        if verb == 'loop':
            words = args.words
            if len(words) == 2 and words[0] == 'stop':
                script_args = words
            elif len(words) == 1:
                command = _claude_command(verb)
                if command is None:
                    return 2
                script_args = ['launch', args.max_cycles, words[0], *command]
            else:
                print('hq loop: takes <slug> or stop <slug>')
                return 2
            script = str(
                pathlib.Path(__file__).resolve().parents[2] / 'bin' / 'hq-loop')
            os.chdir(root)
            os.execv(script, [script, *script_args])
        slug = getattr(args, 'slug', '')
        is_begin = verb == 'begin'
        folder = storage.find_folder(root, slug, missing_ok=is_begin or verb == 'run')
        if verb == 'work-dir':
            return query.verb_work_dir(folder, args)
        if verb == 'run':
            if not folder.is_dir():
                print(
                    f'hq run: no folder named {slug!r} under {folder.parent}'
                    '; run hq list to see what exists')
                return 2
            command = _claude_command(verb)
            if command is None:
                return 2
            os.chdir(root)
            try:
                os.execvp(command[0], [*command, f'/handoff {folder.name}'])
            except FileNotFoundError:
                print(f'hq run: no {command[0]} on PATH')
                return 1
        anch = storage.anchors(folder, args)
        if verb in _CYCLE_WRITE_VERBS:
            refusal = storage.lock_refusal(folder, anch, False, verb)
            if refusal:
                print(refusal)
                return 1
        dispatch = {
            'adopt': adopt.verb_adopt,
            'begin': cycle.verb_begin,
            'stamp': cycle.verb_stamp,
            'note': cycle.verb_note,
            'supersede': cycle.verb_supersede,
            'finish': finish.verb_finish,
            'done': cycle.verb_done,
            'open': open_.verb_open,
            'read': query.verb_read,
            'when': query.verb_when,
            'diff': query.verb_diff,
            'arc': query.verb_arc,
            'artifacts': query.verb_artifacts,
            'standing': query.verb_standing,
            }
        result = dispatch[verb](folder, anch, args)
        if result == 0:
            if verb in _THREAD_VERBS:
                threads.record_thread(str(anch['session']), folder.name)
            if ((verb == 'open' and not getattr(args, 'not_carried', False)
                    and not getattr(args, 'housekeeping', False))
                    or (verb == 'begin' and getattr(args, 'worked', False))):
                threads.record_worked(str(anch['session']), folder)
            elif verb == 'done' and not getattr(args, 'undo', False):
                threads.clear_thread(str(anch['session']), folder.name)
        return result
    except OSError as exc:
        path = exc.filename or '?'
        reason = exc.strerror or type(exc).__name__
        print(f'hq: cannot access {path}: {reason}')
        return 1
