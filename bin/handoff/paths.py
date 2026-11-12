"""Path tokens and the thread work dir, resolved to ledger keys.
"""

import os
import pathlib
import tempfile

from handoff import storage

WORK_PIN_NAME = 'work-dir'


_TEMP_DIRS = (
    pathlib.Path(os.path.abspath(tempfile.gettempdir())), pathlib.Path('/tmp'))


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
        if rel == storage.HANDOFF_DIRNAME or rel.startswith(
            storage.HANDOFF_DIRNAME + '/'):
            return '', f'is under {storage.HANDOFF_DIRNAME}/'
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
    pin = folder / WORK_PIN_NAME
    if pin.is_dir():
        return '', [
            (f'{storage.HANDOFF_DIRNAME}/{folder.name}/{WORK_PIN_NAME} is a directory'
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
        (f'{storage.HANDOFF_DIRNAME}/{folder.name}/{WORK_PIN_NAME} names {raw_pin!r}:'
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
        f'work dir: {storage.HANDOFF_DIRNAME}/{folder.name}/ (default)'
        f' - made files go under specs/, drafts/, notes/, or outputs/{tail}')


def pointer_path(
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
        ``stored_path`` returns them.

    Notes
    -----
    - A foreign file names repo files relative to the repo root, so a
      token the folder does not hold is tried against the project root
      (the folder's grandparent), the cwd, and home before it is seeded
      as a missing folder path. ``stamp`` searches the bases a rendered
      block prints against; the cwd and home are adoption's alone.
    """
    stored, path_obj, base = stored_path(folder, token)
    if base == 'abs' or path_obj.exists():
        return stored, path_obj, base
    clean = token.strip('`')
    for base_dir in (folder.parent.parent, pathlib.Path.cwd(), pathlib.Path.home()):
        candidate = pathlib.Path(os.path.normpath(base_dir / clean))
        if candidate.exists():
            return stored_path(folder, str(candidate))
    return stored, path_obj, base


def successor_path(folder: pathlib.Path, successor: str) -> pathlib.Path:
    """Return the disk path a successor field names, keyed as an artifact is.
    """
    return stored_path(folder, successor)[1]


def _search_bases(folder: pathlib.Path) -> list[pathlib.Path]:
    """Return the bases a relative path is tried against, in order.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the project root is its grandparent and the pin
        its ``work-dir`` file.

    Returns
    -------
    list[pathlib.Path]
        The project root, then the pinned work dir where one is pinned.
        A pin spelled with ``/`` or ``~`` is expanded; any other pin
        resolves against the root.

    Notes
    -----
    - One list serves the write verb and the read-only verbs, so the
      spelling a rendered block prints is the spelling every verb
      takes. They differ in what counts as a hit: ``stamp`` matches on
      the file existing, ``read`` and ``when`` on a row existing.
    """
    root = folder.parent.parent
    bases = [root]
    pin, _ = resolve_work_dir(folder)
    if pin:
        bases.append(
            pathlib.Path(os.path.expanduser(pin)) if pin.startswith(('/', '~'))
            else root / pin)
    return bases


def stored_path(
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
      ``sub/../SPEC.md``, the folder's own absolute spelling, and the
      ``.handoff/<slug>/SPEC.md`` a rendered block prints all key on
      ``SPEC.md``.
    - Normalization is lexical, so a symlink in the spelling reaches the
      ledger as written.
    - An outside path stores as ``~/...`` under home, else absolute. A
      relative token is never searched for under the cwd or home,
      which are adoption's alone. The one retry is
      against the root, and it is kept only when it lands inside the
      folder, which is the block's own spelling and no repo file.
    """
    path_clean = token.strip('`')
    home = pathlib.Path(os.path.abspath(os.path.expanduser('~')))
    folder_abs = pathlib.Path(os.path.abspath(os.path.expanduser(str(folder))))
    if path_clean.startswith(('/', '~')):
        candidate = pathlib.Path(os.path.normpath(os.path.expanduser(path_clean)))
    else:
        candidate = pathlib.Path(os.path.normpath(folder_abs / path_clean))
        # The blocks print a file in the folder from the root -
        # .handoff/<slug>/specs/SPEC.md - so that spelling keys on the
        # row the bare one keys on. A relative token the root holds
        # anywhere else is still never searched for.
        if not candidate.exists():
            from_root = pathlib.Path(os.path.normpath(
                folder_abs.parent.parent / path_clean))
            if folder_abs in from_root.parents:
                candidate = from_root
    try:
        return candidate.relative_to(folder_abs).as_posix(), candidate, 'folder'
    except ValueError:
        pass
    try:
        return '~/' + candidate.relative_to(home).as_posix(), candidate, 'abs'
    except ValueError:
        return str(candidate), candidate, 'abs'


def stamp_path(
    folder: pathlib.Path,
    token: str,
    rows: list[storage.Row],
) -> tuple[str, pathlib.Path, str]:
    """Resolve a stamp token - the path or ``--successor`` - to its key.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder whose ledger holds ``rows``.
    token : str
        The token as typed: folder-relative, root-relative, pin-relative,
        or by ``~`` or absolutely; backticks are stripped.
    rows : list[Row]
        Every ledger row; a spelling whose key has a row counts as held.

    Returns
    -------
    tuple[str, pathlib.Path, str]
        The stored path, the path on disk, and the base, as
        ``stored_path`` returns them.

    Notes
    -----
    - A spelling is held where its file exists or its key has a row.
      The folder spelling wins when held, then each of
      ``_search_bases`` in order; a token no base holds keeps the
      folder spelling.
    - A ``/`` or ``~`` token is never searched.
    - A token the root or the work dir holds prints
      ``hq stamp: <token> -> <key> (root)`` or ``(work dir)``.
    """
    stored, path_obj, base = stored_path(folder, token)
    clean = token.strip('`')
    if (path_obj.exists() or clean.startswith(('/', '~'))
            or any(row['path'] == stored for row in rows)):
        return stored, path_obj, base
    for idx, base_dir in enumerate(_search_bases(folder)):
        cand_path, cand_obj, cand_base = stored_path(folder, str(base_dir / clean))
        if cand_obj.exists() or any(row['path'] == cand_path for row in rows):
            base_name = 'root' if idx == 0 else 'work dir'
            print(f'hq stamp: {clean} -> {cand_path} ({base_name})')
            return cand_path, cand_obj, cand_base
    return stored, path_obj, base


def shown_paths(
    folder: pathlib.Path,
    live: dict[str, storage.Row],
    extra_paths: tuple[str, ...] = (),
) -> dict[str, tuple[str, str]]:
    """Map each stored path under the root or the pin to how a block prints it.

    Parameters
    ----------
    folder : pathlib.Path
        Handoff folder; the root is its grandparent and the pin its
        ``work-dir`` file.
    live : dict[str, Row]
        Latest ledger row per path.
    extra_paths : tuple[str, ...], default ()
        Folder-relative names a block prints without a row - the walk
        entries the Artifacts block grades ``unstamped``.

    Returns
    -------
    dict[str, tuple[str, str]]
        Keyed by stored path, for each row and each extra name under the
        pinned work dir or the root: the path relative to that base, and
        the base phrase a block's first line names - ``root ~/code/proj``
        or ``work dir ~/code/proj-wt``. Every other row is absent and
        prints as stored.

    Notes
    -----
    - A ``folder`` row is placed by the folder's own absolute path, so it
      prints ``.handoff/<slug>/specs/SPEC.md`` under the root rather than
      the bare ``specs/SPEC.md`` the ledger stores, which resolves from
      the folder and not from the base the block names.
    - The root is the one base a thread normally prints against. A pin
      outside the root is the only second base, tried first so the made
      files of a worktree thread print short.
    - A base prints by ``~`` under home, else absolutely, the spelling
      ``hq work-dir`` uses.
    """
    home = pathlib.Path(os.path.abspath(os.path.expanduser('~')))
    root = pathlib.Path(os.path.abspath(folder.parent.parent))
    folder_abs = pathlib.Path(os.path.abspath(os.path.expanduser(str(folder))))
    pin_value, _ = resolve_work_dir(folder)
    bases: list[tuple[str, pathlib.Path]] = []
    # A pin under the root resolves from the root already, and
    # resolve_work_dir spells such a pin relative to it; only a pin
    # outside the root earns a second base line.
    if pin_value.startswith(('/', '~')):
        bases.append((
            'work dir',
            pathlib.Path(os.path.normpath(os.path.expanduser(pin_value)))))
    bases.append(('root', root))
    targets: list[tuple[str, pathlib.Path]] = [
        (path,
         pathlib.Path(os.path.normpath(os.path.expanduser(path)))
         if row['base'] == 'abs' else folder_abs / path)
        for path, row in live.items()
        ]
    targets += [(name, folder_abs / name) for name in extra_paths]
    shown: dict[str, tuple[str, str]] = {}
    for path, target in targets:
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


def ledger_key(folder: pathlib.Path, token: str, known: set[str]) -> str | None:
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
      is found by the path the cursor names. ``stamp`` searches the same
      bases, matching on the file existing where these verbs match on a
      row existing.
    """
    stored = stored_path(folder, token)[0]
    if stored in known:
        return stored
    clean = token.strip('`')
    if clean.startswith(('/', '~')):
        return None
    for base in _search_bases(folder):
        candidate = stored_path(folder, str(base / clean))[0]
        if candidate in known:
            return candidate
    return None
