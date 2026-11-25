"""Shared budget arithmetic for the Stop hook, the nudge, and the status line.

A cycle hands off once its context has grown a fixed work budget W past
F, the billed context of the cycle's first call. Room for work stays the
same however large the resume grows, and the rule needs no price table.
"""

import os

# Context a handoff write adds. The write prints the handoff rules and
# writes one document, and neither grows with the work before it.
HANDOFF_WRITE_TOKENS = 29_000

# Context at which Claude Code compacts a session on its own, on a 1M
# window: the window less a 20,000-token output reserve and a
# 13,000-token buffer. A handoff point closer to it than one handoff
# write leaves no room to finish that write.
AUTO_COMPACT_TOKENS = 967_000

# Billed context on the first call after a restart in place: the system
# prompt, tools, and instruction files all return, along with whatever
# the platform's own reset carries forward.
RESTART_IN_PLACE_TOKENS = 123_000

DEFAULT_WORK_BUDGET = 200_000


def work_budget() -> int:
    """Tokens of context a cycle may add past its first call, W.

    Returns
    -------
    int
        ``HQ_WORK_BUDGET`` read as a whole number of tokens, or
        ``DEFAULT_WORK_BUDGET`` when the variable is unset or empty.

    Raises
    ------
    ValueError
        ``HQ_WORK_BUDGET`` holds anything but a positive whole number.
    """
    tokens = int(os.environ.get('HQ_WORK_BUDGET') or DEFAULT_WORK_BUDGET)
    if tokens <= 0:
        raise ValueError(f'HQ_WORK_BUDGET must be positive, got {tokens}')
    return tokens


def handoff_point(floor: int) -> int:
    """Context at which a cycle should hand off.

    Parameters
    ----------
    floor : int
        Billed context of the cycle's first call, F, in tokens.

    Returns
    -------
    int
        ``min(F + W, AUTO_COMPACT_TOKENS - HANDOFF_WRITE_TOKENS)`` in
        billed tokens, W from :func:`work_budget`.
    """
    return min(floor + work_budget(),
               AUTO_COMPACT_TOKENS - HANDOFF_WRITE_TOKENS)
