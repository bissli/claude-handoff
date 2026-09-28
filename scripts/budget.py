"""Shared budget arithmetic for the Stop hook and the status line.

The budget is a cost rule. A session should hand off where the cost per
call of work is lowest: staying longer re-reads a growing context on
every call, and leaving pays a cycle's fixed overhead again. The point
between the two follows from the cycle's own measurements and from
ratios between the tier's prices, never from a dollar figure.

Notes
-----
- Haiku is not listed: its 200K window holds a session well under any
  point worth announcing, so a warning would spend more attention than
  it saves.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Prices:
    """Published prices of one tier, in dollars per million tokens.

    Attributes
    ----------
    base_input : float
        Uncached input price, the one both cache-write multipliers scale.
    cache_read : float
        Cache-read price.
    output : float
        Output price, thinking tokens included.
    """

    base_input: float
    cache_read: float
    output: float


# Notes:
# - Keys match as substrings of the model id, most specific first, so
#   a generation priced on its own is found before its family.
# - All three prices are stored because the cache-read discount against
#   the base input price varies by model.
PRICES_PER_MTOK = {
    'fable-5-1': Prices(base_input=10.00, cache_read=0.25, output=50.00),
    'fable': Prices(base_input=10.00, cache_read=1.00, output=50.00),
    'opus-5-5': Prices(base_input=4.00, cache_read=0.20, output=20.00),
    'opus': Prices(base_input=5.00, cache_read=0.50, output=25.00),
    'sonnet-5': Prices(base_input=2.00, cache_read=0.20, output=10.00),
    'sonnet': Prices(base_input=3.00, cache_read=0.30, output=15.00),
    }

# Cache-write price as a multiple of the base input price, by TTL. Every
# model shares these two.
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.0

# Claude Code writes its cache blocks at the 1-hour TTL. Both the Stop
# hook and the status line price a cycle with no TTL breakdown at this
# default rather than at the cheaper 5-minute one.
DEFAULT_ONE_HOUR = True

# Assistant calls per user turn, measured across many restart-in-place
# cycles.
CALLS_PER_TURN = 8.8

# Turns a handoff write takes. Reading it back happens in a fresh
# session, so it is charged to that one, not to this.
HANDOFF_TURNS = 2

# Context a handoff write adds. The write prints the handoff rules and
# writes one document, and neither grows with the work before it, so
# this holds whatever the session's growth rate.
HANDOFF_WRITE_TOKENS = 29_000

# Output tokens a handoff write bills, thinking included.
HANDOFF_OUTPUT_TOKENS = 20_000

# Context at which Claude Code compacts a session on its own, on the
# 1M window every tier in PRICES_PER_MTOK has: the window less a
# 20,000-token output reserve and a 13,000-token buffer. A handoff
# point closer to it than one handoff write leaves no room to finish
# that write.
AUTO_COMPACT_TOKENS = 967_000

# Billed context on the first call after a restart in place: the system
# prompt, tools, and instruction files all return, along with whatever
# the platform's own reset carries forward.
RESTART_IN_PLACE_TOKENS = 123_000

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
    after a restart in place, to the handoff that ends it.

    Attributes
    ----------
    floor : int
        Billed context of the cycle's first call, F, in tokens.
    floor_written : int
        Cache-creation tokens of that first call, F_w.
    resume_context : int
        Billed context of the resume-end call j0, C0, less the growth of
        any work the cycle did before its resume began. Equals ``floor``
        when the cycle opened no handoff.
    resume_sum : int
        Billed context summed over the resume's calls up to and including
        j0, S0, each less that same growth, so the resume's first call
        counts as the floor.
    resume_output : int
        Output tokens billed by those same calls, O_r, thinking included.
    one_hour : bool
        True when the cycle's cache writes use the 1-hour TTL, False for
        the 5-minute one.
    """

    floor: int
    floor_written: int
    resume_context: int
    resume_sum: int
    resume_output: int
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
        One of the keys of ``PRICES_PER_MTOK``, or None for a model this
        plugin has nothing worth saying about.
    """
    lowered = model.lower()
    for tier in PRICES_PER_MTOK:
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
    return (context / 1e6 * PRICES_PER_MTOK[tier].cache_read
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
        The ratio m.
    """
    prices = PRICES_PER_MTOK[tier]
    write = CACHE_WRITE_MULTIPLIER_1H if one_hour else CACHE_WRITE_MULTIPLIER_5M
    return write * prices.base_input / prices.cache_read


def handoff_point(cycle: Cycle, per_call: int, tier: str) -> int:
    """Context at which a handoff minimizes the cost per call of work.

    Parameters
    ----------
    cycle : Cycle
        The current cycle's floor, resume, and cache TTL, standing in for
        the next cycle's.
    per_call : int
        Estimated tokens added per assistant call, g.
    tier : str
        Price tier from :func:`model_tier`.

    Returns
    -------
    int
        The handoff point H* in billed tokens, rounded to a token, and
        never past ``AUTO_COMPACT_TOKENS - HANDOFF_WRITE_TOKENS``.

    Notes
    -----
    - Costs are counted in read-token-calls. Every call bills its whole
      context at the cache-read price. A token written to the cache
      bills m - 1 more, on top of the read its own call already counts,
      and an output token bills r::

          w  = HANDOFF_TURNS * CALLS_PER_TURN
          W  = HANDOFF_WRITE_TOKENS
          Ow = HANDOFF_OUTPUT_TOKENS
          m  = write_read_ratio(tier, cycle.one_hour)
          r  = output price / cache-read price
          A  = S0 + w * (C0 + W/2) + (m - 1) * (Fw + C0 - F + W)
               + r * (Or + Ow)
          H* = min(C0 + sqrt(2 * g * A), AUTO_COMPACT_TOKENS - W)

    - A is what a cycle pays whatever its length: the resume, the
      handoff write re-reading the context, the cache writes of the
      floor, the resume, and the write, and the output of the resume and
      the write. T work calls add a growth tax of ``g * T**2 / 2``, so
      the cost per work call is ``K + g*T/2 + A/T``, with K free of T,
      lowest at ``T* = sqrt(2 * A / g)``, where the context has reached
      H*.
    - At H* the next work call costs as much as a work call averages
      over a fresh cycle, so A belongs to the next cycle, and this
      cycle's measurements stand in for it.
    - A does not depend on g, so the room past the resume grows as the
      square root of g and the count of work calls falls as one over it.
    - H* rises with C0: a longer resume earns a longer cycle to spread
      its cost over.
    - The dollar price cancels, so only the ratios m and r move the point.
    - The cap lets a write begun at H* finish before Claude Code compacts
      the session on its own.
    """
    calls_to_write = HANDOFF_TURNS * CALLS_PER_TURN
    prices = PRICES_PER_MTOK[tier]
    write_ratio = write_read_ratio(tier, cycle.one_hour)
    output_ratio = prices.output / prices.cache_read
    overhead = (cycle.resume_sum
                + calls_to_write * (cycle.resume_context
                                    + HANDOFF_WRITE_TOKENS / 2)
                + (write_ratio - 1) * (cycle.floor_written
                                       + cycle.resume_context - cycle.floor
                                       + HANDOFF_WRITE_TOKENS)
                + output_ratio * (cycle.resume_output
                                  + HANDOFF_OUTPUT_TOKENS))
    point = cycle.resume_context + math.sqrt(2 * per_call * overhead)
    return round(min(point, AUTO_COMPACT_TOKENS - HANDOFF_WRITE_TOKENS))
