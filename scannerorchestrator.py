from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from scannercandidate_factory import build_candidate
from scannercouncil import ScannerCouncil
from scannerpair_intake import ScannerPairIntake
from scannerpublisher import ScannerPublisher
from scannerreviewer_remi import RemiReviewer
from scannerspecialist_registry import ScannerSpecialistRegistry
from scannermodels import CandidateSignal, ScanResult
from signalbusschema import build_signal_bus_payload
from signalbusbus_writer import SignalBusBusWriter
from signalbusworker_push import SignalBusWorkerPush

logger = logging.getLogger("scannerorchestrator")

FG_URL = "https://api.alternative.me/fng/?limit=1"
SCAN_HOURS_UTC = [3, 7, 11, 15, 19, 23]
SCAN_MINUTE_UTC = 45


class ScannerOrchestrator:
    def __init__(
        self,
        max_pairs: Optional[int] = None,
        intake: Optional[ScannerPairIntake] = None,
        specialists: Optional[ScannerSpecialistRegistry] = None,
        remi: Optional[RemiReviewer] = None,
        council: Optional[ScannerCouncil] = None,
        publisher: Optional[ScannerPublisher] = None,
        bus_writer: Optional[SignalBusBusWriter] = None,
        worker_push: Optional[SignalBusWorkerPush] = None,
    ) -> None:
        self.max_pairs = max_pairs
        self.intake = intake or ScannerPairIntake()
        self.specialists = specialists or ScannerSpecialistRegistry()
        self.remi = remi or RemiReviewer()
        self.council = council or ScannerCouncil()
        self.publisher = publisher or ScannerPublisher()
        self.bus_writer = bus_writer or SignalBusBusWriter()
        self.worker_push = worker_push or SignalBusWorkerPush()

    def fetch_fg(self) -> Dict[str, Any]:
        try:
            resp = requests.get(FG_URL, timeout=10)
            resp.raise_for_status()
            d = resp.json()["data"][0]
            return {"score": int(d["value"]), "label": d["value_classification"]}
        except Exception as exc:
            logger.warning("V4 FG FAIL err=%s using neutral", exc)
            return {"score": 50, "label": "Neutral"}

    @staticmethod
    def next_scan_time(now: datetime) -> datetime:
        candidates = []
        for hour in SCAN_HOURS_UTC:
            dt = now.replace(hour=hour, minute=SCAN_MINUTE_UTC, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            candidates.append(dt)
        return min(candidates)

    def process_candidate(self, candidate: CandidateSignal) -> CandidateSignal:
        review = self.remi.review(candidate)
        candidate.review = review
        candidate.confidence = max(0.0, min(1.0, candidate.confidence + review.confidence_delta))
        candidate.council = self.council.adjudicate(candidate)
        candidate.final_status = candidate.council.route
        logger.info(
            "V4 COUNCIL pair=%s candidate=%s remi=%s reviewed_score=%s council=%s route=%s",
            candidate.pair,
            candidate.candidate_id,
            review.decision,
            review.adjusted_score,
            candidate.council.decision,
            candidate.council.route,
        )
        return candidate

    def run_scan(self) -> Dict[str, Any]:
        start = time.time()
        now = datetime.now(timezone.utc)
        fg = self.fetch_fg()

        logger.info("V4 SCAN START fg_score=%s fg_label=%s", fg["score"], fg["label"])

        active = self.intake.get_active_pairs(interval=240, limit=self.max_pairs)

        result = ScanResult()
        processed_candidates: List[CandidateSignal] = []
        regime_map: Dict[str, str] = {}

        prepared_pairs = 0
        observations_total = 0

        for item in active:
            prepared = self.intake.prepare_pair(item, fg["score"])
            if not prepared:
                continue

            prepared_pairs += 1
            pair = prepared["pair"]
            df = prepared["df"]
            context = prepared["context"]
            aist = prepared["aist"]

            regime_map[pair] = context.market_regime

            observations = self.specialists.run_specialists(
                pair=pair,
                df=df,
                context=context,
                source_item=item,
                aist=aist,
            )
            observations_total += len(observations)

            for obs in observations:
                candidate = build_candidate(obs)
                processed_candidates.append(self.process_candidate(candidate))

        published = self.publisher.publish_many(processed_candidates)

        for signal in published:
            if signal.bucket == "live_signals":
                result.live_signals.append(signal)
            elif signal.bucket == "caution_signals":
                result.caution_signals.append(signal)
            else:
                result.killed_signals.append(signal)

        result.audit = {
            "last_scan": now.isoformat(),
            "next_scan": self.next_scan_time(now).isoformat(),
            "fg": fg,
            "active_pairs": len(active),
            "prepared_pairs": prepared_pairs,
            "observations_total": observations_total,
            "candidates_total": len(processed_candidates),
            "live_total": len(result.live_signals),
            "caution_total": len(result.caution_signals),
            "killed_total": len(result.killed_signals),
            "regime_map": regime_map,
            "scan_duration_sec": round(time.time() - start, 2),
        }

        logger.info(
            "V4 SUMMARY active=%s prepared=%s observations=%s candidates=%s live=%s caution=%s killed=%s",
            len(active),
            prepared_pairs,
            observations_total,
            len(processed_candidates),
            len(result.live_signals),
            len(result.caution_signals),
            len(result.killed_signals),
        )

        payload = build_signal_bus_payload(result)
        bus_path = self.bus_writer.write(payload)
        pushed = self.worker_push.push_file(bus_path)
        result.audit["worker_push_ok"] = pushed

        logger.info(
            "V4 SCAN COMPLETE duration=%s worker_push_ok=%s",
            result.audit["scan_duration_sec"],
            pushed,
        )
        return payload
