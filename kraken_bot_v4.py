"""kraken_bot_v4.py — Execution loop (Layer 6, top of stack).

Polls ``signal_bus.json`` every ``poll_interval_sec`` and executes eligible
signals through :class:`OrderExecutor`. A signal is eligible only when it is
Remi-CLEAN **and** carries ``december_verdict == 'CONFIRM'``. S-grade signals
auto-confirm; A-grade requires a human to flip the verdict to CONFIRM in the
bus (the bot never self-promotes A-grade). Everything else is ignored.

Open positions are aged out with a 3-candle exit (Rule): once a position has
been open for ``exit_after_candles`` candles of the trade timeframe, it is
closed at market. All order paths inherit the executor's ``dry_run`` guard —
with dry_run true (the default) the bot only *logs* what it would do.
"""

from __future__ import annotations

import json
import logging
import signal as signal_mod
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from order_executor_v2 import OrderExecutor
from signal_bus import SignalBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("kraken_bot_v4")

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "config.json"


class kraken_bot_v4:  # noqa: N801 - name fixed by the architecture spec.
    """Signal-bus-driven execution bot.

    Attributes:
        executor: The dry-run-guarded OrderExecutor.
        bus: SignalBus reader.
        poll_interval: Seconds between bus polls.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """Initialize the bot from config.json.

        Args:
            config_path: Override path to config.json.
        """
        self.config = self._load_config(config_path or CONFIG_PATH)
        self.executor = OrderExecutor(config=self.config)
        self.bus = SignalBus()
        self.poll_interval = int(self.config.get("poll_interval_sec", 60))
        self.auto_grade = str(self.config.get("auto_confirm_grade", "S")).upper()
        self.manual_grade = str(self.config.get("manual_confirm_grade", "A")).upper()
        self.min_conviction = float(self.config.get("min_conviction", 0.75))
        self.exit_after = int(self.config.get("exit_after_candles", 3))
        self.candle_min = int(self.config.get("candle_interval_min", 240))

        self._running = True
        # pair -> {"opened_at": iso, "signal": dict} for 3-candle exit tracking.
        self._open_trades: Dict[str, Dict[str, Any]] = {}
        self._executed_keys: set[str] = set()

    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Config load failed (%s) — DRY_RUN default.", exc)
            return {"dry_run": True, "prop_seat": "5K"}

    # ------------------------------------------------------------------
    def _is_eligible(self, sig: Dict[str, Any]) -> bool:
        """Return True if a signal may be executed this cycle.

        Gates: Remi CLEAN, conviction floor, and an explicit CONFIRM verdict.
        S-grade auto-confirms; A-grade must be human-confirmed in the bus.
        """
        if str(sig.get("remi_status", "")).upper() != "CLEAN":
            return False
        if float(sig.get("conviction", 0.0)) < self.min_conviction:
            return False

        grade = str(sig.get("grade", "")).upper()
        verdict = str(sig.get("december_verdict", "PENDING")).upper()

        if grade == self.auto_grade:
            return True  # S-grade auto-confirms.
        if grade == self.manual_grade:
            return verdict == "CONFIRM"  # A-grade needs a human flip.
        return False

    @staticmethod
    def _sig_key(sig: Dict[str, Any]) -> str:
        """Idempotency key so we execute a given fired signal only once."""
        return f"{sig.get('pair')}|{sig.get('bias')}|{sig.get('engine')}|{sig.get('fired_at')}"

    # ------------------------------------------------------------------
    def process_cycle(self) -> Dict[str, Any]:
        """Run one poll cycle: age out exits, then execute new eligible signals.

        Returns:
            A small per-cycle summary dict.
        """
        now = datetime.now(timezone.utc)
        closed = self._process_exits(now)

        bus = self.bus.get_signals()
        signals: List[Dict[str, Any]] = bus.get("signals", []) or []
        open_count = len(self.executor.get_open_positions()) + len(self._open_trades)

        executed = 0
        skipped = 0
        for sig in signals:
            key = self._sig_key(sig)
            if key in self._executed_keys:
                continue
            if not self._is_eligible(sig):
                skipped += 1
                continue
            if not self.executor.can_trade(open_count):
                logger.info("Risk gate closed — halting new entries this cycle.")
                break

            result = self.executor.place_order(sig)
            self._executed_keys.add(key)
            status = result.get("status")
            if status in ("PLACED", "SIMULATED"):
                executed += 1
                open_count += 1
                self._open_trades[sig["pair"]] = {
                    "opened_at": now.isoformat(), "signal": sig,
                }
                logger.info("Executed %s %s %s [%s] grade=%s",
                            sig.get("pair"), sig.get("bias"), sig.get("engine"),
                            status, sig.get("grade"))
            else:
                logger.info("Order not placed for %s: %s",
                            sig.get("pair"), status)

        summary = {"executed": executed, "skipped": skipped, "closed": closed,
                   "open_trades": len(self._open_trades)}
        logger.info("Cycle: %d executed, %d closed, %d skipped, %d open.",
                    executed, closed, skipped, len(self._open_trades))
        return summary

    def _process_exits(self, now: datetime) -> int:
        """Close positions that have aged past the 3-candle exit window.

        Returns:
            Number of positions closed this cycle.
        """
        max_age = timedelta(minutes=self.candle_min * self.exit_after)
        closed = 0
        for pair, rec in list(self._open_trades.items()):
            try:
                opened = datetime.fromisoformat(
                    rec["opened_at"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                self._open_trades.pop(pair, None)
                continue
            if now - opened < max_age:
                continue
            sig = rec["signal"]
            pos_type = "buy" if str(sig.get("bias")).upper() == "LONG" else "sell"
            volume = self.executor.position_size(sig["entry"], sig["sl"])
            self.executor.close_position({
                "pair_base": pair, "type": pos_type, "vol": volume,
            })
            logger.info("3-candle exit: closed %s (opened %s).", pair, rec["opened_at"])
            self._open_trades.pop(pair, None)
            closed += 1
        return closed

    # ------------------------------------------------------------------
    def run(self, max_cycles: Optional[int] = None) -> None:
        """Main loop: poll the bus and execute until stopped.

        Args:
            max_cycles: Optional cap for a bounded/test run (None = forever).
        """
        signal_mod.signal(signal_mod.SIGINT, self._handle_stop)
        signal_mod.signal(signal_mod.SIGTERM, self._handle_stop)
        logger.warning("kraken_bot_v4 starting | dry_run=%s | seat=%s | poll=%ds",
                       self.executor.dry_run, self.executor.seat, self.poll_interval)

        cycles = 0
        while self._running:
            try:
                self.process_cycle()
            except Exception as exc:  # noqa: BLE001 - loop must survive a bad cycle
                logger.error("Cycle error: %s", exc)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                logger.info("Reached max_cycles=%d — stopping.", max_cycles)
                break
            if self._running:
                time.sleep(self.poll_interval)
        logger.warning("kraken_bot_v4 stopped after %d cycle(s).", cycles)

    def _handle_stop(self, *_: Any) -> None:
        """Signal handler for graceful shutdown."""
        logger.warning("Stop signal received — finishing current cycle.")
        self._running = False


if __name__ == "__main__":
    logger.info("=== kraken_bot_v4 demo (single cycle, DRY_RUN) ===")
    bot = kraken_bot_v4()
    summary = bot.process_cycle()
    print(f"Cycle summary: {summary}")
