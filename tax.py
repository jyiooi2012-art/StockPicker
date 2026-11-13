"""
Tax considerations for a MALAYSIAN TAX-RESIDENT INDIVIDUAL investor.
General information only, not tax advice. Rules last checked 12 Nov 2026; re-check yearly
(each Budget in October can change them). Sources are listed in TAX_SOURCES below.
"""
from __future__ import annotations

import html
import math

US_DIV_WHT = 0.30          # US withholding on dividends to Malaysians (no US-Malaysia income tax treaty)
UCITS_IE_WHT = 0.15        # what an Irish-domiciled fund suffers on US dividends (US-Ireland treaty)
US_ESTATE_THRESHOLD = 60_000

# Non-US companies listed in the US (ADRs or direct listings): home-country dividend withholding (approximate; brokers may reclaim less/more)
ADR_WHT = {"TSM": ("Taiwan", 0.21), "ASML": ("Netherlands", 0.15), "NVO": ("Denmark", 0.27),
           "SHOP": ("Canada", 0.25), "NU": ("Cayman Islands", 0.0)}
FOREIGN_CORPS = set(ADR_WHT)

# Irish-domiciled UCITS ETFs traded on the London Stock Exchange (Yahoo ".L" tickers). "Acc" = accumulating
# (dividends reinvested inside the fund, no payouts to you). Check the exact line on your broker.
UCITS_ALTERNATIVES = {
    "SPY": "CSPX.L (iShares Core S&P 500, Acc) or VUAA.L (Vanguard S&P 500, Acc)",
    "VOO": "CSPX.L or VUAA.L (S&P 500, Acc)",
    "IVV": "CSPX.L or VUAA.L (S&P 500, Acc)",
    "VTI": "CSPX.L (closest large-cap proxy) or a US total-market UCITS such as IUSA.L",
    "QQQ": "CNDX.L (iShares Nasdaq 100, Acc) or EQAC.L (Invesco Nasdaq-100, Acc)",
    "QQQM": "CNDX.L or EQAC.L (Nasdaq 100, Acc)",
    "VT": "VWRA.L (Vanguard FTSE All-World, Acc) or SSAC.L (iShares MSCI ACWI, Acc)",
    "SCHD": "no exact match; consider VHYL.L (Vanguard All-World High Dividend) or FUSD.L (US quality dividend)",
    "VGT": "IUIT.L (iShares S&P 500 Info Tech, Acc)",
    "XLK": "IUIT.L (iShares S&P 500 Info Tech, Acc)",
    "SMH": "SMH.L (VanEck Semiconductor UCITS)",
    "SOXX": "SEMI.L (iShares MSCI Global Semiconductors) or SMH.L",
    "IWM": "R2US.L (SPDR Russell 2000 US Small Cap)",
    "XLV": "IUHC.L (iShares S&P 500 Health Care)",
    "XLF": "IUFS.L (iShares S&P 500 Financials)",
    "XLE": "IUES.L (iShares S&P 500 Energy)",
    "EEM": "EIMI.L (iShares Core MSCI EM IMI, Acc)",
    "GLD": "IGLN.L (iShares Physical Gold ETC)",
    "TLT": "IDTL.L / DTLA.L (iShares USD Treasury 20+yr)",
    "ARKK": "no UCITS copy; thematic alternatives exist (e.g. ARKK-style UCITS from ARK Europe)",
}

TAX_SOURCES = [
    ("PwC Tax Summaries – Malaysia, individual income determination (reviewed Jun 2026)",
     "https://taxsummaries.pwc.com/malaysia/individual/income-determination"),
    ("PwC Malaysian Tax Booklet 2026/27 – stamp duty", "https://www.pwc.com/my/en/publications/mtb/stamp-duty.html"),
    ("StashAway – Malaysia personal tax reliefs YA2027", "https://www.stashaway.my/r/personal-income-tax-reliefs-malaysia-ya2027"),
    ("IRS – Nonresidents with US assets & estate tax", "https://www.irs.gov/individuals/international-taxpayers/some-nonresidents-with-us-assets-must-file-estate-tax-returns"),
    ("Bloomberg Tax – Malaysia gazettes FSI exemption extension", "https://news.bloombergtax.com/daily-tax-report-international/malaysia-gazettes-order-extending-income-tax-exemption-on-foreign-income"),
    ("LHDN (official)", "https://www.hasil.gov.my"),
]


def listing(ticker: str, qtype: str) -> str:
    if ticker.endswith(".KL"):
        return "bursa"
    if ticker.endswith(".L"):
        return "ucits"
    if "." in ticker and not ticker.startswith("^"):
        return "other"
    if ticker in FOREIGN_CORPS:
        return "adr"
    return "us_etf" if qtype == "ETF" else "us_stock"


def dividend_yield(info: dict) -> float:
    for k in ("trailingAnnualDividendYield", "yield"):
        v = info.get(k)
        if isinstance(v, (int, float)) and v == v and 0 <= v < 0.3:
            return float(v)
    v = info.get("dividendYield")  # newer yfinance reports this in percent
    if isinstance(v, (int, float)) and v == v:
        return float(v) / 100 if v > 0.3 else float(v)
    return 0.0


def features(ticker: str, qtype: str, info: dict) -> dict:
    kind = listing(ticker, qtype)
    dy = dividend_yield(info)
    if kind in ("us_stock", "us_etf"):
        wht, estate = US_DIV_WHT, True
    elif kind == "adr":
        wht, estate = ADR_WHT[ticker][1], False
    else:  # Bursa: single-tier dividends; UCITS acc: fund handles it
        wht, estate = 0.0, False
    drag = dy * wht
    return {"tax_kind": kind, "div_yield": dy, "div_wht": wht, "tax_drag": drag, "us_estate_risk": estate}


def note(ticker: str, r) -> str:
    k = r.get("tax_kind")
    dy, drag = r.get("div_yield", 0) or 0, r.get("tax_drag", 0) or 0
    out = []
    if k == "bursa":
        out.append("No tax on gains; dividends tax-free unless total MY dividends > RM100k/yr (2%); stamp duty 0.1% (max RM1k)")
    elif k in ("us_stock", "us_etf"):
        out.append("30% US div. withholding" + (f": yield {dy:.1%} → ~{drag:.2%}/yr lost" if dy > 0.001 else ": no/low dividend, minimal drag"))
        out.append("US estate-tax asset")
        if k == "us_etf" and ticker in UCITS_ALTERNATIVES:
            out.append("UCITS swap: " + UCITS_ALTERNATIVES[ticker])
    elif k == "adr":
        c, w = ADR_WHT[ticker]
        out.append(f"{c} company: ~{w:.0%} div. withholding; non-US company, generally not US-situs (confirm)")
    elif k == "ucits":
        out.append("Irish UCITS: 15% WHT on US dividends inside fund, not US-situs")
    return "; ".join(out)


def render_playbook(holdings_us_value_usd: float | None = None) -> str:
    srcs = " · ".join(f'<a href="{u}" target="_blank">{html.escape(t)}</a>' for t, u in TAX_SOURCES)
    return f"""<h2>Tax playbook for a Malaysian tax resident</h2>
<p class="desc"><b>General information, not tax advice.</b> Checked 12 Nov 2026; Budget announcements each October can change these. Confirm with LHDN or a licensed tax adviser before acting.</p>
<ol style="font-size:14px;max-width:1000px;line-height:1.5">
<li><b>Capital gains on listed shares are not taxed for individuals.</b> Malaysia's CGT (from 2024) targets companies selling unlisted shares; RPGT covers property. Selling Bursa or foreign listed shares at a profit is generally tax-free for you, <i>as an investor</i>.</li>
<li><b>Watch the "trader" line.</b> If you trade very frequently (lots of short-term in-and-out trades, using borrowed money, treating it like a job), LHDN can treat gains as <i>business income</i> taxed at your income-tax rate. Use the short-term list sparingly; the long-term list is the tax-friendliest.</li>
<li><b>Foreign-sourced income (FSI) is exempt for individuals until 31 Dec 2036</b> when remitted to Malaysia, on condition it was taxed abroad (the 30% US withholding counts). So foreign dividends and gains you bring home are generally not taxed again in Malaysia. Some older articles still say 2026; the gazetted extension and PwC (Jun 2026) say 2036.</li>
<li><b>US dividends lose 30% at source</b> (Malaysia has no tax treaty with the US covering this), and you can't reclaim it. Ways to cut it:
  <ul><li>Prefer <b>low/no-dividend growth stocks</b> when holding US names directly (most of the long-term list already is).</li>
  <li>Hold US index exposure through <b>Irish-domiciled UCITS ETFs</b> (e.g. CSPX/VUAA for the S&P 500, CNDX for the Nasdaq-100, VWRA for the world). The fund suffers only 15% on US dividends, and <b>accumulating</b> share classes reinvest them so you get no taxable payout. On a ~1.3% yield that saves ~0.2% a year, compounding.</li></ul></li>
<li><b>US estate tax risk.</b> If a non-US person dies holding over US$60,000 of US-situs assets (US company shares and US-domiciled ETFs, even at a Malaysian/Singapore broker), the estate may owe US estate tax of up to 40% on the excess, with only a ~US$13k credit. Irish UCITS ETFs and Bursa shares are not US-situs. If your US holdings pass ~US$60k, shifting the ETF part to UCITS is the easiest fix.</li>
<li><b>Bursa dividends:</b> single-tier, so tax-free to you, except the <b>2% dividend tax</b> (from YA2025) on Malaysian-company dividends above <b>RM100,000 a year</b>. ASNB, EPF and unit trust distributions are excluded.</li>
<li><b>Trading costs as a "tax":</b> Bursa stamp duty is 0.1% of value, capped at RM1,000 per contract note (SC-approved ETFs are exempt until end-2028). Add brokerage and clearing fees, and the FX spread on USD trades; frequent trading costs add up faster than most taxes.</li>
<li><b>Use the reliefs first, they are a guaranteed return:</b> <b>PRS</b> contributions up to <b>RM3,000</b> relief (until YA2030); <b>EPF</b> (incl. voluntary/i-Saraan top-ups) up to <b>RM4,000</b> within the RM7,000 EPF + life insurance bucket; <b>SSPN</b> up to RM8,000 if you save for a child's education (final year YA2027 unless extended). At a 24–25% marginal rate, RM3,000 into PRS saves ~RM720 of tax immediately.</li>
<li><b>Currency:</b> USD assets carry MYR risk both ways. It is not a tax, but it moves your ringgit returns as much as a tax would. The macro table tracks MYR/USD.</li>
</ol>
<p class="desc">Sources: {srcs}</p>"""
