#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timezone

BUS_PATH = Path('signal_bus.json')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_bus(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_bus(path: Path, bus):
    path.write_text(json.dumps(bus, indent=2), encoding='utf-8')


def ensure_account_state(bus):
    if not isinstance(bus.get('account_state'), dict):
        bus['account_state'] = {}
    if 'PropOne' not in bus['account_state'] or not isinstance(bus['account_state']['PropOne'], dict):
        bus['account_state']['PropOne'] = {
            'max_margin': 250.0,
            'used_margin': 0.0,
            'remaining_margin': 250.0,
            'can_take_new_trade': True,
            'recovery_mode': True,
            'sizing_mode': 'PERCENTAGE',
            'sizing_percent': 10,
            'updated_at': now_iso(),
        }
    return bus['account_state']['PropOne']


def ensure_council(bus):
    if not isinstance(bus.get('council_actions'), dict):
        bus['council_actions'] = {}
    if 'PropOne' not in bus['council_actions'] or not isinstance(bus['council_actions']['PropOne'], dict):
        bus['council_actions']['PropOne'] = {}
    return bus['council_actions']['PropOne']


def ensure_recovery_rules(bus):
    if not isinstance(bus.get('recovery_rules'), dict):
        bus['recovery_rules'] = {}
    if 'PropOne' not in bus['recovery_rules'] or not isinstance(bus['recovery_rules']['PropOne'], dict):
        bus['recovery_rules']['PropOne'] = {
            'rule_close_flat_or_negative_first': True,
            'rule_min_pnl_to_consider_close': -5.0,
            'prefer_rts_or_gimba': True,
            'updated_at': now_iso(),
        }
    return bus['recovery_rules']['PropOne']


def signal_score(sig):
    conviction = float(sig.get('conviction_v2') or sig.get('conviction') or 0)
    mtf = float(sig.get('mtf_score') or 0)
    rr = float(sig.get('rr') or 0)
    struct = float(sig.get('structure_quality') or 0)
    bonus = 0.0
    engine = str(sig.get('engine') or '').upper()
    if 'RTS' in engine:
        bonus += 0.12
    if 'GIMBA' in engine:
        bonus += 0.10
    action = str(sig.get('v2_action') or sig.get('action_state') or '').upper()
    if 'CLICK' in action or 'CONFIRM' in action:
        bonus += 0.05
    return conviction * 0.45 + mtf * 0.2 + min(rr, 3.0) * 0.08 + struct * 0.2 + bonus


def is_prop_candidate(sig):
    engine = str(sig.get('engine') or '').upper()
    return ('RTS' in engine) or ('GIMBA' in engine)


def best_candidate(bus):
    signals = bus.get('signals') or []
    candidates = [s for s in signals if is_prop_candidate(s)]
    if not candidates:
        return None
    candidates.sort(key=signal_score, reverse=True)
    return candidates[0]


def open_positions(bus):
    positions = bus.get('open_positions')
    return positions if isinstance(positions, list) else []


def weakest_position(positions, min_pnl=-5.0):
    if not positions:
        return None
    ranked = []
    for p in positions:
        pnl = float(p.get('unrealized_pnl') or p.get('pnl') or 0)
        ranked.append((pnl, p))
    ranked.sort(key=lambda x: x[0])
    if ranked and ranked[0][0] <= min_pnl:
        return ranked[0][1]
    if ranked and ranked[0][0] <= 0:
        return ranked[0][1]
    return None


def build_message(bus):
    acct = ensure_account_state(bus)
    rules = ensure_recovery_rules(bus)
    candidate = best_candidate(bus)
    positions = open_positions(bus)
    close_pick = weakest_position(positions, float(rules.get('rule_min_pnl_to_consider_close', -5.0)))
    council = ensure_council(bus)

    used = float(acct.get('used_margin') or 0)
    max_margin = float(acct.get('max_margin') or 250.0)
    remaining = max_margin - used
    acct['remaining_margin'] = round(remaining, 2)
    acct['can_take_new_trade'] = remaining > 0
    acct['updated_at'] = now_iso()

    if not candidate:
        council.update({
            'headline': 'PropOne: WAIT — no prop setup.',
            'reason': 'No RTS or Gimba setup is strong enough for conservative PropOne right now.',
            'trigger_source': 'NONE',
            'replacement_candidates': [],
            'updated_at': now_iso(),
        })
        return council

    pair = str(candidate.get('pair') or 'UNKNOWN')
    engine = str(candidate.get('engine') or 'UNKNOWN')

    if acct.get('can_take_new_trade', True):
        council.update({
            'headline': f'PropOne: CLICK {pair} — PROP 1 CONS.',
            'reason': f'{engine} is the strongest prop setup and still fits conservative percentage sizing.',
            'trigger_source': engine,
            'replacement_candidates': [
                {
                    'open_pair': pair,
                    'close_pair': None,
                    'engine': engine,
                    'score': round(signal_score(candidate), 3),
                }
            ],
            'updated_at': now_iso(),
        })
        return council

    if close_pick:
        close_pair = str(close_pick.get('pair') or 'UNKNOWN')
        council.update({
            'headline': f'PropOne: REPLACE {close_pair} with {pair} — PROP 1 CONS.',
            'reason': f'Flat or weaker exposure gets replaced because {engine} has the stronger structure and momentum setup.',
            'trigger_source': engine,
            'replacement_candidates': [
                {
                    'close_pair': close_pair,
                    'open_pair': pair,
                    'engine': engine,
                    'close_unrealized_pnl': float(close_pick.get('unrealized_pnl') or close_pick.get('pnl') or 0),
                    'score': round(signal_score(candidate), 3),
                }
            ],
            'updated_at': now_iso(),
        })
        return council

    council.update({
        'headline': 'PropOne: WAIT — no clean replacement.',
        'reason': f'{engine} is interesting, but there is no flat or negative PropOne position ready to cut yet.',
        'trigger_source': engine,
        'replacement_candidates': [],
        'updated_at': now_iso(),
    })
    return council


def main():
    bus = load_bus(BUS_PATH)
    ensure_account_state(bus)
    ensure_recovery_rules(bus)
    council = build_message(bus)
    save_bus(BUS_PATH, bus)
    print(json.dumps(council, indent=2))


if __name__ == '__main__':
    main()
