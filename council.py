"""council.py — April + Remi Council Decision Layer

Council = April + Remi only.
April issues STAND_DOWN codes when bots are misfiring in wrong regimes.
Remi handles front-end classification and position management.
"""
import os
from typing import Dict, List, Any, Optional
from datetime import datetime


class AprilFieldGeneral:
    """April — Field General monitoring bot performance and regime fit."""
    
    def __init__(self):
        self.mode_history = []
        self.last_decision = None
        
    def assess_bot_performance(
        self,
        signals: List[Dict[str, Any]],
        regime_map: Dict[str, str],
        fg_score: int,
        fg_label: str
    ) -> Dict[str, Any]:
        """
        Assess whether bots are firing in appropriate regime conditions.
        Returns april_view object for the canonical snapshot.
        """
        
        # Default to NORMAL operational mode
        council_mode = "NORMAL"
        status_code = None
        affected_bots = []
        regime_context = f"{fg_label} (FG: {fg_score})"
        
        # Check for regime mismatches based on BOT_SCORING_AUDIT findings
        
        # Rule 1: Gimba bots should NOT fire in Extreme Fear without confirmed volatility
        if fg_label == "Extreme Fear" and fg_score < 30:
            gimba_signals = [
                s for s in signals 
                if s.get('bot_name', '').startswith(('S3', 'S4', 'S5', 'S6'))
            ]
            if gimba_signals:
                council_mode = "STAND_DOWN"
                status_code = "GIMBA_IN_EXTREME_FEAR"
                affected_bots = [s.get('bot_name') for s in gimba_signals]
        
        # Rule 2: MTF bots firing without valid regime confirmation
        mtf_signals = [
            s for s in signals
            if 'MTF' in s.get('bot_name', '')
        ]
        for sig in mtf_signals:
            pair = sig.get('pair')
            if pair and regime_map.get(pair) == 'DEAD':
                council_mode = "STAND_DOWN"
                status_code = "MTF_IN_DEAD_REGIME"
                if sig.get('bot_name') not in affected_bots:
                    affected_bots.append(sig.get('bot_name'))
        
        # Rule 3: High signal count in choppy/RANGE regime
        range_pairs = [p for p, r in regime_map.items() if r == 'RANGE']
        if len(signals) > 5 and len(range_pairs) > len(regime_map) * 0.6:
            council_mode = "STAND_DOWN"
            status_code = "EXCESSIVE_SIGNALS_IN_RANGE"
            affected_bots = list(set([s.get('bot_name') for s in signals]))
        
        # Rule 4: TIME_TO_HUNT when conditions are optimal
        if fg_label in ["Greed", "Extreme Greed"]:
            trend_up_pairs = [p for p, r in regime_map.items() if r == 'TREND_UP']
            if len(trend_up_pairs) > 10 and len(signals) >= 2:
                council_mode = "TIME_TO_HUNT"
                status_code = "OPTIMAL_CONDITIONS"
        
        april_view = {
            "council_mode": council_mode,
            "status_code": status_code,
            "regime_context": regime_context,
            "affected_bots": affected_bots,
            "assessment_time": datetime.utcnow().isoformat() + 'Z',
            "signal_count": len(signals),
            "notes": self._generate_notes(council_mode, status_code, affected_bots)
        }
        
        self.last_decision = april_view
        self.mode_history.append({
            "mode": council_mode,
            "timestamp": april_view["assessment_time"]
        })
        
        return april_view
    
    def _generate_notes(self, mode: str, status_code: Optional[str], bots: List[str]) -> str:
        """Generate human-readable notes for April's decision."""
        if mode == "STAND_DOWN":
            if status_code == "GIMBA_IN_EXTREME_FEAR":
                return f"Gimba bots misfiring in Extreme Fear — recommend parameter review."
            elif status_code == "MTF_IN_DEAD_REGIME":
                return f"MTF bots attempting signals in DEAD regime — halting execution."
            elif status_code == "EXCESSIVE_SIGNALS_IN_RANGE":
                return f"Too many signals in choppy/RANGE market — reducing noise."
            else:
                return "Regime mismatch detected — standing down bots."
        elif mode == "TIME_TO_HUNT":
            return "Optimal market conditions detected — green light for execution."
        else:
            return "Normal operations — all systems nominal."


def build_council_assessment(
    signals: List[Dict[str, Any]],
    regime_map: Dict[str, str],
    fg_score: int,
    fg_label: str
) -> Dict[str, Any]:
    """
    Build April's council assessment for inclusion in canonical snapshot.
    
    Args:
        signals: List of active signals from the scanner
        regime_map: Dict mapping pair -> regime classification
        fg_score: Fear & Greed score (0-100)
        fg_label: Fear & Greed label string
    
    Returns:
        april_view dict for canonical snapshot
    """
    april = AprilFieldGeneral()
    return april.assess_bot_performance(signals, regime_map, fg_score, fg_label)
