"""
Trading cost & tax calculator for Malaysian investors across platforms.

Fee schedules change often (promos especially). Every number below carries its source and the
date it was checked; edit BROKERS when your broker changes pricing. "verified": False means
sources conflicted or the number came from a single third-party page.

    python fees.py                 # print the comparison table
    python fees.py --rm 5000 --usd 2000 --price 50

Checked 12 Nov 2026 against:
 [A] money.com.my "Best online stockbrokers in Malaysia 2026" (upd. 17 Oct 2026)
 [B] duitwise.my "Best trading platforms to buy Bursa shares" (6 Aug 2026)
 [D] malaysianpf.com "Moomoo vs IBKR Malaysia" (25 Jul 2026)
 [E] money.com.my IBKR Malaysia review (data as of 17 Oct 2026)
 [F] money.com.my Webull Malaysia review (upd. 17 Oct 2026)
 [G] malaysia4u.com investment brokers guide (upd. 13 Jul 2026)
 [I] interactivebrokers.com US stock commissions page (read 12 Nov 2026)
 [P] PwC Malaysian Tax Booklet 2026/27, stamp duty
"""
from __future__ import annotations

import argparse
import html
import math

CHECKED = "12 Nov 2026"
SOURCES = {
    "A": "https://money.com.my/guides/best-online-brokers-malaysia-2026",
    "B": "https://duitwise.my/news/best-trading-platforms-bursa-malaysia-2026/",
    "D": "https://malaysianpf.com/moomoo-vs-ibkr-malaysia/",
    "E": "https://money.com.my/guides/interactive-brokers-malaysia-review",
    "F": "https://money.com.my/guides/webull-malaysia-review",
    "G": "https://malaysia4u.com/investment-brokers-guide",
    "I": "https://www.interactivebrokers.com/en/pricing/commissions-stocks.php",
    "P": "https://www.pwc.com/my/en/publications/mtb/stamp-duty.html",
}

# ---------------- Statutory / exchange charges -------------------------------------------------
SST = 0.08                      # on platform/service fees; ordinary-share brokerage is SST-exempt [G]
BURSA_CLEARING = (0.0003, 1000)  # 0.03%, cap RM1,000 per contract, both sides [A,B,G]
STAMP_PER_1000 = 1.0             # RM1 per RM1,000 or part (0.1%), cap RM1,000; ETFs exempt to end-2028 [P]
STAMP_CAP = 1000
SEC_FEE = 20.60 / 1_000_000      # US SEC Section 31, on SELL value [I,G]
FINRA_TAF_PER_SHARE = 0.0        # fee holiday 1 Oct – 31 Dec 2026 per IBKR [I]; normally ~US$0.0002/share sold


def _clip(x, lo=None, hi=None):
    if lo is not None:
        x = max(x, lo)
    if hi is not None:
        x = min(x, hi)
    return x


# ---------------- Brokers ----------------------------------------------------------------------
# Each fn returns the broker's own charges (commission + platform/settlement), before statutory fees.
def rakuten_bursa(v):
    if v <= 100: return 1.0
    if v < 10_000: return 2.88
    if v < 100_000: return v * 0.001
    return 100.0


BROKERS = {
    "moomoo MY": {
        "bursa": lambda v: v * 0.0003 + 3 * (1 + SST),        # 0.03% + RM3 platform/order (+SST) [A,B,D]
        "us": lambda v, sh: v * 0.0003 + 0.99 * (1 + SST) + min(sh * 0.003, v * 0.01),  # 0.03% + US$0.99 platform + settlement [A,D]
        "fx": 0.0075,                                          # ~0.73–0.79% vs mid-market, measured Feb 2026 [D]
        "notes": "0% Bursa commission for first 180 days (new users). FX spread is not published; 0.75% is a third-party measurement.",
        "src": "A,B,D", "verified": True,
    },
    "Webull MY": {
        "bursa": lambda v: _clip(v * 0.0005, 2.50),            # 0.05%, min RM2.50, no platform fee [A,B,F]
        "us": lambda v, sh: 0.0,                               # 0 commission & platform under promo to 31 Dec 2026 / 365 days [A,F]
        "us_after_promo": lambda v, sh: _clip(v * 0.00025, 0.99),
        "fx": None,
        "notes": "US trades free under promo (new users 365 days; existing users to 31 Dec 2026), then 0.025% min US$0.99. FX spread not published.",
        "src": "A,F", "verified": True,
    },
    "Rakuten Trade": {
        "bursa": rakuten_bursa,                                # RM1 / RM2.88 / 0.1% / RM100 cap [A,B]
        "us": None, "fx": None,
        "notes": "Strong for small Bursa trades (RM2.88 up to ~RM10k). US pricing not confirmed by recent sources.",
        "src": "A,B", "verified": False,                       # [G] shows an older/different scale
    },
    "M+ Global": {
        "bursa": lambda v: _clip(v * 0.0005, 8.0),             # 0.05%, min RM8 [B,G]
        "us": lambda v, sh: _clip(v * 0.001, 3.0),             # from 0.10%, min US$3 [G]
        "fx": None,
        "notes": "Direct CDS account.", "src": "B,G", "verified": False,
    },
    "CGS iTrade (ex-CGS-CIMB)": {
        "bursa": lambda v: _clip(v * 0.000388, 8.88),          # 0.0388%, min RM8.88 [G]
        "us": None, "fx": None,
        "notes": "CIMB's retail stockbroking now runs under CGS International. Direct CDS. Rate from one source only.",
        "src": "G", "verified": False,
    },
    "Maybank Investment (M2U)": {
        "bursa": lambda v: _clip(v * 0.001, 8.0),              # 0.1%, min RM8 [G]; another source says 0.08–0.10%
        "us": None, "fx": None,
        "notes": "Bank-linked 'conventional' account; convenient but highest Bursa rate here.",
        "src": "G", "verified": False,
    },
    "Tiger Brokers MY": {
        "bursa": None,
        "us": lambda v, sh: _clip(v * 0.0006, 0.99),           # 0.06%, min US$0.99 [G]
        "fx": None,
        "notes": "Bursa support and platform fee unclear (sources conflict).", "src": "G", "verified": False,
    },
    "IBKR Pro (tiered)": {
        "bursa": None,                                         # sources conflict on access for MY residents [D,E]
        "us": lambda v, sh: _clip(sh * 0.0035, 0.35, v * 0.01) + sh * 0.0002,  # + NSCC/DTC clearing [I,E]
        "fx": 0.005,                                           # MYR can't be deposited directly: ~0.5% via Wise; IBKR's own FX is 0.002% min US$2 [D,E]
        "notes": "Cheapest for US trades of any size; no Bursa for most Malaysian residents. Not SC-licensed in Malaysia.",
        "src": "D,E,I", "verified": True,
    },
}


# ---------------- Calculators ------------------------------------------------------------------
def bursa_cost(broker: str, value_rm: float, side: str = "buy", etf: bool = False) -> dict | None:
    b = BROKERS[broker]
    if not b.get("bursa"):
        return None
    brokerage = b["bursa"](value_rm)
    clearing = min(value_rm * BURSA_CLEARING[0], BURSA_CLEARING[1])
    stamp = 0.0 if etf else min(math.ceil(value_rm / 1000) * STAMP_PER_1000, STAMP_CAP)
    total = brokerage + clearing + stamp
    return {"brokerage": brokerage, "clearing": clearing, "stamp": stamp, "total": total, "pct": total / value_rm}


def us_cost(broker: str, value_usd: float, price: float, side: str = "buy", include_fx: bool = True,
            after_promo: bool = False) -> dict | None:
    b = BROKERS[broker]
    fn = b.get("us_after_promo") if after_promo and b.get("us_after_promo") else b.get("us")
    if not fn:
        return None
    sh = max(1, round(value_usd / price))
    brokerage = fn(value_usd, sh)
    reg = (value_usd * SEC_FEE + sh * FINRA_TAF_PER_SHARE) if side == "sell" else 0.0
    fx = (value_usd * b["fx"]) if include_fx and b.get("fx") is not None else None
    total = brokerage + reg + (fx or 0)
    return {"brokerage": brokerage, "regulatory": reg, "fx": fx, "total": total, "pct": total / value_usd}


def _fmt(x, cur):
    return "?" if x is None else f"{cur}{x:,.2f}"


def table_rows(rm_values=(1_000, 10_000), usd_values=(1_000, 10_000), price=100.0):
    rows = []
    for name, b in BROKERS.items():
        r = {"broker": name, "notes": b["notes"], "src": b["src"], "verified": b["verified"], "fx": b.get("fx")}
        for v in rm_values:
            c = bursa_cost(name, v)
            r[f"bursa_{v}"] = c
            r[f"bursa_rt_{v}"] = None if c is None else c["total"] + bursa_cost(name, v, "sell")["total"]
        for v in usd_values:
            c = us_cost(name, v, price, include_fx=False)
            r[f"us_{v}"] = c
            r[f"us_rt_{v}"] = None if c is None else c["total"] + us_cost(name, v, price, "sell", include_fx=False)["total"]
        rows.append(r)
    return rows


def render(rm_values=(1_000, 10_000), usd_values=(1_000, 10_000), price=100.0) -> str:
    rows = table_rows(rm_values, usd_values, price)
    na = "<td style='color:#aaa'>n/a</td>"

    def cell(c, cur):
        return na if c is None else f"<td>{_fmt(c['total'], cur)} <small style='color:#666'>({c['pct']:.2%})</small></td>"

    def rt(x, cur):
        return na if x is None else f"<td>{_fmt(x, cur)}</td>"

    head = ("<tr><th>Platform</th>" + "".join(f"<th>Bursa buy RM{v:,}</th>" for v in rm_values)
            + "".join(f"<th>Bursa buy+sell RM{v:,}</th>" for v in rm_values)
            + "".join(f"<th>US buy US${v:,}</th>" for v in usd_values)
            + "".join(f"<th>US buy+sell US${v:,}</th>" for v in usd_values)
            + "<th>FX spread MYR→USD (each conversion)</th><th>Notes</th></tr>")
    body = ""
    for r in rows:
        has_us = r[f"us_{usd_values[0]}"] is not None
        fx = (f"<td>~{r['fx']:.2%}</td>" if r["fx"] is not None else ("<td>not published</td>" if has_us else na))
        body += (f"<tr><td><b>{html.escape(r['broker'])}</b>{'' if r['verified'] else ' <small style=color:#b45309>(unverified)</small>'}</td>"
                 + "".join(cell(r[f"bursa_{v}"], "RM") for v in rm_values)
                 + "".join(rt(r[f"bursa_rt_{v}"], "RM") for v in rm_values)
                 + "".join(cell(r[f"us_{v}"], "US$") for v in usd_values)
                 + "".join(rt(r[f"us_rt_{v}"], "US$") for v in usd_values)
                 + fx + f"<td><small>{html.escape(r['notes'])} [{r['src']}]</small></td></tr>")
    srcs = " · ".join(f'<a href="{u}" target="_blank">[{k}]</a>' for k, u in SOURCES.items())
    return f"""<h2>Platform fees & transaction taxes (checked {CHECKED})</h2>
<p class="desc">Bursa columns: brokerage + platform fee (+8% SST) + clearing fee (0.03%, cap RM1,000) + stamp duty
(RM1 per RM1,000, cap RM1,000; Bursa ETFs exempt to end-2028). US columns: commission + platform/settlement/clearing fees,
plus the SEC fee on sells (US$20.60 per US$1m), for a US$100 share. FINRA's trading fee is waived until 31 Dec 2026.
<b>FX is shown separately</b> because you pay it when you convert money, not on every trade: convert in bulk and it shrinks.
Promos change monthly, so check the broker's own page before choosing. Sources: {srcs}</p>
<table><thead>{head}</thead><tbody>{body}</tbody></table>
<ul class="desc">
<li><b>Small Bursa trades (≤RM10k):</b> Webull, Rakuten and moomoo are cheapest. Bank-linked accounts (Maybank, CGS iTrade) cost more because of RM8–8.88 minimums.</li>
<li><b>US trades:</b> Webull is RM0 commission until its promo ends; IBKR and Tiger are cheap per trade. <b>FX is often the biggest hidden cost</b>:
moomoo's ~0.75% spread on RM10k is about US$17 each way, more than its commission. Funding IBKR via Wise (~0.5%) once, then trading many times, spreads that cost out.</li>
<li><b>Ongoing taxes no platform avoids:</b> 30% US dividend withholding, and US estate-tax exposure above US$60k. Both are covered in the tax playbook.</li>
<li><b>Investor protection:</b> SC-licensed brokers (moomoo MY, Webull MY, Rakuten, M+, CGS, Maybank) fall under Malaysian rules; IBKR and Tiger serve Malaysians from abroad.</li>
</ul>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rm", type=float, default=1000, help="Bursa trade value in RM")
    ap.add_argument("--usd", type=float, default=1000, help="US trade value in USD")
    ap.add_argument("--price", type=float, default=100, help="US share price (for per-share fees)")
    a = ap.parse_args()
    print(f"Costs checked {CHECKED}. Bursa trade RM{a.rm:,.0f}; US trade US${a.usd:,.0f} at US${a.price}/share\n")
    print(f"{'Platform':28} {'Bursa buy':>14}   US buy (FX separate)")
    for name in BROKERS:
        b = bursa_cost(name, a.rm)
        u = us_cost(name, a.usd, a.price, include_fx=False)
        bs = f"RM{b['total']:.2f} {b['pct']:.2%}" if b else "n/a"
        fx = BROKERS[name].get("fx")
        us = (f"US${u['total']:.2f} {u['pct']:.2%}" + (f" (+FX ~{fx:.2%})" if fx else (" (+FX ?)" if u else ""))) if u else "n/a"
        print(f"{name:28} {bs:>14}   {us}")


if __name__ == "__main__":
    main()
