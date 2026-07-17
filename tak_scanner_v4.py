from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from aisupertrend import AISupertrend
from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier
from strategies import ENGINE_CLASSES, REGIME_ENGINES

from engineadapter_v4 import EngineSpecialistAdapter
from scannerorchestrator import ScannerOrchestrator
from scannerpair_intake import ScannerPairIntake
from scannerpublisher import ScannerPublisher
from scannerspecialist_registry import SpecialistRegistry
from signalbusbus_writer import SignalBusWriter
from signalbusworker_push import SignalBusWorkerPush
from specialists_s6 import S6FallbackSpecialist


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("takscannerv4")

MODULEDIR = Path(__file__).resolve().parent
CONFIGPATH = MODULEDIR / "config.json"
FGURL = "https://api.alternative.me/fng/?limit=1"
OHLCOLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]


class TakScannerV4:
    def __init__(
        self,
        maxpairs: Optional[int] = None,
        worker_url: str = "https://jhl-signal-bus.blazing0478.workers.dev/update",
        worker_secret: Optional[str] = "jhl2026dragon",
    ) -> None:
        self.maxpairs = maxpairs
        self.universe = PairUniverse()
        self.regime = RegimeClassifier()
        self.aist = AISupertrend()
        self.intake = ScannerPairIntake(regime_classifier=self.regime)
        self.registry = self._build_registry()
        self.orchestrator = ScannerOrchestrator(self.registry)
        self.publisher = ScannerPublisher()
        self.writer = SignalBusWriter("app/signalbus.json")
        self.worker = SignalBusWorkerPush(worker_url=worker_url, secret=worker_secret)

    def fetch_fg(self) -> Dict[str, Any]:
        try:
            resp = requests.get(FGURL, timeout=10)
            resp.raise_for_status()
            payload = resp.json()["data"][0]
            return {"score": int(payload["value"]), "label": payload["value_classification"]}
        except Exception as exc:
            logger.warning("FG fetch failed %s using neutral 50.", exc)
            return {"score": 50, "label": "Neutral"}

    def _build_registry(self) -> SpecialistRegistry:
        registry = SpecialistRegistry()

        for regime, engine_names in REGIME_ENGINES.items():
            for engine_name in engine_names:
                engine_cls = ENGINE_CLASSES.get(engine_name)
                if engine_cls is None:
                    continue
                engine_instance = engine_cls()
                adapter = EngineSpecialistAdapter(
                    name=engine_name,
                    engine=engine_instance,
                    supported_regimes=[str(regime)],
                )
                registry.register(engine_name, adapter)

        if "S6" not in registry.names():
            registry.register("S6", S6FallbackSpecialist())

        return registry

    def _item_to_df(self, item: Dict[str, Any]) -> Any:
        try:
            import pandas as pd

            raw = item.get("ohlc4h")
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=OHLCOLUMNS)
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna().reset_index(drop=True)
        except Exception:
            return None

    def _enrich_active_pairs(self, active_pairs: List[Dict[str, Any]], fgscore: int) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []

        for item in active_pairs:
            pair = item.get("pair")
            df = self._item_to_df(item)

            logger.info(
                "V4 DF pair=%s dfnone=%s rows=%s",
                pair,
                df is None,
                None if df is None else len(df),
            )

            if df is None or len(df) < 60:
                continue

            regime = self.regime.classify(pair, df, fgscore)
            logger.info("V4 REGIME pair=%s regime=%s", pair, regime)

            if str(regime).upper() == "DEAD":
                continue

            aist = self.aist.compute(pair, df)

            item = dict(item)
            item["df"] = df
            item["regime"] = regime
            item["aist"] = aist
            item["aist_direction"] = aist.get("direction")
            item["last_price"] = float(df["close"].iloc[-1])
            enriched.append(item)

        return enriched

    def run_scan(self) -> Dict[str, Any]:
        started = time.time()
        now = datetime.now(timezone.utc)
        fg = self.fetch_fg()
        fgscore = int(fg["score"])

        logger.info("V4 scan start fg=%s label=%s", fgscore, fg.get("label"))

        active_pairs = self.universe.get_active_pairs(interval=240, limit=self.maxpairs)
        enriched_pairs = self._enrich_active_pairs(active_pairs, fgscore)
        contexts = self.intake.build_contexts(enriched_pairs, timeframe="4h", max_pairs=self.maxpairs)

        candidates = self.orchestrator.run(
            contexts,
            shared_state={"fgscore": fgscore, "fg": fg},
        )

        result = self.publisher.publish(
            candidates,
            positions=[],
            audit={
                "lastscan": now.isoformat(),
                "fg": fg,
                "activepairs": len(active_pairs),
                "contextpairs": len(contexts),
                "duration_seconds": round(time.time() - started, 2),
            },
        )

        payload = self.writer.write(result)
        logger.info(
            "V4 bus write ok live=%s caution=%s killed=%s",
            len(result.live_signals),
            len(result.caution_signals),
            len(result.killed_signals),
        )

        push_status: Dict[str, Any]
        try:
            push_status = self.worker.push_payload(payload)
            logger.info("V4 worker push ok status=%s", push_status["status_code"])
        except Exception as exc:
            logger.warning("V4 worker push failed %s", exc)
            push_status = {"status": "failed", "reason": str(exc)}

        summary = {
            "lastscan": now.isoformat(),
            "fg": fg,
            "live_signals": len(result.live_signals),
            "caution_signals": len(result.caution_signals),
            "killed_signals": len(result.killed_signals),
            "contextpairs": len(contexts),
            "duration_seconds": round(time.time() - started, 2),
            "push_status": push_status,
        }

        Path("app/takscannerv4_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary


if __name__ == "__main__":
    scanner = TakScannerV4(maxpairs=None)
    result = scanner.run_scan()
    print(json.dumps(result, ensure_ascii=False, indent=2))
