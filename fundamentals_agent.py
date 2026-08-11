import os
import re
import json
import asyncio
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

# Check if google-genai is available
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# Known manual overrides for Screener.in symbol slugs
SYMBOL_SLUG_OVERRIDES = {
    "TATAMOTORS": "TMCV",
    "TATAMOTORS.NS": "TMCV",
    "M&M": "M%26M",
    "M&M.NS": "M%26M",
    "BAJAJ-AUTO": "BAJAJ-AUTO",
    "BAJAJ-AUTO.NS": "BAJAJ-AUTO",
    "LTIM": "LTIM",
    "LTIM.NS": "LTIM",
}


def fetch_screener_fundamentals(symbol: str) -> Dict[str, Any]:
    """
    Lightweight web scraper using requests and BeautifulSoup to pull fundamental & financial metrics
    from Screener.in (https://www.screener.in/company/{symbol}/consolidated/).
    """
    clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # Determine URL to fetch
    target_slug = SYMBOL_SLUG_OVERRIDES.get(symbol, SYMBOL_SLUG_OVERRIDES.get(clean_sym, clean_sym))
    
    urls_to_try = [
        f"https://www.screener.in/company/{target_slug}/consolidated/",
        f"https://www.screener.in/company/{target_slug}/",
    ]

    # Add space-separated search query fallback (e.g. "TATA MOTORS")
    if clean_sym.endswith("MOTORS"):
        urls_to_try.append(f"https://www.screener.in/company/{clean_sym.replace('MOTORS', ' MOTORS')}/consolidated/")

    html_content = None
    final_url = None

    for url in urls_to_try:
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                html_content = resp.text
                final_url = resp.url
                break
        except Exception:
            pass

    # Search API Fallback if direct URLs fail
    if not html_content:
        try:
            search_query = clean_sym.replace('&', ' ')
            search_url = f"https://www.screener.in/api/company/search/?q={urllib.parse.quote(search_query)}"
            s_resp = requests.get(search_url, headers=headers, timeout=8)
            if s_resp.status_code == 200 and s_resp.json():
                item_url = s_resp.json()[0].get('url')
                if item_url:
                    full_url = f"https://www.screener.in{item_url}"
                    resp = requests.get(full_url, headers=headers, timeout=8)
                    if resp.status_code == 200:
                        html_content = resp.text
                        final_url = resp.url
        except Exception as e:
            print(f"Screener Search API fallback error for {symbol}: {e}")

    if not html_content:
        return {
            "symbol": symbol,
            "error": f"Failed to fetch data from Screener.in for {symbol}",
            "roce_pct": None,
            "debt_to_equity": None,
            "revenue_growth_yoy_pct": None,
            "profit_growth_yoy_pct": None,
            "fii_change_pct": None,
            "dii_change_pct": None,
            "promoter_holding_pct": None
        }

    soup = BeautifulSoup(html_content, 'html.parser')

    def parse_num(val_str: Optional[str]) -> Optional[float]:
        if not val_str or val_str in ['N/A', '']:
            return None
        m = re.search(r'[-+]?\d*\.?\d+', val_str.replace(',', ''))
        return float(m.group()) if m else None

    # 1. Top Ratios Parsing
    top_ratios = {}
    top_ratios_ul = soup.find('ul', id='top-ratios')
    if top_ratios_ul:
        for li in top_ratios_ul.find_all('li'):
            name_span = li.find('span', class_='name')
            value_span = li.find('span', class_='value')
            if name_span and value_span:
                name = name_span.text.strip()
                val_text = value_span.text.strip().replace('\n', '').replace(' ', '')
                top_ratios[name] = val_text

    roce_str = top_ratios.get('ROCE', top_ratios.get('Return on capital employed'))
    roe_str = top_ratios.get('ROE', top_ratios.get('Return on equity'))
    debt_eq_str = top_ratios.get('Debt to equity', top_ratios.get('Debt / Eq', '0.00'))

    roce_pct = parse_num(roce_str)
    roe_pct = parse_num(roe_str)
    debt_to_equity = parse_num(debt_eq_str) if debt_eq_str != '0.00' else 0.0

    # 2. Quarterly Revenue & Profit YoY Growth (%)
    quarters_sec = soup.find('section', id='quarters')
    revenue_growth_yoy = None
    profit_growth_yoy = None

    if quarters_sec:
        table = quarters_sec.find('table')
        if table:
            rows = table.find_all('tr')
            sales_vals = []
            profit_vals = []

            for tr in rows:
                tds = [td.text.strip().replace(',', '') for td in tr.find_all('td')]
                if not tds:
                    continue
                row_name = tds[0]
                if 'Sales' in row_name or 'Revenue' in row_name:
                    sales_vals = [parse_num(v) for v in tds[1:] if parse_num(v) is not None]
                elif 'Net Profit' in row_name:
                    profit_vals = [parse_num(v) for v in tds[1:] if parse_num(v) is not None]

            # Calculate YoY growth comparing latest quarter vs same quarter last year (4 quarters ago)
            if len(sales_vals) >= 5:
                latest_sales = sales_vals[-1]
                yoy_sales = sales_vals[-5]
                if yoy_sales and yoy_sales != 0:
                    revenue_growth_yoy = round(((latest_sales - yoy_sales) / abs(yoy_sales)) * 100.0, 2)

            if len(profit_vals) >= 5:
                latest_profit = profit_vals[-1]
                yoy_profit = profit_vals[-5]
                if yoy_profit and yoy_profit != 0:
                    profit_growth_yoy = round(((latest_profit - yoy_profit) / abs(yoy_profit)) * 100.0, 2)

    # 3. Shareholding Pattern (FII / DII Recent Changes)
    fii_change = None
    dii_change = None
    promoter_holding = None

    shp_sec = soup.find('section', id='shareholding')
    if shp_sec:
        for tr in shp_sec.find_all('tr'):
            tds = [td.text.strip() for td in tr.find_all('td')]
            if not tds:
                continue
            row_name = tds[0]
            vals = [parse_num(v) for v in tds[1:] if parse_num(v) is not None]

            if 'FII' in row_name and len(vals) >= 2:
                fii_change = round(vals[-1] - vals[-2], 2)
            elif 'DII' in row_name and len(vals) >= 2:
                dii_change = round(vals[-1] - vals[-2], 2)
            elif 'Promoter' in row_name and vals:
                promoter_holding = vals[-1]

    return {
        "symbol": symbol,
        "url": final_url,
        "roce_pct": roce_pct,
        "roe_pct": roe_pct,
        "debt_to_equity": debt_to_equity,
        "revenue_growth_yoy_pct": revenue_growth_yoy,
        "profit_growth_yoy_pct": profit_growth_yoy,
        "fii_change_pct": fii_change,
        "dii_change_pct": dii_change,
        "promoter_holding_pct": promoter_holding,
        "top_ratios": top_ratios
    }


class FundamentalAnalysisResult(BaseModel):
    symbol: str = Field(description="NSE Ticker Symbol with .NS extension")
    fundamental_score: int = Field(description="Fundamental Health Score from 1 (poor) to 10 (exceptional)")
    roce_pct: Optional[float] = Field(default=None, description="Return on Capital Employed (%)")
    debt_to_equity: Optional[float] = Field(default=None, description="Debt to Equity Ratio")
    revenue_growth_yoy: Optional[float] = Field(default=None, description="Latest YoY Revenue Growth (%)")
    profit_growth_yoy: Optional[float] = Field(default=None, description="Latest YoY Net Profit Growth (%)")
    fii_dii_trend: str = Field(description="Summary trend of institutional ownership (e.g. Accumulating, Neutral, Trimming)")
    red_flags: List[str] = Field(default_factory=list, description="List of fundamental red flags or risk factors")
    reasoning: str = Field(description="Concise fundamental analysis thesis explaining the health score")


FUNDAMENTALS_ANALYST_SYSTEM_PROMPT = """
You are an Senior Institutional Fundamental Equity Analyst specializing in Indian Equities (Nifty 500).
Your objective is to evaluate company fundamentals scraped from financial statements and determine:
1. Fundamental Health Score (1-10): Rate financial strength, profitability, earnings quality, and balance sheet risk.
2. Red Flags: Identify critical financial risks such as negative/declining YoY earnings, high debt-to-equity (>1.5), poor ROCE (<10%), shrinking margins, or institutional selling (FII/DII trim).
3. FII/DII Trend: Summarize institutional ownership sentiment (Accumulating, Neutral, Trimming).
4. Reasoning: Provide a clear, high-conviction fundamental investment thesis.
"""


def fallback_fundamental_analysis(data: Dict[str, Any]) -> FundamentalAnalysisResult:
    """
    Algorithmic rule engine fallback when GEMINI_API_KEY is not available.
    """
    symbol = data.get('symbol', 'UNKNOWN.NS')
    roce = data.get('roce_pct')
    d_e = data.get('debt_to_equity')
    rev_g = data.get('revenue_growth_yoy_pct')
    pat_g = data.get('profit_growth_yoy_pct')
    fii_chg = data.get('fii_change_pct')
    dii_chg = data.get('dii_change_pct')

    score = 7
    red_flags = []

    # Evaluate ROCE
    if roce is not None:
        if roce >= 20.0:
            score += 1
        elif roce < 10.0:
            score -= 2
            red_flags.append(f"Low ROCE ({roce:.1f}% < 10%)")

    # Evaluate Debt to Equity
    if d_e is not None:
        if d_e <= 0.3:
            score += 1
        elif d_e > 1.5:
            score -= 2
            red_flags.append(f"High Debt-to-Equity ({d_e:.2f} > 1.5)")

    # Evaluate Revenue Growth YoY
    if rev_g is not None:
        if rev_g >= 15.0:
            score += 1
        elif rev_g < 0:
            score -= 1
            red_flags.append(f"Declining YoY Revenue ({rev_g:.1f}%)")

    # Evaluate Profit Growth YoY
    if pat_g is not None:
        if pat_g >= 20.0:
            score += 1
        elif pat_g < 0:
            score -= 2
            red_flags.append(f"Declining YoY Net Profit ({pat_g:.1f}%)")

    # Evaluate Institutional Sentiment
    if fii_chg is not None and dii_chg is not None:
        if fii_chg > 0.5 or dii_chg > 0.5:
            fii_dii_trend = "Institutional Accumulation"
        elif fii_chg < -0.5 and dii_chg < -0.5:
            fii_dii_trend = "Institutional Trimming"
            red_flags.append("Institutional selling (FII & DII reducing stake)")
        else:
            fii_dii_trend = "Neutral Institutional Sentiment"
    else:
        fii_dii_trend = "Stable Institutional Holdings"

    score = max(1, min(10, score))

    reasoning_parts = []
    if roce is not None:
        reasoning_parts.append(f"ROCE of {roce:.1f}%")
    if d_e is not None:
        reasoning_parts.append(f"Debt-to-Equity of {d_e:.2f}")
    if rev_g is not None and pat_g is not None:
        reasoning_parts.append(f"YoY Revenue Growth {rev_g:+.1f}% & Net Profit Growth {pat_g:+.1f}%")

    reasoning = (
        f"{symbol} exhibits a Fundamental Health Score of {score}/10 based on "
        + (", ".join(reasoning_parts) if reasoning_parts else "available Screener metrics")
        + f". Institutional trend: {fii_dii_trend}."
    )

    return FundamentalAnalysisResult(
        symbol=symbol,
        fundamental_score=score,
        roce_pct=roce,
        debt_to_equity=d_e,
        revenue_growth_yoy=rev_g,
        profit_growth_yoy=pat_g,
        fii_dii_trend=fii_dii_trend,
        red_flags=red_flags,
        reasoning=reasoning
    )


async def analyze_fundamentals_async(
    symbol: str,
    data: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None
) -> FundamentalAnalysisResult:
    """
    Asynchronously analyze company fundamentals using Gemini API or rule-based fallback.
    """
    if data is None:
        data = fetch_screener_fundamentals(symbol)

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key or not GENAI_AVAILABLE:
        return fallback_fundamental_analysis(data)

    try:
        client = genai.Client(api_key=api_key)

        prompt = f"""
Analyze the fundamental health of the following Indian equity candidate:
Symbol: {symbol}
Scraped Screener.in Data:
- ROCE (%): {data.get('roce_pct', 'N/A')}
- ROE (%): {data.get('roe_pct', 'N/A')}
- Debt to Equity Ratio: {data.get('debt_to_equity', 'N/A')}
- Latest YoY Revenue Growth (%): {data.get('revenue_growth_yoy_pct', 'N/A')}
- Latest YoY Net Profit Growth (%): {data.get('profit_growth_yoy_pct', 'N/A')}
- Recent FII Holding Change (%): {data.get('fii_change_pct', 'N/A')}
- Recent DII Holding Change (%): {data.get('dii_change_pct', 'N/A')}
- Promoter Holding (%): {data.get('promoter_holding_pct', 'N/A')}

Evaluate fundamental health score (1-10), highlight red flags if any, institutional trend, and reasoning thesis matching JSON schema.
"""

        for model_name in ['gemini-3.5-flash', 'gemini-2.5-flash']:
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=FUNDAMENTALS_ANALYST_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=FundamentalAnalysisResult,
                        temperature=0.2,
                    ),
                )
                if response and response.text:
                    result_dict = json.loads(response.text)
                    return FundamentalAnalysisResult(**result_dict)
            except Exception as e:
                print(f"Fundamentals Agent model '{model_name}' error: {e}. Trying fallback...")
        return fallback_fundamental_analysis(data)

    except Exception as e:
        print(f"Gemini Fundamental API call failed for {symbol}: {e}. Falling back to rule engine.")
        return fallback_fundamental_analysis(data)


async def run_fundamentals_pipeline_async(
    symbols: List[str],
    api_key: Optional[str] = None
) -> List[FundamentalAnalysisResult]:
    """
    Run async fundamental evaluation across a list of stock symbols.
    """
    tasks = []
    for sym in symbols:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, fetch_screener_fundamentals, sym)
        tasks.append(analyze_fundamentals_async(sym, data=data, api_key=api_key))

    results = await asyncio.gather(*tasks, return_exceptions=False)
    return results


if __name__ == "__main__":
    print("=" * 80)
    print("FUNDAMENTALS AGENT SCREENER SCAN TEST")
    print("=" * 80)

    test_symbols = ["DIXON.NS", "PERSISTENT.NS", "ICICIBANK.NS"]
    for sym in test_symbols:
        print(f"\nFetching fundamentals for {sym}...")
        fund_data = fetch_screener_fundamentals(sym)
        print("Scraped Fundamentals Data:", fund_data)

        analysis = asyncio.run(analyze_fundamentals_async(sym, data=fund_data))
        print("Fundamental Health Analysis:")
        print(f"  Score: {analysis.fundamental_score}/10")
        print(f"  FII/DII Trend: {analysis.fii_dii_trend}")
        print(f"  Red Flags: {analysis.red_flags}")
        print(f"  Reasoning: {analysis.reasoning}")
        print("-" * 80)
