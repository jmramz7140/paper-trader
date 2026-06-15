# paper-trader

A static single-page paper-trading dashboard hosted on GitHub Pages. A
scheduled GitHub Action refetches prices each US trading day and rewrites
`index.html` in place — no backend, no JavaScript on the page.

Live: https://jmramz7140.github.io/paper-trader/

## How it works

- `portfolio.json` — the source of truth for holdings (ticker, shares, average
  cost, buy date, thesis) plus starting capital and the benchmark ticker.
- `template.html.j2` — the Jinja2 template that mirrors the visual design.
  Placeholders for prices, totals, P/L, weekly change, the NVDA comparison,
  the verdict line, and the timestamp.
- `scripts/update.py` — reads `portfolio.json`, fetches current and last‑Friday
  closes via [`yfinance`](https://pypi.org/project/yfinance/) (no API key
  required), computes per‑position market value, total value, P/L vs cost
  basis, daily change, weekly change, and the NVDA benchmark, then renders
  the template into `index.html` with a Pacific‑time timestamp.
- `.github/workflows/update-prices.yml` — runs `update.py` on a cron each
  weekday at **21:05 UTC (~2:05 PM PT, just after market close)**, plus on
  manual dispatch. Commits and pushes only if `index.html` actually changed.

The script is idempotent: if nothing has moved, it leaves `index.html`
untouched (timestamp included), so the workflow’s `git diff` check passes and
no commit is made.

## Editing holdings

Edit `portfolio.json` and push. Each buy entry needs:

```json
{
  "ticker": "TICKER",
  "shares": 0.1234,
  "avg_cost": 123.45,
  "buy_date": "2026-06-14",
  "thesis": "Why I bought this."
}
```

Holdings are auto‑sorted by current market value (largest first). Trade
history is sorted by `buy_date` descending. The benchmark used for the
"vs. Just Holding NVDA" card is set by `benchmark_ticker` — its entry price
comes from the matching buy in the `buys` list.

## Manual trigger

Two options:

1. **GitHub UI** — Actions → "Update prices" → "Run workflow" on `main`.
2. **gh CLI** — `gh workflow run update-prices.yml`.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update.py
open index.html
```

## Known limitations

- **yfinance can rate‑limit.** It scrapes Yahoo; intermittent failures are
  possible. A single failed run is harmless — the cron will retry next
  trading day, or you can re‑run manually.
- **Actions cron is best‑effort.** GitHub explicitly does not guarantee the
  scheduled trigger fires on time, especially during high‑load periods. Runs
  can slip several minutes (occasionally tens of minutes).
- **Pages takes 1–3 minutes to redeploy** after the push. The committed
  `index.html` is correct immediately, but the live URL lags slightly.
- **No intraday updates.** By design — the cron fires once after close.
- **Paper trading only.** No brokerage, no real money. The NVDA comparison
  is a sanity check, not a recommendation.
