"""Which thread a session is on, and the state directory that records it.
"""

import os
import pathlib

STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')


_WORKED_NAME = '.hq.worked'


def state_dir() -> pathlib.Path:
    """HQ_STATE_DIR when set, else STATE_DIR. The directory need not exist.
    """
    return pathlib.Path(os.environ.get('HQ_STATE_DIR', STATE_DIR))


def own_loop_dir(slug: str) -> pathlib.Path | None:
    """The ``$HQ_LOOP`` directory when its ``slug`` file names this thread.

    Parameters
    ----------
    slug : str
        The thread's folder name.

    Returns
    -------
    pathlib.Path or None
        None off a loop, or under a loop run for another thread.
    """
    loop_dir = os.environ.get('HQ_LOOP')
    if not loop_dir:
        return None
    try:
        named = (pathlib.Path(loop_dir) / 'slug').read_text(
            encoding='utf-8', errors='replace').strip()
    except OSError:
        return None
    return pathlib.Path(loop_dir) if named == slug else None


def thread_record(session: str) -> pathlib.Path:
    """Path of the file naming the thread a session is on.

    Parameters
    ----------
    session : str
        Session id, as ``anchors`` resolved it.

    Returns
    -------
    pathlib.Path
        ``<slug>.thread`` under HQ_STATE_DIR. The file need not exist.
    """
    safe = session.replace('/', '_')
    return state_dir() / f'{safe}.thread'


def record_worked(session: str, folder: pathlib.Path) -> None:
    """Make folder the session's worked thread; its finish moves Last worked.

    Parameters
    ----------
    session : str
        Session id, as ``anchors`` resolved it. An empty or 'unknown'
        id records nothing, since every shell without an id shares it.
    folder : pathlib.Path
        Handoff folder that takes the session's ``.hq.worked``.
    """
    if session in {'', 'unknown'}:
        return
    held = worked_thread(session, folder)
    if held == str(folder.resolve()):
        return
    # The verb already succeeded: a read-only folder costs the date,
    # never the exit code.
    try:
        if held:
            (pathlib.Path(held) / _WORKED_NAME).unlink(missing_ok=True)
        (folder / _WORKED_NAME).write_text(f'{session}\n', encoding='utf-8')
    except OSError as exc:
        print(
            f'advisory: worked record not written: {exc.filename}:'
            f" {exc.strerror} - this session's finish keeps Last worked")


def worked_thread(session: str, folder: pathlib.Path) -> str:
    """Resolved path of folder or the sibling holding session's marker.

    Returns '' for none. The marker is ``.hq.worked``.
    """
    if session in {'', 'unknown'}:
        return ''
    for sibling in folder.parent.iterdir():
        record = sibling / _WORKED_NAME
        if not record.is_file():
            continue
        if record.read_text(encoding='utf-8', errors='replace').strip() == session:
            return str(sibling.resolve())
    return ''


def record_thread(session: str, slug: str) -> None:
    """Put a session on a thread, for the status line to name.

    Parameters
    ----------
    session : str
        Session id, as ``anchors`` resolved it.
    slug : str
        Folder name of the thread the session just entered.

    Returns
    -------
    None

    Notes
    -----
    - An unwritable cache raises nothing; the status line loses its slug.
    """
    final = thread_record(session)
    # Notes:
    # - The rename keeps a status line that repaints mid-write from
    #   reading a truncated slug.
    # - The pid in the staging name keeps a subagent and its parent off
    #   one half-written file.
    staged = final.with_name(f'{final.name}.{os.getpid()}')
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(f'{slug}\n', encoding='utf-8')
        os.replace(staged, final)
    except OSError:
        try:
            staged.unlink()
        except OSError:
            pass


def clear_thread(session: str, slug: str) -> None:
    """Take a session off a thread whose work is finished.

    Parameters
    ----------
    session : str
        Session id, as ``anchors`` resolved it.
    slug : str
        Folder name the verb was aimed at. A record naming another thread
        stays.

    Returns
    -------
    None
    """
    final = thread_record(session)
    try:
        if final.read_text(encoding='utf-8').strip() == slug:
            final.unlink()
    except OSError:
        pass
