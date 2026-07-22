from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import requests

from convictionscorer import ConvictionScorer
from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier
from remi import Remi
from signalbus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES
from scannermodels import PairContext
from scannerorchestrator import PanelStateRecord, ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry
from oracle_schema import (
    OracleHealth,
    OracleMarket,
    OraclePanel,
    OraclePanelPayload,
    OraclePanelRow,
    OracleRowContext,
    OracleSummary,
    build_panel_payload,
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

    def run_scan(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        logger.info("Starting Oracle-first scan at %s", now.isoformat())

        fg = self.fetch_fear_greed()
        fg_score = fg

        raw_active = self.universe.get_active_pairs()
        active = [
            item for item in raw_active
            if isinstance(item, dict) and item.get("pair") in PROPS_PAIRS
        ]
        dead_count = max(len(raw_active) - len(active), 0)

        regime_map: Dict[str, str] = {}
        contexts: List[PairContext] = []

        logger.info("Props filter kept %s of %s active pairs", len(active), len(raw_active))
        logger.info("Oracle building contexts for %s active pairs", len(active))

        for item in active:
            pair = item.get("pair")
            rows = item.get("ohlc_4h", [])

            if not pair:
                logger.warning("Skipping active item with no pair: %s", item)
                continue
            if not rows:
                logger.warning("Skipping %s due to missing ohlc_4h", pair)
                continue

            try:
                ohlc_df = pd.DataFrame(
                    rows,
                    columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
                )
                for col in ["open", "high", "low", "close", "vwap", "volume"]:
                    ohlc_df[col] = pd.to_numeric(ohlc_df[col], errors="coerce")
                ohlc_df["time"] = pd.to_numeric(ohlc_df["time"], errors="coerce")
                ohlc_df = ohlc_df.dropna().reset_index(drop=True)

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
                    session=self._get_session(now),
                    indicators={},
                    market_state={},
                )
            )

        regime_counts = dict(Counter(regime_map.values()))
        logger.info("Regime counts: %s", regime_counts)

        shared_state = {"fgscore": fg, "timeframe": "1h"}
        logger.info("Oracle calling orchestrator with %s contexts", len(contexts))
        panel_records = self.orchestrator.run(contexts, shared_state)
        logger.info("Oracle orchestrator returned %s panel records", len(panel_records))

        opportunities: List[OraclePanelRow] = []
        watchlist: List[OraclePanelRow] = []
        killed: List[OraclePanelRow] = []

        for record in panel_records:
            row = self._panel_record_to_row(record, regime_map, fg, now)
            state = row.action_state

            if state == "actionable":
                opportunities.append(row)
            elif state == "watch":
                watchlist.append(row)
            elif state == "killed":
                killed.append(row)

        def row_sort_key(row: OraclePanelRow):
            return (
                -float(row.score or 0.0),
                -float(row.confidence or 0.0),
                float(row.trap_score or 0.0),
            )

        opportunities.sort(key=row_sort_key)
        watchlist.sort(key=row_sort_key)
        killed.sort(key=row_sort_key)

        for i, row in enumerate(opportunities, start=1):
            row.panel_rank = i
        for i, row in enumerate(watchlist, start=1):
            row.panel_rank = i
        for i, row in enumerate(killed, start=1):
            row.panel_rank = i

        top_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "UNKNOWN"
        session = self._get_session(now)
        market_phase = self._market_phase(fg, len(opportunities))

        summary = OracleSummary(
            pairs_scanned=len(active),
            opportunity_count=len(opportunities),
            watchlist_count=len(watchlist),
            killed_count=len(killed),
            top_regime=top_regime,
            market_phase=market_phase,
            active_session=session,
            scan_mode="scheduled",
        )

        market = OracleMarket(
            fear_greed=fg,
            fear_greed_label=self._fg_label(fg),
            session=session,
            market_phase=market_phase,
            regime_counts=regime_counts,
            htf_bias_overview=self._htf_bias_overview(opportunities, watchlist, killed),
            notes=[self._regime_summary(fg, regime_map, panel_records)],
        )

        panel = OraclePanel(
            default_sort="panel_rank",
            default_view="opportunities",
            notes=["Panel-first Oracle build. External alerts disabled."],
        )

        health = OracleHealth(
            writer_ok=True,
            reader_ok=True,
            publish_ok=True,
            api_ready=True,
            last_error=None,
            source_path=str(self.bus.path),
            bus_path=str(self.bus.path),
            heartbeat=now.isoformat(),
        )

        payload_obj: OraclePanelPayload = build_panel_payload(
            generated_at=now.isoformat(),
            last_scan=now.isoformat(),
            next_scan=self.next_scan_time(now).isoformat(),
            summary=summary,
            market=market,
            panel=panel,
            opportunities=opportunities,
            watchlist=watchlist,
            killed=killed,
            health=health,
        )

        payload = payload_obj.to_dict()
        payload["sessionstats"] = {
            "scanned": len(active),
            "opportunities": len(opportunities),
            "watchlist": len(watchlist),
            "killed": len(killed),
            "dead_pairs_filtered_outside_props": dead_count,
        }
        payload["regime_map"] = regime_map
        payload["quiet_hours"] = now.hour not in SCAN_HOURS_UTC
        payload["sprint_mode"] = False

        self._trim_last_signals()
        self.last_signals = [row.to_dict() for row in opportunities]

        write_bus_snapshot(payload, self.bus.path)

        logger.info(
            "Oracle scan complete: %s opportunities | %s watchlist | %s killed",
            len(opportunities),
            len(watchlist),
            len(killed),
        )
        return payload

    def _panel_record_to_row(
        self,
        record: PanelStateRecord,
        regime_map: Dict[str, str],
        fg: int,
        now: datetime,
    ) -> OraclePanelRow:
        oracle_context = getattr(record, "oracle_context", {}) or {}
        claims = list(getattr(record, "weapon_claims", []) or [])
        lead_claim = claims[0] if claims else {}

        board_state = str(getattr(record, "board_state", "wait") or "wait").lower()
        action_state = {
            "execute": "actionable",
            "caution": "watch",
            "wait": "watch",
            "dead": "killed",
        }.get(board_state, "watch")

        pair = getattr(record, "pair", "UNKNOWN")
        side = str(getattr(record, "side", "neutral") or "neutral").lower()
        score = float(getattr(record, "score", 0.0) or 0.0)
        confidence = float(getattr(record, "confidence", 0.0) or 0.0)
        trap_score = self._trap_score_from_record(record, claims)
        offense_score = float(lead_claim.get("score", score) or score)
        defense_score = max(0.0, round(confidence - trap_score, 4))

        warnings = list(getattr(record, "warnings", []) or [])
        tags = list(getattr(record, "tags", []) or [])

        kill_reasons = []
        if action_state == "killed":
            kill_reasons.append(getattr(record, "trap_state", None) or "dead_panel_state")

        return OraclePanelRow(
            pair=pair,
            panel_rank=0,
            action_state=action_state,
            side=side,
            setup_family=lead_claim.get("setup_type"),
            specialist=getattr(record, "primary_weapon", None),
            regime=getattr(record, "market_regime", None) or regime_map.get(pair, "UNKNOWN"),
            htf_bias=getattr(record, "oracle_bias", None),
            htf_alignment=self._htf_alignment(side, getattr(record, "oracle_bias", None)),
            offense_score=offense_score,
            defense_score=defense_score,
            trap_score=trap_score,
            confidence=confidence,
            score=score,
            why_now=(getattr(record, "reason", "") or "Oracle panel state recognized")[:220],
            entry_idea=None,
            stop_idea=None,
            target_idea=None,
            warnings=warnings,
            kill_reasons=kill_reasons,
            tags=tags,
            oracle_context=OracleRowContext(
                timeframe=getattr(record, "timeframe", None) or oracle_context.get("timeframe", "1h"),
                session=oracle_context.get("session", self._get_session(now)),
                fear_greed=fg,
                htf_bias=getattr(record, "oracle_bias", None),
                market_regime=getattr(record, "market_regime", None) or regime_map.get(pair, "UNKNOWN"),
            ),
            indicators=dict(oracle_context.get("indicators", {}) or oracle_context.get("pair_indicators", {}) or {}),
            diagnostics={
                "board_state": board_state,
                "primary_weapon": getattr(record, "primary_weapon", None),
                "weapon_claim_count": len(claims),
                "weapon_claims": claims,
                "oracle_rsi_trend": getattr(record, "oracle_rsi_trend", None),
                "oracle_structure_trend": getattr(record, "oracle_structure_trend", None),
                "trap_state": getattr(record, "trap_state", None),
                "pressure_state": getattr(record, "pressure_state", None),
            },
        )

    def _trap_score_from_record(self, record: PanelStateRecord, claims: List[Dict[str, Any]]) -> float:
        trap_state = str(getattr(record, "trap_state", "") or "").lower()
        if trap_state in {"dead", "invalid", "blocked", "do_not_trade"}:
            return 1.0
        if trap_state in {"trap", "high_trap_risk", "retail_trap", "squeeze_risk", "fake_break_risk"}:
            return 0.75

        for claim in claims:
            evidence = dict(claim.get("evidence", {}) or {})
            if "trap_score" in evidence:
                try:
                    return float(evidence.get("trap_score") or 0.0)
                except Exception:
                    pass

            context = dict(claim.get("context", {}) or {})
            if "trap_score" in context:
                try:
                    return float(context.get("trap_score") or 0.0)
                except Exception:
                    pass

        return 0.0

    def _htf_alignment(self, side: str, bias: Any) -> str:
        side_text = str(side or "").lower()
        bias_text = str(bias or "").lower()

        bullish = {"long", "buy", "bull", "bullish", "up"}
        bearish = {"short", "sell", "bear", "bearish", "down"}

        if side_text in bullish and bias_text in bullish:
            return "aligned"
        if side_text in bearish and bias_text in bearish:
            return "aligned"
        if side_text in bullish and bias_text in bearish:
            return "counter"
        if side_text in bearish and bias_text in bullish:
            return "counter"
        return "mixed"

    def _htf_bias_overview(
        self,
        opportunities: List[OraclePanelRow],
        watchlist: List[OraclePanelRow],
        killed: List[OraclePanelRow],
    ) -> Dict[str, int]:
        counts = {"bullish_pairs": 0, "bearish_pairs": 0, "mixed_pairs": 0}
        seen: Dict[str, str] = {}

        for row in [*opportunities, *watchlist, *killed]:
            if not row.pair:
                continue
            bias = (row.htf_bias or "").lower().strip()
            if bias in {"bullish", "long"}:
                current = "bullish"
            elif bias in {"bearish", "short"}:
                current = "bearish"
            else:
                current = "mixed"

            prior = seen.get(row.pair)
            if prior and prior != current:
                seen[row.pair] = "mixed"
            else:
                seen[row.pair] = current

        for state in seen.values():
            if state == "bullish":
                counts["bullish_pairs"] += 1
            elif state == "bearish":
                counts["bearish_pairs"] += 1
            else:
                counts["mixed_pairs"] += 1

        return counts

    def _trim_last_signals(self) -> None:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=SIGNAL_TTL_HOURS)
            trimmed: List[Dict[str, Any]] = []
            for signal in self.last_signals:
                ts = signal.get("timestamp") or signal.get("generated_at") or signal.get("last_scan")
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

    def _market_phase(self, fg: int, opportunity_count: int) -> str:
        if opportunity_count >= 10:
            return "HOT"
        if opportunity_count >= 5:
            return "WARM"
        if opportunity_count >= 1:
            return "COLD"
        if fg < 35:
            return "FEAR"
        return "DEAD"

    def _regime_summary(self, fg: int, regime_map: Dict[str, str], panel_records: List[Any]) -> str:
        if not panel_records:
            if fg < 35:
                return "Fear-heavy tape with no visible Oracle panel records"
            return "Quiet tape with no visible Oracle panel records"

        regime_counts: Dict[str, int] = {}
        for regime in regime_map.values():
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

        top_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "UNKNOWN"
        return f"{top_regime} dominant | FG {fg} | {len(panel_records)} panel records"

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = TakScannerV4()
    scanner.run_scan()
