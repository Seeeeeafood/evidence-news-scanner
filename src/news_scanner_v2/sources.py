from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode


REQUIRED_CATEGORIES = ("GEO", "EARN", "MA", "STRAT", "MOVE", "ANAL", "MACRO")
BRAVE_NEWS_ENDPOINT = "https://api.search.brave.com/res/v1/news/search"


def google_news_search_url(query: str) -> str:
    params = urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return f"https://news.google.com/rss/search?{params}"


@dataclass(frozen=True)
class NewsSource:
    name: str
    category: str
    url: str
    kind: str = "rss"
    provider: str = "rss"
    query: str = ""
    freshness: str = "pd"
    count: int = 20
    country: str = "US"
    search_lang: str = "en"
    timeout_seconds: int = 8


BRAVE_NEWS_SOURCES = (
    NewsSource(
        name="brave-news-geo-policy",
        category="GEO",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query='Iran OR "Middle East" OR Russia OR China OR tariff OR sanctions OR "executive order" market stocks',
        count=20,
    ),
    NewsSource(
        name="brave-news-earnings-guidance",
        category="EARN",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query='earnings OR guidance OR EPS OR revenue "stock market" "large cap"',
        count=20,
    ),
    NewsSource(
        name="brave-news-ma-buyback",
        category="MA",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query=(
            'acquisition OR merger OR buyout OR "buyout offer" OR '
            '"takeover bid" OR "binding offer" OR repurchase OR '
            '"strategic investment" OR "stake deal" stock'
        ),
        count=20,
    ),
    NewsSource(
        name="brave-news-megacap-strategic",
        category="STRAT",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query="NVDA OR AMD OR AAPL OR MSFT OR GOOGL OR AVGO partnership supply deal investment",
        count=20,
    ),
    NewsSource(
        name="brave-news-largecap-movers",
        category="MOVE",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query="premarket movers stocks today OR shares surge plunge today",
        count=20,
    ),
    NewsSource(
        name="brave-news-analyst-actions",
        category="ANAL",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query=(
            '("NVDA" OR "AMD" OR "AVGO" OR "MSFT" OR "AAPL" OR "GOOGL" '
            'OR "META" OR "TSLA" OR "CRWV" OR "NBIS") '
            '("price target" OR upgrade OR downgrade OR "initiates coverage") analyst'
        ),
        count=20,
    ),
    NewsSource(
        name="brave-news-macro-markets",
        category="MACRO",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query='WTI OR Brent OR gold OR DXY OR "10-year Treasury" OR VIX OR USD KRW market',
        count=20,
    ),
)


RSS_FALLBACK_SOURCES = (
    NewsSource(
        name="google-news-geo-policy",
        category="GEO",
        provider="google_rss",
        url=google_news_search_url(
            "Iran OR Middle East OR Russia OR China OR tariff OR sanctions OR White House executive order market stocks"
        ),
    ),
    NewsSource(
        name="google-news-earnings-guidance",
        category="EARN",
        provider="google_rss",
        url=google_news_search_url(
            "earnings OR guidance OR EPS OR revenue stock market large cap"
        ),
    ),
    NewsSource(
        name="google-news-ma-buyback",
        category="MA",
        provider="google_rss",
        url=google_news_search_url(
            "acquisition OR merger OR buyout OR buyout offer OR takeover bid "
            "OR binding offer OR repurchase OR strategic investment OR stake deal stock"
        ),
    ),
    NewsSource(
        name="google-news-megacap-strategic",
        category="STRAT",
        provider="google_rss",
        url=google_news_search_url(
            "NVDA OR AMD OR AAPL OR MSFT OR GOOGL OR AVGO partnership supply deal investment"
        ),
    ),
    NewsSource(
        name="google-news-largecap-movers",
        category="MOVE",
        provider="google_rss",
        url=google_news_search_url(
            "shares surge OR shares plunge OR premarket movers OR S&P 500 movers stock"
        ),
    ),
    NewsSource(
        name="google-news-analyst-actions",
        category="ANAL",
        provider="google_rss",
        url=google_news_search_url(
            "(NVDA OR AMD OR AVGO OR MSFT OR AAPL OR GOOGL OR META OR TSLA "
            "OR CRWV OR NBIS) (price target OR upgrade OR downgrade OR "
            "initiates coverage) analyst"
        ),
    ),
    NewsSource(
        name="google-news-macro-markets",
        category="MACRO",
        provider="google_rss",
        url=google_news_search_url(
            "WTI OR Brent OR gold OR DXY OR 10-year Treasury OR VIX OR USD KRW market"
        ),
    ),
)


OFFICIAL_FEED_SOURCES = (
    NewsSource(
        name="federal-reserve-press",
        category="MACRO",
        provider="official_rss",
        url="https://www.federalreserve.gov/feeds/press_all.xml",
    ),
    NewsSource(
        name="white-house-presidential-actions",
        category="GEO",
        provider="official_rss",
        url="https://www.whitehouse.gov/presidential-actions/feed/",
    ),
)


DEFAULT_SOURCES = BRAVE_NEWS_SOURCES + RSS_FALLBACK_SOURCES + OFFICIAL_FEED_SOURCES


def required_category_status(categories: set[str]) -> dict[str, bool]:
    return {category: category in categories for category in REQUIRED_CATEGORIES}
