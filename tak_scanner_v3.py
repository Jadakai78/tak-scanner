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


SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tak_scanner_v3")

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "config.json"
FG_URL = "https://api.alternative.me/fng/?limit=1"
MAX_SAMMY_ALERTS = 5
SIGNAL_TTL_HOURS = 4
OHL_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]
SCAN_HOURS_UTC = [3, 7, 11, 15, 19, 23]
SCAN_MINUTE_UTC = 45

INTENT_RANK = {
    "ATTACKTRAP": 0,
    "ATTACKBREAK": 1,
    "ATTACK": 2,
    "PROBE": 3,
    "WAIT": 4,
    "CUT": 5,
    "IGNORE": 6,
}


def compute_sizing(entry: Any, sl: Any) -> Dict[str, Dict[str, Any]]:
    try:
        risk_per_unit = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        risk_per_unit = 0.0

    sizing: Dict[str, Dict[str, Any]] = {}
    for seat in SEATS:
        if seat["mode"] == "PROTECT_ONLY" or risk_per_unit <= 0:
            sizing[seat["name"]] = {
                "units": 0,
                "dollar_risk": 0,
                "mode": seat["mode"],
            }
            continue

        units = round(seat["risk"] / risk_per_unit, 2)
        sizing[seat["name"]] = {
            "units": units,
            "dollar_risk": round(units * risk_per_unit, 2),
            "mode": seat["mode"],
        }
    return sizing


def send_sammy_message(message: str) -> None:
    token = "8860741830:AAGiccCbk4dzoTq97gWIIykZVunDvkkl6ys"
    chat_id = "7733126931"
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logging.warning("Sammy alert failed: %s", exc)


def load_sprint_mode() -> bool:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return bool(cfg.get("sprintmode", False))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read sprintmode from config.json (%s); defaulting False.", exc)
        return False


def tier_for_pair(pair: str, active_pairs: List[Dict[str, Any]]) -> str:
    ranked = sorted(
        (item for item in active_pairs if "atrpct" in item),
        key=lambda item: item.get("atrpct", 0.0),
        reverse=True,
    )
    top5 = {item["pair"] for item in ranked[:5]}
    return "TIERA" if pair in top5 else "TIERB"


def now_cdt_hour() -> int:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Chicago")).hour
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=5)).hour


def is_quiet_hours() -> bool:
    hour = now_cdt_hour()
    return hour >= 22 or hour < 5


class TakScannerV3:
    def __init__(self, maxpairs: Optional[int] = None) -> None:
        self.maxpairs = maxpairs
        self.universe = PairUniverse()
        self.regime = RegimeClassifier()
        self.aist = AISupertrend()
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        self.bus = SignalBus()
        self.s8 = S8MTFConfluence(fetch_ohlc=self.universe.fetch_ohlc, ai_supertrend=self .aist)

    def fetch_fg(self) -> Dict[str, Any]:
        try:
            resp = self.universe.session.get(FG_URL, timeout=10)
            resp.raise_for_status()
            d = resp.json()["data"][0]
            return {"score": int(d["value"]), "label": d["value_classification"]}
        except Exception as exc:
            logger.warning("FG fetch failed (%s); using neutral 50.", exc)
            return {"score": 50, "label": "Neutral"}

    @staticmethod
    def _next_scan_time(now: datetime) -> datetime:
        logger.info("next_scan_dt=%r type=%s", next_scan_dt, type(next_scan_dt))
        """Compute the next Tier-1 scan timestamp (UTC)."""
        candidates = []
        for h in SCAN_HOURS_UTC:
            t = now.replace(hour=h, minute=SCAN_MINUTE_UTC, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            candidates.append(t)
        return min(candidates)

    @staticmethod
    def df_from_universe_item(item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        raw = item.get("ohlc4h")
        if not raw:
            return None
        try:
            df = pd.DataFrame(raw, columns=OHL_COLUMNS)
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna().reset_index(drop=True)
        except (ValueError, KeyError):
            return None

    def run_engine(
        self,
        engine_id: str,
        pair: str,
        df: pd.DataFrame,
        regime: str,
        fgscore: int,
        aist: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        cls = ENGINE_CLASSES.get(engine_id)
        if cls is None:
            return None
        try:
            return cls().generate(pair, df, regime, fgscore, aist=aist)
        except Exception as exc:
            logger.warning("Engine %s failed on %s: %s", engine_id, pair, exc)
            return None

    @staticmethod
    def resolve_rts(raw: Dict[str, Any], graded: Dict[str, Any], mtf: Dict[str, Any]) -> Dict[str, Any]:
        engine = str(raw.get("engine", ""))
        is_rts = engine.startswith("RTS")

        if is_rts:
            family = raw.get("rtsfamily", engine.replace("RTS", ""))
            offence = float(raw.get("offencescore") or 0.5)
            defence = float(raw.get("defencescore") or 0.5)
            trap = float(raw.get("trapscore") or 0.5)
            intent = raw.get("intent", "WAIT")
        else:
            family = "LIQ" if raw.get("sweepdetected") else ("BOS" if float(raw.get("structurequality") or 0) >= 0.65 else "ZONE")
            conviction = float(graded.get("score", 0.5))
            mtf_map = {"FULL": 1.0, "PARTIAL": 0.75, "NONE": 0.5}
            mtf_v = mtf_map.get(str(raw.get("mtfalignment", "NONE")), 0.5)
            sweep_q = float(raw.get("displacementquality") or raw.get("structurequality") or 0.5)
            offence = min(1.0, conviction * 0.60 + sweep_q * 0.20 + mtf_v * 0.20)
            defence = min(1.0, float(raw.get("structurequality") or 0.5) * 0.50 + mtf_v * 0.30 + 0.20)
            trap = float(raw.get("sweepdepth") or 0.0) * 0.5 + (1 - float(raw.get("structurequality") or 0.5)) * 0.5
            grade = str(graded.get("grade", "F"))
            if grade == "S":
                intent = "ATTACKTRAP" if trap >= 0.60 else "ATTACKBREAK"
            elif grade == "A":
                intent = "ATTACKBREAK" if offence >= 0.68 else "PROBE"
            elif grade in {"B", "C"}:
                intent = "PROBE" if offence >= 0.55 else "WAIT"
            else:
                intent = "IGNORE"

        reasons = raw.get("rtsreasons") or []
        if not reasons:
            reasons = [f"grade {graded.get('grade', '?')} conviction {graded.get('score', 0):.2f}"]

        return {
            "rtsfamily": family,
            "offencescore": round(offence, 3),
            "defencescore": round(defence, 3),
            "trapscore": round(trap, 3),
            "intent": intent,
            "killlevel": raw.get("killlevel", raw.get("sl")),
            "autocut": bool(raw.get("autocut", False)),
            "rtsreasons": reasons,
        }

    @staticmethod
    def derive_action_state(intent: str, tier: str) -> str:
        if intent in {"ATTACKTRAP", "ATTACKBREAK", "ATTACK"}:
            return "CLICK"
        if intent == "PROBE":
            return "CLICK" if tier == "TIERA" else "WAIT"
        if intent == "WAIT":
            return "WAIT"
        return "REJECT"

    @staticmethod
    def should_cut_now(enriched: Dict[str, Any]) -> bool:
        if enriched.get("autocut") and enriched.get("intent") in {"CUT", "IGNORE"}:
            return True
        zonetouches = enriched.get("zonetouches")
        zonetouches = 0 if zonetouches is None else int(zonetouches)
        if enriched.get("rtsfamily") == "ZONE" and zonetouches >= 2:
            return True
        if enriched.get("rtsfamily") == "CHOCH" and enriched.get("flipconfirmed") is False:
            return True
        return False

    @staticmethod
    def finalize_signal(
        raw: Dict[str, Any],
        graded: Dict[str, Any],
        mtf: Dict[str, Any],
        aist: Dict[str, Any],
        verdict: Dict[str, Any],
        now: datetime,
        activepairs: Optional[List[Dict[str, Any]]] = None,
        v2: Optional[Dict[str, Any]] = None,
        rts: Optional[Dict[str, Any]] = None,
        actionstate: str = "WAIT",
    ) -> Dict[str, Any]:
        v2 = v2 or {}
        rts = rts or {}
        signal = {
            "pair": raw["pair"],
            "bias": raw["bias"],
            "engine": raw["engine"],
            "grade": graded["grade"],
            "conviction": graded["score"],
            "actionstate": actionstate,
            "aistdirection": aist.get("direction"),
            "aiststrength": aist.get("signalstrength"),
            "mtfverdict": mtf.get("mtfverdict"),
            "mtfscore": mtf.get("mtfscore"),
            "entry": raw.get("entry"),
            "sl": raw.get("sl"),
            "tp": raw.get("tp"),
            "rr": raw.get("rr"),
            "regime": raw.get("regime"),
            "structurequality": raw.get("structurequality"),
            "remistatus": verdict.get("status"),
            "remicaution": verdict.get("caution"),
            "killreason": verdict.get("reason"),
            "tier": tier_for_pair(raw["pair"], activepairs or []),
            "firedat": now.isoformat(),
            "expiresat": (now + timedelta(hours=SIGNAL_TTL_HOURS)).isoformat(),
            "positionsizing": compute_sizing(raw.get("entry"), raw.get("sl")),
            "defensivescore": v2.get("defensivescore"),
            "offensivescore": rts.get("offencescore", raw.get("offencescore")),
            "traprisk": v2.get("traprisk"),
            "convictionv2": v2.get("convictionv2"),
            "v2action": v2.get("v2action"),
            "v2reasons": v2.get("v2reasons"),
            "rtsfamily": rts.get("rtsfamily", raw.get("rtsfamily")),
            "intent": rts.get("intent", raw.get("intent")),
            "killlevel": rts.get("killlevel", raw.get("killlevel")),
            "autocut": rts.get("autocut", raw.get("autocut", False)),
            "rtsreasons": rts.get("rtsreasons", []),
            "offencescore": rts.get("offencescore", raw.get("offencescore")),
            "defencescore": rts.get("defencescore", raw.get("defencescore")),
            "trapscore": rts.get("trapscore", raw.get("trapscore")),
            "liquiditypooltype": raw.get("liquiditypooltype"),
            "sweepside": raw.get("sweepside"),
            "sweeplevel": raw.get("sweeplevel"),
            "sweeptype": raw.get("sweeptype"),
            "reclaimstatus": raw.get("reclaimstatus"),
            "continuationstatus": raw.get("continuationstatus"),
            "chochdirection": raw.get("chochdirection"),
            "chochlevel": raw.get("chochlevel"),
            "flipconfirmed": raw.get("flipconfirmed"),
            "boslevel": raw.get("boslevel"),
            "bosdirection": raw.get("bosdirection"),
            "bosretestvalid": raw.get("bosretestvalid"),
            "zonetop": raw.get("zonetop"),
            "zonebottom": raw.get("zonebottom"),
            "zonetouches": raw.get("zonetouches"),
            "zonemitigated": raw.get("zonemitigated"),
            "deltabias": raw.get("deltabias"),
            "sponsorshipquality": raw.get("sponsorshipquality"),
            "vpcontext": raw.get("vpcontext"),
            "vpoc": raw.get("vpoc"),
        }
        signal["gimba_message"] = format_gimba_message(signal)
        return signal

    @staticmethod
    def fire_alerts(sammy: List[Dict[str, Any]], quiet: bool, sprintmode: bool = False) -> None:
        for signal in sammy:
            tier = signal.get("tier", "TIERB")
            mode_tag = " SPRINT MODE" if sprintmode else ""
            sizing = signal.get("positionsizing", {})
            size_lines = []
            for seat, data in sizing.items():
                if data.get("mode") == "PROTECT_ONLY":
                    size_lines.append(f"{seat}: PROTECT ONLY")
                else:
                    size_lines.append(f"{seat}: {data.get('units')} units / ${data.get('dollar_risk')} risk")
            sizing_block = "\n".join(size_lines) if size_lines else "Sizing: N/A"
            gimba = signal.get("gimba_message") or format_gimba_message(signal)
            message = (
                f"{gimba}\n\n"
                f"{signal.get('grade', '?')}-GRADE SIGNAL{mode_tag}\n"
                f"Tier: {tier}\n"
                f"{sizing_block}"
            )
            if quiet:
                logger.info(
                    "SAMMY quiet-hours %s %s %s conv=%.2f",
                    signal.get("pair"),
                    signal.get("bias"),
                    signal.get("engine"),
                    signal.get("conviction", 0.0),
                )
            else:
                logger.info(
                    "SAMMY ALERT %s %s %s conv=%.2f grade=%s tier=%s",
                    signal.get("pair"),
                    signal.get("bias"),
                    signal.get("engine"),
                    signal.get("conviction", 0.0),
                    signal.get("grade"),
                    tier,
                )
            send_sammy_message(message)

    def run_scan(self) -> Dict[str, Any]:
        start = time.time()
        now = datetime.now(timezone.utc)
        quiet = is_quiet_hours()
        sprintmode = load_sprint_mode()
        fg = self.fetch_fg()
        fgscore = fg["score"]

        logger.info(
            "Scan start | F&G=%s (%s) | quiet_hours=%s | sprint_mode=%s",
            fgscore,
            fg["label"],
            quiet,
            sprintmode,
        )

        active = self.universe.get_active_pairs(interval=240, limit=self.maxpairs)
        logger.info("Active universe: %d pairs", len(active))

        signals: List[Dict[str, Any]] = []
        killed: List[Dict[str, Any]] = []
        regime_map: Dict[str, str] = {}
        dead_count = 0

        for item in active:
            pair = item["pair"]
            df = self.df_from_universe_item(item)
            if df is None or len(df) < 60:
                continue

            regime = self.regime.classify(pair, df, fgscore)
            regime_map[pair] = regime
            if regime == "DEAD":
                dead_count += 1
                continue

            aist = self.aist.compute(pair, df)
            daily_df: Optional[pd.DataFrame] = None

            for engine_id in REGIME_ENGINES.get(regime, []):
                raw = self.run_engine(engine_id, pair, df, regime, fgscore, aist)
                if raw is None or not raw.get("bias"):
                    continue

                raw["regime"] = regime
                raw["aistdirection"] = aist.get("direction")
                raw["aiststrength"] = aist.get("signalstrength")

                mtf = self.s8.score_mtf(pair, raw["bias"], df, pairkey=item.get("pairkey"))
                raw["mtfalignment"] = mtf.get("mtfverdict")
                raw["atrpct"] = item.get("atrpct", 0.0)
                raw["volumeratio"] = item.get("volumeratio", 1.0)
                microenrich(raw, df, active)

                if str(raw.get("engine", "")).startswith("RTS"):
                    delta_ctx = score_delta_context(df, raw.get("bias", "LONG"))
                    raw.update(delta_ctx)
                    if "offencescore" in raw:
                        raw["offencescore"] = min(1.0, max(0.0, float(raw.get("offencescore", 0.0)) + float(delta_ctx.get("delta_modifier", 0.0))))

                graded = self.scorer.score(raw)
                v2 = score_v2_shadow(raw)
                if graded.get("grade") == "F":
                    continue

                if daily_df is None:
                    daily_df = self.universe.fetch_ohlc(item["pairkey"], interval=1440)
                verdict = self.remi.evaluate(raw, conviction=graded["score"], dailydf=daily_df, fgscore=fgscore)
                rts = self.resolve_rts(raw, graded, mtf)
                tier = tier_for_pair(pair, active)
                action_state = self.derive_action_state(rts["intent"], tier)
                enriched = self.finalize_signal(raw, graded, mtf, aist, verdict, now, active, v2, rts, action_state)

                if verdict.get("status") == "KILLED" or self.should_cut_now(enriched):
                    killed.append(
                        {
                            "pair": pair,
                            "engine": engine_id,
                            "bias": raw["bias"],
                            "killreason": verdict.get("reason") or rts["intent"],
                            "killedat": now.isoformat(),
                            "rtsfamily": rts["rtsfamily"],
                            "intent": rts["intent"],
                            "gimba_message": enriched.get("gimba_message"),
                        }
                    )
                elif pair in PROP_WHITELIST:
                    signals.append(enriched)

        signals.sort(
            key=lambda sig: (
                INTENT_RANK.get(sig.get("intent", "WAIT"), 4),
                -float(sig.get("defencescore") or 0.0),
                -float(sig.get("trapscore") or 0.0),
                -float(sig.get("offencescore") or 0.0),
                -float(sig.get("conviction") or 0.0),
            )
        )

        sammy = [s for s in signals if s.get("grade") in {"S", "A"}][:MAX_SAMMY_ALERTS]
        stats = {
            "signals_fired": len(signals),
            "signals_killed": len(killed),
            "s_grade_count": len(sammy),
            "scan_duration_sec": round(time.time() - start, 2),
        }

        self.bus.update({
            "last_scan": now.isoformat(),
            "next_scan": self._next_scan_time(now).isoformat(),
            "f_g": fg,
            "active_pairs": len(active),
            "dead_pairs": dead_count,
            "signals": signals,
            "killed_signals": killed,
            "regime_map": regime_map,
            "session_stats": stats,
            "quiet_hours": quiet,
            "sprint_mode": sprint_mode,
        })

        try:
            worker_url = "https://jhl-signal-bus.blazing0478.workers.dev/update"
            bus_path = Path("/app/signal_bus.json")
            payload = bus_path.read_text(encoding="utf-8")

            resp = requests.post(
                worker_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-JHL-Secret": "jhl2026dragon",
                },
                timeout=20,
            )
            resp.raise_for_status()
            logger.info("Worker push OK: %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Worker push failed: %s", exc)

        self._fire_alerts(sammy, quiet, sprint_mode)
        logger.info(
            "Scan complete: %d fired, %d killed, %d S-grade in %.1fs",
            stats["signals_fired"],
            stats["signals_killed"],
            stats["s_grade_count"],
            stats["scan_duration_sec"],
        )
        return stats


if __name__ == "__main__":
    scanner = TakScannerV3(maxpairs=None)
    results = scanner.run_scan()
    print(
        f"Scan complete: {results['signals_fired']} signals, "
        f"{results['signals_killed']} killed, "
        f"{results['s_grade_count']} S-grade "
        f"({results['scan_duration_sec']}s)"
    )
