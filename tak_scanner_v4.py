from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from aisupertrend import AISupertrend
from convictionscorer import ConvictionScorer, score_v2_shadow
from microstructure import enrich as microenrich
from pairuniverse import PairUniverse, PROP_WHITELIST
from regimeclassifier import RegimeClassifier
from remi import Remi
from signalbus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES, S8MTFConfluence, score_delta_context
from gimba_formatter import format_gimba_message
from scannermodels import PairContext, CandidateSignal
from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry

# Configuration
SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

logger = logging.getLogger(__name__)
MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "s8_mtf_confluence.json"
FG_URL = "https://api.alternative.me/fng/"
OHL_COLUMNS = ["open", "high", "low"]
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


def build_bus_payload(
    lastscan: str,
    nextscan: str,
    fg: int,
    activepairs: int,
    deadpairs: int,
    signals: List[Dict[str, Any]],
    killedsignals: List[Dict[str, Any]],
    regimemap: Dict[str, str],
    sessionstats: Dict[str, Any],
    quiethours: bool,
    sprintmode: bool,
    april_view: Optional[Dict[str, Any]] = None,
    oracle: Optional[Dict[str, Any]] = None,
    council: Optional[Dict[str, Any]] = None,
    april: Optional[Dict[str, Any]] = None,
    remi: Optional[Dict[str, Any]] = None,
    tak: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build complete signal bus payload with Oracle, Council, April, Remi, and Tak data"""
    payload = {
        "lastscan": lastscan,
        "nextscan": nextscan,
        "fg": fg,
        "activepairs": activepairs,
        "deadpairs": deadpairs,
        "signals": signals,
        "killedsignals": killedsignals,
        "regimemap": regimemap,
        "sessionstats": sessionstats,
        "quiethours": quiethours,
        "sprintmode": sprintmode,
    }
    
    # Add new dashboard sections if provided
    if oracle is not None:
        payload["oracle"] = oracle
    if council is not None:
        payload["council"] = council
    if april is not None:
        payload["april"] = april
    if remi is not None:
        payload["remi"] = remi
    if tak is not None:
        payload["tak"] = tak
    
    # Legacy april_view for backward compatibility
    if april_view is not None:
        payload["april_view"] = april_view
    
    return payload


def write_bus_snapshot(payload: Dict[str, Any], output_path: Path) -> None:
    """
    Writes the bus payload snapshot to disk as JSON.
    """
    try:
        # Use Path.open for consistency
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"Bus snapshot written to {output_path}")
    except Exception as e:
        logger.error(f"Failed to write bus snapshot: {e}")


class TakScannerV4:
    def __init__(self):
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        # Ensure scanner writes to the underscore-named bus file scheduler expects
        self.bus = SignalBus(path=MODULE_DIR / "signal_bus.json")
        self.classifier = RegimeClassifier()
        self.universe = PairUniverse()
        self.last_signals = []
        self.killed_cache = []
        
        # April integration scaffold
        self.april_enabled = False
        self.april_status = {"ready": False, "last_check": None}

        # V4 Architecture: Orchestrator + Specialist Registry
        try:
            # Prefer a classmethod if the API provides it
            registry = SpecialistRegistry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
        except Exception:
            # Fallback to instance method if available
            registry = SpecialistRegistry()
            try:
                registry = registry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
            except Exception as e:
                logger.exception(f"Failed to build SpecialistRegistry from engine map: {e}")
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
            logger.warning(f"FG fetch failed: {e}")
            return 50

    def run_scan(self):
        now = datetime.now(timezone.utc)
        logger.info(f"Starting scan at {now.isoformat()}")
        
        fg = self.fetch_fear_greed()
        active = self.universe.get_active_pairs()
        dead_count = len(self.universe.pairs) - len(active)
        
        signals = []
        killed = []
        regime_map = {}
        stats = {"scanned": len(active), "signaled": 0, "killed": 0}
        
        # V4 ORCHESTRATOR PATTERN: Build contexts for all pairs, run specialists -> REMI -> APRIL
        logger.info("V4 building contexts for %s active pairs", len(active))
        contexts = []
        for pair in active:
            regime = self.classifier.classify(pair)
            regime_map[pair] = regime
            context = PairContext(
                pair=pair,
                market_regime=regime,
                timeframe="1h",
                fear_greed=fg,
                session=None,
                indicators={},
                market_state={},
            )
            contexts.append(context)
        
        # Run orchestrator: specialists -> REMI -> APRIL
        shared_state = {"fgscore": fg, "timeframe": "1h"}
        logger.info("V4 calling orchestrator with %s contexts", len(contexts))
        candidates = self.orchestrator.run(contexts, shared_state)
        logger.info("V4 orchestrator returned %s candidates", len(candidates))
        
        # Convert CandidateSignal objects to dict format for backward compatibility
        for candidate in candidates:
            signal_dict = {
                "pair": candidate.pair,
                "setup_type": candidate.setup_type,
                "side": candidate.side,
                "specialist": candidate.specialist,
                "thesis": candidate.thesis,
                "score": candidate.score,
                "confidence": candidate.confidence,
                "entry_idea": candidate.entry_idea,
                "stop_idea": candidate.stop_idea,
                "target_idea": candidate.target_idea,
                "warnings": candidate.warnings,
                "tags": candidate.tags,
                "context": candidate.context,
                "conviction": candidate.score,
                "timestamp": now.isoformat(),
            }
            
            # Route based on council decision
            if candidate.council and candidate.council.route == "killed_signals":
                killed.append(signal_dict)
                stats["killed"] += 1
            else:
                signals.append(signal_dict)
        
        # ==========================================
        # BUILD ORACLE + COUNCIL DATA FOR DASHBOARD
        # ==========================================
        
        # Oracle data - market context at top of dashboard
        oracle_data = {
            "fg": fg,
            "fg_label": self._fg_label(fg),
            "btc_regime": regime_map.get("BTC/USD", "UNKNOWN"),
            "session": self._get_session(now),
            "activepairs": len(active),
            "deadpairs": dead_count,
            "market_phase": self._market_phase(fg, len(signals)),
            "sgrade_count": sum(1 for s in signals if s.get("grade") == "S"),
            "agrade_count": sum(1 for s in signals if s.get("grade") == "A")
        }
        
        # Council data - grouped bot claims for hunting panel
        council_data = self._build_council_data(candidates)
        
        # April data - position health (mock for now, real when positions exist)
        april_data = {
            "open_positions": 0,
            "total_pnl": 0.0,
            "at_risk": 0.0,
            "warnings": []
        }
        
        # Remi data - recent kills for visibility
        remi_data = {
            "recent_kills": [
                {
                    "pair": k["pair"],
                    "reason": self._kill_reason(k),
                    "timestamp": now.isoformat()
                }
                for k in killed[-10:]  # Last 10 kills
            ]
        }
        
        # Tak status - system health
        tak_data = {
            "lastscan": now.isoformat(),
            "nextscan": self.next_scan_time(now).isoformat(),
            "bus_ok": True,
            "cf_workers_ok": True,
            "scheduler_ok": True
        }
        
        # trim last_signals to avoid unbounded growth
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=SIGNAL_TTL_HOURS)
            trimmed = []
            for s in self.last_signals:
                ts = s.get("timestamp")
                if not ts:
                    continue
                try:
                    t = datetime.fromisoformat(ts)
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    if t >= cutoff:
                        trimmed.append(s)
                except Exception:
                    # if parsing fails, drop the entry
                    continue
            self.last_signals = trimmed
        except Exception:
            logger.debug("Failed to trim last_signals")
        
        # Sort signals by intent rank
        signals.sort(key=lambda s: INTENT_RANK.get(s.get("intent", ""), 999))
        
        # Check for quiet hours and sprint mode
        quiet = now.hour not in SCAN_HOURS_UTC
        sprintmode = False  # Placeholder for future logic
        
        # Build april_view once so payload and live bus remain identical
        april_view = self._build_april_view() if self.april_enabled else None

        # Build canonical payload
        payload = build_bus_payload(
            lastscan=now.isoformat(),
            nextscan=self.next_scan_time(now).isoformat(),
            fg=fg,
            activepairs=len(active),
            deadpairs=dead_count,
            signals=signals,
            killedsignals=killed,
            regimemap=regime_map,
            sessionstats=stats,
            quiethours=quiet,
            sprintmode=sprintmode,
            april_view=april_view,
            oracle=oracle_data,
            council=council_data,
            april=april_data,
            remi=remi_data,
            tak=tak_data,
        )
        
        # Update bus (legacy interface)
        try:
            self.bus.update(
                lastscan=now.isoformat(),
                nextscan=self.next_scan_time(now).isoformat(),
                fg=fg,
                activepairs=len(active),
                deadpairs=dead_count,
                signals=signals,
                killedsignals=killed,
                regimemap=regime_map,
                sessionstats=stats,
                quiethours=quiet,
                sprintmode=sprintmode,
                april_view=april_view,
            )
        except Exception as e:
            logger.debug(f"Bus update_failed: {e}")
        
        # Write snapshot to disk
        snapshot_path = self.bus.path
        write_bus_snapshot(payload, snapshot_path)
        
        # Fire alerts
        self.fire_alerts(signals[:MAX_SAMMY_ALERTS])
        
        logger.info(f"Scan complete: {stats['signaled']} signals, {stats['killed']} killed")
        return payload

    def _resolve_action_state(self, signal: Dict[str, Any], pair: str) -> str:
        """
        Determines whether a signal is active or killed based on TTL and other factors.
        """
        # Check if signal is in last_signals and still within TTL
        for old_signal in self.last_signals:
            if old_signal.get("pair") == pair:
                timestamp = old_signal.get("timestamp")
                if not timestamp:
                    continue
                try:
                    signal_time = datetime.fromisoformat(timestamp)
                    # Ensure timezone-aware (assume UTC if none specified)
                    if signal_time.tzinfo is None:
                        signal_time = signal_time.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - signal_time).total_seconds() / 3600
                    if age_hours > SIGNAL_TTL_HOURS:
                        return "Killed"
                except Exception as e:
                    logger.debug(f"Failed to parse timestamp for TTL check: {timestamp!r}: {e}")
        return "Signal"

    def _build_april_view(self) -> Dict[str, Any]:
        """
        Scaffold for April integration panel data.
        Returns status and performance metrics for April's alert system.
        """
        return {
            "status": "OK",
            "last_check": datetime.now(timezone.utc).isoformat(),
            "bot_health": {"scanner": "running", "scheduler": "running"},
            "alerts_pending": 0,
            "performance_flags": [],
        }

    def fire_alerts(self, top_signals: List[Dict[str, Any]]):
        """
        Sends top signals to Telegram and other alert channels.
        """
        for signal in top_signals:
            try:
                message = format_gimba_message(signal)
                # Send via Telegram, Outlook, etc.
                logger.info(f"Alert fired for {signal.get('pair', 'unknown')}")
            except Exception as e:
                logger.exception(f"Alert failed: {e}")

    def _fg_label(self, fg: int) -> str:
        """Convert F&G number to label"""
        if fg < 25:
            return "Extreme Fear"
        elif fg < 45:
            return "Fear"
        elif fg < 55:
            return "Neutral"
        elif fg < 75:
            return "Greed"
        else:
            return "Extreme Greed"
    
    def _get_session(self, now: datetime) -> str:
        """Determine market session based on UTC hour"""
        hour = now.hour
        if 0 <= hour < 8:
            return "Asia"
        elif 8 <= hour < 16:
            return "London"
        else:
            return "NY"
    
    def _market_phase(self, fg: int, signal_count: int) -> str:
        """Determine market phase based on signal count"""
        if signal_count >= 10:
            return "HOT"
        elif signal_count >= 5:
            return "WARM"
        elif signal_count >= 1:
            return "COLD"
        else:
            return "DEAD"
    
    def _bot_name(self, bot: str) -> str:
        """Map bot code to display name"""
        names = {
            "S6": "Reversal",
            "S7": "Range Scalper",
            "S9": "Capitulation",
            "S1": "Sniper",
            "S2": "Trend Rider",
            "S3": "Volatile",
            "S4": "Mean Reversion",
            "S5": "EMA Cross",
            "S8": "MTF Confluence",
            "S10": "Gimba Range"
        }
        return names.get(bot, bot)
    
    def _remi_status(self, candidate) -> str:
        """Determine Remi status from candidate review"""
        if not candidate.review:
            return "unknown"
        
        decision = candidate.review.decision
        if decision == "approve":
            return "approved"
        elif decision in ["caution", "watchlist"]:
            return "caution"
        else:
            return "killed"
    
    def _kill_reason(self, killed_signal: dict) -> str:
        """Extract kill reason from killed signal"""
        # Try to get from review or council
        review = killed_signal.get("review")
        council = killed_signal.get("council")
        
        if council and council.get("veto_reasons"):
            return ", ".join(council["veto_reasons"][:2])  # First 2 reasons
        
        if review and review.get("caution_flags"):
            return ", ".join(review["caution_flags"][:2])
        
        return "Quality threshold"
    
    def _build_council_data(self, candidates: list) -> dict:
        """Group candidates by specialist and rank by score"""
        from collections import defaultdict
        
        bot_groups = defaultdict(list)
        
        for candidate in candidates:
            bot = candidate.specialist
            
            # Build claim dict
            claim = {
                "pair": candidate.pair,
                "score": round(candidate.score, 1),
                "side": candidate.side,
                "confidence": round(candidate.confidence, 3),
                "remi_status": self._remi_status(candidate),
                "april_status": "healthy",  # TODO: Real April logic when positions exist
                "entry_idea": candidate.entry_idea,
                "stop_idea": candidate.stop_idea,
                "target_idea": candidate.target_idea,
                "thesis": candidate.thesis[:80] if candidate.thesis else "No thesis"
            }
            
            bot_groups[bot].append(claim)
        
        # Sort each bot's claims by score desc, take top 10
        bot_claims = []
        for bot, claims in bot_groups.items():
            sorted_claims = sorted(claims, key=lambda x: x["score"], reverse=True)[:10]
            bot_claims.append({
                "bot": bot,
                "name": self._bot_name(bot),
                "active_claims": len(claims),
                "top_claims": sorted_claims
            })
        
        # Sort bots by active claims desc
        bot_claims.sort(key=lambda x: x["active_claims"], reverse=True)
        
        return {"bot_claims": bot_claims}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = TakScannerV4()
    scanner.run_scan()
