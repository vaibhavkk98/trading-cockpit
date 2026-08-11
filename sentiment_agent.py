import os
import re
import json
import asyncio
import urllib.parse
import warnings
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

# Filter XMLParsedAsHTMLWarning warning for clean terminal output
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Check if google-genai SDK is available
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def fetch_google_news_rss(symbol: str, max_items: int = 5) -> List[Dict[str, str]]:
    """
    Fetch recent news headlines for a stock using Google News RSS feed:
    https://news.google.com/rss/search?q={symbol}+stock+india&hl=en-IN&gl=IN&ceid=IN:en
    """
    clean_sym = symbol.replace('.NS', '').replace('.BO', '')
    query = f"{clean_sym} stock india"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"Google News RSS HTTP error {resp.status_code} for {symbol}")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.find_all('item')

        headlines = []
        for item in items[:max_items]:
            title_tag = item.find('title')
            title = title_tag.text.strip() if title_tag else ""
            pub_date_tag = item.find('pubdate')
            pub_date = pub_date_tag.text.strip() if pub_date_tag else ""
            link_tag = item.find('link')
            link = link_tag.text.strip() if link_tag else ""

            if title:
                headlines.append({
                    "title": title,
                    "pub_date": pub_date,
                    "link": link
                })

        return headlines

    except Exception as e:
        print(f"Error fetching Google News RSS for {symbol}: {e}")
        return []


class SentimentAnalysisResult(BaseModel):
    symbol: str = Field(description="NSE Ticker Symbol with .NS extension")
    sentiment_score: float = Field(description="Numeric sentiment score between -1.0 (extremely bearish) and +1.0 (extremely bullish)")
    sentiment_classification: str = Field(description="Sentiment classification: 'Bullish', 'Neutral', or 'Bearish'")
    market_narrative: List[str] = Field(description="Bulleted summary of key market narratives, news drivers, and swing trading risks")
    key_news_headlines: List[str] = Field(default_factory=list, description="List of recent news headlines analyzed")


SENTIMENT_ANALYST_SYSTEM_PROMPT = """
You are a Senior News & Event Sentiment Analyst specializing in Indian Equities (Nifty 500) for Swing Trading.
Your objective is to analyze recent news headlines and evaluate market narrative, event risks, and sentiment impact:
1. Sentiment Score (-1.0 to +1.0):
   - +0.5 to +1.0: Strongly positive catalysts (earnings beat, major order win, FII buying upgrade, expansion).
   - -0.4 to +0.4: Neutral, routine news, or mixed market noise.
   - -1.0 to -0.5: High-risk events (earnings miss, regulatory probes/fines, management resignation, debt default).
2. Sentiment Classification: "Bullish", "Neutral", or "Bearish".
3. Market Narrative: Provide 2-3 bullet points highlighting dominant news catalysts and swing trading risks (e.g. upcoming earnings, margin pressures, order wins).
"""


def fallback_sentiment_analysis(symbol: str, headlines: List[Dict[str, str]]) -> SentimentAnalysisResult:
    """
    Algorithmic keyword sentiment fallback when GEMINI_API_KEY is not available.
    """
    headline_texts = [h.get('title', '') for h in headlines]
    
    if not headline_texts:
        return SentimentAnalysisResult(
            symbol=symbol,
            sentiment_score=0.0,
            sentiment_classification="Neutral",
            market_narrative=["No recent news headlines found on Google News RSS."],
            key_news_headlines=[]
        )

    bullish_keywords = [
        "beat", "jump", "rise", "gain", "rally", "growth", "profit", "buy", "upgrade",
        "win", "contract", "order", "inflow", "surge", "higher", "record", "target", "soar"
    ]
    bearish_keywords = [
        "fall", "drop", "down", "cut", "loss", "decline", "plunge", "penalty", "probe",
        "downgrade", "investigation", "raid", "miss", "slip", "lower", "slump", "concern"
    ]

    total_bullish = 0
    total_bearish = 0
    narrative_points = []

    for text in headline_texts:
        lower_t = text.lower()
        b_count = sum(1 for kw in bullish_keywords if kw in lower_t)
        br_count = sum(1 for kw in bearish_keywords if kw in lower_t)

        total_bullish += b_count
        total_bearish += br_count

        if b_count > br_count:
            narrative_points.append(f"Positive momentum signal: '{text}'")
        elif br_count > b_count:
            narrative_points.append(f"Risk signal detected: '{text}'")

    net_sentiment = total_bullish - total_bearish
    if net_sentiment > 0:
        score = min(0.8, 0.2 + net_sentiment * 0.2)
        classification = "Bullish"
    elif net_sentiment < 0:
        score = max(-0.8, -0.2 + net_sentiment * 0.2)
        classification = "Bearish"
    else:
        score = 0.0
        classification = "Neutral"

    if not narrative_points:
        narrative_points = ["Recent news sentiment is balanced with routine market coverage."]

    return SentimentAnalysisResult(
        symbol=symbol,
        sentiment_score=round(score, 2),
        sentiment_classification=classification,
        market_narrative=narrative_points[:3],
        key_news_headlines=headline_texts[:5]
    )


async def analyze_sentiment_async(
    symbol: str,
    headlines: Optional[List[Dict[str, str]]] = None,
    api_key: Optional[str] = None
) -> SentimentAnalysisResult:
    """
    Asynchronously analyze news sentiment for a stock symbol using Gemini API or fallback engine.
    """
    if headlines is None:
        loop = asyncio.get_running_loop()
        headlines = await loop.run_in_executor(None, fetch_google_news_rss, symbol)

    headline_texts = [h.get('title', '') for h in headlines if h.get('title')]

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key or not GENAI_AVAILABLE or not headline_texts:
        return fallback_sentiment_analysis(symbol, headlines)

    try:
        client = genai.Client(api_key=api_key)

        prompt = f"""
Analyze news sentiment and swing trading risks for:
Symbol: {symbol}
Recent Google News Headlines:
{json.dumps(headline_texts, indent=2)}

Evaluate sentiment score (-1.0 to +1.0), classification (Bullish/Neutral/Bearish), and bulleted market narrative.
"""

        for model_name in ['gemini-3.5-flash', 'gemini-2.5-flash']:
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SENTIMENT_ANALYST_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=SentimentAnalysisResult,
                        temperature=0.2,
                    ),
                )
                if response and response.text:
                    result_dict = json.loads(response.text)
                    result_dict['key_news_headlines'] = headline_texts[:5]
                    return SentimentAnalysisResult(**result_dict)
            except Exception as e:
                print(f"Sentiment Agent model '{model_name}' error: {e}. Trying fallback...")
        return fallback_sentiment_analysis(symbol, headlines)

    except Exception as e:
        print(f"Gemini Sentiment API call failed for {symbol}: {e}. Falling back to rule engine.")
        return fallback_sentiment_analysis(symbol, headlines)


async def run_sentiment_pipeline_async(
    symbols: List[str],
    api_key: Optional[str] = None
) -> List[SentimentAnalysisResult]:
    """
    Run async sentiment evaluation across a list of stock symbols.
    """
    tasks = []
    for sym in symbols:
        loop = asyncio.get_running_loop()
        headlines = await loop.run_in_executor(None, fetch_google_news_rss, sym)
        tasks.append(analyze_sentiment_async(sym, headlines=headlines, api_key=api_key))

    results = await asyncio.gather(*tasks, return_exceptions=False)
    return results


if __name__ == "__main__":
    print("=" * 80)
    print("SENTIMENT AGENT GOOGLE NEWS SCAN TEST")
    print("=" * 80)

    test_symbols = ["DIXON.NS", "ICICIBANK.NS", "TECHM.NS"]
    for sym in test_symbols:
        print(f"\nFetching news for {sym}...")
        news = fetch_google_news_rss(sym)
        print(f"Scraped {len(news)} headlines.")

        analysis = asyncio.run(analyze_sentiment_async(sym, headlines=news))
        print("Sentiment Analysis Result:")
        print(f"  Score: {analysis.sentiment_score} ({analysis.sentiment_classification})")
        print(f"  Narrative: {analysis.market_narrative}")
        print(f"  Headlines: {analysis.key_news_headlines[:2]}")
        print("-" * 80)
