"""Fetch live prices, recompute portfolio stats, and render index.html.

Reads portfolio.json, pulls current + last-Friday closes via yfinance for every
held ticker plus the benchmark, then renders template.html.j2 into index.html.

Idempotent: if no underlying number changes, the timestamp is left alone and
the file is not rewritten — so the GitHub Action will skip the commit.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = ROOT / "portfolio.json"
TEMPLATE_DIR = ROOT
TEMPLATE_NAME = "template.html.j2"
OUTPUT_PATH = ROOT / "index.html"

PACIFIC = ZoneInfo("America/Los_Angeles")
TIMESTAMP_PLACEHOLDER = "__TIMESTAMP_PLACEHOLDER__"


@dataclass
class PriceSnapshot:
    current: float
    prev_close: float | None
    last_friday_close: float | None


def last_friday(today: date) -> date:
    """Return the date of the most recent Friday strictly before today."""
    days_back = (today.weekday() - 4) % 7
    if days_back == 0:
        days_back = 7
    return today - timedelta(days=days_back)


def fetch_prices(tickers: list[str], today: date) -> dict[str, PriceSnapshot]:
    """Fetch current + previous-close + last-Friday-close for each ticker.

    Uses a single yf.download spanning ~10 calendar days back, which covers
    both the previous trading day and the most recent Friday even across
    holidays. Falls back gracefully if a value is missing.
    """
    start = today - timedelta(days=14)
    end = today + timedelta(days=1)
    data = yf.download(
        tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        progress=False,
        auto_adjust=False,
        group_by="ticker",
        threads=True,
    )
    if data is None or data.empty:
        raise RuntimeError("yfinance returned no data")

    target_friday = last_friday(today)
    out: dict[str, PriceSnapshot] = {}
    for t in tickers:
        if len(tickers) == 1:
            closes = data["Close"]
        else:
            closes = data[t]["Close"]
        closes = closes.dropna()
        if closes.empty:
            raise RuntimeError(f"No close data for {t}")
        current = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else None

        friday_match = [d for d in closes.index if d.date() <= target_friday]
        last_fri_close = float(closes.loc[friday_match[-1]]) if friday_match else None

        out[t] = PriceSnapshot(
            current=current,
            prev_close=prev_close,
            last_friday_close=last_fri_close,
        )
    return out


def format_shares(shares: float) -> str:
    """Trim trailing zeros, keep up to 4 decimals — matches the original page."""
    s = f"{shares:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def signed(value: float) -> tuple[str, float, str]:
    """Return (sign_str, absolute_value, css_class) for a signed number."""
    if value >= 0:
        return "+", abs(value), "pos"
    return "-", abs(value), "neg"


def render(portfolio: dict, prices: dict[str, PriceSnapshot], now: datetime) -> str:
    starting_capital = float(portfolio["starting_capital"])
    start_date = portfolio["start_date"]
    benchmark_ticker = portfolio["benchmark_ticker"]
    buys = portfolio["buys"]

    holdings = []
    total_value = 0.0
    total_cost = 0.0
    total_week_baseline = 0.0
    has_week_data = True

    start_dt = date.fromisoformat(start_date)
    target_friday = last_friday(now.date())

    for b in buys:
        snap = prices[b["ticker"]]
        shares = float(b["shares"])
        avg_cost = float(b["avg_cost"])
        buy_dt = date.fromisoformat(b["buy_date"])

        market_value = shares * snap.current
        cost_basis = shares * avg_cost
        pl_pct = (snap.current / avg_cost - 1.0) * 100.0
        sign, abs_pct, klass = signed(pl_pct)

        if buy_dt <= target_friday and snap.last_friday_close is not None:
            week_baseline = shares * snap.last_friday_close
        else:
            week_baseline = cost_basis
            if snap.last_friday_close is None:
                has_week_data = False

        total_value += market_value
        total_cost += cost_basis
        total_week_baseline += week_baseline

        holdings.append({
            "ticker": b["ticker"],
            "shares": shares,
            "shares_str": format_shares(shares),
            "avg_cost": avg_cost,
            "market_value": market_value,
            "pl_pct_abs": abs_pct,
            "pl_sign": sign,
            "pl_class": klass,
            "_sort_key": market_value,
        })

    holdings.sort(key=lambda h: h["_sort_key"], reverse=True)

    pl_abs_raw = total_value - starting_capital
    pl_pct_raw = pl_abs_raw / starting_capital * 100.0
    pl_sign, pl_abs, pl_class = signed(pl_abs_raw)
    _, pl_pct_abs, _ = signed(pl_pct_raw)

    if has_week_data:
        week_abs_raw = total_value - total_week_baseline
        week_pct_raw = (
            week_abs_raw / total_week_baseline * 100.0 if total_week_baseline else 0.0
        )
    else:
        week_abs_raw = 0.0
        week_pct_raw = 0.0
    week_sign, week_abs, week_class = signed(week_abs_raw)
    _, week_pct_abs, _ = signed(week_pct_raw)

    bench_snap = prices[benchmark_ticker]
    bench_entry_buy = next(
        (b for b in buys if b["ticker"] == benchmark_ticker), None
    )
    if bench_entry_buy is None:
        raise RuntimeError(
            f"Benchmark ticker {benchmark_ticker} must have a buy entry for entry price"
        )
    bench_entry_price = float(bench_entry_buy["avg_cost"])
    nvda_pct_raw = (bench_snap.current / bench_entry_price - 1.0) * 100.0
    nvda_pct_sign, nvda_pct_abs, nvda_pct_class = signed(nvda_pct_raw)

    port_pct_raw = pl_pct_raw
    port_pct_sign, port_pct_abs, port_pct_class = signed(port_pct_raw)

    delta_pts = port_pct_raw - nvda_pct_raw
    if abs(delta_pts) < 0.005:
        verdict_text = f"Tied with {benchmark_ticker}"
        verdict_class = "muted"
    elif delta_pts > 0:
        verdict_text = f"Beating {benchmark_ticker} by +{delta_pts:.2f} pts"
        verdict_class = "pos"
    else:
        verdict_text = f"Trailing {benchmark_ticker} by {delta_pts:.2f} pts"
        verdict_class = "neg"

    trades = sorted(
        ({
            "ticker": b["ticker"],
            "shares_str": format_shares(float(b["shares"])),
            "avg_cost": float(b["avg_cost"]),
            "buy_date": b["buy_date"],
            "thesis": b["thesis"],
        } for b in buys),
        key=lambda t: t["buy_date"],
        reverse=True,
    )

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = env.get_template(TEMPLATE_NAME)
    return template.render(
        updated_at=TIMESTAMP_PLACEHOLDER,
        starting_capital=starting_capital,
        start_date=start_date,
        total_value=total_value,
        pl_abs=pl_abs,
        pl_pct_abs=pl_pct_abs,
        pl_sign=pl_sign,
        pl_class=pl_class,
        week_abs=week_abs,
        week_pct_abs=week_pct_abs,
        week_sign=week_sign,
        week_class=week_class,
        port_pct_abs=port_pct_abs,
        port_pct_sign=port_pct_sign,
        port_pct_class=port_pct_class,
        nvda_pct_abs=nvda_pct_abs,
        nvda_pct_sign=nvda_pct_sign,
        nvda_pct_class=nvda_pct_class,
        verdict_text=verdict_text,
        verdict_class=verdict_class,
        holdings=holdings,
        trades=trades,
    )


def format_timestamp(now: datetime) -> str:
    pacific = now.astimezone(PACIFIC)
    hour = pacific.hour % 12 or 12
    ampm = "AM" if pacific.hour < 12 else "PM"
    return (
        f"{pacific.strftime('%A, %B')} {pacific.day}, {pacific.year} · "
        f"{hour}:{pacific.strftime('%M')} {ampm} PT"
    )


def strip_timestamp(html: str) -> str:
    return re.sub(
        r'<div class="updated">Updated [^<]*</div>',
        f'<div class="updated">Updated {TIMESTAMP_PLACEHOLDER}</div>',
        html,
        count=1,
    )


def main() -> int:
    portfolio = json.loads(PORTFOLIO_PATH.read_text())
    tickers = sorted({b["ticker"] for b in portfolio["buys"]} | {portfolio["benchmark_ticker"]})

    now = datetime.now(tz=PACIFIC)
    prices = fetch_prices(tickers, now.date())

    rendered_with_placeholder = render(portfolio, prices, now)

    existing = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else ""
    existing_normalized = strip_timestamp(existing)

    if rendered_with_placeholder.strip() == existing_normalized.strip():
        print("No changes — skipping write.", file=sys.stderr)
        return 0

    final = rendered_with_placeholder.replace(TIMESTAMP_PLACEHOLDER, format_timestamp(now))
    OUTPUT_PATH.write_text(final)
    print(f"Wrote {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
