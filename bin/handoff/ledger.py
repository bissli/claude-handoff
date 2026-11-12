"""ledger.tsv rows: kinds, tiers, and the R1 and R3 rules.
"""

import fnmatch
import pathlib
import re

from handoff import paths, storage

LEDGER_FIELDS = [
    'cycle', 'ts', 'path', 'base', 'kind', 'status', 'read_before',
    'successor', 'where', 'sha12', 'lines', 'reason', 'label',
    ]


LEDGER_HEADER = '\t'.join(LEDGER_FIELDS)


_SKIP_NAMES = {
    'HANDOFF.md', 'ledger.tsv', 'standing.md', 'cycles', '.hq.lock', 'work-dir'}


_SNAPSHOT_PATS = ['*.pre-*', '*.prev.*', '*.orig.*', '*.bak']


_SPEC_PATS = ['SPEC*', 'DESIGN*', 'PROPOSAL*', '*-DECLARATION*']


_DRAFT_EXTS = {'.py', '.sql', '.js', '.ts', '.ps1'}


KIND_DIRS = {'specs': 'spec', 'drafts': 'draft', 'notes': 'notes', 'outputs': 'other'}


GATE_RB = {'always', 'edit'}


# Least recoverable last: a superseded row has a successor to follow, an
# archived one a reason, and a missing one neither.
NON_LIVE_ORDER = ('superseded', 'archived', 'missing')


# The tier a gated kind seeds: a spec is the contract, read at every
# resume; a draft is read at the write that changes it.
TIER_SEED = {'spec': 'always', 'draft': 'edit'}


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
        ``KIND_DIRS`` - or empty. The folder sets the kind of a file,
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
    - A spec seeds ``always`` and a draft ``edit``, per ``TIER_SEED``;
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
        kind = KIND_DIRS[kind_dir]
        return (kind, TIER_SEED.get(kind, 'never'))
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


def is_recorded(name: str, live: dict[str, storage.Row]) -> bool:
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
        A key of ``KIND_DIRS`` when the path is ``<kind folder>/<name>``
        with no deeper segment; otherwise ``''``.
    """
    head, sep, tail = stored_path.partition('/')
    if base != 'folder' or not sep or '/' in tail or head not in KIND_DIRS:
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
    new_row: storage.Row,
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


def check_r3(rows: list[storage.Row], sha_by_path: dict[str, str]) -> list[storage.Row]:
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
        if row['status'] != 'live' or row['read_before'] not in GATE_RB:
            continue
        current = sha_by_path.get(path, '')
        if current and current != '-' and row['sha12'] not in {'-', current}:
            result.append(row)
    return result


def latest_rows(rows: list[storage.Row]) -> dict[str, storage.Row]:
    """Return the most recent ledger row for each path.
    """
    result: dict[str, storage.Row] = {}
    for row in rows:
        result[row['path']] = row
    return result


def walk_folder(folder: pathlib.Path) -> list[tuple[str, str]]:
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
        (f'{kind_dir}/', folder / kind_dir) for kind_dir in sorted(KIND_DIRS)
        if (folder / kind_dir).is_dir()]
    for prefix, scope in scopes:
        for entry in sorted(scope.iterdir()):
            name = entry.name
            if name.startswith('.') or name in _SKIP_NAMES:
                continue
            is_dir = entry.is_dir()
            if not prefix and is_dir and name in KIND_DIRS:
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


def folder_state(
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
        walk: list of (name, inferred_kind) from walk_folder.
        rows: all ledger rows.
        live: latest row per path (latest_rows).
        sha_map: sha12 per path, including abs rows' current shas.
        manifest: all manifest rows.
        lb: ledger.tsv bytes.
        sb: standing.md bytes.
    """
    walk = walk_folder(folder)
    rows = storage.read_tsv(folder / 'ledger.tsv', LEDGER_FIELDS)
    live = latest_rows(rows)
    sha_map = {
        n: storage.sha12_path(folder / n)
        for n, _ in walk if (folder / n).is_file()
        }
    for p_key, row in live.items():
        if row['base'] == 'abs':
            abs_p = pathlib.Path(p_key).expanduser()
            if abs_p.is_file():
                sha_map[p_key] = storage.sha12_path(abs_p)
        elif p_key not in sha_map and (folder / p_key).is_file():
            sha_map[p_key] = storage.sha12_path(folder / p_key)
    manifest = storage.read_tsv(
        folder / 'cycles' / 'manifest.tsv', storage.MANIFEST_FIELDS)
    ledger_p = folder / 'ledger.tsv'
    standing_p = folder / 'standing.md'
    lb = ledger_p.read_bytes() if ledger_p.exists() else b''
    sb = standing_p.read_bytes() if standing_p.exists() else b''
    return walk, rows, live, sha_map, manifest, lb, sb


def reconcile_missing(
    folder: pathlib.Path, live: dict[str, storage.Row]) -> dict[str, storage.Row]:
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
    out: dict[str, storage.Row] = {}
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
    live: dict[str, storage.Row],
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
        names nothing on disk, in ledger order. A directory counts as
        present, so a moved ``probe-dir`` row passes.
    """
    return [
        (path, row['successor']) for path, row in live.items()
        if row['status'] == 'superseded' and row['successor'] != '-'
        and not paths.successor_path(folder, row['successor']).exists()
        ]
