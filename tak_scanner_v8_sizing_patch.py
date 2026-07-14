from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List

from casino_counter import CasinoCounter

_counter = CasinoCounter()

LANE_CONFIG = [
    {
        "key": "Dragon",
        "label": "Dragon",
        "account": "Eval 4",
        "baseline": 24193,
        "risk_dollars": 177,
        "mode": "FULL_AGGRESSION",
        "priority": 1,
    },
    {
        "key": "Starter3",
        "label": "Starter 3",
        "account": "Starter 3",
        "baseline": 9725,
        "risk_dollars": 130,
        "mode": "FULL_AGGRESSION",
        "priority": 2,
    },
    {
        "key": "Starter2",
        "label": "Starter 2",
        "account": "Starter 2",
        "baseline": 9729,
        "risk_dollars": 66,
        "mode": "FULL_AGGRESSION",
        "priority": 3,
    },
    {
        "key": "Eval1",
        "label": "Eval 1",
        "account": "Eval 1",
        "baseline": 4817,
        "risk_dollars": 13,
        "mode": "PROTECT_ONLY",
        "priority": 4,
    },
]


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class TakScannerV8:
    @staticmethod
    def _compute_position_sizing(signal: Dict[str, Any]) -> OrderedDict[str, Dict[str, Any]]:
        entry = _safe_float(signal.get("entry"))
        sl = _safe_float(signal.get("sl"))
        pair = signal.get("pair") or signal.get("symbol") or "UNKNOWN"
        side = (signal.get("bias") or signal.get("direction") or "").upper()

        sizing: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        if entry is None or sl is None or entry == sl:
            return sizing

        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return sizing

        for lane in sorted(LANE_CONFIG, key=lambda x: x["priority"]):
        for lane in sorted(LANE_CONFIG, key=lambda x: x["priority"]):
        if lane["mode"] == "PROTECT_ONLY":
        sizing[lane["key"]] = {
            "label": lane["label"],
            "account": lane["account"],
            "baseline": lane["baseline"],
            "risk_dollars": lane["risk_dollars"],
            "units": 0,
            "dollar_risk": 0,
            "mode": lane["mode"],
            "priority": lane["priority"],
            "note": "PROTECT ONLY",
        }
        continue

    # TEMP: disable casino_counter, use pure math
    units = lane["risk_dollars"] / risk_per_unit
    units = round(float(units), 4)
    dollar_risk = round(units * risk_per_unit, 2)

    sizing[lane["key"]] = {
        "label": lane["label"],
        "account": lane["account"],
        "baseline": lane["baseline"],
        "risk_dollars": lane["risk_dollars"],
        "units": units,
        "dollar_risk": dollar_risk,
        "mode": lane["mode"],
        "priority": lane["priority"],
    }

return sizing

            units = None
            try:
                units = _counter.get_position_size(
                    pair=pair,
                    entry_price=entry,
                    stop_loss=sl,
                    risk_dollars=lane["risk_dollars"],
                    side=side or None,
                )
            except TypeError:
                try:
                    units = _counter.get_position_size(entry, sl, lane["risk_dollars"])
                except Exception:
                    units = None
            except Exception:
                units = None

            if units is None:
                units = lane["risk_dollars"] / risk_per_unit

            units = round(float(units), 4)
            dollar_risk = round(units * risk_per_unit, 2)

            sizing[lane["key"]] = {
                "label": lane["label"],
                "account": lane["account"],
                "baseline": lane["baseline"],
                "risk_dollars": lane["risk_dollars"],
                "units": units,
                "dollar_risk": dollar_risk,
                "mode": lane["mode"],
                "priority": lane["priority"],
            }

        return sizing

    @staticmethod
    def _attach_position_sizing(signal: Dict[str, Any]) -> Dict[str, Any]:
        signal["position_sizing"] = TakScannerV8._compute_position_sizing(signal)
        signal["top_lane"] = "Dragon"
        signal["prop_display"] = [
            signal["position_sizing"][k]
            for k in ["Dragon", "Starter3", "Starter2", "Eval1"]
            if k in signal["position_sizing"]
        ]
        return signal

    def _finalize_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        signal = dict(signal)
        return self._attach_position_sizing(signal)

    @staticmethod
    def _fire_alerts(signals: List[Dict[str, Any]], quiet: bool, sprint_mode: bool = False) -> None:
        from alerts import fire_alerts
        fire_alerts(signals, quiet=quiet, sprint_mode=sprint_mode)


def _format_position_sizing(signal: Dict[str, Any]) -> str:
    sizing = signal.get("position_sizing") or {}
    if not sizing:
        return "Sizing: N/A"

    lines = []
    for lane_key in ["Dragon", "Starter3", "Starter2", "Eval1"]:
        row = sizing.get(lane_key)
        if not row:
            continue
        label = row.get("label", lane_key)
        if row.get("mode") == "PROTECT_ONLY":
            lines.append(f"{label}: PROTECT ONLY")
        else:
            lines.append(f"{label}: {row['units']:.4f} units | ${row['dollar_risk']:.2f} risk")
    return "\n".join(lines) if lines else "Sizing: N/A"


if __name__ == "__main__":
    scanner = TakScannerV8()
    demo_signal = {
        "pair": "TRXUSD",
        "bias": "LONG",
        "entry": 0.3315,
        "sl": 0.3300,
        "tp": 0.3347,
        "grade": "A",
    }
    finalized = scanner._finalize_signal(demo_signal)
    print(finalized)
    print(_format_position_sizing(finalized))
