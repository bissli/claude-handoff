"""Generate the README's SVG charts, a light and a dark variant of each.

Run from the repo root:

    python3 docs/charts.py

Writes eight files, ``docs/<chart>-{light,dark}.svg``. The README embeds
each pair behind a ``<picture>`` element so GitHub serves the variant
matching the reader's theme.

Notes
-----
- Cache-read prices and the calls-per-turn rate are imported from
  ``scripts/budget.py``, so a price change reaches the charts at the
  same moment it reaches the hooks.
- ``BASE_INPUT_PER_MTOK`` is the undiscounted input price, which the
  cache-discount chart needs and the runtime never does.
- Series colors (blue for Opus, orange for Fable, gray for full-price
  context lines) were validated for color-blind separation against
  GitHub's light (#ffffff) and dark (#0d1117) page surfaces.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import budget  # noqa: E402  (path must be set before this import resolves)

# Undiscounted input price per million tokens, for the full-price
# comparison the cache-discount chart draws.
BASE_INPUT_PER_MTOK = {
    'opus-5-5': 4.0,
    'fable-5-1': 10.0,
    }

FONT = 'system-ui, -apple-system, Segoe UI, sans-serif'
WIDTH = 800


@dataclass(frozen=True)
class Theme:
    """Color tokens for one README theme.

    Parameters
    ----------
    name : str
        File-name suffix, ``light`` or ``dark``.
    surface : str
        GitHub page background; used for the ring around markers and
        the gap between stacked segments.
    primary : str
        Ink for titles and values.
    secondary : str
        Ink for series labels and annotations.
    muted : str
        Ink for axis ticks, captions, and thresholds.
    grid : str
        Hairline gridline color.
    axis : str
        Baseline and axis rule color.
    blue : str
        Opus series color.
    orange : str
        Fable series color.
    wash : str
        Recessive fill for the re-sent history segments.
    gray : str
        De-emphasis series color for full-price context lines.
    """
    name: str
    surface: str
    primary: str
    secondary: str
    muted: str
    grid: str
    axis: str
    blue: str
    orange: str
    wash: str
    gray: str


LIGHT = Theme(
    name='light',
    surface='#ffffff',
    primary='#0b0b0b',
    secondary='#52514e',
    muted='#898781',
    grid='#e1e0d9',
    axis='#c3c2b7',
    blue='#2a78d6',
    orange='#eb6834',
    wash='#b7d3f6',
    gray='#898781',
    )
DARK = Theme(
    name='dark',
    surface='#0d1117',
    primary='#ffffff',
    secondary='#c3c2b7',
    muted='#898781',
    grid='#2c2c2a',
    axis='#383835',
    blue='#3987e5',
    orange='#d95926',
    wash='#1c5cab',
    gray='#898781',
    )


def dollars_per_turn(context_tokens: float, tier: str) -> float:
    """Cost of one user turn at cache-read prices.

    Parameters
    ----------
    context_tokens : float
        Billed context carried into the turn.
    tier : str
        Key of ``budget.CACHE_READ_PER_MTOK``.

    Returns
    -------
    float
        Dollars for one turn, at that tier's cache-read price.
    """
    return budget.cost_per_turn(context_tokens, tier)


def fmt(value: float) -> str:
    """Format a coordinate with at most one decimal place.
    """
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def text_el(x: float, y: float, s: str, fill: str, size: float = 11.0,
            anchor: str = 'start', weight: str = '') -> str:
    """One SVG text element.

    Parameters
    ----------
    x : float
        Anchor x in pixels.
    y : float
        Baseline y in pixels.
    s : str
        Text content, ASCII only.
    fill : str
        Ink color; always a text token, never a series color.
    size : float, default 11.0
        Font size in pixels.
    anchor : str, default 'start'
        SVG ``text-anchor`` value.
    weight : str, default ''
        Optional ``font-weight`` value, e.g. ``600``.

    Returns
    -------
    str
        Serialized ``<text>`` element.
    """
    style = f' font-weight="{weight}"' if weight else ''
    return (f'<text x="{fmt(x)}" y="{fmt(y)}" fill="{fill}" '
            f'font-size="{fmt(size)}" text-anchor="{anchor}"{style}>{s}</text>')


def line_el(x1: float, y1: float, x2: float, y2: float, stroke: str,
            width: float = 1.0, dash: str = '') -> str:
    """One straight SVG line.

    Parameters
    ----------
    x1, y1, x2, y2 : float
        Endpoints in pixels.
    stroke : str
        Line color.
    width : float, default 1.0
        Stroke width; 1.0 is the hairline used for grid and axes.
    dash : str, default ''
        Optional ``stroke-dasharray``, used only for threshold rules.

    Returns
    -------
    str
        Serialized ``<line>`` element.
    """
    dashed = f' stroke-dasharray="{dash}"' if dash else ''
    return (f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" '
            f'y2="{fmt(y2)}" stroke="{stroke}" '
            f'stroke-width="{fmt(width)}"{dashed}/>')


def series_el(points: list[tuple[float, float]], stroke: str) -> str:
    """One 2px data line with round caps and joins.

    Parameters
    ----------
    points : list of tuple of float
        Vertices in pixels.
    stroke : str
        Series color.

    Returns
    -------
    str
        Serialized ``<polyline>`` element.
    """
    coords = ' '.join(f'{fmt(x)},{fmt(y)}' for x, y in points)
    return (f'<polyline points="{coords}" fill="none" stroke="{stroke}" '
            f'stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round"/>')


def dot_el(x: float, y: float, fill: str, ring: str) -> str:
    """One 9px marker with a 2px surface ring.

    Parameters
    ----------
    x, y : float
        Center in pixels.
    fill : str
        Series color.
    ring : str
        Surface color, so the dot stays legible on its line.

    Returns
    -------
    str
        Serialized ``<circle>`` element.
    """
    return (f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="4.5" fill="{fill}" '
            f'stroke="{ring}" stroke-width="2"/>')


def column_el(x: float, y_top: float, w: float, h: float, fill: str,
              rounded: bool) -> str:
    """One vertical bar segment growing up from ``y_top + h``.

    Parameters
    ----------
    x : float
        Left edge in pixels.
    y_top : float
        Top edge in pixels.
    w : float
        Bar width; capped at 24px by the callers.
    h : float
        Segment height.
    fill : str
        Fill color.
    rounded : bool
        Round the top corners 4px when the segment is the data end.

    Returns
    -------
    str
        Serialized ``<path>`` or ``<rect>`` element.
    """
    if not rounded:
        return (f'<rect x="{fmt(x)}" y="{fmt(y_top)}" width="{fmt(w)}" '
                f'height="{fmt(h)}" fill="{fill}"/>')
    r = min(4.0, h / 2)
    return (f'<path d="M {fmt(x)} {fmt(y_top + h)} L {fmt(x)} {fmt(y_top + r)} '
            f'Q {fmt(x)} {fmt(y_top)} {fmt(x + r)} {fmt(y_top)} '
            f'L {fmt(x + w - r)} {fmt(y_top)} '
            f'Q {fmt(x + w)} {fmt(y_top)} {fmt(x + w)} {fmt(y_top + r)} '
            f'L {fmt(x + w)} {fmt(y_top + h)} Z" fill="{fill}"/>')


def hbar_el(x: float, y: float, w: float, h: float, fill: str,
            opacity: float = 1.0) -> str:
    """One horizontal bar, square at the baseline, rounded at the tip.

    Parameters
    ----------
    x : float
        Baseline edge in pixels.
    y : float
        Top edge in pixels.
    w : float
        Bar length.
    h : float
        Bar thickness; callers keep it under 24px.
    fill : str
        Fill color.
    opacity : float, default 1.0
        Fill opacity; de-emphasis bars use 0.55 so a long gray bar
        does not read as a heavy block.

    Returns
    -------
    str
        Serialized ``<path>`` element.
    """
    r = min(4.0, h / 2)
    faded = f' fill-opacity="{fmt(opacity)}"' if opacity < 1 else ''
    return (f'<path d="M {fmt(x)} {fmt(y)} L {fmt(x + w - r)} {fmt(y)} '
            f'Q {fmt(x + w)} {fmt(y)} {fmt(x + w)} {fmt(y + r)} '
            f'L {fmt(x + w)} {fmt(y + h - r)} '
            f'Q {fmt(x + w)} {fmt(y + h)} {fmt(x + w - r)} {fmt(y + h)} '
            f'L {fmt(x)} {fmt(y + h)} Z" fill="{fill}"{faded}/>')


def svg_doc(height: int, body: list[str]) -> str:
    """Wrap chart elements in a standalone SVG document.

    Parameters
    ----------
    height : int
        Document height; width is the module-wide ``WIDTH``.
    body : list of str
        Serialized elements, drawn in order.

    Returns
    -------
    str
        Complete SVG file content with a transparent background.
    """
    inner = '\n'.join(body)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{height}" viewBox="0 0 {WIDTH} {height}" '
            f'font-family="{FONT}">\n{inner}\n</svg>\n')


def chart_cost_per_turn(t: Theme) -> str:
    """The headline chart: dollars per turn against context, both models.
    """
    x0, x1, y0, y1 = 56.0, 620.0, 60.0, 316.0
    px_per_k = (x1 - x0) / 1000
    px_per_dollar = (y1 - y0) / 2.5

    def sx(tokens_k: float) -> float:
        return x0 + tokens_k * px_per_k

    def sy(dollars: float) -> float:
        return y1 - dollars * px_per_dollar

    body = [
        text_el(x0, 24, 'What one turn costs', t.primary, 13.5, weight='600'),
        text_el(x0, 42, 'at cache-read prices, about 9 API calls per turn',
                t.muted, 11.5),
        dot_el(560, 20, t.orange, t.surface),
        text_el(570, 24, 'Fable 5.1', t.secondary, 11.5),
        dot_el(640, 20, t.blue, t.surface),
        text_el(650, 24, 'Opus 5.5', t.secondary, 11.5),
        ]
    for dollars in (0.5, 1.0, 1.5, 2.0):
        body.extend((line_el(x0, sy(dollars), x1, sy(dollars), t.grid), text_el(x0 - 8, sy(dollars) + 4, f'${dollars:.2f}', t.muted, anchor='end')))
    body.append(line_el(x0, y1, x1, y1, t.axis))
    for tokens_k in (0, 200, 400, 600, 800, 1000):
        label = ('0' if tokens_k == 0
                 else '1M' if tokens_k == 1000 else f'{tokens_k}K')
        body.append(text_el(sx(tokens_k), y1 + 18, label, t.muted,
                            anchor='middle'))
    body.append(text_el((x0 + x1) / 2, y1 + 38, 'context (tokens)', t.muted,
                        anchor='middle'))

    for dollars, label in ((0.62, '$0.62 target'), (0.88, '$0.88 over budget')):
        body.extend((line_el(x0, sy(dollars), x1, sy(dollars), t.muted, 1.2, dash='5 4'), text_el(x0 + 4, sy(dollars) - 6, label, t.muted, 10.5)))

    body.extend((series_el([(sx(0), sy(0)), (sx(1000), sy(2.2))], t.orange), series_el([(sx(0), sy(0)), (sx(1000), sy(1.76))], t.blue)))

    crossings = (
        (282, 0.62, t.orange, '282K'),
        (400, 0.88, t.orange, '400K'),
        (352, 0.62, t.blue, '352K'),
        (500, 0.88, t.blue, ''),
        )
    for tokens_k, dollars, color, label in crossings:
        body.append(dot_el(sx(tokens_k), sy(dollars), color, t.surface))
        if label:
            body.append(text_el(sx(tokens_k), sy(dollars) + 18, label, t.muted,
                                10.5, anchor='middle'))

    body.extend((text_el(x1 + 12, sy(2.2) + 4, 'Fable 5.1', t.secondary, 11.5), text_el(x1 + 70, sy(2.2) + 4, '$2.20', t.primary, 12, weight='600'), text_el(x1 + 12, sy(1.76) + 4, 'Opus 5.5', t.secondary, 11.5), text_el(x1 + 70, sy(1.76) + 4, '$1.76', t.primary, 12, weight='600')))
    return svg_doc(360, body)


def chart_cache_discount(t: Theme) -> str:
    """Small multiples: full-price vs cached re-send cost per model.
    """
    y0, y1 = 72.0, 300.0
    px_per_dollar = (y1 - y0) / 45
    panels = (
        ('Opus 5.5  ($4 / M input)', 56.0, 330.0, 'opus-5-5', t.blue),
        ('Fable 5.1  ($10 / M input)', 450.0, 724.0, 'fable-5-1', t.orange),
        )

    def sy(dollars: float) -> float:
        return y1 - dollars * px_per_dollar

    body = [
        text_el(56, 24, 'The cache discount', t.primary, 13.5, weight='600'),
        text_el(56, 42,
                'one turn, re-sending history at full price vs through the '
                'cache', t.muted, 11.5),
        ]
    for title, px0, px1, tier, color in panels:
        px_per_k = (px1 - px0) / 500

        def sx(tokens_k: float, panel_x0: float = px0,
               scale: float = px_per_k) -> float:
            return panel_x0 + tokens_k * scale

        body.append(text_el(px0, 62, title, t.primary, 12, weight='600'))
        body.extend(line_el(px0, sy(dollars), px1, sy(dollars), t.grid) for dollars in (10, 20, 30, 40))
        body.append(line_el(px0, y1, px1, y1, t.axis))
        for tokens_k in (0, 250, 500):
            label = '0' if tokens_k == 0 else f'{tokens_k}K'
            body.append(text_el(sx(tokens_k), y1 + 18, label, t.muted,
                                anchor='middle'))

        full = (500_000 / 1e6 * BASE_INPUT_PER_MTOK[tier]
                * budget.CALLS_PER_TURN)
        cached = dollars_per_turn(500_000, tier)
        body.extend((series_el([(sx(0), sy(0)), (sx(500), sy(full))], t.gray), series_el([(sx(0), sy(0)), (sx(500), sy(cached))], color), dot_el(sx(500), sy(full), t.gray, t.surface), dot_el(sx(500), sy(cached), color, t.surface), text_el(px1 + 10, sy(full) - 2, 'full price', t.secondary, 11), text_el(px1 + 10, sy(full) + 12, f'${full:.2f}', t.primary, 12, weight='600'), text_el(px1 + 10, sy(cached) - 2, 'cached', t.secondary, 11), text_el(px1 + 10, sy(cached) + 12, f'${cached:.2f}', t.primary, 12, weight='600')))
    body.extend(text_el(48, sy(dollars) + 4, f'${dollars}', t.muted,
                        anchor='end') for dollars in (10, 20, 30, 40))
    body.append(text_el(400, y1 + 38, 'context (tokens)', t.muted,
                        anchor='middle'))
    return svg_doc(340, body)


def chart_resend(t: Theme) -> str:
    """The mechanism: each turn re-sends everything before it.
    """
    x0, x1, y0, y1 = 56.0, 620.0, 56.0, 248.0
    px_per_k = (y1 - y0) / 300
    slot = (x1 - x0) / 12
    floor_k, growth_k = 69, 17

    body = [
        text_el(x0, 24, 'Every request re-sends the whole conversation',
                t.primary, 13.5, weight='600'),
        text_el(x0, 42, 'tokens sent to the API, turn by turn', t.muted, 11.5),
        ]
    for tokens_k in (100, 200, 300):
        y = y1 - tokens_k * px_per_k
        body.extend((line_el(x0, y, x1, y, t.grid), text_el(x0 - 8, y + 4, f'{tokens_k}K', t.muted, anchor='end')))
    body.append(line_el(x0, y1, x1, y1, t.axis))

    last_cap_y = last_body_y = 0.0
    for turn in range(1, 13):
        x = x0 + slot * (turn - 0.5) - 12
        history_h = (floor_k + growth_k * (turn - 1)) * px_per_k
        cap_h = growth_k * px_per_k
        cap_y = y1 - history_h - 2 - cap_h
        body.extend((column_el(x, y1 - history_h, 24, history_h, t.wash, False), column_el(x, cap_y, 24, cap_h, t.blue, True), text_el(x + 12, y1 + 18, str(turn), t.muted, 10.5, anchor='middle')))
        last_cap_y = cap_y + cap_h / 2
        last_body_y = y1 - history_h / 2
    body.extend((text_el((x0 + x1) / 2, y1 + 38, 'turns', t.muted, anchor='middle'), line_el(x1 + 10, last_cap_y, x1 + 26, last_cap_y, t.axis), text_el(x1 + 32, last_cap_y + 4, 'what this turn adds', t.secondary, 11.5), line_el(x1 + 10, last_body_y, x1 + 26, last_body_y, t.axis), text_el(x1 + 32, last_body_y - 3, 'everything before it,', t.secondary, 11.5), text_el(x1 + 32, last_body_y + 11, 're-sent every time', t.secondary, 11.5)))
    return svg_doc(300, body)


def session_costs(tier: str, target_k: float | None) -> list[float]:
    """Cumulative cost of a simulated 40-turn session.

    Parameters
    ----------
    tier : str
        Key of ``budget.CACHE_READ_PER_MTOK``.
    target_k : float or None
        Hand off when context reaches this many thousand tokens; None
        never hands off.

    Returns
    -------
    list of float
        Cumulative dollars after each turn.

    Notes
    -----
    - The session starts at the 69K fresh-session floor, grows 17K a
      turn, and restarts at 72K (floor plus handoff file) after a
      handoff.
    """
    context_k = 69.0
    totals: list[float] = []
    total = 0.0
    for _ in range(40):
        total += dollars_per_turn(context_k * 1000, tier)
        totals.append(total)
        context_k += 17
        if target_k is not None and context_k >= target_k:
            context_k = 72.0
    return totals


def chart_session_cost(t: Theme) -> str:
    """Small multiples: cumulative session cost, held vs run on.
    """
    y0, y1 = 72.0, 300.0
    px_per_dollar = (y1 - y0) / 40
    panels = (
        ('Opus 5.5  (hand off at 352K)', 56.0, 330.0, 'opus-5-5', 352.3,
         t.blue),
        ('Fable 5.1  (hand off at 282K)', 450.0, 724.0, 'fable-5-1', 281.8,
         t.orange),
        )

    def sy(dollars: float) -> float:
        return y1 - dollars * px_per_dollar

    body = [
        text_el(56, 24, 'A 40-turn session, both ways', t.primary, 13.5,
                weight='600'),
        text_el(56, 42,
                'cumulative cost growing 17K a turn: run on, or hand off at '
                'the target', t.muted, 11.5),
        ]
    for title, px0, px1, tier, target_k, color in panels:
        px_per_turn = (px1 - px0) / 40

        def sx(turn: float, panel_x0: float = px0,
               scale: float = px_per_turn) -> float:
            return panel_x0 + turn * scale

        body.append(text_el(px0, 62, title, t.primary, 12, weight='600'))
        body.extend(line_el(px0, sy(dollars), px1, sy(dollars), t.grid) for dollars in (10, 20, 30, 40))
        body.append(line_el(px0, y1, px1, y1, t.axis))
        body.extend(text_el(sx(turn), y1 + 18, str(turn), t.muted,
                            anchor='middle') for turn in (0, 10, 20, 30, 40))

        run_on = session_costs(tier, None)
        held = session_costs(tier, target_k)
        for series, color_used in ((run_on, t.gray), (held, color)):
            points = [(sx(0), sy(0.0))]
            points += [(sx(i + 1), sy(v)) for i, v in enumerate(series)]
            body.append(series_el(points, color_used))
        body.extend((dot_el(sx(40), sy(run_on[-1]), t.gray, t.surface), dot_el(sx(40), sy(held[-1]), color, t.surface), text_el(px1 + 10, sy(run_on[-1]) - 2, 'run on', t.secondary, 11), text_el(px1 + 10, sy(run_on[-1]) + 12, f'${run_on[-1]:.0f}', t.primary, 12, weight='600'), text_el(px1 + 10, sy(held[-1]) - 2, 'hand off', t.secondary, 11), text_el(px1 + 10, sy(held[-1]) + 12, f'${held[-1]:.0f}', t.primary, 12, weight='600')))
    body.extend(text_el(48, sy(dollars) + 4, f'${dollars}', t.muted,
                        anchor='end') for dollars in (10, 20, 30, 40))
    body.append(text_el(400, y1 + 38, 'turns', t.muted, anchor='middle'))
    return svg_doc(340, body)


def chart_restarts(t: Theme) -> str:
    """Where each exit lands: billed context on the first call after.
    """
    label_x, bar_x = 162.0, 170.0
    px_per_k = 520 / 123
    rows = (
        ('new session', 69, t.gray),
        ('handoff restart', 72, t.blue),
        ('compact restart', 123, t.gray),
        )

    body = [
        text_el(56, 24, 'Where each exit restarts you', t.primary, 13.5,
                weight='600'),
        text_el(56, 42, 'billed context on the first call after', t.muted,
                11.5),
        ]
    y = 60.0
    for label, tokens_k, color in rows:
        w = tokens_k * px_per_k
        body.extend((text_el(label_x, y + 14, label, t.secondary, 12, anchor='end'), hbar_el(bar_x, y, w, 20, color, opacity=0.55 if color == t.gray else 1.0), text_el(bar_x + w + 8, y + 14, f'{tokens_k}K', t.primary, 12, weight='600')))
        y += 38
    body.append(line_el(bar_x, 56, bar_x, y - 14, t.axis))
    return svg_doc(180, body)


CHARTS = {
    'cost-per-turn': chart_cost_per_turn,
    'cache-discount': chart_cache_discount,
    'resend': chart_resend,
    'session-cost': chart_session_cost,
    'restarts': chart_restarts,
    }

if __name__ == '__main__':
    out_dir = Path(__file__).resolve().parent
    for theme in (LIGHT, DARK):
        for name, build in CHARTS.items():
            path = out_dir / f'{name}-{theme.name}.svg'
            path.write_text(build(theme))
            print(path.name)
