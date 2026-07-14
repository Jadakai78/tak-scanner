"""propone_council.py
Reads signal_bus.json and prints suggested close->open actions for PropOne
based on max_margin, used_margin, and simple flat/negative filters.
"""
import json
from pathlib import Path

BUS_PATH = Path('signal_bus.json')

def load_bus(path: Path = BUS_PATH):
    with path.open('r') as f:
        return json.load(f)

def infer_margin_for_position(pos):
    """Fallback: use Dragon dollar_risk as margin proxy if margin not stored.
    Expects pos to have a 'position_sizing' block like signals do.
    """
    sizing = pos.get('position_sizing', {})
    dragon = sizing.get('Dragon', {})
    return float(dragon.get('dollar_risk', 0.0))

def get_pnl(pos):
    """Read unrealized PnL if present, else treat as flat (0)."""
    return float(pos.get('unrealized_pnl', 0.0))

def suggest_replacements(bus=None):
    if bus is None:
        bus = load_bus()

    account_state = bus.get('account_state', {}).get('PropOne', {})
    council_cfg = bus.get('council_actions', {}).get('PropOne', {})

    max_margin = float(account_state.get('max_margin', 0.0))
    used_margin = float(account_state.get('used_margin', 0.0))
    remaining_margin = float(account_state.get('remaining_margin', max_margin - used_margin))
    can_take_new_trade = bool(account_state.get('can_take_new_trade', True))

    rules = council_cfg.get('rules', {})
    close_flat_or_negative_first = bool(rules.get('close_flat_or_negative_first', True))
    min_pnl_to_consider_close = float(rules.get('min_pnl_to_consider_close', -5.0))

    open_positions = bus.get('open_positions', [])

    suggestions = {
        'meta': {
            'max_margin': max_margin,
            'used_margin': used_margin,
            'remaining_margin': remaining_margin,
            'can_take_new_trade': can_take_new_trade,
            'rule_close_flat_or_negative_first': close_flat_or_negative_first,
            'rule_min_pnl_to_consider_close': min_pnl_to_consider_close,
        },
        'candidates_to_close': [],
    }

    # Rank positions: most negative PnL first, then oldest.
    ranked = []
    for pos in open_positions:
        pnl = get_pnl(pos)
        margin = infer_margin_for_position(pos)
        pair = pos.get('pair')
        engine = pos.get('engine')
        fired_at = pos.get('fired_at')
        ranked.append({
            'pair': pair,
            'engine': engine,
            'pnl': pnl,
            'margin_proxy': margin,
            'fired_at': fired_at,
        })

    ranked.sort(key=lambda x: (x['pnl'], x['fired_at'] or ''))

    for r in ranked:
        if not close_flat_or_negative_first:
            break
        if r['pnl'] <= min_pnl_to_consider_close:
            suggestions['candidates_to_close'].append(r)

    return suggestions

if __name__ == '__main__':
    s = suggest_replacements()
    print(json.dumps(s, indent=2))
