"""Every line hq.py prints, driven once and asserted verbatim.

The skill tells the agent what to do about each message the script
prints, so each message is a contract. These tests drive each refusal,
advisory, and status path and pin its exact text and exit code; the
contract tests hold the same text to the skill.
"""

import contextlib
import io
import os
import pathlib
import re
import shutil
import subprocess

from scripts import hq

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'handoff')
EXAMPLE = (pathlib.Path(HERE).parent / 'skills' / 'handoff' / 'reference'
           / 'example-handoff.md')
_SLUG = 'msg-slug'
_SESSION = 'session-abc'


def _root(tmp_path, monkeypatch, git=False):
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', '2026-09-09T12:00:00')
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', 'test-host')
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    if git:
        monkeypatch.delenv('HQ_GIT', raising=False)
    else:
        monkeypatch.setenv('HQ_GIT', '0')
    folder = root / '.handoff' / _SLUG
    folder.mkdir(parents=True)
    return folder


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = hq.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _lock(folder, session, time_str, cycle=1):
    (folder / '.hq.lock').write_text(
        f'slug={folder.name}\nsession={session}\nhost=other-host\n'
        f'time={time_str}\ncycle={cycle}\n')


def _spec(folder, text='# Spec\n\n## 1. Scope\n\nOne.\n'):
    (folder / 'SPEC.md').write_text(text)


def test_slug_resolution_names_the_candidates_or_the_miss(tmp_path, monkeypatch):
    """An ambiguous or unknown slug exits 2 with the documented stdout line.

    Mutation: exit 1 for a slug problem, the candidate list dropped, or
    the line sent to stderr, where the agent's captured output loses it.
    Oracle: two folders sharing a prefix; both names in the message.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder.parent / 'msg-two').mkdir()
    rc, out, err = _run(['open', 'msg'])
    assert rc == 2
    assert out.startswith("hq: ambiguous slug 'msg': ")
    assert 'msg-slug' in out
    assert 'msg-two' in out
    assert err == ''
    rc, out, err = _run(['open', 'zzz'])
    assert rc == 2
    assert out.startswith(f"hq: no folder matching 'zzz' under {folder.parent}")
    assert err == ''


def test_adopt_refusals_name_their_cause(tmp_path, monkeypatch):
    """Adopt exits 1 with a distinct line for no file, a foreign header,
    and an adopted folder, printing the heading inventory for the second.

    Mutation: exit 2 on any of the three, or the inventory dropped.
    Oracle: the three documented lines; the fixture's own headings.
    """
    folder = _root(tmp_path, monkeypatch)
    rc, out, _ = _run(['adopt', _SLUG])
    assert (rc, out.strip()) == (1, f'hq adopt: no HANDOFF.md in {folder}')
    shutil.copy(
        os.path.join(FIXTURES, 'legacy-import-notes', 'HANDOFF.md'), folder / 'HANDOFF.md')
    rc, out, _ = _run(['adopt', _SLUG])
    assert rc == 1
    lines = out.splitlines()
    _adoption_ref = str(
        pathlib.Path(hq.__file__).resolve().parent.parent
        / 'skills' / 'handoff' / 'reference' / 'adoption.md')
    assert lines[0] == (
        'hq adopt: non-conforming header; write a conforming HANDOFF.md first'
        f' - the conversion is in {_adoption_ref}')
    assert '  # Legacy CSV importer' in lines
    assert '  ## Summary' in lines
    shutil.rmtree(folder)
    shutil.copytree(os.path.join(FIXTURES, 'orbit-cache-rewrite'), folder)
    assert _run(['adopt', _SLUG])[0] == 0
    rc, out, _ = _run(['adopt', _SLUG])
    assert (rc, out.strip()) == (1, 'hq adopt: already adopted; ledger.tsv exists')


def test_adopt_summary_reports_labels_obligations_and_the_gated_set(
        tmp_path, monkeypatch):
    """Adopt prints the seeded count, each label, read_before counts, and
    the gated names.

    Mutation: a summary line dropped, or the counts keyed on the wrong
    field.
    Oracle: the orbit fixture seeds three walk entries - a snapshot at
    never, a spec at always, a notes file graded edit - so two are gated.
    """
    folder = _root(tmp_path, monkeypatch)
    shutil.rmtree(folder)
    shutil.copytree(os.path.join(FIXTURES, 'orbit-cache-rewrite'), folder)
    rc, out, _ = _run(['adopt', _SLUG])
    assert rc == 0
    lines = out.splitlines()
    assert f'hq adopt: seeded 3 entries in {_SLUG}' in lines
    assert any(ln.startswith('  label: ') for ln in lines)
    assert '  read_before=always: 1' in lines
    assert '  read_before=edit: 1' in lines
    assert '  read_before=never: 1' in lines
    gated = next(ln for ln in lines if ln.startswith('  gated (2): '))
    assert 'SPEC.md' in gated
    assert 'notes-algos.md' in gated


def test_begin_announces_creation_adoption_and_a_saved_hand_edit(
        tmp_path, monkeypatch):
    """Begin prints the created path, the adopt it ran, and the .hand.md
    copy of a file edited since the last finish.

    Mutation: any of the three lines dropped, or the hand copy named for
    the wrong cycle.
    Oracle: the documented lines; c01.hand.md after the first finish.
    """
    folder = _root(tmp_path, monkeypatch)
    shutil.rmtree(folder)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert f'hq begin: created {folder}' in out
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    (folder / 'HANDOFF.md').write_text(
        (folder / 'HANDOFF.md').read_text().replace('## Task', '## Task\nEdited by hand.'))
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: HANDOFF.md changed since last finish; saved to c01.hand.md' in out
    assert (folder / 'cycles' / 'c01.hand.md').exists()
    shutil.rmtree(folder)
    shutil.copytree(os.path.join(FIXTURES, 'orbit-cache-rewrite'), folder)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: ran adopt on existing HANDOFF.md' in out


def test_begin_refuses_a_young_lock_and_names_a_takeover(tmp_path, monkeypatch):
    """A foreign lock under two hours prints the --force line and exits 1;
    an older one is taken over with the two documented lines.

    Mutation: the age comparison flipped, or the takeover lines dropped.
    Oracle: locks 1h59m and 2h01m old against HQ_NOW.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _run(['finish', _SLUG, '--log', 'one'])
    _lock(folder, 'other-session', '2026-09-09T10:01:00', cycle=2)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 1
    assert out.strip() == (
        'hq begin: lock held by other-session on other-host since'
        ' 2026-09-09T10:01:00; use --force to take over')
    _lock(folder, 'other-session', '2026-09-09T09:59:00', cycle=2)
    rc, out, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'hq begin: took over from other-session' in out
    assert ('cycle 2 begun by other-session on other-host at 2026-09-09T09:59:00,'
            ' never finished') in out


def test_stamp_refusals_and_usage_errors_print_their_documented_lines(
        tmp_path, monkeypatch):
    """The R1 refusal, the archive and receipt-prefix usage errors, and the
    batch parse error each print their exact line.

    Mutation: a message reworded or the usage exit code set to 1.
    Oracle: the four documented lines; batch line 2 is the bad one and
    line 1 still lands.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--read-before', 'never'])
    assert rc == 1
    assert out.strip() == (
        'hq stamp: refused: R1: spec read_before must stay always'
        ' without --successor or --archive'
        ' - supply a live --successor or --archive --reason, or leave the row gated')
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--archive'])
    assert (rc, out.strip()) == (2, 'hq stamp: --archive requires --reason')
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--reason', 'refused: no'])
    assert (rc, out.strip()) == (
        2, "hq stamp: --reason may not start with 'refused: ', the receipt prefix")
    monkeypatch.setattr('sys.stdin', io.StringIO(
        'SPEC.md --where Spec --label "first row"\nSPEC.md --bogus\n'))
    rc, out, _ = _run(['stamp', _SLUG, '--batch'])
    assert rc == 2
    assert 'hq stamp: batch line 2 not parsed: SPEC.md --bogus' in out
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    assert rows[-1].endswith('\tfirst row')


def test_note_and_supersede_refusals_print_their_documented_lines(
        tmp_path, monkeypatch):
    """An unknown note kind is a usage error; an unparsable batch line, a
    prefix mismatch, and an unknown id each print their exact line.

    Mutation: any of the three messages reworded, a refusal exiting 0, the
    kind choices dropped from the parser, the good batch line dropped
    alongside the bad one, or the batch body split like a shell line so an
    apostrophe refuses the line or a pair of them strips two letters.
    Oracle: exit 2 for the kind; the three documented lines; d01 and c01
    exist afterward; two prose bodies land in standing.md verbatim.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    assert _run(['note', _SLUG, 'wish', '--headline', 'H', 'body'])[0] == 2
    monkeypatch.setattr('sys.stdin', io.StringIO(
        'decision --headline "Unclosed body\n'
        'constraint --headline "Keep" "the floor"\n'))
    rc, out, _ = _run(['note', _SLUG, '--batch'])
    assert rc == 2
    assert 'hq note: batch line 1 not parsed: decision --headline "Unclosed body' in out
    standing = (folder / 'standing.md').read_text()
    assert '[c01]' in standing
    assert '**Keep** the floor' in standing
    monkeypatch.setattr('sys.stdin', io.StringIO(
        "constraint --headline 'Prose' The user's house (WATCH, PORT) isn't up for it.\n"
        'dead-end --headline "Said" He said "no" (twice); we didn\'t ask again.\n'))
    assert _run(['note', _SLUG, '--batch'])[0] == 0
    standing = (folder / 'standing.md').read_text()
    assert "**Prose** The user's house (WATCH, PORT) isn't up for it." in standing
    assert '**Said** He said "no" (twice); we didn\'t ask again.' in standing
    assert _run(['note', _SLUG, 'decision', '--headline', 'D', 'b'])[0] == 0
    rc, out, _ = _run(['supersede', _SLUG, 'd01', 'c01'])
    assert (rc, out.strip()) == (
        1, "hq supersede: ids must share a prefix ('d01' vs 'c01')")
    rc, out, _ = _run(['supersede', _SLUG, 'd01', 'd99'])
    assert (rc, out.strip()) == (
        1,
        ("hq supersede: 'd99' not found in standing.md"
         f'; run hq standing {_SLUG} to list the ids'))


def test_finish_blocking_lines_name_the_class_that_fired(tmp_path, monkeypatch):
    """Each blocking class prints its documented line and exits 1: a foreign
    lock, W1, a missing gated row, R3, and an untyped Unfiled bullet.

    Mutation: a class silently passing, a message reworded, or W1 not
    reported on a size-preserving edit.
    Oracle: the five documented lines, each provoked in turn.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])[0] == 0
    _lock(folder, 'other-session', '2026-09-09T11:00:00')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'x'])
    assert (rc, out.strip()) == (
        1,
        (f'hq finish: lock held by other-session; this is {_SESSION}'
         f' - run hq begin {_SLUG} first'))
    _lock(folder, _SESSION, '2026-09-09T11:00:00')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    ledger = folder / 'ledger.tsv'
    ledger.write_bytes(ledger.read_bytes().replace(b'\tlive\t', b'\tlive\t', 1)
                       .replace(b'always', b'alwayz', 1))
    rc, out, _ = _run(['finish', _SLUG, '--log', 'x'])
    assert rc == 1
    assert out.startswith('W1: ledger prefix changed')
    ledger.write_bytes(ledger.read_bytes().replace(b'alwayz', b'always', 1))
    (folder / 'SPEC.md').unlink()
    rc, out, _ = _run(['finish', _SLUG, '--log', 'x'])
    assert (rc, out.strip()) == (
        1, 'missing live gated: SPEC.md; use --successor or --archive --reason')
    _spec(folder, '# Spec\n\nchanged\n')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'x'])
    assert (rc, out.strip()) == (1, 'R3: SPEC.md sha moved; re-stamp before finish')
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])[0] == 0
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(handoff.read_text().replace(
        '## Open questions', '## Open questions\n\n## Unfiled\n- foo bar', 1))
    rc, out, _ = _run(['finish', _SLUG, '--log', 'x'])
    assert (rc, out.strip()) == (
        1,
        ("hq finish: untyped Unfiled bullet: '- foo bar'"
         ' - prefix it decision:, constraint:, or dead-end:,'
         ' move it into a cursor section, or rehome it to a sibling'))


def test_finish_success_and_advisory_lines_have_their_documented_shape(
        tmp_path, monkeypatch):
    """A successful finish prints the size line and the resume line, and
    the unstamped and shorter-label advisories name their subject.

    Mutation: the token split dropped, the resume line pointing at hq.py
    open, or an advisory class silent.
    Oracle: the documented shapes; one unstamped notes file and one label
    shortened from 21 to 5 characters.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    (folder / 'notes-x.md').write_text('# Notes\n')
    assert _run(
        ['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--label', 'a long spec label here'])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--label', 'short'])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    lines = out.splitlines()
    assert 'advisory: unstamped notes x1: notes-x.md - stamp each' in lines
    assert (
        'advisory: label shorter than predecessor: SPEC.md'
        ' - check the new label kept every backticked token'
        ' and s<n> reference the old one carried'
        in lines)
    assert re.fullmatch(
        r'\S+/HANDOFF\.md  \d+ cursor lines  \d+ tokens'
        r' \(cursor \d+, read \d+, artifacts \d+, standing \d+\)', lines[-3])
    assert lines[-2] == 'read first: 1 rows, 6 tok (1 anchored, 0 whole)'
    assert lines[-1] == f'resume: /handoff {_SLUG}'


def test_open_reports_each_drift_class_by_its_documented_line(tmp_path, monkeypatch):
    """Open prints the unfinished cycle, W1, LEDGER BEHIND, and a moved sha,
    each in its documented form, and nothing on a clean folder.

    Mutation: any class silent, the cycle comparison flipped, or a line
    reworded.
    Oracle: each condition staged in turn against a finished cycle 1.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec'])[0] == 0
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    assert out.strip() == (
        f'unfinished cycle 1 held by {_SESSION} on test-host'
        ' - the file may be behind its stamps; report it')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['open', _SLUG])[1] == ''
    _spec(folder, '# Spec\n\n## 1. Scope\n\nchanged\n')
    assert _run(['open', _SLUG])[1].strip() == (
        'sha moved since stamp: SPEC.md'
        ' - read the file, not the span alone')
    _spec(folder)
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(handoff.read_text().replace('Cycle: 1', 'Cycle: 9', 1))
    assert 'LEDGER BEHIND: file cycle 9 > last finished 1' in _run(['open', _SLUG])[1]
    handoff.write_text(handoff.read_text().replace('Cycle: 9', 'Cycle: 1', 1))
    ledger = folder / 'ledger.tsv'
    ledger.write_bytes(ledger.read_bytes().replace(b'always', b'alwayz', 1))
    assert _run(['open', _SLUG])[1].startswith('WARNING: W1: ledger prefix changed')


def test_open_reports_git_drift_against_the_header_sha(tmp_path, monkeypatch):
    """A commit after finish makes open print the drift line.

    Mutation: the header sha compared to itself, or the line dropped.
    Oracle: a real repository advanced by one commit; the line names the
    header sha and the new sha.
    """
    folder = _root(tmp_path, monkeypatch, git=True)
    root = folder.parent.parent
    git = ['git', '-C', str(root), '-c', 'user.email=d@example.com', '-c', 'user.name=d']
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'a.txt').write_text('a\n')
    subprocess.run(git + ['add', 'a.txt'], check=True)
    subprocess.run(git + ['commit', '-qm', 'one'], check=True)
    _run(['begin', _SLUG])
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    header = (folder / 'HANDOFF.md').read_text().splitlines()[2]
    old_sha = re.search(r'@ ([0-9a-f]{7})', header).group(1)
    assert _run(['open', _SLUG])[1] == ''
    (root / 'a.txt').write_text('b\n')
    subprocess.run(git + ['commit', '-qam', 'two'], check=True)
    out = _run(['open', _SLUG])[1]
    assert re.fullmatch(
        rf'git drift: header \S+@{old_sha} -> now \S+@[0-9a-f]{{7}}'
        rf' - run git log --oneline {old_sha}\.\.'
        r'HEAD, and -- <todo path> for each todo file the Plan points at\n',
        out), out


def test_read_and_diff_refusals_print_their_documented_lines(tmp_path, monkeypatch):
    """Read names an unstamped path, a vanished file, and an unresolved
    anchor; diff names a cycle that was never finished.

    Mutation: a refusal exiting 0, or the anchor marker dropped.
    Oracle: the four documented lines.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    rc, out, _ = _run(['read', _SLUG, 'ghost.md'])
    assert (rc, out.strip()) == (
        1, 'hq read: ghost.md not in ledger - read it whole by hand')
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Ghost'])[0] == 0
    rc, out, _ = _run(['read', _SLUG, 'SPEC.md'])
    assert (rc, out.strip()) == (
        0,
        ('? unresolved: Ghost'
         ' - read the file whole when no span printed above'))
    (folder / 'SPEC.md').unlink()
    rc, out, _ = _run(['read', _SLUG, 'SPEC.md'])
    assert (rc, out.strip()) == (
        1,
        ('hq read: SPEC.md not on disk'
         ' - re-point, supersede, or archive its row at the next write'))
    rc, out, _ = _run(['diff', _SLUG, '4', '5'])
    assert (rc, out.strip()) == (
        1,
        'hq diff: c04.md not found - that cycle was never finished here')


def test_the_reference_example_file_is_the_scripts_own_output(
        tmp_path, monkeypatch):
    """The fenced example in reference/example-handoff.md equals a real
    three-cycle run.

    Mutation: a renderer change - a column separator, the Log line shape,
    the header form, the standing id scheme - not mirrored in the example.
    Oracle: hq.py run on a synthetic git repo with the example's inputs;
    only the repo path, the git sha, and the block shas are normalized.
    """
    root = tmp_path / 'poller'
    (root / 'scripts').mkdir(parents=True)
    git = ['git', '-c', 'user.email=dev@example.com', '-c', 'user.name=dev']
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    auth_lines = ['def poll():'] + [
        f'    step_{i:03d}()' for i in range(1, 119)] + ['    return 401']
    (root / 'scripts' / 'auth.py').write_text('\n'.join(auth_lines) + '\n')
    # Exclude the handoff dir so git status only shows code changes.
    (root / '.gitignore').write_text('.handoff/\n', encoding='utf-8')
    subprocess.run(
        git + ['-C', str(root), 'add', 'scripts/auth.py', '.gitignore'], check=True)
    subprocess.run(git + ['-C', str(root), 'commit', '-qm', 'poller'], check=True)
    (root / 'scripts' / 'auth.py').write_text('\n'.join(auth_lines[:-1]) + '\n    raise Refresh()\n')
    folder = root / '.handoff' / 'auth-token-refresh'
    (folder / 'notes').mkdir(parents=True)
    (folder / 'specs').mkdir()
    (folder / 'specs' / 'SPEC.md').write_text(
        '# Spec\n\n## 1. Token store\n\nKeep tokens in memory only.\n\n'
        '## 2. Refresh endpoint\n\nPOST /oauth/refresh with the refresh token.\n\n'
        '## 3. Retry\n\nBack off 1s, 2s, 4s; give up after five tries.\n')
    (folder / 'notes' / 'idp-quirks.md').write_text(
        '# Staging IdP quirks\n\n- The 401 body is HTML, not JSON.\n'
        '- Refresh tokens rotate on every call.\n')
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_SESSION', 's1')
    monkeypatch.setenv('HQ_HOST', 'dev')
    monkeypatch.delenv('HQ_GIT', raising=False)
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    monkeypatch.setenv('HQ_NOW', '2026-08-24T10:00:00')
    monkeypatch.chdir(root)
    slug = 'auth-token-refresh'
    assert hq.main(['begin', slug]) == 0
    assert hq.main([
        'stamp', slug, 'specs/SPEC.md', '--where', '3. Retry',
        '--label', 'refresh contract; s3 is the retry schedule']) == 0
    assert hq.main([
        'stamp', slug, 'notes/idp-quirks.md', '--read-before', 'edit',
        '--label', 'staging IdP quirks, found the hard way']) == 0
    assert hq.main([
        'stamp', slug, str(root / 'scripts' / 'auth.py'), '--kind', 'draft',
        '--label', 'poller; the 401 branch is under edit']) == 0
    assert hq.main([
        'note', slug, 'decision', '--headline', 'Refresh in-process, no sidecar',
        'One caller; latency is fine. Settled.']) == 0
    assert hq.main([
        'note', slug, 'constraint', '--headline', 'Never log token values',
        'Not even at debug; the user said so.']) == 0
    assert hq.main([
        'note', slug, 'dead-end', '--headline', 'httpx event hooks for auto-refresh',
        'A hook cannot retry the original request.']) == 0
    example_text = EXAMPLE.read_text()
    fence_start = example_text.index('# Handoff: auth-token-refresh')
    fence_end = example_text.index('```', fence_start)
    example = example_text[fence_start:fence_end].rstrip('\n')
    cursor_start = example.index('## Task')
    cursor_end = example.index('<!-- hq:read')
    cursor = example[cursor_start:cursor_end].rstrip('\n') + (
        '\n\n## Unfiled\n- dead-end: A pid in the lock. Meaningless across two hosts.\n')
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text()
    handoff.write_text(text[:text.index('\n## ') + 1] + cursor)
    assert hq.main(['finish', slug, '--log', 'token store and refresh endpoint written']) == 0
    for day, log in (
            ('25', 'refresh verified against staging; backoff added'),
            ('26', 'poll() wiring started')):
        monkeypatch.setenv('HQ_NOW', f'2026-08-{day}T10:00:00')
        assert hq.main(['begin', slug]) == 0
        assert hq.main(['finish', slug, '--log', log]) == 0

    def _normalize(text):
        text = text.replace(str(root), '~/code/poller')
        text = re.sub(r'@ ?[0-9a-f]{7}\b', '@SHA', text)
        return re.sub(r'<!-- hq:(\w+) [0-9a-f]{12} -->', r'<!-- hq:\1 SHA -->', text)

    assert _normalize(handoff.read_text()).rstrip('\n').splitlines() == \
        _normalize(example).splitlines()


def test_the_artifacts_block_prints_every_full_row_with_no_cap(
        tmp_path, monkeypatch):
    """Finish renders all 41 mention rows in full and prints no cap advisory.

    Mutation: a line cap on the full rows, folding the overflow into the
    kind counts; or a finish advisory counting rows over such a cap.
    Oracle: hand-counted - 41 mention rows render 41 full lines and no
    count line; finish prints no 'advisory: artifacts' line.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    for i in range(41):
        (folder / f'note-{i:02d}.md').write_text('# N\n')
        assert _run(['stamp', _SLUG, f'note-{i:02d}.md', '--read-before', 'mention',
                     '--label', 'a note'])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'forty-one'])
    assert rc == 0
    assert 'advisory: artifacts' not in out
    block = hq.split_handoff((folder / 'HANDOFF.md').read_text())['blocks']['artifacts']
    full = [ln for ln in block.splitlines() if '  other  mention  ' in ln]
    assert len(full) == 41
    assert not any(' - hq artifacts ' in ln for ln in block.splitlines())


def test_finish_names_each_previous_cursor_line_nothing_now_carries(
        tmp_path, monkeypatch):
    """Finish lists the cycle-1 cursor lines that cycle 2 neither kept,
    ticked, drained to standing.md, nor rehomed to a stamped sibling.

    Mutation: the union built from the new cursor alone, so the drained
    Plan item or the rehomed State line is reported; the checkbox kept in
    the comparison, so the ticked item is reported; the omitted State
    heading reported as a line; or the advisory silent.
    Oracle: hand-built cursors - of the six cycle-1 lines exactly two
    leave with no home, the old Now step and one State line; cycle 1
    prints no advisory since no archive precedes it.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    handoff = folder / 'HANDOFF.md'
    header = handoff.read_text()
    header = header[:header.index('\n## ') + 1]
    handoff.write_text(
        header + '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token() into the 401 branch.\n\n'
        '## Plan\n- [ ] Pick the retry ceiling\n- [ ] Name the backoff cap\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n'
        '- Unverified: retry backoff never exercised.\n')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    assert 'not carried' not in out
    _run(['begin', _SLUG])
    (folder / 'notes-state.md').write_text(
        '# State notes\n\n- Unverified: retry backoff never exercised.\n')
    assert _run(['stamp', _SLUG, 'notes-state.md', '--label', 'state moved'])[0] == 0
    handoff.write_text(
        header + '## Task\nRefresh the poller token.\n\n'
        '## Now\nRun the integration test against staging.\n\n'
        '## Plan\n- [x] Pick the retry ceiling\n\n'
        '## Unfiled\n- decision: **Name the backoff cap** 60 s.\n')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    lines = out.splitlines()
    assert (
        'advisory: 2 cursor lines from c01 not carried'
        ' - confirm each was settled or moved, else carry it forward'
        ' or rehome it; hq diff msg-slug 1 2 shows the whole change'
        in lines)
    assert '  not carried: Wire refresh_token() into the 401 branch.' in lines
    assert '  not carried: - Verified: refresh round-trips against staging.' in lines
    assert sum(ln.startswith('  not carried:') for ln in lines) == 2


def test_supersede_prints_the_item_it_drops_from_the_block(tmp_path, monkeypatch):
    """Supersede echoes the superseded item's stored line as it leaves.

    Mutation: the echo dropped, the new id's item echoed instead of the
    old one, or the headline echoed without the body a decision never
    shows in the block.
    Oracle: the standing.md line note wrote for d01, read back from the
    file and compared with the echo minus its prefix.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _run(['note', _SLUG, 'decision', '--headline', 'Ship order fixed',
          'Release the parser first. The crew owns retention.'])
    _run(['note', _SLUG, 'decision', '--headline', 'Ship order reversed',
          'Release the sweeper first.'])
    rc, out, _ = _run(['supersede', _SLUG, 'd01', 'd02'])
    assert rc == 0
    assert out.splitlines() == [
        ('dropped from the block: [d01] (c1) **Ship order fixed**'
         ' Release the parser first. The crew owns retention.')]
    stored = (folder / 'standing.md').read_text().splitlines()[0]
    assert out.split(': ', 1)[1].rstrip('\n') == stored[2:]


def test_supersede_refuses_an_id_that_would_supersede_itself(tmp_path, monkeypatch):
    """An id cannot supersede itself, and nothing is appended when it tries.

    Mutation: the self-edge accepted, so standing.md gains d01 -> d01 and
    hq standing can name no id now current for d01; or the guard widened
    to refuse two distinct ids of one kind.
    Oracle: standing.md read back - one item line and no supersession
    line after the refusal, while d01 -> d02 still appends its line.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _run(['note', _SLUG, 'decision', '--headline', 'Ship order fixed',
          'Release the parser first.'])
    _run(['note', _SLUG, 'decision', '--headline', 'Ship order reversed',
          'Release the sweeper first.'])
    rc, out, _ = _run(['supersede', _SLUG, 'd01', 'd01'])
    assert rc == 1
    assert out.splitlines() == [
        ('hq supersede: d01 cannot supersede itself'
         ' - name the item that replaces it, or hq note one first')]
    lines = (folder / 'standing.md').read_text().splitlines()
    assert [ln for ln in lines if ln.startswith('- (c')] == []
    assert _run(['supersede', _SLUG, 'd01', 'd02'])[0] == 0
    lines = (folder / 'standing.md').read_text().splitlines()
    assert [ln for ln in lines if ln.startswith('- (c')] == ['- (c1) d01 -> d02']


def test_open_names_a_folder_path_under_another_directory(tmp_path, monkeypatch):
    """Open names cursor and standing text placing the folder under an old dir.

    Mutation: HANDOFF.md scanned and not standing.md, the scan widened to
    any directory so a source tree named after the slug reports as a
    moved folder, or one line printed per mention instead of one per
    directory.
    Oracle: hand-seeded text - two 'working/<slug>/' mentions in the
    cursor, one in standing.md, and a '.handoff/<slug>/', a 'src/<slug>/'
    and a 'tests/<slug>/' mention that must not print.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    handoff = folder / 'HANDOFF.md'
    handoff.write_text(handoff.read_text().replace(
        '## Environment',
        '## Environment\n'
        f'- Root: working/{_SLUG}/\n'
        f'- Notes: working/{_SLUG}/notes-crew.md\n'
        f'- Current: .handoff/{_SLUG}/\n', 1))
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the crew notes',
          f'Under working/{_SLUG}/notes-crew.md.'])
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the grammar',
          f'It lives in src/{_SLUG}/ and its tests in tests/{_SLUG}/.'])
    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    _stale_tail = (
        ' - correct it at the next write, inside a cycle, never in'
        ' HANDOFF.md outside one; hq help stale-path has the steps')
    assert [ln for ln in out.splitlines() if 'stale folder path' in ln] == [
        f'stale folder path in HANDOFF.md: working/{_SLUG}/ x2{_stale_tail}',
        f'stale folder path in standing.md: working/{_SLUG}/ x1{_stale_tail}',
        ]


def test_open_stops_counting_a_stale_path_once_its_item_is_superseded(
        tmp_path, monkeypatch):
    """Open counts an old folder path only in standing items still in force.

    Mutation: the superseded set ignored, so the append-only file keeps
    the count at x2 after each re-note and no move ever clears the line;
    or the wrong id skipped, so the live re-note that still names the old
    path as history goes uncounted.
    Oracle: hand-staged supersessions - two items name the old path (x2);
    superseding the first by a re-note that still names it leaves x1;
    superseding that by one naming only the new path prints no line.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the crew notes',
          f'Under working/{_SLUG}/notes-crew.md.'])
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the ledger',
          f'Under working/{_SLUG}/ledger.tsv.'])

    def stale(out):
        return [ln for ln in out.splitlines() if 'stale folder path' in ln]

    _stale_tail = (
        ' - correct it at the next write, inside a cycle, never in'
        ' HANDOFF.md outside one; hq help stale-path has the steps')
    assert stale(_run(['open', _SLUG])[1]) == [
        f'stale folder path in standing.md: working/{_SLUG}/ x2{_stale_tail}']
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the crew notes',
          f'Under .handoff/{_SLUG}/notes-crew.md, moved from working/{_SLUG}/.'])
    assert _run(['supersede', _SLUG, 'c01', 'c03'])[0] == 0
    assert stale(_run(['open', _SLUG])[1]) == [
        f'stale folder path in standing.md: working/{_SLUG}/ x2{_stale_tail}']
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the ledger',
          f'Under .handoff/{_SLUG}/ledger.tsv.'])
    assert _run(['supersede', _SLUG, 'c02', 'c04'])[0] == 0
    assert stale(_run(['open', _SLUG])[1]) == [
        f'stale folder path in standing.md: working/{_SLUG}/ x1{_stale_tail}']
    _run(['note', _SLUG, 'constraint', '--headline', 'Keep the crew notes',
          f'Under .handoff/{_SLUG}/notes-crew.md.'])
    assert _run(['supersede', _SLUG, 'c03', 'c05'])[0] == 0
    assert stale(_run(['open', _SLUG])[1]) == []


def test_shorter_label_advisory_fires_only_in_the_cycle_that_shortened_it(
        tmp_path, monkeypatch):
    """A label shortened in one cycle is reported at that finish alone.

    Mutation: the cycle guard dropped, so the latest row is compared with
    its predecessor at every later finish and a deliberate shortening is
    re-reported for the life of the ledger; or the comparand taken as the
    positional predecessor, so two re-stamps in one cycle hide a
    shortening against the last cycle's label.
    Oracle: the finish that follows the shortening prints the advisory;
    the next cycle's finish, with no new row for the path, does not; a
    cycle that re-stamps twice, 'a' then 'ab', is judged against the
    earlier cycle's 'short' and prints it.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    assert _run(
        ['stamp', _SLUG, 'SPEC.md', '--where', 'Spec', '--label', 'a long spec label here'])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--label', 'short'])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    _lbl_short_tail = (
        ' - check the new label kept every backticked token'
        ' and s<n> reference the old one carried')
    assert (
        f'advisory: label shorter than predecessor: SPEC.md{_lbl_short_tail}'
        in out.splitlines())
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    assert 'label shorter' not in out
    assert 'label dropped' not in out
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--label', 'a'])[0] == 0
    assert _run(['stamp', _SLUG, 'SPEC.md', '--label', 'ab'])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'three'])
    assert rc == 0
    assert (
        f'advisory: label shorter than predecessor: SPEC.md{_lbl_short_tail}'
        in out.splitlines())
