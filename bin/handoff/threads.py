"""Which thread a session is on, and the state directory that records it.
"""

import os
import pathlib

# The per-session cache the hooks key by session id. The status line
# reads the thread file from it, so hq and the hooks must agree on the
# directory and on the HQ_STATE_DIR that moves it.
STATE_DIR = os.path.expanduser('~/.claude/cache/claude-handoff')


_WORKED_NAME = '.hq.worked'


DEFAULT_STATE = pathlib.Path.home() / '.claude' / 'cache' / 'claude-handoff'


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
        ``<slug>.thread`` under HQ_STATE_DIR, beside the state the
        hooks keep. The file need not exist.

    Notes
    -----
    - The file holds the slug and nothing else. The status line
      repaints continuously, so it reads one short line rather than
      parsing the document two hooks already read and rewrite whole.
    - One file per session id, and the id is the one the ledger
      records. Concurrent sessions on different threads never write
      the same path, and the line agrees with the ledger on whose
      cycle this is.
    """
    safe = session.replace('/', '_')
    state_dir = pathlib.Path(os.environ.get('HQ_STATE_DIR', STATE_DIR))
    return state_dir / f'{safe}.thread'


def record_worked(session: str, folder: pathlib.Path) -> None:
    """Make folder the session's one worked thread, whose finish moves
    ``Last worked``.

    Parameters
    ----------
    session : str
        Session id, as ``anchors`` resolved it. An empty or 'unknown'
        id records nothing, since every shell without an id shares it.
    folder : pathlib.Path
        Handoff folder. Its ``.hq.worked`` takes the id over any other
        session's, and a sibling's record of this id is removed.
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
    """Resolved path of folder or the sibling whose ``.hq.worked`` holds
    session; '' for none.
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
    - A cache that cannot be written costs the status line its slug
      and nothing else, so nothing here raises into a verb that has
      already done its work.
    """
    final = thread_record(session)
    # Notes:
    # - The rename is what makes the write safe to read. A status line
    #   repainting during a plain write reads a truncated slug, which
    #   raises nothing and renders as a real thread. Two writers
    #   racing one session now get one whole slug or the other.
    # - The staging name carries the pid, so a subagent writing
    #   beside its parent cannot land in the other's half-written
    #   file before either rename runs.
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
        Folder name the verb was aimed at.

    Returns
    -------
    None

    Notes
    -----
    - Only a record naming this very thread is removed. Finishing one
      thread from a session working another says nothing about the
      one it is working.
    """
    final = thread_record(session)
    try:
        if final.read_text(encoding='utf-8').strip() == slug:
            final.unlink()
    except OSError:
        pass
