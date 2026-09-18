"""Conservation on numbered standing items and heading-form Key files
labels.
"""

import pathlib

import pytest

from bin import hq

_SLUG = 'cons-test'
_NOW = '2026-09-10T09:00:00'
_SESSION = 'session-cons'
_HOST = 'test-host'

_CURSOR = (
    '## Task\n\nDo the task.\n\n'
    '## Now\n\nNext step.\n\n'
    '## Plan\n\nPlan line.\n\n'
    '## State\n\nState line.\n\n'
    '## Environment\n\nEnv line.\n\n'
    '## Open questions\n\nNone.\n'
)


def _setup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, slug: str = _SLUG,
) -> pathlib.Path:
    """Create HQ_ROOT and set env vars; return the handoff folder path.
    """
    root = pathlib.Path(tmp_path) / 'root'
    root.mkdir(exist_ok=True)
    monkeypatch.setenv('HQ_ROOT', str(root))
    monkeypatch.setenv('HQ_NOW', _NOW)
    monkeypatch.setenv('HQ_SESSION', _SESSION)
    monkeypatch.setenv('HQ_HOST', _HOST)
    monkeypatch.setenv('HQ_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('HQ_GIT', '0')
    monkeypatch.delenv('HQ_CYCLE', raising=False)
    folder = root / '.handoff' / slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_handoff(
    folder: pathlib.Path, extra_sections: str = '', cycle: int = 3,
) -> None:
    """Write a conforming HANDOFF.md with the cursor and extra sections.
    """
    text = (
        f'# Handoff: {folder.name}\n\n'
        f'Written: 2026-09-01 | Cycle: {cycle}\n\n'
        + _CURSOR
        + extra_sections
    )
    (folder / 'HANDOFF.md').write_text(text, encoding='utf-8')


def test_conservation_numbered_decisions_carried(tmp_path, monkeypatch, capsys):
    r"""A numbered Decisions item is reported carried after adopt.

    Mutation: _normalize missing the r'^\\d+[.)]\\s+' substitution, leaving
    '1. Choose Postgres...' unstripped while standing.md has 'Choose
    Postgres...' - the two never match.
    Oracle: 'conservation: every original line carried' in stdout after adopt
    on a HANDOFF.md whose Decisions section holds a numbered bold item.
    """
    folder = _setup(tmp_path, monkeypatch)
    _write_handoff(
        folder,
        extra_sections=(
            '\n## Decisions\n\n'
            '1. **Choose Postgres** The database.\n\n'
            '## Constraints\n\n'
            '1. Must run on Linux.\n\n'
            '## Dead ends\n\n'
            '2. NoSQL was too slow.\n'
        ),
    )
    rc = hq.main(['adopt', _SLUG])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert 'conservation: every original line carried' in out


def test_conservation_kf_label_heading_carried(tmp_path, monkeypatch, capsys):
    """A heading-form grade label under Key files is reported carried.

    Mutation: section_content builder missing the kf_label guard, so a
    '## Read now:' heading opens a new section and Key files gets no content
    lines - _heading_covered returns False, reporting the heading as missing.
    Oracle: 'conservation: every original line carried' in stdout after adopt
    on a HANDOFF.md with '## Read now:' inside the Key files section.
    """
    folder = _setup(tmp_path, monkeypatch)
    _write_handoff(
        folder,
        extra_sections=(
            '\n## Key files\n\n'
            '## Read now:\n'
            '- some-file.py core implementation\n\n'
            '## Read now, under src/ unless noted:\n'
            '- other-file.py secondary module\n'
        ),
    )
    rc = hq.main(['adopt', _SLUG])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert 'conservation: every original line carried' in out


def test_conservation_reports_a_label_heading_outside_key_files(
        tmp_path, monkeypatch, capsys):
    """A heading shaped like a grade label outside Key files is reported lost.

    Mutation: the label suppression applied to every heading, so an empty
    'Reference only, ...:' section that adopt drops passes conservation.
    Oracle: 'conservation: 1 original lines not carried' with that heading
    named under 'not carried:'.
    """
    folder = _setup(tmp_path, monkeypatch)
    _write_handoff(
        folder,
        extra_sections='\n## Reference only, until the audit clears the ledger:\n')
    rc = hq.main(['adopt', _SLUG])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert 'conservation: 1 original lines not carried' in out
    assert 'not carried: ## Reference only, until the audit clears' in out


def test_adopt_counts_the_bullets_it_left_unfiled(tmp_path, monkeypatch, capsys):
    """Adopt names how many Unfiled bullets the rewrite left behind.

    Mutation: the summary reporting conservation alone, so a run that
    parks every foreign line under ## Unfiled prints 'every original line
    carried' and the agent meets the untyped-bullet refusal only at
    finish; or the line printed after the conservation block, where it
    reads as one of its 'not carried' lines.
    Oracle: hand-computed - a foreign section of two bullets yields two
    '- unfiled:' bullets, so the summary reads 'unfiled: 2 bullets to
    rehome' above the conservation line; a file with nothing unfiled
    prints no such line.
    """
    folder = _setup(tmp_path, monkeypatch)
    _write_handoff(folder, '\n## Background context\n\n- One stray note.\n'
                   '- Another stray note.\n')

    assert hq.main(['adopt', _SLUG]) == 0

    out = capsys.readouterr().out
    assert '  unfiled: 2 bullets to rehome' in out.splitlines()
    assert out.index('unfiled: 2') < out.index('conservation:')
    clean = _setup(tmp_path, monkeypatch, slug='cons-clean')
    _write_handoff(clean)
    assert hq.main(['adopt', 'cons-clean']) == 0
    assert 'unfiled:' not in capsys.readouterr().out


def _set_cursor(folder: pathlib.Path, body: str) -> None:
    """Overwrite the cursor, keeping the header line the script owns.
    """
    handoff = folder / 'HANDOFF.md'
    text = handoff.read_text()
    handoff.write_text(text[:text.index('\n## ') + 1] + body)


_MOVED_PARA = '- Verified: page_key() separates its two inputs with a null byte.\n'
_WITH_PARA = (
    '## Task\nSkip a rendered page.\n\n## Now\nWire page_key().\n\n'
    f'## State\n{_MOVED_PARA}')
_WITHOUT_PARA = '## Task\nSkip a rendered page.\n\n## Now\nBenchmark it.\n'


def test_a_line_moved_into_a_file_stamped_this_cycle_is_carried(
        tmp_path, monkeypatch, capsys):
    """A rehome carries the line only when its file is stamped this cycle.

    Mutation: the same-cycle texts dropped from the union, so a line
    moved into a fresh output is reported; or the file set widened to
    every live row, so a line matching a file stamped cycles ago is
    suppressed with nothing prompting the re-stamp.
    Oracle: the identical rehome either side of the cycle boundary the
    rule is keyed on, with the file's kind deliberately not notes and
    its grade not edit, so sibling_texts cannot be what passes it.
    """
    folder = _setup(tmp_path, monkeypatch, slug='cons-moved')
    assert hq.main(['begin', 'cons-moved']) == 0
    _set_cursor(folder, _WITH_PARA)
    assert hq.main(['finish', 'cons-moved', '--log', 'one']) == 0
    assert hq.main(['begin', 'cons-moved']) == 0
    (folder / 'outputs').mkdir(exist_ok=True)
    (folder / 'outputs' / 'moved.md').write_text('# Moved\n\n' + _MOVED_PARA)
    assert hq.main([
        'stamp', 'cons-moved', 'outputs/moved.md',
        '--label', 'the state line this cycle rehomed']) == 0
    rows = hq._read_tsv(folder / 'ledger.tsv', hq.LEDGER_FIELDS)
    row = {r['path']: r for r in rows}['outputs/moved.md']
    assert (row['kind'], row['read_before']) == ('other', 'never')
    _set_cursor(folder, _WITHOUT_PARA)
    capsys.readouterr()
    rc = hq.main(['finish', 'cons-moved', '--log', 'two'])
    assert rc == 0
    assert 'not carried' not in capsys.readouterr().out

    earlier = _setup(tmp_path, monkeypatch, slug='cons-earlier')
    assert hq.main(['begin', 'cons-earlier']) == 0
    (earlier / 'outputs').mkdir(exist_ok=True)
    (earlier / 'outputs' / 'moved.md').write_text('# Moved\n\n' + _MOVED_PARA)
    assert hq.main([
        'stamp', 'cons-earlier', 'outputs/moved.md',
        '--label', 'the state line, stamped a cycle early']) == 0
    _set_cursor(earlier, _WITH_PARA)
    assert hq.main(['finish', 'cons-earlier', '--log', 'one']) == 0
    assert hq.main(['begin', 'cons-earlier']) == 0
    _set_cursor(earlier, _WITHOUT_PARA)
    capsys.readouterr()
    rc = hq.main(['finish', 'cons-earlier', '--log', 'two'])
    assert rc == 1
    assert f'  not carried: {_MOVED_PARA.strip()}' in capsys.readouterr().out
