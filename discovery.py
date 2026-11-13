"""
Self-updating watchlist: finds NEW stocks worth watching from free "stock-picker" sources,
filters out penny-stock traps, and keeps a rolling auto-watchlist (auto_watchlist.json).

Your own watchlist.txt is never touched; those stocks are "pinned". Auto-picked stocks are added
when they score well and dropped after several weak runs.

Sources (each is best-effort; if a site blocks the script, the others still work):
  * Yahoo Finance screens: built-in ones (undervalued growth, aggressive small caps, growth tech…)
    plus custom ones for hidden small/mid-cap growers and for Bursa Malaysia growth/momentum
  * Finviz screener: fast-growing, profitable, liquid stocks in uptrends (CAN SLIM-style)
  * OpenInsider: "cluster buys" (several company insiders buying with their own money)
  * Dataroma: recent buys by "superinvestors" (Buffett, Ackman, Li Lu… via 13F filings)
  * Ticker mentions in free stock-picking articles (Motley Fool, Seeking Alpha, Zacks, Nasdaq RSS)
Not used: Zacks Rank lists and paid picks from Motley Fool / Seeking Alpha are behind logins or
block scripts, so the tool only reads their free public feeds.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# ---------------- rules (edit freely) -------------------------------------------------------------
RULES = {
    "min_price_us": 5.0,              # avoid US penny stocks
    "min_price_my": 0.50,             # RM; avoid Bursa penny stocks
    "min_dollar_vol_us": 5_000_000,   # average daily traded value, USD
    "min_dollar_vol_my": 1_000_000,   # RM
    "min_mcap_us": 300_000_000,       # USD
    "min_mcap_my": 500_000_000,       # RM
    "max_candidates_to_score": 120,   # keeps runs fast and polite
    "add_if_best_score": 75,          # add if short, medium OR long score >= this...
    "add_if_long_score": 70,          # ...or long-term score >= this
    "max_adds_per_run": 8,
    "max_auto": 40,                   # size cap for the auto-watchlist
    "drop_below": 50,                 # a run where every score is below this = one strike
    "strikes_to_drop": 3,             # dropped after this many weak runs in a row
    # a new stock must also score 50+ on at least 2 of the 3 horizons
}

TICKER_IN_TEXT = re.compile(r"\((?:NASDAQ|Nasdaq|NYSE|NYSEMKT|NYSE American|NYSEAMERICAN)\s*:\s*([A-Z]{1,5}(?:\.[A-Z])?)\)")
ARTICLE_FEEDS = {
    "Motley Fool": "https://www.fool.com/a/feeds/foolwatch?format=rss2&id=foolwatch&apikey=foolwatch-feed",
    "Seeking Alpha (market news)": "https://seekingalpha.com/market_currents.xml",
    "Seeking Alpha (ideas)": "https://seekingalpha.com/feed.xml",
    "Zacks (Nasdaq.com)": "https://www.nasdaq.com/feed/rssoutbound?category=Zacks",
    "Nasdaq stocks": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks",
}
FINVIZ_SCREENS = {
    # EPS & sales growth >25% q/q, above 200-day, liquid, small cap and up
    "Finviz growth + uptrend screen": "https://finviz.com/screener.ashx?v=111&f=cap_smallover,fa_epsqoq_o25,fa_salesqoq_o25,sh_avgvol_o300,sh_price_o5,ta_sma200_pa&o=-perf13w",
    # profitable, high ROE, reasonable PEG
    "Finviz quality-at-a-fair-price screen": "https://finviz.com/screener.ashx?v=111&f=cap_smallover,fa_peg_u2,fa_roe_o20,fa_sales5years_o15,sh_avgvol_o300,sh_price_o5&o=-marketcap",
    # insider buying signal
    "Finviz recent insider buying": "https://finviz.com/screener.ashx?v=111&s=it_latestbuys&f=cap_smallover,sh_avgvol_o200,sh_price_o5",
}
PLAIN = {  # how each source is explained in the report
    "yahoo:undervalued_growth_stocks": "Yahoo screen: growing earnings fast but still cheap",
    "yahoo:growth_technology_stocks": "Yahoo screen: fast-growing tech companies",
    "yahoo:aggressive_small_caps": "Yahoo screen: small companies with very fast earnings growth",
    "yahoo:small_cap_gainers": "Yahoo screen: small companies with strong price momentum",
    "yahoo:undervalued_large_caps": "Yahoo screen: big companies trading cheaply",
    "yahoo:hidden_growers": "Hidden-gem screen: US small/mid caps with sales up 25%+ and high margins",
    "yahoo:my_growth": "Bursa screen: Malaysian companies growing sales and profitable",
    "yahoo:my_momentum": "Bursa screen: Malaysian stocks up strongly over the past year",
}


def _get(url: str, timeout: int = 20) -> str:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"  ! discovery source failed ({url[:55]}…): {e}", file=sys.stderr)
        return ""


def _add(pool: dict, ticker: str, source: str):
    t = ticker.strip().upper().replace("/", "-")
    if not t or len(t) > 10:
        return
    pool.setdefault(t, set()).add(source)


# ---------------- sources -----------------------------------------------------------------------
def from_yahoo(pool: dict):
    try:
        import yfinance as yf
        from yfinance import EquityQuery as Q
    except ImportError:
        return
    for name in ("undervalued_growth_stocks", "growth_technology_stocks", "aggressive_small_caps",
                 "small_cap_gainers", "undervalued_large_caps"):
        try:
            for q in yf.screen(name, count=50).get("quotes", []):
                _add(pool, q["symbol"], f"yahoo:{name}")
        except Exception as e:
            print(f"  ! Yahoo screen {name}: {e}", file=sys.stderr)
    custom = {
        "hidden_growers": Q("and", [Q("eq", ["region", "us"]),
                                    Q("btwn", ["intradaymarketcap", 3e8, 1e10]),
                                    Q("gt", ["totalrevenues1yrgrowth.lasttwelvemonths", 25]),
                                    Q("gt", ["grossprofitmargin.lasttwelvemonths", 40]),
                                    Q("gt", ["intradayprice", 5]),
                                    Q("gt", ["avgdailyvol3m", 300000])]),
        "my_growth": Q("and", [Q("eq", ["region", "my"]),
                               Q("gt", ["intradaymarketcap", 5e8]),
                               Q("gt", ["totalrevenues1yrgrowth.lasttwelvemonths", 10]),
                               Q("gt", ["returnonequity.lasttwelvemonths", 10])]),
        "my_momentum": Q("and", [Q("eq", ["region", "my"]),
                                 Q("gt", ["intradaymarketcap", 3e8]),
                                 Q("gt", ["fiftytwowkpercentchange", 20])]),
    }
    for name, q in custom.items():
        try:
            res = yf.screen(q, size=60, sortField="intradaymarketcap", sortAsc=False)
            for x in res.get("quotes", []):
                _add(pool, x["symbol"], f"yahoo:{name}")
        except Exception as e:
            print(f"  ! Yahoo custom screen {name}: {e}", file=sys.stderr)


def from_finviz(pool: dict):
    for name, url in FINVIZ_SCREENS.items():
        page = _get(url)
        for t in set(re.findall(r'quote\.ashx\?t=([A-Z][A-Z.\-]{0,6})&', page)):
            _add(pool, t, name)


def from_openinsider(pool: dict):
    page = _get("http://openinsider.com/latest-cluster-buys")
    for t in set(re.findall(r'<a href="/([A-Z][A-Z.]{0,5})"', page)):
        _add(pool, t, "OpenInsider: several insiders buying their own shares")


def from_dataroma(pool: dict):
    page = _get("https://www.dataroma.com/m/allact.php?typ=b")
    for t in set(re.findall(r'sym=([A-Z][A-Z.]{0,6})', page)):
        _add(pool, t, "Dataroma: recently bought by a well-known 'superinvestor' fund")


def from_articles(pool: dict):
    for name, url in ARTICLE_FEEDS.items():
        text = _get(url)
        counts: dict[str, int] = {}
        for t in TICKER_IN_TEXT.findall(text):
            counts[t] = counts.get(t, 0) + 1
        for t in counts:
            _add(pool, t, f"Mentioned in {name} articles")


def collect(exclude: set[str]) -> dict[str, set[str]]:
    pool: dict[str, set[str]] = {}
    for fn in (from_yahoo, from_finviz, from_openinsider, from_dataroma, from_articles):
        try:
            fn(pool)
        except Exception as e:
            print(f"  ! {fn.__name__}: {e}", file=sys.stderr)
    pool = {t: s for t, s in pool.items() if t not in exclude and not t.startswith("^")}
    # keep Bursa and US listings only (the scoring/tax/fee logic is built for these)
    pool = {t: s for t, s in pool.items() if t.endswith(".KL") or "." not in t or t.endswith("-B")}
    # most-recommended first: a stock found by several independent sources is a stronger lead
    ranked = sorted(pool.items(), key=lambda kv: -len(kv[1]))
    print(f"  discovery: {len(pool)} new candidates from all sources", file=sys.stderr)
    return dict(ranked[: RULES["max_candidates_to_score"]])


# ---------------- penny-stock / liquidity filter --------------------------------------------------
def passes_filters(ticker: str, df, info: dict) -> tuple[bool, str]:
    my = ticker.endswith(".KL")
    c, v = df["Close"], df.get("Volume")
    price = float(c.iloc[-1])
    dollar_vol = float((c.iloc[-60:] * v.iloc[-60:]).mean()) if v is not None else 0.0
    mcap = info.get("marketCap") or 0
    if price < RULES["min_price_my" if my else "min_price_us"]:
        return False, "penny stock (price too low)"
    if dollar_vol < RULES["min_dollar_vol_my" if my else "min_dollar_vol_us"]:
        return False, "too thinly traded (hard to buy/sell)"
    if mcap and mcap < RULES["min_mcap_my" if my else "min_mcap_us"]:
        return False, "company too small"
    if len(c) < 200:
        return False, "listed too recently (less than ~1 year of prices)"
    return True, ""


# ---------------- state: the rolling auto-watchlist -------------------------------------------------
def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def save_state(path: Path, state: dict):
    path.write_text(json.dumps(state, indent=1, sort_keys=True))


FACTOR_WORDS = {
    "ret_3m": "strong 3-month price rise", "ret_1m": "strong recent price rise", "pct_vs_sma50": "trading above its 50-day trend",
    "dist_52w_high": "near its 52-week high", "vol_surge": "rising trading volume", "macd_hist_pct": "momentum turning up",
    "ret_12_1": "strong 12-month momentum", "ret_6m": "strong 6-month momentum", "pct_vs_sma200": "above its long-term trend",
    "earnings_growth": "profits growing fast", "revenue_growth": "sales growing fast", "fwd_pe_discount": "analysts expect profits to rise",
    "analyst_upside": "analyst price targets well above today's price", "gross_margin": "high profit margins",
    "roe": "high return on shareholders' money", "fcf_yield": "generates real cash", "peg": "price reasonable for its growth",
    "debt_to_equity": "low debt", "insider_own": "insiders own a big stake", "size_runway": "still small, so room to grow",
    "cagr_3y": "strong 3-year track record", "tax_drag": "little dividend tax drag",
}


def explain(row, factor_sets: dict) -> str:
    """Plain-English 'why': the strongest factors behind the stock's best horizon score."""
    best = max(("short", "medium", "long"), key=lambda h: (row.get(f"{h}_score") or -1))
    pct = row.get("_pct", {}).get(best, {})
    tops = [f for f, _ in sorted(pct.items(), key=lambda kv: -kv[1]) if f in FACTOR_WORDS][:3]
    label = {"short": "short-term", "medium": "medium-term", "long": "long-term"}[best]
    return f"{label} score {row.get(f'{best}_score', 0):.0f}: " + ", ".join(FACTOR_WORDS[f] for f in tops)


def update(state: dict, scored, candidates: dict[str, set[str]], rejected: dict[str, str], today: str,
           shorts: dict | None = None) -> dict:
    """Mutates state; returns a change log for the report."""
    log = {"added": [], "dropped": [], "kept": [], "rejected": rejected, "n_candidates": len(candidates)}
    best = lambda t: max((scored.loc[t, k] for k in ("short_score", "medium_score", "long_score")  # noqa: E731
                          if scored.loc[t, k] == scored.loc[t, k]), default=0)
    # 1. existing auto picks: strike or keep
    for t in list(state):
        if t not in scored.index:
            continue
        b = best(t)
        state[t]["last_best"] = float(b)
        state[t]["strikes"] = state[t].get("strikes", 0) + 1 if b < RULES["drop_below"] else 0
        if state[t]["strikes"] >= RULES["strikes_to_drop"]:
            log["dropped"].append((t, f"all scores below {RULES['drop_below']} for {RULES['strikes_to_drop']} runs in a row (latest best {b:.0f})"))
            del state[t]
        else:
            log["kept"].append(t)
    # 2. new candidates
    cands = [t for t in candidates if t in scored.index and t not in state]
    cands.sort(key=lambda t: -best(t))
    adds = 0
    for t in cands:
        r = scored.loc[t]
        ls = r.get("long_score")
        ok = best(t) >= RULES["add_if_best_score"] or (ls == ls and ls >= RULES["add_if_long_score"])
        n_decent = sum(1 for k in ("short_score", "medium_score", "long_score") if r.get(k) == r.get(k) and r.get(k) >= 50)
        if not ok:
            continue
        if n_decent < 2:  # one-trick ponies (e.g. pure hype momentum with weak fundamentals) are skipped
            log["rejected"][t] = "strong on only one measure"
            continue
        if shorts and t in shorts:
            log["rejected"][t] = "has a short-seller report on file"
            continue
        if adds >= RULES["max_adds_per_run"]:
            break
        why = r.get("_why", "")
        state[t] = {"added": today, "sources": sorted(candidates[t]), "why": why, "strikes": 0, "last_best": float(best(t)),
                    "name": str(r.get("name", ""))}
        log["added"].append((t, sorted(candidates[t]), why))
        adds += 1
    # 3. size cap: drop the weakest auto picks
    if len(state) > RULES["max_auto"]:
        for t in sorted(state, key=lambda x: state[x].get("last_best", 0))[: len(state) - RULES["max_auto"]]:
            log["dropped"].append((t, "watchlist full; weakest auto-pick made room for better ones"))
            del state[t]
    return log


def render(log: dict, state: dict) -> str:
    import html as h
    src = lambda ss: "<br>".join(h.escape(PLAIN.get(s, s)) for s in ss)  # noqa: E731
    added = "".join(f"<tr><td><b>{h.escape(t)}</b> <span class='badge new'>NEW</span></td><td><small>{src(ss)}</small></td>"
                    f"<td><small>{h.escape(why)}</small></td></tr>" for t, ss, why in log["added"])
    dropped = "".join(f"<tr><td><b>{h.escape(t)}</b></td><td colspan=2><small>{h.escape(why)}</small></td></tr>" for t, why in log["dropped"])
    current = "".join(f"<tr><td><b>{h.escape(t)}</b> <small>{h.escape(d.get('name', '')[:28])}</small></td><td><small>{h.escape(d['added'])}</small></td>"
                      f"<td><small>{src(d.get('sources', []))}</small></td><td><small>{h.escape(d.get('why', ''))}</small></td>"
                      f"<td>{'⚠️ ' * d.get('strikes', 0)}</td></tr>"
                      for t, d in sorted(state.items(), key=lambda kv: -kv[1].get('last_best', 0)))
    rej = log.get("rejected", {})
    rej_reasons: dict[str, int] = {}
    for why in rej.values():
        rej_reasons[why] = rej_reasons.get(why, 0) + 1
    rej_txt = "; ".join(f"{n} × {w}" for w, n in sorted(rej_reasons.items(), key=lambda kv: -kv[1])) or "none"
    return f"""<h2>Self-updating watchlist</h2>
<p class="desc"><b>How it works, simply:</b> every run, the tool asks several free stock-picking sources "what looks interesting?"
(Yahoo and Finviz screens, insiders buying their own shares, famous investors' recent buys, and stocks named in Motley Fool / Seeking Alpha / Zacks articles).
It throws out penny stocks, tiny companies and stocks that barely trade, then scores the rest with the same model as everything else.
Strong ones join the watchlist (marked NEW); auto-picked stocks that stay weak for {RULES['strikes_to_drop']} runs in a row are removed. Your own watchlist.txt stocks are never removed.</p>
<p class="desc">This run: {log['n_candidates']} candidates checked · {len(log['added'])} added · {len(log['dropped'])} dropped · filtered out: {h.escape(rej_txt)}.</p>
<h3>Added this run</h3>{'<table><thead><tr><th>Stock</th><th>Found by</th><th>Why it made the cut</th></tr></thead><tbody>' + added + '</tbody></table>' if added else '<p class="desc">No new stock was strong enough this run.</p>'}
{'<h3>Dropped this run</h3><table><tbody>' + dropped + '</tbody></table>' if dropped else ''}
<h3>Current auto-picked stocks ({len(state)})</h3>
<table><thead><tr><th>Stock</th><th>Added</th><th>Found by</th><th>Why</th><th>Weak runs</th></tr></thead><tbody>{current}</tbody></table>"""
