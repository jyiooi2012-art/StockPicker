"""
Short-seller red-flag layer (Culper Research and others).

Activist short sellers publish allegations (accounting problems, fraud, inflated users...) and
PROFIT IF THE PRICE FALLS, so their reports are biased by design. They are sometimes right
and sometimes wrong. We never score on them; we flag the stock so you read both sides first.

Sources:
  1. short_reports.csv next to this file. Add a line whenever you see a new report
     (ticker,firm,date,headline,url). Works for any firm: Culper, Muddy Waters, Spruce Point,
     Grizzly, Wolfpack, Kerrisdale, Fuzzy Panda, Blue Orca, etc.
  2. Best-effort live scan of culperresearch.com: any "(NASDAQ: XXX)"-style ticker on the
     site's pages is added. The site sits behind a disclaimer page, so this may find nothing;
     the CSV is the reliable path.
"""
from __future__ import annotations

import csv
import html
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CULPER_PAGES = ["https://culperresearch.com/", "https://culperresearch.com/latest-research",
                "https://culperresearch.com/research-archive"]
TICKER_RE = re.compile(r"\((?:NASDAQ|Nasdaq|NYSE(?: American)?|NYSEAMERICAN|TSX|TSXV|ASX|OTC|LSE)\s*:\s*([A-Z][A-Z.\-]{0,6})\)")


def load(csv_path: Path = HERE / "short_reports.csv", live: bool = True) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip().upper()
                if t:
                    out.setdefault(t, []).append(r)
    if live:
        for url in CULPER_PAGES:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research script)"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    text = r.read().decode("utf-8", "ignore")
            except Exception as e:
                print(f"  ! Culper page {url}: {e}", file=sys.stderr)
                continue
            for t in set(TICKER_RE.findall(text)):
                if t not in out:
                    out[t] = [{"ticker": t, "firm": "Culper Research", "date": "", "headline": "Listed on culperresearch.com",
                               "url": url}]
    return out


def flag(reports: dict, ticker: str) -> str:
    rs = reports.get(ticker.upper())
    if not rs:
        return ""
    r = rs[-1]
    return f"SHORT-SELLER REPORT: {r.get('firm', '?')} {r.get('date', '')}".strip()


def render(reports: dict, watchlist: list[str]) -> str:
    hits = [(t, r) for t in watchlist for r in reports.get(t.upper(), [])]
    rows = "".join(
        f"<tr><td><b>{html.escape(t)}</b></td><td>{html.escape(r.get('firm', ''))}</td><td>{html.escape(r.get('date', ''))}</td>"
        f"<td>{html.escape(r.get('headline', ''))}</td><td><a href='{html.escape(r.get('url', ''))}' target='_blank'>source</a></td></tr>"
        for t, r in hits)
    others = sorted(set(reports) - {t.upper() for t in watchlist})
    body = (f"<table><thead><tr><th>Ticker</th><th>Firm</th><th>Date</th><th>Allegation</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
            if rows else "<p class='desc'>None of your watchlist stocks have a short-seller report on file.</p>")
    return f"""<h2>Short-seller red flags (Culper Research & others)</h2>
<p class="desc">Activist short sellers profit when a stock falls, so treat their reports as one-sided allegations, not verdicts.
A flagged stock is not excluded from the scores; read the report <i>and</i> the company's response before buying.
<a href="https://culperresearch.com/" target="_blank">Culper Research</a> · add new reports to <code>short_reports.csv</code>.</p>
{body}<p class="desc">Other tracked targets not in your watchlist: {html.escape(", ".join(others)) or "none"}</p>"""
