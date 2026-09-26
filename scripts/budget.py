"""Shared budget arithmetic for the Stop hook and the status line.

The budget is a cost rule. A session should hand off where the cost per
call of work is lowest: staying longer re-reads a growing context on
every call, and leaving pays a cycle's fixed overhead again. The point
between the two follows from the cycle's own measurements and from a
ratio of two prices, never from a dollar figure.

Notes
-----
- Haiku is not listed: its 200K window holds a session well under any
  point worth announcing, so a warning would spend more attention than
  it saves.
"""

import math
from dataclasses import dataclass

# Notes:
# - Keys match as substrings of the model id, most specific first, so
#   a generation priced on its own is found before its family.
# - The cache-read price is stored, not the base price and the
#   multiplier against it, because that multiplier varies by model:
#   see CACHE_READ_MULTIPLIER.
CACHE_READ_PER_MTOK = {
    'fable-5-1': 0.25,
    'fable': 1.00,
    'opus-5-5': 0.20,
    'opus': 0.50,
    'sonnet-5': 0.20,
    'sonnet': 0.30,
    }

# Cache-read price as a fraction of the base input price, per tier.
CACHE_READ_MULTIPLIER = {
    'fable-5-1': 0.025,
    'fable': 0.1,
    'opus-5-5': 0.05,
    'opus': 0.1,
    'sonnet-5': 0.1,
    'sonnet': 0.1,
    }

# Cache-write price as a multiple of the base input price, by TTL. Every
# model shares these two.
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.0

# Claude Code writes its cache blocks at the 1-hour TTL. Both the Stop
# hook and the status line price a cycle with no TTL breakdown at this
# default rather than at the cheaper 5-minute one.
DEFAULT_ONE_HOUR = True

# Assistant calls per user turn, measured across 604 compaction cycles.
CALLS_PER_TURN = 8.8

# Turns a handoff write takes. Reading it back happens in a fresh
# session, so it is charged to that one, not to this.
HANDOFF_TURNS = 2

# Billed context on the first call after a compaction: the system prompt,
# tools, and instruction files all return, along with the summary.
# Measured median across 299 compactions.
POST_COMPACTION_TOKENS = 123_000

# Billed context on the first call of a brand new session: the same
# instruction and tool floor, with no summary and no preserved tail.
# Measured median across 325 sessions.
FRESH_SESSION_TOKENS = 69_000

# Used until a session has enough history to measure its own rate.
FALLBACK_GROWTH_PER_CALL = 1_900


@dataclass(frozen=True)
class Cycle:
    """The measurements of one cycle that its handoff point is priced from.

    A cycle runs from a session's first call, or from the first call
    after a compaction, to the handoff that ends it.

    Attributes
    ----------
    floor : int
        Billed context of the cycle's first call, F, in tokens.
    floor_written : int
        Cache-creation tokens of that first call, F_w.
    resume_context : int
        Billed context of the resume-end call j0, C0. Equals ``floor``
        when the cycle opened no handoff.
    resume_sum : int
        Billed context summed over the calls up to and including j0, S0.
    one_hour : bool
        True when the cycle's cache writes use the 1-hour TTL, False for
        the 5-minute one.
    """

    floor: int
    floor_written: int
    resume_context: int
    resume_sum: int
    one_hour: bool


def model_tier(model: str) -> str | None:
    """Map a model id onto its price tier.

    Parameters
    ----------
    model : str
        Model id, as recorded on a transcript message or a status line
        payload.

    Returns
    -------
    str or None
        One of the keys of ``CACHE_READ_PER_MTOK``, or None for a model this
        plugin has nothing worth saying about.
    """
    lowered = model.lower()
    for tier in CACHE_READ_PER_MTOK:
        if tier in lowered:
            return tier
    return None


def cost_per_turn(context: int, tier: str) -> float:
    """Dollars one user turn costs at a given context size.

    Parameters
    ----------
    context : int
        Billed context in tokens.
    tier : str
        Price tier from :func:`model_tier`.

    Returns
    -------
    float
        Cost of a single user turn, in dollars.
    """
    return (context / 1e6 * CACHE_READ_PER_MTOK[tier]
            * CALLS_PER_TURN)


def write_read_ratio(tier: str, one_hour: bool) -> float:
    """Price of writing a token to the cache over the price of reading it.

    Parameters
    ----------
    tier : str
        Price tier from :func:`model_tier`.
    one_hour : bool
        True for the 1-hour TTL, False for the 5-minute one.

    Returns
    -------
    float
        The ratio m, the same whatever the base input price.
    """
    write = CACHE_WRITE_MULTIPLIER_1H if one_hour else CACHE_WRITE_MULTIPLIER_5M
    return write / CACHE_READ_MULTIPLIER[tier]


def handoff_write_tokens(per_call: int) -> int:
    """Tokens a handoff write adds to the context, W.

    Parameters
    ----------
    per_call : int
        Estimated tokens added per assistant call, g.

    Returns
    -------
    int
        ``W = w * g``, where ``w = HANDOFF_TURNS * CALLS_PER_TURN`` is the
        write's length in calls, rounded to a token.
    """
    return round(HANDOFF_TURNS * CALLS_PER_TURN * per_call)


def handoff_point(cycle: Cycle, per_call: int, tier: str) -> int:
    """Context at which a handoff minimizes the cycle's cost per work call.

    Parameters
    ----------
    cycle : Cycle
        The current cycle's floor, resume, and cache TTL.
    per_call : int
        Estimated tokens added per assistant call, g.
    tier : str
        Price tier from :func:`model_tier`.

    Returns
    -------
    int
        The handoff point H* in billed tokens, rounded to a token.

    Notes
    -----
    - Costs are counted in read-token-calls: every call bills its whole
      context at the cache-read price, and every new token bills once
      more at m times that price for its cache write::

          w  = HANDOFF_TURNS * CALLS_PER_TURN
          W  = w * g
          m  = write_read_ratio(tier, cycle.one_hour)
          A  = S0 + w * (C0 + W/2) + m * (Fw + C0 - F + W)
          H* = C0 + sqrt(2 * g * A)

    - A is what a cycle pays whatever its length: the resume, the
      handoff write re-reading the context, and the cache writes of the
      floor, the resume, and the handoff. T work calls add a growth tax
      of ``g * T**2 / 2``, so the cost per work call is
      ``C0 + w*g + g*T/2 + A/T``, lowest at ``T* = sqrt(2 * A / g)``,
      where the context has reached H*.
    - The price itself cancels, so only the ratio m moves the point.
    - H* rises with C0: a longer resume earns a longer cycle to spread
      its cost over.
    - Output tokens are left out of A, which places the point early
      rather than late.
    """
    calls_to_write = HANDOFF_TURNS * CALLS_PER_TURN
    written = handoff_write_tokens(per_call)
    ratio = write_read_ratio(tier, cycle.one_hour)
    overhead = (cycle.resume_sum
                + calls_to_write * (cycle.resume_context + written / 2)
                + ratio * (cycle.floor_written + cycle.resume_context
                           - cycle.floor + written))
    return round(cycle.resume_context + math.sqrt(2 * per_call * overhead))
