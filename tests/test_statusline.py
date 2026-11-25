"""Tests for the status line's gauge against the handoff point."""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))

import statusline as sl  # noqa: E402  (path must be set before this resolves)


def test_the_gauge_color_changes_at_each_threshold(tmp_path, monkeypatch):
    """Verify green below the handoff point, amber from it, red from escalation.

    Mutation: > in place of >= at either threshold, or the two branches
    swapped so the escalation point turns the line amber.
    Oracle: inputs at each threshold and one token below it, handoff
    262,000 and escalation 291,000.
    """
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path))
    (tmp_path / 's.json').write_text(
        json.dumps({'handoff': 262_000, 'escalate': 291_000}))

    def color(context):
        return sl.render({
            'session_id': 's',
            'model': {'display_name': 'Opus 5'},
            'workspace': {'project_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            })[:5]

    assert color(261_999) == sl.GREEN
    assert color(262_000) == sl.YELLOW
    assert color(290_999) == sl.YELLOW
    assert color(291_000) == sl.RED
