"""remi_macro.py — Live economic calendar for Remi's Q1.

Fetches the current week's economic events from the ForexFactory JSON feed
(free, no API key, updated weekly). Identifies HIGH-impact USD events within
a configurable lookahead window and returns a structured risk assessment.

High-impact USD events suppress or CAUTION crypto longs/shorts because:
  - Fed statements cause instant BTC correlation spikes
  - NFP / CPI create 5-15 min extreme wicks that eat stops
  - Rate decisions (FOMC) cause regime flips mid-candle

No API key required. Data cached per session (refreshes on new process start).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger("remi_macro")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_TTL = 3600          # re-fetch weekly data every hour (it rarely changes)
BACKOFF_TTL = 600         # after a 429/error, wait 10 min before retrying
REQUEST_TIMEOUT = 8

# How far ahead to look for upcoming events
DEFAULT_LOOKAHEAD_MINUTES = 120   # ±2h window (standard Remi CAUTION zone)
KILL_LOOKAHEAD_MINUTES    = 30    # within 30 min = KILL zone (stops will get hunted)

# Countries whose events directly move crypto
HIGH_PRIORITY_COUNTRIES = {"USD", "EUR", "GBP", "JPY", "CNY"}

# Event titles that are especially dangerous for crypto (substring match)
CRYPTO_KILLER_EVENTS = [
    "non-farm", "nfp", "unemployment rate", "cpi", "inflation",
    "federal reserve", "fomc", "fed chairman", "rate decision",
    "gdp", "interest rate", "pce", "jobs", "payroll",
    "treasury", "sec", "crypto", "digital asset",
]

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_cache: Tuple[float, List[dict]] = (0.0, [])


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class MacroEvent:
    title: str
    country: str
    impact: str                 # "High" / "Medium" / "Low"
    event_time: datetime        # UTC-aware
    minutes_away: float         # positive = future, negative = past
    is_crypto_killer: bool = False
    forecast: str = ""
    previous: str = ""


@dataclass
class MacroRisk:
    """Summary returned to Remi Q1."""
    caution: bool = False
    kill: bool = False
    reason: str = ""
    upcoming_events: List[MacroEvent] = field(default_factory=list)
    next_high_impact: Optional[MacroEvent] = None


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------
class RemiMacro:
    """Live macro calendar risk check.

    Usage::

        macro = RemiMacro()
        risk = macro.check(now=datetime.now(timezone.utc))
        if risk.kill:
            return remi._kill(signal, "MACRO_KILL")
        if risk.caution:
            caution = True
    """

    def __init__(
        self,
        lookahead_minutes: int = DEFAULT_LOOKAHEAD_MINUTES,
        kill_minutes: int = KILL_LOOKAHEAD_MINUTES,
    ) -> None:
        self.lookahead_minutes = lookahead_minutes
        self.kill_minutes = kill_minutes

    # ------------------------------------------------------------------
    def fetch_events(self, force: bool = False) -> List[dict]:
        """Return cached or fresh events from ForexFactory."""
        global _cache
        now_ts = time.monotonic()
        cached_ts, cached_data = _cache

        cached_ts, cached_data = _cache
        # Use cache if fresh OR if a fetch just failed (backoff_ts protection)
        if not force and cached_data and (now_ts - cached_ts) < CACHE_TTL:
            return cached_data

        try:
            req = urllib.request.Request(
                CALENDAR_URL,
                headers={"User-Agent": "JHL-RemiMacro/2.0"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw_data = json.loads(resp.read())
            _cache = (now_ts, raw_data)
            logger.info("RemiMacro: fetched %d events", len(raw_data))
            return raw_data
        except Exception as exc:
            logger.warning("RemiMacro: calendar fetch failed — %s. Using stale cache.", exc)
            # Set cache timestamp to backoff window so we don't hammer on errors
            _cache = (now_ts - CACHE_TTL + BACKOFF_TTL, cached_data)
            return cached_data  # fall back to stale cache (empty list on first run)

    # ------------------------------------------------------------------
    def check(self, now: Optional[datetime] = None) -> MacroRisk:
        """Check for dangerous macro events around the current time.

        Args:
            now: Override for testing (defaults to UTC now).

        Returns:
            :class:`MacroRisk` with caution/kill flags and event list.
        """
        now = now or datetime.now(timezone.utc)
        raw_events = self.fetch_events()

        upcoming: List[MacroEvent] = []
        for ev in raw_events:
            parsed = self._parse_event(ev, now)
            if parsed is None:
                continue
            # Only care about future events within lookahead, or very recent
            if -15 <= parsed.minutes_away <= self.lookahead_minutes:
                upcoming.append(parsed)

        # Filter to HIGH impact in priority countries
        dangerous = [
            e for e in upcoming
            if e.impact.lower() == "high"
            and e.country in HIGH_PRIORITY_COUNTRIES
        ]

        risk = MacroRisk(upcoming_events=dangerous)

        if not dangerous:
            return risk

        # Sort by time proximity
        dangerous.sort(key=lambda e: abs(e.minutes_away))
        risk.next_high_impact = dangerous[0]

        for ev in dangerous:
            if 0 <= ev.minutes_away <= self.kill_minutes or ev.minutes_away < 0:
                # Within 30 min or already fired (wick danger)
                if ev.is_crypto_killer:
                    risk.kill = True
                    risk.reason = f"MACRO_KILL: {ev.title} ({ev.country}) in {ev.minutes_away:.0f}min"
                    return risk
                else:
                    risk.caution = True
                    risk.reason = f"MACRO_CAUTION: {ev.title} ({ev.country}) in {ev.minutes_away:.0f}min"
            elif ev.minutes_away <= self.lookahead_minutes:
                # Within lookahead window
                risk.caution = True
                risk.reason = (
                    f"MACRO_CAUTION: {ev.title} ({ev.country}) "
                    f"in {ev.minutes_away:.0f}min"
                )

        return risk

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_event(raw: dict, now: datetime) -> Optional[MacroEvent]:
        """Parse a raw ForexFactory event dict into a MacroEvent."""
        try:
            date_str = raw.get("date", "")
            if not date_str:
                return None
            # ForexFactory returns ISO 8601 with UTC offset e.g. "2026-07-03T08:30:00-04:00"
            event_time = datetime.fromisoformat(date_str).astimezone(timezone.utc)
            minutes_away = (event_time - now).total_seconds() / 60

            title = raw.get("title", "")
            is_killer = any(kw in title.lower() for kw in CRYPTO_KILLER_EVENTS)

            return MacroEvent(
                title=title,
                country=raw.get("country", ""),
                impact=raw.get("impact", "Low"),
                event_time=event_time,
                minutes_away=minutes_away,
                is_crypto_killer=is_killer,
                forecast=raw.get("forecast", ""),
                previous=raw.get("previous", ""),
            )
        except (ValueError, TypeError, KeyError):
            return None

    # ------------------------------------------------------------------
    def get_next_high_impact(self, now: Optional[datetime] = None) -> Optional[MacroEvent]:
        """Return the next HIGH-impact USD event regardless of window."""
        now = now or datetime.now(timezone.utc)
        raw_events = self.fetch_events()
        candidates = []
        for ev in raw_events:
            parsed = self._parse_event(ev, now)
            if parsed and parsed.impact.lower() == "high" and parsed.country == "USD" and parsed.minutes_away > 0:
                candidates.append(parsed)
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.minutes_away)
        return candidates[0]


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    logger.info("=== RemiMacro live test ===")

    macro = RemiMacro()
    now = datetime.now(timezone.utc)
    print(f"\nCurrent UTC time: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    # Show all high-impact events this week
    raw = macro.fetch_events(force=True)
    high_usd = [
        e for e in raw
        if e.get("impact", "").lower() == "high"
        and e.get("country") in HIGH_PRIORITY_COUNTRIES
    ]
    print(f"\nHigh-impact events this week ({len(high_usd)} total):")
    for e in high_usd:
        parsed = macro._parse_event(e, now)
        if parsed:
            direction = "ago" if parsed.minutes_away < 0 else "away"
            print(f"  {parsed.event_time.strftime('%a %m/%d %H:%M')} UTC | "
                  f"{parsed.country} | {parsed.title} | "
                  f"{abs(parsed.minutes_away):.0f}min {direction} "
                  f"{'[CRYPTO-KILLER]' if parsed.is_crypto_killer else ''}")

    # Run the check
    risk = macro.check(now)
    print(f"\nRisk check result:")
    print(f"  caution = {risk.caution}")
    print(f"  kill    = {risk.kill}")
    print(f"  reason  = {risk.reason or 'None'}")

    nxt = macro.get_next_high_impact(now)
    if nxt:
        print(f"\nNext HIGH-impact USD event:")
        print(f"  {nxt.title} @ {nxt.event_time.strftime('%a %m/%d %H:%M')} UTC "
              f"({nxt.minutes_away:.0f}min away)")
    else:
        print("\nNo upcoming HIGH-impact USD events found this week.")
