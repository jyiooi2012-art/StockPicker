#!/usr/bin/env python3
"""
Ongoing stock & ETF picker (research tool, NOT financial advice).

Pulls free data (Yahoo Finance via yfinance, macro data from FRED's public CSV),
scores every ticker in watchlist.txt for SHORT, MEDIUM and LONG-term potential,
flags risks, and writes a self-contained HTML report + CSV.

    pip install yfinance pandas numpy
    python stock_picker.py                 # uses watchlist.txt next to this file
    python stock_picker.py --top 15 --out reports
    python stock_picker.py --demo          # synthetic data, to test without internet

Scores are 0-100 percentiles *within your watchlist* (stocks ranked against stocks,
ETFs against ETFs). A 90 means "better than 90% of what you're watching on these
factors", not "90% chance of going up".
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import news as newsmod  # noqa: E402
import tax as taxmod  # noqa: E402
import shortsellers  # noqa: E402
import fees as feesmod  # noqa: E402
import discovery  # noqa: E402

HERE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------------------
# Factor definitions: (feature, weight, direction)  direction +1 = higher is better
# --------------------------------------------------------------------------------------
SHORT_FACTORS = [  # weeks to ~3 months: trend + momentum + participation
    ("ret_1m", 1.0, +1),
    ("ret_3m", 1.0, +1),
    ("pct_vs_sma50", 1.0, +1),
    ("rsi_quality", 1.0, +1),       # best around 55-65, penalised when overbought/oversold
    ("macd_hist_pct", 0.8, +1),
    ("vol_surge", 0.6, +1),         # 10d vs 60d volume
    ("dist_52w_high", 0.8, +1),     # closer to the high = stronger trend
]
MEDIUM_FACTORS = [  # ~3-18 months: intermediate momentum + earnings revisions proxies
    ("ret_12_1", 1.2, +1),          # 12-month return excluding last month (classic momentum)
    ("ret_6m", 0.8, +1),
    ("pct_vs_sma200", 0.8, +1),
    ("golden_cross", 0.4, +1),
    ("earnings_growth", 1.0, +1),
    ("revenue_growth", 0.8, +1),
    ("fwd_pe_discount", 0.6, +1),   # forward P/E below trailing = earnings expected to rise
    ("analyst_upside", 0.6, +1),
    ("analyst_rating", 0.4, -1),    # 1 = strong buy ... 5 = sell
]
LONG_FACTORS = [  # 3-10 years: "can it compound?" quality + growth at a sane price
    ("revenue_growth", 1.2, +1),
    ("gross_margin", 1.0, +1),
    ("roe", 0.8, +1),
    ("fcf_yield", 0.8, +1),
    ("peg", 0.8, -1),
    ("debt_to_equity", 0.6, -1),
    ("insider_own", 0.4, +1),
    ("size_runway", 0.5, +1),       # smaller market cap = more room to multiply (and more risk)
    ("cagr_3y", 0.5, +1),
    ("tax_drag", 0.3, -1),          # yield lost to dividend withholding for a Malaysian resident
]
ETF_SHORT = [("ret_1m", 1, +1), ("ret_3m", 1, +1), ("pct_vs_sma50", 1, +1),
             ("rsi_quality", 0.8, +1), ("macd_hist_pct", 0.6, +1), ("dist_52w_high", 0.8, +1)]
ETF_MEDIUM = [("ret_12_1", 1.2, +1), ("ret_6m", 0.8, +1), ("pct_vs_sma200", 1, +1),
              ("golden_cross", 0.4, +1)]
ETF_LONG = [("cagr_3y", 1, +1), ("etf_5y_return", 1, +1), ("expense_ratio", 0.8, -1),
            ("aum", 0.5, +1), ("max_dd_1y", 0.5, +1), ("tax_drag", 0.4, -1)]

FRED_SERIES = {
    "T10Y2Y": "US 10y-2y yield curve (%)",
    "DGS10": "US 10y Treasury yield (%)",
    "FEDFUNDS": "Fed funds rate (%)",
    "VIXCLS": "VIX volatility index",
    "BAMLH0A0HYM2": "US high-yield credit spread (%)",
    "DEXMAUS": "MYR per USD",
}
SECTOR_ETFS = {"XLK": "Tech", "XLC": "Comm. services", "XLY": "Consumer disc.", "XLF": "Financials",
               "XLV": "Healthcare", "XLI": "Industrials", "XLE": "Energy", "XLB": "Materials",
               "XLP": "Consumer staples", "XLU": "Utilities", "XLRE": "Real estate",
               "SMH": "Semiconductors", "EWM": "Malaysia (iShares MSCI)", "EEM": "Emerging mkts",
               "GLD": "Gold", "TLT": "Long Treasuries"}
BENCHMARKS = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^KLSE": "FBM KLCI"}
# Everyday names people search for (Yahoo uses official names like "Malayan Banking Berhad")
ALIASES = {"1155.KL": "maybank", "1295.KL": "public bank pbb", "1023.KL": "cimb", "5819.KL": "hong leong bank hlbank",
           "5347.KL": "tenaga tnb", "5225.KL": "ihh healthcare", "6012.KL": "maxis", "6947.KL": "celcomdigi digi celcom",
           "4863.KL": "telekom tm unifi", "0166.KL": "inari", "0097.KL": "vitrox", "0128.KL": "frontken", "0138.KL": "myeg",
           "5398.KL": "gamuda", "4677.KL": "ytl corp", "6742.KL": "ytl power", "8869.KL": "press metal", "5211.KL": "sunway",
           "3182.KL": "genting", "5168.KL": "hartalega", "7113.KL": "top glove", "7084.KL": "ql resources",
           "5183.KL": "petronas chemicals pchem", "3816.KL": "misc", "GOOGL": "google alphabet", "META": "facebook",
           "BRK-B": "berkshire hathaway buffett", "TSM": "tsmc taiwan semiconductor", "NVO": "novo nordisk ozempic wegovy",
           "LLY": "eli lilly", "V": "visa", "MA": "mastercard", "COST": "costco", "SPY": "s&p 500 sp500",
           "VOO": "s&p 500 sp500 vanguard", "QQQ": "nasdaq 100", "EWM": "malaysia etf", "GLD": "gold"}


# TradingView lists Bursa stocks by short name, not by number
TV_MYX = {"1155": "MAYBANK", "1295": "PBBANK", "1023": "CIMB", "5819": "HLBANK", "5347": "TENAGA", "5225": "IHH",
          "6012": "MAXIS", "6947": "CDB", "4863": "TM", "0166": "INARI", "0097": "VITROX", "0128": "FRONTKN",
          "0138": "MYEG", "5398": "GAMUDA", "4677": "YTL", "6742": "YTLPOWR", "8869": "PMETAL", "5211": "SUNWAY",
          "3182": "GENTING", "5168": "HARTA", "7113": "TOPGLOV", "7084": "QL", "5183": "PCHEM", "3816": "MISC",
          "1066": "RHBBANK", "1818": "BURSA", "4715": "GENM", "5681": "PETDAG", "6033": "PETGAS", "1961": "IOICORP",
          "2445": "KLK", "5285": "SDG", "4197": "SIME", "1082": "HLFG", "6888": "AXIATA", "5296": "MRDIY",
          "5246": "WPRTS", "0099": "SCICOM", "4324": "HENGYUAN", "5292": "UWC"}


def tv_symbol(t: str) -> str:
    """TradingView symbol for live price widgets/links ('' if unknown)."""
    if t.endswith(".KL"):
        name = TV_MYX.get(t[:-3])
        return f"MYX:{name}" if name else ""
    if t.startswith("^"):
        return {"^GSPC": "FOREXCOM:SPXUSD", "^IXIC": "NASDAQ:IXIC", "^KLSE": "MYX:FBMKLCI"}.get(t, "")
    return t.replace("-", ".")


def search_text(t, r) -> str:
    base = t.split(".")[0]
    return html.escape(f"{t} {base} {r.get('name', '')} {r.get('sector', '')} {ALIASES.get(t, '')}".lower())


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
def read_watchlist(path: Path) -> list[str]:
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line not in out:
            out.append(line.upper())
    return out


def fetch_prices(tickers: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    raw = yf.download(tickers, period="3y", interval="1d", auto_adjust=True,
                      group_by="ticker", threads=True, progress=False)
    out = {}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df.dropna(subset=["Close"])
            if len(df) > 60:
                out[t] = df
        except KeyError:
            pass
    return out


def fetch_info(tickers: list[str], cache_file: Path, max_age_h: float = 20) -> dict[str, dict]:
    """Fundamentals change slowly; cache them so daily runs are fast and polite."""
    import yfinance as yf
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except Exception:
            cache = {}
    now = time.time()
    for i, t in enumerate(tickers, 1):
        c = cache.get(t)
        if c and now - c.get("_ts", 0) < max_age_h * 3600:
            continue
        for attempt in range(3):
            try:
                info = yf.Ticker(t).info or {}
                info = {k: v for k, v in info.items() if isinstance(v, (int, float, str, bool)) or v is None}
                info["_ts"] = now
                cache[t] = info
                break
            except Exception as e:  # rate limits happen; back off
                if attempt == 2:
                    print(f"  ! info failed for {t}: {e}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        print(f"  fundamentals {i}/{len(tickers)} {t}", end="\r", file=sys.stderr)
        time.sleep(0.25)
    print(file=sys.stderr)
    cache_file.write_text(json.dumps(cache))
    return cache


def fetch_fred(series_id: str) -> pd.Series | None:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stock-picker/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            df = pd.read_csv(io.StringIO(r.read().decode()))
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        s = df.dropna().set_index(pd.to_datetime(df.dropna()["date"]))["value"]
        return s
    except Exception as e:
        print(f"  ! FRED {series_id} failed: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------------------
def _ret(c: pd.Series, n: int, skip: int = 0) -> float:
    if len(c) <= n:
        return np.nan
    return c.iloc[-1 - skip] / c.iloc[-1 - n] - 1


def rsi(c: pd.Series, n: int = 14) -> float:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return float(100 - 100 / (1 + rs.iloc[-1]))


def technical_features(df: pd.DataFrame) -> dict:
    c, v = df["Close"], df.get("Volume")
    last = c.iloc[-1]
    sma50, sma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
    ema12, ema26 = c.ewm(span=12).mean(), c.ewm(span=26).mean()
    macd = ema12 - ema26
    hist = (macd - macd.ewm(span=9).mean()).iloc[-1]
    r = rsi(c)
    yr = c.iloc[-252:]
    daily = c.pct_change().dropna()
    years = min(len(c) / 252, 3)
    f = {
        "price": last,
        "ret_1m": _ret(c, 21), "ret_3m": _ret(c, 63), "ret_6m": _ret(c, 126),
        "ret_12m": _ret(c, 252), "ret_12_1": _ret(c, 252, skip=21),
        "pct_vs_sma50": last / sma50 - 1 if sma50 == sma50 else np.nan,
        "pct_vs_sma200": last / sma200 - 1 if sma200 == sma200 else np.nan,
        "golden_cross": float(sma50 > sma200) if sma200 == sma200 else np.nan,
        "rsi": r,
        "rsi_quality": -abs(r - 60) - (max(0, r - 75) * 2),
        "macd_hist_pct": hist / last,
        "dist_52w_high": last / yr.max() - 1,
        "max_dd_1y": float((yr / yr.cummax() - 1).min()),
        "vol_ann": float(daily.iloc[-63:].std() * math.sqrt(252)),
        "cagr_3y": (last / c.iloc[0]) ** (1 / years) - 1 if years >= 1 else np.nan,
    }
    if v is not None and v.iloc[-60:].mean() > 0:
        f["vol_surge"] = v.iloc[-10:].mean() / v.iloc[-60:].mean()
    return f


def fundamental_features(info: dict) -> dict:
    g = lambda k: info.get(k) if isinstance(info.get(k), (int, float)) else np.nan  # noqa: E731
    price = g("currentPrice") if g("currentPrice") == g("currentPrice") else g("regularMarketPrice")
    mcap, fcf = g("marketCap"), g("freeCashflow")
    tpe, fpe = g("trailingPE"), g("forwardPE")
    peg = g("trailingPegRatio")
    if not peg == peg:
        peg = g("pegRatio")
    exp = g("netExpenseRatio")
    if not exp == exp:
        exp = g("annualReportExpenseRatio") * 100 if g("annualReportExpenseRatio") == g("annualReportExpenseRatio") else np.nan
    f5 = g("fiveYearAverageReturn")
    return {
        "name": info.get("shortName") or info.get("longName") or "",
        "type": "ETF" if str(info.get("quoteType", "")).upper() in ("ETF", "MUTUALFUND") else "Stock",
        "sector": info.get("sector") or info.get("category") or "",
        "currency": info.get("currency") or "",
        "market_cap": mcap,
        "revenue_growth": g("revenueGrowth"),
        "earnings_growth": g("earningsGrowth") if g("earningsGrowth") == g("earningsGrowth") else g("earningsQuarterlyGrowth"),
        "gross_margin": g("grossMargins"),
        "op_margin": g("operatingMargins"),
        "roe": g("returnOnEquity"),
        "fcf_yield": fcf / mcap if mcap and mcap == mcap and fcf == fcf else np.nan,
        "fcf": fcf,
        "pe": tpe, "fwd_pe": fpe,
        "fwd_pe_discount": (tpe / fpe - 1) if tpe and fpe and tpe > 0 and fpe > 0 else np.nan,
        "peg": peg if peg == peg and peg > 0 else np.nan,
        "debt_to_equity": g("debtToEquity"),
        "insider_own": g("heldPercentInsiders"),
        "size_runway": -math.log10(mcap) if mcap and mcap == mcap and mcap > 0 else np.nan,
        "analyst_upside": (g("targetMeanPrice") / price - 1) if price and price == price and g("targetMeanPrice") == g("targetMeanPrice") else np.nan,
        "analyst_rating": g("recommendationMean"),
        "n_analysts": g("numberOfAnalystOpinions"),
        "beta": g("beta"),
        "expense_ratio": exp,
        "aum": g("totalAssets"),
        "etf_5y_return": f5,
    }


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------
def score(df: pd.DataFrame, factors: list[tuple]) -> pd.Series:
    """Weighted average of percentile ranks; missing factors are skipped, not penalised,
    but a ticker needs at least half of the factor weight present to get a score."""
    total = pd.Series(0.0, index=df.index)
    wsum = pd.Series(0.0, index=df.index)
    full = sum(w for _, w, _ in factors)
    for col, w, d in factors:
        if col not in df:
            continue
        x = pd.to_numeric(df[col], errors="coerce") * d
        pr = x.rank(pct=True)
        m = pr.notna()
        total[m] += pr[m] * w
        wsum[m] += w
    s = (total / wsum.replace(0, np.nan)) * 100
    s[wsum < full * 0.5] = np.nan
    return s.round(0)


def risk_flags(r: pd.Series) -> str:
    f = []
    if r.get("vol_ann", 0) > 0.55: f.append("very volatile")
    if r.get("max_dd_1y", 0) < -0.40: f.append(f"fell {-r['max_dd_1y']:.0%} from 1y high")
    if r.get("rsi", 50) > 75: f.append("overbought (RSI>75)")
    if r.get("rsi", 50) < 30: f.append("oversold/falling knife")
    if r.get("beta", 1) == r.get("beta", 1) and r.get("beta", 1) > 1.8: f.append("high beta")
    if r.get("type") == "Stock":
        if r.get("fcf", 0) == r.get("fcf", 0) and r.get("fcf", 0) < 0: f.append("cash-burning")
        if r.get("debt_to_equity", 0) == r.get("debt_to_equity", 0) and r.get("debt_to_equity", 0) > 200: f.append("high debt")
        if r.get("pe", 0) == r.get("pe", 0) and r.get("pe", 0) > 80: f.append("rich valuation (P/E>80)")
        if r.get("revenue_growth", 0) == r.get("revenue_growth", 0) and r.get("revenue_growth", 0) < 0: f.append("shrinking sales")
    if r.get("pct_vs_sma200", 0) < 0: f.append("below 200-day trend")
    return "; ".join(f)


def years_to_multiple(cagr: float, mult: float) -> float:
    if not cagr == cagr or cagr <= 0:
        return np.nan
    return math.log(mult) / math.log(1 + cagr)


def build_table(prices: dict, infos: dict) -> pd.DataFrame:
    rows = []
    for t, df in prices.items():
        row = {"ticker": t}
        row.update(fundamental_features(infos.get(t, {})))
        row.update(taxmod.features(t, row["type"], infos.get(t, {})))
        if t.startswith("^"):
            row["type"] = "Index"
        row.update(technical_features(df))
        rows.append(row)
    tab = pd.DataFrame(rows).set_index("ticker")
    for kind, (s, m, l) in {"Stock": (SHORT_FACTORS, MEDIUM_FACTORS, LONG_FACTORS),
                            "ETF": (ETF_SHORT, ETF_MEDIUM, ETF_LONG)}.items():
        sub = tab[tab["type"] == kind]
        if sub.empty:
            continue
        tab.loc[sub.index, "short_score"] = score(sub, s)
        tab.loc[sub.index, "medium_score"] = score(sub, m)
        tab.loc[sub.index, "long_score"] = score(sub, l)
    tab["risks"] = tab.apply(risk_flags, axis=1)
    tab["tax_note"] = [taxmod.note(t, r) for t, r in tab.iterrows()]
    # Growth "what would it take" context: growth rate = blend of revenue growth and 3y price CAGR,
    # capped, used only to show how many years a 2x / 3x would take IF that pace held.
    g = tab[["revenue_growth", "cagr_3y"]].clip(upper=0.6).mean(axis=1, skipna=True)
    tab["yrs_to_2x"] = g.apply(lambda x: years_to_multiple(x, 2))
    tab["yrs_to_3x"] = g.apply(lambda x: years_to_multiple(x, 3))
    return tab


def explain(tab: pd.DataFrame, t: str, horizon: str | None = None) -> str:
    """Plain-English reason: the stock's strongest factors in its best time horizon."""
    r = tab.loc[t]
    sets = ({"short": SHORT_FACTORS, "medium": MEDIUM_FACTORS, "long": LONG_FACTORS} if r["type"] == "Stock"
            else {"short": ETF_SHORT, "medium": ETF_MEDIUM, "long": ETF_LONG})
    val = lambda h: r.get(f"{h}_score") if r.get(f"{h}_score") == r.get(f"{h}_score") else -1  # noqa: E731
    h = horizon or max(sets, key=val)
    sub = tab[tab["type"] == r["type"]]
    ps = []
    for f, w, d in sets[h]:
        if f in sub and sub.loc[t, f] == sub.loc[t, f] and f in discovery.FACTOR_WORDS:
            ps.append(((pd.to_numeric(sub[f], errors="coerce") * d).rank(pct=True)[t], f))
    tops = [discovery.FACTOR_WORDS[f] for p, f in sorted(ps, reverse=True)[:3] if p >= 0.6]
    label = {"short": "short-term", "medium": "medium-term", "long": "long-term"}[h]
    return f"{label} score {val(h):.0f}" + (": " + ", ".join(tops) if tops else "")


# --------------------------------------------------------------------------------------
# Macro / market trend
# --------------------------------------------------------------------------------------
def macro_summary(fred: dict[str, pd.Series]) -> list[dict]:
    out = []
    for sid, label in FRED_SERIES.items():
        s = fred.get(sid)
        if s is None or s.empty:
            continue
        last = s.iloc[-1]
        ago = s[s.index <= s.index[-1] - pd.Timedelta(days=90)]
        chg = last - ago.iloc[-1] if len(ago) else np.nan
        note = ""
        if sid == "T10Y2Y":
            note = "inverted: recession-warning signal" if last < 0 else "positive: curve normal"
        elif sid == "VIXCLS":
            note = "calm" if last < 16 else ("elevated fear" if last > 25 else "normal")
        elif sid == "BAMLH0A0HYM2":
            note = "credit stress" if last > 5 else "credit markets relaxed"
        elif sid == "DEXMAUS":
            note = "ringgit weaker vs 3m ago" if chg > 0 else "ringgit stronger vs 3m ago"
        out.append({"series": label, "id": sid, "latest": last, "date": s.index[-1].date().isoformat(),
                    "chg_3m": chg, "note": note})
    return out


def regime(tab: pd.DataFrame, macro: list[dict]) -> str:
    bits = []
    for t, name in BENCHMARKS.items():
        if t in tab.index:
            r = tab.loc[t]
            trend = "uptrend" if r["pct_vs_sma200"] > 0 and r["golden_cross"] == 1 else (
                "downtrend" if r["pct_vs_sma200"] < 0 else "mixed")
            bits.append(f"{name}: {trend} ({r['ret_3m']:+.1%} 3m, {r['ret_12m']:+.1%} 12m)")
    m = {d["id"]: d for d in macro}
    if "VIXCLS" in m:
        bits.append(f"VIX {m['VIXCLS']['latest']:.1f} ({m['VIXCLS']['note']})")
    if "T10Y2Y" in m:
        bits.append(f"yield curve {m['T10Y2Y']['latest']:+.2f}% ({m['T10Y2Y']['note']})")
    return " · ".join(bits)


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------
def links(t: str) -> str:
    q = html.escape(t)
    yahoo = f"https://finance.yahoo.com/quote/{q}"
    tv_sym = tv_symbol(t)
    l = [f'<a href="{yahoo}" target="_blank">Yahoo</a>']
    if tv_sym:
        l.append(f'<a href="https://www.tradingview.com/symbols/{html.escape(tv_sym.replace(":", "-"))}/" target="_blank">TradingView</a>')
    if t.endswith(".KL"):
        l.append(f'<a href="https://www.bursamalaysia.com/market_information/announcements/company_announcement?keyword={q[:-3]}" target="_blank">Bursa</a>')
    elif not t.startswith("^"):
        l.append(f'<a href="https://finviz.com/quote.ashx?t={q}" target="_blank">Finviz</a>')
        l.append(f'<a href="https://stockanalysis.com/{"etf" if False else "stocks"}/{q.lower()}/" target="_blank">StockAnalysis</a>')
    return " ".join(l)


def pct(x, d=0):
    return "" if x is None or not x == x else f"{x:+.{d}%}" if d else f"{x:+.0%}"


def num(x, d=1):
    return "" if x is None or not x == x else f"{x:.{d}f}"


def mcap_fmt(x):
    if x is None or not x == x:
        return ""
    for div, s in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if x >= div:
            return f"{x / div:.1f}{s}"
    return f"{x:.0f}"


def _num_ok(x):
    return x is not None and x == x


def status(s):
    """Score -> (css class, icon, plain label). Icon + label so colour never carries meaning alone."""
    if not _num_ok(s):
        return "na", "–", "n/a"
    if s >= 75:
        return "good", "▲", "Strong"
    if s >= 50:
        return "ok", "●", "Fair"
    return "weak", "▼", "Weak"


def meter(s, big=False):
    cls, icon, label = status(s)
    if cls == "na":
        return "<span class='muted'>–</span>"
    return (f"<div class='meter {'big' if big else ''}' title='{label}: better than {s:.0f}% of the stocks being watched'>"
            f"<span class='mlabel {cls}'>{icon} {s:.0f}</span><span class='track'><span class='fill {cls}' style='width:{s:.0f}%'></span></span></div>")


def market_of(t):
    return "my" if t.endswith(".KL") else ("us" if not t.startswith("^") else "idx")


FLAG = {"my": "🇲🇾", "us": "🇺🇸", "idx": ""}

TIPS = {
    "Score": "0-100. How this stock compares with everything on your list for this time frame. 75+ = strong.",
    "Why": "The strongest reasons behind the score, in plain words.",
    "1m": "Price change over the last month.", "3m": "Price change over the last 3 months.",
    "12m": "Price change over the last 12 months.",
    "RSI": "Momentum gauge 0-100. Above 70 = may have risen too fast; below 30 = falling hard.",
    "Sales growth": "How fast the company's revenue grew over the last year.",
    "Profit growth": "How fast earnings grew over the last year.",
    "Analyst target": "How far analysts' average price target is above today's price.",
    "Margin": "Gross margin: of every RM1 of sales, how much is left after direct costs. Higher = stronger business.",
    "Size": "Market value of the whole company. Smaller = more room to grow, but riskier.",
    "Doubles in": "Years to double IF recent growth simply continued. Arithmetic, not a forecast.",
    "Fee": "Yearly ETF fee (expense ratio).",
    "Risks": "Warning signs worth checking before buying.",
}


def th(label):
    tip = TIPS.get(label)
    return f"<th title='{html.escape(tip)}'>{label}{' <span class=q>?</span>' if tip else ''}</th>" if tip else f"<th>{label}</th>"


def risk_chips(text):
    if not text:
        return "<span class='chip safe'>✓ no major flags</span>"
    out = []
    for r in text.split("; "):
        cls = "crit" if r.startswith("SHORT-SELLER") else "warn"
        out.append(f"<span class='chip {cls}'>⚠ {html.escape(r)}</span>")
    return " ".join(out)


def detail_html(t, r, news):
    rows = [("Price", num(r.get("price"), 2)), ("1m / 3m / 12m", f"{pct(r.get('ret_1m'))} / {pct(r.get('ret_3m'))} / {pct(r.get('ret_12m'))}"),
            ("RSI", num(r.get("rsi"), 0)), ("vs 50-day / 200-day trend", f"{pct(r.get('pct_vs_sma50'))} / {pct(r.get('pct_vs_sma200'))}"),
            ("From 52-week high", pct(r.get("dist_52w_high"))), ("Sales growth", pct(r.get("revenue_growth"))),
            ("Profit growth", pct(r.get("earnings_growth"))), ("Gross margin", pct(r.get("gross_margin")).lstrip("+")),
            ("Return on equity", pct(r.get("roe"))), ("Free-cash-flow yield", pct(r.get("fcf_yield"), 1)),
            ("P/E now → next year", f"{num(r.get('pe'), 0)} → {num(r.get('fwd_pe'), 0)}"), ("PEG", num(r.get("peg"), 1)),
            ("Debt / equity", num(r.get("debt_to_equity"), 0)), ("Market value", mcap_fmt(r.get("market_cap"))),
            ("Analyst target", pct(r.get("analyst_upside"))), ("Dividend yield", pct(r.get("div_yield"), 1).lstrip("+")),
            ("3-year growth per year", pct(r.get("cagr_3y"))),
            ("Years to 2× / 3× (if growth continued)", f"{num(r.get('yrs_to_2x'), 1)} / {num(r.get('yrs_to_3x'), 1)}")]
    if r.get("type") == "ETF":
        rows += [("Yearly fee", "" if not _num_ok(r.get("expense_ratio")) else f"{r['expense_ratio']:.2f}%"), ("Fund size", mcap_fmt(r.get("aum")))]
    grid = "".join(f"<div><span class='muted'>{k}</span><b>{v or '–'}</b></div>" for k, v in rows if v and v.strip(" /→–"))
    nb = newsmod.ticker_cell(news, t)
    sym = tv_symbol(t)
    live = (f"<div class='tvbox' data-sym='{html.escape(sym)}'><span class='muted small'>Loading live price…</span></div>" if sym
            else "<p class='muted small'>Live chart not available for this stock; use the Yahoo link below.</p>")
    return (f"<h4 class='livehead'>🔴 Live price</h4>{live}<h4 class='livehead'>Numbers from the last update</h4><div class='dgrid'>{grid}</div>"
            f"<p><b>Tax for you (Malaysian resident):</b> {html.escape(r.get('tax_note', ''))}</p>"
            + (f"<p><b>In the news:</b> {nb}</p>" if nb else "")
            + f"<p><b>Research it:</b> {links(t)}</p>")


def pick_table(df, key, cols, top, news, tags, horizon):
    d = df.dropna(subset=[key]).sort_values(key, ascending=False).head(top)
    head = th("Stock") + th("Score") + th("Why") + "".join(th(c[0]) for c in cols) + th("Risks")
    body = []
    for t, r in d.iterrows():
        mk = market_of(t)
        risky = "1" if r.get("risks") else "0"
        search = search_text(t, r)
        others = " ".join(f"<span class='mini'>{lab} {status(r.get(k))[1]} {'' if not _num_ok(r.get(k)) else int(r.get(k))}</span>"
                          for lab, k in (("S", "short_score"), ("M", "medium_score"), ("L", "long_score")) if k != key)
        body.append(
            f"<tr class='row' data-market='{mk}' data-risky='{risky}' data-search='{search}' onclick='toggle(this)'>"
            f"<td><span class='tk'>{FLAG[mk]} {html.escape(t)}</span>{tags.get(t, '')}<br><small class='muted'>{html.escape(str(r.get('name', ''))[:32])}</small></td>"
            f"<td>{meter(r.get(key))}<div class='others'>{others}</div></td>"
            f"<td class='why'>{html.escape(str(r.get('_why_' + horizon, '') or '').split(': ', 1)[-1])}</td>"
            + "".join(f"<td>{fn(t, r)}</td>" for _, fn in cols)
            + f"<td class='risks'>{risk_chips(r.get('risks', ''))}</td></tr>"
            f"<tr class='detail' hidden><td colspan='{len(cols) + 4}'>{detail_html(t, r, news)}</td></tr>")
    if not body:
        return "<p class='muted'>Nothing to show yet.</p>"
    return f"<div class='tablewrap'><table class='picks'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def search_table(df, news, tags):
    """Every scored stock & ETF, so the search box can find anything on the list (not just the top 20)."""
    d = df[df[["short_score", "medium_score", "long_score"]].notna().any(axis=1)].copy()
    d["_best"] = d[["short_score", "medium_score", "long_score"]].max(axis=1)
    body = []
    for t, r in d.sort_values("_best", ascending=False).iterrows():
        mk = market_of(t)
        body.append(
            f"<tr class='row srow' data-market='{mk}' data-risky='{1 if r.get('risks') else 0}' data-search='{search_text(t, r)}' onclick='toggle(this)' hidden>"
            f"<td><span class='tk'>{FLAG[mk]} {html.escape(t)}</span>{tags.get(t, '')}<br><small class='muted'>{html.escape(str(r.get('name', ''))[:32])}</small></td>"
            f"<td>{meter(r.get('short_score'))}</td><td>{meter(r.get('medium_score'))}</td><td>{meter(r.get('long_score'))}</td>"
            f"<td class='risks'>{risk_chips(r.get('risks', ''))}</td></tr>"
            f"<tr class='detail' hidden><td colspan='5'>{detail_html(t, r, news)}</td></tr>")
    return ("<div class='tablewrap'><table class='picks'><thead><tr>" + th("Stock") + "<th>Short term</th><th>Medium term</th><th>Long term</th>"
            + th("Risks") + f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def top_cards(df, key, title, sub, n=25, tags=None):
    horizon = key.split("_")[0]
    d = df.dropna(subset=[key]).sort_values(key, ascending=False).head(n)
    cards = []
    for i, (t, r) in enumerate(d.iterrows(), 1):
        mk = market_of(t)
        risk = r.get("risks", "")
        first_risk = risk.split("; ")[0] if risk else ""
        cards.append(
            f"<div class='card' data-market='{mk}' data-risky='{1 if risk else 0}' data-search='{search_text(t, r)}'>"
            f"<div class='rank'>{i}</div><div class='cbody'><div class='ctop'><span class='tk'>{FLAG[mk]} {html.escape(t)}</span>{(tags or {}).get(t, '')}"
            f"<span class='cname muted'>{html.escape(str(r.get('name', ''))[:26])}</span></div>{meter(r.get(key), True)}"
            f"<div class='why'>{html.escape(str(r.get('_why_' + horizon, '') or '').split(': ', 1)[-1]) or '&nbsp;'}</div>"
            + (f"<span class='chip {'crit' if first_risk.startswith('SHORT') else 'warn'}'>⚠ {html.escape(first_risk)}</span>" if first_risk
               else "<span class='chip safe'>✓ no major flags</span>")
            + "</div></div>")
    return (f"<section class='col'><h3>{title}</h3><p class='muted small'>{sub}</p>{''.join(cards)}"
            "<p class='muted nonefound' hidden>Nothing matches these filters.</p></section>")


def mood(tab, macro):
    """Plain-language market mood from the S&P 500, KLCI and VIX."""
    bits, score = [], 0
    for t, name in BENCHMARKS.items():
        if t in tab.index:
            r = tab.loc[t]
            up = _num_ok(r.get("pct_vs_sma200")) and r["pct_vs_sma200"] > 0
            score += 1 if up else -1
            bits.append(f"{name} {'rising' if up else 'falling'} ({pct(r.get('ret_3m'))} in 3 months)")
    vix = next((m for m in macro if m["id"] == "VIXCLS"), None)
    if vix:
        score += 1 if vix["latest"] < 18 else (-1 if vix["latest"] > 25 else 0)
        bits.append(f"fear gauge (VIX) {vix['latest']:.0f}: {vix['note']}")
    cls, label = (("good", "🟢 Favourable") if score >= 2 else (("weak", "🔴 Cautious") if score <= -1 else ("ok", "🟡 Mixed")))
    advice = {"good": "Trends are up. Normal position sizes are reasonable.",
              "ok": "Signals are mixed. Be selective and size positions smaller.",
              "weak": "Markets are falling. Even high scorers can drop; consider waiting or buying in small steps."}[cls]
    return f"<div class='mood {cls}'><b>Market mood: {label}</b> · {advice}<br><small>{html.escape(' · '.join(bits))}</small></div>"


def render_html(tab: pd.DataFrame, macro: list[dict], top: int, demo: bool, news: dict | None = None,
                shorts: dict | None = None, watchlist: list[str] | None = None, tags: dict | None = None,
                discovery_html: str = "") -> str:
    tags = tags or {}
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%a %d %b %Y, %I:%M %p")
    except Exception:
        now = dt.datetime.now().strftime("%a %d %b %Y, %H:%M")
    stocks = tab[tab["type"] == "Stock"]
    etfs = tab[(tab["type"] == "ETF") & tab["long_score"].notna()]

    short_cols = [("1m", lambda t, r: pct(r["ret_1m"])), ("3m", lambda t, r: pct(r["ret_3m"])), ("RSI", lambda t, r: num(r["rsi"], 0))]
    med_cols = [("12m", lambda t, r: pct(r["ret_12m"])), ("Profit growth", lambda t, r: pct(r["earnings_growth"])),
                ("Analyst target", lambda t, r: pct(r["analyst_upside"]))]
    long_cols = [("Sales growth", lambda t, r: pct(r["revenue_growth"])), ("Margin", lambda t, r: pct(r["gross_margin"]).lstrip("+")),
                 ("Size", lambda t, r: mcap_fmt(r["market_cap"])), ("Doubles in", lambda t, r: "" if not _num_ok(r["yrs_to_2x"]) else f"~{r['yrs_to_2x']:.0f} yrs")]
    etf_cols = [("12m", lambda t, r: pct(r["ret_12m"])), ("Fee", lambda t, r: "" if not _num_ok(r["expense_ratio"]) else f"{r['expense_ratio']:.2f}%"),
                ("Size", lambda t, r: mcap_fmt(r["aum"]))]

    sect = tab[tab.index.isin(SECTOR_ETFS)].sort_values("ret_3m", ascending=False)
    sect_rows = "".join(
        f"<tr><td><b>{SECTOR_ETFS[t]}</b> <small class='muted'>{t}</small></td><td>{pct(r['ret_1m'])}</td><td>{pct(r['ret_3m'])}</td>"
        f"<td>{pct(r['ret_12m'])}</td><td>{'▲ rising' if r['pct_vs_sma200'] > 0 else '▼ falling'}</td></tr>" for t, r in sect.iterrows())
    macro_rows = "".join(
        f"<tr><td>{html.escape(m['series'])}</td><td>{m['latest']:.2f}</td><td>{'' if not m['chg_3m'] == m['chg_3m'] else format(m['chg_3m'], '+.2f')}</td>"
        f"<td>{html.escape(m['note'])}</td><td class='muted'>{m['date']}</td></tr>" for m in macro)
    # live ticker bar: market gauges + your current top picks
    tape_syms = [{"proName": "FOREXCOM:SPXUSD", "title": "S&P 500"}, {"proName": "NASDAQ:NDX", "title": "Nasdaq 100"},
                 {"proName": "MYX:FBMKLCI", "title": "FBM KLCI"}, {"proName": "FX_IDC:USDMYR", "title": "USD/MYR"}]
    seen = set()
    for key in ("short_score", "medium_score", "long_score"):
        for t in stocks.dropna(subset=[key]).sort_values(key, ascending=False).head(5).index:
            sym = tv_symbol(t)
            if sym and t not in seen:
                seen.add(t)
                tape_syms.append({"proName": sym, "title": t.replace(".KL", "")})
    new_n = sum(1 for v in tags.values() if "NEW" in v)
    flagged = sum(1 for t in (watchlist or []) if t.upper() in (shorts or {}))

    tabs = [("top", "⭐ Top picks"), ("short", "Short term"), ("medium", "Medium term"), ("long", "Long term"), ("etf", "ETFs"),
            ("new", f"New & dropped{f' ({new_n})' if new_n else ''}"), ("news", "News"), ("flags", f"Red flags{f' ({flagged})' if flagged else ''}"),
            ("money", "Tax & fees"), ("market", "Market"), ("help", "How it works")]
    nav = "".join(f"<button class='tab' data-tab='{k}' onclick=\"show('{k}')\">{v}</button>" for k, v in tabs)
    demo_banner = "<div class='demo'>DEMO MODE: made-up data for testing, not real prices.</div>" if demo else ""

    def panel(k, inner):
        if "tablewrap" not in inner:  # give tables from the other modules the same scrollable card look
            inner = inner.replace("<table", "<div class='tablewrap'><table").replace("</table>", "</table></div>")
        return f"<section class='panel' id='p-{k}' hidden>{inner}</section>"

    howto = f"""<h2>How to read this page</h2>
<ol class='plain'>
<li><b>Every stock gets three marks out of 100</b>: <b>Short term</b> (next few weeks to 3 months), <b>Medium term</b> (about a year) and <b>Long term</b> (3–10 years).
A mark of 80 means it looks better than 80% of the stocks being watched. It is <i>not</i> an 80% chance of going up.</li>
<li><b>▲ Strong</b> = 75 or more, <b>● Fair</b> = 50–74, <b>▼ Weak</b> = under 50.</li>
<li><b>"Why"</b> lists the top reasons for the mark in plain words.</li>
<li><b>Always check the ⚠ risk tags</b> before buying, especially red "short-seller report" tags.</li>
<li><b>Click any row</b> to see all the numbers, tax notes for a Malaysian investor, and links to Yahoo, TradingView, Finviz and Bursa.</li>
<li>Use the <b>search box</b> and <b>US / Malaysia</b> buttons at the top to narrow the list, and tick <b>Hide risky</b> to show only stocks without warning flags.</li>
<li><span class='badge new'>NEW</span> = found automatically this run. <span class='badge auto'>auto</span> = found automatically earlier. Others are from your own watchlist.</li>
</ol>
<p class='muted'>What goes into each mark: Short = recent price rise, trend, healthy momentum and trading volume. Medium = 12-month momentum, profit and sales growth, analyst targets.
Long = fast sales growth, high margins, real cash generation, sensible price for its growth, low debt, room to grow, and low dividend tax for a Malaysian investor.</p>
<p class='muted'>This is a research tool, not financial advice. No tool can reliably tell which stock will double; most high scorers won't, and some low scorers will.
Data comes from Yahoo Finance (free, sometimes late or wrong, especially for smaller Bursa stocks) and FRED.</p>"""

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My stock picker · {now}</title>
<style>
:root{{--bg:#f6f6f4;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e1e0d9;--accent:#2a78d6;
--good:#0ca30c;--ok:#fab219;--weak:#d03b3b;--warnbg:#fff4dc;--warnink:#7a4a00;--critbg:#fde8e8;--critink:#9b1c1c;--safebg:#e7f6e7;--safeink:#006300}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111110;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--accent:#3987e5;
--warnbg:#3a2c10;--warnink:#f5c76b;--critbg:#3d1717;--critink:#f2a3a3;--safebg:#13301a;--safeink:#7fd67f}}}}
*{{box-sizing:border-box}} body{{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
.wrap{{max-width:1280px;margin:auto;padding:16px 20px 60px}} h1{{margin:0;font-size:26px}} h2{{font-size:20px;margin:28px 0 8px}} h3{{margin:4px 0}}
a{{color:var(--accent)}} .muted{{color:var(--ink2)}} .small{{font-size:13px}}
header .sub{{color:var(--ink2);font-size:14px}}
[hidden]{{display:none!important}}
.tape{{min-height:46px;margin:6px 0}} .tvbox{{min-height:60px;margin-bottom:8px}} h4.livehead{{margin:6px 0;font-size:13px;color:var(--ink2);text-transform:uppercase;letter-spacing:.03em}}
.stale{{background:var(--critbg);color:var(--critink);padding:10px 14px;border-radius:10px;margin:10px 0}}
.demo{{background:#fde68a;color:#0b0b0b;padding:8px 12px;border-radius:8px;margin:10px 0}}
.mood{{margin:12px 0;padding:12px 14px;border-radius:10px;background:var(--card);border-left:6px solid var(--ok)}}
.mood.good{{border-left-color:var(--good)}} .mood.weak{{border-left-color:var(--weak)}}
.bar{{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line)}}
.tabs{{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px}} .tab{{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:7px 12px;border-radius:999px;cursor:pointer;white-space:nowrap;font-size:14px}}
.tab.on{{background:var(--ink);color:var(--bg);border-color:var(--ink)}}
.filters{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:4px}}
.filters input[type=search]{{flex:1;min-width:180px;padding:8px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);font-size:15px}}
.seg button{{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:7px 11px;cursor:pointer;font-size:14px}}
.seg button:first-child{{border-radius:8px 0 0 8px}} .seg button:last-child{{border-radius:0 8px 8px 0}} .seg button.on{{background:var(--accent);color:#fff;border-color:var(--accent)}}
label.hide{{font-size:14px;color:var(--ink2);cursor:pointer}}
.cols{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:14px}} @media (max-width:900px){{.cols{{grid-template-columns:1fr}}}}
.card{{display:flex;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:8px 0}}
.rank{{font-weight:700;color:var(--ink2);min-width:18px}} .cbody{{flex:1;min-width:0}} .ctop{{display:flex;gap:6px;align-items:baseline;flex-wrap:wrap}}
.cname{{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} .tk{{font-weight:700}}
.why{{font-size:13px;color:var(--ink2);margin:4px 0}}
.meter{{display:flex;align-items:center;gap:8px;min-width:120px}} .meter .track{{flex:1;height:6px;background:var(--line);border-radius:3px;overflow:hidden}}
.meter .fill{{display:block;height:100%;border-radius:0 3px 3px 0}} .fill.good{{background:var(--good)}} .fill.ok{{background:var(--ok)}} .fill.weak{{background:var(--weak)}}
.mlabel{{font-weight:700;font-size:13px;min-width:44px;color:var(--ink)}} .meter.big .mlabel{{font-size:15px}}
.others{{margin-top:3px}} .mini{{font-size:11px;color:var(--ink2);margin-right:6px}}
.chip{{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;margin:2px 2px 0 0}}
.chip.warn{{background:var(--warnbg);color:var(--warnink)}} .chip.crit{{background:var(--critbg);color:var(--critink);font-weight:600}} .chip.safe{{background:var(--safebg);color:var(--safeink)}}
.badge{{font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;vertical-align:middle}} .badge.new{{background:var(--accent);color:#fff}} .badge.auto{{border:1px solid var(--line);color:var(--ink2)}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:var(--ink2);background:var(--card);white-space:nowrap}} th .q{{font-size:10px;border:1px solid var(--line);border-radius:50%;padding:0 4px}}
tr.row{{cursor:pointer}} tr.row:hover{{background:rgba(42,120,214,.06)}} td.why{{max-width:280px;font-size:13px;color:var(--ink2)}} td.risks{{max-width:260px}}
tr.detail td{{background:var(--bg)}} .dgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 16px;font-size:13px}}
.dgrid div{{display:flex;justify-content:space-between;gap:8px;border-bottom:1px dashed var(--line);padding:2px 0}}
.tablewrap td:last-child small{{display:block;min-width:240px}} .tablewrap + .tablewrap{{margin-top:12px}}
.desc{{color:var(--ink2);font-size:14px;max-width:900px}} ol.plain li{{margin:6px 0}} .b{{font-weight:600}} .hi{{color:var(--safeink)}}
.disc{{font-size:12px;color:var(--ink2);border-top:1px solid var(--line);margin-top:40px;padding-top:10px}}
</style></head><body data-generated="{int(time.time())}"><div class="wrap">
<header><h1>📈 My stock picker</h1><div class="sub">Scores updated {now} (Malaysia time) · refreshed every hour while markets are open · prices in the ticker bar and in each stock's details are live · {len(stocks)} stocks & {len(etfs)} ETFs checked · Research only, not financial advice</div></header>
{demo_banner}
<div id='tape' class='tape' data-syms='{html.escape(json.dumps(tape_syms))}'></div>
<div class='stale' id='stale' hidden></div>
{mood(tab, macro)}
<div class="bar"><div class="tabs">{nav}</div>
<div class="filters"><input type="search" id="q" placeholder="Search a stock, e.g. NVDA, Maybank, healthcare…" oninput="filter()">
<span class="seg" id="mk"><button class="on" data-m="all" onclick="mk(this)">All</button><button data-m="us" onclick="mk(this)">🇺🇸 US</button><button data-m="my" onclick="mk(this)">🇲🇾 Malaysia</button></span>
<label class="hide"><input type="checkbox" id="safe" onchange="filter()"> Hide risky</label></div></div>

{panel("top", f"<p class='desc'>The five highest-scoring stocks for each time frame. Click a tab above for the full lists, or a stock there for details.</p><div class='cols'>"
      + top_cards(stocks, "short_score", "⚡ Short term", "Next few weeks to 3 months: price momentum", tags=tags)
      + top_cards(stocks, "medium_score", "📅 Medium term", "About a year: momentum plus growing profits", tags=tags)
      + top_cards(stocks, "long_score", "🌱 Long term", "3–10 years: businesses that can keep compounding", tags=tags)
      + "</div>" + (f"<h2>Top ETFs</h2><div class='cols'>" + top_cards(etfs, "long_score", "Best long-term ETFs", "Steady growth, low fees", 5, tags) + "</div>" if len(etfs) else ""))}
{panel("short", "<h2>⚡ Short-term picks <small class='muted'>(weeks to 3 months)</small></h2><p class='desc'>Stocks with strong recent momentum that aren't overheated yet. Short-term moves are the least predictable, so use small positions and a stop-loss.</p>" + pick_table(stocks, "short_score", short_cols, top, news, tags, "short"))}
{panel("medium", "<h2>📅 Medium-term picks <small class='muted'>(3 to 18 months)</small></h2><p class='desc'>Rising for a year and backed by growing profits and analyst expectations.</p>" + pick_table(stocks, "medium_score", med_cols, top, news, tags, "medium"))}
{panel("long", "<h2>🌱 Long-term picks <small class='muted'>(3 to 10 years)</small></h2><p class='desc'>The traits most big long-term winners share: fast-growing sales, high margins, real cash, sensible price, low debt and room to grow. “Doubles in” is simple arithmetic if growth continued, not a promise.</p>" + pick_table(stocks, "long_score", long_cols, top, news, tags, "long"))}
{panel("etf", "<h2>ETFs</h2><p class='desc'>ETFs are baskets of many stocks: lower risk than single stocks. For US-listed ETFs, check the Tax & fees tab for cheaper-tax Irish (UCITS) versions.</p>" + pick_table(etfs, "long_score", etf_cols, top, news, tags, "long"))}
{panel("new", discovery_html or "<p class='desc'>Automatic discovery was switched off for this run.</p>")}
{panel("news", (newsmod.render(news) if news else "<p class='desc'>News scan was skipped this run.</p>"))}
{panel("flags", shortsellers.render(shorts or {}, watchlist or []))}
{panel("money", taxmod.render_playbook() + feesmod.render())}
{panel("market", f"<h2>Which sectors are hot</h2><p class='desc'>Where money has been flowing over the last 3 months.</p><div class='tablewrap'><table><thead><tr><th>Sector</th><th>1m</th><th>3m</th><th>12m</th><th>Long-term trend</th></tr></thead><tbody>{sect_rows}</tbody></table></div>"
      f"<h2>Economy dashboard</h2><div class='tablewrap'><table><thead><tr><th>Measure</th><th>Latest</th><th>Change, 3m</th><th>What it means</th><th>As of</th></tr></thead><tbody>{macro_rows}</tbody></table></div>"
      "<p class='desc'>Go deeper: <a href='https://tradingeconomics.com/calendar' target='_blank'>Economic calendar</a> · <a href='https://tradingeconomics.com/malaysia/indicators' target='_blank'>Malaysia indicators</a> · "
      "<a href='https://finviz.com/map.ashx' target='_blank'>Finviz heat map</a> · <a href='https://www.tradingview.com/markets/stocks-malaysia/market-movers-gainers/' target='_blank'>Bursa top movers</a> · "
      "<a href='https://www.bursamalaysia.com/market_information/announcements/company_announcement' target='_blank'>Bursa announcements</a></p>")}
{panel("help", howto)}
<section class='panel' id='p-search' hidden><h2 id='stitle'>Search results</h2>
<p class='desc' id='snone' hidden>No stock on your list matches that. Try the ticker (e.g. NVDA, 1155) or a short name (e.g. Maybank). To add a new stock, put its Yahoo symbol in watchlist.txt.</p>
{search_table(pd.concat([stocks, etfs]), news, tags)}</section>
<p class="disc">Research tool, not financial or tax advice. Scores rank evidence, not certainty. Size positions so one wrong pick can't hurt you badly.</p>
</div>
<script>
var current='top', market='all';
function show(k){{current=k;var q=document.getElementById('q');if(q&&q.value){{q.value='';filter();}}
document.querySelectorAll('.panel').forEach(function(p){{p.hidden=p.id!=='p-'+k;}});
document.querySelectorAll('.tab').forEach(function(b){{b.classList.toggle('on',b.dataset.tab===k);}});
try{{history.replaceState(null,'','#'+k);}}catch(e){{}}}}
function mk(b){{market=b.dataset.m;document.querySelectorAll('#mk button').forEach(function(x){{x.classList.toggle('on',x===b);}});filter();}}
function filter(){{
  var q=document.getElementById('q').value.trim().toLowerCase().replace(/^\$/,'');var safe=document.getElementById('safe').checked;
  var words=q.split(/\s+/).filter(Boolean);
  function match(el){{return (market==='all'||el.dataset.market===market)&&(!safe||el.dataset.risky==='0')&&words.every(function(w){{return el.dataset.search.indexOf(w)>=0;}});}}
  if(words.length){{  // searching: show one results list covering every stock on the list
    document.querySelectorAll('.panel').forEach(function(p){{p.hidden=p.id!=='p-search';}});
    document.querySelectorAll('.tab').forEach(function(b){{b.classList.remove('on');}});
    var n=0;document.querySelectorAll('tr.srow').forEach(function(el){{var ok=match(el);el.hidden=!ok;if(!ok)el.nextElementSibling.hidden=true;if(ok)n++;}});
    document.getElementById('stitle').textContent=n+' result'+(n===1?'':'s')+' for "'+q+'"';document.getElementById('snone').hidden=n>0;return;}}
  document.querySelectorAll('.panel').forEach(function(p){{p.hidden=p.id!=='p-'+current;}});
  document.querySelectorAll('.tab').forEach(function(b){{b.classList.toggle('on',b.dataset.tab===current);}});
  document.querySelectorAll('tr.row:not(.srow), .card').forEach(function(el){{var ok=match(el);el.hidden=!ok;if(el.tagName==='TR'&&!ok)el.nextElementSibling.hidden=true;}});
  topFive();}}
function topFive(){{document.querySelectorAll('.col').forEach(function(c){{var i=0;c.querySelectorAll('.card').forEach(function(el){{
  if(el.hidden)return; i++; if(i>5){{el.hidden=true;}} else {{el.querySelector('.rank').textContent=i;}}}});
  var none=c.querySelector('.nonefound'); if(none) none.hidden=i>0;}});}}
function toggle(tr){{var d=tr.nextElementSibling;d.hidden=!d.hidden;if(!d.hidden)loadLive(d);}}
function tvWidget(box,kind,cfg){{  // TradingView's free embeddable widgets: live prices straight from TradingView
  var c=document.createElement('div');c.className='tradingview-widget-container';var w=document.createElement('div');w.className='tradingview-widget-container__widget';c.appendChild(w);
  var sc=document.createElement('script');sc.src='https://s3.tradingview.com/external-embedding/embed-widget-'+kind+'.js';sc.async=true;sc.text=JSON.stringify(cfg);c.appendChild(sc);box.innerHTML='';box.appendChild(c);}}
var dark=window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches;
function loadLive(d){{var box=d.querySelector('.tvbox');if(!box||box.dataset.done)return;box.dataset.done=1;
  tvWidget(box,'symbol-overview',{{symbols:[[box.dataset.sym.split(':').pop(),box.dataset.sym+'|1D']],chartOnly:false,width:'100%',height:300,locale:'en',colorTheme:dark?'dark':'light',
    autosize:false,showVolume:false,hideDateRanges:false,scalePosition:'right',chartType:'area',dateRanges:['1d|5','1m|30','3m|60','12m|1D','60m|1W','all|1M']}});}}
(function(){{var t=document.getElementById('tape');if(!t)return;var syms=JSON.parse(t.dataset.syms);
  tvWidget(t,'ticker-tape',{{symbols:syms,showSymbolLogo:true,isTransparent:true,displayMode:'adaptive',colorTheme:dark?'dark':'light',locale:'en'}});}})();
(function(){{var gen=+document.body.dataset.generated*1000, hrs=(Date.now()-gen)/36e5, el=document.getElementById('stale');
  var when=new Date(gen).toLocaleString([], {{weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'}});
  if(hrs>72){{el.innerHTML='<b>⚠ This report is out of date</b> (last updated '+when+', '+Math.round(hrs/24*10)/10+' days ago). '+
    'Automatic updates may have stopped. On GitHub, open the <b>Actions</b> tab and click <b>Run workflow</b>, or ask Claude to check.';el.hidden=false;}}}})();
var h=(location.hash||'#top').slice(1);show(document.getElementById('p-'+h)?h:'top');topFive();
</script></body></html>"""


# --------------------------------------------------------------------------------------
# Demo data (lets you test the pipeline offline)
# --------------------------------------------------------------------------------------
def demo_data(tickers):
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(end=dt.date.today(), periods=756)
    prices, infos = {}, {}
    for t in tickers:
        drift, vol = rng.normal(0.0004, 0.0008), rng.uniform(0.01, 0.035)
        c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, len(idx))))
        prices[t] = pd.DataFrame({"Close": c, "Volume": rng.integers(1e5, 1e7, len(idx))}, index=idx)
        etf = t in SECTOR_ETFS or t in {"SPY", "QQQ", "VTI", "VOO", "VT", "SCHD", "VGT", "SOXX", "ARKK", "IWM"}
        mcap = 10 ** rng.uniform(9, 12.5)
        infos[t] = {"shortName": f"Demo {t}", "quoteType": "ETF" if etf else ("INDEX" if t.startswith("^") else "EQUITY"),
                    "marketCap": mcap, "revenueGrowth": rng.normal(0.12, 0.15), "earningsGrowth": rng.normal(0.15, 0.3),
                    "grossMargins": rng.uniform(0.2, 0.85), "returnOnEquity": rng.normal(0.18, 0.15),
                    "freeCashflow": mcap * rng.normal(0.03, 0.03), "trailingPE": rng.uniform(8, 90),
                    "forwardPE": rng.uniform(8, 60), "trailingPegRatio": rng.uniform(0.5, 4),
                    "debtToEquity": rng.uniform(0, 250), "heldPercentInsiders": rng.uniform(0, 0.3),
                    "currentPrice": c[-1], "targetMeanPrice": c[-1] * rng.uniform(0.85, 1.4),
                    "recommendationMean": rng.uniform(1.5, 3.5), "beta": rng.uniform(0.5, 2.2),
                    "netExpenseRatio": rng.uniform(0.03, 0.75), "totalAssets": 10 ** rng.uniform(8, 11),
                    "fiveYearAverageReturn": rng.normal(0.1, 0.05)}
    fred = {sid: pd.Series(rng.normal(2, 0.3, 400), index=pd.bdate_range(end=dt.date.today(), periods=400))
            for sid in FRED_SERIES}
    return prices, infos, fred


def demo_candidates():
    srcs = ["yahoo:hidden_growers", "OpenInsider: several insiders buying their own shares",
            "Dataroma: recently bought by a well-known 'superinvestor' fund", "Mentioned in Motley Fool articles",
            "Finviz growth + uptrend screen", "yahoo:my_growth"]
    return {f"DMO{i}" if i % 5 else f"{9000 + i}.KL": {srcs[i % 6], srcs[(i * 3) % 6]} for i in range(24)}


# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watchlist", default=str(HERE / "watchlist.txt"))
    ap.add_argument("--out", default=str(HERE / "reports"))
    ap.add_argument("--top", type=int, default=20, help="rows per table")
    ap.add_argument("--demo", action="store_true", help="synthetic data, no internet needed")
    ap.add_argument("--no-news", action="store_true", help="skip the news & sentiment layer")
    ap.add_argument("--no-transcripts", action="store_true", help="skip YouTube transcripts (faster)")
    ap.add_argument("--no-discover", action="store_true", help="don't search for new stocks this run")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pinned = read_watchlist(Path(a.watchlist))
    state_path = out / "auto_watchlist.json"
    state = discovery.load_state(state_path)
    auto = [t for t in state if t not in pinned]
    core = pinned + auto
    extra = [t for t in list(SECTOR_ETFS) + list(BENCHMARKS) if t not in core]

    candidates: dict[str, set] = {}
    if not a.no_discover:
        print("Looking for new stocks to watch…", file=sys.stderr)
        candidates = (demo_candidates() if a.demo else discovery.collect(set(core) | set(extra)))
        candidates = {t: v for t, v in candidates.items() if t not in core and t not in extra}
    all_t = core + extra + [t for t in candidates if t not in core + extra]

    if a.demo:
        prices, infos, fred = demo_data(all_t)
        for i, t in enumerate(candidates):  # make some demo candidates penny stocks / illiquid to exercise filters
            if i % 4 == 0:
                prices[t]["Close"] = prices[t]["Close"] / 60
            if i % 4 == 1:
                prices[t]["Volume"] = prices[t]["Volume"] // 1000
    else:
        print(f"Downloading prices for {len(all_t)} tickers…", file=sys.stderr)
        prices = fetch_prices(all_t)
        missing = sorted(set(core) - set(prices))
        if missing:
            print(f"  no price data for: {', '.join(missing)}", file=sys.stderr)
        if len([t for t in core if t in prices]) < max(5, len(core) // 3):
            print("OFFLINE: could not download prices (no internet?). Keeping the previous report.", file=sys.stderr)
            sys.exit(3)

    # Penny-stock / liquidity / size filters for new candidates (price & volume first: cheap)
    rejected: dict[str, str] = {}
    for t in list(candidates):
        if t not in prices:
            rejected[t] = "no price data"
            continue
        ok, why = discovery.passes_filters(t, prices[t], {})
        if not ok:
            rejected[t] = why
    survivors = [t for t in candidates if t not in rejected]

    if not a.demo:
        print("Fetching fundamentals (cached ~20h)…", file=sys.stderr)
        infos = fetch_info([t for t in prices if not t.startswith("^") and (t not in candidates or t in survivors)],
                           out / ".info_cache.json")
        print("Fetching macro data from FRED…", file=sys.stderr)
        fred = {sid: s for sid in FRED_SERIES if (s := fetch_fred(sid)) is not None}
    for t in list(survivors):  # size filter needs market cap
        ok, why = discovery.passes_filters(t, prices[t], infos.get(t, {}))
        if ok and str(infos.get(t, {}).get("quoteType", "EQUITY")).upper() not in ("EQUITY", ""):
            ok, why = False, "not a regular stock"
        if not ok:
            rejected[t] = why
            survivors.remove(t)

    # Score your watchlist + auto picks + surviving candidates together, so candidates are judged against them.
    pool = [t for t in core + survivors if t in prices]
    ranked = build_table({t: prices[t] for t in pool}, infos)
    for t in survivors:
        if t in ranked.index:
            ranked.loc[t, "_why"] = explain(ranked, t)
    shorts = shortsellers.load(live=not a.demo)
    today = dt.date.today().isoformat()
    log = discovery.update(state, ranked, {t: candidates[t] for t in survivors}, rejected, today, shorts)
    for t, d in state.items():  # refresh plain-English reasons for existing auto picks
        if t in ranked.index and t not in [x[0] for x in log["added"]]:
            d["why"] = explain(ranked, t)
    if not a.no_discover:
        discovery.save_state(state_path, state)

    tickers = pinned + [t for t in state if t not in pinned]
    ranked = ranked[ranked.index.isin(tickers)]
    tab = build_table({t: prices[t] for t in tickers + extra if t in prices}, infos)
    for c in ("short_score", "medium_score", "long_score"):
        tab[c] = ranked[c].reindex(tab.index)
    for h in ("short", "medium", "long"):
        tab[f"_why_{h}"] = [explain(ranked, t, h) if t in ranked.index and ranked.loc[t, "type"] in ("Stock", "ETF") else ""
                            for t in tab.index]
    new_set = {x[0] for x in log["added"]}
    tags = {t: (" <span class='badge new'>NEW</span>" if t in new_set else " <span class='badge auto'>auto</span>")
            for t in state}
    macro = macro_summary(fred)
    news = None
    if not a.no_news and not a.demo:
        print("Scanning news & videos…", file=sys.stderr)
        news = newsmod.collect({t: str(tab.loc[t, "name"]) for t in tickers if t in tab.index},
                               with_transcripts=not a.no_transcripts)
        (out / "news_latest.json").write_text(json.dumps(news, default=str)[:5_000_000])
    for df_ in (tab, ranked):
        df_["risks"] = ["; ".join(x for x in (shortsellers.flag(shorts, t), r) if x) for t, r in df_["risks"].items()]
    report = render_html(tab, macro, a.top, a.demo, news, shorts, tickers, tags,
                         discovery.render(log, state) if not a.no_discover else "")

    stamp = dt.date.today().isoformat()
    (out / "latest.html").write_text(report)
    (out / f"picks_{stamp}.html").write_text(report)
    cols = ["name", "type", "sector", "short_score", "medium_score", "long_score", "price", "ret_1m", "ret_3m",
            "ret_12m", "rsi", "revenue_growth", "earnings_growth", "gross_margin", "roe", "fcf_yield", "pe",
            "fwd_pe", "peg", "market_cap", "analyst_upside", "yrs_to_2x", "yrs_to_3x", "div_yield", "tax_drag", "risks", "tax_note"]
    ranked[[c for c in cols if c in ranked]].sort_values("long_score", ascending=False).to_csv(out / f"scores_{stamp}.csv")

    st = ranked[ranked["type"] == "Stock"]
    for k, lab in (("short_score", "SHORT"), ("medium_score", "MEDIUM"), ("long_score", "LONG")):
        top5 = st[k].dropna().sort_values(ascending=False).head(5)
        print(f"{lab:6} top 5: " + ", ".join(f"{t} ({v:.0f})" for t, v in top5.items()))
    if not a.no_discover:
        print(f"Watchlist: +{len(log['added'])} added ({', '.join(x[0] for x in log['added']) or '-'}), "
              f"-{len(log['dropped'])} dropped; {len(rejected)} candidates filtered out")
    print(f"\nReport: {out / 'latest.html'}")


if __name__ == "__main__":
    main()
