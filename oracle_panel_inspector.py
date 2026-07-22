from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List


BUS_CANDIDATES = [
    Path("/app/data/signal_bus.json"),
    Path("/app/data/last_good_signal_bus.json"),
    Path("signal_bus.json"),
    Path("last_good_signal_bus.json"),
]


def load_bus() -> tuple[Path, Dict[str, Any]]:
    for path in BUS_CANDIDATES:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return path, json.load(f)
    raise FileNotFoundError("No signal bus snapshot found.")


def all_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for lane in ("opportunities", "watchlist", "killed"):
        for row in payload.get(lane, []) or []:
            if isinstance(row, dict):
                item = dict(row)
                item["_lane"] = lane
                rows.append(item)
    return rows


def sort_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            -float(r.get("score") or 0.0),
            -float(r.get("confidence") or 0.0),
            float(r.get("trap_score") or 0.0),
        ),
    )


def compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    diagnostics = dict(row.get("diagnostics") or {})
    return {
        "lane": row.get("_lane"),
        "pair": row.get("pair"),
        "action_state": row.get("action_state"),
        "board_state": diagnostics.get("board_state"),
        "score": row.get("score"),
        "confidence": row.get("confidence"),
        "trap_score": row.get("trap_score"),
        "specialist": row.get("specialist") or diagnostics.get("primary_weapon"),
        "regime": row.get("regime"),
        "htf_bias": row.get("htf_bias"),
        "htf_alignment": row.get("htf_alignment"),
        "why_now": row.get("why_now"),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    lane_counts = Counter()
    board_state_counts = Counter()
    action_state_counts = Counter()
    regime_counts = Counter()
    specialist_counts = Counter()
    bias_counts = Counter()

    for row in rows:
        lane_counts[row.get("_lane") or "unknown"] += 1
        action_state_counts[row.get("action_state") or "unknown"] += 1
        regime_counts[row.get("regime") or "UNKNOWN"] += 1
        bias_counts[row.get("htf_bias") or "mixed"] += 1

        diagnostics = dict(row.get("diagnostics") or {})
        board_state_counts[diagnostics.get("board_state") or "unknown"] += 1
        specialist_counts[row.get("specialist") or diagnostics.get("primary_weapon") or "none"] += 1

    return {
        "lane_counts": dict(lane_counts),
        "action_state_counts": dict(action_state_counts),
        "board_state_counts": dict(board_state_counts),
        "regime_counts": dict(regime_counts),
        "bias_counts": dict(bias_counts),
        "top_specialists": dict(specialist_counts.most_common(10)),
    }


def main() -> None:
    path, payload = load_bus()
    rows = all_rows(payload)
    ordered = sort_rows(rows)

    report = {
        "source_path": str(path),
        "generated_at": payload.get("generated_at"),
        "summary": payload.get("summary"),
        "counts": summarize(rows),
        "top_10_rows": [compact_row(r) for r in ordered[:10]],
        "sample_watchlist": [compact_row(r) for r in rows if r.get("_lane") == "watchlist"][:5],
        "sample_killed": [compact_row(r) for r in rows if r.get("_lane") == "killed"][:5],
        "sample_opportunities": [compact_row(r) for r in rows if r.get("_lane") == "opportunities"][:5],
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
