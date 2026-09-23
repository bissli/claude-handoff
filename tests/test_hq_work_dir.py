"""The work-dir pin: per-thread resolution, the checks, and the advisories.

Each test pins one defect of the placement machinery: a pin read from a
global source or another thread, a refused token that still writes the pin,
a missing directory accepted or made, a kind folder whose files keep their
name-based kind, and a finish advisory that fires on a row placed under an
earlier ruling.
"""

import pathlib

from bin import hq

_SLUG = 'wd-slug'
_SESSION = 'session-wd'
_HOST = 'test-host'
_NOW = '2026-09-11T12:00:00'
_DEFAULT_TAIL = (
    ' (default) - made files go under specs/, drafts/, notes/, or outputs/;'
    ' any other folder is one unit')
_PIN_TAIL = (
    ' (pin) - specs, drafts, and outputs go there, stamped by their ~ or'
    ' absolute path; notes stay under notes/')


def _root(tmp_path, monkeypatch):
    """Create an HQ_ROOT with the HQ_* environment set; return the root."""
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root


def _strip_read(out: str) -> str:
    """Remove the appended read topic from open output before comparing."""
    return out.replace(hq.HELP_TOPICS['read'] + '\n', '')


def _pin(root, slug=_SLUG):
    """Return the thread's pin text, or None when no pin exists."""
    pin = root / '.handoff' / slug / 'work-dir'
    return pin.read_text() if pin.is_file() else None


def _cursor(folder):
    """Give the thread's HANDOFF.md a Task and a Now so finish accepts it."""
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(handoff.read_text().replace(
        '## Task\n', '## Task\nT.\n').replace('## Now\n', '## Now\nN.\n'))


def test_no_pin_is_the_default_and_only_the_first_begin_names_the_pin_verb(
        tmp_path, monkeypatch, capsys):
    """With no pin the folder's kind folders are the work dir: the verb exits
    0, and begin ends its first work list in the pin command, later ones in
    the one-unit rule.

    Mutation: an unpinned thread treated as a finding (exit 1); the pin
    command printed every cycle or never; a print that writes the pin.
    Oracle: the hand-written begin lines for cycles 1 and 2, the verb's
    line, and no pin file throughout.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    assert hq.main(['begin', _SLUG]) == 0
    assert (
        f'work dir: .handoff/{_SLUG}/ (default) - made files go under specs/,'
        ' drafts/, notes/, or outputs/; when the spec and experiments already'
        f' live in a project directory, hq work-dir {_SLUG} <dir> pins it\n'
        f'cycle 1 begun by {_SESSION} on {_HOST}\n') in capsys.readouterr().out
    assert hq.main(['work-dir', _SLUG]) == 0
    assert capsys.readouterr().out == f'work dir: .handoff/{_SLUG}/{_DEFAULT_TAIL}\n'
    _cursor(folder)
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    capsys.readouterr()
    assert hq.main(['begin', _SLUG]) == 0
    assert f'work dir: .handoff/{_SLUG}/{_DEFAULT_TAIL}\n' in capsys.readouterr().out
    assert _pin(root) is None


def test_a_pin_is_the_threads_own_and_no_global_source_is_read(
        tmp_path, monkeypatch, capsys):
    """A pin lives in the thread's folder and binds that thread alone; the
    HQ_WORK_DIR variable and a .handoff/work-dir file move nothing, and the
    pin file is never listed as unstamped.

    Mutation: the pin read from the root's .handoff/ or the environment, so
    one setting moves every thread; 'work-dir' dropped from the walk's
    skip list, so each begin nags about the pin.
    Oracle: the pinned thread prints 'docs (pin)' and the other the default
    with both global sources set; the pinned thread's second begin has no
    unstamped line.
    """
    root = _root(tmp_path, monkeypatch)
    (root / 'docs').mkdir()
    (root / 'lab').mkdir()
    other = 'other-slug'
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['begin', other]) == 0
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG, str(root / 'docs')]) == 0
    assert capsys.readouterr().out == (
        f'work dir: docs - pinned in .handoff/{_SLUG}/work-dir\n')
    assert _pin(root) == 'docs\n'
    assert _pin(root, other) is None
    monkeypatch.setenv('HQ_WORK_DIR', 'lab')
    (root / '.handoff' / 'work-dir').write_text('lab\n')
    assert hq.main(['work-dir', other]) == 0
    assert capsys.readouterr().out == f'work dir: .handoff/{other}/{_DEFAULT_TAIL}\n'
    assert hq.main(['work-dir', _SLUG]) == 0
    assert capsys.readouterr().out == f'work dir: docs{_PIN_TAIL}\n'
    _cursor(root / '.handoff' / _SLUG)
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    monkeypatch.setenv('HQ_CYCLE', '2')
    capsys.readouterr()
    assert hq.main(['begin', _SLUG]) == 0
    out = capsys.readouterr().out
    assert f'work dir: docs{_PIN_TAIL}\n' in out
    assert 'unstamped' not in out


def test_an_outside_directory_pins_by_its_tilde_or_absolute_spelling(
        tmp_path, monkeypatch, capsys):
    """A directory outside the root passes and is stored as ~/... under home
    (home itself as ~), else absolutely, and reads back through the same
    HOME; one under the root is stored root-relative from any spelling.

    Mutation: the outside-root refusal kept; the token stored as typed, so
    a clone at another path or another user reads a dead pin; home itself
    stored as '~/.'; a ~ pin resolved without expanding it.
    Oracle: the pin text after each spelling, hand-written; the ruling line
    for the ~ pin, and 'does not exist' once HOME points elsewhere.
    """
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(hq, '_TEMP_DIRS', ())
    home = pathlib.Path(tmp_path) / 'home'
    (home / 'notes').mkdir(parents=True)
    elsewhere = pathlib.Path(tmp_path) / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.setenv('HOME', str(home))
    assert hq.main(['begin', _SLUG]) == 0

    assert hq.main(['work-dir', _SLUG, str(home / 'notes')]) == 0
    assert _pin(root) == '~/notes\n'
    assert hq.main(['work-dir', _SLUG, '~/notes/']) == 0
    assert _pin(root) == '~/notes\n'
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG]) == 0
    assert capsys.readouterr().out == f'work dir: ~/notes{_PIN_TAIL}\n'
    monkeypatch.setenv('HOME', str(elsewhere))
    assert hq.main(['work-dir', _SLUG]) == 1
    assert capsys.readouterr().out.startswith(
        f".handoff/{_SLUG}/work-dir names '~/notes': does not exist - ")
    monkeypatch.setenv('HOME', str(home))
    assert hq.main(['work-dir', _SLUG, '~']) == 0
    assert _pin(root) == '~\n'
    assert hq.main(['work-dir', _SLUG, str(elsewhere)]) == 0
    assert _pin(root) == f'{elsewhere}\n'
    (root / 'docs' / 'design').mkdir(parents=True)
    assert hq.main(['work-dir', _SLUG, str(root / 'docs' / 'design')]) == 0
    assert _pin(root) == 'docs/design\n'
    assert hq.main(['work-dir', _SLUG, 'docs/design/']) == 0
    assert _pin(root) == 'docs/design\n'
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG]) == 0
    assert capsys.readouterr().out == f'work dir: docs/design{_PIN_TAIL}\n'


def test_a_root_reached_through_a_symlink_is_itself_and_owns_its_directories(
        tmp_path, monkeypatch, capsys):
    """The root spelled through a symlink is still the root, and a directory
    under it spelled either way is stored root-relative.

    Mutation: the root equality tested on the lexical spelling alone, so
    the root is pinned by its other name and becomes the dump; the realpath
    containment branch dropped, so the other spelling of an inside
    directory is stored absolutely.
    Oracle: 'is the root itself' for the real path with HQ_ROOT the link
    and for the link with HQ_ROOT the real path; 'docs' in the pin from
    the link spelling.
    """
    real_root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(hq, '_TEMP_DIRS', ())
    link = pathlib.Path(tmp_path) / 'link'
    link.symlink_to(real_root, target_is_directory=True)
    (real_root / 'docs').mkdir()
    assert hq.main(['begin', _SLUG]) == 0
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG, str(link)]) == 1
    assert capsys.readouterr().out.startswith(f'hq work-dir: {link} is the root itself')
    monkeypatch.setenv('HQ_ROOT', str(link))
    assert hq.main(['work-dir', _SLUG, str(real_root)]) == 1
    assert capsys.readouterr().out.startswith(
        f'hq work-dir: {real_root} is the root itself')
    assert hq.main(['work-dir', _SLUG, str(real_root / 'docs')]) == 0
    assert _pin(real_root) == 'docs\n'
    monkeypatch.setenv('HQ_ROOT', str(real_root))
    assert hq.main(['work-dir', _SLUG, str(link / 'docs')]) == 0
    assert _pin(real_root) == 'docs\n'


def test_refused_tokens_write_no_pin_and_each_names_its_reason(
        tmp_path, monkeypatch, capsys):
    """The root - spelled '.' or as the empty token - .handoff/ and anything
    under it, a temp path, a missing path, and a plain file are refused with
    nothing written; --clear with a directory, empty or not, is a usage
    error.

    Mutation: any one check dropped, so a dump target, a temp path, or a
    typo is pinned and every later session writes there; the missing
    directory made instead of refused.
    Oracle: exit 1 and no pin file for each token; the printed reason
    clause per token, hand-listed; exit 2 for the flag clash.
    """
    root = _root(tmp_path, monkeypatch)
    (root / '.handoff' / 'other').mkdir(parents=True)
    (root / 'afile').write_text('x\n')
    assert hq.main(['begin', _SLUG]) == 0
    cases = {
        '.': 'is the root itself',
        '': 'is the root itself',
        '.handoff': 'is under .handoff/',
        '.handoff/other': 'is under .handoff/',
        f'.handoff/{_SLUG}/specs': 'is under .handoff/',
        '/tmp': 'is under the system temp directory',
        'missing/dir': 'does not exist',
        'afile': 'is not a directory',
        }
    for token, reason in cases.items():
        capsys.readouterr()
        assert hq.main(['work-dir', _SLUG, token]) == 1, token
        out = capsys.readouterr().out
        assert out.startswith(f'hq work-dir: {token} {reason} - name a directory'), out
    assert hq.main(['work-dir', _SLUG, 'afile', '--clear']) == 2
    assert hq.main(['work-dir', _SLUG, '', '--clear']) == 2
    assert _pin(root) is None
    assert not (root / 'missing').exists()


def test_clear_removes_the_pin_and_a_bad_pin_is_reported_never_fatal(
        tmp_path, monkeypatch, capsys):
    """A pin whose directory vanished, that is empty, or that cannot be read
    is named with its cause and move by the verb (exit 1), begin, and finish,
    each falling back to the folder and running on; --clear removes it and
    is idempotent.

    Mutation: a bad pin aborting begin or finish; the verb exiting 0 on it;
    --clear leaving the pin or failing with none to remove; hq recreating
    the vanished directory; an unreadable pin reported as empty.
    Oracle: the hand-written pin line and default line, the exit codes, the
    pin file's absence after --clear, and the directory still absent.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    (root / 'lab').mkdir()
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['work-dir', _SLUG, 'lab']) == 0
    (root / 'lab').rmdir()
    bad_line = (
        f".handoff/{_SLUG}/work-dir names 'lab': does not exist - repin it with"
        f' hq work-dir {_SLUG} <dir>, or --clear to fall back to the folder')
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG]) == 1
    assert capsys.readouterr().out.splitlines() == [
        bad_line, f'work dir: .handoff/{_SLUG}/{_DEFAULT_TAIL}']
    _cursor(folder)
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    assert bad_line in capsys.readouterr().out
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    out = capsys.readouterr().out
    assert bad_line in out
    assert f'work dir: .handoff/{_SLUG}/{_DEFAULT_TAIL}' in out
    assert not (root / 'lab').exists()

    assert hq.main(['work-dir', _SLUG, '--clear']) == 0
    assert capsys.readouterr().out == f'work dir: .handoff/{_SLUG}/{_DEFAULT_TAIL}\n'
    assert _pin(root) is None
    assert hq.main(['work-dir', _SLUG, '--clear']) == 0
    (folder / 'work-dir').write_text('\n')
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG]) == 1
    assert capsys.readouterr().out.startswith(
        f".handoff/{_SLUG}/work-dir names '': is empty - repin it")
    (root / 'lab').mkdir()
    (folder / 'work-dir').write_text('lab\n')
    (folder / 'work-dir').chmod(0)
    try:
        assert hq.main(['work-dir', _SLUG]) == 1
        assert capsys.readouterr().out.startswith(
            f".handoff/{_SLUG}/work-dir names '': cannot be read - repin it")
    finally:
        (folder / 'work-dir').chmod(0o600)


def test_a_directory_under_the_pins_name_is_named_by_every_surface(
        tmp_path, monkeypatch, capsys):
    """A folder made where the pin file belongs is named with its move by
    the verb, a pin or clear attempt, begin, and finish; none writes, and
    the cycle runs on.

    Mutation: the is_dir test dropped, so the verb prints the default
    ruling with exit 0, a pin or clear attempt dies in the generic OSError
    handler naming no move, and begin and finish stay silent while the
    spec inside the folder goes ungated and unlisted.
    Oracle: the hand-written line on each surface, the exit codes, and
    the folder with its file still on disk afterward.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    (root / 'lab').mkdir()
    assert hq.main(['begin', _SLUG]) == 0
    (folder / 'work-dir').mkdir()
    (folder / 'work-dir' / 'SPEC.md').write_text('# Spec\n')
    dir_line = (
        f'.handoff/{_SLUG}/work-dir is a directory - the pin is a one-line file;'
        ' move the folder under specs/, drafts/, notes/, or outputs/')
    capsys.readouterr()
    assert hq.main(['work-dir', _SLUG]) == 1
    assert capsys.readouterr().out.splitlines() == [
        dir_line, f'work dir: .handoff/{_SLUG}/{_DEFAULT_TAIL}']
    assert hq.main(['work-dir', _SLUG, 'lab']) == 1
    assert capsys.readouterr().out == dir_line + '\n'
    assert hq.main(['work-dir', _SLUG, '--clear']) == 1
    assert capsys.readouterr().out == dir_line + '\n'
    _cursor(folder)
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    assert dir_line in capsys.readouterr().out
    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['begin', _SLUG]) == 0
    assert dir_line in capsys.readouterr().out
    assert (folder / 'work-dir' / 'SPEC.md').is_file()


def test_a_kind_folder_sets_the_kind_and_the_walk_lists_its_files(
        tmp_path, monkeypatch, capsys):
    """A file one level under specs/, drafts/, notes/, or outputs/ takes the
    folder's kind whatever its name but a snapshot-shaped one; the walk
    lists it and not the folder.

    Mutation: name-based inference kept for nested files, so specs/auth.md
    lands other/never and drafts/x.py other; the snapshot rule dropped
    below the kind folder, so specs/old.bak is gated as a spec; the walk
    listing the kind folder as one probe-dir entry.
    Oracle: the inferred rows read from the ledger; the begin work list
    naming 'specs/auth.md' and the exact probe-dir line, and grading a
    HANDOFF-named file under specs/ as the spec the stamp will record; a
    .py two levels down other; the bare token 'specs' refused with no row.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    for sub in ('specs', 'drafts', 'notes', 'outputs', 'probes'):
        (folder / sub).mkdir(parents=True)
    (folder / 'specs' / 'auth.md').write_text('# Auth\n\nBody.\n')
    (folder / 'specs' / 'HANDOFF-old.md').write_text('# Old\n')
    (folder / 'drafts' / 'x.py').write_text('x = 1\n')
    (folder / 'notes' / 'quirks.md').write_text('# Quirks\n')
    (folder / 'outputs' / 'SPEC-chart.md').write_text('# Spec\n')
    (folder / 'specs' / 'old.bak').write_text('# Old\n')
    (folder / 'probes' / 'p.py').write_text('y = 2\n')
    (folder / 'drafts' / 'deep').mkdir()
    (folder / 'drafts' / 'deep' / 'z.py').write_text('z = 3\n')
    assert hq.main(['begin', _SLUG]) == 0
    work_list = capsys.readouterr().out
    assert '\n  unstamped spec x2: specs/HANDOFF-old.md, specs/auth.md' in work_list
    assert '\n  unstamped snapshot x1: specs/old.bak' in work_list
    assert '\n  unstamped probe-dir x2: probes, drafts/deep - stamp each' in work_list

    anchors = {'specs/auth.md': ['--where', 'Auth'],
               'specs/HANDOFF-old.md': ['--where', 'Old']}
    for token in ('specs/auth.md', 'specs/HANDOFF-old.md', 'drafts/x.py',
                  'notes/quirks.md', 'outputs/SPEC-chart.md', 'specs/old.bak',
                  'probes/p.py', 'drafts/deep/z.py'):
        assert hq.main(['stamp', _SLUG, token] + anchors.get(token, [])) == 0, token
    capsys.readouterr()
    assert hq.main(['stamp', _SLUG, 'specs']) == 2
    assert capsys.readouterr().out.startswith('hq stamp: specs is a kind folder - ')

    rows = (folder / 'ledger.tsv').read_text().splitlines()[1:]
    kinds = {r.split('\t')[2]: (r.split('\t')[4], r.split('\t')[6]) for r in rows}
    assert kinds == {
        'specs/auth.md': ('spec', 'always'),
        'specs/HANDOFF-old.md': ('spec', 'always'),
        'drafts/x.py': ('draft', 'edit'),
        'notes/quirks.md': ('notes', 'never'),
        'outputs/SPEC-chart.md': ('other', 'never'),
        'specs/old.bak': ('snapshot', 'never'),
        'probes/p.py': ('other', 'never'),
        'drafts/deep/z.py': ('other', 'never'),
        }


def test_a_file_stamped_inside_a_folder_records_the_folder(
        tmp_path, monkeypatch, capsys):
    """A folder of the work's own whose file carries a row is not listed as
    unstamped by begin, finish, or artifacts.

    Mutation: the recorded test reduced to a row under the folder's own
    path, so a folder stamped file by file is nagged every cycle.
    Oracle: no 'probe-dir' unstamped line after the inner stamp, and one
    before it.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    (folder / 'experiments').mkdir(parents=True)
    (folder / 'experiments' / 'e1.py').write_text('e = 1\n')
    assert hq.main(['begin', _SLUG]) == 0
    assert 'unstamped probe-dir x1: experiments' in capsys.readouterr().out
    assert hq.main(['stamp', _SLUG, 'experiments/e1.py', '--label', 'first run']) == 0
    _cursor(folder)
    capsys.readouterr()
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    assert 'probe-dir' not in capsys.readouterr().out
    assert hq.main(['artifacts', _SLUG]) == 0
    assert 'unstamped' not in capsys.readouterr().out


def test_finish_names_a_made_file_first_stamped_this_cycle_against_the_work_dir(
        tmp_path, monkeypatch, capsys):
    """Under a pin, a spec, draft, or output first stamped in the folder this
    cycle is named - loose at the top level or filed under specs/, drafts/,
    or outputs/ - and a draft stamped in the pinned directory by its path
    is not; unpinned, only a loose top-level made file is. A notes file, a
    directory of any name, and a row from an earlier cycle never are.

    Mutation: the first-cycle test dropped, so every old row is nagged each
    cycle; a spec or any one kind folder exempted under the pin; the base
    test dropped, so the pinned directory's own file is named; notes or a
    directory counted as made; the unpinned case flagging a file already
    filed under a kind folder or a folder of the agent's own name.
    Oracle: the hand-written advisory lines and their absence, per case,
    and the pinned file gated in the rendered artifacts block.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    (folder / 'specs').mkdir(parents=True)
    (folder / 'probes').mkdir()
    (folder / 'SPEC-old.md').write_text('# Spec\n\nOld.\n')
    (folder / 'proto.py').write_text('p = 1\n')
    (folder / 'notes-a.md').write_text('# A\n')
    (folder / 'outputs').mkdir()
    (folder / 'outputs' / 'table.csv').write_text('a,b\n')
    (folder / 'notes').mkdir()
    (folder / 'notes' / 'quirks.md').write_text('# Q\n')
    (folder / 'reviews').mkdir()
    (folder / 'reviews' / 'skeptic.md').write_text('# Skeptic\n')
    (folder / 'specs' / 'in.md').write_text('# In\n')
    (folder / 'drafts').mkdir()
    (folder / 'drafts' / 'd.py').write_text('d = 1\n')
    (root / 'working').mkdir()
    (root / 'working' / 'w.py').write_text('w = 1\n')
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['work-dir', _SLUG, 'working']) == 0
    anchors = {'SPEC-old.md': ['--where', 'Spec'], 'specs/in.md': ['--where', 'In']}
    for token in ('SPEC-old.md', 'proto.py', 'notes-a.md', 'probes',
                  'outputs/table.csv', 'notes/quirks.md', 'reviews/skeptic.md',
                  'specs/in.md', 'drafts/d.py'):
        assert hq.main(['stamp', _SLUG, token] + anchors.get(token, [])) == 0, token
    assert hq.main([
        'stamp', _SLUG, str(root / 'working' / 'w.py'), '--kind', 'draft']) == 0
    _cursor(folder)
    capsys.readouterr()
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    out = capsys.readouterr().out
    assert ('advisory: made in the folder x5: SPEC-old.md, proto.py,'
            ' outputs/table.csv, specs/in.md, drafts/d.py'
            ' - move each to working, or under notes/ when it is evidence,'
            " then re-stamp with --successor and the file's ~ or absolute path") in out
    rendered = (folder / 'HANDOFF.md').read_text()
    assert f'\nroot {root}\n' in rendered
    assert '\nworking/w.py  draft  edit  c1  -\n' in rendered

    monkeypatch.setenv('HQ_CYCLE', '2')
    assert hq.main(['work-dir', _SLUG, '--clear']) == 0
    (folder / 'SPEC-new.md').write_text('# Spec\n\nNew.\n')
    (folder / 'specs' / 'inside.md').write_text('# Inside\n')
    (folder / 'experiments').mkdir()
    assert hq.main(['begin', _SLUG]) == 0
    assert hq.main(['stamp', _SLUG, 'SPEC-old.md', '--label', 'old, re-stamped']) == 0
    assert hq.main(['stamp', _SLUG, 'SPEC-new.md', '--where', 'Spec']) == 0
    assert hq.main(['stamp', _SLUG, 'specs/inside.md', '--where', 'Inside']) == 0
    assert hq.main(['stamp', _SLUG, 'experiments']) == 0
    capsys.readouterr()
    assert hq.main(['finish', _SLUG, '--log', 'c2']) == 0
    out = capsys.readouterr().out
    assert ('advisory: made at the top level x1: SPEC-new.md'
            ' - move each under specs/, drafts/, notes/, or outputs/'
            ' and re-stamp with --successor') in out
    assert not any(
        'SPEC-old.md' in ln for ln in out.splitlines()
        if ln.startswith('advisory: made'))


def test_a_work_dir_under_a_former_handoff_name_is_not_a_stale_path(
        tmp_path, monkeypatch, capsys):
    """A cursor line naming working/<slug>/ is stale only while no segment
    of this thread's pinned work dir is working/.

    Mutation: the stale scan reading _FORMER_HANDOFF_DIRNAMES unfiltered;
    the filter comparing the whole pin to the bare name, so a pin inside
    working/ keeps its own paths in the scan; filtering on another
    thread's pin.
    Oracle: the stale line with no pin, none once this thread pins a
    directory under working/.
    """
    root = _root(tmp_path, monkeypatch)
    folder = root / '.handoff' / _SLUG
    assert hq.main(['begin', _SLUG]) == 0
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(handoff.read_text().replace(
        '## Task\n', '## Task\nT.\n').replace(
        '## Now\n', f'## Now\nRun working/{_SLUG}/proto.py again.\n'))
    assert hq.main(['finish', _SLUG, '--log', 'c1']) == 0
    capsys.readouterr()
    assert hq.main(['open', _SLUG]) == 0
    assert f'stale folder path in HANDOFF.md: working/{_SLUG}/ x1' in (
        _strip_read(capsys.readouterr().out))
    (root / 'working' / _SLUG).mkdir(parents=True)
    assert hq.main(['work-dir', _SLUG, f'working/{_SLUG}']) == 0
    assert hq.main(['open', _SLUG]) == 0
    assert 'stale folder path' not in _strip_read(capsys.readouterr().out)
