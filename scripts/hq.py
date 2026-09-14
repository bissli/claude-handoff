#!/usr/bin/env python3
"""Handoff ledger script: fifteen verbs for managing per-project handoff files.

Each handoff lives in .handoff/<slug>/: HANDOFF.md written by the agent,
ledger.tsv stamping every artifact, and standing.md for decisions,
constraints, and dead ends. Three rendered blocks keep the payload bounded
across cycles, because each block shows live counts and unsuperseded items,
not history.

Invoke as::

    hq <verb> <slug> [args] [options]

``bin/hq`` is a wrapper the plugin puts on the agent's PATH; it runs
``python3 scripts/hq.py`` with the same arguments. Or call ``main(argv)``
in-process. Exit 0 on success, 1 on a refusal or a
blocking finding, 2 on bad usage or an unresolvable slug.

Notes
-----
- Every rule and rendering is a pure function importable from ``hq``.
- All impure inputs enter through ``anchors()``.
- ``finish`` writes nothing until every check passes; a failed run appends
  nothing to the ledger or standing.md.
- ``ledger.tsv`` and ``standing.md`` are append-only; ``witness()`` detects
  any edit to a previously recorded line via a byte-prefix sha check.
"""

import argparse
import contextlib
import datetime
import difflib
import fnmatch
import hashlib
import io
import os
import pathlib
import re
import shlex
import socket
import subprocess
import sys
import tempfile

Row = dict[str, str]

LEDGER_FIELDS = [
    'cycle', 'ts', 'path', 'base', 'kind', 'status', 'read_before',
    'successor', 'where', 'sha12', 'lines', 'reason', 'label',
    ]
# rewrite_sha trails note so a manifest written before the column
# existed still reads by position; _read_tsv pads it to `-`.
MANIFEST_FIELDS = [
    'cycle', 'written', 'session', 'repos', 'cursor_lines', 'payload_tokens',
    'handoff_sha', 'ledger_bytes', 'ledger_sha', 'standing_bytes',
    'standing_sha', 'log', 'note', 'rewrite_sha',
    ]

_LEDGER_HEADER = '\t'.join(LEDGER_FIELDS)
_MANIFEST_HEADER = '\t'.join(MANIFEST_FIELDS)
_SKIP_NAMES = {
    'HANDOFF.md', 'ledger.tsv', 'standing.md', 'cycles', '.hq.lock', 'work-dir'}
HANDOFF_DIRNAME = '.handoff'
# The directories earlier plugin versions kept the folder under; a
# path written under one of them names where a folder used to be.
_FORMER_HANDOFF_DIRNAMES = ('working', 'scratch')
_NOTE_BATCH_LINE = re.compile(
    r'^(?P<kind>decision|constraint|dead-end)\s+--headline\s+'
    r'(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|(?P<bare>[^"\'\s]\S*))'
    r'(?P<body>.*)$')
_HEADER_PAT = re.compile(r'Written:\s*.+?\s*\|\s*Cycle:\s*(\d+)')
# Notes:
# - A grading label under Key files may be written as its own heading;
#   there it is structure and stays in the section rather than opening
#   one, for the adopt parser and for conservation alike.
# - A label may carry a clause, `Read now, under x/ unless noted:`, and
#   is structure when the whole line ends with a colon.
_KF_LABEL_PAT = re.compile(
    r'^\s*(?:[-*+]\s+|#+\s*)?(read now|reference only)\b(?:[^\n]*:)?\s*$',
    re.IGNORECASE)
_SNAPSHOT_PATS = ['*.pre-*', '*.prev.*', '*.orig.*', '*.bak']
_SPEC_PATS = ['SPEC*', 'DESIGN*', 'PROPOSAL*', '*-DECLARATION*']
_DRAFT_EXTS = {'.py', '.sql', '.js', '.ts', '.ps1'}
_WORK_PIN_NAME = 'work-dir'
_TEMP_DIRS = (
    pathlib.Path(os.path.abspath(tempfile.gettempdir())), pathlib.Path('/tmp'))
_KIND_DIRS = {'specs': 'spec', 'drafts': 'draft', 'notes': 'notes', 'outputs': 'other'}
_GATE_RB = {'always', 'edit'}
_FULL_RB = {'always', 'edit', 'mention'}
# The tier a gated kind seeds: a spec is the contract, read at every
# resume; a draft is read at the write that changes it.
_TIER_SEED = {'spec': 'always', 'draft': 'edit'}
_TWO_HOURS = 7200
HELP_TOPICS: dict[str, str] = {
    'anchors': """\
hq help anchors - the --where grammar

--where names one or more headings of the stamped file. Several
anchors join with ';' (a ';' inside a heading's text is written
'\\;'). Each anchor is the heading's text without its number, or
s<n> for the heading numbered <n>.

  heading                        accepted anchors
  ## s4: Field-to-path mapping   s4 | s4: Field-to-path mapping |
                                 Field-to-path mapping
  ## 4. Cache warmup             s4 | Cache warmup
  # 11b. Proof                   s11b | 11b. Proof | Proof
  #### 2a - Basis                s2a | 2a - Basis | Basis
  ## F65. Long title             F65 | f65 | sF65 | Long title
  ### 24.4 The decision          s24.4 (s24 names '## 24. Parent')

- '4.', '4:', '4 -', 's4.', and 's4:' all count as the number 4; a
  bare 'S4 ...' is a word, not a number.
- A number may carry one letter when a '.', ':', or ' - ' follows
  it: s11b names '# 11b. Proof', s11 does not; a bare '3D ...' or
  '2a-b ...' is a word.
- A dotted sub-heading takes its whole number: s24.4 names
  '### 24.4 The decision', s24 names '## 24. Parent'.
- The title alone lands on the first heading of that text, so a
  repeated title is named by its number.
- A letter-led id of one or two letters ('## F65. Title', '## Q3:
  Title') is named by that id - F65, f65, or sF65 - and F7 never
  lands on '## 7. Seven'; a longer word such as 'Log4j:' is title
  text.
- In the rendered read block, SPEC.md:11-13  (320 tok) is where the
  anchor resolves today and what the span costs; a row with no
  anchor, which only an older cycle can leave, shows (N lines, T tok)
  and the whole file is the read; SPEC.md:? means the anchor matches
  no heading - read the whole file, then re-stamp with a --where that
  resolves and re-run hq open. A ? that survives means the anchor is
  still wrong.""",
    'kinds': """\
hq help kinds - how stamp infers kind and read_before

Thirteen fields per ledger row: cycle ts path base kind status
read_before successor where sha12 lines reason label.

Kind inference, first match wins:

  name or shape                                        kind       read_before
  contains 'conflicted copy'; a tab or newline in      skip       -
    the name; not a regular file or directory
  *.pre-*, *.prev.*, *.orig.*, *.bak                   snapshot   never
  HANDOFF*.md at the folder's top level                snapshot   never
  name contains cycle<digits>                          snapshot   never
  a directory                                          probe-dir  never
  a file one level under specs/                        spec       always
  a file one level under drafts/                       draft      edit
  a file one level under notes/                        notes      never
  a file one level under outputs/                      other      never
  SPEC*, DESIGN*, PROPOSAL*, *-DECLARATION*            spec       always
  notes-*, REVIEW*                                     notes      never
  todo*, TODO*                                         todo       never
  first heading starts Spec/Design, any level          spec       always
  *.py *.sql *.js *.ts *.ps1 at the folder's top level draft      edit
  anything else, a nested or outside file included     other      never

read_before is a tier - when the file is loaded, and what belongs there:

  grade    means                      loaded when
  always   the contract               every resume, Read first
  edit     read before you change it  the gate, at a write to it
  mention  know it exists             on demand, hq read
  never    on the record              hq artifacts, hq when

- always holds spec sections and anchored notes; edit holds drafts,
  code, tests, and templates; mention holds notes, evidence, and
  reviews; never holds outputs, snapshots, and superseded rows.
- always requires an anchor: a stamp that would leave a live always
  row with no --where is refused, new row and re-stamp alike, whatever
  the kind, and the receipt row records it; the file's headings print
  under the refusal, one per line. A spec needed whole is anchored at
  its title heading, and its size prints beside it in the Read first
  block. A file with no headings cannot be always.
- The kind folder sets the kind of a file one level under it, whatever
  the name but a snapshot-shaped one (*.bak, *.orig.*, *.prev.*,
  *.pre-*, cycle<N>), which stays a snapshot; probes/, like any other
  directory, is one probe-dir entry, stamped as a unit or recorded by
  the rows of its files. drafts/ holds the candidate that will land,
  gated edit; a throwaway script goes to probes/. A thread pinned
  with hq work-dir keeps the specs, drafts, and outputs it makes in
  that directory instead (hq work-dir --help).
- Outside the folder only the draft rule lapses: a SPEC*-shaped name
  or a Spec/Design first heading still infers spec; any other outside
  file infers other/never - pass --kind spec to gate it always or
  --kind draft to gate it edit; the writer grades a note the cursor
  points at mention or edit.
- When a stem (SPEC) has several members, adopt and begin gate only
  the newest spec-kind file; every older stem-mate is stamped
  superseded/never pointing at the newest.
- --kind takes spec, draft, notes, todo, snapshot, probe-dir, or
  other; --read-before always, edit, mention, or never; --status
  live, superseded, archived, or missing.
- --kind, --read-before, --status, --where, and --label default to
  the previous row's value; --reason carries only while kind, status,
  and read_before all hold.
- --successor P sets status=superseded read_before=never unless the
  stamp says otherwise. --archive sets status=archived
  read_before=never and requires --reason. --defer writes kind=other
  read_before=never reason=deferred so the file reappears in the next
  work list; it is refused for a spec or draft.
- Read first block: one line per live always row with the size of
  what hq read prints - specs/SPEC.md:11-13  (320 tok)  label for an
  anchored row, notes/x.md  (186 lines, 8.2k tok)  label for a whole
  file an older cycle left; a file that is not UTF-8 text shows bytes,
  (35 KB), and a directory (N files). Tokens are len(text) // 4.
- Artifacts block: rows with read_before in {always, edit, mention}
  print in full, no cap; read_before=never rows collapse to counts by
  kind; rows no longer live collapse to superseded/archived/missing
  counts. In both blocks an absolute path under the root prints
  relative to it and one under the pinned work dir relative to the
  pin, and the block's first line names each base in use - root
  ~/code/proj; work dir ~/code/proj-wt. The ledger and hq artifacts
  keep the path as stamped.
- Standing block: constraints print in full, decisions and dead ends
  as headlines, no cap; hq standing <slug> <id> prints an item in
  full. Ids are d decision, c constraint, x dead end; (cN) is the
  cycle that recorded the item.""",
    'rules': """\
hq help rules - what the script refuses and why

R1  A row whose inferred kind is spec or draft, or whose stored or
    --kind kind is, keeps its tier - a spec read_before always, a
    draft edit or always - and may not leave live or change kind,
    unless its successor - passed or carried forward - names a file
    on disk other than itself, or --archive --reason is given
    (--status archived --reason is the same). An explicit --successor
    whose own current row is not live is refused, so two specs cannot
    name each other; a successor with no row yet is accepted and the
    next begin lists it as unstamped. A path that has ever been spec
    or draft stays gated: its way back to live is always for a spec
    and edit for a draft. The refusal lines, each printed after
    'hq stamp: ':
      refused: R1: spec read_before must stay always without --successor or --archive
      refused: R1: draft read_before must stay edit or always without --successor or --archive
      refused: R1: <kind> status must stay live without --successor or --archive
      refused: R1: <kind> kind must stay spec or draft without --successor or --archive
      refused: R1: successor <path> is <status>, not live
      refused: --defer not allowed for inferred <kind>
    A live always row needs an anchor, whatever its kind: a stamp
    that would leave where at '-' is refused, new row and re-stamp
    alike, and the file's headings print under the line:
      refused: <path> always with no anchor - N lines read whole at every
        resume; stamp --where <heading> or --read-before edit
    begin lists every live always row with no anchor the same way.
R2  A refused stamp still appends a row: the previous fields with
    reason set to 'refused: <why>'; with no previous row, the kind
    table's seed and the file's sha and line count. The attempt is
    in the record and clears nothing.
R3  A live row with read_before in {always, edit} whose file sha
    differs from sha12 blocks finish until the agent re-stamps it. A
    re-stamp alone clears R3.
W1, W2  finish and open check that ledger.tsv and standing.md are
    byte-prefix-identical to their last finished state; an edited
    recorded line is a hard fail in finish. When a known tool caused
    the break (a formatter, a merge), pass --acknowledge "<reason>"
    to finish; the reason lands in the manifest.""",
    'stale-path': """\
hq help stale-path - a folder path under a former directory

'stale folder path in <file>: <dir>/<slug>/ x<n>' means the cursor or
an unsuperseded standing item names the folder under a directory an
earlier plugin version used (working/ or scratch/), so the text
points at where the folder was.

- Run grep -rl '<dir>/<slug>/' over the repo to find every carrier.
- At the next write, inside a cycle, correct the path in each cursor
  line that carries it, and re-note plus supersede each standing
  item that does; the count clears.
- Edit the path alone, never a quoted sentence, and never HANDOFF.md
  outside a cycle: a rewrite outside one trips block sha mismatch at
  open, and begin archives the file as c<NN>.hand.md.""",
    }
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
_DEFAULT_STATE = pathlib.Path.home() / '.claude' / 'cache' / 'claude-handoff'


# ----------------------------------------------------------------------
# Pure functions
# ----------------------------------------------------------------------


def anchors(folder: pathlib.Path, argv: argparse.Namespace) -> dict:
    """Return environment fields for the current invocation.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; read to determine the current cycle from the manifest.
    argv : argparse.Namespace
        Parsed flags; each overrides its ``HQ_*`` environment fallback.

    Returns
    -------
    dict
        Keys: ``cycle`` (int), ``now`` (str), ``session`` (str), ``host``
        (str), ``branch`` (str), ``sha`` (str), ``dirty`` (list[str]),
        ``root`` (Path).

    Notes
    -----
    - Precedence: argv flag > ``HQ_*`` env var > git/socket/default.
    - ``HQ_GIT=0`` disables git calls; branch and sha become ``'-'``.
    - cycle is last manifest row's cycle + 1, or 1 with no manifest rows.
    - A cycle or timestamp that does not parse exits before any verb runs:
      2 for the flag or env value, 1 for a manifest row.
    """
    root = _resolve_root(argv)
    cycle_raw = getattr(argv, 'cycle', None) or os.environ.get('HQ_CYCLE')
    if cycle_raw:
        if not re.fullmatch(r'\d+', str(cycle_raw).strip()):
            print('hq: --cycle must be an integer')
            sys.exit(2)
        cycle = int(cycle_raw)
    else:
        manifest = _read_tsv(folder / 'cycles' / 'manifest.tsv', MANIFEST_FIELDS)
        for idx, row in enumerate(manifest, start=1):
            if not re.fullmatch(r'\d+', row['cycle'].strip()):
                print(
                    f'hq: cycles/manifest.tsv row {idx} has a'
                    ' non-integer cycle - a hand-edited manifest; restore the integer')
                sys.exit(1)
        cycle = (int(manifest[-1]['cycle']) + 1) if manifest else 1
    now = (
        getattr(argv, 'now', None)
        or os.environ.get('HQ_NOW')
        or datetime.datetime.now().isoformat(timespec='seconds')
    )
    try:
        datetime.datetime.fromisoformat(now)
    except ValueError:
        print('hq: --now must be an ISO 8601 timestamp')
        sys.exit(2)
    session = (
        getattr(argv, 'session', None)
        or os.environ.get('HQ_SESSION')
        or os.environ.get('CLAUDE_CODE_SESSION_ID', 'unknown')
    )
    host = (
        getattr(argv, 'host', None)
        or os.environ.get('HQ_HOST')
        or socket.gethostname()
    )
    branch, sha, dirty = '-', '-', []
    if os.environ.get('HQ_GIT', '1') != '0':
        branch, sha, dirty = _git_state(root)
    return {
        'cycle': cycle, 'now': now, 'session': session, 'host': host,
        'branch': branch, 'sha': sha, 'dirty': dirty, 'root': root,
        }


def infer_kind(
    name: str,
    is_dir: bool,
    first_heading: str,
    top_level: bool = True,
    kind_dir: str = '',
) -> tuple[str, str]:
    """Infer the ledger kind and seeded read_before for one file or directory.

    Parameters
    ----------
    name : str
        File or directory basename.
    is_dir : bool
        True when the entry is a directory.
    first_heading : str
        The first ``#`` heading line from the file; empty when unread.
    top_level : bool, default True
        True for a top-level entry of the handoff folder. The draft rule
        applies only there; a nested or outside ``.py`` is ``other``.
    kind_dir : str, default ''
        The kind folder the entry sits one level under - a key of
        ``_KIND_DIRS`` - or empty. The folder sets the kind of a file,
        whatever its name; a directory there is still ``probe-dir``.

    Returns
    -------
    tuple[str, str]
        ``(kind, read_before)``. Returns ``('skip', '-')`` for a conflicted
        copy.

    Notes
    -----
    - First match wins; order follows the section 4 table.
    - ``cycle<digits>`` uses regex; all other patterns use ``fnmatchcase``.
    - Draft detection applies at the top level, per ``top_level``.
    - A spec seeds ``always`` and a draft ``edit``, per ``_TIER_SEED``;
      every other kind seeds ``never``.
    - ``HANDOFF*.md`` is snapshot only at the top level; a nested or
      outside ``HANDOFF.md`` is not this folder's handoff and falls
      through to ``other``.
    """
    if 'conflicted copy' in name:
        return ('skip', '-')
    for pat in _SNAPSHOT_PATS:
        if fnmatch.fnmatchcase(name, pat):
            return ('snapshot', 'never')
    if top_level and fnmatch.fnmatchcase(name, 'HANDOFF*.md'):
        return ('snapshot', 'never')
    if re.search(r'cycle\d+', name):
        return ('snapshot', 'never')
    if is_dir:
        return ('probe-dir', 'never')
    if kind_dir:
        kind = _KIND_DIRS[kind_dir]
        return (kind, _TIER_SEED.get(kind, 'never'))
    for pat in _SPEC_PATS:
        if fnmatch.fnmatchcase(name, pat):
            return ('spec', 'always')
    # An authored name prefix outranks the first heading: a heading is a
    # guess over prose, and a spec grade it gets wrong is gated for good
    # under R1.
    if fnmatch.fnmatchcase(name, 'notes-*') or fnmatch.fnmatchcase(name, 'REVIEW*'):
        return ('notes', 'never')
    if fnmatch.fnmatchcase(name, 'todo*') or fnmatch.fnmatchcase(name, 'TODO*'):
        return ('todo', 'never')
    if re.match(r'^#+ *(Spec|Design)\b', first_heading):
        return ('spec', 'always')
    if top_level and pathlib.Path(name).suffix in _DRAFT_EXTS:
        return ('draft', 'edit')
    return ('other', 'never')


def is_recorded(name: str, live: dict[str, Row]) -> bool:
    """Return True when a walk entry has a row, or a row names a file in it.

    Parameters
    ----------
    name : str
        A walk entry: a top-level name or ``<kind folder>/<name>``.
    live : dict[str, Row]
        Latest ledger row per path.

    Returns
    -------
    bool
        True for a row under the entry's own path, or for a directory
        whose files carry rows of their own, which records the directory
        file by file.
    """
    return name in live or any(path.startswith(name + '/') for path in live)


def kind_dir_of(stored_path: str, base: str) -> str:
    """Return the kind folder a stored path sits one level under, else ''.

    Parameters
    ----------
    stored_path : str
        The path as the ledger stores it.
    base : str
        ``folder`` or ``abs``; only a folder-relative path can sit under a
        kind folder.

    Returns
    -------
    str
        A key of ``_KIND_DIRS`` when the path is ``<kind folder>/<name>``
        with no deeper segment; otherwise ``''``.
    """
    head, sep, tail = stored_path.partition('/')
    if base != 'folder' or not sep or '/' in tail or head not in _KIND_DIRS:
        return ''
    return head


def apply_stem_rule(entries: list[tuple[str, str, float]]) -> dict[str, str]:
    """Mark older spec stem-mates as superseded by the newest member.

    Parameters
    ----------
    entries : list[tuple[str, str, float]]
        Each entry is ``(name, kind, mtime)`` for one walk entry.

    Returns
    -------
    dict[str, str]
        Maps each older stem-mate's name to its successor's name.
        Only stems that contain at least one ``'spec'`` entry are processed;
        every member of such a stem older than the newest spec is included,
        whatever its kind; a newer or same-age member is left alone.
    """
    by_stem: dict[str, list[tuple[str, str, float]]] = {}
    stems_with_spec: set[str] = set()
    for name, kind, mtime in entries:
        stem = name.split('.')[0]
        by_stem.setdefault(stem, []).append((name, kind, mtime))
        if kind == 'spec':
            stems_with_spec.add(stem)
    result: dict[str, str] = {}
    for stem, mates in by_stem.items():
        if stem not in stems_with_spec:
            continue
        if len(mates) < 2:
            continue
        spec_mates = [(n, mt) for n, k, mt in mates if k == 'spec']
        if not spec_mates:
            continue
        spec_mates.sort(key=lambda x: x[1], reverse=True)
        newest, newest_mtime = spec_mates[0]
        for name, kind, mtime in mates:
            if name != newest and mtime < newest_mtime:
                result[name] = newest
    return result


def check_r1(
    new_row: Row,
    gate_kind: str,
    successor_on_disk: bool,
) -> str | None:
    """Check R1: a gated artifact may not leave its tier without a successor.

    Parameters
    ----------
    new_row : Row
        The row as it would be written.
    gate_kind : str
        The kind R1 binds to: the kind inferred from the file name and
        content, or spec or draft when ``--kind`` or any earlier row of
        the path carries one and the inferred kind does not.
    successor_on_disk : bool
        True when the resulting row's successor names a file on disk other
        than the row's own path.

    Returns
    -------
    str | None
        Refusal text, or ``None`` when the stamp is allowed.

    Notes
    -----
    - A spec keeps ``read_before`` ``always``; a draft keeps ``edit`` or
      ``always``; both keep ``live`` and their kind. A path once spec or
      draft finds its way back to ``live`` at that same tier.
    """
    if gate_kind not in {'spec', 'draft'}:
        return None
    if (new_row.get('status') == 'archived'
            and new_row.get('reason', '-') not in {'-', ''}):
        return None
    if successor_on_disk:
        return None
    kept = 'always' if gate_kind == 'spec' else 'edit or always'
    if new_row.get('read_before', 'always') not in kept.split(' or '):
        return f'R1: {gate_kind} read_before must stay {kept} without --successor or --archive'
    if new_row.get('status', 'live') != 'live':
        return f'R1: {gate_kind} status must stay live without --successor or --archive'
    if new_row.get('kind', gate_kind) not in {'spec', 'draft'}:
        return f'R1: {gate_kind} kind must stay spec or draft without --successor or --archive'
    return None


def check_r3(rows: list[Row], sha_by_path: dict[str, str]) -> list[Row]:
    """Return live gated rows whose stored sha12 no longer matches the file.

    Parameters
    ----------
    rows : list[Row]
        All ledger rows in order.
    sha_by_path : dict[str, str]
        Current sha12 (first 12 hex of sha256) keyed by relative path.

    Returns
    -------
    list[Row]
        Live rows with ``read_before`` in {always, edit} where the file's
        current sha differs from the stored ``sha12``.

    Notes
    -----
    - A current sha of ``-`` means the file could not be hashed - a
      directory, or a file the process cannot read - and is not a move.
    """
    live = latest_rows(rows)
    result = []
    for path, row in live.items():
        if row['status'] != 'live' or row['read_before'] not in _GATE_RB:
            continue
        current = sha_by_path.get(path, '')
        if current and current != '-' and row['sha12'] not in {'-', current}:
            result.append(row)
    return result


def witness(
    prev_manifest: dict | None,
    ledger_bytes: bytes,
    standing_bytes: bytes,
) -> list[str]:
    """Detect any edit to a recorded line in the append-only files.

    Parameters
    ----------
    prev_manifest : dict | None
        The last manifest row, or ``None`` when no cycle has finished.
    ledger_bytes : bytes
        Current raw bytes of ``ledger.tsv``.
    standing_bytes : bytes
        Current raw bytes of ``standing.md``.

    Returns
    -------
    list[str]
        Each entry is a ``'W1 ...'`` or ``'W2 ...'`` break description.

    Notes
    -----
    - Computes sha256 of the first N bytes of each file (N from the
      manifest row) and compares against the sha stored at the last finish.
    - A pure append leaves the first N bytes unchanged and passes.
    - An in-line deletion, a clause rewrite, or a deletion masked by an
      append all fail.
    """
    if prev_manifest is None:
        return []
    breaks = []
    for code, name, data in (
        ('W1', 'ledger', ledger_bytes),
        ('W2', 'standing', standing_bytes),
    ):
        n = int(prev_manifest.get(f'{name}_bytes') or '0')
        stored = prev_manifest.get(f'{name}_sha', '-')
        if stored == '-' or n == 0:
            continue
        actual = hashlib.sha256(data[:n]).hexdigest()[:12]
        if actual != stored:
            msg = (
                f'{code}: {name} prefix changed'
                f' (expected {stored}, got {actual})'
                )
            breaks.append(msg)
    return breaks


def live_sha(row: dict) -> str:
    """Return the digest a manifest row's write left in HANDOFF.md.

    Parameters
    ----------
    row : dict
        One manifest row keyed by ``MANIFEST_FIELDS``.

    Returns
    -------
    str
        ``rewrite_sha`` where the row records one, else ``handoff_sha``.

    Notes
    -----
    - finish writes one text to HANDOFF.md and to ``cycles/c<N>.md``, so
      its row leaves ``rewrite_sha`` as ``-`` and ``handoff_sha`` names
      both files.
    - adopt archives the file it read and rewrites HANDOFF.md, so its row
      names the archive in ``handoff_sha`` and the rewrite here.
    - A row with no ``rewrite_sha`` column reads ``-`` and falls back.
      That holds for an adopt row from before the column too: its
      ``handoff_sha`` names the rewrite, not the archive.
    """
    rewrite = row.get('rewrite_sha', '-')
    return rewrite if rewrite not in {'', '-'} else row.get('handoff_sha', '')


def latest_rows(rows: list[Row]) -> dict[str, Row]:
    """Return the most recent ledger row for each path.
    """
    result: dict[str, Row] = {}
    for row in rows:
        result[row['path']] = row
    return result


def resolve_where(
    text: str,
    anchors: list[str],
) -> tuple[list[tuple[int, int]], list[str]]:
    """Resolve anchor strings to 1-based inclusive line spans in a file.

    Parameters
    ----------
    text : str
        The full content of the target file (split into lines internally).
    anchors : list[str]
        Anchor strings to locate - each is a heading text from the file's
        ``where`` field, with backticks, leading ``#`` markers, numbering,
        and surrounding whitespace stripped before comparison.

    Returns
    -------
    tuple[list[tuple[int, int]], list[str]]
        Resolved 1-based inclusive spans (one per matched anchor) and the
        list of anchor strings that could not be matched.

    Notes
    -----
    - Matching is case-insensitive.
    - A span runs from the matched heading to the line before the next
      heading of the same or higher level (same or fewer ``#`` characters),
      or to the last line of the file when no such heading follows.
    - An anchor ``s<d>`` or ``s<d><letter>`` resolves to the first heading
      whose leading section token matches: ``<d>.``, ``<d>:``, ``<d> -``,
      ``<d><letter>.``, ``<d><letter>:``, ``<d><letter> -``, or
      ``s<d>[<letter>][.:]``, the same token set ``_norm_heading`` strips.
      Equality on the whole token is required, so ``s11`` does not resolve
      to ``# 11b. Proof``. A bare ``S3`` word without a dot or colon is not
      a token. A literal heading match is tried first.
    - A dotted number is one token: ``s24.4`` names ``### 24.4 The
      decision`` and ``s24`` does not. The title alone lands on the first
      heading of that text, so the dotted form is how a sub-heading whose
      title repeats is named.
    - A letter-led id, one or two letters then ``<d>[<letter>]`` followed
      by ``.``, ``:``, or `` - ``, is a token too: ``F65``, ``f65``, and
      ``sF65`` all resolve ``## F65. Title``. The letters count, so ``F7``
      never lands on ``## 7. Seven``, and a longer word such as ``Log4j:``
      is title text.
    - The literal match is equality on the normalized text, never
      containment, so ``Retry`` does not land on ``## Retry budget``.
    - An unresolved anchor prints ``?`` in its span slot during rendering.
    """
    file_lines = text.splitlines()
    heading_info: list[tuple[int, int, str, set[str]]] = []
    for i, line in enumerate(file_lines):
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            # The dotted branch leads so `24.4` is one token and never
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
        # The s<d> and id forms must be read before normalizing:
        # _norm_heading strips the very token that names the section.
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
    return spans, unresolved


def block_sha(heading_and_body: str) -> str:
    """Return the first 12 hex of sha256 over the normalized block text.
    """
    normalized = re.sub(r' +', ' ', heading_and_body)
    # The writer hashes heading + newline + body; the reader hashes the
    # stripped block text. With an empty body only a trailing newline
    # separates them, so it must not count.
    normalized = '\n'.join(line.rstrip() for line in normalized.split('\n')).rstrip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def render_read(
    rows: list[Row],
    spans: dict[str, list[tuple[int, int] | None]],
    sizes: dict[str, str],
    shown: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Render the hq:read block body.

    Parameters
    ----------
    rows : list[Row]
        Live rows with ``read_before='always'``.
    spans : dict[str, list[tuple[int, int] | None]]
        Resolved spans per path; ``None`` for an unresolved anchor.
    sizes : dict[str, str]
        Size text per path, as ``read_first_data`` returns it.
    shown : dict[str, tuple[str, str]] | None, default None
        Display path and base phrase per stored path, as ``shown_paths``
        returns it; a path absent from it prints as stored.

    Returns
    -------
    str
        One line per row, sorted by kind then path:
        ``path:11-13  (320 tok)  label`` for an anchored row and
        ``path  (186 lines, 8.2k tok)  label`` for a whole file. When a
        row prints relative to the root or the pin, a first line names
        each base in use.
    """
    out = []
    bases: set[str] = set()
    for row in sorted(rows, key=lambda r: (r['kind'], r['path'])):
        path = row['path']
        display, base = (shown or {}).get(path, (path, ''))
        if base:
            bases.add(base)
        size = sizes.get(path, '-')
        label = row['label']
        if row['where'] == '-':
            out.append(f'{display}  ({size})  {label}')
        else:
            parts = [
                '?' if span is None else f'{span[0]}-{span[1]}'
                for span in spans.get(path, [])
                ]
            out.append(f'{display}:{",".join(parts) or "?"}  ({size})  {label}')
    if bases:
        out.insert(0, '; '.join(sorted(bases)))
    return '\n'.join(out)


def artifact_lines(
    walk: list[tuple[str, str]],
    rows: dict[str, Row],
    shown: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[str], dict[str, int], dict[str, int]]:
    """Sort artifacts into full lines, a never count, and a non-live count.

    Parameters
    ----------
    walk : list[tuple[str, str]]
        Top-level entries as ``(path, inferred_kind)``; ``'skip'`` entries
        excluded before this call.
    rows : dict[str, Row]
        Latest ledger row per path, with the caller's ``missing`` marks
        already applied; the disk is never consulted.
    shown : dict[str, tuple[str, str]] | None, default None
        Display path per stored path, as ``shown_paths`` returns it; a
        path absent from it prints as stored.

    Returns
    -------
    tuple[list[str], dict[str, int], dict[str, int]]
        The full lines, uncapped, in walk order and then ledger order for
        the rows the walk cannot see; the ``never`` rows counted by kind;
        the rows no longer live counted by status.

    Notes
    -----
    - A full line is ``path  kind  read_before  cN  label`` for a live row
      with ``read_before`` in {always, edit, mention}, or
      ``path  kind?  unstamped`` for a walk entry with no row.
    """
    full_lines: list[str] = []
    kind_never: dict[str, int] = {}
    non_live: dict[str, int] = {}
    seen: set[str] = set()

    def _classify(path: str, row: Row | None, inferred: str) -> None:
        """Emit one line for a walk entry or an out-of-walk row.

        Parameters
        ----------
        path : str
            Relative or stored path of the artifact.
        row : Row | None
            Latest ledger row for the path, or ``None`` when unstamped.
        inferred : str
            Kind inferred from the walk; used only when ``row`` is ``None``.
        """
        if row is None:
            full_lines.append(f'{path}  {inferred}?  unstamped')
            return
        status, rb = row['status'], row['read_before']
        if status != 'live':
            non_live[status] = non_live.get(status, 0) + 1
        elif rb in _FULL_RB:
            display = (shown or {}).get(path, (path, ''))[0]
            full_lines.append(
                f'{display}  {row["kind"]}  {rb}  c{row["cycle"]}  {row["label"]}')
        else:
            kind_never[row['kind']] = kind_never.get(row['kind'], 0) + 1

    for path, inferred in walk:
        seen.add(path)
        if rows.get(path) is None and is_recorded(path, rows):
            continue
        _classify(path, rows.get(path), inferred)

    # Rows the walk cannot see: abs paths and subdirectory paths.
    for path, row in rows.items():
        if path not in seen:
            _classify(path, row, row.get('kind', 'other'))
    return full_lines, kind_never, non_live


def render_artifacts(
    walk: list[tuple[str, str]],
    rows: dict[str, Row],
    slug: str,
    shown: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Render the hq:artifacts block body.

    Parameters
    ----------
    walk : list[tuple[str, str]]
        Top-level entries as ``(path, inferred_kind)``; ``'skip'`` entries
        excluded before this call.
    rows : dict[str, Row]
        Latest ledger row per path, with the caller's ``missing`` marks
        already applied; the renderer never consults the disk.
    slug : str
        Handoff slug for the hq command in count lines.
    shown : dict[str, tuple[str, str]] | None, default None
        Display path and base phrase per stored path, as ``shown_paths``
        returns it; a path absent from it prints as stored.

    Returns
    -------
    str
        Block body lines joined with newlines: every live row graded
        always, edit, or mention as a full line, then the never rows
        counted by kind and the rows no longer live counted by status.
        When a full line prints relative to the root or the pin, a first
        line names each base in use.
    """
    full_lines, kind_never, non_live = artifact_lines(walk, rows, shown)
    bases = {
        base for path, (_, base) in (shown or {}).items()
        if path in rows and rows[path]['status'] == 'live'
        and rows[path]['read_before'] in _FULL_RB
        }
    out = ['; '.join(sorted(bases))] if bases else []
    out += full_lines
    if kind_never:
        parts = '  '.join(f'{k} x{v}' for k, v in sorted(kind_never.items()))
        out.append(f'{parts}  - hq artifacts {slug}')
    if non_live:
        ordered = [k for k in ('superseded', 'archived', 'missing') if k in non_live]
        ordered += sorted(k for k in non_live if k not in ordered)
        parts = '  '.join(f'{k} {non_live[k]}' for k in ordered)
        out.append(f'{parts}  - hq when {slug} <path>')
    return '\n'.join(out)


def _join_headline_body(headline: str, body: str) -> str:
    """Join a bold headline to its body with the correct separator.

    Parameters
    ----------
    headline : str
        Headline text, typically bold-formatted.
    body : str
        Body text following the headline; may be empty.

    Returns
    -------
    str
        Headline and body joined, with no separator when the body opens
        with punctuation that continues the headline's sentence, and a
        single space otherwise. Returns the headline unchanged when body
        is empty.

    Notes
    -----
    - Punctuation set: ``,``, ``.``, ``;``, ``:``, ``)``, ``!``, ``?``.
    - A body that opens with punctuation continues the headline's own
      sentence; a space before the comma would change the wording that
      adoption preserves.
    """
    if not body:
        return headline
    joiner = '' if body[:1] in {',', '.', ';', ':', ')', '!', '?'} else ' '
    return f'{headline}{joiner}{body}'


def split_headline(content: str) -> tuple[str, str]:
    """Split one plain item's text into its headline and the body after it.

    Parameters
    ----------
    content : str
        The item's text with its marker and any ``kind:`` prefix removed,
        holding no bold span.

    Returns
    -------
    tuple[str, str]
        The headline and the body, both stripped; the body is empty when
        the headline is the whole text.

    Notes
    -----
    - A sentence ends at ``.``, ``!``, or ``?`` followed by a space or
      the end, so a dot inside a file name or a version does not split.
    - A candidate that leaves a double quote open is skipped: a quoted
      sentence end is the quotation's, not the item's. A closing quote
      may follow the punctuation, ``later."``.
    - A candidate of one or two words with text after it is a label,
      ``Cycle 26.``, not the item, so the headline runs on to the next
      sentence; a candidate with no word at all is returned as is, so
      the caller's no-headline refusal still fires.
    """
    for m in re.finditer(r'[.!?]"?(?=\s|$)', content):
        head, rest = content[:m.end()], content[m.end():]
        if head.count('"') % 2:
            continue
        if rest.strip() and len(re.findall(r'\w+', head)) in {1, 2}:
            continue
        return head.strip(), rest.strip()
    return content.strip(), ''


def render_standing(
    items: list[dict],
    superseded_ids: set[str],
    slug: str,
) -> str:
    """Render the hq:standing block body.

    Parameters
    ----------
    items : list[dict]
        All standing items with keys: id, prefix, cycle, headline, body.
    superseded_ids : set[str]
        Item ids targeted by a supersession line.
    slug : str
        Handoff slug for the hq command in the superseded line.

    Returns
    -------
    str
        Block body: every live constraint in full and every live
        decision and dead end as a headline, each kind under its heading,
        then ``superseded N  - hq standing <slug>`` when any item was
        superseded. No cap: the body of a decision or a dead end is the
        one thing behind ``hq standing <slug> <id>``.
    """
    live = [i for i in items if i['id'] not in superseded_ids]
    sup_count = len(items) - len(live)
    out: list[str] = []
    for prefix, heading, include_body in (
        ('c', '### Constraints', True),
        ('d', '### Decisions', False),
        ('x', '### Dead ends', False),
    ):
        group = [i for i in live if i['prefix'] == prefix]
        if not group:
            continue
        out.append(heading)
        for item in group:
            pfx = f'(c{item["cycle"]}) ' if item.get('cycle') else ''
            line = f'[{item["id"]}] {pfx}**{item["headline"]}**'
            if include_body:
                line = _join_headline_body(line, item.get('body', ''))
            out.append(line.rstrip())
    if sup_count:
        out.append(f'superseded {sup_count}  - hq standing {slug}')
    return '\n'.join(out)


def render_log(manifest: list[dict], n: int = 3) -> str:
    """Render the ## Log block body.

    Parameters
    ----------
    manifest : list[dict]
        All finished cycles in order, each a dict keyed by MANIFEST_FIELDS.
    n : int, default 3
        Number of recent cycles to show in full.

    Returns
    -------
    str
        Log lines joined with newlines.
    """
    if not manifest:
        return ''
    older = manifest[:-n] if len(manifest) > n else []
    recent = manifest[-n:]
    out: list[str] = []
    for row in recent:
        repos = row.get('repos', '-')
        if repos == '-':
            out.append(f'- {row["written"]}: {row["log"]}')
        else:
            out.append(
                f'- {row["written"]} (cycle {row["cycle"]}, {repos}): {row["log"]}')
    if older:
        first = manifest[0]['cycle']
        last_old = older[-1]['cycle']
        out.append(f'- cycles {first}-{last_old}: see cycles/manifest.tsv')
    return '\n'.join(out)


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
    now_terms = _extract_terms(now_text)
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


def drain_unfiled(
    cursor_text: str,
) -> tuple[list[tuple[str, str, str]], str, str | None]:
    """Parse the ## Unfiled section and extract typed bullets.

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
    - Accepted prefixes: ``- decision: ``, ``- constraint: ``,
      ``- dead-end: ``.
    - The headline is the bold span if present, else the first sentence
      as ``split_headline`` reads it: never ending inside an open
      quotation, and past a one- or two-word label such as ``Cycle 26.``.
    - An indented line continues the bullet above it, joined by one space,
      as ``adopt`` joins a wrapped standing bullet.
    - Any other bullet, and any unindented line that is no bullet, is the
      refusal.
    - A newline in a body becomes a space.
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
                content = content[len(prefix) + 2:]
                break
        if kind is None:
            return (
                [], cursor_text,
                (f'untyped Unfiled bullet: {stripped!r}'
                 ' - prefix it decision:, constraint:, or dead-end:,'
                 ' move it into a cursor section, or rehome it to a sibling'))
        bold = re.search(r'\*\*(.+?)\*\*', content)
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
    _standing_prefix = re.compile(r'^- \[[dcx]\d+\] \(c\d+\) ')
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
                and _KF_LABEL_PAT.match(raw)):
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
        if _HEADER_PAT.search(ln):
            continue
        is_heading = ln.lstrip().startswith('#')
        # A heading-form grade label is structure only under Key files;
        # anywhere else a heading adopt drops is a lost line.
        kf_label = in_key_files and bool(_KF_LABEL_PAT.match(ln.lstrip()))
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
        if norm in union_text:
            continue
        if is_heading and (kf_label or _heading_covered(norm)):
            continue
        result.append(ln)
    return result


def sibling_texts(folder: pathlib.Path, live: dict[str, Row]) -> list[str]:
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


def split_handoff(text: str) -> dict:
    """Parse HANDOFF.md text into its structural components.

    Parameters
    ----------
    text : str
        Raw text of a ``HANDOFF.md`` file.

    Returns
    -------
    dict
        Keys: ``header`` (str), ``slug`` (str), ``cycle`` (int or None),
        ``cursor`` (str from first ``## `` to first block or ``## Log``),
        ``blocks`` (dict of block name to body text), ``log`` (str).
    """
    result: dict = {'header': '', 'slug': '', 'cycle': None,
                    'cursor': '', 'blocks': {}, 'log': ''}
    for line in text.splitlines():
        m = _HEADER_PAT.search(line)
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


# ----------------------------------------------------------------------
# Private helpers
# ----------------------------------------------------------------------


def _resolve_root(argv: argparse.Namespace) -> pathlib.Path:
    """Return the project root from flags, env, git, or cwd.

    Parameters
    ----------
    argv : argparse.Namespace
        Parsed flags; ``--root`` overrides all other sources.

    Returns
    -------
    pathlib.Path
        Project root, in priority order: ``--root`` flag, ``HQ_ROOT``
        env var, ``git rev-parse --show-toplevel``, the nearest ancestor
        of the cwd (itself included) that holds a ``.handoff`` directory,
        cwd.

    Notes
    -----
    - A root that exists and is not a directory exits 2; every caller
      would otherwise raise NotADirectoryError joining ``.handoff`` onto
      it.
    - The ancestor walk covers a shell whose cwd moved into
      ``.handoff/<slug>/`` outside a repo; taking that cwd as the root
      would look for ``.handoff/<slug>/.handoff/<slug>``.
    """
    root = getattr(argv, 'root', None) or os.environ.get('HQ_ROOT')
    if root:
        try:
            root_path = pathlib.Path(root).expanduser()
        except RuntimeError:
            root_path = pathlib.Path(root)
        if root_path.exists() and not root_path.is_dir():
            print(f'hq: root is not a directory: {root_path}')
            sys.exit(2)
        return root_path
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, check=True)
        return pathlib.Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    cwd = pathlib.Path.cwd()
    for ancestor in (cwd, *cwd.parents):
        if (ancestor / HANDOFF_DIRNAME).is_dir():
            return ancestor
    return cwd


def _git_state(root: pathlib.Path) -> tuple[str, str, list[str]]:
    """Return (branch, sha7, dirty_paths) from git; hyphens on error.

    Parameters
    ----------
    root : pathlib.Path
        Repository working tree the three git calls run in.

    Returns
    -------
    tuple[str, str, list[str]]
        Branch name, seven-hex HEAD sha, and one path per porcelain
        status line. ``('-', '-', [])`` when git is absent or fails.

    Notes
    -----
    - The dirty list comes from ``git status --porcelain``, so a staged
      edit and an untracked file count as dirty alongside an unstaged one.
    - A rename line (``R  old -> new``) counts as its new name.
    """
    try:
        branch = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, check=True, cwd=root,
            ).stdout.strip()
        sha = subprocess.run(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            capture_output=True, text=True, check=True, cwd=root,
            ).stdout.strip()
        status_out = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, cwd=root,
            ).stdout
        dirty = []
        for line in status_out.splitlines():
            path = line[3:].strip()
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            if path:
                dirty.append(path)
        return branch, sha, dirty
    except (subprocess.CalledProcessError, FileNotFoundError):
        return '-', '-', []


def _sha12(data: bytes) -> str:
    """Return the first 12 hex of sha256 of data.
    """
    return hashlib.sha256(data).hexdigest()[:12]


def _sha12_path(path: pathlib.Path) -> str:
    """Return the first 12 hex of sha256 of path's bytes, or '-' on error.
    """
    try:
        return _sha12(path.read_bytes())
    except OSError:
        return '-'


def _read_tsv(path: pathlib.Path, fields: list[str]) -> list[dict]:
    """Read a tab-separated file into a list of dicts, skipping the header.
    """
    if not path.exists():
        return []
    rows = []
    text = path.read_text(encoding='utf-8', errors='replace')
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.split('\t')
        rows.append(dict(zip(fields, parts + ['-'] * (len(fields) - len(parts)))))
    return rows


def _append_lines(path: pathlib.Path, lines: list[str]) -> None:
    """Append lines to a text file, restoring a lost trailing newline first.

    Parameters
    ----------
    path : pathlib.Path
        File to append to; created when absent.
    lines : list[str]
        Lines without their newline.

    Notes
    -----
    - The guard is the one ``_append_tsv`` applies to the ledger: a file
      whose last byte is not a newline gets one before the append, so
      the byte-prefix witness still holds and no two items share a line.
    """
    last_byte = b''
    if path.exists() and path.stat().st_size:
        with path.open('rb') as fh:
            fh.seek(-1, os.SEEK_END)
            last_byte = fh.read(1)
    lead = '' if last_byte in {b'', b'\n'} else '\n'
    if lead:
        print(f'advisory: {path.name} lacked its trailing newline; restored')
    with path.open('a', encoding='utf-8') as fh:
        fh.write(lead + ''.join(line + '\n' for line in lines))


def _append_tsv(path: pathlib.Path, fields: list[str], row: dict, header: str) -> None:
    """Append one row to a TSV file, writing the header when the file is new.

    Parameters
    ----------
    path : pathlib.Path
        File to append to; created with the header when absent.
    fields : list[str]
        Field names, in column order.
    row : dict
        Values by field name; a missing field is written as ``-``, and a
        tab, carriage return, or newline inside a value becomes a space.
    header : str
        Header line for a new file.

    Notes
    -----
    - A file whose last byte is not a newline gets one before the row, so
      an append can never glue two rows onto one line.
    """
    if not path.exists():
        path.write_text(header + '\n', encoding='utf-8')
    vals = '\t'.join(
        ' '.join(str(row.get(f, '-')).split('\t')).replace('\r', ' ').replace('\n', ' ')
        for f in fields)
    last_byte = b''
    if path.stat().st_size:
        with path.open('rb') as fh:
            fh.seek(-1, os.SEEK_END)
            last_byte = fh.read(1)
    lead = '' if last_byte in {b'', b'\n'} else '\n'
    if lead:
        print(f'advisory: {path.name} lacked its trailing newline; restored')
    with path.open('a', encoding='utf-8') as fh:
        fh.write(lead + vals + '\n')


def validate_work_dir(root: pathlib.Path, token: str) -> tuple[str, str]:
    """Check one work-dir token and return (value, reason).

    Parameters
    ----------
    root : pathlib.Path
        Project root; a directory under it is stored relative to it.
    token : str
        A directory spelled relative to the root, by ``~``, or
        absolutely.

    Returns
    -------
    tuple[str, str]
        ``(value, '')`` on a pass - the directory's posix path relative
        to the root when under it, else its ``~`` or absolute spelling -
        or ``('', reason)``, the reason a clause that follows the token
        in a printed line.

    Notes
    -----
    - The root itself, anything under ``.handoff/``, and anything under
      the system temp directory are refused: the first turns the root
      into a dump, the second is the handoff's own, the third does not
      outlive the host. Any other directory passes, inside the root or
      out; a root that itself sits under the temp directory keeps its
      own subdirectories.
    - The directory must already be on disk: a pin names where the
      thread's work already lives, so a missing one is a mistyped path.
    """
    clean = token.strip().strip('`')
    root_abs = pathlib.Path(os.path.abspath(os.path.expanduser(str(root))))
    if clean.startswith(('/', '~')):
        candidate = pathlib.Path(os.path.normpath(os.path.expanduser(clean)))
    else:
        candidate = pathlib.Path(os.path.normpath(root_abs / clean))
    if candidate == root_abs:
        return '', 'is the root itself'
    # Containment is lexical first, then by real path, so a root reached
    # through a symlink still owns its directories spelled either way
    # and is still itself spelled either way.
    under_root = root_abs in candidate.parents
    if not under_root:
        real_root = pathlib.Path(os.path.realpath(root_abs))
        real_candidate = pathlib.Path(os.path.realpath(candidate))
        if real_candidate == real_root:
            return '', 'is the root itself'
        under_root = real_root in real_candidate.parents
        if under_root:
            candidate = real_root / real_candidate.relative_to(real_root)
            root_abs = real_root
    if under_root:
        rel = candidate.relative_to(root_abs).as_posix()
        if rel == HANDOFF_DIRNAME or rel.startswith(HANDOFF_DIRNAME + '/'):
            return '', f'is under {HANDOFF_DIRNAME}/'
    # A project checked out under the temp directory keeps its own
    # subdirectories; the temp refusal names a path outside the root.
    elif any(candidate == d or d in candidate.parents for d in _TEMP_DIRS):
        return '', 'is under the system temp directory'
    if not candidate.exists():
        return '', 'does not exist'
    if not candidate.is_dir():
        return '', 'is not a directory'
    if under_root:
        return candidate.relative_to(root_abs).as_posix(), ''
    home = pathlib.Path(os.path.abspath(os.path.expanduser('~')))
    try:
        rel = candidate.relative_to(home).as_posix()
    except ValueError:
        return str(candidate), ''
    return ('~' if rel == '.' else '~/' + rel), ''


def resolve_work_dir(folder: pathlib.Path) -> tuple[str, list[str]]:
    """Resolve the thread's work dir from its pin; no pin is the folder.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the pin is ``folder/work-dir`` and the root its
        grandparent.

    Returns
    -------
    tuple[str, list[str]]
        ``(value, lines)``: the pinned directory as ``validate_work_dir``
        returns it, or ``''`` when the folder's own kind folders take the
        made files. ``lines`` are the printed lines for a pin that failed
        its check, each naming its move.

    Notes
    -----
    - No pin is the default, not a finding: the folder's kind folders
      hold what the thread makes. Only a pin naming no directory prints.
    - A bad pin is reported and passed over, never fatal: begin and
      finish must still run on one.
    - A directory under the pin's name is such a pin: the walk skips the
      reserved name, so a file put there is invisible until it moves.
    """
    pin = folder / _WORK_PIN_NAME
    if pin.is_dir():
        return '', [
            (f'{HANDOFF_DIRNAME}/{folder.name}/{_WORK_PIN_NAME} is a directory'
             ' - the pin is a one-line file; move the folder under specs/,'
             ' drafts/, notes/, or outputs/')]
    if not pin.is_file():
        return '', []
    value = ''
    try:
        raw_pin = pin.read_text(encoding='utf-8').strip()
    except OSError:
        raw_pin, reason = '', 'cannot be read'
    else:
        value, reason = (
            validate_work_dir(folder.parent.parent, raw_pin) if raw_pin
            else ('', 'is empty'))
    if value:
        return value, []
    return '', [
        (f'{HANDOFF_DIRNAME}/{folder.name}/{_WORK_PIN_NAME} names {raw_pin!r}:'
         f' {reason} - repin it with hq work-dir {folder.name} <dir>, or'
         ' --clear to fall back to the folder')]


def work_dir_line(folder: pathlib.Path, value: str, first_cycle: bool = False) -> str:
    """Return the ``work dir:`` line for a resolved value.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder, named in the default line.
    value : str
        The pinned directory as ``resolve_work_dir`` returns it, or
        ``''`` for the folder's own kind folders.
    first_cycle : bool, default False
        True at a thread's first begin: the line then ends in the pin
        command, since that is when a thread whose work already lives in
        a project directory says so.

    Returns
    -------
    str
        ``work dir: <where> (pin|default) - <where made files go>``.
    """
    if value:
        return (
            f'work dir: {value} (pin) - specs, drafts, and outputs go there,'
            ' stamped by their ~ or absolute path; notes stay under notes/')
    tail = (
        '; when the spec and experiments already live in a project'
        f' directory, hq work-dir {folder.name} <dir> pins it'
        if first_cycle else '; any other folder is one unit')
    return (
        f'work dir: {HANDOFF_DIRNAME}/{folder.name}/ (default)'
        f' - made files go under specs/, drafts/, notes/, or outputs/{tail}')


def _find_folder(
    root: pathlib.Path,
    slug: str,
    missing_ok: bool = False,
) -> pathlib.Path:
    """Resolve slug to a handoff folder, exiting 2 on ambiguity or no match.

    Parameters
    ----------
    root : pathlib.Path
        Project root.
    slug : str
        Exact folder name or a unique prefix under ``root/.handoff/``.
    missing_ok : bool, default False
        When True, return ``.handoff/<slug>`` instead of exiting when no
        folder matches; still exits on ambiguity. ``begin`` creates it.

    Returns
    -------
    pathlib.Path
        The resolved folder path.

    Notes
    -----
    - The slug is one path component: ``[A-Za-z0-9][A-Za-z0-9._-]*``. It
      is joined onto ``.handoff/`` unquoted, so ``..`` or an embedded
      separator would place a handoff folder outside ``.handoff/``.
    - Nothing here writes: a read-only verb on a mistyped root leaves
      the disk as it found it, and ``begin`` creates the folder itself.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', slug) or '..' in slug:
        print(
            f'hq: invalid slug {slug!r}'
            " - letters, digits, '.', '_', '-' only, never '/' or '..'")
        sys.exit(2)
    handoffs = root / HANDOFF_DIRNAME
    exact = handoffs / slug
    if exact.is_dir():
        return exact
    siblings = sorted(handoffs.iterdir()) if handoffs.is_dir() else []
    candidates = [
        d for d in siblings
        if d.is_dir() and d.name.startswith(slug)
        ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = ', '.join(d.name for d in candidates)
        print(f'hq: ambiguous slug {slug!r}: {names}')
        sys.exit(2)
    if missing_ok:
        return exact
    print(
        f'hq: no folder matching {slug!r} under {handoffs}'
        '; run hq list to see what exists')
    sys.exit(2)


def _walk_folder(folder: pathlib.Path) -> list[tuple[str, str]]:
    """Walk the folder and return (name, inferred_kind) pairs.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to walk.

    Returns
    -------
    list[tuple[str, str]]
        One pair per entry - a top-level name, or ``<kind folder>/<name>``
        - with its inferred kind from ``infer_kind``.

    Notes
    -----
    - Skips HANDOFF.md, ledger.tsv, standing.md, cycles/, .hq.lock,
      dotfiles.
    - Walks one level into each kind folder - specs/, drafts/, notes/,
      outputs/ - listing ``<kind folder>/<name>`` with the folder's kind;
      the kind folder itself is not an entry. Any other directory,
      probes/ included, is one probe-dir entry.
    - Reads the first heading line of text files for spec detection.
    - ``'skip'`` entries are included; callers filter them as needed.
      Besides a conflicted copy, an entry that is neither a regular file
      nor a directory and one whose name carries a tab, carriage return,
      or newline are
      ``'skip'``: opening a FIFO blocks forever, and a ledger row scrubs
      the separator out of the name, so the row could never find the
      file again. A caller tells the two apart by the name.
    """
    results = []
    scopes = [('', folder)] + [
        (f'{kind_dir}/', folder / kind_dir) for kind_dir in sorted(_KIND_DIRS)
        if (folder / kind_dir).is_dir()]
    for prefix, scope in scopes:
        for entry in sorted(scope.iterdir()):
            name = entry.name
            if name.startswith('.') or name in _SKIP_NAMES:
                continue
            is_dir = entry.is_dir()
            if not prefix and is_dir and name in _KIND_DIRS:
                continue
            if not is_dir and not entry.is_file():
                results.append((prefix + name, 'skip'))
                continue
            if any(ch in name for ch in '\t\r\n'):
                results.append((prefix + name, 'skip'))
                continue
            first_heading = ''
            if not is_dir:
                try:
                    with entry.open(encoding='utf-8', errors='replace') as fh:
                        for line in fh:
                            if line.startswith('#'):
                                first_heading = line.strip()
                                break
                except OSError:
                    pass
            kind, _ = infer_kind(
                name, is_dir, first_heading, top_level=not prefix,
                kind_dir=prefix.rstrip('/'))
            results.append((prefix + name, kind))
    return results


def _read_lock(folder: pathlib.Path) -> dict[str, str]:
    """Read .hq.lock as a key=value dict; return {} when absent or unreadable.
    """
    lock_path = folder / '.hq.lock'
    if not lock_path.exists():
        return {}
    try:
        text = lock_path.read_text(encoding='utf-8')
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            result[k.strip()] = v.strip()
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
      a dotted number ``<d>.<d>``, ``<d><letter>.``, ``<d><letter>:``,
      ``<d><letter> -``, or a letter-led id ``<letters><d>[<letter>]``
      with the same delimiters, so ``## 11b. Proof``, ``## s11b: Proof``,
      ``## 2a - Basis``, ``## 3.1 Warmup``, and ``## F65. Title`` all
      resolve. A bare ``S3`` (no dot or colon) is a word, and stays.
    - An id takes one or two letters and its dot is not followed by a
      digit, so ``Log4j:``, ``OAuth2:``, and ``v2.0`` are words a title
      may start with, never section tokens.
    - The dash delimiter needs whitespace on both sides, so ``2a-b`` in
      ``## 2a-b range`` is not a token and its ``2`` is stripped by the
      plain-digit fallback.
    - A letter suffix is recognized only when a ``.``, ``:``, or `` - ``
      follows it; ``3D`` in ``## 3D printing`` has no such delimiter and
      its ``3`` is stripped by the plain-digit fallback.
    """
    text = re.sub(r'^[#\s]+', '', text)
    text = re.sub(
        r'^(?:[a-z]{1,2}\d+[a-z]?(?:[.:](?!\d)|\s+-(?=\s))'
        r'|\d+[a-z](?:[.:]|\s+-(?=\s))|\d[\d.]*(?::|\s+-(?=\s))?)\s*',
        '', text, flags=re.IGNORECASE)
    text = re.sub(r'`', '', text)
    return text.strip().lower()


def _split_where(where: str) -> list[str]:
    r"""Split a ledger ``where`` field into its anchors.

    Parameters
    ----------
    where : str
        The field as stored: anchors joined with ``;``, or ``-``.

    Returns
    -------
    list[str]
        The anchors, stripped, with empty parts dropped; ``-`` gives none.

    Notes
    -----
    - A ``;`` inside a heading's text is written ``\\;`` in the field, so
      the split is on an unescaped ``;`` and each anchor gets its ``;``
      back before it is matched.
    """
    if where in {'-', ''}:
        return []
    parts = re.split(r'(?<!\\);', where)
    return [part.replace('\\;', ';').strip() for part in parts if part.strip()]


def _extract_terms(text: str) -> set[str]:
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


def _parse_standing(text: str) -> tuple[list[dict], set[str]]:
    """Parse standing.md into items and the set of superseded ids.

    Parameters
    ----------
    text : str
        Raw text of ``standing.md``.

    Returns
    -------
    tuple[list[dict], set[str]]
        A pair of: all standing items and the set of superseded ids.
        Each item dict has keys ``id``, ``prefix`` (first character of
        the id), ``cycle``, ``headline``, and ``body``. The superseded-id
        set holds every id that appears as the source in a supersession
        line ``(c<n>) <old> -> <new>``.
    """
    items: list[dict] = []
    superseded_ids: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('- '):
            continue
        content = stripped[2:]
        sup_m = re.match(r'\(c\d+\)\s+([dcx]\d+)\s*->\s*([dcx]\d+)', content)
        if sup_m:
            superseded_ids.add(sup_m.group(1))
            continue
        id_pat = r'\[([dcx]\d+)\]\s+(?:\(c(\d+)\)\s+)?\*\*(.+?)\*\*\s*(.*)'
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


def _next_id(items: list[dict], prefix: str) -> str:
    """Return the next unused two-digit-minimum id for the given prefix.
    """
    existing = {item['id'] for item in items if item['prefix'] == prefix}
    n = 1
    while True:
        candidate = f'{prefix}{n:02d}' if n <= 99 else f'{prefix}{n}'
        if candidate not in existing:
            return candidate
        n += 1


def _line_count(path: pathlib.Path) -> int:
    """Return the ledger's ``lines`` figure for a file or directory.

    Parameters
    ----------
    path : pathlib.Path
        File or directory to count.

    Returns
    -------
    int
        For a file, the newline count ``wc -l`` reports: a last line with
        no terminator adds nothing. For a directory, its regular files,
        dotfiles and nested directories excluded. 0 on an OSError.
    """
    try:
        if path.is_dir():
            return sum(
                1 for entry in path.iterdir()
                if entry.is_file() and not entry.name.startswith('.'))
        return path.read_bytes().count(b'\n')
    except OSError:
        return 0


def _pointer_path(
    folder: pathlib.Path,
    token: str,
) -> tuple[str, pathlib.Path, str]:
    """Resolve a Key files pointer the way section 12 step 3 reads it.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder the pointer was written in.
    token : str
        The pointer's path token, backticked or bare.

    Returns
    -------
    tuple[str, pathlib.Path, str]
        The stored path, the path on disk, and the base, as
        ``_stored_path`` returns them.

    Notes
    -----
    - A foreign file names repo files relative to the repo root, so a
      token the folder does not hold is tried against the project root
      (the folder's grandparent), the cwd, and home before it is seeded
      as a missing folder path. ``stamp`` never searches; only adoption
      reads pointers it did not write.
    """
    stored, path_obj, base = _stored_path(folder, token)
    if base == 'abs' or path_obj.exists():
        return stored, path_obj, base
    clean = token.strip('`')
    for base_dir in (folder.parent.parent, pathlib.Path.cwd(), pathlib.Path.home()):
        candidate = pathlib.Path(os.path.normpath(base_dir / clean))
        if candidate.exists():
            return _stored_path(folder, str(candidate))
    return stored, path_obj, base


def _successor_path(folder: pathlib.Path, successor: str) -> pathlib.Path:
    """Return the disk path a successor field names, keyed as an artifact is.
    """
    return _stored_path(folder, successor)[1]


def _stored_path(
    folder: pathlib.Path,
    token: str,
) -> tuple[str, pathlib.Path, str]:
    """Resolve a path token to (stored_path, path_obj, base).

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder used as the base for relative paths.
    token : str
        Raw path token; backticks are stripped, a leading ``/`` or ``~``
        names a path in its own right, anything else joins the folder.

    Returns
    -------
    tuple[str, pathlib.Path, str]
        The path as the ledger stores it, the path on disk, and the base:
        ``folder`` inside the handoff folder, ``abs`` outside it.

    Notes
    -----
    - One file has one stored path: ``SPEC.md``, ``./SPEC.md``,
      ``sub/../SPEC.md``, and the folder's own absolute spelling all key
      on ``SPEC.md``.
    - Normalization is lexical, so a symlink in the spelling reaches the
      ledger as written.
    - An outside path stores as ``~/...`` under home, else absolute. A
      relative token is never searched for under the cwd or home: a repo
      file is stamped by its ``~`` or absolute path.
    """
    path_clean = token.strip('`')
    home = pathlib.Path(os.path.abspath(os.path.expanduser('~')))
    folder_abs = pathlib.Path(os.path.abspath(os.path.expanduser(str(folder))))
    if path_clean.startswith(('/', '~')):
        candidate = pathlib.Path(os.path.normpath(os.path.expanduser(path_clean)))
    else:
        candidate = pathlib.Path(os.path.normpath(folder_abs / path_clean))
    try:
        return candidate.relative_to(folder_abs).as_posix(), candidate, 'folder'
    except ValueError:
        pass
    try:
        return '~/' + candidate.relative_to(home).as_posix(), candidate, 'abs'
    except ValueError:
        return str(candidate), candidate, 'abs'


def _folder_state(
    folder: pathlib.Path,
) -> tuple[list, list, dict, dict, list, bytes, bytes]:
    """Load walk, ledger rows, sha_map, manifest, and raw bytes for folder.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to load.

    Returns
    -------
    tuple
        (walk, rows, live, sha_map, manifest, lb, sb).
        walk: list of (name, inferred_kind) from _walk_folder.
        rows: all ledger rows.
        live: latest row per path (latest_rows).
        sha_map: sha12 per path, including abs rows' current shas.
        manifest: all manifest rows.
        lb: ledger.tsv bytes.
        sb: standing.md bytes.
    """
    walk = _walk_folder(folder)
    rows = _read_tsv(folder / 'ledger.tsv', LEDGER_FIELDS)
    live = latest_rows(rows)
    sha_map = {
        n: _sha12_path(folder / n)
        for n, _ in walk if (folder / n).is_file()
        }
    for p_key, row in live.items():
        if row['base'] == 'abs':
            abs_p = pathlib.Path(p_key).expanduser()
            if abs_p.is_file():
                sha_map[p_key] = _sha12_path(abs_p)
        elif p_key not in sha_map and (folder / p_key).is_file():
            sha_map[p_key] = _sha12_path(folder / p_key)
    manifest = _read_tsv(folder / 'cycles' / 'manifest.tsv', MANIFEST_FIELDS)
    ledger_p = folder / 'ledger.tsv'
    standing_p = folder / 'standing.md'
    lb = ledger_p.read_bytes() if ledger_p.exists() else b''
    sb = standing_p.read_bytes() if standing_p.exists() else b''
    return walk, rows, live, sha_map, manifest, lb, sb


def _reconcile_missing(folder: pathlib.Path, live: dict[str, Row]) -> dict[str, Row]:
    """Return the live rows with ``missing`` judged against the disk.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder that folder-base paths are relative to.
    live : dict[str, Row]
        Latest ledger row per path, as stored.

    Returns
    -------
    dict[str, Row]
        A copy in which a live row whose path is absent reads ``missing``
        and a ``missing`` row whose path is back reads ``live``. The ledger
        itself is not rewritten.
    """
    out: dict[str, Row] = {}
    for p_key, row in live.items():
        target = (
            pathlib.Path(p_key).expanduser() if row['base'] == 'abs'
            else folder / p_key)
        status = row['status']
        if status == 'live' and not target.exists():
            status = 'missing'
        elif status == 'missing' and target.exists():
            status = 'live'
        out[p_key] = dict(row, status=status) if status != row['status'] else row
    return out


def dangling_successors(
    folder: pathlib.Path,
    live: dict[str, Row],
) -> list[tuple[str, str]]:
    """Return (path, successor) for superseded rows whose successors are gone.

    Parameters
    ----------
    folder : pathlib.Path
        The handoff folder.
    live : dict[str, Row]
        Latest row per path.

    Returns
    -------
    list[tuple[str, str]]
        One pair per row with status ``superseded`` whose successor field
        names no regular file on disk, in ledger order.

    Notes
    -----
    - Such a row is ungated with nothing live standing in for it, so
      ``begin`` and ``finish`` name it as an advisory; R1 accepted the
      successor when it was on disk, and this is the only place its later
      disappearance surfaces.
    """
    return [
        (path, row['successor']) for path, row in live.items()
        if row['status'] == 'superseded' and row['successor'] != '-'
        and not _successor_path(folder, row['successor']).is_file()
        ]


def _dirty_str(dirty: list[str]) -> str:
    """Return the dirty-paths tail for the Written header, or ' | clean'.

    Parameters
    ----------
    dirty : list[str]
        Paths reported dirty by git status --porcelain.

    Returns
    -------
    str
        ' | clean' when dirty is empty; ' | dirty: a, b' for up to five
        paths; ' | N files dirty' past five, N the total.
    """
    if not dirty:
        return ' | clean'
    if len(dirty) > 5:
        return f' | {len(dirty)} files dirty'
    return f' | dirty: {", ".join(dirty)}'


def shown_paths(
    folder: pathlib.Path,
    live: dict[str, Row],
) -> dict[str, tuple[str, str]]:
    """Map each stored path under the root or the pin to how a block prints it.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the root is its grandparent and the pin its
        ``work-dir`` file.
    live : dict[str, Row]
        Latest ledger row per path.

    Returns
    -------
    dict[str, tuple[str, str]]
        Keyed by stored path, for each ``abs`` row under the pinned work
        dir or the root: the path relative to that base, and the base
        phrase a block's first line names - ``root ~/code/proj`` or
        ``work dir ~/code/proj-wt``. Every other row is absent and
        prints as stored.

    Notes
    -----
    - The pin is tried first: a pin inside the root is the more specific
      base, and a thread's made files print as their neighbors are named.
    - A base prints by ``~`` under home, else absolutely, the spelling
      ``hq work-dir`` uses.
    """
    home = pathlib.Path(os.path.abspath(os.path.expanduser('~')))
    root = pathlib.Path(os.path.abspath(folder.parent.parent))
    pin_value, _ = resolve_work_dir(folder)
    bases: list[tuple[str, pathlib.Path]] = []
    if pin_value:
        pin = (
            pathlib.Path(os.path.normpath(os.path.expanduser(pin_value)))
            if pin_value.startswith(('/', '~'))
            else pathlib.Path(os.path.normpath(root / pin_value)))
        bases.append(('work dir', pin))
    bases.append(('root', root))
    shown: dict[str, tuple[str, str]] = {}
    for path, row in live.items():
        if row['base'] != 'abs':
            continue
        target = pathlib.Path(os.path.normpath(os.path.expanduser(path)))
        for name, base in bases:
            if base not in target.parents:
                continue
            try:
                rel = base.relative_to(home).as_posix()
                base_shown = '~' if rel == '.' else f'~/{rel}'
            except ValueError:
                base_shown = str(base)
            shown[path] = (target.relative_to(base).as_posix(), f'{name} {base_shown}')
            break
    return shown


def read_first_data(
    folder: pathlib.Path,
    live: dict[str, Row],
) -> tuple[
    list[Row],
    dict[str, list[tuple[int, int] | None]],
    dict[str, str],
    dict[str, int],
]:
    """Resolve what ``hq read`` prints for each live ``always`` row.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder that folder-base paths are relative to.
    live : dict[str, Row]
        Latest ledger row per path, with any ``missing`` marks applied.

    Returns
    -------
    tuple
        ``(rows, spans, sizes, tokens)``: the live ``always`` rows; per
        anchored path, one span per anchor, ``None`` for one that matches
        no heading; per path, the size text of the read - ``320 tok`` or
        ``1.2k tok`` for an anchored row, ``186 lines, 8.2k tok`` for a
        whole file,
        ``35 KB`` for a file that is not UTF-8 text, ``3 files`` for a
        directory; and per path the tokens of the read, ``len(text) // 4``.

    Notes
    -----
    - A row whose anchors all miss is read whole, so it sizes as a whole
      file.
    - A file gone from disk sizes by the ledger's line count and counts
      no tokens; bytes and files count none either.
    """
    rows = [
        r for r in live.values()
        if r['status'] == 'live' and r['read_before'] == 'always'
        ]
    spans: dict[str, list[tuple[int, int] | None]] = {}
    sizes: dict[str, str] = {}
    tokens: dict[str, int] = {}
    for row in rows:
        path = row['path']
        fp = pathlib.Path(path).expanduser() if row['base'] == 'abs' else folder / path
        tokens[path] = 0
        if fp.is_dir():
            sizes[path] = f'{_line_count(fp)} files'
            continue
        try:
            raw = fp.read_bytes()
        except OSError:
            sizes[path] = f'{row["lines"]} lines'
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            sizes[path] = f'{-(-len(raw) // 1024)} KB'
            continue
        if row['where'] != '-':
            anchor_list = _split_where(row['where'])
            resolved, unresolved = resolve_where(text, anchor_list)
            bad = set(unresolved)
            resolved_iter = iter(resolved)
            spans[path] = [
                None if a in bad else next(resolved_iter) for a in anchor_list]
            if resolved:
                file_lines = text.splitlines()
                tokens[path] = sum(
                    len('\n'.join(file_lines[a - 1:b])) for a, b in resolved) // 4
                sizes[path] = ''
        if path not in sizes:
            tokens[path] = len(text) // 4
            line_cnt = raw.count(b'\n')
            sizes[path] = f'{line_cnt} lines, '
        # Under a thousand the count itself reads better than 0.3k.
        tok = tokens[path]
        sizes[path] += f'{tok / 1000:.1f}k tok' if tok >= 1000 else f'{tok} tok'
    return rows, spans, sizes, tokens


def _assemble_handoff(
    folder: pathlib.Path,
    cursor_text: str,
    header_line: str,
    log_body: str,
    live: dict[str, Row],
    walk: list[tuple[str, str]],
    standing_text: str,
) -> str:
    """Render read/artifacts/standing blocks and return full HANDOFF.md text.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; used to resolve row paths and derive the slug.
    cursor_text : str
        Cursor portion of the handoff (## Task through ## Open questions).
    header_line : str
        'Written: ... | Cycle: N | branch @ sha7 | clean' line.
    log_body : str
        Rendered log body from render_log.
    live : dict[str, Row]
        Latest ledger row per path, with in-memory missing marks applied.
    walk : list[tuple[str, str]]
        Top-level entries as (path, inferred_kind); skip entries included.
    standing_text : str
        Current text of standing.md.

    Returns
    -------
    str
        Complete HANDOFF.md text.
    """
    always_rows, read_spans, read_sizes, _ = read_first_data(folder, live)
    shown = shown_paths(folder, live)
    read_body = render_read(always_rows, read_spans, read_sizes, shown)
    _rh = '## Read first\n' + read_body
    read_block = (
        f'<!-- hq:read {block_sha(_rh)} -->\n'
        f'## Read first\n{read_body}\n<!-- /hq:read -->'
    )
    art_walk = [(n, k) for n, k in walk if k != 'skip']
    artifacts_body = render_artifacts(art_walk, live, folder.name, shown)
    _ah = '## Artifacts\n' + artifacts_body
    artifacts_block = (
        f'<!-- hq:artifacts {block_sha(_ah)} -->\n'
        f'## Artifacts\n{artifacts_body}\n<!-- /hq:artifacts -->'
    )
    s_items, s_sup = _parse_standing(standing_text)
    standing_body = render_standing(s_items, s_sup, folder.name)
    _sh = '## Standing\n' + standing_body
    standing_block = (
        f'<!-- hq:standing {block_sha(_sh)} -->\n'
        f'## Standing\n{standing_body}\n<!-- /hq:standing -->'
    )
    return (
        f'# Handoff: {folder.name}\n\n'
        f'{header_line}\n\n'
        f'{cursor_text.strip()}\n\n'
        f'{read_block}\n\n'
        f'{artifacts_block}\n\n'
        f'{standing_block}\n\n'
        f'## Log\n{log_body}\n'
    )


# ----------------------------------------------------------------------
# Verb implementations
# ----------------------------------------------------------------------


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


def _verb_adopt(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
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
        0 on success; 1 when HANDOFF.md is absent, non-conforming, or
        ledger.tsv already exists.

    Notes
    -----
    - Writes ledger.tsv, standing.md, cycles/manifest.tsv, cycles/c<n>.md,
      and rewrites HANDOFF.md; a failed header check writes nothing.
    """
    handoff_path = folder / 'HANDOFF.md'
    if handoff_path.is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    if not handoff_path.exists():
        print(f'hq adopt: no HANDOFF.md in {folder}')
        return 1
    text = handoff_path.read_text(encoding='utf-8-sig', errors='replace')
    parsed = split_handoff(text)
    if parsed['cycle'] is None:
        ref = (
            pathlib.Path(__file__).resolve().parent.parent
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
    walk = _walk_folder(folder)
    stem_entries = [
        (n, k, (folder / n).stat().st_mtime if (folder / n).exists() else 0.0)
        for n, k in walk if k != 'skip'
        ]
    stem_successors = apply_stem_rule(stem_entries)
    ledger_path = folder / 'ledger.tsv'
    standing_path = folder / 'standing.md'
    if not ledger_path.exists():
        ledger_path.write_text(_LEDGER_HEADER + '\n', encoding='utf-8')
    if not standing_path.exists():
        standing_path.write_text('', encoding='utf-8')
    manifest_path = cycles_dir / 'manifest.tsv'
    if not manifest_path.exists():
        manifest_path.write_text(_MANIFEST_HEADER + '\n', encoding='utf-8')
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
                cur_h2 == 'Key files' and _KF_LABEL_PAT.match(line)):
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
            # edit lands on a notes file; the seeding branches turn it
            # into mention for any other ungated kind.
            rb_over = 'edit'
        elif grade:
            # An ungraded group: the label stays in view, the file is
            # not gated, and the seeding branches keep a gated kind at
            # always.
            rb_over = 'mention'
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
                stored, path_obj, base = _stored_path(folder, str(sibling))
            else:
                stored, path_obj, base = _pointer_path(folder, tok)
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
                            if not resolve_where(pointed_text, [c])[1]]
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
                elif 'mention' in {prev_rb, rb_over}:
                    rb_merged = 'mention'
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
        joined_m = _KF_LABEL_PAT.match(kf_loose_current)
        if joined_m:
            kf_group = joined_m.group(1).lower()
        elif kf_loose_current.endswith(':'):
            # A header the parser does not know, `Read before touching
            # the gateway:`, still ends the group above it: the bullets
            # under it take the header's own text as their group, which
            # grades mention, never the grade of a label the author
            # closed.
            kf_group = kf_loose_current.lower()
        kf_loose.append(kf_loose_current)
        kf_loose_current = ''

    for line in sections_raw.get('Key files', []):
        gm = _KF_LABEL_PAT.match(line)
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
        sha12 = '-' if is_dir else _sha12_path(path_obj)
        n_lines = str(_line_count(path_obj))
        rb = 'always' if kind in {'spec', 'draft'} else 'never'
        status = 'live'
        successor = stem_successors.get(name, '-')
        if successor != '-':
            status = 'superseded'
            rb = 'never'
        kf_label, kf_rb, kf_where = kf_map.get(name, ('-', None, '-'))
        if name in kf_map:
            kf_matched.add(name)
        if kf_rb is not None and successor == '-':
            # Notes:
            # - always applies to every kind; edit only to a notes file.
            # - Any other grade lifts a row that would otherwise read
            #   never to mention, so its label shows in the Artifacts
            #   block, and never demotes a gated kind.
            if kf_rb == 'always' or (kf_rb == 'edit' and kind == 'notes'):
                rb = kf_rb
            elif rb == 'never':
                rb = 'mention'
        row: Row = {
            'cycle': cycle_str, 'ts': ts, 'path': name, 'base': 'folder',
            'kind': kind, 'status': status, 'read_before': rb,
            'successor': successor, 'where': kf_where, 'sha12': sha12,
            'lines': n_lines, 'reason': '-', 'label': kf_label,
            }
        successor_on_disk = successor != '-' and (folder / successor).is_file()
        refusal = check_r1(row, kind, successor_on_disk)
        if refusal:
            row['reason'] = f'refused: {refusal}'
            row['status'] = 'live'
            row['read_before'] = 'always' if kind in {'spec', 'draft'} else 'never'
            print(f'  advisory: R1 {refusal}')
        _append_tsv(ledger_path, LEDGER_FIELDS, row, _LEDGER_HEADER)
        if kf_label != '-':
            seeded_labels.append(kf_label)
        rb_count[row['read_before']] = rb_count.get(row['read_before'], 0) + 1
        if row['status'] == 'live' and row['read_before'] in _GATE_RB:
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
        stored_again, path_obj, base = _pointer_path(folder, stored)
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
        kind, _ = infer_kind(
            path_obj.name, is_dir, first_heading, top_level=at_top,
            kind_dir=kind_dir_of(stored_again, base))
        on_disk = path_obj.exists()
        rb = 'always' if kind in {'spec', 'draft'} else 'never'
        # The grade applies exactly as the walk branch applies it.
        if kf_rb == 'always' or (kf_rb == 'edit' and kind == 'notes'):
            rb = kf_rb
        elif kf_rb is not None and rb == 'never':
            rb = 'mention'
        row = {
            'cycle': cycle_str, 'ts': ts, 'path': stored_again, 'base': base,
            'kind': kind, 'status': 'live' if on_disk else 'missing',
            'read_before': rb if on_disk else 'never',
            'successor': '-', 'where': kf_where,
            'sha12': _sha12_path(path_obj) if path_obj.is_file() else '-',
            'lines': (
                str(_line_count(path_obj)) if is_dir
                else str(_line_count(path_obj)) if on_disk else '-'),
            'reason': '-', 'label': kf_label,
            }
        _append_tsv(ledger_path, LEDGER_FIELDS, row, _LEDGER_HEADER)
        if kf_label != '-':
            seeded_labels.append(kf_label)
        rb_count[row['read_before']] = rb_count.get(row['read_before'], 0) + 1
        if row['status'] == 'live' and row['read_before'] in _GATE_RB:
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
                headline, body = split_headline(content)
            _do_note(folder, anch, current_kind, headline, body)

        # An item starts at any unindented non-empty line, whatever its
        # marker; an indented line continues the item above it. A
        # lead-in wrapped at the column joins as a paragraph does.
        lead_in_open = False
        for idx, line in enumerate(section_lines):
            if not line.strip():
                lead_in_open = False
                continue
            if line.startswith(' ') and bullet_lines:
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
        'Task', 'Now', 'Plan', 'State', 'Environment', 'Open questions',
        }
    cursor_parts: list[str] = []
    for h in ['Task', 'Now', 'Plan', 'State', 'Environment', 'Open questions']:
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

    fresh_rows = _read_tsv(ledger_path, LEDGER_FIELDS)
    fresh_live = latest_rows(fresh_rows)
    fresh_standing_text = (
        standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
        )
    fresh_manifest = _read_tsv(manifest_path, MANIFEST_FIELDS)
    repos = f'{anch["branch"]}@{anch["sha"]}' if anch['branch'] != '-' else '-'
    # Notes:
    # - The row indexes cycles/c<N>.md, so its date, repos, sizes, and
    #   handoff_sha are read off that file, not the rewrite made today.
    # - The archive is read back rather than reusing `text`: a c<N>.md
    #   already on disk is kept, and the row must describe that file.
    # - The header carries no session, so that column stays `-` and the
    #   adopting session rides in the note with the adopt date and sha.
    archive_text = archive.read_text(encoding='utf-8-sig', errors='replace')
    archive_parsed = split_handoff(archive_text)
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
    log_body_f = render_log(fresh_manifest + [{
        'cycle': cycle_str,
        'written': archive_written,
        'repos': archive_repos,
        'log': adopt_log,
        }])
    branch_a = anch['branch']
    sha7_a = anch['sha']
    if branch_a != '-':
        header_line_a = (
            f'Written: {ts[:10]} | Cycle: {cycle}'
            f' | {branch_a} @ {sha7_a}{_dirty_str(anch["dirty"])}'
            )
    else:
        header_line_a = f'Written: {ts[:10]} | Cycle: {cycle}'
    new_handoff = _assemble_handoff(
        folder, cursor_text, header_line_a, log_body_f,
        _reconcile_missing(folder, fresh_live), walk, fresh_standing_text)
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
        'handoff_sha': _sha12_path(archive),
        'ledger_bytes': str(len(lb_final)),
        'ledger_sha': _sha12(lb_final),
        'standing_bytes': str(len(sb_final)),
        'standing_sha': _sha12(sb_final),
        'log': adopt_log,
        'note': adopt_note,
        'rewrite_sha': _sha12(new_handoff.encode()),
        }
    _append_tsv(manifest_path, MANIFEST_FIELDS, adopt_manifest_row, _MANIFEST_HEADER)

    # --- Summary ---
    gated_cnt = len(gated_names)
    never_cnt = rb_count.get('never', 0)
    seeded_cnt = len(_read_tsv(ledger_path, LEDGER_FIELDS))
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
    not_carried = conservation(
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
        orig_missing = conservation(
            orig_path.read_text(encoding='utf-8-sig', errors='replace'),
            union_cursor, union_standing,
            seeded_labels + witnessed + legacy_log + header_tail
            + sibling_texts(folder, fresh_live))
        if orig_missing:
            print(f'conservation vs HANDOFF.orig.md: {len(orig_missing)} lines not carried')
            for line in orig_missing:
                print(f'  not carried: {line.strip()}')
        else:
            print('conservation vs HANDOFF.orig.md: every line carried')
    return 0


def _lock_age(lock: dict[str, str], now: str) -> float | None:
    """Return a lock's age in seconds, or None when its time does not parse.

    Parameters
    ----------
    lock : dict[str, str]
        The parsed lock fields; ``time`` is the ISO timestamp it was
        written at.
    now : str
        The current ISO timestamp.

    Returns
    -------
    float | None
        Seconds from the lock's time to ``now``; None for a lock with no
        parseable time, which is how an unreadable lock arrives.

    Notes
    -----
    - Both times are compared as naive local time; an aware value is
      converted first, since a lock written on the other synced host
      may carry an offset.
    """
    now_dt = datetime.datetime.fromisoformat(now)
    if now_dt.tzinfo is not None:
        now_dt = now_dt.astimezone().replace(tzinfo=None)
    try:
        lock_time = datetime.datetime.fromisoformat(lock.get('time', ''))
    except ValueError:
        return None
    if lock_time.tzinfo is not None:
        lock_time = lock_time.astimezone().replace(tzinfo=None)
    return (now_dt - lock_time).total_seconds()


def _lock_refusal(folder: pathlib.Path, anch: dict, force: bool) -> str:
    """Return the line that refuses begin for a foreign lock, or ''.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder the lock sits in.
    anch : dict
        Anchors for this invocation; supplies session and time.
    force : bool
        True when ``--force`` was given; every lock then passes.

    Returns
    -------
    str
        The refusal line to print, or '' when begin may proceed.

    Notes
    -----
    - An absent lock, this session's own lock, and a foreign lock older
      than two hours pass.
    - A lock that cannot be read or parsed refuses, so a takeover of it
      is always explicit.
    """
    lock_path = folder / '.hq.lock'
    if force or not lock_path.exists():
        return ''
    lock = _read_lock(folder)
    if lock.get('session') == anch['session']:
        return ''
    age = _lock_age(lock, anch['now'])
    if age is None:
        return 'hq begin: lock file unreadable; use --force to take over'
    if age < _TWO_HOURS:
        return (
            f'hq begin: lock held by {lock.get("session")} on'
            f' {lock.get("host")} since {lock.get("time")};'
            ' use --force to take over')
    return ''


def _take_lock(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Acquire .hq.lock, refusing a young or unreadable foreign lock.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder the lock sits in.
    anch : dict
        Anchors for this invocation; supplies session, host, time, cycle.
    argv : argparse.Namespace
        Parsed flags; ``--force`` takes over any lock.

    Returns
    -------
    int
        0 once the lock is this session's, 1 on a refusal.

    Notes
    -----
    - Creation is ``O_CREAT | O_EXCL``, so of two begins racing for an
      absent lock exactly one creates it and the other re-reads and
      applies the same rules.
    - A lock this session already holds is rewritten, carrying its
      ``takeover`` forward, so a second begin still reaches finish with
      the takeover recorded.
    - A lock taken over but not writable is replaced whole, so a lock
      whose permissions were lost cannot hold the folder forever.
    """
    lock_path = folder / '.hq.lock'
    session = anch['session']
    force = getattr(argv, 'force', False)
    created = True
    fd = -1
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        created = False
    takeover_from = ''
    if not created:
        refusal = _lock_refusal(folder, anch, force)
        if refusal:
            print(refusal)
            return 1
        lock = _read_lock(folder)
        if lock.get('session') == session:
            takeover_from = lock.get('takeover', '')
        else:
            takeover_from = lock.get('session') or 'unknown'
            print(f'hq begin: took over from {takeover_from}')
            if _lock_age(lock, anch['now']) is not None:
                print(
                    f'cycle {lock.get("cycle")} begun by {takeover_from}'
                    f' on {lock.get("host")} at {lock.get("time")},'
                    ' never finished')
    fields: dict[str, str] = {
        'slug': folder.name,
        'session': session,
        'host': anch['host'],
        'time': anch['now'],
        'cycle': str(anch['cycle']),
        }
    if takeover_from:
        fields['takeover'] = takeover_from
    content = '\n'.join(f'{k}={v}' for k, v in fields.items()) + '\n'
    if created:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(content)
    else:
        try:
            lock_path.write_text(content, encoding='utf-8')
        except PermissionError:
            lock_path.unlink()
            lock_path.write_text(content, encoding='utf-8')

    # A hand edit since the last finish or adopt: HANDOFF.md no longer
    # hashes to what that write left in it. live_sha reads the rewrite
    # an adopt row records, not the archive its handoff_sha names.
    manifest_rows = _read_tsv(folder / 'cycles' / 'manifest.tsv', MANIFEST_FIELDS)
    handoff = folder / 'HANDOFF.md'
    if manifest_rows and handoff.exists():
        last = manifest_rows[-1]
        if _sha12_path(handoff) != live_sha(last):
            hand_path = folder / 'cycles' / f'c{int(last["cycle"]):02d}.hand.md'
            hand_path.write_bytes(handoff.read_bytes())
            print(
                f'hq begin: HANDOFF.md changed since last finish;'
                f' saved to {hand_path.name}'
                ' - fold anything wanted back into the cursor')

    _print_worklist(folder, anch)
    return 0


def _print_worklist(folder: pathlib.Path, anch: dict) -> None:
    """Print unstamped files, sha-moved, missing, and deferred rows.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to report on.
    anch : dict
        Anchors for this invocation; supplies the closing cycle line.

    Notes
    -----
    - Every class stops at five names and closes with ``... and N more``,
      so a sixth item is counted rather than dropped.
    - A ``skip`` walk entry is a conflicted copy only when its name says
      so; every other ``skip`` entry - a name carrying a tab, carriage
      return, or newline, or a non-regular file - prints as unstampable,
      the separator escaped.
    """
    walk, rows, live, sha_map, manifest, lb, sb = _folder_state(folder)
    for b in witness(manifest[-1] if manifest else None, lb, sb):
        print(f'  {b}')
    unstamped = [(n, k) for n, k in walk if not is_recorded(n, live) and k != 'skip']
    by_kind: dict[str, list[str]] = {}
    for n, k in unstamped:
        by_kind.setdefault(k, []).append(n)
    for k, names in sorted(by_kind.items()):
        shown = ', '.join(names[:5])
        tail = f' ... and {len(names) - 5} more' if len(names) > 5 else ''
        print(f'  unstamped {k} x{len(names)}: {shown}{tail} - stamp each')
    for n, k in walk:
        if k != 'skip':
            continue
        if 'conflicted copy' in n:
            print(f'  conflicted copy: {n} - a sync duplicate; resolve it by hand')
        else:
            shown = n.replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
            print(
                f'  unstampable name: {shown}'
                ' - rename or remove it, or leave it out of the ledger')
    moved = check_r3(list(_reconcile_missing(folder, live).values()), sha_map)
    slug = folder.name
    for row in moved[:5]:
        print(
            f'  sha moved: {row["path"]}'
            f' - re-read it (hq read {slug} {row["path"]}), then re-stamp')
    if len(moved) > 5:
        print(f'  ... and {len(moved) - 5} more')
    missing_live = []
    for path, row in live.items():
        if row['status'] != 'live':
            continue
        if row['base'] == 'abs':
            if not pathlib.Path(path).expanduser().exists():
                missing_live.append(path)
        elif not (folder / path).exists():
            missing_live.append(path)
    for path in missing_live[:5]:
        print(f'  missing live: {path} - hq when {folder.name} {path}')
    if len(missing_live) > 5:
        print(f'  ... and {len(missing_live) - 5} more')
    # A row the ledger stores as missing - a Key files pointer adopt
    # found nowhere - is otherwise a count inside the Artifacts block; a
    # plain re-stamp carries the status forward, so the line names the
    # move that clears it.
    missing_rows = [p for p, r in live.items() if r['status'] == 'missing']
    for path in missing_rows[:5]:
        print(
            f'  missing: {path} - stamp --status live if it is back,'
            ' or --successor / --archive --reason')
    if len(missing_rows) > 5:
        print(f'  ... and {len(missing_rows) - 5} more')
    dangling = dangling_successors(folder, live)
    for path, successor in dangling[:5]:
        print(
            f'  successor missing: {path} -> {successor}'
            ' - name a new successor or archive the row')
    if len(dangling) > 5:
        print(f'  ... and {len(dangling) - 5} more')
    deferred = [path for path, row in live.items() if row['reason'] == 'deferred']
    if deferred:
        shown = ', '.join(deferred[:5])
        tail = f' ... and {len(deferred) - 5} more' if len(deferred) > 5 else ''
        print(f'  deferred x{len(deferred)}: {shown}{tail} - stamp each when decided')
    # An always row with no anchor is a whole file read at every
    # resume; stamp refuses its next re-stamp until it conforms, so an
    # older thread converges as its files are touched.
    for path, row in live.items():
        if (row['status'] == 'live' and row['read_before'] == 'always'
                and row['where'] == '-'):
            print(
                f'  {path} always with no anchor, {row["lines"]} lines'
                ' - stamp --where <heading> or --read-before edit')
    wd_value, wd_lines = resolve_work_dir(folder)
    for line in wd_lines:
        print(line)
    print(work_dir_line(folder, wd_value, first_cycle=anch['cycle'] == 1))
    print(f'cycle {anch["cycle"]} begun by {anch["session"]} on {anch["host"]}')


def _verb_begin(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run begin: create the folder or take the lock on an existing one.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to begin; created when absent.
    anch : dict
        Anchors dict from ``anchors()``; provides session and host.
    argv : argparse.Namespace
        Parsed begin arguments; ``--force`` overrides a foreign lock.

    Returns
    -------
    int
        0 once the lock is held; 1 when a young foreign lock blocks it.
    """
    if (folder / 'HANDOFF.md').is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    no_handoff = not (folder / 'HANDOFF.md').exists()
    no_ledger = not (folder / 'ledger.tsv').exists()
    if not folder.exists() or (no_handoff and no_ledger):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'cycles').mkdir(exist_ok=True)
        (folder / 'HANDOFF.md').write_text(
            f'# Handoff: {folder.name}\n\n'
            f'Written: {anch["now"][:10]} | Cycle: 1\n\n'
            '## Task\n\n## Now\n\n## Plan\n\n## State\n\n'
            '## Environment\n\n## Open questions\n\n## Log\n',
            encoding='utf-8')
        (folder / 'ledger.tsv').write_text(_LEDGER_HEADER + '\n', encoding='utf-8')
        (folder / 'standing.md').write_text('', encoding='utf-8')
        (folder / 'cycles' / 'manifest.tsv').write_text(
            _MANIFEST_HEADER + '\n', encoding='utf-8')
        print(f'hq begin: created {folder}')
    elif (folder / 'HANDOFF.md').exists() and not (folder / 'ledger.tsv').exists():
        refusal = _lock_refusal(folder, anch, getattr(argv, 'force', False))
        if refusal:
            print(refusal)
            return 1
        rc = _verb_adopt(folder, anch, argv)
        if rc != 0:
            return rc
        print('hq begin: ran adopt on existing HANDOFF.md')
        anch = anchors(folder, argv)
    return _take_lock(folder, anch, argv)


def _do_stamp(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Write one ledger row after applying R1 and defer checks.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing ledger.tsv.
    anch : dict
        Anchors dict from ``anchors()``; provides cycle and timestamp.
    argv : argparse.Namespace
        Parsed stamp arguments: path, kind, read_before, status, etc.

    Returns
    -------
    int
        0 on success; 1 when R1 refuses the stamp; 2 on bad usage.

    Notes
    -----
    - A refusal appends a receipt row; a successful stamp appends the
      row; a ``--defer`` stamps with kind ``other`` and reason
      ``deferred``.
    - A row that would sit live at ``always`` with no ``--where`` is
      refused whatever its kind, and the file's headings print under
      the refusal so the next stamp can anchor it.
    - A written row shorter than its comparand, or missing one of its
      backticked tokens or ``s<n>`` references, draws an advisory
      here, where retyping the label costs nothing; the row stands
      either way, and both lines can fire at once.
    - The comparand is the last label an earlier cycle left, or the
      previous stamp of this cycle where the path has no earlier row.
    """
    path = getattr(argv, 'path', '') or ''
    if not path:
        print('hq stamp: path is required')
        return 2
    if '\t' in path or '\n' in path or '\r' in path:
        print('hq stamp: path may not contain a tab, newline, or carriage return')
        return 2
    ledger_path = folder / 'ledger.tsv'
    rows = _read_tsv(ledger_path, LEDGER_FIELDS)
    live = latest_rows(rows)
    stored_path, path_obj, base = _stored_path(folder, path)
    prev = live.get(stored_path, {})
    # Notes:
    # - stamp never searches, so a relative token the folder does not
    #   hold is a repo path typed folder-relative, not a file to record:
    #   the row would sit live with no sha until finish called it
    #   missing.
    # - An abs path not on disk is a file on another host or one still
    #   to come, and stays an advisory at begin and finish.
    # - A path gone on purpose is recorded with --status missing,
    #   --archive, or --successor, which pass; a path with a row already
    #   is a re-stamp, judged by R1 and R3.
    recorded_on_purpose = (
        getattr(argv, 'status', None) in {'missing', 'archived'}
        or getattr(argv, 'archive', False)
        or bool(getattr(argv, 'successor', None)))
    never_stamped = all(row['path'] != stored_path for row in rows)
    if (never_stamped and base == 'folder' and not path_obj.exists()
            and not recorded_on_purpose):
        if path.strip('`').startswith(('/', '~')):
            print(
                f'hq stamp: {stored_path}: no such file under {folder}'
                ' - create it first, or record it gone with --status missing')
        else:
            print(
                f'hq stamp: {stored_path}: no such file under {folder}'
                ' - a file outside the folder is stamped by its ~ or absolute path')
        return 2
    if base == 'folder' and stored_path in _KIND_DIRS and path_obj.is_dir():
        print(
            f'hq stamp: {stored_path} is a kind folder'
            ' - stamp the files under it, each by its own path')
        return 2
    is_dir = path_obj.is_dir()
    if not is_dir and path_obj.exists() and not path_obj.is_file():
        print(
            f'hq stamp: {path} is not a regular file or directory'
            ' - rename it or leave it out of the ledger')
        return 2
    first_heading = ''
    if not is_dir and path_obj.exists():
        try:
            with path_obj.open(encoding='utf-8', errors='replace') as fh:
                for ln in fh:
                    if ln.startswith('#'):
                        first_heading = ln.strip()
                        break
        except OSError:
            pass
    # The draft rule names the folder's top level alone: a .py nested in
    # the folder or outside it infers other, gated by --kind draft.
    top_level = base == 'folder' and '/' not in stored_path
    inferred_kind, seed_rb = infer_kind(
        path_obj.name, is_dir, first_heading, top_level,
        kind_dir_of(stored_path, base))
    history = [row for row in rows if row['path'] == stored_path]
    # Notes:
    # - R1 binds to every kind the path has ever carried, so an
    #   --archive that also passes --kind other cannot take a
    #   once-declared spec out of the gate.
    # - A kind the agent declares with --kind counts too: a file
    #   called a spec is gated from that stamp on.
    gate_kind = inferred_kind
    if gate_kind not in {'spec', 'draft'}:
        declared = [getattr(argv, 'kind', None)] + [row['kind'] for row in history]
        gate_kind = next(
            (k for k in declared if k in {'spec', 'draft'}), gate_kind)
    successor_given = getattr(argv, 'successor', None)
    successor = (
        _stored_path(folder, successor_given)[0] if successor_given
        else prev.get('successor', '-') or '-')
    successor_path = _successor_path(folder, successor)
    # Notes:
    # - R1 judges the resulting row, so a successor carried forward
    #   from the previous row counts, re-checked every stamp.
    # - A directory, or the artifact itself under any spelling, is
    #   not a successor.
    successor_on_disk = (
        successor != '-'
        and successor_path.is_file()
        and successor_path.resolve() != path_obj.resolve())
    # A refusal receipt records an attempt, not a state; its reason is
    # skipped when carrying reason forward.
    carried_reason = next(
        (row['reason'] for row in reversed(history)
         if not row['reason'].startswith('refused: ')),
        '-')
    if (getattr(argv, 'reason', None) or '').startswith('refused: '):
        print("hq stamp: --reason may not start with 'refused: ', the receipt prefix")
        return 2
    if getattr(argv, 'archive', False) and not getattr(argv, 'reason', None):
        print('hq stamp: --archive requires --reason')
        return 2
    if (getattr(argv, 'status', None) == 'archived'
            and not getattr(argv, 'reason', None)):
        print('hq stamp: --status archived requires --reason')
        return 2
    defer = getattr(argv, 'defer', False)
    refusal_reason: str | None = None
    if defer and gate_kind in {'spec', 'draft'}:
        refusal_reason = f'refused: --defer not allowed for inferred {gate_kind}'
    # A non-live successor would let two specs name each other and
    # leave nothing gated; an explicit one is refused.
    if refusal_reason is None and successor_given:
        successor_row = live.get(successor)
        if successor_row is not None and successor_row['status'] != 'live':
            refusal_reason = (
                f'refused: R1: successor {successor} is'
                f' {successor_row["status"]}, not live')
    if refusal_reason is None and not defer:
        kind = getattr(argv, 'kind', None) or prev.get('kind', inferred_kind)
        explicit_rb = getattr(argv, 'read_before', None)
        rb = explicit_rb or prev.get('read_before', _TIER_SEED.get(gate_kind, 'never'))
        explicit_status = getattr(argv, 'status', None)
        status = explicit_status or prev.get('status', 'live')
        # --status archived and --archive are the same demotion and
        # must land the same row: the two spellings cannot drift.
        if getattr(argv, 'archive', False) or status == 'archived':
            status = 'archived'
            rb = 'never'
        elif successor_given and successor != '-':
            if not explicit_status:
                status = 'superseded'
            if not explicit_rb:
                rb = 'never'
        # A reason explains the state it was given with; it carries
        # only while kind, status, and read_before hold. A transition
        # needs its own reason.
        state_holds = (kind, status, rb) == (
            prev.get('kind', inferred_kind),
            prev.get('status', 'live'),
            prev.get('read_before', _TIER_SEED.get(gate_kind, 'never')))
        reason = getattr(argv, 'reason', None) or (
            carried_reason if state_holds else '-')
    label = getattr(argv, 'label', None) or prev.get('label', '-')
    where = getattr(argv, 'where', None) or prev.get('where', '-')
    sha12 = _sha12_path(path_obj) if path_obj.is_file() else '-'
    n_lines = str(_line_count(path_obj)) if path_obj.exists() else '-'
    if refusal_reason is None and not defer:
        new_row: Row = {
            'cycle': str(anch['cycle']), 'ts': anch['now'],
            'path': stored_path, 'base': base,
            'kind': kind, 'status': status, 'read_before': rb,
            'successor': successor, 'where': where, 'sha12': sha12,
            'lines': n_lines, 'reason': reason, 'label': label,
            }
        r1_result = check_r1(new_row, gate_kind, successor_on_disk)
        if r1_result:
            refusal_reason = f'refused: {r1_result}'
        elif status == 'live' and rb == 'always' and where == '-':
            # always is an anchored span read at every resume; a whole
            # file at that tier is an edit row read too early.
            refusal_reason = f'refused: {stored_path} always with no anchor'
    if refusal_reason:
        # Notes:
        # - A receipt verifies nothing: it keeps the previous sha so
        #   a failed stamp cannot clear the R3 gate.
        # - With no previous row it seeds read_before from the kind
        #   table, so a refusal never promotes an ungated file into
        #   the read block, and records the sha on disk so R3 covers
        #   the file from here on.
        refused_row: Row = {
            'cycle': str(anch['cycle']), 'ts': anch['now'],
            'path': stored_path, 'base': base,
            'kind': prev.get('kind', inferred_kind),
            'status': prev.get('status', 'live'),
            'read_before': prev.get('read_before', seed_rb),
            'successor': prev.get('successor', '-'),
            'where': prev.get('where', '-'),
            'sha12': prev.get('sha12', sha12),
            'lines': prev.get('lines', n_lines),
            'reason': refusal_reason,
            'label': prev.get('label', '-'),
            }
        _append_tsv(ledger_path, LEDGER_FIELDS, refused_row, _LEDGER_HEADER)
        if refusal_reason.endswith('always with no anchor'):
            print(
                f'hq stamp: {refusal_reason} - {n_lines} lines read whole at'
                ' every resume; stamp --where <heading> or --read-before edit')
            try:
                for ln in path_obj.read_text(
                        encoding='utf-8', errors='replace').splitlines():
                    if ln.startswith('#'):
                        print(f'  {ln.rstrip()}')
            except OSError:
                pass
        else:
            print(
                f'hq stamp: {refusal_reason}'
                ' - supply a live --successor or --archive --reason,'
                ' or leave the row gated')
        return 1
    if defer:
        new_row = {
            'cycle': str(anch['cycle']), 'ts': anch['now'],
            'path': stored_path, 'base': base,
            'kind': 'other', 'status': prev.get('status', 'live'),
            'read_before': 'never', 'successor': successor, 'where': where,
            'sha12': sha12, 'lines': n_lines, 'reason': 'deferred',
            'label': label,
            }
    _append_tsv(ledger_path, LEDGER_FIELDS, new_row, _LEDGER_HEADER)
    # Notes:
    # - The comparand is the last row of an earlier cycle, so two
    #   re-stamps in one cycle are both judged against what the
    #   previous cycle left; with none, against the earlier re-stamp.
    # - Backticks are markup, not content, so both tests read the
    #   label with them stripped: an identifier the new label spells
    #   plainly is neither a loss nor a shortening, and a formatting
    #   change reported as an omission cannot be told from a real one.
    # - A token counts as kept only where it stands on its own.
    #   Characters of it inside a longer identifier name something
    #   else, and the old name is gone.
    # - Both lines can fire on one re-stamp: the dropped line is what
    #   the operator retypes, the shorter line what is left unnamed.
    cycle_now = str(anch['cycle'])
    prev_row = next(
        (r for r in reversed(history) if r['cycle'] != cycle_now),
        history[-1] if history else None)
    prev_label = prev_row['label'] if prev_row else '-'
    curr_label = new_row['label']
    if prev_label == '-' or curr_label == '-':
        return 0
    prev_plain = prev_label.replace('`', '')
    curr_plain = curr_label.replace('`', '')
    if len(curr_plain) < len(prev_plain):
        print(
            f'advisory: label shorter than predecessor: {stored_path}'
            ' - check the new label still carries what the old one said')
    dropped = {
        f'`{token}`' for token in re.findall(r'`([^`]+)`', prev_label)
        if not re.search(
            r'(?<![0-9A-Za-z_])' + re.escape(token) + r'(?![0-9A-Za-z_])',
            curr_plain)
        }
    dropped |= (
        set(re.findall(r'\bs\d+\b', prev_label))
        - set(re.findall(r'\bs\d+\b', curr_label)))
    if dropped:
        print(
            f'advisory: label dropped {dropped!r}: {stored_path}'
            ' - put it back or accept the loss')
    return 0


def _verb_stamp(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run stamp: record a ledger row for one artifact (or batch from stdin).

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing ledger.tsv.
    anch : dict
        Anchors dict from ``anchors()``; provides cycle and timestamp.
    argv : argparse.Namespace
        Parsed stamp arguments; ``--batch`` reads lines from stdin.

    Returns
    -------
    int
        0 when every row was written; 1 when any R1 refusal fires;
        2 on a bad path or unparseable batch line.
    """
    if getattr(argv, 'batch', False):
        rc = 0
        parser = _build_parser()
        slug = folder.name
        for line_no, raw_line in enumerate(sys.stdin, 1):
            line = raw_line.strip()
            if not line or line == '-':
                continue
            # The batch reports a bad line by number on stdout, so the
            # parser's own usage block is swallowed.
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    sub = parser.parse_args(['stamp', slug] + shlex.split(line))
                except (SystemExit, ValueError):
                    sub = None
            if sub is None or not getattr(sub, 'path', ''):
                print(
                    f'hq stamp: batch line {line_no} not parsed: {line}'
                    ' - fix that line and re-run it alone; the other lines ran')
                rc = max(rc, 2)
                continue
            rc = max(rc, _do_stamp(folder, anch, sub))
        return rc
    return _do_stamp(folder, anch, argv)


def _note_line(
    standing_text: str,
    kind_str: str,
    headline: str,
    body: str,
    cycle: int | str,
) -> str | None:
    """Format one standing item with the next free id for its kind.

    Parameters
    ----------
    standing_text : str
        Current text of standing.md, which fixes the ids in use.
    kind_str : str
        Item kind: 'decision', 'constraint', or 'dead-end'.
    headline : str
        Bold-span headline text for the item. A tab or a newline becomes
        a space.
    body : str
        Body text following the headline; may be empty. A newline becomes
        a space.
    cycle : int | str
        Cycle number recorded on the item.

    Returns
    -------
    str | None
        The item line without its newline, or ``None`` for an unknown kind.

    Notes
    -----
    - The item is one physical line: a separator left in the headline
      would split it, and every reader of ``standing.md`` - the block,
      ``standing``, ``supersede``, and the next id - reads line by line.
    """
    prefix_map = {'decision': 'd', 'constraint': 'c', 'dead-end': 'x'}
    prefix = prefix_map.get(kind_str)
    if not prefix:
        return None
    items, _ = _parse_standing(standing_text)
    item_id = _next_id(items, prefix)
    headline_clean = ' '.join(headline.split())
    body_clean = body.replace('\n', ' ')
    bold = f'**{headline_clean}**'
    return f'- [{item_id}] (c{cycle}) {_join_headline_body(bold, body_clean)}'.rstrip()


def _do_note(
    folder: pathlib.Path,
    anch: dict,
    kind_str: str,
    headline: str,
    body: str,
) -> int:
    """Append one standing item to standing.md.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing standing.md.
    anch : dict
        Anchors dict from anchors(); provides the current cycle number.
    kind_str : str
        Item kind: 'decision', 'constraint', or 'dead-end'.
    headline : str
        Bold-span headline text for the item.
    body : str
        Body text following the headline; may be empty.

    Returns
    -------
    int
        0 on success, 2 on an unknown kind or an empty headline, writing
        nothing in either case.

    Notes
    -----
    - The headline is required: an item with an empty bold span is
      invisible to ``_parse_standing``, so the block, ``standing``, and
      ``supersede`` never see it while ``_next_id`` reissues its id.
    """
    standing_path = folder / 'standing.md'
    text = (
        standing_path.read_text(encoding='utf-8', errors='replace')
        if standing_path.exists() else ''
        )
    line = _note_line(text, kind_str, headline, body, anch['cycle'])
    if line is None:
        print(f'hq note: kind must be decision, constraint, or dead-end; got {kind_str!r}')
        return 2
    if not re.search(r'\w', headline):
        print('hq note: --headline is required')
        return 2
    _append_lines(standing_path, [line])
    return 0


def _verb_note(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run note: append one item to standing.md (or batch from stdin).

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder holding standing.md.
    anch : dict
        Anchors dict from anchors(); provides the current cycle number.
    argv : argparse.Namespace
        Parsed note arguments: note_kind, headline, body, batch.

    Returns
    -------
    int
        0 when every item was appended, 2 when any was refused.

    Notes
    -----
    - Under ``--batch`` the kind slot holds the batch marker ``-`` (or
      nothing) and each stdin line reads ``<kind> --headline <headline>
      <body>``: the headline quoted or one bare word, the body the rest
      of the line verbatim. Prose is not shell: an apostrophe or a
      parenthesis in the body carries no meaning here, and a body
      wrapped whole in one pair of quotes loses only that pair.
    - A batch line that does not parse, names no known kind, or carries
      no headline is reported by number; the rest still run.
    """
    if getattr(argv, 'batch', False):
        rc = 0
        for line_no, raw_line in enumerate(sys.stdin, 1):
            line = raw_line.strip()
            if not line or line == '-':
                continue
            match = _NOTE_BATCH_LINE.match(line)
            if match is None:
                print(
                    f'hq note: batch line {line_no} not parsed: {line}'
                    ' - write it as <kind> --headline "<headline>" <body>'
                    ' and re-run it alone; the other lines ran')
                rc = max(rc, 2)
                continue
            kind_str = match.group('kind')
            headline = next(
                g for g in match.group('dq', 'sq', 'bare') if g is not None)
            body = match.group('body').strip()
            if len(body) >= 2 and body[0] == body[-1] and body[0] in '"\'':
                if body[0] not in body[1:-1]:
                    body = body[1:-1]
            rc = max(rc, _do_note(folder, anch, kind_str, headline, body.strip()))
        return rc
    return _do_note(
        folder, anch,
        getattr(argv, 'note_kind', '') or '',
        getattr(argv, 'headline', '') or '',
        getattr(argv, 'body', '') or '')


def _verb_supersede(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run supersede: append a supersession line to standing.md.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing standing.md.
    anch : dict
        Anchors dict from ``anchors()``; provides the current cycle.
    argv : argparse.Namespace
        Parsed supersede arguments: old_id, new_id.

    Returns
    -------
    int
        0 on success; 1 when either id is absent or the prefixes differ.

    Notes
    -----
    - Appends one line to standing.md; writes nothing on refusal.
    - Prints the superseded item as standing.md holds it: the block
      renders a decision or a dead end by its headline alone, so a
      ruling still live inside the body would otherwise leave unseen.
    """
    old_id = getattr(argv, 'old_id', '')
    new_id = getattr(argv, 'new_id', '')
    if not old_id or not new_id or old_id[0] != new_id[0]:
        print(f'hq supersede: ids must share a prefix ({old_id!r} vs {new_id!r})')
        return 1
    # An id superseded by itself leaves no id now current for hq standing
    # to name, and drops the item from the block with nothing replacing it.
    if old_id == new_id:
        print(
            f'hq supersede: {old_id} cannot supersede itself'
            ' - name the item that replaces it, or hq note one first')
        return 1
    standing_path = folder / 'standing.md'
    text = standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
    items, _ = _parse_standing(text)
    all_ids = {item['id'] for item in items}
    if old_id not in all_ids:
        print(
            f'hq supersede: {old_id!r} not found in standing.md'
            f'; run hq standing {folder.name} to list the ids')
        return 1
    if new_id not in all_ids:
        print(
            f'hq supersede: {new_id!r} not found in standing.md'
            f'; run hq standing {folder.name} to list the ids')
        return 1
    line = f'- (c{anch["cycle"]}) {old_id} -> {new_id}'
    _append_lines(standing_path, [line])
    # The block shows a decision or a dead end by its headline alone, so
    # a ruling still live inside the body would leave unseen; the item
    # is echoed as standing.md holds it on its way out.
    old_item = next(item for item in items if item['id'] == old_id)
    pfx = f'(c{old_item["cycle"]}) ' if old_item['cycle'] else ''
    shown = f'[{old_id}] {pfx}**{old_item["headline"]}** {old_item["body"]}'
    print(f'dropped from the block: {shown.rstrip()}')
    return 0


def _verb_finish(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run finish: check all conditions, then write everything in one pass.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to close the cycle in.
    anch : dict
        Anchors for this invocation.
    argv : argparse.Namespace
        Parsed flags; ``--log`` is required and ``--acknowledge`` passes a
        witness break.

    Returns
    -------
    int
        0 once everything is written, 1 on the first class that fires.

    Notes
    -----
    - Check order (design section 5, step 7): the lock is not this
      session's; W1 or W2; an R3 sha mismatch; a live gated row absent
      from disk; an ``## Unfiled`` section below the first block marker;
      an untyped ``## Unfiled`` bullet.
    - Nothing is written until every check passes, so a failed run leaves
      the folder byte-identical and a re-run appends nothing twice.
    """
    if (folder / 'HANDOFF.md').is_dir():
        print('hq: HANDOFF.md is a directory - move the directory aside and re-run')
        return 1
    lock = _read_lock(folder)
    session = anch['session']
    if lock.get('session') != session:
        print(
            f'hq finish: lock held by {lock.get("session", "none")}; this is {session}'
            f' - run hq begin {folder.name} first')
        return 1
    ledger_path = folder / 'ledger.tsv'
    standing_path = folder / 'standing.md'
    manifest_path = folder / 'cycles' / 'manifest.tsv'
    walk, rows, live, sha_map, manifest, lb, sb = _folder_state(folder)
    ack = ' '.join((getattr(argv, 'acknowledge', None) or '').split())
    breaks = witness(manifest[-1] if manifest else None, lb, sb)
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
        if r['status'] == 'live' and r['read_before'] in _GATE_RB
        and r['base'] == 'folder' and not (folder / r['path']).exists()
        ]
    # A row lifted from missing back to live faces R3 like any other.
    live = _reconcile_missing(folder, live)
    r3_rows = check_r3(list(live.values()), sha_map)
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
    parsed = split_handoff(handoff_text)
    items, cursor_clean, refusal = drain_unfiled(parsed['cursor'])
    if refusal:
        print(f'hq finish: {refusal}')
        return 1
    # --- Advisory: collisions ---
    now_m = re.search(r'^## Now\s*$', cursor_clean, re.MULTILINE)
    if now_m:
        rest = cursor_clean[now_m.end():]
        nm2 = re.search(r'^## ', rest, re.MULTILINE)
        now_text = rest[:nm2.start()] if nm2 else rest
        standing_text_adv = (
            standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
            )
        s_items_adv, s_sup_adv = _parse_standing(standing_text_adv)
        dead_end_headlines = [
            (i['headline'], i['id']) for i in s_items_adv
            if i['prefix'] == 'x' and i['id'] not in s_sup_adv
            ]
        spec_headings: list[tuple[str, int, str]] = []
        now_terms = _extract_terms(now_text)
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
            # A live spec heading uses the ledger kind and status when
            # a row exists, the inferred kind for an unstamped file.
            row = live.get(name)
            is_live_spec = (
                row['kind'] == 'spec' and row['status'] == 'live' if row
                else kind == 'spec')
            if not is_live_spec:
                continue
            for i, ln in enumerate(file_text.splitlines()):
                if ln.startswith('#'):
                    spec_headings.append((name, i + 1, ln))
        for hit in collisions(now_text, dead_end_headlines, spec_headings, rarity):
            print(
                f'advisory: {hit}'
                ' - check that the Now step does not retry a rejected idea')
    # --- Advisory: unstamped ---
    unstamped_entries = [
        (n, k) for n, k in walk if not is_recorded(n, live) and k != 'skip']
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
    # - Only a path first stamped this cycle is named: an older row was
    #   placed under an earlier ruling, and moving it is the agent's
    #   call.
    # - A directory of the agent's own name is an organized unit under
    #   either ruling, so only a loose file at the top level is named -
    #   and, under a pin, a file in specs/, drafts/, or outputs/, whose
    #   place the pin claims.
    wd_value, wd_lines = resolve_work_dir(folder)
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
        and first_cycle.get(path) == int(anch['cycle'])
        and ('/' not in path or kind_dir_of(path, 'folder') in claimed_dirs)]
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
    # A skip entry the ledger cannot carry: a sync duplicate by name
    # or a name holding a tab or newline, shown escaped.
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
    for p_key, successor in dangling_successors(folder, live):
        print(
            f'advisory: successor missing: {p_key} -> {successor}'
            ' - name a new successor or archive the row')
    # --- Advisory: one-line spans ---
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
        spans, _ = resolve_where(span_text, _split_where(row['where']))
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
    # The drained items are formatted in memory: nothing touches disk
    # until the whole handoff text exists, so a failure writes nothing
    # and a re-run appends nothing twice.
    standing_text_new = (
        standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
        )
    new_note_lines: list[str] = []
    for kind_str, headline, body in items:
        line = _note_line(standing_text_new, kind_str, headline, body, anch['cycle'])
        if line is None:
            print(f'hq finish: unknown item kind {kind_str!r}')
            return 1
        new_note_lines.append(line)
        standing_text_new += line + '\n'
    # --- Advisory: cursor lines not carried ---
    # Notes:
    # - The cursor is rewritten every cycle, so the previous archive is
    #   the one record of what it held; a line that neither the new
    #   cursor, standing.md, a live label, nor a rehome sibling carries
    #   is named here, and the agent settles whether it was done or lost.
    # - A cursor heading carries no fact, so a section omitted as empty
    #   is not reported by its heading.
    if manifest:
        prev_cycle = int(manifest[-1]['cycle'])
        prev_archive = folder / 'cycles' / f'c{prev_cycle:02d}.md'
        if prev_archive.is_file():
            prev_cursor = split_handoff(
                prev_archive.read_text(encoding='utf-8', errors='replace'))['cursor']
            live_labels = [
                row['label'] for row in live.values()
                if row['status'] == 'live' and row.get('label', '-') != '-']
            dropped_lines = [
                ln for ln in conservation(
                    prev_cursor, cursor_clean, standing_text_new,
                    live_labels + sibling_texts(folder, live))
                if not ln.lstrip().startswith('#')]
            if dropped_lines:
                print(
                    f'advisory: {len(dropped_lines)} cursor lines from'
                    f' c{prev_cycle:02d} not carried'
                    ' - confirm each was settled or moved, else carry it forward'
                    ' or rehome it; hq diff'
                    f' {folder.name} {prev_cycle} {anch["cycle"]}'
                    ' shows the whole change')
                for dropped in dropped_lines:
                    print(f'  not carried: {dropped.strip()}')
    log_text = ' '.join((getattr(argv, 'log', '') or '').split())
    branch, sha7, dirty = anch['branch'], anch['sha'], anch['dirty']
    if branch != '-':
        header_line = (
            f'Written: {anch["now"][:10]} | Cycle: {anch["cycle"]}'
            f' | {branch} @ {sha7}{_dirty_str(dirty)}'
            )
    else:
        header_line = f'Written: {anch["now"][:10]} | Cycle: {anch["cycle"]}'
    repos = f'{branch}@{sha7}' if branch != '-' else '-'
    if dirty:
        repos += f' +{len(dirty)}'
    log_body = render_log(manifest + [{
        'cycle': str(anch['cycle']),
        'written': anch['now'][:10],
        'repos': repos,
        'log': log_text,
        }])
    new_handoff = _assemble_handoff(
        folder, cursor_clean, header_line, log_body,
        live, walk, standing_text_new)
    (folder / 'cycles').mkdir(exist_ok=True)
    if new_note_lines:
        _append_lines(standing_path, new_note_lines)
    handoff_path.write_text(new_handoff, encoding='utf-8')
    cycle_archive = folder / 'cycles' / f'c{anch["cycle"]:02d}.md'
    cycle_archive.write_text(new_handoff, encoding='utf-8')
    lb_final = ledger_path.read_bytes()
    sb_final = standing_path.read_bytes() if standing_path.exists() else b''
    handoff_sha = _sha12(new_handoff.encode())
    cursor_lines = len(cursor_clean.splitlines())
    payload_tokens = len(new_handoff) // 4
    blocks_new = split_handoff(new_handoff).get('blocks', {})
    token_split = ', '.join(
        f'{name} {len(blocks_new.get(name, "")) // 4}'
        for name in ('read', 'artifacts', 'standing'))
    takeover = ' '.join(lock.get('takeover', '').split())
    manifest_session = f'{session} took over {takeover}' if takeover else session
    note_val = f'acknowledged {"; ".join(breaks)}; {ack}' if breaks and ack else '-'
    manifest_row: dict = {
        'cycle': str(anch['cycle']),
        'written': anch['now'][:10],
        'session': manifest_session,
        'repos': repos,
        'cursor_lines': str(cursor_lines),
        'payload_tokens': str(payload_tokens),
        'handoff_sha': handoff_sha,
        'ledger_bytes': str(len(lb_final)),
        'ledger_sha': _sha12(lb_final),
        'standing_bytes': str(len(sb_final)),
        'standing_sha': _sha12(sb_final),
        'log': log_text,
        'note': note_val,
        }
    _append_tsv(manifest_path, MANIFEST_FIELDS, manifest_row, _MANIFEST_HEADER)
    (folder / '.hq.lock').unlink(missing_ok=True)
    print(
        f'{handoff_path}  {cursor_lines} cursor lines  {payload_tokens} tokens'
        f' (cursor {len(cursor_clean) // 4}, {token_split})')
    rf_rows, rf_spans, _, rf_tokens = read_first_data(folder, live)
    anchored = sum(
        1 for r in rf_rows
        if any(s is not None for s in rf_spans.get(r['path'], [])))
    print(
        f'read first: {len(rf_rows)} rows, {sum(rf_tokens.values())} tok'
        f' ({anchored} anchored, {len(rf_rows) - anchored} whole)')
    print(f'resume: /handoff {folder.name}')
    return 0


def _verb_open(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run open: print status, drift, W1/W2, sha-moved rows, and stale paths.

    A label the rendered block and the ledger disagree on is one of the
    drift classes reported.

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
    walk, rows, live, sha_map, manifest, lb, sb = _folder_state(folder)
    for b in witness(manifest[-1] if manifest else None, lb, sb):
        print(f'WARNING: {b}')
    lock = _read_lock(folder)
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
    parsed = split_handoff(hf_text)
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
    # - Both sides are compared stripped of trailing space: the block
    #   body loses it at its last line, and the ledger keeps it.
    shown = shown_paths(folder, live)
    artifacts_body = parsed.get('blocks', {}).get('artifacts', '')
    own_cycle = bool(lock) and lock.get('session') == anch['session']
    labeled = [] if own_cycle else [
        (p_key, row, shown.get(p_key, (p_key, ''))[0])
        for p_key, row in sorted(live.items())
        if row['status'] == 'live' and row['label'] != '-']
    displays = [display for _, _, display in labeled]
    for p_key, row, display in labeled:
        if displays.count(display) > 1:
            continue
        rendered = re.findall(
            '^' + re.escape(display) + r'  \S+  \S+  c\d+  (.*)$',
            artifacts_body, re.MULTILINE)
        if len(rendered) == 1 and rendered[0].rstrip() != row['label'].rstrip():
            print(
                f'LEDGER BEHIND: block label differs: {p_key}'
                ' - the block and the ledger disagree; trust the ledger,'
                ' and the next finish re-renders the block')
    for row in check_r3(list(_reconcile_missing(folder, live).values()), sha_map):
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
                f' - run git log --oneline {h_sha}..HEAD,'
                ' and -- <todo path> for each todo file the Plan points at')
    block_open_pat = re.compile(r'^<!-- hq:(\w+) ([0-9a-f]+) -->', re.MULTILINE)
    for m in block_open_pat.finditer(hf_text):
        bname, stored_sha = m.group(1), m.group(2)
        body = parsed.get('blocks', {}).get(bname, '')
        computed = block_sha(body)
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
        anchor_list = _split_where(row['where'])
        new_spans, unresolved = resolve_where(file_text, anchor_list)
        for anchor_str in unresolved:
            print(
                f'unresolved anchor in {row["path"]}: {anchor_str!r}'
                ' - read the whole file; at write time re-stamp with a --where'
                ' that resolves (hq help anchors), then re-run open')
        # Report spans that moved since the stored read block, which
        # prints a path under the root or the pin relative to it.
        read_block_body = parsed.get('blocks', {}).get('read', '')
        display = shown.get(row['path'], (row['path'], ''))[0]
        for line in read_block_body.splitlines():
            if not line.startswith(display + ':'):
                continue
            m2 = re.match(re.escape(display) + r':([0-9?,\-]+)', line)
            if not m2:
                continue
            # An unresolved anchor prints ? in its slot; only resolved
            # slots are compared.
            old_spans = []
            for ss in m2.group(1).split(','):
                parts = ss.split('-')
                if len(parts) == 2:
                    try:
                        old_spans.append((int(parts[0]), int(parts[1])))
                    except ValueError:
                        pass
            if old_spans and new_spans and old_spans != new_spans:
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
    # - A superseded standing item is skipped: the file is append-only,
    #   so the old wording stays on disk after the re-note, and counting
    #   it would leave the line with no move that clears it.
    # A work dir pinned under or inside a former name is a real place:
    # its per-thread paths are not stale, so that name leaves the scan.
    wd_value, _ = resolve_work_dir(folder)
    pinned_parts = pathlib.PurePosixPath(wd_value).parts
    former = [d for d in _FORMER_HANDOFF_DIRNAMES if d not in pinned_parts]
    stale_pat = re.compile(
        r'(' + '|'.join(re.escape(d) for d in former) + r')/'
        + re.escape(folder.name) + r'/' if former else r'(?!)')
    standing_text = sb.decode('utf-8', 'replace')
    _, superseded_ids = _parse_standing(standing_text)
    standing_live_lines: list[str] = []
    for line in standing_text.splitlines():
        id_m = re.match(r'\s*- \[([dcx]\d+)\]', line)
        if id_m and id_m.group(1) in superseded_ids:
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
    return 0


def _ledger_key(folder: pathlib.Path, token: str, known: set[str]) -> str | None:
    """Resolve a read-only verb's path token to the ledger key it names.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the root is its grandparent and the pin its
        ``work-dir`` file.
    token : str
        The path as typed: folder-relative, root-relative, pin-relative,
        or by ``~`` or absolutely.
    known : set[str]
        Every path the ledger holds a row for.

    Returns
    -------
    str | None
        The stored path whose row the token names, or ``None`` when no
        spelling of it has a row.

    Notes
    -----
    - A relative token is tried as ``stamp`` stores it, then against the
      project root, then against the pinned work dir, so a repo file
      is found by the path the cursor names. ``stamp`` never searches:
      this is the read-only verbs' courtesy alone.
    """
    stored = _stored_path(folder, token)[0]
    if stored in known:
        return stored
    clean = token.strip('`')
    if clean.startswith(('/', '~')):
        return None
    root = folder.parent.parent
    bases = [root]
    pin, _ = resolve_work_dir(folder)
    if pin:
        bases.append(
            pathlib.Path(os.path.expanduser(pin)) if pin.startswith(('/', '~'))
            else root / pin)
    for base in bases:
        candidate = _stored_path(folder, str(base / clean))[0]
        if candidate in known:
            return candidate
    return None


def _verb_read(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run read: print resolved spans and record a receipt.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder whose ledger names the artifact.
    anch : dict
        Anchors dict from ``anchors()``; provides session and timestamp.
    argv : argparse.Namespace
        Parsed read arguments: path.

    Returns
    -------
    int
        0 on success; 1 when the path is not in the ledger, not on disk,
        or the receipt directory cannot be written.

    Notes
    -----
    - The spans print before the receipt is written, so a state directory
      the receipt cannot reach costs the caller the exit code, never
      the content it asked for.
    - The path keys as ``stamp`` stores it, else relative to the root or
      the pinned work dir (``_ledger_key``), and the file opens where the
      stored path resolves; the receipt carries the stored path, which
      is what the gate matches.
    - A ``/`` in the session id becomes ``_``: the id names one receipt
      file, not a path under the state directory.
    """
    path = getattr(argv, 'path', '')
    rows = _read_tsv(folder / 'ledger.tsv', LEDGER_FIELDS)
    stored_path = _ledger_key(folder, path, {r['path'] for r in rows})
    if stored_path is None:
        print(f'hq read: {path} not in ledger - read it whole by hand')
        return 1
    file_path = _stored_path(folder, stored_path)[1]
    row = latest_rows(rows)[stored_path]
    if not file_path.exists():
        print(
            f'hq read: {path} not on disk'
            ' - re-point, supersede, or archive its row at the next write')
        return 1
    file_text = file_path.read_text(encoding='utf-8', errors='replace')
    file_lines = file_text.splitlines()
    if row['where'] != '-':
        anchor_list = _split_where(row['where'])
        spans, unresolved = resolve_where(file_text, anchor_list)
        for span in spans:
            print('\n'.join(file_lines[span[0] - 1:span[1]]))
        for anchor_str in unresolved:
            print(
                f'? unresolved: {anchor_str}'
                ' - read the file whole when no span printed above')
    else:
        print('\n'.join(file_lines))
    state_path = pathlib.Path(os.environ.get('HQ_STATE_DIR', str(_DEFAULT_STATE)))
    session_name = re.sub(r'[/\\]', '_', str(anch['session']))
    try:
        state_path.mkdir(parents=True, exist_ok=True)
        receipt_path = state_path / f'hq-reads-{session_name}.txt'
        with receipt_path.open('a', encoding='utf-8') as fh:
            fh.write(f'{anch["now"]} {folder.name} {stored_path}\n')
    except OSError as exc:
        print(
            f'hq read: receipt not written: {exc}'
            ' - the gate will not credit this read')
        return 1
    return 0


def _verb_when(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run when: print ledger history for one path.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing ledger.tsv.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed when arguments: path.

    Returns
    -------
    int
        0 with every row for the path, oldest first, no cap; 1 when no
        spelling of the path has a row.

    Notes
    -----
    - The path keys as ``stamp`` stores it, so every spelling of one
      file - ``~``, its expansion, a dotted folder path - shows one
      history; a relative token the folder does not hold is tried
      against the project root, then the pinned work dir.
    """
    path = getattr(argv, 'path', '')
    rows = _read_tsv(folder / 'ledger.tsv', LEDGER_FIELDS)
    stored_path = _ledger_key(folder, path, {r['path'] for r in rows})
    if stored_path is None:
        print(
            f'hq when: {path} not in ledger'
            f' - hq artifacts {folder.name} lists the rows')
        return 1
    for row in rows:
        if row['path'] == stored_path:
            print('\t'.join([
                row['path'], row['cycle'], row['status'], row['read_before'],
                row['successor'], row['reason'], row['label']]))
    return 0


def _verb_diff(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run diff: the cursor change between two cycles, by section.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder whose cycles/ directory holds the archives.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed diff arguments: ``c1`` and ``c2`` as ``5``, ``c5``, or
        ``c05``; ``section``, a heading to expand, matched without regard
        to case; ``--full``, every section expanded.

    Returns
    -------
    int
        0 with one line per cursor section in file order - ``## Now  +3
        -1``, ``## State  unchanged``, ``## Unfiled  added`` or
        ``removed`` - or nothing when the two cursors are identical
        once whitespace is normalized; with a section or ``--full``, the
        section's ``-`` and ``+`` lines as difflib orders them, no cap.
        1 when a cycle archive is absent or the section matches no
        heading; 2 when a cycle is not a number.

    Notes
    -----
    - A line that moved between sections counts in both, so the summary
      never reads identical when only the shape changed; identity is
      the whole cursor's, so the help text's "no output means identical"
      holds.
    """
    cycles: list[int] = []
    for token in (str(getattr(argv, 'c1', '')), str(getattr(argv, 'c2', ''))):
        digits = token[1:] if token[:1] in 'cC' else token
        if not digits.isdigit():
            print(
                f'hq diff: cycle {token!r} is not N, cN, or cNN'
                ' - name a finished cycle by its number')
            return 2
        cycles.append(int(digits))
    c1, c2 = cycles
    p1 = folder / 'cycles' / f'c{c1:02d}.md'
    p2 = folder / 'cycles' / f'c{c2:02d}.md'
    for p in (p1, p2):
        if not p.exists():
            print(f'hq diff: {p.name} not found - that cycle was never finished here')
            return 1
    cursors = [split_handoff(p.read_text(encoding='utf-8'))['cursor'] for p in (p1, p2)]
    if ' '.join(cursors[0].split()) == ' '.join(cursors[1].split()):
        return 0
    parsed: list[dict[str, list[str]]] = []
    for cursor in cursors:
        sections: dict[str, list[str]] = {}
        heading = ''
        for line in cursor.splitlines():
            if line.startswith('## '):
                heading = line[3:].strip()
                sections[heading] = []
            elif heading:
                sections[heading].append(line)
        parsed.append(sections)
    old, new = parsed
    order = list(old) + [h for h in new if h not in old]
    wanted = getattr(argv, 'section', None)
    expand = bool(wanted) or getattr(argv, 'full', False)
    if wanted:
        match = next((h for h in order if h.lower() == wanted.lower()), None)
        if match is None:
            print(
                f'hq diff: no section {wanted} in c{c1} or c{c2}'
                f' - sections: {", ".join(order)}')
            return 1
        order = [match]
    for heading in order:
        if heading not in old or heading not in new:
            sign = '+' if heading not in old else '-'
            print(f'## {heading}  {"added" if sign == "+" else "removed"}')
            if expand:
                for line in (new if sign == '+' else old)[heading]:
                    print(f'{sign} {line}')
            continue
        changes = [
            line for line in difflib.ndiff(old[heading], new[heading])
            if line[:1] in '+-']
        if not changes:
            print(f'## {heading}  unchanged')
            continue
        counts = ' '.join(
            f'{sign}{n}' for sign, n in (
                ('+', sum(line[0] == '+' for line in changes)),
                ('-', sum(line[0] == '-' for line in changes)))
            if n)
        print(f'## {heading}  {counts}')
        if expand:
            print('\n'.join(changes))
    return 0


def _verb_artifacts(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run artifacts: print live rows and unstamped walk entries, no cap.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to report on.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed artifacts arguments (none beyond the common flags).

    Returns
    -------
    int
        0 always; prints one line per entry.
    """
    rows = _read_tsv(folder / 'ledger.tsv', LEDGER_FIELDS)
    live = latest_rows(rows)
    walk = _walk_folder(folder)
    live = _reconcile_missing(folder, live)
    seen: set[str] = set()
    for name, kind in walk:
        seen.add(name)
        if kind == 'skip':
            if 'conflicted copy' in name:
                print(f'{name}  conflicted copy')
            else:
                print(f'{name}  unstampable')
            continue
        row = live.get(name)
        if row is None:
            if not is_recorded(name, live):
                print(f'{name}  {kind}?  unstamped')
            continue
        if row['status'] == 'live':
            print(
                f'{name}  {row["kind"]}  {row["read_before"]}'
                f'  c{row["cycle"]}  {row["label"]}'
                )
    for path, row in live.items():
        if path in seen or row['status'] != 'live':
            continue
        print(
            f'{path}  {row["kind"]}  {row["read_before"]}'
            f'  c{row["cycle"]}  {row["label"]}')
    return 0


def _verb_standing(folder: pathlib.Path, anch: dict, argv: argparse.Namespace) -> int:
    """Run standing: print unsuperseded items, all with --all, or named ids.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder containing standing.md.
    anch : dict
        Anchors dict from ``anchors()``; unused but required by dispatch.
    argv : argparse.Namespace
        Parsed standing arguments; ``ids`` names items to print in full,
        ``--all`` includes superseded items in the listing.

    Returns
    -------
    int
        0 with no id, one line per item; with ids, 0 once every id
        printed and 1 when any id is not in standing.md.

    Notes
    -----
    - The block renders a decision or a dead end by its headline alone;
      the id form is where its body is read.
    - A superseded item names the id now current and the cycle that
      made it current, never the first hop out of a chain whose own
      successor was superseded later.
    - The id form adds the whole chain, ``old -> mid -> current``,
      once it runs past one hop.
    """
    standing_path = folder / 'standing.md'
    text = standing_path.read_text(encoding='utf-8') if standing_path.exists() else ''
    items, superseded_ids = _parse_standing(text)
    successors = {
        m.group(2): (m.group(3), m.group(1))
        for m in re.finditer(
            r'^\s*- \(c(\d+)\)\s+([dcx]\d+)\s*->\s*([dcx]\d+)', text, re.MULTILINE)
        }
    # Notes:
    # - A successor can itself be superseded, so the id now current
    #   sits at the end of the walk and not one hop along it.
    # - standing.md is append-only and hand-editable, so the seen set
    #   stops a cycle in the edges from looping here.
    chains: dict[str, list[str]] = {}
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
            chains[start] = chain
    show_all = getattr(argv, 'all', False)
    wanted = list(getattr(argv, 'ids', None) or [])
    by_id = {item['id']: item for item in items}
    rc = 0
    for item_id in wanted:
        if item_id not in by_id:
            print(
                f'hq standing: {item_id} not in standing.md'
                f' - hq standing {folder.name} lists the ids')
            rc = 1
    for item in items:
        if wanted:
            if item['id'] not in wanted:
                continue
        elif not show_all and item['id'] in superseded_ids:
            continue
        sup = ''
        chain = chains.get(item['id'])
        if chain:
            cycle = successors[chain[-2]][1]
            hops = f' - {" -> ".join(chain)}' if wanted and len(chain) > 2 else ''
            sup = f' [superseded by {chain[-1]} in c{cycle}{hops}]'
        elif item['id'] in superseded_ids:
            sup = ' [superseded]'
        print(f'[{item["id"]}] (c{item["cycle"]}) **{item["headline"]}** {item["body"]}{sup}')
    return rc


def _verb_work_dir(folder: pathlib.Path, argv: argparse.Namespace) -> int:
    """Print where the thread's made files go, pin a directory, or clear it.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the pin is ``folder/work-dir``.
    argv : argparse.Namespace
        Parsed ``work-dir`` arguments: ``dir`` is the token to pin or
        None, ``clear`` removes the pin.

    Returns
    -------
    int
        0 with the resolved line, once the pin is written, or once it is
        cleared; 1 when the token is refused, the pin on disk fails its
        check, or a directory sits under the pin's name, with nothing
        written; 2 when ``dir`` and ``--clear`` are both given.

    Notes
    -----
    - No scan: whether the thread's work already lives in a project
      directory is a judgment over the layout at the first begin, and a
      name list would pin a dump in the wrong place. The agent judges
      and pins.
    """
    token = getattr(argv, 'dir', None)
    pin = folder / _WORK_PIN_NAME
    clear = getattr(argv, 'clear', False)
    if clear and token is not None:
        print('hq work-dir: --clear takes no directory - pass one or the other')
        return 2
    # A directory under the pin's name can be neither pinned over nor
    # cleared; the resolve line names the move, and the bare print
    # below carries it with the ruling.
    if pin.is_dir() and (clear or token is not None):
        for line in resolve_work_dir(folder)[1]:
            print(line)
        return 1
    if clear:
        pin.unlink(missing_ok=True)
        print(work_dir_line(folder, ''))
        return 0
    if token is not None:
        value, reason = validate_work_dir(folder.parent.parent, token)
        if not value:
            print(
                f'hq work-dir: {token} {reason} - name a directory that'
                " already holds the thread's work; with none, leave the"
                ' folder unpinned')
            return 1
        pin.write_text(value + '\n', encoding='utf-8')
        print(
            f'work dir: {value} - pinned in'
            f' {HANDOFF_DIRNAME}/{folder.name}/{_WORK_PIN_NAME}')
        return 0
    value, lines = resolve_work_dir(folder)
    for line in lines:
        print(line)
    print(work_dir_line(folder, value))
    return 1 if lines else 0


def _verb_list(root: pathlib.Path, argv: argparse.Namespace) -> int:
    """Print one line per handoff folder, newest first.

    Parameters
    ----------
    root : pathlib.Path
        Project root; the handoff folders sit under ``root / HANDOFF_DIRNAME``.
    argv : argparse.Namespace
        Parsed ``list`` arguments; ``count`` caps the lines, None for all.

    Returns
    -------
    int
        0, also when no folder holds a ``HANDOFF.md`` and the one line is
        ``hq list: no handoff under <dir>``; 2 on a count below 1.

    Notes
    -----
    - Order is each ``HANDOFF.md``'s modification time, newest first,
      the order ``ls -t`` gives; files changed in the same second list
      A to Z by slug.
    - The line is ``<slug>  <written>  c<N>  <done>/<total>  <task>``.
      A non-conforming header gives ``-`` for the date and the cycle; a
      Plan with no checkbox item, or no Plan, gives ``-`` for the
      progress; a missing Task line gives ``-``.
    - Only ``- [ ]``, ``- [x]``, and ``- [X]`` bullets under ``## Plan``
      count; a checkbox under any other section is not a plan item.
    - A file the script cannot read prints its slug with ``-`` in every
      field and ``unreadable: <reason>`` as the Task; the survey goes on
      and the exit stays 0.
    """
    count = getattr(argv, 'count', None)
    if count is not None and count < 1:
        print('hq list: count must be a positive integer')
        return 2
    handoffs = root / HANDOFF_DIRNAME
    files = []
    if handoffs.is_dir():
        files = sorted(
            (path for path in handoffs.glob('*/HANDOFF.md') if path.is_file()),
            key=lambda path: path.parent.name)
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        print(f'hq list: no handoff under {handoffs}')
        return 0
    rows: list[tuple[str, str, str, str, str]] = []
    for path in files[:count]:
        try:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            rows.append((path.parent.name, '-', '-', '-', f'unreadable: {reason}'))
            continue
        parsed = split_handoff(text)
        written = cycle = '-'
        if parsed['cycle'] is not None:
            written_match = re.search(r'Written:\s*(\S+)', parsed['header'])
            written = written_match.group(1) if written_match else '-'
            cycle = f'c{parsed["cycle"]}'
        task = '-'
        done = total = 0
        section = ''
        for line in text.splitlines():
            if line.startswith('## '):
                section = line[3:].strip()
                continue
            if line.startswith('<!-- hq:'):
                section = ''
                continue
            if section == 'Task' and task == '-' and line.strip():
                task = line.strip()
            elif section == 'Plan':
                box = re.match(r'\s*- \[([ xX])\]', line)
                if box:
                    total += 1
                    done += box.group(1) != ' '
        progress = f'{done}/{total}' if total else '-'
        rows.append((path.parent.name, written, cycle, progress, task))
    headers = ('SLUG', 'WRITTEN', 'CYCLE', 'PROGRESS')
    col_widths = [
        max(*(len(row[i]) for row in rows), len(headers[i]))
        for i in range(4)
        ]
    header_line = (
        f'{headers[0].ljust(col_widths[0])}  '
        f'{headers[1].ljust(col_widths[1])}  '
        f'{headers[2].ljust(col_widths[2])}  '
        f'{headers[3].ljust(col_widths[3])}  '
        f'TASK'
        )
    print(header_line)
    print('-' * len(header_line))
    for slug, written, cycle, progress, task in rows:
        print(
            f'{slug.ljust(col_widths[0])}  '
            f'{written.ljust(col_widths[1])}  '
            f'{cycle.ljust(col_widths[2])}  '
            f'{progress.ljust(col_widths[3])}  '
            f'{task}')
    return 0


# ----------------------------------------------------------------------
# Argument parser
# ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser for all fifteen verbs.
    """
    _top_epilog = (
        'Every verb but list and help takes the slug first. A slug resolves\n'
        'to an exact folder name under .handoff/, else a unique prefix of\n'
        'one.\n'
        'Five flags before the verb - --root DIR, --cycle N, --now ISO,\n'
        '--session ID, --host H - override the HQ_ROOT, HQ_CYCLE, HQ_NOW,\n'
        'HQ_SESSION, and HQ_HOST environment values the script otherwise\n'
        'reads; a session never needs them. A ~ in --root or HQ_ROOT is\n'
        'expanded.\n'
        'Exit 0 is done; 1 is a refusal or a blocking finding, and nothing is\n'
        'written except that a refused stamp appends its receipt row; 2 is a\n'
        'usage error. All output is stdout, one fact per line.\n'
        'Reference: hq help anchors | kinds | rules | stale-path.'
    )
    p = argparse.ArgumentParser(
        prog='hq',
        description='Handoff ledger manager',
        epilog=_top_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', help='project root (HQ_ROOT)')
    p.add_argument('--cycle', help='override cycle number (HQ_CYCLE)')
    p.add_argument('--now', help='override timestamp (HQ_NOW)')
    p.add_argument('--session', help='override session id (HQ_SESSION)')
    p.add_argument('--host', help='override hostname (HQ_HOST)')
    sub = p.add_subparsers(dest='verb', required=True)

    sub.add_parser('adopt').add_argument('slug')

    beg = sub.add_parser('begin')
    beg.add_argument('slug')
    beg.add_argument('--force', action='store_true')

    _stamp_epilog = (
        'The path is relative to the handoff folder: ./SPEC.md and\n'
        'sub/../SPEC.md are the row SPEC.md. A path outside it - a repo file,\n'
        'a ~ path, an absolute path - is stored whole and gated the same way;\n'
        'there is no search of the working directory, and a first stamp of a\n'
        'relative token the folder does not hold exits 2. An outside .py/.sql/\n'
        '.js/.ts/.ps1 infers other/never: pass --kind draft to gate it at edit.\n'
        'A live always row needs --where: a stamp that would leave one with\n'
        "no anchor is refused and prints the file's headings.\n"
        '--kind, --read-before, --status, --where, and --label default to the\n'
        "previous row's value; omit them on a re-stamp to carry them forward.\n"
        'A label shorter than its comparand, or missing one of its backticked\n'
        'tokens or s<n> references, draws an advisory on the stamp that wrote\n'
        'it; the row stands. The comparand is the last label an earlier cycle\n'
        'left, or the previous stamp of this cycle where there is no earlier\n'
        'row. Backticks are markup: an identifier the new label still spells\n'
        'plainly is neither a loss nor a shortening.\n'
        '--successor P sets status=superseded read_before=never; --archive\n'
        'sets status=archived read_before=never and requires --reason;\n'
        '--defer marks a non-gated file deferred so it reappears in the next\n'
        'work list. --batch reads one stamp per stdin line, the same\n'
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
        choices=['always', 'edit', 'mention', 'never'])
    stmp.add_argument(
        '--status',
        choices=['live', 'superseded', 'archived', 'missing'])
    stmp.add_argument('--successor')
    stmp.add_argument('--archive', action='store_true')
    stmp.add_argument('--defer', action='store_true')
    stmp.add_argument('--where')
    stmp.add_argument('--label')
    stmp.add_argument('--reason')
    stmp.add_argument('--batch', action='store_true')

    _note_epilog = (
        'Kinds are decision, constraint, dead-end. --headline is required and\n'
        'one line; the body follows it as the last argument. --batch reads\n'
        'one note per stdin line: <kind> --headline "<h>" <body>, the body\n'
        'taken verbatim to the end of the line, quotes and apostrophes\n'
        'included; a body wrapped whole in one pair of quotes loses the pair.'
    )
    # The kind slot carries no choices: the batch marker '-' lands
    # here on 3.13 and later, and _verb_note names an unknown kind.
    nt = sub.add_parser(
        'note',
        epilog=_note_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    nt.add_argument('slug')
    nt.add_argument('note_kind', nargs='?')
    nt.add_argument('body', nargs='?')
    nt.add_argument('--headline', default='')
    nt.add_argument('--batch', action='store_true')

    _supersede_epilog = (
        'Two ids of one kind, old then new: d17 d23. The old item leaves the\n'
        'rendered Standing block and stays in standing.md. hq standing <slug>\n'
        'lists the ids.'
    )
    sp = sub.add_parser(
        'supersede',
        epilog=_supersede_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument('slug')
    sp.add_argument('old_id')
    sp.add_argument('new_id')

    _finish_epilog = (
        '--log is the one line the Log keeps for this cycle. --acknowledge\n'
        '"<reason>" turns a W1 or W2 witness break into an acknowledged line\n'
        'and records the break and the reason in the manifest.'
    )
    fin = sub.add_parser(
        'finish',
        epilog=_finish_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    fin.add_argument('slug')
    fin.add_argument('--log', required=True)
    fin.add_argument('--acknowledge')

    sub.add_parser('open').add_argument('slug')

    rd = sub.add_parser('read')
    rd.add_argument('slug')
    rd.add_argument('path')

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

    _artifacts_epilog = (
        'Every live row as a full line - path kind read_before cN label - with\n'
        'no cap, then <name>  <kind>?  unstamped for a file with no row,\n'
        '<name>  conflicted copy for a sync duplicate, and <name>  unstampable\n'
        'for a name holding a tab or newline or an entry that is not a regular\n'
        'file.'
    )
    sub.add_parser(
        'artifacts',
        epilog=_artifacts_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ).add_argument('slug')

    _standing_epilog = (
        'Every unsuperseded item in full; --all adds the superseded ones.\n'
        'With ids, the named items in full. A superseded item names the id\n'
        'now current and the cycle that made it current, following the\n'
        'old -> new lines to the end of the chain: [d18] ... [superseded by\n'
        'd21 in c5]. Past one hop the id form adds the chain itself:\n'
        '[superseded by d21 in c5 - d18 -> d20 -> d21]. An id not in\n'
        'standing.md prints hq standing: <id> not in standing.md and exits 1.'
    )
    st = sub.add_parser(
        'standing',
        epilog=_standing_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    st.add_argument('slug')
    st.add_argument('ids', nargs='*')
    st.add_argument('--all', action='store_true')

    _list_epilog = (
        'One line per folder under .handoff/ holding a HANDOFF.md, newest\n'
        "first by that file's mtime: <slug>  <Written date>  c<N>\n"
        '<done>/<total>  <Task line>. <done>/<total> counts - [x] over all\n'
        "- [ ] and - [x] items under ## Plan alone, '-' when the Plan has no\n"
        "checkbox item; a file with no conforming header shows '-' for the\n"
        "date and the cycle; an unreadable file shows '-  -  -  unreadable:\n"
        "<reason>'. Ties in the same second list A to Z by slug. A bare list\n"
        'shows every one; list 5 the five most recent.'
    )
    sub.add_parser(
        'list',
        epilog=_list_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ).add_argument('count', nargs='?', type=int)

    _work_dir_epilog = (
        "With the slug alone, prints where the thread's made files go:\n"
        'work dir: .handoff/<slug>/ (default) - the kind folders specs/,\n'
        'drafts/, notes/, outputs/ - or work dir: <dir> (pin) when the pin\n'
        '.handoff/<slug>/work-dir names a directory. With a directory,\n'
        'checks it - already on disk, not the root, not under .handoff/,\n'
        'not under the system temp directory unless the root itself is;\n'
        'inside the root or out - and writes it to the pin, root-relative\n'
        'under the root, else by ~ or absolutely;\n'
        'a refused token writes nothing (exit 1). --clear removes the pin.\n'
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
    parser = _build_parser()
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
                for key, body in HELP_TOPICS.items():
                    lines = body.splitlines()
                    first_line = next(
                        (l for l in lines[1:] if l.strip()), '')
                    print(f'hq help {key}: {first_line}')
                return 0
            if topic not in HELP_TOPICS:
                keys = ', '.join(HELP_TOPICS)
                print(
                    f'hq help: no topic {topic!r};'
                    f' topics are {keys}')
                return 2
            print(HELP_TOPICS[topic])
            return 0
        root = _resolve_root(args)
        if verb == 'list':
            return _verb_list(root, args)
        slug = getattr(args, 'slug', '')
        is_begin = verb == 'begin'
        folder = _find_folder(root, slug, missing_ok=is_begin)
        if verb == 'work-dir':
            return _verb_work_dir(folder, args)
        anch = anchors(folder, args)
        dispatch = {
            'adopt': _verb_adopt,
            'begin': _verb_begin,
            'stamp': _verb_stamp,
            'note': _verb_note,
            'supersede': _verb_supersede,
            'finish': _verb_finish,
            'open': _verb_open,
            'read': _verb_read,
            'when': _verb_when,
            'diff': _verb_diff,
            'artifacts': _verb_artifacts,
            'standing': _verb_standing,
            }
        return dispatch[verb](folder, anch, args)
    except OSError as exc:
        path = exc.filename or '?'
        reason = exc.strerror or type(exc).__name__
        print(f'hq: cannot access {path}: {reason}')
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
