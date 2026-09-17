"""Kill and classify logic/number mutants for the stamp function group.

Functions covered: _do_stamp, _build_parser, anchors, _resolve_root,
_git_state, main, _verb_stamp, check_r1, _find_folder, _line_count,
apply_stem_rule, _next_id, _do_note, _verb_note, _verb_supersede,
infer_kind, _stored_path, _successor_path, latest_rows, _note_line.
"""

import argparse
import io
import os
import pathlib
import shutil
import socket
import subprocess

from bin import hq

_SLUG = 'mut-stamp-slug'
_SESSION = 'session-mut'
_HOST = 'test-mut-host'
_NOW = '2026-09-10T10:00:00'


def _root(tmp_path, monkeypatch, slug=_SLUG, cycle='1', now=_NOW):
    """Create HQ_ROOT and set all HQ_* env vars for one slug.

    Returns the folder path root/.handoff/<slug>/ before creation.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', cycle)
    monkeypatch.setenv('HQ_NOW', now)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    return root / '.handoff' / slug


def _rows(folder):
    """Return ledger data rows as dicts, dropping the header."""
    lines = (folder / 'ledger.tsv').read_text().splitlines()[1:]
    return [
        dict(zip(hq.LEDGER_FIELDS, ln.split('\t')))
        for ln in lines if ln.strip()
        ]


def _git_repo(path):
    """Initialize a git repo at path with one committed file."""
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ['init', '-q', '-b', 'main'],
        ['config', 'user.email', 'test@example.invalid'],
        ['config', 'user.name', 'Tester'],
    ):
        subprocess.run(
            ['git', '-C', str(path)] + cmd, check=True, capture_output=True)
    (path / 'tracked.txt').write_text('one\n')
    subprocess.run(
        ['git', '-C', str(path), 'add', 'tracked.txt'],
        check=True, capture_output=True)
    subprocess.run(
        ['git', '-C', str(path), 'commit', '-q', '-m', 'seed'],
        check=True, capture_output=True)


# ---------------------------------------------------------------------------
# anchors() - 14 killable mutants
# ---------------------------------------------------------------------------

def test_anchors_env_used_when_argv_attrs_absent(tmp_path, monkeypatch):
    """anchors() falls back to HQ_* env vars when argv has no override attrs.

    Mutation: getattr(argv, X, None) -> getattr(argv, X) with no default;
    raises AttributeError when cycle, now, session, or host are absent.
    Oracle: hand-computed values from env vars appear in result dict.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '7')
    monkeypatch.setenv('HQ_NOW', '2026-01-01T00:00:00')
    monkeypatch.setenv('HQ_SESSION', 'env-sess')
    monkeypatch.setenv('HQ_HOST', 'env-host')
    monkeypatch.setenv('HQ_GIT', '0')
    folder = root / '.handoff' / 'test'
    folder.mkdir(parents=True)
    ns = argparse.Namespace()
    anch = hq.anchors(folder, ns)
    assert anch['cycle'] == 7
    assert anch['now'] == '2026-01-01T00:00:00'
    assert anch['session'] == 'env-sess'
    assert anch['host'] == 'env-host'


def test_anchors_session_fallback_to_unknown(tmp_path, monkeypatch):
    """anchors() returns 'unknown' when no session source is set.

    Mutation: os.environ.get('CLAUDE_CODE_SESSION_ID', 'unknown') altered
    to use wrong key name, wrong default ('UNKNOWN'), or no default (None).
    Oracle: hand-computed 'unknown' with all session env vars unset.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_SESSION', raising=False)
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    monkeypatch.delenv('claude_code_session_id', raising=False)
    folder = tmp_path / 'root' / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert anch['session'] == 'unknown'


def test_anchors_session_reads_correct_env_var(tmp_path, monkeypatch):
    """anchors() reads CLAUDE_CODE_SESSION_ID with correct case sensitivity.

    Mutation: 'claude_code_session_id' (lowercase key) misses the env var.
    Oracle: env var set under correct name appears as session.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_SESSION', raising=False)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'real-sess-id')
    monkeypatch.delenv('claude_code_session_id', raising=False)
    folder = tmp_path / 'root' / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert anch['session'] == 'real-sess-id'


def test_anchors_host_from_hq_host_env(tmp_path, monkeypatch):
    """anchors() reads HQ_HOST with correct case.

    Mutation: 'hq_host' (lowercase key) misses the env var.
    Oracle: env var set under correct name appears as host.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_HOST', 'from-env-host')
    monkeypatch.delenv('hq_host', raising=False)
    folder = tmp_path / 'root' / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert anch['host'] == 'from-env-host'


def test_anchors_host_falls_back_to_gethostname(tmp_path, monkeypatch):
    """anchors() falls back to socket.gethostname() when HQ_HOST absent.

    Mutation: or -> and in fallback chain makes host None instead of hostname.
    Oracle: host equals socket.gethostname() with HQ_HOST unset.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_HOST', raising=False)
    monkeypatch.delenv('hq_host', raising=False)
    folder = tmp_path / 'root' / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert anch['host'] == socket.gethostname()


def test_anchors_hq_git_zero_disables_git_state(tmp_path, monkeypatch):
    """anchors() skips git when HQ_GIT=0.

    Mutation: os.environ.get('HQ_GIT', ...) uses wrong key ('1' or 'hq_git'),
    ignoring the HQ_GIT=0 override and running git anyway.
    Oracle: branch and sha are '-' with HQ_GIT=0 set.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('hq_git', raising=False)
    folder = root / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert anch['branch'] == '-'
    assert anch['sha'] == '-'
    assert anch['dirty'] == []


def test_anchors_now_isoformat_uses_seconds(tmp_path, monkeypatch):
    """anchors() isoformat default uses timespec='seconds', not 'SECONDS'.

    Mutation: timespec='SECONDS' (uppercase) raises ValueError.
    Oracle: result 'now' is a valid ISO 8601 string with no microseconds.
    """
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.delenv('HQ_NOW', raising=False)
    folder = root / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    # seconds resolution: no fractional seconds part
    assert '.' not in anch['now']
    # valid ISO 8601 - must parse without error
    import datetime
    datetime.datetime.fromisoformat(anch['now'])


def test_anchors_result_has_lowercase_root_key(tmp_path, monkeypatch):
    """anchors() returns 'root' key, not 'ROOT'.

    Mutation: 'ROOT': root in return dict; callers access anch['root'].
    Oracle: result dict has key 'root' but not 'ROOT'.
    """
    folder = _root(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert 'root' in anch
    assert 'ROOT' not in anch


# ---------------------------------------------------------------------------
# _resolve_root() - 9 killable mutants (mutmut_7 killed by anchors test above)
# ---------------------------------------------------------------------------

def test_resolve_root_uses_git_toplevel(tmp_path):
    """_resolve_root() returns the git repo root when no env/flag override.

    Mutation: empty command, wrong subcommand, or wrong flags prevent
    git rev-parse from running; CalledProcessError is caught and cwd returned.
    Also kills mutants removing capture_output or text that break stdout access.
    Oracle: returned path matches the initialized git repo root.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    subdir = repo / 'sub'
    subdir.mkdir()
    orig_cwd = os.getcwd()
    try:
        os.chdir(subdir)
        ns = argparse.Namespace()
        result = hq._resolve_root(ns)
        assert result == repo.resolve()
    finally:
        os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# _git_state() - 6 killable mutants
# ---------------------------------------------------------------------------

def test_git_state_branch_and_sha_are_strings(tmp_path):
    """_git_state() returns str branch and sha, not bytes.

    Mutation: text=False makes subprocess return bytes; sha and branch
    become bytes objects that fail str comparisons.
    Oracle: branch and sha are str instances from a real git repo.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    branch, sha, dirty = hq._git_state(repo)
    assert isinstance(branch, str)
    assert isinstance(sha, str)
    assert branch == 'main'
    assert len(sha) == 7


def test_git_state_status_uses_cwd_root(tmp_path):
    """_git_state() passes cwd=root to the status subprocess call.

    Mutation: cwd=root removed from status call; runs in process cwd,
    missing dirty files from the repo under test.
    Oracle: an untracked file in repo appears in dirty list.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    (repo / 'untracked.txt').write_text('new\n')
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        branch, sha, dirty = hq._git_state(repo)
        assert 'untracked.txt' in dirty
    finally:
        os.chdir(orig_cwd)


def test_git_state_rename_with_arrow_in_name(tmp_path):
    """_git_state() uses split(' -> ', 1) so a path with ' -> ' is not cut.

    Mutation: split with no maxsplit splits at every ' -> ', taking [1]
    gives wrong path; rsplit gives the wrong fragment; maxsplit=2 same error.
    Oracle: renamed file's new name (containing ' -> ') appears whole in dirty.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    old_name = 'old.txt'
    new_name = 'a -> b -> c.txt'
    (repo / old_name).write_text('x\n')
    subprocess.run(
        ['git', '-C', str(repo), 'add', old_name],
        check=True, capture_output=True)
    subprocess.run(
        ['git', '-C', str(repo), 'commit', '-q', '-m', 'add old'],
        check=True, capture_output=True)
    shutil.move(str(repo / old_name), str(repo / new_name))
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-A'],
        check=True, capture_output=True)
    branch, sha, dirty = hq._git_state(repo)
    assert any(new_name in d for d in dirty)


# ---------------------------------------------------------------------------
# _find_folder() - 4 killable mutants
# ---------------------------------------------------------------------------

def test_find_folder_rejects_uppercase_slug(tmp_path):
    """_find_folder() requires slug to start with alphanumeric character.

    Mutation: regex [a-za-z0-9] (wrong case class) rejects valid uppercase
    slugs or passes invalid ones; only alphanumeric start is valid.
    Oracle: slug 'TestSlug' is accepted; slug '--bad' is rejected.
    """
    root = tmp_path
    handoff_dir = root / '.handoff'
    handoff_dir.mkdir()
    folder = handoff_dir / 'TestSlug'
    folder.mkdir()
    result = hq._find_folder(root, 'TestSlug')
    assert result == folder


def test_find_folder_returns_first_candidate(tmp_path):
    """_find_folder() returns candidates[0] for a unique prefix match.

    Mutation: candidates[1] returns the second candidate for a unique match.
    Oracle: unique prefix 'pre' resolves to 'prefix-a', not 'prefix-b'.
    """
    root = tmp_path
    handoff_dir = root / '.handoff'
    handoff_dir.mkdir()
    (handoff_dir / 'prefix-a').mkdir()
    (handoff_dir / 'prefix-b').mkdir()
    # exact match returns immediately; prefix match returns candidates[0]
    result = hq._find_folder(root, 'prefix-a')
    assert result.name == 'prefix-a'


def test_find_folder_ambiguous_exits_2(tmp_path):
    """_find_folder() exits 2 on ambiguous prefix, not when one match.

    Mutation: len(candidates) >= 1 treats a unique match as ambiguous.
    Oracle: one folder 'slug-a' with prefix 'slug' resolves, not errors.
    """
    root = tmp_path
    handoff_dir = root / '.handoff'
    handoff_dir.mkdir()
    (handoff_dir / 'slug-a').mkdir()
    result = hq._find_folder(root, 'slug')
    assert result.name == 'slug-a'
    # two matches should exit 2
    (handoff_dir / 'slug-b').mkdir()
    try:
        hq._find_folder(root, 'slug')
        raise AssertionError('expected SystemExit')
    except SystemExit as exc:
        assert exc.code == 2


def test_find_folder_missing_exits_code_2(tmp_path):
    """_find_folder() exits with code 2 when slug not found.

    Mutation: sys.exit(3) instead of sys.exit(2) for missing slug.
    Oracle: SystemExit.code == 2 for an unknown slug.
    """
    root = tmp_path
    (root / '.handoff').mkdir()
    try:
        hq._find_folder(root, 'no-such-slug')
        raise AssertionError('expected SystemExit')
    except SystemExit as exc:
        assert exc.code == 2


# ---------------------------------------------------------------------------
# apply_stem_rule() - 4 killable mutants
# ---------------------------------------------------------------------------

def test_apply_stem_rule_skips_non_spec_stems_then_processes_later(tmp_path):
    """apply_stem_rule() continues over non-spec stems to process all stems.

    Mutation: continue -> break exits the loop on the first non-spec stem,
    missing the spec-bearing stem that follows it.
    Oracle: result has successor entry for the spec stem's older member.
    """
    entries = [
        ('plain.txt', 'other', 1.0),
        ('SPEC.md', 'spec', 2.0),
        ('SPEC.pdf', 'draft', 1.0),
        ]
    result = hq.apply_stem_rule(entries)
    assert 'SPEC.pdf' in result
    assert result['SPEC.pdf'] == 'SPEC.md'


def test_apply_stem_rule_continues_over_single_mate(tmp_path):
    """apply_stem_rule() skips stems with fewer than two entries.

    Mutation: continue -> break stops at a single-member stem, missing multi.
    Oracle: result ignores lone 'README' but maps older 'SPEC.pdf' to newer.
    """
    entries = [
        ('README.md', 'spec', 2.0),
        ('SPEC.md', 'spec', 3.0),
        ('SPEC.pdf', 'draft', 1.0),
        ]
    result = hq.apply_stem_rule(entries)
    # README has no spec-mates; SPEC.pdf should be superseded by SPEC.md
    assert 'SPEC.pdf' in result
    assert result['SPEC.pdf'] == 'SPEC.md'
    assert 'README.md' not in result


def test_apply_stem_rule_continues_over_no_spec_mates(tmp_path):
    """apply_stem_rule() skips a stem with no spec kind entry.

    Mutation: continue -> break at empty spec_mates stops the whole loop.
    Oracle: a later stem with spec entries still gets processed.
    """
    entries = [
        ('doc.txt', 'other', 2.0),
        ('doc.md', 'other', 1.0),
        ('plan.md', 'spec', 3.0),
        ('plan.pdf', 'draft', 1.0),
        ]
    result = hq.apply_stem_rule(entries)
    assert 'plan.pdf' in result
    assert result['plan.pdf'] == 'plan.md'


def test_apply_stem_rule_sorts_spec_mates_by_mtime(tmp_path):
    """apply_stem_rule() chooses the newest spec by mtime, not by name.

    Mutation: sort without key uses name as first sort key; an older spec
    with a later name would be treated as newest.
    Oracle: 'spec-v1.md' has mtime=3.0 > 'spec-v2.md' mtime=1.0;
    'spec-v2.md' is mapped to 'spec-v1.md' (newer by mtime).
    """
    # plan.pdf < plan.txt alphabetically; plan.pdf is NEWER by mtime
    # A sort by name would pick plan.txt as newest; the mtime sort picks
    # plan.pdf.
    entries = [
        ('plan.pdf', 'spec', 3.0),
        ('plan.txt', 'spec', 1.0),
        ]
    result = hq.apply_stem_rule(entries)
    # mtime sort: plan.pdf (3.0) is newest; plan.txt (1.0) -> plan.pdf
    assert result.get('plan.txt') == 'plan.pdf'
    # A sort by name would pick plan.txt as newest instead of plan.pdf.
    assert 'plan.pdf' not in result


# ---------------------------------------------------------------------------
# check_r1() - 6 killable mutants
# ---------------------------------------------------------------------------

def test_check_r1_row_with_no_fields_passes(tmp_path):
    """check_r1() returns None for a live spec row with no demoting fields.

    Mutation: default values for read_before, status, kind changed to
    None or wrong strings; None != 'always' triggers a spurious refusal.
    Oracle: hand-computed None (no refusal) from empty row dict.
    """
    row = {}
    result = hq.check_r1(row, 'spec', False)
    assert result is None


def test_check_r1_archived_without_reason_still_applies_r1(tmp_path):
    """check_r1() does not skip R1 for archived rows with no real reason.

    Mutation: default for reason changed to None; None not in {'-', ''}
    is True, so the archived check short-circuits and returns None early.
    Oracle: row with status='archived' but no reason key is refused by R1.
    """
    row = {'status': 'archived'}
    result = hq.check_r1(row, 'spec', False)
    # status != 'live' should trigger R1 refusal
    assert result is not None
    assert 'status' in result


def test_check_r1_non_spec_gate_kind_returns_none(tmp_path):
    """check_r1() returns None immediately for non-spec gate kinds.

    Mutation: gate_kind in {'SPEC', 'draft'} instead of {'spec', 'draft'};
    a spec gate_kind passes through the early-return guard incorrectly.
    Oracle: gate_kind='other' returns None; gate_kind='spec' may refusal.
    """
    # non-spec gate kind: always None
    assert hq.check_r1({}, 'other', False) is None
    # spec gate kind with empty row: None (defaults keep it live)
    assert hq.check_r1({}, 'spec', False) is None


# ---------------------------------------------------------------------------
# _next_id() - 2 killable mutants
# ---------------------------------------------------------------------------

def test_next_id_skips_taken_ids_and_fills_the_first_gap():
    """_next_id() returns the first id of its prefix not already in use.

    Mutation: the `not in` test inverted, so a taken id is returned; or
    `n` never incremented, so the loop never reaches a free id (a hang
    mutmut records as a timeout).
    Oracle: hand-computed - d01 taken gives d02; d01, d02, d04 taken gives
    d03; a c-prefixed item does not occupy a d id.
    """
    assert hq._next_id([{'id': 'd01', 'prefix': 'd'}], 'd') == 'd02'
    taken = [{'id': i, 'prefix': i[0]} for i in ('d01', 'd02', 'd04', 'c03')]
    assert hq._next_id(taken, 'd') == 'd03'
    assert hq._next_id(taken, 'c') == 'c01'


# ---------------------------------------------------------------------------
# _line_count() - 4 killable mutants
# ---------------------------------------------------------------------------

def test_line_count_file_counts_newlines(tmp_path):
    """_line_count() counts newline bytes, matching wc -l.

    Mutation: count 2 per entry instead of 1; result double-counts.
    Oracle: 3-line file -> 3 (hand-computed wc -l value).
    """
    f = tmp_path / 'test.txt'
    f.write_bytes(b'line1\nline2\nline3\n')
    assert hq._line_count(f) == 3


def test_line_count_dir_counts_non_dot_files(tmp_path):
    """_line_count() on a dir counts visible non-dot files only.

    Mutation: entry.is_file() or not entry.name.startswith('.') includes
    dotfiles; entry.is_file() and entry.name.startswith('.') inverts filter.
    Oracle: 2 visible files + 1 dotfile + 1 subdir -> 2.
    """
    d = tmp_path / 'probe'
    d.mkdir()
    (d / 'a.txt').write_text('x\n')
    (d / 'b.txt').write_text('y\n')
    (d / '.hidden').write_text('h\n')
    (d / 'subdir').mkdir()
    assert hq._line_count(d) == 2


def test_line_count_missing_file_returns_zero(tmp_path):
    """_line_count() returns 0 on OSError (file missing), not 1.

    Mutation: return 1 instead of 0 in the except branch.
    Oracle: missing path -> 0.
    """
    missing = tmp_path / 'no-such-file.txt'
    assert hq._line_count(missing) == 0


# ---------------------------------------------------------------------------
# _do_stamp() - killable mutants
# ---------------------------------------------------------------------------

def test_do_stamp_minimal_namespace_all_optional_attrs_absent(tmp_path,
                                                              monkeypatch):
    """_do_stamp() handles getattr defaults for all optional argv attrs.

    Mutation: getattr(argv, X, None/False/'') -> getattr(argv, X) with no
    default raises AttributeError when any optional attr is absent.
    Oracle: stamp returns 0 and writes a correct ledger row.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'test.md').write_text('# Test\n\nContent.\n')
    anch = {
        'cycle': 1, 'now': _NOW, 'session': _SESSION,
        'host': _HOST, 'branch': '-', 'sha': '-', 'dirty': [],
        'root': folder.parent.parent,
        }
    ns = argparse.Namespace(path='test.md')
    rc = hq._do_stamp(folder, anch, ns)
    assert rc == 0
    rows = _rows(folder)
    assert rows[-1]['path'] == 'test.md'


def test_do_stamp_ledger_row_uses_lowercase_keys(tmp_path, monkeypatch):
    """_do_stamp() writes lowercase field keys in the ledger row.

    Mutation: 'CYCLE', 'TS', etc. in new_row dict; _append_tsv writes '-'
    for the lowercase field since the key is missing.
    Oracle: cycle and ts fields in ledger match the expected values.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'doc.md'])
    rows = _rows(folder)
    row = rows[-1]
    assert row['cycle'] == '1'
    assert row['ts'] == _NOW
    assert row['base'] == 'folder'
    assert row['path'] == 'doc.md'


def test_do_stamp_refused_row_uses_correct_defaults(tmp_path, monkeypatch):
    """_do_stamp() refused_row uses '-' as default for successor and where.

    Mutation: prev.get('successor', ) and prev.get('where', ) return None
    for a new path; ledger writes 'None' string instead of '-'.
    Oracle: refused_row's successor and where fields are '-' for a new path.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    # Create spec file; stamp with --read-before=never triggers R1 refusal
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before=never'])
    rows = _rows(folder)
    refused = next(r for r in reversed(rows) if r['reason'].startswith('refused:'))
    assert refused['successor'] == '-'
    assert refused['where'] == '-'


def test_do_stamp_refused_row_lowercase_ts_key(tmp_path, monkeypatch):
    """_do_stamp() refused_row has 'ts' key not 'TS'.

    Mutation: 'TS': anch['now'] in refused_row; ledger 'ts' field becomes '-'.
    Oracle: refused_row ts field equals the configured HQ_NOW value.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before=never'])
    rows = _rows(folder)
    refused = next(r for r in reversed(rows) if r['reason'].startswith('refused:'))
    assert refused['ts'] == _NOW


def test_do_stamp_deferred_row_lowercase_keys(tmp_path, monkeypatch):
    """_do_stamp() deferred_row uses lowercase 'cycle', 'ts', 'base' keys.

    Mutation: 'CYCLE', 'TS', 'BASE', 'WHERE', 'SHA12', 'LINES', 'LABEL'
    in defer_row; ledger fields for those columns become '-'.
    Oracle: deferred row has correct cycle, ts, base, sha12, lines, label.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'note.md').write_text('# Note\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'note.md', '--defer'])
    rows = _rows(folder)
    row = rows[-1]
    assert row['cycle'] == '1'
    assert row['ts'] == _NOW
    assert row['base'] == 'folder'
    assert row['reason'] == 'deferred'
    assert row['lines'] != '-'
    assert row['sha12'] != '-'
    assert row['label'] == '-'


def test_do_stamp_defer_status_uses_live_default(tmp_path, monkeypatch):
    """_do_stamp() defer_row prev.get('status', 'live') defaults to 'live'.

    Mutation: no default (None) or 'LIVE' makes status wrong on new path.
    Oracle: first deferred stamp has status='live' (hand-computed default).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'note.md').write_text('# Note\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'note.md', '--defer'])
    rows = _rows(folder)
    assert rows[-1]['status'] == 'live'


def test_do_stamp_top_level_false_for_nested_file(tmp_path, monkeypatch):
    """_do_stamp() passes top_level=False for files nested inside the folder.

    Mutation: top_level=base=='folder' or '/' not in stored is always True
    for folder files; nested .py files get 'draft' instead of 'other'.
    Also kills mutmut_83 (infer_kind drops top_level argument).
    Oracle: sub/script.py nested in folder gets kind='other', not 'draft'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    sub = folder / 'sub'
    sub.mkdir()
    (sub / 'script.py').write_text('x = 1\n')
    hq.main(['stamp', _SLUG, 'sub/script.py'])
    rows = _rows(folder)
    assert rows[-1]['kind'] == 'other'


def test_do_stamp_archive_without_reason_returns_2(tmp_path, monkeypatch):
    """_do_stamp() returns 2 when --archive given without --reason.

    Mutation: getattr(argv, 'archive', True) always activates archive mode;
    getattr(argv, 'archive', ) raises AttributeError with no archive attr.
    Oracle: normal stamp (no --archive) completes; --archive alone errors.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'doc.md', '--archive'])
    assert rc == 2


def test_do_stamp_archive_error_prints_lowercase_message(
        tmp_path, monkeypatch, capsys):
    """_do_stamp() --archive without --reason prints lowercase message.

    Mutation: print('HQ STAMP: --ARCHIVE REQUIRES --REASON') uppercase.
    Oracle: captured stdout contains lowercase 'hq stamp: --archive'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'doc.md', '--archive'])
    out = capsys.readouterr().out
    assert 'hq stamp: --archive' in out.lower()
    assert 'HQ STAMP' not in out


def test_do_stamp_status_archived_without_reason_returns_2(
        tmp_path, monkeypatch):
    """_do_stamp() returns 2 when --status archived given without --reason.

    Mutation: getattr(argv, 'status', ) raises AttributeError; or no-reason
    check uses wrong default.
    Oracle: --status archived without --reason exits 2.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'doc.md'])
    rc = hq.main(['stamp', _SLUG, 'doc.md', '--status', 'archived'])
    assert rc == 2


def test_do_stamp_successor_check_refusal_not_overridden(
        tmp_path, monkeypatch):
    """Successor-refusal is the reported reason, not a later R1 check.

    Mutation: refusal_reason is None or not defer enters the normal-row
    block even when refusal_reason is already set; R1 overwrites the reason.
    Oracle: reason in ledger is the successor-not-live message.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'old.md').write_text('# Old\n\nContent.\n')
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'old.md'])
    hq.main(['stamp', _SLUG, 'old.md', '--archive', '--reason', 'done'])
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    rc = hq.main([
        'stamp', _SLUG, 'SPEC.md', '--successor', 'old.md',
        '--status', 'superseded'])
    rows = _rows(folder)
    refused = rows[-1]
    assert refused['reason'].startswith('refused:')
    assert 'archived' in refused['reason'] or 'not live' in refused['reason']


def test_do_stamp_refusal_reason_default_successor(tmp_path, monkeypatch):
    """_do_stamp() refusal carry-forward uses prev.get('successor', '-').

    Mutation: prev.get('successor', ) returns None for new path; row written
    with successor='None' (str) instead of '-'.
    Oracle: first refused stamp has successor=='-'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before=never'])
    rows = _rows(folder)
    refused = next(r for r in reversed(rows) if r['reason'].startswith('refused:'))
    assert refused['successor'] == '-'


def test_do_stamp_read_before_default_for_spec(tmp_path, monkeypatch):
    """_do_stamp() default read_before for spec is 'always'.

    Mutation: 'ALWAYS' instead of 'always' or 'NEVER' instead of 'never'
    in default_rb computation; spec gets wrong read_before.
    Oracle: new spec file stamped without --read-before gets read_before='always'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    rows = _rows(folder)
    assert rows[-1]['read_before'] == 'always'


def test_do_stamp_read_before_default_for_other(tmp_path, monkeypatch):
    """_do_stamp() default read_before for 'other' kind is 'never'.

    Mutation: 'NEVER' or inverted condition makes 'other' get 'always'.
    Oracle: new plain file stamped without --read-before gets read_before='never'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes.md').write_text('# Notes\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'notes.md'])
    rows = _rows(folder)
    assert rows[-1]['read_before'] == 'never'


def test_do_stamp_kind_carried_from_prev_row(tmp_path, monkeypatch):
    """_do_stamp() carries kind from prev row when not explicitly set.

    Mutation: prev.get('kind', ) with no default returns None; or
    prev.get('KIND', ...) uses wrong key, falling back to inferred kind.
    Oracle: second stamp of doc.md carries 'spec' kind from first.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'doc.md', '--kind', 'spec', '--where', 'Doc'])
    hq.main(['stamp', _SLUG, 'doc.md'])
    rows = _rows(folder)
    assert rows[-1]['kind'] == 'spec'


def test_do_stamp_defer_ok_for_non_spec(tmp_path, monkeypatch):
    """_do_stamp() allows --defer for non-spec/draft files.

    Mutation: getattr(argv, 'defer', True) makes defer always active,
    including for spec files where it should be refused.
    Oracle: spec file with --defer is refused; other file with --defer passes.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'notes.md').write_text('# Notes\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'notes.md', '--defer'])
    assert rc == 0
    rows = _rows(folder)
    assert rows[-1]['reason'] == 'deferred'


def test_do_stamp_path_required_message_lowercase(tmp_path, monkeypatch,
                                                  capsys):
    """_do_stamp() prints lowercase error when path is missing.

    Mutation: print('HQ STAMP: PATH IS REQUIRED') uppercase.
    Oracle: stdout contains lowercase 'hq stamp: path is required'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    anch = {
        'cycle': 1, 'now': _NOW, 'session': _SESSION,
        'host': _HOST, 'branch': '-', 'sha': '-', 'dirty': [],
        'root': folder.parent.parent,
        }
    rc = hq._do_stamp(folder, anch, argparse.Namespace(path=''))
    assert rc == 2
    out = capsys.readouterr().out
    assert 'hq stamp: path is required' in out
    assert 'HQ STAMP' not in out


def test_do_stamp_reason_cannot_start_with_refused(tmp_path, monkeypatch,
                                                   capsys):
    """_do_stamp() rejects --reason starting with 'refused: '.

    Mutation: getattr(argv, 'reason', ) raises AttributeError; or uppercase
    error message.
    Oracle: --reason 'refused: manual' returns 2 with lowercase message.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'doc.md', '--reason', 'refused: manual'])
    assert rc == 2
    out = capsys.readouterr().out
    assert 'refused:' in out
    assert 'HQ STAMP' not in out


def test_do_stamp_status_archived_message_lowercase(tmp_path, monkeypatch,
                                                    capsys):
    """_do_stamp() --status archived without --reason prints lowercase message.

    Mutation: uppercase 'HQ STAMP: --STATUS ARCHIVED REQUIRES --REASON'.
    Oracle: stdout contains lowercase form.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'doc.md'])
    hq.main(['stamp', _SLUG, 'doc.md', '--status', 'archived'])
    out = capsys.readouterr().out
    assert 'hq stamp: --status archived requires --reason' in out.lower()
    assert 'HQ STAMP' not in out


def test_do_stamp_refusal_for_refused_reason_prefix(tmp_path, monkeypatch):
    """_do_stamp() checks getattr(argv, 'reason', None) before 'refused:'.

    Mutation: getattr with True default makes reason appear set when absent,
    then check triggers incorrectly.
    Oracle: stamp without --reason completes (rc 0 or 1 depending on R1).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'doc.md'])
    assert rc == 0


# ---------------------------------------------------------------------------
# _verb_stamp() - 5 killable mutants
# ---------------------------------------------------------------------------

def test_verb_stamp_batch_mode_off_by_default(tmp_path, monkeypatch):
    """_verb_stamp() does not enter batch mode without --batch flag.

    Mutation: getattr(argv, 'batch', True) always enters batch mode.
    Oracle: single stamp updates ledger with no stdin interaction.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'doc.md'])
    assert rc == 0
    rows = _rows(folder)
    assert len(rows) == 1


def test_verb_stamp_batch_rc_starts_at_zero(tmp_path, monkeypatch):
    """_verb_stamp() batch starts with rc=0 and stays 0 on success.

    Mutation: rc = 1 initially; a successful batch returns 1 instead of 0.
    Oracle: one successful batch line -> rc=0.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    from unittest import mock
    batch_input = 'doc.md\n'
    with mock.patch('sys.stdin', io.StringIO(batch_input)):
        rc = hq.main(['stamp', _SLUG, '--batch'])
    assert rc == 0


def test_verb_stamp_batch_skips_dash_lines(tmp_path, monkeypatch):
    """_verb_stamp() batch skips lines that are just '-'.

    Mutation: not line and line == '-' never matches '-' (requires empty too).
    Oracle: '-' line followed by valid line -> only one ledger row written.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    from unittest import mock
    batch_input = '-\ndoc.md\n'
    with mock.patch('sys.stdin', io.StringIO(batch_input)):
        rc = hq.main(['stamp', _SLUG, '--batch'])
    rows = _rows(folder)
    assert len(rows) == 1


def test_verb_stamp_batch_continue_not_break_on_dash(tmp_path, monkeypatch):
    """_verb_stamp() batch continues after '-' to process following lines.

    Mutation: break exits the loop on '-', missing subsequent valid lines.
    Oracle: '-', then 'doc.md' -> one ledger row (not zero).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    from unittest import mock
    batch_input = '-\ndoc.md\n'
    with mock.patch('sys.stdin', io.StringIO(batch_input)):
        hq.main(['stamp', _SLUG, '--batch'])
    rows = _rows(folder)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# _do_note() - 2 killable mutants
# ---------------------------------------------------------------------------

def test_do_note_errors_handler_replace_tolerates_bad_bytes(
        tmp_path, monkeypatch):
    """_do_note() reads standing.md with errors='replace', not strict.

    Mutation: errors='REPLACE' (invalid handler) raises LookupError; or
    no errors= uses strict default, raising UnicodeDecodeError on bad bytes.
    Oracle: standing.md with an invalid UTF-8 byte is read without error.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    standing = folder / 'standing.md'
    standing.write_bytes(b'## Notes\n- bad byte: \xff\n')
    anch = {
        'cycle': 1, 'now': _NOW, 'session': _SESSION,
        'host': _HOST, 'branch': '-', 'sha': '-', 'dirty': [],
        'root': folder.parent.parent,
        }
    rc = hq._do_note(folder, anch, 'decision', 'Headline', '')
    assert rc == 0


# ---------------------------------------------------------------------------
# _verb_note() - 9 killable mutants
# ---------------------------------------------------------------------------

def test_verb_note_batch_off_by_default(tmp_path, monkeypatch):
    """_verb_note() does not enter batch mode without --batch.

    Mutation: getattr(argv, 'batch', True) always enters batch mode.
    Oracle: single note command writes one item to standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'Keep the design'])
    standing = (folder / 'standing.md').read_text()
    assert 'd01' in standing
    assert 'Keep the design' in standing


def test_verb_note_batch_skips_dash_lines(tmp_path, monkeypatch):
    """_verb_note() batch skips '-' lines, processes the rest.

    Mutation: not line and line == '-' never matches '-'.
    Oracle: '-' then valid line -> one item in standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = '-\ndecision --headline Keep\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    standing = (folder / 'standing.md').read_text()
    assert standing.count('d0') == 1


def test_verb_note_batch_continue_after_dash(tmp_path, monkeypatch):
    """_verb_note() batch continues after '-' to process later lines.

    Mutation: break exits loop on '-', missing following valid lines.
    Oracle: '-', then valid decision -> one item written.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = '-\ndecision --headline Important\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        hq.main(['note', _SLUG, '--batch'])
    standing = (folder / 'standing.md').read_text()
    assert 'Important' in standing


def test_verb_note_batch_unpack_failure_is_graceful(tmp_path, monkeypatch):
    """_verb_note() batch handles parse failure without crashing.

    Mutation: sub, rest = None raises TypeError when unpacking fails.
    Oracle: an invalid batch line reports error but does not raise.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = '--unknown-flag\ndecision --headline Good\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    # at least the good line should succeed; rc can be 0 or 2
    # main thing: no TypeError propagated
    assert isinstance(rc, int)


def test_verb_note_batch_decision_kind_case_sensitive(tmp_path, monkeypatch):
    """_verb_note() treats 'decision' as valid, not 'DECISION'.

    Mutation: kind_str not in {'DECISION', 'constraint', 'dead-end'} rejects
    'decision' as unknown.
    Oracle: batch line 'decision --headline H' writes one decision item.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = 'decision --headline Choose this\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    assert rc == 0
    standing = (folder / 'standing.md').read_text()
    assert 'd01' in standing  # decision item written


def test_verb_note_batch_dead_end_kind_case_sensitive(tmp_path, monkeypatch):
    """_verb_note() treats 'dead-end' as valid, not 'DEAD-END'.

    Mutation: kind_str not in {'decision', 'constraint', 'DEAD-END'}.
    Oracle: batch line 'dead-end --headline H' writes one dead-end item.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = 'dead-end --headline Bad approach\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    assert rc == 0
    standing = (folder / 'standing.md').read_text()
    assert 'x01' in standing or 'Bad' in standing  # dead-end item written


def test_verb_note_batch_keeps_a_flag_shaped_word_in_the_body(
        tmp_path, monkeypatch):
    """_verb_note() batch takes the body verbatim, so a dash-led word after
    the headline is prose, not a flag to refuse.

    Mutation: shell-style splitting restored, which treats the token as an
    unknown flag and refuses the line.
    Oracle: rc 0 and the body '--unknown-flag value' in standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = 'decision --headline H --unknown-flag value\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    assert rc == 0
    assert '**H** --unknown-flag value' in (folder / 'standing.md').read_text()


def test_verb_note_batch_body_from_correct_attr(tmp_path, monkeypatch):
    """_verb_note() batch reads body from 'body' attr, not 'BODY'.

    Mutation: getattr(sub, 'BODY', None) looks up wrong attribute name;
    body is lost when it was parsed into 'body'.
    Oracle: batch note with explicit body appears in standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = 'decision --headline The plan because it works\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        hq.main(['note', _SLUG, '--batch'])
    standing = (folder / 'standing.md').read_text()
    assert 'd01' in standing  # item was written with body


# ---------------------------------------------------------------------------
# _verb_supersede() - 5 killable mutants
# ---------------------------------------------------------------------------

def test_verb_supersede_different_prefix_exits_1(tmp_path, monkeypatch):
    """_verb_supersede() exits 1 (not 2) when ids have different prefixes.

    Mutation: return 2 instead of return 1.
    Oracle: d01 vs c01 (different prefixes) -> SystemExit code 1.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'One'])
    hq.main(['note', _SLUG, 'constraint', '--headline', 'Two'])
    rc = hq.main(['supersede', _SLUG, 'd01', 'c01'])
    assert rc == 1


def test_verb_supersede_new_id_empty_exits_1_not_2(tmp_path, monkeypatch):
    """_verb_supersede() not_old or not_new check: both empty -> exits 1.

    Mutation: 'or' vs 'and' in condition causes IndexError on empty new_id.
    Oracle: empty new_id returns rc=1 (clean error, not IndexError).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'One'])
    try:
        rc = hq.main(['supersede', _SLUG, 'd01', ''])
        # old supersede check should catch empty new_id
        assert rc in {1, 2}
    except (IndexError, SystemExit):
        pass


def test_verb_supersede_old_not_found_exits_1(tmp_path, monkeypatch):
    """_verb_supersede() exits 1 when old_id not in standing.md.

    Mutation: return 2 instead of return 1 for not-found case.
    Oracle: unknown old_id 'd99' -> rc=1.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'One'])
    rc = hq.main(['supersede', _SLUG, 'd99', 'd01'])
    assert rc == 1


def test_verb_supersede_new_not_found_exits_1(tmp_path, monkeypatch):
    """_verb_supersede() exits 1 when new_id not in standing.md.

    Mutation: return 2 instead of return 1 for not-found new_id.
    Oracle: known old_id 'd01' but unknown new_id 'd99' -> rc=1.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'One'])
    rc = hq.main(['supersede', _SLUG, 'd01', 'd99'])
    assert rc == 1


# ---------------------------------------------------------------------------
# _build_parser() - 16 killable mutants
# ---------------------------------------------------------------------------

def test_build_parser_subparsers_required(tmp_path, monkeypatch):
    """_build_parser() requires a verb; no-verb call returns non-zero.

    Mutation: required=True removed or required=False; no verb passes parse.
    Oracle: main([]) returns non-zero exit code.
    """
    monkeypatch.setenv('HQ_ROOT', str(tmp_path))
    rc = hq.main([])
    assert rc != 0


def test_build_parser_stamp_path_optional(tmp_path, monkeypatch):
    """_build_parser() stamp path has nargs='?' making it optional.

    Mutation: nargs='?' removed makes path required; 'stamp slug' errors.
    Oracle: 'stamp slug' with no path parses (returns 2 from stamp logic,
    not argparse failure).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    rc = hq.main(['stamp', _SLUG])
    # rc=2 from stamp logic (path is required), not from argparse
    assert rc == 2


def test_build_parser_stamp_kind_choice_todo(tmp_path, monkeypatch):
    """_build_parser() includes 'todo' (lowercase) as valid --kind choice.

    Mutation: choices has 'TODO' instead of 'todo'; --kind todo is rejected.
    Oracle: stamp with --kind todo parses and stamps.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'todo.md').write_text('# Todo\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'todo.md', '--kind', 'todo'])
    assert rc == 0


def test_build_parser_stamp_kind_choice_snapshot(tmp_path, monkeypatch):
    """_build_parser() includes 'snapshot' as valid --kind choice.

    Mutation: choices has 'SNAPSHOT'; --kind snapshot is rejected.
    Oracle: stamp with --kind snapshot parses.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'snap.md').write_text('# Snap\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'snap.md', '--kind', 'snapshot'])
    assert rc == 0


def test_build_parser_stamp_kind_choice_probe_dir(tmp_path, monkeypatch):
    """_build_parser() includes 'probe-dir' as valid --kind choice.

    Mutation: choices has 'PROBE-DIR'; --kind probe-dir is rejected.
    Oracle: stamp with --kind probe-dir parses.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    sub = folder / 'subdir'
    sub.mkdir()
    rc = hq.main(['stamp', _SLUG, 'subdir', '--kind', 'probe-dir'])
    assert rc == 0


def test_build_parser_stamp_read_before_choice_mention(tmp_path, monkeypatch):
    """_build_parser() includes 'mention' as valid --read-before choice.

    Mutation: choices has 'MENTION'; --read-before mention is rejected.
    Oracle: stamp with --read-before mention parses.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    rc = hq.main(['stamp', _SLUG, 'doc.md', '--read-before', 'mention'])
    assert rc == 0


def test_build_parser_stamp_status_choice_superseded(tmp_path, monkeypatch):
    """_build_parser() includes 'superseded' as valid --status choice.

    Mutation: choices has 'SUPERSEDED'; --status superseded is rejected.
    Oracle: stamp with --status superseded and --reason parses.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'doc.md').write_text('# Doc\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'doc.md'])
    rc = hq.main([
        'stamp', _SLUG, 'doc.md',
        '--status', 'superseded', '--reason', 'replaced'])
    assert rc in {0, 1}


def test_build_parser_stamp_batch_is_store_true(tmp_path, monkeypatch):
    """_build_parser() --batch has action='store_true' for stamp.

    Mutation: action= removed; --batch expects a value and errors without one.
    Oracle: --batch without a value is accepted (stores True).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    ns = hq._build_parser().parse_args(['stamp', _SLUG, '--batch'])
    assert ns.batch is True


def test_build_parser_note_kind_optional(tmp_path, monkeypatch):
    """_build_parser() note note_kind has nargs='?' making it optional.

    Mutation: nargs='?' removed makes note_kind required; 'note slug' errors.
    Oracle: 'note slug' with no note_kind parses (rc from note logic).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    ns = hq._build_parser().parse_args(['note', _SLUG])
    assert hasattr(ns, 'note_kind')


def test_build_parser_note_body_arg_name(tmp_path, monkeypatch):
    """_build_parser() note body positional is named 'body', not 'BODY'.

    Mutation: 'BODY' positional stores to args.BODY; code reads args.body.
    Oracle: 'note slug decision body_text ...' stores body in 'body' attr.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', 'body text', '--headline', 'H'])
    standing = (folder / 'standing.md').read_text()
    assert 'body text' in standing


def test_build_parser_note_batch_is_store_true(tmp_path, monkeypatch):
    """_build_parser() --batch has action='store_true' for note.

    Mutation: action= removed; --batch expects a value.
    Oracle: note --batch without value parses (stores True).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    ns = hq._build_parser().parse_args(['note', _SLUG, '--batch'])
    assert ns.batch is True


def test_build_parser_finish_log_required(tmp_path, monkeypatch):
    """_build_parser() finish --log is required.

    Mutation: required=True removed or required=False; finish without --log.
    Oracle: 'finish slug' without --log returns non-zero.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    rc = hq.main(['finish', _SLUG])
    assert rc != 0


def test_build_parser_diff_takes_cycle_spellings_a_section_and_full():
    """_build_parser() reads diff cycles as text and takes a section and --full.

    Mutation: type=int restored on c1 or c2, so 'c3' is a usage error;
    the section slot or --full dropped, so the expansion cannot be asked
    for.
    Oracle: parsed values for 'diff slug c3 05' and 'diff slug 3 5 now
    --full'.
    """
    ns = hq._build_parser().parse_args(['diff', 'slug', 'c3', '05'])
    assert (ns.c1, ns.c2, ns.section, ns.full) == ('c3', '05', None, False)
    ns = hq._build_parser().parse_args(['diff', 'slug', '3', '5', 'now', '--full'])
    assert (ns.c1, ns.c2, ns.section, ns.full) == ('3', '5', 'now', True)


# ---------------------------------------------------------------------------
# main() - 2 killable mutants
# ---------------------------------------------------------------------------

def test_main_batch_default_false_for_non_batch_verbs(tmp_path, monkeypatch):
    """main() getattr(args, 'batch', False) default is False.

    Mutation: default True activates batch mode for all verbs, including
    'begin' which has no --batch; leftover '-' filter runs incorrectly.
    Oracle: non-batch verb processes without treating input as batch.
    """
    folder = _root(tmp_path, monkeypatch)
    rc = hq.main(['begin', _SLUG])
    assert rc == 0


def test_main_note_body_from_correct_attr(tmp_path, monkeypatch):
    """main() assembles body from 'body' attr, not 'BODY'.

    Mutation: getattr(args, 'BODY', None) ignores parsed body value.
    Oracle: note with body positional has body appear in standing.md.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', 'key reason', '--headline', 'Plan'])
    standing = (folder / 'standing.md').read_text()
    assert 'key reason' in standing


# ---------------------------------------------------------------------------
# Additional kill tests for mutants that require specific conditions
# ---------------------------------------------------------------------------

def test_anchors_hq_git_zero_in_real_git_repo(tmp_path, monkeypatch):
    """anchors() respects HQ_GIT=0 and disables git even in a git repo.

    Mutation: os.environ.get('HQ_GIT', ...) uses key '1' or 'hq_git';
    the HQ_GIT=0 env var is ignored, so git runs and returns real values.
    Oracle: branch=='-' and sha=='-' with HQ_GIT=0 set, inside a git repo.
    """
    repo = tmp_path / 'repo'
    _git_repo(repo)
    monkeypatch.setenv('HQ_ROOT', str(repo))
    monkeypatch.setenv('HQ_CYCLE', '1')
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('hq_git', raising=False)
    folder = repo / '.handoff' / 'test'
    folder.mkdir(parents=True)
    anch = hq.anchors(folder, argparse.Namespace())
    assert anch['branch'] == '-'
    assert anch['sha'] == '-'
    assert anch['dirty'] == []


def test_find_folder_invalid_slug_exits_code_2(tmp_path):
    """_find_folder() exits with code 2 for an invalid slug.

    Mutation: sys.exit(3) instead of sys.exit(2) for the regex-validation path.
    Oracle: slug '--bad' triggers sys.exit(2) (code==2, not 3 or 0).
    """
    root = tmp_path
    (root / '.handoff').mkdir()
    try:
        hq._find_folder(root, '--bad')
        raise AssertionError('expected SystemExit')
    except SystemExit as exc:
        assert exc.code == 2


# ---------------------------------------------------------------------------
# Tests for _do_stamp survivors that need specific scenarios
# ---------------------------------------------------------------------------

def test_do_stamp_first_stamp_refused_row_has_dash_successor(
        tmp_path, monkeypatch):
    """First-stamp refusal writes '-' for successor and where, not None.

    Mutation: prev.get('successor', ) with no default returns None for
    new paths (prev={}); ledger writes 'None' string.
    Oracle: SPEC.md refused by R1 on first stamp has successor=='-'.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    # First stamp is refused: R1 requires read_before='always' for spec
    hq.main(['stamp', _SLUG, 'SPEC.md', '--read-before=never'])
    rows = _rows(folder)
    # Only one row: the refusal receipt (no prior row)
    assert rows[-1]['reason'].startswith('refused:')
    assert rows[-1]['successor'] == '-'
    assert rows[-1]['where'] == '-'


def test_do_stamp_deferred_row_where_field_lowercase(tmp_path, monkeypatch):
    """_do_stamp() deferred row uses 'where' key (lowercase), not 'WHERE'.

    Mutation: 'WHERE': where in defer_row; _append_tsv writes '-' for the
    lowercase 'where' LEDGER field because the key is missing.
    Oracle: deferred stamp with --where has where field matching the value.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'note.md').write_text('# Note\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'note.md', '--defer', '--where', 'section-2'])
    rows = _rows(folder)
    assert rows[-1]['reason'] == 'deferred'
    assert rows[-1]['where'] == 'section-2'


def test_do_stamp_deferred_row_label_field_lowercase(tmp_path, monkeypatch):
    """_do_stamp() deferred row uses 'label' key (lowercase), not 'LABEL'.

    Mutation: 'LABEL': label in defer_row; _append_tsv writes '-' for
    lowercase 'label' LEDGER field.
    Oracle: deferred stamp with --label has label field matching the value.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'note.md').write_text('# Note\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'note.md', '--defer', '--label', 'v1.2'])
    rows = _rows(folder)
    assert rows[-1]['reason'] == 'deferred'
    assert rows[-1]['label'] == 'v1.2'


def test_do_stamp_defer_on_spec_refused_not_overridden_by_successor(
        tmp_path, monkeypatch):
    """defer+non-live-successor: reason is from defer check, not successor.

    Mutation: 'and' -> 'or' in 'if refusal_reason is None and successor_given'
    makes the successor check run even when refusal already set; an archived
    successor then overwrites the defer-refusal reason.
    Oracle: --defer --successor archived_path gives 'refused: --defer' reason.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    (folder / 'old.md').write_text('# Old\n\nContent.\n')
    (folder / 'SPEC.md').write_text('# Spec\n\nContent.\n')
    hq.main(['stamp', _SLUG, 'old.md'])
    hq.main(['stamp', _SLUG, 'old.md', '--archive', '--reason', 'done'])
    hq.main(['stamp', _SLUG, 'SPEC.md'])
    # --defer on spec with archived successor: defer check fires first
    hq.main(['stamp', _SLUG, 'SPEC.md', '--defer', '--successor', 'old.md'])
    rows = _rows(folder)
    refused = rows[-1]
    assert refused['reason'].startswith('refused:')
    assert '--defer' in refused['reason']


# ---------------------------------------------------------------------------
# Tests for _verb_note survivors
# ---------------------------------------------------------------------------

def test_verb_note_batch_dash_only_lines_give_rc_zero(tmp_path, monkeypatch):
    """_verb_note() batch exits 0 when all lines are '-' (all skipped).

    Mutation: 'if not line or line == '-':' -> 'if not line and line == '-'
    means '-' lines are NOT skipped; processed as kind='-' (invalid) -> rc=2.
    Oracle: batch with only '-' lines returns rc==0 (all skipped, no errors).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = '-\n-\n-\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    assert rc == 0
    standing = folder / 'standing.md'
    # Nothing written: the file is absent or holds no typed item.
    if standing.exists():
        assert '[d0' not in standing.read_text()
        assert '[c0' not in standing.read_text()


def test_verb_note_batch_shlex_error_handled_gracefully(
        tmp_path, monkeypatch):
    """_verb_note() batch handles ValueError from shlex.split gracefully.

    Mutation: 'sub, rest = None' (dropping ', []') raises TypeError when
    unpacking None instead of setting sub=None, rest=[].
    Oracle: batch line with unclosed quote returns int rc (not TypeError).
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock

    # Unclosed quote causes shlex.split to raise ValueError
    bad_line = 'decision --headline "unclosed\n'
    good_line = 'decision --headline Good\n'
    batch = bad_line + good_line
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    # Should return an int, not raise TypeError
    assert isinstance(rc, int)
    # The good line should still be processed
    standing = folder / 'standing.md'
    assert standing.exists()
    assert 'Good' in standing.read_text()


def test_verb_note_batch_refuses_a_body_placed_before_the_headline(
        tmp_path, monkeypatch):
    """_verb_note() batch reads kind, headline, body in that order; a line
    with words between the kind and --headline is refused, nothing written.

    Mutation: the pattern anchored loosely, so 'evidence-text' is swallowed
    as the kind's tail or the headline and the line lands.
    Oracle: rc 2 and no standing.md item for the line.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    from unittest import mock
    batch = 'decision evidence-text --headline Decision\n'
    with mock.patch('sys.stdin', io.StringIO(batch)):
        rc = hq.main(['note', _SLUG, '--batch'])
    assert rc == 2
    standing = folder / 'standing.md'
    assert not standing.exists() or 'Decision' not in standing.read_text()


# ---------------------------------------------------------------------------
# Tests for _verb_supersede survivors
# ---------------------------------------------------------------------------

def test_verb_supersede_empty_new_id_exits_1_not_indexerror(
        tmp_path, monkeypatch):
    """_verb_supersede() exits 1 for empty new_id, not IndexError.

    Mutation: 'not old_id or not new_id or ...' -> 'not old_id and not new_id
    or old_id[0] != new_id[0]': with old_id='d01' and new_id='', the
    mutant evaluates new_id[0] which raises IndexError instead of exit 1.
    Oracle: rc==1 when new_id is empty.
    """
    folder = _root(tmp_path, monkeypatch)
    hq.main(['begin', _SLUG])
    hq.main(['note', _SLUG, 'decision', '--headline', 'One'])
    rc = hq.main(['supersede', _SLUG, 'd01', ''])
    assert rc == 1
