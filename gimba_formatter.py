from __future__ import annotations

from typing import Any, Dict, List


INTENT_TO_ACTION = {
    "ATTACKTRAP": "CLICK",
    "ATTACKBREAK": "CLICK",
    "ATTACK": "CLICK",
    "PROBE": "WATCH CLOSE",
    "WAIT": "WAIT",
    "CUT": "CUT",
    "IGNORE": "REJECT",
}

FAMILY_LABELS = {
    "BOTTLE": "RTS-BOTTLE",
    "CHOCH": "RTS-CHOCH",
    "BOS": "RTS-BOS",
    "ZONE": "RTS-ZONE",
    "LIQ": "RTS-LIQ",
}


def _f(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "?"


def _price(value: Any) -> str:
    try:
        v = float(value)
    except Exception:
        return "?"
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


def _family(signal: Dict[str, Any]) -> str:
    family = str(signal.get("rtsfamily") or signal.get("engine") or "RTS").upper()
    return FAMILY_LABELS.get(family, family)


def _action(signal: Dict[str, Any]) -> str:
    intent = str(signal.get("intent") or "WAIT").upper()
    return INTENT_TO_ACTION.get(intent, "WAIT")


def _story(signal: Dict[str, Any]) -> str:
    family = str(signal.get("rtsfamily") or "").upper()
    intent = str(signal.get("intent") or "WAIT").upper()
    reasons = signal.get("rtsreasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []

    if family == "BOTTLE":
        if intent == "ATTACKTRAP":
            return "Flush got reclaimed. The bottle is alive and sponsorship is backing the reversal."
        if intent == "ATTACK":
            return "Structure has flipped, but the move still wants cleaner sponsorship."
        if intent == "PROBE":
            return "Base is forming after the flush. We are early, not blind."
    if family == "CHOCH":
        if intent in {"ATTACKTRAP", "ATTACKBREAK", "ATTACK"}:
            return "Control has shifted. The sweep is no longer just noise."
        return "A structure flip is trying to form, but it has not earned full trust yet."
    if family == "BOS":
        if intent == "ATTACKBREAK":
            return "Break held and the retest is clean. This is continuation, not hope."
        if intent == "PROBE":
            return "The break is on record, but the retest still needs respect."
    if family == "ZONE":
        return "Price is back at a decision zone. This is location before aggression."
    if family == "LIQ":
        return "Liquidity has been disturbed. Now we watch for reclaim or continuation."

    if reasons:
        return str(reasons[0]).rstrip(".") + "."
    return "The council has a live read, but the table wants one more piece of proof."


def _reasons_text(signal: Dict[str, Any]) -> str:
    reasons = signal.get("rtsreasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []
    cleaned = [str(r).strip().rstrip(".") for r in reasons if str(r).strip()]
    return ", ".join(cleaned[:4]) if cleaned else "No council notes yet"


def format_gimba_message(signal: Dict[str, Any]) -> str:
    pair = str(signal.get("pair") or "?")
    bias = str(signal.get("bias") or "?")
    regime = str(signal.get("regime") or "?")
    tier = str(signal.get("tier") or "TIERB")
    intent = str(signal.get("intent") or "WAIT")
    family = _family(signal)
    action = _action(signal)
    kill = _price(signal.get("killlevel") or signal.get("sl"))
    entry = _price(signal.get("entry"))
    tp = _price(signal.get("tp"))
    off = _f(signal.get("offencescore"), 2)
    deff = _f(signal.get("defencescore"), 2)
    trap = _f(signal.get("trapscore"), 2)
    story = _story(signal)
    reasons = _reasons_text(signal)

    return (
        "GIMBA COUNCIL\n"
        f"{pair} | {family} | {intent}\n"
        f"{bias} | {regime} | {tier}\n\n"
        f"{story}\n"
        f"Action: {action}\n"
        f"Entry: {entry} | TP: {tp}\n"
        f"Kill: {kill}\n"
        f"Off/Def/Trap: {off} / {deff} / {trap}\n"
        f"Reasons: {reasons}"
    )


__all__ = ["format_gimba_message"]
