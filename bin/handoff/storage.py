"""Store files on disk: root and folder lookup, TSV and key=value I/O, digests, locks.
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import re
import socket
import subprocess
import sys

Row = dict[str, str]


# Each later column trails the ones before it, so a manifest written
# before it existed still reads by position; read_tsv pads it to `-`.
MANIFEST_FIELDS = [
    'cycle', 'written', 'session', 'repos', 'cursor_lines', 'payload_tokens',
    'handoff_sha', 'ledger_bytes', 'ledger_sha', 'standing_bytes',
    'standing_sha', 'log', 'note', 'rewrite_sha', 'stall',
    ]


MANIFEST_HEADER = '\t'.join(MANIFEST_FIELDS)


# Notes:
# - The append-only stores the agent dictates through a verb instead
#   of opening, each against the verb that replaces a direct read.
#   `read` refuses them by this map and the PreToolUse gate reports
#   them by it, so both halves name the same route.
# - cycles/ is absent: no verb prints an archived cycle whole, so the
#   agent reads cycles/cNN.md by range and never edits it.
# - Each verb prints one item or one path's rows: a reader sent here
#   never pays for the whole store, which bare hq standing prints.
STORE_VERBS = {
    'ledger.tsv': 'hq when {slug} <path>',
    'standing.md': 'hq standing {slug} <id>',
    }


HANDOFF_DIRNAME = '.handoff'


# The directories earlier plugin versions kept the folder under; a
# path written under one of them names where a folder used to be.
FORMER_HANDOFF_DIRNAMES = ('working', 'scratch')


DONE_NAME = '.hq.done'


TWO_HOURS = 7200


def anchors(folder: pathlib.Path, argv: argparse.Namespace) -> dict:
    """Return environment fields for the current invocation.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; read to determine the current cycle from the manifest.
    argv : argparse.Namespace
        Parsed flags; ``--session`` overrides its ``HQ_SESSION``
        environment fallback.

    Returns
    -------
    dict
        Keys: ``cycle`` (int), ``now`` (str), ``session`` (str), ``host``
        (str), ``branch`` (str), ``sha`` (str), ``dirty`` (list[str]),
        ``root`` (Path).

    Notes
    -----
    - Precedence: ``HQ_CYCLE``, ``HQ_NOW``, and ``HQ_HOST`` each override
      the manifest cycle, the clock, and the hostname they fall back to;
      ``--session`` overrides ``HQ_SESSION`` the same way.
    - ``HQ_GIT=0`` disables git calls; branch and sha become ``'-'``.
    - cycle is last manifest row's cycle + 1, or 1 with no manifest rows.
    - A cycle or timestamp that does not parse exits before any verb runs:
      2 for the env value, 1 for a manifest row.
    """
    root = resolve_root(argv)
    cycle_raw = os.environ.get('HQ_CYCLE')
    if cycle_raw:
        if not re.fullmatch(r'\d+', str(cycle_raw).strip()):
            print('hq: HQ_CYCLE must be an integer')
            sys.exit(2)
        cycle = int(cycle_raw)
    else:
        manifest = read_tsv(folder / 'cycles' / 'manifest.tsv', MANIFEST_FIELDS)
        for idx, row in enumerate(manifest, start=1):
            if not re.fullmatch(r'\d+', row['cycle'].strip()):
                print(
                    f'hq: cycles/manifest.tsv row {idx} has a'
                    ' non-integer cycle - a hand-edited manifest; restore the integer')
                sys.exit(1)
        cycle = (int(manifest[-1]['cycle']) + 1) if manifest else 1
    now = (
        os.environ.get('HQ_NOW')
        or datetime.datetime.now().isoformat(timespec='seconds')
    )
    try:
        datetime.datetime.fromisoformat(now)
    except ValueError:
        print('hq: HQ_NOW must be an ISO 8601 timestamp')
        sys.exit(2)
    session = (
        getattr(argv, 'session', None)
        or os.environ.get('HQ_SESSION')
        or os.environ.get('CLAUDE_CODE_SESSION_ID', 'unknown')
    )
    host = os.environ.get('HQ_HOST') or socket.gethostname()
    branch, sha, dirty = '-', '-', []
    if os.environ.get('HQ_GIT', '1') != '0':
        branch, sha, dirty = _git_state(root)
    return {
        'cycle': cycle, 'now': now, 'session': session, 'host': host,
        'branch': branch, 'sha': sha, 'dirty': dirty, 'root': root,
        }


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


def resolve_root(argv: argparse.Namespace) -> pathlib.Path:
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


def sha12(data: bytes) -> str:
    """Return the first 12 hex of sha256 of data.
    """
    return hashlib.sha256(data).hexdigest()[:12]


def sha12_path(path: pathlib.Path) -> str:
    """Return the first 12 hex of sha256 of path's bytes, or '-' on error.
    """
    try:
        return sha12(path.read_bytes())
    except OSError:
        return '-'


def read_tsv(path: pathlib.Path, fields: list[str]) -> list[dict]:
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


def append_lines(path: pathlib.Path, lines: list[str]) -> None:
    """Append lines to a text file, restoring a lost trailing newline first.

    Parameters
    ----------
    path : pathlib.Path
        File to append to; created when absent.
    lines : list[str]
        Lines without their newline.

    Notes
    -----
    - The guard is the one ``append_tsv`` applies to the ledger: a file
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


def append_tsv(path: pathlib.Path, fields: list[str], row: dict, header: str) -> None:
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


def find_folder(
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
        When True, return ``.handoff/<slug>`` whenever no folder bears
        that exact name, ahead of any prefix match. ``begin`` creates it.

    Returns
    -------
    pathlib.Path
        The resolved folder path.

    Notes
    -----
    - The slug is one path component: ``[A-Za-z0-9][A-Za-z0-9._-]*``. It
      is joined onto ``.handoff/`` unquoted, so ``..`` or an embedded
      separator would place a handoff folder outside ``.handoff/``.
    - Prefix resolution serves the verbs that resume a thread, which can
      only mean one already on disk. ``begin`` is the verb that creates,
      so it takes the slug as typed and a longer neighbor never claims
      it: ``begin auth`` beside ``authz`` opens ``auth``.
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
    if missing_ok:
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


def read_kv(path: pathlib.Path) -> dict[str, str]:
    """Read a key=value file as a dict; return {} when absent or unreadable.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            result[k.strip()] = v.strip()
    return result


def read_lock(folder: pathlib.Path) -> dict[str, str]:
    """Read .hq.lock as a key=value dict; return {} when absent or unreadable.
    """
    return read_kv(folder / '.hq.lock')


def done_refusal(folder: pathlib.Path, verb: str) -> str:
    """Return the line refusing a folder marked done, '' when it is not.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder to test for the ``.hq.done`` marker.
    verb : str
        The verb naming itself in the line, ``begin`` or ``adopt``.

    Returns
    -------
    str
        The refusal, or '' where the folder carries no marker.

    Notes
    -----
    - The marker's existence is the whole test, and every reader of it
      uses this one. A marker a hand edit left empty, unparseable, or a
      directory still refuses; the fields it cannot read print as ``-``.
      Reading the fields instead would hide a folder from ``list`` while
      ``begin`` opened a cycle on it.
    """
    if not (folder / DONE_NAME).exists():
        return ''
    done = read_kv(folder / DONE_NAME)
    return (
        f'hq {verb}: {folder.name} is marked done'
        f' (c{done.get("cycle", "-")}, {done.get("time", "-")})'
        f' - run hq done {folder.name} --undo to reopen it')


def line_count(path: pathlib.Path) -> int:
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


def dirty_str(dirty: list[str]) -> str:
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


def lock_age(lock: dict[str, str], now: str) -> float | None:
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


def lock_refusal(folder: pathlib.Path, anch: dict, force: bool, verb: str) -> str:
    """Return the line that refuses a verb for a foreign lock, or ''.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder the lock sits in.
    anch : dict
        Anchors for this invocation; supplies session and time.
    force : bool
        True when ``--force`` was given; every lock then passes.
    verb : str
        The verb being refused; names itself in the line. ``begin``
        takes ``--force`` as its remedy, every other verb a ``begin``
        that carries it.

    Returns
    -------
    str
        The refusal line to print, or '' when the verb may proceed.

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
    lock = read_lock(folder)
    if lock.get('session') == anch['session']:
        return ''
    age = lock_age(lock, anch['now'])
    if verb == 'begin':
        if age is None:
            return 'hq begin: lock file unreadable; use --force to take over'
        if age < TWO_HOURS:
            return (
                f'hq begin: lock held by {lock.get("session")} on'
                f' {lock.get("host")} since {lock.get("time")};'
                ' use --force to take over')
        return ''
    takeover = f'run hq begin {folder.name} --force to take over'
    if age is None:
        return f'hq {verb}: lock file unreadable; {takeover}'
    if age < TWO_HOURS:
        return (
            f'hq {verb}: lock held by {lock.get("session")} on'
            f' {lock.get("host")} since {lock.get("time")};'
            f' {takeover}')
    return ''
