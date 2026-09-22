"""Shared budget arithmetic for the Stop hook and the status line.

The budget is set in dollars per user turn, not in tokens. Each model's
token target is derived from it and from that model's price, which is the
only way the targets stay honest: a model billing at twice the rate
reaches the same cost per turn at half the context, and should be
compacted there. One knob therefore moves every model at once.

Notes
-----
- Cost per turn is ``context * cache_read_per_mtok * calls_per_turn``.
  A turn deep in a session is almost entirely cache reads, so this is
  the whole bill to within a few percent.
- Only the expensive models are listed. A long Sonnet or Haiku session
  costs little enough that interrupting one to talk about money would
  spend more attention than it saves, so they are left alone.
"""

# Dollars per user turn. Everything else follows from this.
COST_PER_TURN_TARGET = 0.62
COST_PER_TURN_LIMIT = 0.88

# Notes:
# - Keys match as substrings of the model id, most specific first, so
#   a generation priced on its own is found before its family.
# - The cache-read price is stored, not the base price and the
#   multiplier against it, because that multiplier varies by model:
#   0.05 on Opus 5.5 and 0.025 on Fable 5.1 against 0.1 elsewhere.
CACHE_READ_PER_MTOK = {
    'fable-5-1': 0.25,
    'fable': 1.00,
    'opus-5-5': 0.20,
    'opus': 0.50,
    }

# Assistant calls per user turn, measured across 604 compaction cycles.
CALLS_PER_TURN = 8.8

# Turns the handoff costs this session. A handoff write measures 17.5
# assistant calls, which is two turns at the rate above. Reading it back
# happens in a fresh session, so it is charged to that one, not to this.
HANDOFF_TURNS = 2

# Growth is a recent average, so one heavy turn during the handoff can
# outrun it. This buys that slack.
HANDOFF_MARGIN = 1.5

# The warning is useless if it fires on a young session and useless if it
# leaves no room, so the reserve is held between these two bounds. The
# ceiling is a quarter, which is what puts the earliest possible warning
# at three quarters of the gauge: a fast session used to reserve half
# the budget and be told to hand off beside a half-filled bar.
MIN_RESERVE_TOKENS = 60_000
MAX_RESERVE_FRACTION = 0.25

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

# Turns a compaction cycle has to be worth: HANDOFF_TURNS to get out of
# the session, and three more to do something with the room that buys
# back.
MIN_CYCLE_TURNS = HANDOFF_TURNS + 3


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


def tokens_for_cost(budget: float, tier: str) -> int:
    """Context size at which a turn costs a given number of dollars.

    Parameters
    ----------
    budget : float
        Dollars per user turn.
    tier : str
        Price tier from :func:`model_tier`.

    Returns
    -------
    int
        Billed context in tokens.

    Notes
    -----
    - Rounded, not truncated. The division lands a hair under a round
      number often enough that truncating shifts a threshold down by one
      token, which is invisible in use and maddening in a test.
    """
    per_token = CACHE_READ_PER_MTOK[tier] * CALLS_PER_TURN / 1e6
    return round(budget / per_token)


def cycle_floor_tokens(per_call: int) -> int:
    """Lowest target a repeatedly-compacted session can actually work to.

    Parameters
    ----------
    per_call : int
        Estimated tokens added per assistant call.

    Returns
    -------
    int
        Context size that leaves ``MIN_CYCLE_TURNS`` of room above where a
        compaction lands.
    """
    return POST_COMPACTION_TOKENS + int(MIN_CYCLE_TURNS * per_call
                                        * CALLS_PER_TURN)


def target_tokens(tier: str, per_call: int = FALLBACK_GROWTH_PER_CALL) -> int:
    """Context size a session should be compacted at.

    Parameters
    ----------
    tier : str
        Price tier from :func:`model_tier`.
    per_call : int, default FALLBACK_GROWTH_PER_CALL
        Estimated tokens added per assistant call.

    Returns
    -------
    int
        Billed context in tokens.

    Notes
    -----
    - The larger of what cost wants and what the compaction cycle allows.
      Cost alone puts an expensive model's target near where a compaction
      lands, and a target below that point is not a target at all: the
      session re-enters it the moment it restarts, so the warning fires
      every turn and means nothing.
    - Raising the target above cost parity is a real admission. On an
      expensive model a session you must keep compacting simply costs
      more per turn than a cheap one, and the honest move is to say by
      how much rather than to set a threshold nobody can hold.
    - This is what the growth rate justifies right now, not what the
      session is held to. The rate is re-measured every turn and swings
      by a factor of three, so a caller tracking one session latches
      this figure downward rather than following it up.
    """
    return max(tokens_for_cost(COST_PER_TURN_TARGET, tier),
               cycle_floor_tokens(per_call))


def over_budget_tokens(tier: str, target: int) -> int:
    """Context size past which a turn is plainly overpriced.

    Parameters
    ----------
    tier : str
        Price tier from :func:`model_tier`.
    target : int
        The compaction target in force for this session, in tokens.

    Returns
    -------
    int
        Billed context in tokens.

    Notes
    -----
    - The boundary sits where a turn costs COST_PER_TURN_LIMIT, or at
      the target when the cycle floor has pushed that past the limit.
    - Never below the target it is handed, so the over band cannot fire
      before the budget band.
    """
    # Notes:
    # - The limit is a dollar figure, so the boundary comes from price
    #   alone - scaling the target instead lets the cycle floor push
    #   "overpriced" to $5 a turn on a fast-growing session.
    # - Taking the target rather than a growth rate is what lets a
    #   caller pass the latched one. Deriving it here from the live
    #   rate would move the boundary under a target that no longer
    #   moves.
    return max(tokens_for_cost(COST_PER_TURN_LIMIT, tier), target)


def reserve_tokens(target: int, per_call: int) -> int:
    """Room to hold back so a handoff still fits before the target.

    Parameters
    ----------
    target : int
        The compaction target for this model, in tokens.
    per_call : int
        Estimated tokens added per assistant call.

    Returns
    -------
    int
        Tokens reserved below the target, bounded by
        ``MIN_RESERVE_TOKENS`` and ``MAX_RESERVE_FRACTION``.

    Notes
    -----
    - ``MAX_RESERVE_FRACTION`` is the guarantee that the warning cannot
      arrive before ``1 - MAX_RESERVE_FRACTION`` of the budget is spent,
      however fast the session grows. Without it the reserve saturates
      on any session reading large files, and every one of them is told
      to hand off at the same point regardless of its rate.
    - On a small target that ceiling falls below the floor, and the
      floor is what wins: a fraction of a small budget is not enough
      room to write anything, and a bound meant to keep the warning
      legible must not quietly delete the bound meant to keep it
      useful.
    """
    raw = int(HANDOFF_TURNS * per_call * CALLS_PER_TURN * HANDOFF_MARGIN)
    ceiling = max(int(target * MAX_RESERVE_FRACTION), MIN_RESERVE_TOKENS)
    reserve = min(ceiling, max(MIN_RESERVE_TOKENS, raw))
    # A reserve deep enough to put the warning below where a compaction
    # lands would fire on the first turn of every cycle, forever.
    return min(reserve, max(0, target - POST_COMPACTION_TOKENS))
