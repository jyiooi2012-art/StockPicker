# Stock & ETF picker

**Live report: https://jyiooi2012-art.github.io/StockPicker/** (prices on the page are live via TradingView; scores refresh hourly while Bursa and US markets are open, and new stocks are searched for at about 6:30am and 6:15pm Malaysia time; to refresh now: Actions tab → Daily stock picker → Run workflow)


A self-updating research dashboard. It scores every stock and ETF in `watchlist.txt` for
**short-term (weeks–3 months)**, **medium-term (3–18 months)** and **long-term (3–10 years)**
potential. It adds a news/sentiment layer, short-seller red flags and Malaysian tax notes, then
writes one HTML report.

> Research tool, not financial or tax advice. No screen can reliably say which stock will
> double or triple. This one gives you a ranked shortlist of where the evidence points, with
> the risks spelled out, so you spend your research time on the best candidates.

## What each source is used for

| Source | Best at | How the tool uses it |
|---|---|---|
| Yahoo Finance (yfinance) | Free prices for US, Bursa (.KL) and London (.L); fundamentals | All price/technical factors, growth, margins, valuation, analyst targets, dividends |
| FRED (St. Louis Fed) | Official macro data | Yield curve, 10y yield, Fed funds, VIX, high-yield spreads, MYR/USD |
| Sector ETFs (XLK, SMH, EWM…) | Where money is rotating | Sector & theme leaderboard |
| Bloomberg TV / Bloomberg Originals / CNBC / Yahoo Finance on YouTube | Daily market narrative | Latest videos plus transcripts, turned into themes and tone |
| Free RSS: CNBC, MarketWatch, Yahoo, Investing.com, The Star, Malay Mail, The Edge | Headlines, including Malaysia | Themes, tone, per-ticker "news buzz" |
| Culper Research (and other short sellers) | Forensic red flags | Flags the stock in the Risks column; never changes the score |
| Finviz, TradingView, StockAnalysis, Bursa announcements, Trading Economics | Deep-dive pages (charts, financials, filings, calendar) | One-click research links next to every pick (these sites don't allow scraping) |

## The watchlist updates itself

You keep your favourites in `watchlist.txt`; those are never removed. On top of that, every run the tool:

1. **Asks free stock-picking sources what looks interesting:** Yahoo screens (undervalued growth, aggressive small caps,
   a custom "hidden growers" screen for US small/mid caps with sales up 25%+ and high margins, and Bursa growth and momentum
   screens), Finviz screens (fast growth in an uptrend, quality at a fair price, insider buying), OpenInsider cluster buys
   (several executives buying their own shares), Dataroma superinvestor buys, and stocks named in free Motley Fool,
   Seeking Alpha and Zacks articles.
2. **Throws out traps:** shares under US$5 or RM0.50, stocks trading less than US$5M (RM1M) a day, companies under US$300M
   (RM500M) in size, and anything listed for less than about a year.
3. **Scores the rest** with the same model, against your current list.
4. **Adds the strong ones** (any score 75+, or long-term 70+, and decent on at least 2 of the 3 measures), up to 8 per run
   and 40 in total. Stocks with a short-seller report on file are not auto-added.
5. **Drops auto-picks that stay weak** (all three scores under 50) for 3 runs in a row.

The report shows every addition with **which source found it** and **why it made the cut, in plain words**, for example
"long-term score 78: sales growing fast, high profit margins, generates real cash". The rolling list is saved in
`reports/auto_watchlist.json`. All thresholds are in `RULES` at the top of `discovery.py`. Zacks Rank lists and paid
Motley Fool / Seeking Alpha picks sit behind logins, so only their free public articles are read. Run with `--no-discover`
to skip discovery for a run.

## How scoring works

Each factor is turned into a percentile *within your watchlist* (stocks vs stocks, ETFs vs ETFs),
then the factors are averaged with weights (see the top of `stock_picker.py`). A score of 80
means "stronger than 80% of what you're watching", **not** an 80% chance of going up.

- **Short:** 1m/3m return, price vs 50-day average, RSI sweet spot (penalised above 75), MACD, volume surge, distance from 52-week high.
- **Medium:** 12-month momentum (skipping the last month), vs 200-day average, golden cross, EPS and revenue growth, forward-P/E discount, analyst upside and rating.
- **Long ("multi-bagger traits"):** revenue growth, gross margin, ROE, free-cash-flow yield, PEG, debt, insider ownership, room to grow (smaller market cap), 3-year CAGR, and **tax drag for a Malaysian resident**.
- **ETFs:** momentum for short/medium; 3y/5y returns, fees, size, drawdown and tax drag for long-term.
- **Yrs→2x / 3x:** how long a double/triple would take *if* recent growth simply continued. It is arithmetic, not a forecast.
- **💎 On sale:** a stock with a weak short-term score (price falling over 1 month, 3 months or below its 50-day average) but a long-term score of 70+ and a double within ~10 years at its recent growth pace. Short-seller-flagged stocks are excluded. Shown as a badge everywhere and in its own tab.

News, sentiment and short-seller reports are shown **next to** the scores, not mixed in. Headlines are noisy and short sellers are biased, so you judge them yourself.

## Malaysian tax built in

Every row has a "Tax (MY resident)" note. The long-term and ETF scores penalise dividend
withholding drag, and the report includes a tax playbook (sources linked, checked 12 Nov 2026):

- No tax on listed-share gains for individual investors, unless you trade so often that LHDN treats it as a business.
- Foreign-sourced income is exempt for individuals until 31 Dec 2036, if it was taxed abroad.
- US dividends lose 30% at source. Irish-domiciled UCITS ETFs (CSPX, VUAA, CNDX, VWRA…) only lose 15%, and accumulating classes pay you nothing taxable. The tool suggests a UCITS swap for each US ETF.
- US estate tax applies to US-situs assets over US$60k (US shares and US-domiciled ETFs). UCITS ETFs and Bursa shares are not US-situs.
- Bursa: dividends are single-tier, with a 2% tax only above RM100k of Malaysian dividends a year. Stamp duty is 0.1%, capped at RM1,000 per contract.
- Reliefs: PRS (RM3,000, until YA2030), EPF (RM4,000 within the RM7,000 bucket), SSPN.

Re-check `tax.py` after each October Budget.

## Platform fees (moomoo, Webull, Rakuten, M+, CGS iTrade, Maybank, Tiger, IBKR)

The report includes a cost table per trade (RM1k / RM10k Bursa, US$1k / US$10k US, buy and buy+sell) with brokerage,
platform fees + SST, Bursa clearing, stamp duty, SEC fee and the FX spread shown separately. Numbers were checked on 12 Nov 2026
from 2026 comparison sites (official broker pages mostly block scripts); rows marked *unverified* had conflicting or single sources.
Try your own sizes with `python fees.py --rm 5000 --usd 2000 --price 50`, and update `BROKERS` in `fees.py` when pricing changes.

## On your Mac: install once, then just open the app

- **See your picks:** press ⌘ + Space, type **Stock Picker**, press Enter. (Or open `~/StockPicker/reports/latest.html`.)
- **Updates:** automatic. It checks every hour and refreshes whenever the report is 10+ hours old and the Mac is awake and online,
  so a Mac that was asleep at update time catches up as soon as you open it. The report shows a red "out of date" banner if it ever falls behind.
- **Force an update:** double-click **Run now** in `~/StockPicker`. **Check status** shows the last update and whether automatic updates are on.
- You never need to reinstall. Only re-run the installer if Claude sends you a new version.

## Run it on your computer

```bash
pip install -r requirements.txt
python stock_picker.py              # → reports/latest.html (+ dated copy and CSV)
python stock_picker.py --no-transcripts --top 15
python stock_picker.py --demo       # synthetic data, to test without internet
```

The first run takes a few minutes (fundamentals for ~100 tickers). Later runs reuse a 20-hour cache.

## Keep it online and updated automatically (free)

1. Create a new **public** GitHub repository (GitHub Pages is free for public repos) and upload this folder, including `.github/workflows/daily.yml`.
2. In the repo, open **Settings → Pages** and set **Source: GitHub Actions**.
3. Open **Actions → Daily stock picker → Run workflow** once.

From then on it refreshes at **6:30am** (after the US close) and **6:15pm** (after Bursa closes) Malaysia time, every weekday. The live report is at `https://<your-username>.github.io/<repo>/`, and a dated CSV history is committed to `reports/`.

If you'd rather keep it private, run it on your Mac with cron (`crontab -e`):
```
30 7 * * 2-6 cd ~/stock-picker && /usr/bin/python3 stock_picker.py >> run.log 2>&1
```

## Customise

- **What to scan:** edit `watchlist.txt`. Add any Yahoo symbol, e.g. `0208.KL` for Bursa or `CSPX.L` for a UCITS ETF.
- **Weights:** edit the `*_FACTORS` lists at the top of `stock_picker.py`.
- **News feeds and themes:** edit `YOUTUBE_CHANNELS`, `ARTICLE_FEEDS` and `THEMES` in `news.py`.
- **Short-seller reports:** add a line to `short_reports.csv` whenever Culper (or Muddy Waters, Spruce Point, etc.) publishes.

## Known limits

- Yahoo data is unofficial and sometimes missing or wrong, especially fundamentals for smaller Bursa stocks. Check numbers on the linked pages before acting.
- Scores are relative. In a falling market the "best" stocks can still fall; check the market-regime line at the top.
- Sentiment uses a simple word list. It's good for spotting themes, not for timing trades.
