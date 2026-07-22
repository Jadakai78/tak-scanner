from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
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
    OraclePanelPayload,
    OracleSummary,
    make_oracle_action,
)

logger = logging.getLogger(__name__)

MODULE_DIR = Path(__file__).resolve().parent

FG_URL = "https://api.alternative.me/fng/"

SCAN_HOURS_UTC = [14, 18, 22, 2, 6, 10]
SCAN_MINUTE_UTC = 0

INTENT_RANK = {
    "EVICTION_NOTICE": 1,
    "POWER_PLAY": 2,
    "STRUCTURE_BREAK": 3,
    "SA_DELTA": 4,
    "B_DELTA": 5,
    "STARTER_DELTA": 6,
}

MAX_SAMMY_ALERTS = 5
SIGNAL_TTL_HOURS = 48

BUS_PATH = (
    Path("/app/data/signal_bus.json")
    if Path("/app/data").exists()
    else MODULE_DIR / "signal_bus.json"
)


def write_bus_snapshot(payload: Dict[str, Any], output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info("Bus snapshot written to %s", output_path)
    except Exception as e:
        logger.error("Failed to write bus snapshot: %s", e)
        raise


def upload_bus_snapshot_to_r2(local_path: Path, object_name: str = "signal_bus.json") -> None:
    """
    Upload the bus snapshot JSON to Cloudflare R2 using S3-compatible boto3 client.

    Expects these environment variables (already set in Railway):
      - R2_ACCOUNT_ID
      - R2_ACCESS_KEY_ID
      - R2_SECRET_ACCESS_KEY
      - R2_BUCKET_NAME
    """
    try:
        account_id = os.environ["R2_ACCOUNT_ID"]
        access_key_id = os.environ["R2_ACCESS_KEY_ID"]
        secret_access_key = os.environ["R2_SECRET_ACCESS_KEY"]
        bucket_name = os.environ["R2_BUCKET_NAME"]

        client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

        client.upload_file(str(local_path), bucket_name, object_name)
        logger.info(
            "Bus snapshot uploaded to R2 bucket=%s object=%s",
            bucket_name,
            object_name,
        )
    except Exception as e:
        logger.exception("Failed to upload bus snapshot to R2: %s", e)


class TakScannerV4:
    def __init__(self) -> None:
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        self.bus = SignalBus(path=BUS_PATH)
        self.classifier = RegimeClassifier()
        self.universe = PairUniverse()
        self.last_signals: List[Dict[str, Any]] = []

        try:
            registry = SpecialistRegistry.from_engine_map(ENGINECLASSES, REGIMEENGINES)
        except Exception:
            registry = SpecialistRegistry()
            try:
                registry = registry.from_engine_map(ENGINECLASSES, REGIMEENGINES)
            except Exception as e:
                logger.exception(
                    "Failed to build SpecialistRegistry from engine map: %s", e
                )
                registry = SpecialistRegistry()

        self.orchestrator = ScannerOrchestrator(specialist_registry=registry)

    def next_scan_time(self, now: datetime) -> datetime:
        candidates: List[datetime] = []
        for hour in SCAN_HOURS_UTC:
            candidate = now.replace(
                hour=hour, minute=SCAN_MINUTE_UTC, second=0, microsecond=0
            )
            if candidate <= now:
                candidate = candidate + timedelta(days=1)
            candidates.append(candidate)
        return min(candidates)

    def fetch_fear_greed(self) -> int:
        try:
            resp = requests.get(FG_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return int(data["data"][0]["value"])
        except Exception as e:
            logger.warning("FG fetch failed: %s", e)
            return 50

    def run_scan(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        logger.info("Starting Oracle-first scan at %s", now.isoformat())

        fg = self.fetch_fear_greed()
        active = self.universe.get_active_pairs()
        dead_count = max(len(getattr(self.universe, "pairs", [])) - len(active), 0)

        regimemap: Dict[str, str] = {}
        contexts: List[PairContext] = []

        logger.info("Oracle building contexts for %s active pairs", len(active))

        for pair in active:
            regime = self.classifier.classify_pair(pair)
            regimemap[pair] = regime
            contexts.append(
                PairContext(
                    pair=pair,
                    market_regime=regime,
                    timeframe="1h",
                    fear_greed=fg,
                    session=None,
                    indicators=None,
                    market_state=None,
                    shared_state=None,
                )
            )

        logger.info("Oracle calling orchestrator with %s contexts", len(contexts))
        candidates = self.orchestrator.run(contexts, shared_state=None)
        logger.info("Oracle orchestrator returned %s candidates", len(candidates))

        oracle_actions: List[Any] = []
        action_priority = {"signal": 0, "caution": 1, "kill": 2, "flat": 3}

        for candidate in candidates:
            action_type = self.candidate_action(candidate)
            reason = getattr(candidate, "thesis", "").strip() or "Oracle setup recognized"

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
                context_regime=regimemap.get(getattr(candidate, "pair", ""), "UNKNOWN"),
                fg=fg,
                specialist=getattr(candidate, "specialist", None),
                intent=getattr(candidate, "intent", None),
                grade=getattr(candidate, "grade", None),
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
            a
            for a in oracle_actions
            if a.action != "signal" or float(a.score or 0.0) >= 0.70
        ]

        summary = OracleSummary(
            fg=fg,
            fg_label=self.fg_label(fg),
            market_phase=self.market_phase(fg, len(filtered_actions)),
            session=self.get_session(now),
            regime_summary=self.regime_summary(fg, regimemap, filtered_actions),
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

        payload_obj = OraclePayload(
            payload_from_actions(
                last_scan=now.isoformat(),
                next_scan=self.next_scan_time(now).isoformat(),
                oracle_summary=summary,
                actions=filtered_actions,
                positions=[],
                health=health,
            )
        )

        payload = payload_obj.to_dict()
        payload["payload_last_scan"] = payload.get("last_scan")
        payload["payload_next_scan"] = payload.get("next_scan")
        payload["payload_fg"] = fg
        payload["payload_active_pairs"] = len(active)
        payload["payload_dead_pairs"] = dead_count
        payload["payload_regime_map"] = regimemap
        payload["payload_session_stats"] = {
            "scanned": len(active),
            "actions": len(filtered_actions),
            "signals": len(payload.get("signals", [])),
            "killed": len(payload.get("killed_signals", [])),
        }
        payload["payload_quiet_hours"] = now.hour not in SCAN_HOURS_UTC
        payload["payload_sprint_mode"] = False

        self.trim_last_signals()
        self.last_signals.extend(payload.get("signals", []))

        write_bus_snapshot(payload, self.bus.path)
        upload_bus_snapshot_to_r2(self.bus.path)

        self.fire_alerts(payload.get("signals", [])[:MAX_SAMMY_ALERTS])

        logger.info(
            "Oracle scan complete %s actions %s signals %s killed",
            len(filtered_actions),
            len(payload.get("signals", [])),
            len(payload.get("killed_signals", [])),
        )

        return payload

    def candidate_action(self, candidate: Any) -> str:
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
            if decision in ("reject", "kill", "drop"):
                return "kill"
            if decision in ("caution", "watchlist", "wait"):
                return "caution"

        return "signal"

    def trim_last_signals(self) -> None:
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
                logger.info(
                    "Alert fired for %s", signal.get("pair", "unknown")
                )
                logger.debug("Alert payload preview %s", message[:240])
            except Exception as e:
                logger.exception("Alert failed: %s", e)

    def fg_label(self, fg: int) -> str:
        if fg <= 25:
            return "Extreme Fear"
        if fg <= 45:
            return "Fear"
        if fg <= 55:
            return "Neutral"
        if fg <= 75:
            return "Greed"
        return "Extreme Greed"

    def get_session(self, now: datetime) -> str:
        hour = now.hour
        if 0 <= hour < 8:
            return "Asia"
        if 8 <= hour < 16:
            return "London"
        return "NY"

    def market_phase(self, fg: int, signal_count: int) -> str:
        if signal_count >= 10:
            return "HOT"
        if signal_count >= 5:
            return "WARM"
        if signal_count >= 1:
            return "COLD"
        if fg <= 35:
            return "FEAR"
        return "DEAD"

    def regimesummary(
        self,
        fg: int,
        regimemap: Dict[str, str],
        actions: List[Any],
    ) -> str:
        if not actions:
            if fg <= 35:
                return "Fear-heavy tape with no qualified Oracle actions"
            return "Quiet tape with no qualified Oracle actions"

        regimecounts: Dict[str, int] = {}
        for regime in regimemap.values():
            regimecounts[regime] = regimecounts.get(regime, 0) + 1

        topregime = max(regimecounts, key=regimecounts.get) if regimecounts else "UNKNOWN"
        signalcount = len([a for a in actions if getattr(a, "action", None) == "signal"])
        cautioncount = len([a for a in actions if getattr(a, "action", None) == "caution"])

        return f"{topregime} dominant FG {fg} signalcount {signalcount} signals cautioncount {cautioncount} cautions"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = TakScannerV4()
    scanner.run_scan()
