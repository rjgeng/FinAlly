"""
FinAlly — Market Data Live Terminal Demo
=========================================
Runs the SimulatorProvider directly — no server, no Docker required.
Displays a live-updating price table with directional indicators, session
change %, and an event log that captures notable moves (≥1.5%).

Usage (from the repo root):
    python backend/market_data_demo.py

Press Ctrl+C to exit cleanly.
"""

import asyncio
import os
import sys
import time
from datetime import datetime

# Allow running as a script from the repo root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.market.simulator import SimulatorProvider  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]
REFRESH_INTERVAL = 0.5          # seconds — matches simulator tick rate
EVENT_LOG_LINES  = 8            # how many events to show
JUMP_THRESHOLD   = 0.015        # flag moves >= 1.5% as notable events

# ---------------------------------------------------------------------------
# ANSI primitives  (no external dependencies)
# ---------------------------------------------------------------------------

RESET     = "\033[0m"
BOLD      = "\033[1m"
DIM       = "\033[2m"
GREEN     = "\033[92m"
RED       = "\033[91m"
YELLOW    = "\033[93m"
CYAN      = "\033[96m"
WHITE     = "\033[97m"
BG_GREEN  = "\033[42m"
BG_RED    = "\033[41m"

HOME      = "\033[H"
CLEAR     = "\033[2J\033[H"
ERASE_EOL = "\033[K"
HIDE_CUR  = "\033[?25l"
SHOW_CUR  = "\033[?25h"


def c(*codes: str) -> str:
    """Compose ANSI codes into a single prefix (does not reset — caller manages)."""
    return "".join(codes)


def styled(text: str, *codes: str) -> str:
    return "".join(codes) + text + RESET


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_price(p: float) -> str:
    """Right-aligned 9-char price string."""
    return f"{p:>9,.2f}"


def fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def direction_glyph(direction: str) -> str:
    if direction == "up":
        return styled(" ▲ ", GREEN, BOLD)
    if direction == "down":
        return styled(" ▼ ", RED, BOLD)
    return styled(" ─ ", DIM)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_COL_W = 52   # inner content width (no surrounding margin)

def _sep(char: str = "─") -> str:
    return styled("  " + char * _COL_W, DIM)


def render_header(tick: int, uptime: float) -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    title  = styled("  FinAlly  Market Data Demo", BOLD, YELLOW)
    badge  = styled(" ● LIVE ", GREEN, BOLD)
    detail = styled(f"  ticks={tick}  uptime={uptime:.0f}s  {ts}", DIM)
    return f"\n{title} {badge}\n{detail}\n"


def render_table_header() -> str:
    hdr = styled(
        f"  {'TICKER':<6}  {'PRICE':>9}  {'PREV CLOSE':>10}  {'SESSION':>8}  DIR",
        BOLD,
    )
    return f"\n{hdr}\n{_sep()}"


def render_row(ticker: str, snap, flash: str | None) -> str:
    price     = snap.price
    prev_cl   = snap.prev_close
    pct       = (price - prev_cl) / prev_cl * 100 if prev_cl else 0.0

    # Price cell — flash background on tick of change, colour otherwise
    p_str = fmt_price(price)
    if flash == "up":
        p_str = styled(p_str, BG_GREEN, BOLD)
    elif flash == "down":
        p_str = styled(p_str, BG_RED, BOLD)
    elif snap.direction == "up":
        p_str = styled(p_str, GREEN)
    elif snap.direction == "down":
        p_str = styled(p_str, RED)
    else:
        p_str = styled(p_str, WHITE)

    chg_color = GREEN if pct >= 0 else RED
    chg_str   = styled(f"{fmt_pct(pct):>8}", chg_color)
    prev_str  = styled(f"{fmt_price(prev_cl):>10}", DIM)
    tick_str  = styled(f"{ticker:<6}", BOLD, CYAN)

    return f"  {tick_str}  {p_str}  {prev_str}  {chg_str}  {direction_glyph(snap.direction)}"


def render_event_log(events: list[str]) -> str:
    title = styled("  EVENT LOG  (moves ≥ 1.5%)", BOLD, DIM)
    lines = [f"\n{_sep()}", title]
    visible = events[-EVENT_LOG_LINES:] if events else []
    for e in visible:
        lines.append(e)
    if not visible:
        lines.append(styled("  — no notable moves yet —", DIM))
    # Pad to keep layout stable
    for _ in range(EVENT_LOG_LINES - len(visible)):
        lines.append("")
    return "\n".join(lines)


def render_footer() -> str:
    return styled(f"\n  {_sep('·')}\n  Ctrl+C to exit\n", DIM)


# ---------------------------------------------------------------------------
# Main async loop
# ---------------------------------------------------------------------------

async def run_demo() -> None:
    provider = SimulatorProvider(get_watchlist=lambda: TICKERS)
    await provider.start()

    events:       list[str]            = []
    flash_map:    dict[str, str | None] = {t: None for t in TICKERS}
    prev_prices:  dict[str, float]      = {}
    tick:         int                   = 0
    start_time:   float                 = time.time()

    # Set up terminal
    sys.stdout.write(HIDE_CUR + CLEAR)
    sys.stdout.flush()

    try:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            tick += 1
            uptime     = time.time() - start_time
            all_prices = provider.get_all_prices()

            # Detect price changes, record flashes and notable events
            for t, snap in all_prices.items():
                prev = prev_prices.get(t)
                if prev is not None and prev != snap.price:
                    flash_map[t] = snap.direction
                    move_pct     = (snap.price - prev) / prev
                    if abs(move_pct) >= JUMP_THRESHOLD:
                        glyph = "▲" if move_pct > 0 else "▼"
                        col   = GREEN if move_pct > 0 else RED
                        ts    = datetime.now().strftime("%H:%M:%S")
                        events.append(
                            styled(
                                f"  [{ts}] {t:<6} {glyph} {snap.price:>9.2f}   "
                                f"({move_pct*100:+.2f}%)",
                                col,
                            )
                        )
                else:
                    flash_map[t] = None
                prev_prices[t] = snap.price

            # Compose the full frame
            frame_lines: list[str] = [
                render_header(tick, uptime),
                render_table_header(),
            ]
            for t in TICKERS:
                snap = all_prices.get(t)
                if snap:
                    frame_lines.append(render_row(t, snap, flash_map.get(t)))
            frame_lines.append(render_event_log(events))
            frame_lines.append(render_footer())

            # Atomic redraw: jump home and overwrite
            sys.stdout.write(HOME + "\n".join(frame_lines))
            sys.stdout.flush()

            # Flashes last exactly one frame
            for t in flash_map:
                flash_map[t] = None

    except asyncio.CancelledError:
        pass
    finally:
        await provider.stop()
        sys.stdout.write(SHOW_CUR + "\n\n")
        sys.stdout.flush()


def main() -> None:
    sys.stdout.write(CLEAR)
    sys.stdout.flush()
    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        pass
    print(styled("Demo stopped.", DIM))


if __name__ == "__main__":
    main()
