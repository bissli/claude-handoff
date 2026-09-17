"""The bin/hq wrapper: the command the plugin puts on the agent's PATH."""

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bin_hq_forwards_its_arguments_to_the_script(tmp_path):
    """Verify bin/hq runs bin/hq.py with the arguments it was given.

    Mutation: the wrapper's path to hq.py wrong, the exec bit dropped,
    "$@" left off, or its quotes dropped so a root with a space splits
    into two arguments.
    Oracle: a differential run of python3 bin/hq.py on the same argv;
    the root holds no handoff, so the script prints its own `hq list: no
    handoff under <root>/.handoff` line, which the wrapper must match on
    stdout and exit code.
    """
    root = tmp_path / 'a dir'
    root.mkdir()
    argv = ['--root', str(root), 'list']
    direct = subprocess.run(
        [sys.executable, str(ROOT / 'bin' / 'hq.py'), *argv],
        capture_output=True, text=True)
    wrapped = subprocess.run(
        [str(ROOT / 'bin' / 'hq'), *argv], capture_output=True, text=True)
    assert os.access(ROOT / 'bin' / 'hq', os.X_OK)
    assert direct.returncode == 0
    assert direct.stdout == f'hq list: no handoff under {root}/.handoff\n'
    assert (wrapped.returncode, wrapped.stdout) == (
        direct.returncode, direct.stdout)


def test_bin_hq_execs_a_sibling_of_its_own_directory(tmp_path):
    """Verify the wrapper execs a path inside the directory holding it.

    Mutation: the program addressed through the plugin root, as
    "$root/scripts/hq.py" or "$(dirname "$0")/../scripts/hq.py". The
    plugin eval sandbox exposes only the directory it puts on PATH, so
    every such path names a directory absent there and the entry point
    never loads, which is how one whole eval pass scored against a
    plugin whose hq never ran.
    Oracle: the exec line sh -x prints, its dirname compared against the
    directory holding the wrapper, and read for a literal '..'.
    """
    traced = subprocess.run(
        ['sh', '-x', str(ROOT / 'bin' / 'hq'), '--help'],
        capture_output=True, text=True)
    execs = [ln for ln in traced.stderr.splitlines() if 'exec python3' in ln]
    assert len(execs) == 1, traced.stderr
    assert '..' not in execs[0]
    execd = pathlib.Path(execs[0].split('exec python3 ')[1].split(' ')[0])
    assert execd.parent == ROOT / 'bin'
    assert execd.name == 'hq.py'


def test_bin_hq_runs_through_a_symlink_on_the_path(tmp_path):
    """Verify a symlinked wrapper resolves the real root, not the link's.

    Mutation: dropping the readlink resolution, which sends $0's dirname
    to the directory holding the link, so the program is looked for
    where it is not. A PATH entry that symlinks the wrapper is the shape
    that breaks.
    Oracle: a differential run of the real wrapper on the same argv, both
    the exit code and the stdout line naming the root.
    """
    link = tmp_path / 'hq'
    link.symlink_to(ROOT / 'bin' / 'hq')
    root = tmp_path / 'work'
    root.mkdir()
    argv = ['--root', str(root), 'list']
    direct = subprocess.run(
        [str(ROOT / 'bin' / 'hq'), *argv], capture_output=True, text=True)
    linked = subprocess.run(
        [str(link), *argv], capture_output=True, text=True, cwd='/')
    assert direct.stdout == f'hq list: no handoff under {root}/.handoff\n'
    assert (linked.returncode, linked.stdout) == (
        direct.returncode, direct.stdout)
