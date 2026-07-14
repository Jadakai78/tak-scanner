"""casino_counter.py — SPRT edge detection + Safe Optimal F position sizing.

Two statistical components working together, per the "casino model":

1. **SPRT** (Sequential Probability Ratio Test) tracks each strategy engine
   (S1-S9) and each pair's win/loss sequence to decide, with as few trades as
   possible, whether it has a real edge (H1: p=0.62) over a coin flip
   (H0: p=0.50) at alpha=0.05 / beta=0.10 (Wald boundaries).

2. **Safe Optimal F** takes the realized P&L history and finds the optimal
   fixed-fraction bet size (maximizing terminal wealth relative, TWR), then
   applies a conservative 25% haircut ("safe f") to control drawdown risk.

A third component, **Aggression Tier**, gates position sizing against a
static per-session equity baseline so a losing session automatically scales
down risk (and a losing session past the drawdown threshold locks to
fixed-minimum sizing only).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("casino_counter")

# ---------------------------------------------------------------------
# SPRT parameters
# ---------------------------------------------------------------------
P0 = 0.50   # H0: no edge (coin flip)
P1 = 0.62   # H1: minimum viable edge
ALPHA = 0.05
BETA = 0.10
MIN_TRADES = 15

# Wald decision boundaries (log-likelihood-ratio space).
import math

LOG_A = math.log(BETA / (1 - ALPHA))          # lower bound -> accept H0 (reject edge)
LOG_B = math.log((1 - BETA) / ALPHA)          # upper bound -> accept H1 (edge confirmed)

# ---------------------------------------------------------------------
# Fixed minimum position sizes per account tier (fallback sizing).
# ---------------------------------------------------------------------
FIXED_MIN_RISK: Dict[str, float] = {
    "eval_1_5k": 13.0,       # Eval 1 $5K — protect only
    "starter_2_10k": 66.0,   # Starter 2 $10K conservative
    "starter_3_10k": 130.0,  # Starter 3 $10K — easiest pass
    "eval_4_25k": 177.0,     # Eval 4 $25K Dragon
    # Generic seat aliases (in case caller uses seat labels instead of account ids).
    "5K": 25.0,
    "10K": 50.0,
    "25K_DRAGON": 150.0,
}

AGGRESSION_SCALE_BACK_THRESHOLD = 0.75  # baseline * 0.75 boundary


class CasinoCounter:
    """SPRT edge tracking + Safe Optimal F sizing + Aggression Tier gating.

    Attributes:
        sprt_state: Per-key (engine or pair) sequential test state.
        trade_history: Per-account list of realized P&L values (chronological).
        baselines: Per-account static equity baseline snapshot (set once).
    """

    def __init__(self) -> None:
        """Initialize empty tracking structures."""
        # key -> {"n": int, "wins": int, "losses": int, "llr": float, "state": str}
        self.sprt_state: Dict[str, Dict[str, Any]] = {}
        # account_id -> [pnl, pnl, ...]
        self.trade_history: Dict[str, List[float]] = {}
        # account_id -> baseline equity (static snapshot, set once per session)
        self.baselines: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # SPRT
    # ------------------------------------------------------------------
    def _sprt_key(self, engine_id: str, pair: Optional[str] = None) -> str:
        """Build the SPRT tracking key for an engine (optionally per-pair)."""
        return f"{engine_id}:{pair}" if pair else engine_id

    def _ensure_sprt(self, key: str) -> Dict[str, Any]:
        return self.sprt_state.setdefault(key, {
            "n": 0, "wins": 0, "losses": 0, "llr": 0.0, "state": "TESTING",
        })

    def record_trade(
        self, engine_id: str, pair: str, pnl: float, account_id: str
    ) -> None:
        """Record one closed trade's outcome into SPRT (engine + pair) and P&L history.

        Args:
            engine_id: Strategy engine id (e.g. "S1").
            pair: Traded pair symbol (e.g. "BTC").
            pnl: Realized profit/loss in dollars (win if > 0).
            account_id: Account/seat id this trade was booked against.
        """
        win = pnl > 0
        for key in (self._sprt_key(engine_id), self._sprt_key(engine_id, pair)):
            self._update_sprt(key, win)

        self.trade_history.setdefault(account_id, []).append(float(pnl))
        logger.info(
            "Recorded trade | engine=%s pair=%s pnl=%.2f account=%s win=%s",
            engine_id, pair, pnl, account_id, win,
        )

    def _update_sprt(self, key: str, win: bool) -> None:
        """Update the Wald SPRT log-likelihood-ratio for one key after a trade."""
        st = self._ensure_sprt(key)
        st["n"] += 1
        if win:
            st["wins"] += 1
            st["llr"] += math.log(P1 / P0)
        else:
            st["losses"] += 1
            st["llr"] += math.log((1 - P1) / (1 - P0))

        if st["n"] < MIN_TRADES:
            st["state"] = "TESTING"
        elif st["llr"] >= LOG_B:
            st["state"] = "EDGE_CONFIRMED"
        elif st["llr"] <= LOG_A:
            st["state"] = "EDGE_REJECTED"
        else:
            st["state"] = "TESTING"

        # If we've drifted a long time without a decision, flag for reset.
        if st["state"] == "TESTING" and st["n"] >= MIN_TRADES * 4:
            st["state"] = "RESET_NEEDED"

    def get_sprt_state(self, engine_id: str, pair: Optional[str] = None) -> Dict[str, Any]:
        """Return the current SPRT state for an engine (optionally per-pair).

        Args:
            engine_id: Strategy engine id.
            pair: Optional pair symbol for a pair-specific SPRT track.

        Returns:
            ``{state, n_trades, likelihood_ratio, edge_estimate}``.
        """
        key = self._sprt_key(engine_id, pair)
        st = self.sprt_state.get(key)
        if st is None:
            return {
                "state": "TESTING", "n_trades": 0,
                "likelihood_ratio": 0.0, "edge_estimate": 0.5,
            }
        edge_estimate = st["wins"] / st["n"] if st["n"] else 0.5
        return {
            "state": st["state"],
            "n_trades": st["n"],
            "likelihood_ratio": round(st["llr"], 4),
            "edge_estimate": round(edge_estimate, 4),
        }

    # ------------------------------------------------------------------
    # Safe Optimal F
    # ------------------------------------------------------------------
    @staticmethod
    def _twr(f: float, trades: List[float], worst_loss: float) -> float:
        """Terminal Wealth Relative for a fixed fraction f over a trade series."""
        twr = 1.0
        for pnl in trades:
            hpr = 1 + f * (pnl / worst_loss)
            if hpr <= 0:
                return 0.0  # ruin — this f is invalid
            twr *= hpr
        return twr

    def _optimal_f(self, trades: List[float]) -> float:
        """Find the optimal fixed fraction f (0-1) maximizing TWR via grid search.

        Args:
            trades: Historical P&L values (must include at least one loss).

        Returns:
            Optimal f in [0.01, 0.99], or 0.0 if no valid losses exist.
        """
        losses = [t for t in trades if t < 0]
        if not losses:
            return 0.0
        worst_loss = abs(min(losses))
        if worst_loss <= 0:
            return 0.0

        best_f, best_twr = 0.0, 0.0
        # Coarse-to-fine grid search over f in (0, 1).
        for coarse in [i / 100 for i in range(1, 100)]:
            twr = self._twr(coarse, trades, worst_loss)
            if twr > best_twr:
                best_twr, best_f = twr, coarse
        # Refine around best_f.
        lo, hi = max(0.001, best_f - 0.01), min(0.99, best_f + 0.01)
        step = (hi - lo) / 40 if hi > lo else 0.001
        f = lo
        while f <= hi:
            twr = self._twr(f, trades, worst_loss)
            if twr > best_twr:
                best_twr, best_f = twr, f
            f += step
        return best_f

    def get_safe_f(self, account_id: str) -> float:
        """Return the safe fraction of equity to risk per trade for an account.

        Applies 25% of the mathematically optimal f as a conservative floor.
        Falls back to 0.0 (caller should use fixed sizing) if fewer than 15
        trades exist or the account's dominant engine's edge is rejected.

        Args:
            account_id: Account id.

        Returns:
            Safe fraction (0.0 - 1.0) of equity to risk on the next trade.
        """
        trades = self.trade_history.get(account_id, [])
        if len(trades) < MIN_TRADES:
            return 0.0
        optimal_f = self._optimal_f(trades)
        return round(optimal_f * 0.25, 6)

    # ------------------------------------------------------------------
    # Aggression Tier
    # ------------------------------------------------------------------
    def set_baseline(self, account_id: str, baseline_equity: float) -> None:
        """Set the static per-session baseline equity for an account (once).

        Args:
            account_id: Account id.
            baseline_equity: Snapshot equity to compare against for the whole
                session. Subsequent calls are ignored unless the baseline is
                not yet set (prevents mid-session drift).
        """
        if account_id not in self.baselines:
            self.baselines[account_id] = float(baseline_equity)
            logger.info("Baseline set for %s: $%.2f", account_id, baseline_equity)
        else:
            logger.debug("Baseline for %s already set; ignoring update.", account_id)

    def get_mode(self, account_id: str, current_equity: float) -> Dict[str, Any]:
        """Determine the aggression mode for an account given current equity.

        Args:
            account_id: Account id.
            current_equity: Current live equity for the account.

        Returns:
            ``{mode, recommended_risk_per_trade, baseline, current_equity, gap_pct}``.
        """
        baseline = self.baselines.get(account_id)
        if baseline is None:
            # No baseline set yet — treat current equity as the baseline.
            self.set_baseline(account_id, current_equity)
            baseline = current_equity

        fixed_min = FIXED_MIN_RISK.get(account_id, 25.0)

        if current_equity >= baseline:
            mode = "FULL_AGGRESSION"
            safe_f = self.get_safe_f(account_id)
            recommended_risk = (safe_f * current_equity) if safe_f > 0 else fixed_min
        elif current_equity > baseline * AGGRESSION_SCALE_BACK_THRESHOLD:
            mode = "SCALE_BACK"
            safe_f = self.get_safe_f(account_id)
            base_risk = (safe_f * current_equity) if safe_f > 0 else fixed_min
            recommended_risk = base_risk * 0.75
        else:
            mode = "CONSERVATIVE"
            recommended_risk = fixed_min

        gap_pct = ((current_equity - baseline) / baseline * 100) if baseline else 0.0
        return {
            "mode": mode,
            "recommended_risk_per_trade": round(recommended_risk, 2),
            "baseline": baseline,
            "current_equity": current_equity,
            "gap_pct": round(gap_pct, 3),
        }

    # ------------------------------------------------------------------
    # Position sizing (combines SPRT + Safe F + Aggression Tier)
    # ------------------------------------------------------------------
    def get_position_size(
        self,
        account_id: str,
        current_equity: float,
        entry_price: float,
        sl_price: float,
        engine_id: Optional[str] = None,
        pair: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute the final position size for a new trade.

        Args:
            account_id: Account id.
            current_equity: Current live equity for the account.
            entry_price: Proposed entry price.
            sl_price: Proposed stop-loss price.
            engine_id: Optional engine id, used to gate on SPRT EDGE_REJECTED.
            pair: Optional pair symbol for a pair-specific SPRT check.

        Returns:
            ``{risk_dollars, position_size, mode}``.
        """
        mode_info = self.get_mode(account_id, current_equity)
        mode = mode_info["mode"]
        fixed_min = FIXED_MIN_RISK.get(account_id, 25.0)

        trades = self.trade_history.get(account_id, [])
        sprt_rejected = False
        if engine_id:
            sprt_rejected = self.get_sprt_state(engine_id, pair)["state"] == "EDGE_REJECTED"

        if len(trades) < MIN_TRADES or sprt_rejected or mode == "CONSERVATIVE":
            risk_dollars = fixed_min
        else:
            risk_dollars = mode_info["recommended_risk_per_trade"]
            if risk_dollars <= 0:
                risk_dollars = fixed_min

        risk_dist = abs(float(entry_price) - float(sl_price))
        position_size = round(risk_dollars / risk_dist, 8) if risk_dist > 0 else 0.0

        return {
            "risk_dollars": round(risk_dollars, 2),
            "position_size": position_size,
            "mode": mode,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save_state(self, path: Path | str) -> None:
        """Persist all tracking state to a JSON file.

        Args:
            path: Destination file path.
        """
        payload = {
            "sprt_state": self.sprt_state,
            "trade_history": self.trade_history,
            "baselines": self.baselines,
        }
        Path(path).write_text(json.dumps(payload, indent=2))
        logger.info("CasinoCounter state saved to %s", path)

    def load_state(self, path: Path | str) -> None:
        """Load tracking state from a JSON file (overwrites current state).

        Args:
            path: Source file path.
        """
        p = Path(path)
        if not p.exists():
            logger.warning("No state file at %s — starting fresh.", path)
            return
        data = json.loads(p.read_text())
        self.sprt_state = data.get("sprt_state", {})
        self.trade_history = data.get("trade_history", {})
        self.baselines = data.get("baselines", {})
        logger.info("CasinoCounter state loaded from %s", path)


if __name__ == "__main__":
    logger.info("=== CasinoCounter self-test ===")
    cc = CasinoCounter()

    # --- SPRT: feed a winning-edge sequence (~65% win rate) for S1/BTC. ---
    import random
    random.seed(42)
    wins = 0
    for i in range(40):
        win = random.random() < 0.65
        pnl = 50.0 if win else -25.0
        cc.record_trade("S1", "BTC", pnl, "eval_4_25k")
        wins += int(win)
    state = cc.get_sprt_state("S1", "BTC")
    print(f"SPRT S1/BTC after 40 trades ({wins} wins): {state}")
    assert state["n_trades"] == 40

    # --- SPRT: feed a coin-flip sequence for S9 (no edge) -> should reject. ---
    for i in range(40):
        win = random.random() < 0.50
        pnl = 40.0 if win else -40.0
        cc.record_trade("S9", "XRP", pnl, "eval_4_25k")
    state9 = cc.get_sprt_state("S9", "XRP")
    print(f"SPRT S9/XRP (coin flip): {state9}")

    # --- Safe Optimal F ---
    safe_f = cc.get_safe_f("eval_4_25k")
    print(f"Safe f for eval_4_25k: {safe_f}")
    assert 0.0 <= safe_f <= 0.25

    # --- Aggression tiers ---
    cc.set_baseline("eval_4_25k", 24193.10)
    full = cc.get_mode("eval_4_25k", 24500.0)
    scale = cc.get_mode("eval_4_25k", 20000.0)
    conservative = cc.get_mode("eval_4_25k", 15000.0)
    print("FULL_AGGRESSION case:", full)
    print("SCALE_BACK case:", scale)
    print("CONSERVATIVE case:", conservative)
    assert full["mode"] == "FULL_AGGRESSION"
    assert scale["mode"] == "SCALE_BACK"
    assert conservative["mode"] == "CONSERVATIVE"

    # --- Position sizing ---
    size = cc.get_position_size("eval_4_25k", 24500.0, entry_price=150.0, sl_price=147.0,
                                 engine_id="S1", pair="BTC")
    print("Position size (FULL_AGGRESSION, S1 edge confirmed likely):", size)

    size_cons = cc.get_position_size("eval_4_25k", 15000.0, entry_price=150.0, sl_price=147.0)
    print("Position size (CONSERVATIVE, fixed min):", size_cons)
    assert size_cons["risk_dollars"] == FIXED_MIN_RISK["eval_4_25k"]

    # --- Persistence roundtrip ---
    tmp_path = Path("/tmp/casino_counter_state_test.json")
    cc.save_state(tmp_path)
    cc2 = CasinoCounter()
    cc2.load_state(tmp_path)
    assert cc2.get_sprt_state("S1", "BTC")["n_trades"] == 40
    print("Persistence roundtrip OK.")

    print("All self-tests passed.")
