"""
News & sentiment layer: kept SEPARATE from the quantitative scores on purpose.

Sources (all free, no API keys):
  * YouTube channel RSS feeds (Bloomberg Television, Bloomberg Originals, CNBC, Yahoo Finance)
    + optional transcripts via `pip install youtube-transcript-api`
  * Free article RSS feeds (CNBC, MarketWatch, Yahoo Finance, The Star, Malay Mail, The Edge, Investing.com)
  * Per-ticker headlines from Yahoo Finance (via yfinance)

It produces: market themes (what the news is talking about, and whether the tone is
positive or negative), and per-ticker "buzz" + tone. Edit FEEDS / THEMES freely.
Note: Bloomberg.com articles are paywalled; we use Bloomberg's free YouTube videos instead.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

YOUTUBE_CHANNELS = {
    "Bloomberg Television": "UCIALMKvObZNtJ6AmdCLP7Lg",
    "Bloomberg Originals": "UCUMZ7gohGI9HcU9VNsr2FJQ",
    "CNBC Television": "UCrp_UI8XtuYfpiqluWLD7Lg",
    "Yahoo Finance": "UCEAZeUIeJs0IjQiqTCdVSIg",
}
ARTICLE_FEEDS = {
    "CNBC Markets": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    "CNBC Top News": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Investing.com": "https://www.investing.com/rss/news_25.rss",
    "The Star Business": "https://www.thestar.com.my/rss/business/business-news",
    "Malay Mail Money": "https://www.malaymail.com/feed/rss/money",
    "The Edge Malaysia": "https://theedgemalaysia.com/rss",
}

# theme -> keywords (lower-case, matched as whole words / phrases)
THEMES = {
    "AI & data centres": ["ai", "artificial intelligence", "data center", "data centre", "nvidia", "gpu", "openai", "hyperscaler"],
    "Semiconductors": ["chip", "chips", "semiconductor", "tsmc", "foundry", "wafer"],
    "Interest rates & Fed": ["fed", "rate cut", "rate hike", "powell", "interest rate", "yields", "treasury", "fomc", "opr", "bank negara", "bnm"],
    "Inflation": ["inflation", "cpi", "pce", "price pressures"],
    "Trade & tariffs": ["tariff", "tariffs", "trade war", "export controls", "sanctions"],
    "China": ["china", "chinese", "beijing", "yuan"],
    "Oil & energy": ["oil", "crude", "opec", "brent", "natural gas", "lng", "petronas"],
    "Recession / jobs": ["recession", "layoffs", "jobless", "unemployment", "payrolls", "slowdown"],
    "Earnings": ["earnings", "revenue", "guidance", "profit", "quarterly results"],
    "Crypto": ["bitcoin", "crypto", "ethereum", "stablecoin"],
    "Malaysia & ringgit": ["malaysia", "ringgit", "bursa", "klci", "johor", "budget 2026", "budget 2027"],
    "Healthcare & GLP-1": ["glp-1", "obesity drug", "weight-loss", "fda", "biotech"],
    "EV & clean energy": ["ev", "electric vehicle", "solar", "battery", "renewable"],
    "Gold": ["gold", "bullion"],
}

# Small finance-tuned lexicon (inspired by Loughran-McDonald). Headline-level only: crude but useful in aggregate.
POS = set("""beat beats surge surges soar soars rally rallies jump jumps gain gains record upgrade upgraded
strong stronger growth boost boosts rebound rebounds outperform bullish optimism optimistic profit profits
raise raises raised expands expansion approve approved approval wins win breakthrough top tops buy
accelerate accelerates recovery recover beat-expectations upbeat higher high rises rise climbs climb""".split())
NEG = set("""miss misses plunge plunges slump slumps fall falls drop drops tumble tumbles crash crashes
downgrade downgraded weak weaker warning warns loss losses lawsuit probe investigation recall fraud bearish
fear fears selloff sell-off decline declines slowdown recession layoffs bankruptcy default risk risks
concern concerns lower low sinks sink slide slides tariff tariffs ban halt delays delay""".split())

UA = {"User-Agent": "Mozilla/5.0 (stock-picker research script)"}


def _get(url: str, timeout: int = 15) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except Exception as e:
        print(f"  ! feed failed {url[:60]}: {e}", file=sys.stderr)
        return None


def _parse_feed(raw: bytes, source: str, kind: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    for it in root.iter("item"):  # RSS
        items.append({"source": source, "kind": kind, "title": (it.findtext("title") or "").strip(),
                      "link": (it.findtext("link") or "").strip(), "date": it.findtext("pubDate") or "",
                      "summary": re.sub("<[^>]+>", "", it.findtext("description") or "")[:400]})
    for e in root.findall("a:entry", ns):  # Atom (YouTube)
        link = e.find("a:link", ns)
        desc = e.find("media:group/media:description", ns)
        items.append({"source": source, "kind": kind, "title": (e.findtext("a:title", "", ns) or "").strip(),
                      "link": link.get("href") if link is not None else "", "date": e.findtext("a:published", "", ns),
                      "video_id": e.findtext("yt:videoId", "", ns),
                      "summary": (desc.text or "")[:400] if desc is not None else ""})
    return items


def transcript(video_id: str, max_chars: int = 6000) -> str:
    """Optional: needs `pip install youtube-transcript-api`. Returns '' if unavailable."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:  # v1.x API
            fetched = YouTubeTranscriptApi().fetch(video_id)
            text = " ".join(s.text for s in fetched)
        except AttributeError:  # older 0.x API
            text = " ".join(s["text"] for s in YouTubeTranscriptApi.get_transcript(video_id))
        return text[:max_chars]
    except Exception:
        return ""


def tone(text: str) -> float:
    words = re.findall(r"[a-z][a-z\-]+", text.lower())
    p = sum(w in POS for w in words)
    n = sum(w in NEG for w in words)
    return 0.0 if p + n == 0 else (p - n) / (p + n)


def _has(text: str, kw: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text) is not None


def collect(watch: dict[str, str], max_videos: int = 15, with_transcripts: bool = True,
            ticker_news: bool = True) -> dict:
    """watch: {ticker: company short name}. Returns a dict the report can render."""
    items: list[dict] = []
    for name, cid in YOUTUBE_CHANNELS.items():
        raw = _get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
        if raw:
            items += _parse_feed(raw, name, "video")
    for name, url in ARTICLE_FEEDS.items():
        raw = _get(url)
        if raw:
            items += _parse_feed(raw, name, "article")

    if with_transcripts:
        vids = [i for i in items if i.get("video_id")][:max_videos]
        got = 0
        for v in vids:
            v["transcript"] = transcript(v["video_id"])
            got += bool(v["transcript"])
        print(f"  transcripts: {got}/{len(vids)} videos", file=sys.stderr)

    if ticker_news:
        try:
            import yfinance as yf
            for t in watch:
                if t.startswith("^"):
                    continue
                try:
                    for n in (yf.Ticker(t).news or [])[:8]:
                        c = n.get("content", n)
                        url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link", "")
                        items.append({"source": "Yahoo ticker news", "kind": "article", "title": c.get("title", ""),
                                      "link": url or "", "date": c.get("pubDate", ""), "summary": c.get("summary", "")[:400],
                                      "ticker_hint": t})
                except Exception:
                    pass
        except ImportError:
            pass

    # de-duplicate by title
    seen, uniq = set(), []
    for i in items:
        k = i["title"].lower()[:90]
        if k and k not in seen:
            seen.add(k)
            uniq.append(i)
    items = uniq

    themes = defaultdict(lambda: {"count": 0, "tone": 0.0, "examples": []})
    per_ticker = defaultdict(lambda: {"mentions": 0, "tone": 0.0, "headlines": []})
    aliases = {t: _aliases(t, n) for t, n in watch.items()}
    for i in items:
        text = f"{i['title']} {i.get('summary', '')} {i.get('transcript', '')}".lower()
        tn = tone(text)
        i["tone"] = tn
        for th, kws in THEMES.items():
            hits = sum(_has(text, k) for k in kws)
            if hits:
                d = themes[th]
                d["count"] += 1
                d["tone"] += tn
                if len(d["examples"]) < 3:
                    d["examples"].append(i)
        for t, al in aliases.items():
            if i.get("ticker_hint") == t or any(_has(text, a) for a in al):
                d = per_ticker[t]
                d["mentions"] += 1
                d["tone"] += tn
                if len(d["headlines"]) < 3:
                    d["headlines"].append(i)
    for d in list(themes.values()) + list(per_ticker.values()):
        n = d.get("count") or d.get("mentions")
        d["tone"] = d["tone"] / n if n else 0.0
    return {"generated": dt.datetime.now().isoformat(timespec="minutes"), "n_items": len(items),
            "n_videos": sum(i["kind"] == "video" for i in items),
            "themes": dict(sorted(themes.items(), key=lambda kv: -kv[1]["count"])),
            "tickers": dict(per_ticker),
            "latest_videos": [i for i in items if i["kind"] == "video"][:12]}


_GENERIC = {"inc", "corp", "corporation", "holdings", "group", "berhad", "bhd", "company", "co", "ltd", "plc",
            "the", "class", "platforms", "technologies", "technology", "international", "limited", "etf", "trust"}


def _aliases(ticker: str, name: str) -> list[str]:
    al = []
    base = ticker.split(".")[0].lower()
    if not ticker.endswith(".KL") and len(base) >= 3 and base not in POS | NEG | {"now", "net", "app", "low", "all"}:
        al.append(base)
    words = [w for w in re.findall(r"[a-z][a-z&\-]+", (name or "").lower()) if w not in _GENERIC]
    if words and len(words[0]) >= 4:
        al.append(words[0])
    return al


def tone_label(x: float) -> str:
    return "positive" if x > 0.15 else ("negative" if x < -0.15 else "mixed")


def render(news: dict) -> str:
    if not news:
        return ""
    rows = "".join(
        f"<tr><td><b>{html.escape(th)}</b></td><td>{d['count']}</td><td>{tone_label(d['tone'])} ({d['tone']:+.2f})</td><td><small>"
        + "<br>".join(f'<a href="{html.escape(e["link"])}" target="_blank">{html.escape(e["title"][:110])}</a> '
                      f'<span style="color:#888">({html.escape(e["source"])})</span>' for e in d["examples"])
        + "</small></td></tr>" for th, d in news["themes"].items())
    vids = "".join(f'<li><a href="{html.escape(v["link"])}" target="_blank">{html.escape(v["title"])}</a> '
                   f'<small style="color:#888">{html.escape(v["source"])} · {html.escape(v["date"][:10])}'
                   f'{" · transcript read" if v.get("transcript") else ""}</small></li>' for v in news["latest_videos"])
    return f"""<h2>News & sentiment (separate from the scores)</h2>
<p class="desc">{news['n_items']} headlines and {news['n_videos']} videos scanned from Bloomberg/CNBC/Yahoo YouTube and free RSS feeds
(CNBC, MarketWatch, Yahoo Finance, Investing.com, The Star, Malay Mail, The Edge) at {news['generated']}.
Tone is a simple word-count measure on headlines/transcripts: good for spotting what the market is talking about, not for timing trades.
Heavy positive buzz often means the move is already priced in.</p>
<table><thead><tr><th>Theme</th><th>Stories</th><th>Tone</th><th>Examples</th></tr></thead><tbody>{rows}</tbody></table>
<h3>Latest market videos</h3><ul style="font-size:13px">{vids}</ul>"""


def ticker_cell(news: dict, t: str) -> str:
    d = (news or {}).get("tickers", {}).get(t)
    if not d:
        return ""
    head = " | ".join(h["title"][:80] for h in d["headlines"])
    return f'<span title="{html.escape(head)}">{d["mentions"]} · {tone_label(d["tone"])}</span>'
