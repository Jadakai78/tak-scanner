"""remi_news.py — Live crypto news sentiment for Remi's Q1.

Pulls headlines from CoinDesk, CoinTelegraph, and Decrypt via free RSS feeds.
Scores each headline as BULLISH (+1), BEARISH (-1), or NEUTRAL (0) using a
curated keyword set. Returns a per-pair sentiment score and a flag for any
breaking negative news that should trigger Remi CAUTION or KILL.

No API key required. Cache TTL is 5 minutes to respect rate limits.
"""

from __future__ import annotations

import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("remi_news")

# ---------------------------------------------------------------------------
# RSS feed sources — all free, no auth
# ---------------------------------------------------------------------------
FEEDS: Dict[str, str] = {
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "Decrypt":       "https://decrypt.co/feed",
}

CACHE_TTL = 300  # seconds (5 min)
REQUEST_TIMEOUT = 8  # seconds per feed

# ---------------------------------------------------------------------------
# Sentiment keyword lists
# ---------------------------------------------------------------------------
BEARISH_KEYWORDS = [
    "crash", "dump", "plunge", "collapse", "ban", "banned", "hack",
    "exploit", "stolen", "breach", "liquidat", "margin call", "bankruptcy",
    "insolvency", "insolvent", "fraud", "scam", "sec", "lawsuit", "fine",
    "sanction", "regulation", "regulatory", "crackdown", "sell-off", "selloff",
    "correction", "bear", "bearish", "drop", "fell", "falling", "decline",
    "loses", "loss", "losses", "down", "fear", "panic", "warning", "risk",
    "concern", "trouble", "problem", "crisis", "contagion", "depegged",
    "depeg", "rug", "exit scam", "ponzi", "arrest", "criminal",
]

BULLISH_KEYWORDS = [
    "rally", "surge", "pump", "soar", "breakout", "bull", "bullish",
    "adoption", "etf", "approval", "approved", "launch", "partnership",
    "upgrade", "halving", "institutional", "investment", "buy", "buying",
    "accumulate", "accumulation", "all-time high", "ath", "record",
    "growth", "gains", "profit", "revenue", "positive", "recover",
    "recovery", "rebound", "moon", "milestone", "integration",
    "mainstream", "treasury", "reserve", "strategic", "listing",
]

# Pair → keywords to match in headlines (includes symbol and common names)
PAIR_KEYWORDS: Dict[str, List[str]] = {
    "XBTUSD":  ["bitcoin", "btc"],
    "ETHUSD":  ["ethereum", "eth", "ether"],
    "SOLUSD":  ["solana", "sol"],
    "XRPUSD":  ["xrp", "ripple"],
    "ADAUSD":  ["cardano", "ada"],
    "DOTUSD":  ["polkadot", "dot"],
    "AVAXUSD": ["avalanche", "avax"],
    "MATICUSD":["polygon", "matic"],
    "LINKUSD": ["chainlink", "link"],
    "LTCUSD":  ["litecoin", "ltc"],
    "DOGEUSD": ["dogecoin", "doge"],
    "UNIUSD":  ["uniswap", "uni"],
    "ATOMUSD": ["cosmos", "atom"],
    "NEARUSD": ["near", "near protocol"],
    "FTMUSD":  ["fantom", "ftm"],
}

# High-impact words that alone justify Remi CAUTION regardless of pair
GLOBAL_DANGER_WORDS = [
    "sec halt", "emergency", "war", "sanctions", "terror", "nuclear",
    "fed rate", "federal reserve", "fomc decision", "rate hike", "rate cut",
    "inflation data", "cpi report", "nfp report", "jobs report",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Headline:
    source: str
    title: str
    published: Optional[str]
    sentiment: int = 0          # +1 BULLISH, -1 BEARISH, 0 NEUTRAL
    global_danger: bool = False


@dataclass
class PairSentiment:
    pair: str
    score: int                  # sum of matching headline sentiments
    headline_count: int
    bearish_count: int
    bullish_count: int
    top_bearish: List[str] = field(default_factory=list)
    caution: bool = False
    kill: bool = False          # True if score <= -3 (heavy negative coverage)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_cache: Dict[str, Tuple[float, List[Headline]]] = {}


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------
class RemiFeed:
    """Live crypto news sentiment engine for Remi Q1.

    Usage::

        feed = RemiFeed()
        result = feed.evaluate_pair("XBTUSD")
        if result.kill:
            return remi._kill(signal, "NEWS_KILL")
        if result.caution:
            caution = True
    """

    def __init__(self, feeds: Optional[Dict[str, str]] = None) -> None:
        self._feeds = feeds or FEEDS

    # ------------------------------------------------------------------
    def fetch_headlines(self, force: bool = False) -> List[Headline]:
        """Return cached or fresh headlines from all RSS feeds."""
        now = time.monotonic()
        cache_key = "all"
        cached_ts, cached_items = _cache.get(cache_key, (0.0, []))

        if not force and (now - cached_ts) < CACHE_TTL:
            return cached_items

        all_headlines: List[Headline] = []
        for source, url in self._feeds.items():
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "JHL-RemiFeed/2.0"}
                )
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                    raw = resp.read()
                items = self._parse_rss(raw, source)
                all_headlines.extend(items)
                logger.debug("RemiFeed: %s returned %d headlines", source, len(items))
            except Exception as exc:
                logger.warning("RemiFeed: %s fetch failed — %s", source, exc)

        _cache[cache_key] = (now, all_headlines)
        return all_headlines

    # ------------------------------------------------------------------
    def evaluate_pair(
        self, pair: str, headlines: Optional[List[Headline]] = None
    ) -> PairSentiment:
        """Score sentiment for a single pair.

        Args:
            pair: Kraken pair string e.g. ``'XBTUSD'``.
            headlines: Pre-fetched list (avoids redundant fetch when scoring
                       multiple pairs in one scanner cycle).

        Returns:
            :class:`PairSentiment` with score, flags, and top bearish snippets.
        """
        if headlines is None:
            headlines = self.fetch_headlines()

        pair_kws = PAIR_KEYWORDS.get(pair.upper(), [pair.lower()[:3]])

        matched: List[Headline] = []
        for h in headlines:
            title_lower = h.title.lower()
            if any(kw in title_lower for kw in pair_kws):
                matched.append(h)

        score = sum(h.sentiment for h in matched)
        bearish_count = sum(1 for h in matched if h.sentiment < 0)
        bullish_count = sum(1 for h in matched if h.sentiment > 0)
        top_bearish = [h.title for h in matched if h.sentiment < 0][:3]

        # Global danger check — any headline with danger words = CAUTION
        global_danger = any(h.global_danger for h in headlines)

        # Kill threshold: 3+ bearish headlines with no bullish offset
        kill = score <= -3 and bullish_count == 0
        caution = (score <= -1) or global_danger

        return PairSentiment(
            pair=pair,
            score=score,
            headline_count=len(matched),
            bearish_count=bearish_count,
            bullish_count=bullish_count,
            top_bearish=top_bearish,
            caution=caution,
            kill=kill,
        )

    # ------------------------------------------------------------------
    def evaluate_all_pairs(
        self, pairs: List[str]
    ) -> Dict[str, PairSentiment]:
        """Batch evaluate a list of pairs against one headline fetch."""
        headlines = self.fetch_headlines()
        return {p: self.evaluate_pair(p, headlines) for p in pairs}

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_rss(raw: bytes, source: str) -> List[Headline]:
        """Parse RSS XML into Headline objects."""
        headlines: List[Headline] = []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            logger.warning("RemiFeed: XML parse error (%s): %s", source, exc)
            return headlines

        # Handle both RSS 2.0 (<item>) and Atom (<entry>)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for item in items[:30]:  # cap at 30 per feed
            title_el = item.find("title")
            pub_el   = item.find("pubDate") or item.find("atom:published", ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            pub   = (pub_el.text or "").strip() if pub_el is not None else None

            if not title:
                continue

            sentiment = RemiFeed._score_title(title)
            danger = RemiFeed._is_global_danger(title)
            headlines.append(Headline(
                source=source,
                title=title,
                published=pub,
                sentiment=sentiment,
                global_danger=danger,
            ))

        return headlines

    # ------------------------------------------------------------------
    @staticmethod
    def _score_title(title: str) -> int:
        """Return +1 / -1 / 0 based on keyword presence."""
        t = title.lower()
        bear = sum(1 for kw in BEARISH_KEYWORDS if kw in t)
        bull = sum(1 for kw in BULLISH_KEYWORDS if kw in t)
        if bear > bull:
            return -1
        if bull > bear:
            return 1
        return 0

    # ------------------------------------------------------------------
    @staticmethod
    def _is_global_danger(title: str) -> bool:
        t = title.lower()
        return any(kw in t for kw in GLOBAL_DANGER_WORDS)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    logger.info("=== RemiFeed live test ===")

    feed = RemiFeed()
    headlines = feed.fetch_headlines(force=True)
    logger.info("Total headlines fetched: %d", len(headlines))

    # Show sentiment distribution
    scores = [h.sentiment for h in headlines]
    print(f"\nHeadline breakdown: {len(headlines)} total")
    print(f"  Bullish (+1): {scores.count(1)}")
    print(f"  Bearish (-1): {scores.count(-1)}")
    print(f"  Neutral  (0): {scores.count(0)}")
    print(f"  Global danger flags: {sum(1 for h in headlines if h.global_danger)}")

    print("\n--- Sample bearish headlines ---")
    for h in [h for h in headlines if h.sentiment < 0][:5]:
        print(f"  [{h.source}] {h.title}")

    print("\n--- Sample bullish headlines ---")
    for h in [h for h in headlines if h.sentiment > 0][:5]:
        print(f"  [{h.source}] {h.title}")

    print("\n--- Pair sentiment scores ---")
    test_pairs = ["XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    for pair in test_pairs:
        result = feed.evaluate_pair(pair, headlines)
        flag = "KILL" if result.kill else ("CAUTION" if result.caution else "CLEAN")
        print(f"  {pair}: score={result.score:+d}  "
              f"bull={result.bullish_count} bear={result.bearish_count}  [{flag}]")
        if result.top_bearish:
            for h in result.top_bearish:
                print(f"    -> {h}")
