from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests

from aisupertrend import AISupertrend
from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier
from strategies import ENGINE_CLASSES

from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import ScannerSpecialistRegistry
from scannerreviewer_remi import RemiReviewer
from scannercouncil import ScannerCouncil
from scannerpublisher import ScannerPublisher
from signalbusbus_writer import SignalBusWriter
from scannermodels import PairContext, SpecialistObservation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("tak_scanner_v4")

FG_URL = "https://api.alternative.me/fng/?limit=1"
OHL_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]


class V4PairIntake:
    def __init__(self) -> None:
        self.universe = PairUniverse()
        self.regime = RegimeClassifier()
        self.aist = AISupertrend()

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
    def df_from_universe_item(item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        raw = item.get("ohlc_4h")
        if not raw:
            return None
        try:
            df = pd.DataFrame(raw, columns=OHL_COLUMNS)
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna().reset_index(drop=True)
        except (ValueError, KeyError):
            return None

    def build_contexts(self, pairs: Iterable[str]) -> List[PairContext]:
        fg = self.fetch_fg()
        fg_score = int(fg["score"])
        logger.info(
            "V4 FG | score=%s label=%s",
            fg_score,
            fg["label"],
        )

        active = self.universe.get_active_pairs(interval=240, limit=None)
        logger.info("V4 universe | active_count=%s", len(active))

        # For now, ignore requested list and scan full active universe (like v3).
        contexts: List[PairContext] = []

        for item in active:
            pair = str(item["pair"]).upper()

            df = self.df_from_universe_item(item)
            logger.info(
                "V4 DF | pair=%s df_none=%s rows=%s",
                pair,
                df is None,
                None if df is None else len(df),
            )
            if df is None or len(df) < 60:
                continue

            regime = self.regime.classify(pair, df, fg_score)
            logger.info("V4 REGIME | pair=%s regime=%s", pair, regime)
            if regime == "DEAD":
                continue

            aist = self.aist.compute(pair, df)

            contexts.append(
                PairContext(
                    pair=pair,
                    timeframe="4h",
                    last_price=float(df["close"].iloc[-1]),
                    market_regime=regime,
                    metadata={
                        "ohlc_df": df,
                        "regime": regime,
                        "fg_score": fg_score,
                        "aist": aist,
                        "pairkey": item.get("pairkey"),
                        "atrpct": item.get("atrpct", 0.0),
                        "volumeratio": item.get("volumeratio", 1.0),
                    },
                )
            )

        logger.info(
            "V4 intake complete | contexts=%s | context_pairs=%s",
            len(contexts),
            [c.pair for c in contexts],
        )
        return contexts


class LegacyEngineAdapter:
    def __init__(self, engine_id: str) -> None:
        self.engine_id = engine_id
        self.engine_cls = ENGINE_CLASSES.get(engine_id)
        if self.engine_cls is None:
            logger.warning("V4 adapter | engine %s not found in ENGINE_CLASSES", engine_id)

    def generate(self, context: PairContext) -> List[SpecialistObservation]:
        if self.engine_cls is None:
            logger.info(
                "V4 adapter | pair=%s engine=%s skipped (missing class)",
                context.pair,
                self.engine_id,
            )
            return []

        df = context.metadata.get("ohlc_df")
        regime = str(context.metadata.get("regime", context.market_regime))
        fg_score = int(context.metadata.get("fg_score", 50))
        aist = context.metadata.get("aist", {})

        logger.info(
            "V4 adapter | calling engine=%s pair=%s regime=%s fg=%s",
            self.engine_id,
            context.pair,
            regime,
            fg_score,
        )

        try:
            raw = self.engine_cls().generate(
                context.pair,
                df,
                regime,
                fg_score,
                aist=aist,
            )
        except Exception as exc:
            logger.warning(
                "V4 engine %s failed on %s: %s",
                self.engine_id,
                context.pair,
                exc,
            )
            return []

        logger.info(
            "V4 TAK | pair=%s engine=%s raw_none=%s",
            context.pair,
            self.engine_id,
            raw is None,
        )

        if not raw:
            return []

        bias = str(raw.get("bias", "")).upper()
        if bias not in {"LONG", "SHORT"}:
            logger.info(
                "V4 TAK | pair=%s engine=%s dropped (no bias)",
                context.pair,
                self.engine_id,
            )
            return []

        rr = float(raw.get("rr", 0.0) or 0.0)
        structure_quality = float(raw.get("structurequality", 0.5) or 0.5)
        vol_ratio = float(
            raw.get("volumeratio", context.metadata.get("volumeratio", 1.0)) or 1.0
        )

        confidence = max(
            0.0,
            min(
                1.0,
                (rr * 0.22)
                + (structure_quality * 0.45)
                + min(vol_ratio, 2.0) * 0.10,
            ),
        )
        score = max(
            0.0,
            min(
                100.0,
                rr * 25.0
                + structure_quality * 35.0
                + min(vol_ratio, 2.0) * 10.0
                + confidence * 15.0,
            ),
        )

        logger.info(
            "V4 OBS | pair=%s engine=%s bias=%s rr=%.2f struct=%.2f vol=%.2f conf=%.3f score=%.2f",
            context.pair,
            self.engine_id,
            bias,
            rr,
            structure_quality,
            vol_ratio,
            confidence,
            score,
        )

        thesis = (
            f"{self.engine_id} produced a {bias} setup on {context.pair} "
            f"in regime {regime} with rr={rr:.2f}."
        )

        obs = SpecialistObservation(
            specialist=self.engine_id,
            pair=context.pair,
            setup_type=str(raw.get("engine", self.engine_id)).lower(),
            side=bias,
            confidence=round(confidence, 4),
            score=round(score, 2),
            thesis=thesis,
            evidence={
                "entry_idea": raw.get("entry"),
                "stop_idea": raw.get("sl"),
                "target_idea": raw.get("tp"),
                "rr": raw.get("rr"),
                "structurequality": raw.get("structurequality"),
                "volumeratio": raw.get("volumeratio"),
                "fg_score": fg_score,
                "aistdirection": raw.get("aistdirection", aist.get("direction")),
                "aiststrength": raw.get("aiststrength", aist.get("signalstrength")),
                "kill_condition": raw.get("kill_condition"),
                "raw_signal": raw,
            },
            warnings=[],
            tags=[
                self.engine_id.lower(),
                regime.lower(),
                str(raw.get("engine", self.engine_id)).lower(),
            ],
            context={
                "regime": regime,
                "fg_score": fg_score,
                "pairkey": context.metadata.get("pairkey"),
                "atrpct": context.metadata.get("atrpct"),
            },
        )
        return [obs]


class V4Scanner:
    def __init__(self) -> None:
        self.intake = V4PairIntake()

    def build_registry(self) -> SpecialistRegistry:
        registry = ScannerSpecialistRegistry()
        registry.register("S6", LegacyEngineAdapter("S6").generate)
        logger.info("V4 registry | specialists=%s", registry.list_specialists())
        return registry

    def run_scan(self, pairs: Iterable[str]) -> Dict[str, Any]:
        start = time.time()

        registry = self.build_registry()
        reviewer = RemiReviewer()
        council = ScannerCouncil()
        publisher = ScannerPublisher()
        orchestrator = ScannerOrchestrator(
            registry=registry,
            reviewer=reviewer,
            council=council,
            publisher=publisher,
        )

        contexts = self.intake.build_contexts(pairs)
        logger.info("V4 orchestrator | context_count=%s", len(contexts))

        all_candidates = []

        for context in contexts:
            logger.info("V4 orchestrator | run pair=%s regime=%s", context.pair, context.market_regime)
            candidates_for_pair = orchestrator._build_candidates_for_pair(context)
            logger.info(
                "V4 orchestrator | pair=%s candidates=%s",
                context.pair,
                len(candidates_for_pair),
            )
            all_candidates.extend(candidates_for_pair)

        logger.info("V4 orchestrator | total_candidates=%s", len(all_candidates))

        scan_result = publisher.publish(all_candidates)
        scan_result.audit["pairs_scanned"] = [ctx.pair for ctx in contexts]
        scan_result.audit["specialists"] = registry.list_specialists()
        scan_result.audit["scan_duration_sec"] = round(time.time() - start, 2)

        writer = SignalBusWriter("/app/signal_bus.json")
        payload = writer.write(scan_result)

        try:
            worker_url = "https://jhl-signal-bus.blazing0478.workers.dev/update"
            resp = requests.post(
                worker_url,
                data=json.dumps(payload, ensure_ascii=False, indent=2),
                headers={
                    "Content-Type": "application/json",
                    "X-JHL-Secret": "jhl2026dragon",
                },
                timeout=20,
            )
            resp.raise_for_status()
            logger.info("V4 Worker push OK: %s", resp.status_code)
        except Exception as exc:
            logger.warning("V4 Worker push failed: %s", exc)

        logger.info(
            "V4 scan complete: live=%d caution=%d killed=%d in %.1fs",
            len(scan_result.live_signals),
            len(scan_result.caution_signals),
            len(scan_result.killed_signals),
            scan_result.audit["scan_duration_sec"],
        )

        return {
            "live": len(scan_result.live_signals),
            "caution": len(scan_result.caution_signals),
            "killed": len(scan_result.killed_signals),
            "scan_duration_sec": scan_result.audit["scan_duration_sec"],
        }


def run_v4_scan(pairs: Iterable[str]) -> Dict[str, Any]:
    scanner = V4Scanner()
    return scanner.run_scan(pairs)


if __name__ == "__main__":
    import sys

    cli_pairs: List[str] = [p.upper() for p in (sys.argv[1:] or ["ADAUSD"])]
    results = run_v4_scan(cli_pairs)
    print(
        f"V4 complete: {results['live']} live, "
        f"{results['caution']} caution, "
        f"{results['killed']} killed "
        f"({results['scan_duration_sec']}s)"
    )
