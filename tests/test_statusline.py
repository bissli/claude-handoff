"""Tests for the status line's gauge against the handoff point."""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))

import statusline as sl  # noqa: E402  (path must be set before this resolves)


def test_past_escalation_the_line_names_the_multiple_of_the_handoff_point(
        tmp_path, monkeypatch):
    """Verify the red line reads context over the handoff point, one decimal.

    Mutation: dividing by the escalation point in place of the handoff
    point, or dropping the multiple so 1.2x and 3.0x read alike.
    Oracle: hand-computed - handoff 262K, escalation 291K: 310K reads
    1.2x (1.1x against escalation) and 786K reads 3.0x.
    """
    monkeypatch.setattr(sl, 'STATE_DIR', str(tmp_path))
    (tmp_path / 's.json').write_text(
        json.dumps({'handoff': 262_000, 'escalate': 291_000}))

    def visible(context):
        return re.sub(r'\x1b\[[0-9;]*m', '', sl.render({
            'session_id': 's',
            'model': {'display_name': 'Opus 5'},
            'workspace': {'project_dir': '/x/proj'},
            'context_window': {'total_input_tokens': context},
            }))

    assert visible(310_000) == '310K/262K 1.2x over  Opus 5 proj'
    assert visible(786_000).startswith('786K/262K 3.0x over')
