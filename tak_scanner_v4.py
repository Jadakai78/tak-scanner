from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from convictionscorer import ConvictionScorer
from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier
from remi import Remi
from signalbus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES
from gimba_formatter import format_gimba_message
from scannermodels import PairContext
from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry
from oracle_schema import (
    OracleHealth,
    OraclePayload,
    OracleSummary,
    payload_from_actions,
    make_oracle_action,
)

SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

PROPS_PAIRS = {
    "BTC", "ETH", "SOL", "HYPE", "XRP", "ZEC", "SUI", "ADA", "DOGE", "AAVE",
    "LTC", "TAO", "LINK", "UNI", "NEAR", "ARB", "ONDO", "TRX", "AVAX", "DOT",
    "BCH", "PUMP", "CRV", "ALGO", "TIA", "HBAR", "WLD", "FARTCOIN", "POL", "XPL",
    "WIF", "BNB", "INJ", "FIL", "JUP", "ATOM", "LDO", "PENGU", "VIRTUAL", "RENDER",
    "JTO", "GRASS", "KAITO", "TRUMP", "ASTER", "OP", "POPCAT", "APT", "S", "STX",
    "ETC", "MOODENG", "PNUT", "AIXBT",
}

logger = logging.getLogger(__name__)
MODULE_DIR = Path(__file__).resolve().parent
FG_URL = "https://api.alternative.me/fng/"
SCAN_HOURS_UTC = [14, 18, 22, 2, 6, 10]
SCAN_MINUTE_UTC = 0
INTENT_RANK = {
    "EVICTION_NOTICE": 1,
    "POWER_PLAY": 2,
    "STRUCTURE_BREAK": 3,
    "S_A_DELTA": 4,
    "B_DELTA": 5,
    "STARTER_DELTA": 6,
}

MAX_SAMMY_ALERTS = 5
SIGNAL_TTL_HOURS = 48
BUS_PATH = Path("/app/data/signal_bus.json") if Path("/app/data").exists() else MODULE_DIR / "signal_bus.json"


def write_bus_snapshot(payload: Dict[str, Any], output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info("Bus snapshot written to %s", output_path)
    except Exception as e:
        logger.error("Failed to write bus snapshot: %s", e)
        raise


class TakScannerV4:
    def __init__(self):
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        self.bus = SignalBus(path=BUS_PATH)
        self.classifier = RegimeClassifier()
        self.universe = PairUniverse()
        self.last_signals: List[Dict[str, Any]] = []

        try:
            registry = SpecialistRegistry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
        except Exception:
            registry = SpecialistRegistry()
        try:
            registry = registry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
        except Exception as e:
            logger.exception("Failed to build SpecialistRegistry from engine map: %s", e)
            registry = SpecialistRegistry()

        self.orchestrator = ScannerOrchestrator(specialist_registry=registry)

    def next_scan_time(self, now: datetime) -> datetime:
        candidates = []
        for hour in SCAN_HOURS_UTC:
            candidate = now.replace(hour=hour, minute=SCAN_MINUTE_UTC, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
        return min(candidates)

    def fetch_fear_greed(self) -> int:
        try:
            resp = requests.get(FG_URL, timeout=10)
            resp.raise_for_status()
            return int(resp.json()["data"][0]["value"])
        except Exception as e:
            logger.warning("FG fetch failed: %s", e)
            return 50

    def _extract_symbol(self, item: Any) -> Optional[str]:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            symbol = item.get("pair") or item.get("symbol") or item.get("altname") or item.get("wsname")
            if isinstance(symbol, str):
                return symbol
        return None

    def run_scan(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        logger.info("Starting Oracle-first scan at %s", now.isoformat())

        fg = self.fetch_fear_greed()
        fg_score = fg
        raw_active
        dead_count = max(len(getattr(self.universe, "pairs", [])) - len(active), 0)

        regime_map: Dict[str, str] = {}
        contexts: List[PairContext] = []

        logger.info("Props filter kept %s of %s active pairs", len(active), len(raw_active))
        logger.info("Oracle building contexts for %s active pairs", len(active))

        for pair in active:
            try:
                ohlc_df = self.universe.fetch_ohlc(pair, interval=240)
                regime = self.classifier.classify(pair, ohlc_df, fg_score)
            except Exception as e:
                logger.warning("Skipping %s due to regime prep failure: %s", pair, e)
                continue

            regime_map[pair] = regime
            contexts.append(
                PairContext(
                    pair=pair,
                    market_regime=regime,
                    timeframe="1h",
                    fear_greed=fg,
                    session=None,
                    indicators={},
                    market_state={},
                )
            )

        shared_state = {"fgscore": fg, "timeframe": "1h"}
        logger.info("Oracle calling orchestrator with %s contexts", len(contexts))
        candidates = self.orchestrator.run(contexts, shared_state)
        logger.info("Oracle orchestrator returned %s candidates", len(candidates))

        oracle_actions = []
        action_priority = {"signal": 0, "caution": 1, "kill": 2, "flat": 3}

        for candidate in candidates:
            action_type = self._candidate_action(candidate)
            reason = (getattr(candidate, "thesis", "") or "").strip() or "Oracle setup recognized"

            oracle_action = make_oracle_action(
                pair=getattr(candidate, "pair", "UNKNOWN"),
                action=action_type,
                timestamp=now.isoformat(),
                setup_family=getattr(candidate, "setup_type", None),
                side=getattr(candidate, "side", None),
                confidence=float(getattr(candidate, "confidence", 0.0) or 0.0),
                score=float(getattr(candidate, "score", 0.0) or 0.0),
                why_now=reason[:220],
                entry_idea=getattr(candidate, "entry_idea", None),
                stop_idea=getattr(candidate, "stop_idea", None),
                target_idea=getattr(candidate, "target_idea", None),
                tags=list(getattr(candidate, "tags", []) or []),
                warnings=list(getattr(candidate, "warnings", []) or []),
                context={
                    "regime": regime_map.get(getattr(candidate, "pair", ""), "UNKNOWN"),
                    "fg": fg,
                    "specialist": getattr(candidate, "specialist", None),
                    "intent": getattr(candidate, "intent", None),
                    "grade": getattr(candidate, "grade", None),
                },
            )
            oracle_actions.append(oracle_action)

        oracle_actions.sort(
            key=lambda a: (
                action_priority.get(a.action, 99),
                -float(a.score or 0.0),
                -float(a.confidence or 0.0),
            )
        )

        filtered_actions = [
            a for a in oracle_actions
            if a.action != "signal" or float(a.score or 0.0) >= 0.70
        ]

        summary = OracleSummary(
            fg=fg,
            fg_label=self._fg_label(fg),
            market_phase=self._market_phase(
                fg, len([a for a in filtered_actions if a.action == "signal"])
            ),
            session=self._get_session(now),
            regime_summary=self._regime_summary(fg, regime_map, filtered_actions),
            active_pairs=len(active),
            dead_pairs=dead_count,
            scan_mode="scheduled",
        )

        health = OracleHealth(
            scheduler_ok=True,
            bus_ok=True,
            publish_ok=True,
            last_error=None,
            source_path=str(self.bus.path),
            heartbeat=now.isoformat(),
        )

        payload_obj: OraclePayload = payload_from_actions(
            last_scan=now.isoformat(),
            next_scan=self.next_scan_time(now).isoformat(),
            oracle=summary,
            actions=filtered_actions,
            positions=[],
            health=health,
        )
        payload = payload_obj.to_dict()

        payload["lastscan"] = payload["last_scan"]
        payload["nextscan"] = payload["next_scan"]
        payload["fg"] = fg
        payload["activepairs"] = len(active)
        payload["deadpairs"] = dead_count
        payload["regimemap"] = regime_map
        payload["sessionstats"] = {
            "scanned": len(active),
            "actions": len(filtered_actions),
            "signals": len(payload.get("signals", [])),
            "killed": len(payload.get("killedsignals", [])),
        }
        payload["quiethours"] = now.hour not in SCAN_HOURS_UTC
        payload["sprintmode"] = False

        self._trim_last_signals()
        self.last_signals = payload.get("signals", [])

        write_bus_snapshot(payload, self.bus.path)
        self.fire_alerts(payload.get("signals", [])[:MAX_SAMMY_ALERTS])

        logger.info(
            "Oracle scan complete: %s actions | %s signals | %s killed",
            len(filtered_actions),
            len(payload.get("signals", [])),
            len(payload.get("killedsignals", [])),
        )
        return payload

    def _candidate_action(self, candidate: Any) -> str:
        council = getattr(candidate, "council", None)
        review = getattr(candidate, "review", None)

        if council is not None:
            route = getattr(council, "route", None)
            if route == "killed_signals":
                return "kill"
            if route == "caution_signals":
                return "caution"

        if review is not None:
            decision = getattr(review, "decision", None)
            if decision in {"reject", "kill", "drop"}:
                return "kill"
            if decision in {"caution", "watchlist", "wait"}:
                return "caution"

        return "signal"

    def _trim_last_signals(self) -> None:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=SIGNAL_TTL_HOURS)
            trimmed: List[Dict[str, Any]] = []
            for signal in self.last_signals:
                ts = signal.get("timestamp")
                if not ts:
                    continue
                try:
                    t = datetime.fromisoformat(ts)
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    if t >= cutoff:
                        trimmed.append(signal)
                except Exception:
                    continue
            self.last_signals = trimmed
        except Exception:
            logger.debug("Failed to trim last_signals")

    def fire_alerts(self, top_signals: List[Dict[str, Any]]) -> None:
        for signal in top_signals:
            try:
                message = format_gimba_message(signal)
                logger.info("Alert fired for %s", signal.get("pair", "unknown"))
                logger.debug("Alert payload preview: %s", message[:240])
            except Exception as e:
                logger.exception("Alert failed: %s", e)

    def _fg_label(self, fg: int) -> str:
        if fg < 25:
            return "Extreme Fear"
        if fg < 45:
            return "Fear"
        if fg < 55:
            return "Neutral"
        if fg < 75:
            return "Greed"
        return "Extreme Greed"

    def _get_session(self, now: datetime) -> str:
        hour = now.hour
        if 0 <= hour < 8:
            return "Asia"
        if 8 <= hour < 16:
            return "London"
        return "NY"

    def _market_phase(self, fg: int, signal_count: int) -> str:
        if signal_count >= 10:
            return "HOT"
        if signal_count >= 5:
            return "WARM"
        if signal_count >= 1:
            return "COLD"
        if fg < 35:
            return "FEAR"
        return "DEAD"

    def _regime_summary(self, fg: int, regime_map: Dict[str, str], actions: List[Any]) -> str:
        if not actions:
            if fg < 35:
                return "Fear-heavy tape with no qualified Oracle actions"
            return "Quiet tape with no qualified Oracle actions"

        regime_counts: Dict[str, int] = {}
        for regime in regime_map.values():
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

        top_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "UNKNOWN"
        signal_count = len([a for a in actions if getattr(a, "action", None) == "signal"])
        caution_count = len([a for a in actions if getattr(a, "action", None) == "caution"])

        return f"{top_regime} dominant | FG {fg} | {signal_count} signals | {caution_count} cautions"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = TakScannerV4()
    scanner.run_scan()
