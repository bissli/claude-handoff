"""Every line hq.py prints, driven once and asserted verbatim.

The skill tells the agent what to do about each message the script
prints, so each message is a contract. These tests drive each refusal,
advisory, and status path and pin its exact text and exit code; the
contract tests hold the same text to the skill.
"""

import contextlib
import hashlib
import io
import os
import pathlib
import re
import shutil
import subprocess

from bin import hq

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


def test_begin_names_the_stale_cycle_header_only_when_it_is_stale(
        tmp_path, monkeypatch):
    """Begin names the header's Cycle field beside the cycle it opened,
    and says nothing where the two agree.

    Mutation: the line printed wherever a header exists, so the first
    cycle of a thread is told its own header is wrong; or the header
    never read, so the misattribution the line exists to stop stays
    silent for every later cycle.
    Oracle: cycle 1, whose header begin itself wrote as Cycle: 1, and
    cycle 2, whose finish has not yet moved that field off 1.
    """
    _root(tmp_path, monkeypatch)
    rc, first, _ = _run(['begin', _SLUG])
    assert rc == 0
    assert 'header reads Cycle:' not in first
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0

    rc, second, _ = _run(['begin', _SLUG])

    assert rc == 0
    assert 'cycle 2 begun by session-abc on test-host' in second.splitlines()
    assert (
        'header reads Cycle: 1; this cycle is 2 - finish rewrites that line'
        " last; attribute this session's work to 2") in second.splitlines()


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
    """A successful finish prints the size line and the resume line, the
    unstamped advisory names its subject, and the shorter-label advisory
    lands on the stamp that shortened it, not on finish.

    Mutation: the token split dropped, the resume line pointing at hq.py
    open, an advisory class silent, or the label advisory left in finish,
    where the render is already written.
    Oracle: the documented shapes; one unstamped notes file and one label
    shortened from 21 to 5 characters.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    (folder / 'notes-x.md').write_text('# Notes\n')
    assert _run([
        'stamp', _SLUG, 'SPEC.md', '--where', 'Spec',
        '--label', 'a long spec label here'])[0] == 0
    rc, stamp_out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--label', 'short'])
    assert rc == 0
    assert (
        'advisory: label shorter than predecessor: SPEC.md'
        ' - check the new label still carries what the old one said'
        in stamp_out.splitlines())
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    lines = out.splitlines()
    assert 'advisory: unstamped notes x1: notes-x.md - stamp each' in lines
    assert 'label shorter' not in out
    assert re.fullmatch(
        r'\S+/HANDOFF\.md  \d+ cursor lines  \d+ tokens'
        r' \(cursor \d+, read \d+, artifacts \d+, standing \d+\)', lines[-3])
    assert lines[-2] == 'read first: 1 rows, 6 tok (1 anchored, 0 whole)'
    assert lines[-1] == f'resume: /handoff {_SLUG}'


def test_open_reports_a_label_the_ledger_moved_past_after_finish(
        tmp_path, monkeypatch):
    """A re-stamp after finish leaves the artifacts block behind, and open
    names the path; a block that agrees, and a cycle in flight, stay silent.

    Mutation: the rendered label compared with itself rather than with the
    ledger row, so a re-stamp after finish is silent and the next session
    reads a block that disagrees with the ledger with no signal; or the
    lock guard dropped, so a stamp inside a cycle reports drift the
    pending finish clears on its own.
    Oracle: a block rendered from 'the first label' against a ledger row
    that reads 'the second label', longer, so no shortening fires too.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    assert _run(['stamp', _SLUG, 'SPEC.md', '--where', 'Spec',
                 '--label', 'the first label'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    rc, out, _ = _run(['open', _SLUG])
    assert (rc, 'LEDGER BEHIND' in out) == (0, False)

    assert _run(['stamp', _SLUG, 'SPEC.md', '--label', 'the second label'])[0] == 0

    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    assert (
        'LEDGER BEHIND: block label differs: SPEC.md'
        ' - the block and the ledger disagree; trust the ledger,'
        ' and the next finish re-renders the block'
        in out.splitlines())
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['open', _SLUG])
    assert (rc, 'LEDGER BEHIND' in out) == (0, False)


def test_open_label_check_reads_an_abs_row_through_its_rendered_display(
        tmp_path, monkeypatch):
    """A row stored by absolute path is judged against the relative line
    the block prints for it.

    Mutation: the ledger key matched against the block instead of the
    display shown_paths returns, so every abs row under the root or the
    pin stops being checked and open goes back to the silence the bug
    was filed about.
    Oracle: the row is stored as an absolute path and rendered as
    'a/x.md', so only the mapped display can match the block line.
    """
    folder = _root(tmp_path, monkeypatch)
    root = folder.parent.parent
    (root / 'a').mkdir()
    (root / 'a' / 'x.md').write_text('# X\n')
    _run(['begin', _SLUG])
    assert _run(['stamp', _SLUG, str(root / 'a' / 'x.md'),
                 '--read-before', 'mention', '--label', 'first label'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['stamp', _SLUG, str(root / 'a' / 'x.md'),
                 '--read-before', 'mention', '--label', 'second label'])[0] == 0

    rc, out, _ = _run(['open', _SLUG])

    assert rc == 0
    assert f'LEDGER BEHIND: block label differs: {root / "a" / "x.md"}' in out


def _two_copies(tmp_path, monkeypatch):
    """Pin a work dir and return (folder, root) with a/x.md in both bases."""
    folder = _root(tmp_path, monkeypatch)
    root = folder.parent.parent
    (root / 'a').mkdir()
    (root / 'wt' / 'a').mkdir(parents=True)
    (root / 'a' / 'x.md').write_text('# X\n')
    (root / 'wt' / 'a' / 'x.md').write_text('# X\n')
    _run(['begin', _SLUG])
    assert _run(['work-dir', _SLUG, 'wt'])[0] == 0
    return folder, root


def test_open_label_check_passes_over_a_display_the_block_prints_twice(
        tmp_path, monkeypatch):
    """A display the block carries on two lines is left alone.

    Mutation: the first matching block line taken as the row's own, so a
    live row whose display the block prints twice is judged against the
    other row's label and reports a permanent LEDGER BEHIND that no
    finish clears.
    Oracle: the block holds 'root copy' and 'work dir copy' under one
    relative path, the root row is then archived, and the surviving row
    carries the label on the second of those two lines.
    """
    folder, root = _two_copies(tmp_path, monkeypatch)
    assert _run(['stamp', _SLUG, str(root / 'a' / 'x.md'),
                 '--read-before', 'mention', '--label', 'root copy'])[0] == 0
    assert _run(['stamp', _SLUG, str(root / 'wt' / 'a' / 'x.md'),
                 '--read-before', 'mention', '--label', 'work dir copy'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['stamp', _SLUG, str(root / 'a' / 'x.md'), '--archive',
                 '--reason', 'the work dir copy is the one'])[0] == 0

    rc, out, _ = _run(['open', _SLUG])

    assert (rc, 'LEDGER BEHIND' in out) == (0, False), out


def test_open_label_check_passes_over_a_display_two_live_rows_share(
        tmp_path, monkeypatch):
    """A display two live rows share is left alone.

    Mutation: the ledger-side ambiguity ignored, so a row stamped after
    the render, under a display an older row already owns, is judged
    against that older row's block line and reports a LEDGER BEHIND for
    a label the block never claimed to hold.
    Oracle: one block line reading 'root copy' against a second live row
    added after the finish and labeled 'work dir copy'.
    """
    folder, root = _two_copies(tmp_path, monkeypatch)
    assert _run(['stamp', _SLUG, str(root / 'a' / 'x.md'),
                 '--read-before', 'mention', '--label', 'root copy'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['stamp', _SLUG, str(root / 'wt' / 'a' / 'x.md'),
                 '--read-before', 'mention', '--label', 'work dir copy'])[0] == 0

    rc, out, _ = _run(['open', _SLUG])

    assert (rc, 'LEDGER BEHIND' in out) == (0, False), out


def test_open_label_check_survives_a_label_whose_trailing_space_the_block_drops(
        tmp_path, monkeypatch):
    """A label ending in a space is not reported against its own block line.

    Mutation: the two labels compared unstripped, so a label with
    trailing space on the block's last line reports a permanent
    LEDGER BEHIND that no finish clears.
    Oracle: a clean finish with one full-line row whose label ends in a
    space the parsed block body has dropped.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'doc.md').write_text('# Doc\n')
    _run(['begin', _SLUG])
    assert _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                 '--label', 'fix the parser '])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0

    rc, out, _ = _run(['open', _SLUG])

    assert (rc, 'LEDGER BEHIND' in out) == (0, False), out


def test_open_label_check_speaks_for_a_cycle_another_session_left_unfinished(
        tmp_path, monkeypatch):
    """A lock this session holds silences the label check; one another
    session left does not.

    Mutation: the guard keyed on the lock existing rather than on whose
    it is, so a cycle that began, re-stamped, and died leaves the next
    session with no per-path signal at all - the case the ticket asks
    open to report.
    Oracle: one re-stamp, read twice: once under this session's lock and
    once under a lock naming another session.
    """
    folder = _root(tmp_path, monkeypatch)
    (folder / 'doc.md').write_text('# Doc\n')
    _run(['begin', _SLUG])
    assert _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                 '--label', 'first label'])[0] == 0
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    assert _run(['begin', _SLUG])[0] == 0
    assert _run(['stamp', _SLUG, 'doc.md', '--read-before', 'mention',
                 '--label', 'second label'])[0] == 0
    rc, out, _ = _run(['open', _SLUG])
    assert (rc, 'LEDGER BEHIND' in out) == (0, False), out

    _lock(folder, 'session-other', '2026-09-09T11:00:00', cycle=2)

    rc, out, _ = _run(['open', _SLUG])
    assert rc == 0
    assert (
        'LEDGER BEHIND: block label differs: doc.md'
        ' - the block and the ledger disagree; trust the ledger,'
        ' and the next finish re-renders the block'
        in out.splitlines()), out


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


def test_read_sends_a_store_to_its_verb_not_to_the_file(tmp_path, monkeypatch):
    """Read refuses the three dictated stores by naming the verb instead.

    Mutation: falling through to the not-in-ledger refusal, which reads
    'read it whole by hand' and so tells the agent to do the one thing
    the PreToolUse gate reports it for; or matching the basename only,
    which misses cycles/<file>.
    Oracle: the hand-written verb per store from hq.STORE_VERBS, and
    exit 1 on each.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    for path, verb in [
            ('ledger.tsv', f'hq artifacts {_SLUG}, or hq when {_SLUG} <path>'),
            ('standing.md', f'hq standing {_SLUG}'),
            ('cycles', f'hq diff {_SLUG} <c1> <c2>'),
            ('cycles/c01.md', f'hq diff {_SLUG} <c1> <c2>'),
            ]:
        store = path.split('/')[0]
        rc, out, _ = _run(['read', _SLUG, path])
        assert (rc, out.strip()) == (
            1, (f'hq read: {store} is dictated, not opened'
                f' - run this instead: {verb}'))


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
         ' - use --whole to read the whole file when no span printed above'))
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


_REFUSAL = (
    'hq finish: {n} cursor lines from c{c:02d} not carried'
    ' - carry each forward, rehome it to a sibling and stamp'
    ' that sibling this cycle, or re-run with'
    ' --accept-not-carried "<reason>"')


def _digests(folder):
    """Return a sha256 per bookkeeping file, None where it is absent.
    """
    names = ('HANDOFF.md', 'standing.md', 'ledger.tsv', 'cycles/manifest.tsv')
    out = {}
    for name in names:
        path = folder / name
        out[name] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() else None)
    return out


def _cursor(folder, body):
    """Overwrite the cursor, keeping the header line the script owns.
    """
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text()
    handoff.write_text(text[:text.index('\n## ') + 1] + body)


def test_finish_reports_no_dropped_line_before_any_archive_exists(
        tmp_path, monkeypatch):
    """Cycle 1 finishes silently: no archive precedes it to compare against.

    Mutation: the empty-manifest guard dropped, so not_carried reads a
    c00 archive and the first cycle of every thread refuses; or the
    comparand defaulted to the live cursor, which matches itself.
    Oracle: a cycle-1 cursor with six content lines and no cycles/
    directory, against an exit code of 0 and stdout carrying no
    'not carried'.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token() into the 401 branch.\n\n'
        '## Plan\n- [ ] Pick the retry ceiling\n- [ ] Name the backoff cap\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n'
        '- Unverified: retry backoff never exercised.\n')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    assert 'not carried' not in out


def test_finish_refuses_before_the_archive_when_a_cursor_line_is_dropped(
        tmp_path, monkeypatch):
    """The refusal precedes every disk write, so the folder is untouched.

    Mutation: the refusal left as an advisory that prints and falls
    through to the write; or the return 1 placed after the cycles/
    mkdir or the archive write, so the message changes but the order
    does not.
    Oracle: the absence of cycles/c02.md and four unchanged file
    digests, against a hand-built pair of cursors whose one State line
    has no home in the second.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token() into the 401 branch.\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nRun the integration test against staging.\n')
    before = _digests(folder)
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 1
    lines = out.splitlines()
    assert _REFUSAL.format(n=1, c=1) in lines
    assert (
        '  not carried: - Verified: refresh round-trips against staging.'
        in lines)
    assert not (folder / 'cycles' / 'c02.md').exists()
    assert _digests(folder) == before


def test_a_spent_now_step_is_not_a_dropped_line(tmp_path, monkeypatch):
    """Replacing the Now step alone finishes; cutting a State line refuses.

    Mutation: the Now exemption dropped, so every healthy cycle
    refuses; or the exemption widened past the Now section, so a
    dropped State line passes.
    Oracle: the pair of cursors straddling the real boundary - Now
    alone replaced, versus Now replaced and a State line cut.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    body = (
        '## Task\nRefresh the poller token.\n\n'
        '## Now\n{now}\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n'
        '{tail}')
    _cursor(folder, body.format(
        now='Wire refresh_token() into the 401 branch.',
        tail='- Unverified: retry backoff never exercised.\n'))
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(folder, body.format(
        now='Run the integration test against staging.',
        tail='- Unverified: retry backoff never exercised.\n'))
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    assert 'not carried' not in out
    _run(['begin', _SLUG])
    _cursor(folder, body.format(now='Cap the backoff.', tail=''))
    rc, out, _ = _run(['finish', _SLUG, '--log', 'three'])
    assert rc == 1
    assert (
        '  not carried: - Unverified: retry backoff never exercised.'
        in out.splitlines())


def test_an_item_annotated_after_its_final_mark_is_carried(
        tmp_path, monkeypatch):
    """Ticking and annotating a Plan item carries it; deleting it refuses.

    Mutation: the trailing-mark trim dropped, so the annotation moves
    the period and a carried item reports as lost - the firing observed
    in 17 of 22 retained c11 eval runs; or the trim applied to the
    union side as well, so an item genuinely cut down to its opening
    words passes.
    Oracle: the same item in three states - ticked and annotated after
    its period, ticked alone, and absent - against one exit code each.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    body = '## Task\nSkip a rendered page.\n\n## Now\n{now}\n\n## Plan\n{plan}'
    _cursor(folder, body.format(
        now='Wire page_key() into render_page().',
        plan='- [ ] Wire the key into render_page().\n'))
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(folder, body.format(
        now='Benchmark a thousand-page site.',
        plan='- [x] Wire the key into render_page()'
             ' (cycle 2; written, never run).\n'))
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    assert 'not carried' not in out
    _run(['begin', _SLUG])
    _cursor(folder, body.format(
        now='Cap the cache.', plan='- [ ] Size the cache from the page count.\n'))
    rc, out, _ = _run(['finish', _SLUG, '--log', 'three'])
    assert rc == 1
    assert any(
        'Wire the key into render_page()' in ln
        for ln in out.splitlines() if ln.startswith('  not carried:'))


def test_stamping_the_previous_archive_does_not_clear_the_refusal(
        tmp_path, monkeypatch):
    """The cycles/ archive is no witness, so stamping it suppresses nothing.

    Mutation: the cycles/ exclusion dropped from the stamped-this-cycle
    witness set, which turns one hq stamp of the archive into a silent,
    unrecorded bypass of --accept-not-carried; or the exclusion written
    as a prefix test that an absolute ledger path slips past.
    Oracle: the identical refused cycle run twice, once with the
    previous archive stamped live this cycle.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token().\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(folder, '## Task\nRefresh the poller token.\n\n## Now\nCap it.\n')
    assert _run(['finish', _SLUG, '--log', 'two'])[0] == 1
    assert _run(['stamp', _SLUG, 'cycles/c01.md', '--label', 'the archive'])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 1
    assert _REFUSAL.format(n=1, c=1) in out.splitlines()
    assert not (folder / 'cycles' / 'c02.md').exists()


def test_a_now_step_quoting_a_plan_item_does_not_exempt_that_item(
        tmp_path, monkeypatch):
    """The spent exemption reaches the Now section alone, never a twin line.

    Mutation: the spent set filtered against every reported line rather
    than against lines the Now section alone holds, so a Now step
    written as the Plan bullet it names silently exempts that bullet's
    own drop.
    Oracle: a cycle-1 cursor whose Now step and Plan bullet are the
    same text, against the dropped bullet being named.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nSkip a rendered page.\n\n'
        '## Now\n- [ ] Wire the key into render_page().\n\n'
        '## Plan\n- [ ] Wire the key into render_page().\n'
        '- [ ] Benchmark a thousand-page site.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nSkip a rendered page.\n\n'
        '## Now\nBenchmark next.\n\n'
        '## Plan\n- [ ] Benchmark a thousand-page site.\n')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 1
    assert any(
        'Wire the key into render_page().' in ln
        for ln in out.splitlines() if ln.startswith('  not carried:'))


def test_accept_not_carried_with_no_reason_refuses_on_its_own_line(
        tmp_path, monkeypatch):
    """An empty reason refuses and says so, rather than reprinting the list.

    Mutation: the empty reason normalized to a falsy string and left to
    fall into the not-carried branch, so an agent passing '' loops on an
    unchanged message with no word that the flag was seen.
    Oracle: the flag given as '' and as three spaces, against one
    distinct line and exit 1 each.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token().\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(folder, '## Task\nRefresh the poller token.\n\n## Now\nCap it.\n')
    empty_line = (
        'hq finish: --accept-not-carried was given with no reason'
        ' - re-run naming what settles the drop')
    for reason in ('', '   '):
        rc, out, _ = _run([
            'finish', _SLUG, '--log', 'two', '--accept-not-carried', reason])
        assert rc == 1
        assert empty_line in out.splitlines()
        assert 'not carried - carry each forward' not in out


def test_the_capped_refusal_names_the_command_that_prints_the_rest(
        tmp_path, monkeypatch):
    """Five lines print, the count names the rest, and the lister has them all.

    Mutation: the cap removed, so a long list floods the refusal; the
    cap applied with no count line, so the agent cannot tell how many
    remain; or the listing flag made to block or to write.
    Oracle: nine hand-built State lines against the printed five plus
    the named four, and the lister's own nine.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    state = ''.join(f'- Verified: probe {i} holds.\n' for i in range(9))
    _cursor(
        folder,
        '## Task\nProbe the cache.\n\n## Now\nRun probe 0.\n\n'
        f'## State\n{state}')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(folder, '## Task\nProbe the cache.\n\n## Now\nRun probe 9.\n')
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 1
    lines = out.splitlines()
    assert _REFUSAL.format(n=9, c=1) in lines
    assert sum(ln.startswith('  not carried:') for ln in lines) == 5
    assert (
        f'  ... and 4 more; hq open {_SLUG} --not-carried prints every one'
        in lines)
    before = _digests(folder)
    rc, out, _ = _run(['open', _SLUG, '--not-carried'])
    assert rc == 0
    lines = out.splitlines()
    assert sum(ln.startswith('  not carried:') for ln in lines) == 9
    assert lines[0].startswith('not carried from c01: 9')
    assert _digests(folder) == before


def test_the_lister_and_the_refusal_agree_on_a_drained_unfiled_item(
        tmp_path, monkeypatch):
    """The lister files this cycle's Unfiled items, as the refusal does.

    Mutation: the open branch passing standing.md without the drained
    items, so the command the refusal names as authoritative reports a
    line the refusal itself passes.
    Oracle: one State line rehomed as a typed Unfiled decision, against
    a zero from the lister and an exit 0 from finish on the same tree.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token().\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n## Now\nCap it.\n\n'
        '## Unfiled\n- decision: **Staging round-trip settled**'
        ' - Verified: refresh round-trips against staging.\n')
    rc, out, _ = _run(['open', _SLUG, '--not-carried'])
    assert rc == 0
    assert out.splitlines()[0].startswith('not carried from c01: 0')
    assert _run(['finish', _SLUG, '--log', 'two'])[0] == 0


def test_the_lister_says_when_the_comparand_is_gone(tmp_path, monkeypatch):
    """A missing archive is named, never reported as nothing dropped.

    Mutation: the archive guard left to not_carried, which returns an
    empty list for a missing file, so the lister prints a count of zero
    and a reader cannot tell it from a clean cycle.
    Oracle: the same tree with cycles/c01.md moved aside, against the
    two different first lines.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _cursor(
        folder,
        '## Task\nRefresh the poller token.\n\n'
        '## Now\nWire refresh_token().\n\n'
        '## State\n- Verified: refresh round-trips against staging.\n')
    assert _run(['finish', _SLUG, '--log', 'one'])[0] == 0
    _run(['begin', _SLUG])
    _cursor(folder, '## Task\nRefresh the poller token.\n\n## Now\nCap it.\n')
    assert _run(['open', _SLUG, '--not-carried'])[1].splitlines()[0].startswith(
        'not carried from c01: 1')
    archive = folder / 'cycles' / 'c01.md'
    archive.rename(archive.with_suffix('.md.aside'))
    rc, out, _ = _run(['open', _SLUG, '--not-carried'])
    assert rc == 0
    assert out.splitlines()[0] == (
        'not carried: cycles/c01.md is absent, so the comparand is gone'
        ' - restore it before trusting a count of zero')


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


def test_shorter_label_advisory_fires_at_the_stamp_against_an_earlier_cycle(
        tmp_path, monkeypatch):
    """Each stamp that shortens a label reports it, judged against the
    last label an earlier cycle left, and no finish repeats it.

    Mutation: the comparand taken as the row immediately before, so the
    second of two re-stamps in one cycle is judged against the first and
    a shortening against the last cycle's label goes unreported; or the
    advisory left in finish, where it is re-reported for every later
    cycle and costs a whole cycle to act on.
    Oracle: 'short' after 'a long spec label here' prints it; the finish
    and the next cycle's finish are silent; a cycle that re-stamps 'a'
    then 'ab' is judged against the earlier cycle's 'short' and prints
    it twice, which comparing 'ab' against 'a' would not.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    _spec(folder)
    assert _run([
        'stamp', _SLUG, 'SPEC.md', '--where', 'Spec',
        '--label', 'a long spec label here'])[0] == 0
    _lbl_short = (
        'advisory: label shorter than predecessor: SPEC.md'
        ' - check the new label still carries what the old one said')
    rc, out, _ = _run(['stamp', _SLUG, 'SPEC.md', '--label', 'short'])
    assert rc == 0
    assert _lbl_short in out.splitlines()
    rc, out, _ = _run(['finish', _SLUG, '--log', 'one'])
    assert rc == 0
    assert 'label shorter' not in out
    assert _run(['begin', _SLUG])[0] == 0
    rc, out, _ = _run(['finish', _SLUG, '--log', 'two'])
    assert rc == 0
    assert 'label shorter' not in out
    assert 'label dropped' not in out
    assert _run(['begin', _SLUG])[0] == 0
    rc, out_a, _ = _run(['stamp', _SLUG, 'SPEC.md', '--label', 'a'])
    assert rc == 0
    assert _lbl_short in out_a.splitlines()
    rc, out_ab, _ = _run(['stamp', _SLUG, 'SPEC.md', '--label', 'ab'])
    assert rc == 0
    assert _lbl_short in out_ab.splitlines()


def test_an_unparsed_batch_line_names_which_fault_stopped_it(
        tmp_path, monkeypatch):
    """Each unparsed stamp batch line names its own cause, not just itself.

    Mutation: the causes collapsed to one generic clause, or the quote
    fault and the flag fault swapped, so a reader retypes quotes at a
    mistyped flag and retypes flags at an apostrophe.
    Oracle: three hand-written lines, one per fault - an apostrophe, a
    bad flag, a missing path - each matched against the clause that
    fault and no other produces, plus the good fourth line's row.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    (folder / 'notes-a.md').write_text('# A\n')
    monkeypatch.setattr('sys.stdin', io.StringIO(
        "notes-a.md --label the reader's own span\n"
        'notes-a.md --successr NEXT.md\n'
        '--label "orphan"\n'
        'notes-a.md --label "good"\n'))
    rc, out, err = _run(['stamp', _SLUG, '--batch'])
    assert rc == 2
    assert err == ''
    assert 'batch line 1 not parsed' in out
    assert 'no closing quotation: put the whole label in double quotes' in out
    assert 'batch line 2 not parsed' in out
    assert 'unrecognized arguments: --successr NEXT.md' in out
    assert 'batch line 3 not parsed' in out
    assert 'path is required: it is the first word of the line' in out
    assert out.count('no closing quotation') == 1
    assert out.count('unrecognized arguments') == 1
    rows = (folder / 'ledger.tsv').read_text().splitlines()
    assert len(rows) == 2
    assert rows[-1].endswith('\tgood')



def test_note_prints_the_id_it_assigned(tmp_path, monkeypatch):
    """Verify note echoes every id it allocates, single and batch.

    Mutation: note appending to standing.md and returning 0 in silence,
    echoing a constant, reusing the previous id, restarting a prefix at
    01, or leaving the --batch path mute while the single path speaks.
    Oracle: the ids standing.md itself holds after each call, parsed from
    the file rather than read back from the printed line.
    """
    folder = _root(tmp_path, monkeypatch)
    _run(['begin', _SLUG])
    standing_path = folder / 'standing.md'
    ids_seen = []
    for kind, headline in (
            ('decision', 'Refresh in process'),
            ('constraint', 'Never log a token value'),
            ('dead-end', 'Event hooks for auto-refresh'),
            ('decision', 'Ship order fixed'),
            ):
        rc, out, _ = _run(
            ['note', _SLUG, kind, '--headline', headline, 'a body'])
        assigned = re.findall(r'\[([cdx]\d\d)\]', standing_path.read_text())
        fresh = [item for item in assigned if item not in ids_seen]
        assert (rc, fresh) != (0, []), 'note allocated no id'
        assert fresh[0] in out, (fresh[0], out)
        ids_seen.extend(fresh)
    assert ids_seen == ['d01', 'c01', 'x01', 'd02']
    monkeypatch.setattr('sys.stdin', io.StringIO(
        'constraint --headline "Keep the floor" the floor holds\n'
        'dead-end --headline "Sidecar" a sidecar was rejected\n'))
    rc, out, _ = _run(['note', _SLUG, '--batch'])
    assigned = re.findall(r'\[([cdx]\d\d)\]', standing_path.read_text())
    fresh = [item for item in assigned if item not in ids_seen]
    assert (rc, fresh) == (0, ['c02', 'x02'])
    for batch_id in fresh:
        assert batch_id in out, (batch_id, out)
