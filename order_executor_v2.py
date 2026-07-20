from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("order_executor_v2")

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "config.json"
KRAKEN_API_BASE = "https://api.kraken.com"
ADD_ORDER_PATH = "/0/private/AddOrder"
OPEN_POSITIONS_PATH = "/0/private/OpenPositions"
BALANCE_PATH = "/0/private/Balance"
REQUEST_TIMEOUT = 15

PAIR_MAP: Dict[str, str] = {
    "BTC": "XBTUSD", "XBT": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "ADA": "ADAUSD",
    "XRP": "XRPUSD", "AVAX": "AVAXUSD", "LINK": "LINKUSD", "DOT": "DOTUSD", "MATIC": "MATICUSD",
    "AAVE": "AAVEUSD", "LTC": "LTCUSD", "DOGE": "DOGEUSD", "ATOM": "ATOMUSD", "UNI": "UNIUSD",
    "ARB": "ARBUSD", "OP": "OPUSD", "INJ": "INJUSD", "SUI": "SUIUSD", "APT": "APTUSD",
    "BNB": "BNBUSD", "ONDO": "ONDOUSD", "HYPE": "HYPEUSD", "TAO": "TAOUSD", "TON": "TONUSD",
}

PROP_SEATS: Dict[str, Dict[str, Any]] = {
    "5K": {"risk_per_trade": 25.0, "daily_loss_max": 150.0, "leverage": 2, "max_open": 2, "all_hours": False},
    "10K": {"risk_per_trade": 50.0, "daily_loss_max": 300.0, "leverage": 3, "max_open": 2, "all_hours": False},
    "25KDRAGON": {"risk_per_trade": 150.0, "daily_loss_max": None, "leverage": 3, "max_open": 3, "all_hours": True},
}

LADDER_TP_NORMAL: List[float] = [1.0, 1.5, 2.0, 2.5]
LADDER_TP_SPRINT: List[float] = [0.8, 1.3, 1.8, 2.2]
LADDER_TP_FRACTIONS: List[float] = [0.25, 0.25, 0.25, 0.25]

QUOTE_SUFFIXES = ("USDT", "USD", "USDC", "PERP")


def normalize_pair_base(raw_pair: Any) -> str:
    p = str(raw_pair or "").upper().replace("-", "").replace("/", "").strip()
    for q in QUOTE_SUFFIXES:
        if p.endswith(q) and len(p) > len(q):
            p = p[: -len(q)]
            break
    if p == "BTC":
        p = "XBT"
    return p


class OrderExecutor:
    def __init__(self, config: Optional[Dict[str, Any]] = None, config_path: Optional[Path] = None) -> None:
        cfg = config if config is not None else self.load_config(config_path)
        self.config = cfg
        self.dryrun = bool(cfg.get("dryrun", True))
        self.apikey = str(cfg.get("kraken_api_key", ""))
        self.apisecret = str(cfg.get("kraken_api_secret", ""))
        self.seat = str(cfg.get("prop_seat", "5K"))
        self.seatcfg = dict(PROP_SEATS.get(self.seat, PROP_SEATS["5K"]))
        for k in ("risk_per_trade", "daily_loss_max", "leverage", "max_open"):
            if k in cfg:
                self.seatcfg[k] = cfg[k]
        self.session = requests.Session()
        self.daily_loss = 0.0
        self.daily_loss_date = self.today()
        self.buspath = MODULE_DIR / "signal_bus.json"
        if self.dryrun:
            logger.warning("DRYRUN=True, orders will be SIMULATED, not sent.")
        else:
            logger.warning("DRYRUN=False, LIVE ORDERS ENABLED on seat %s.", self.seat)

    @staticmethod
    def load_config(config_path: Optional[Path]) -> Dict[str, Any]:
        path = config_path or CONFIG_PATH
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"dryrun": True, "prop_seat": "5K"}

    @staticmethod
    def today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def roll_day(self) -> None:
        today = self.today()
        if today != self.daily_loss_date:
            self.daily_loss = 0.0
            self.daily_loss_date = today

    def positionsize(self, entry: float, sl: float) -> float:
        risk_dist = abs(float(entry) - float(sl))
        if risk_dist <= 0:
            return 0.0
        risk = float(self.seatcfg["risk_per_trade"])
        return round(risk / risk_dist, 8)

    def position_size_with_sammy(self, signal: Dict[str, Any]) -> float:
        entry = float(signal["entry"])
        sl = float(signal["sl"])
        base_volume = self.position_size(entry, sl)
        if base_volume <= 0:
            return 0.0

        raw_mult = signal.get("sammy_bonus_multiplier", signal.get("st_ai_multiplier", 1.0))
        try:
            bonus_mult = float(raw_mult)
        except (TypeError, ValueError):
            bonus_mult = 1.0

        bonus_mult = max(1.0, min(2.0, bonus_mult))
        return round(base_volume * bonus_mult, 8)

    def can_trade(self, open_count: int) -> bool:
        self.roll_day()
        if open_count >= int(self.seatcfg["max_open"]):
            return False
        cap = self.seatcfg.get("daily_loss_max")
        if cap is not None and self.daily_loss >= float(cap):
            return False
        return True

    def record_loss(self, amount: float) -> None:
        self.roll_day()
        if amount > 0:
            self.daily_loss += float(amount)

    def place_order(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        raw_pair = str(signal.get("pair", "")).upper()
        pair_base = normalize_pair_base(raw_pair)
        kraken_pair = PAIR_MAP.get(pair_base)
        if kraken_pair is None:
            logger.info("SKIP %s -> %s reason=UNMAPPED_PAIR", raw_pair, pair_base)
            return {"status": "SKIPPED", "reason": "UNMAPPED_PAIR", "pair": raw_pair, "pair_base": pair_base}
        bias = str(signal.get("bias", "")).upper()
        order_type = "buy" if bias == "LONG" else "sell"
        entry = float(signal["entry"])
        sl = float(signal["sl"])
        tp = float(signal.get("tp", 0) or 0)
        volume = self.position_size_with_sammy(signal)
        if volume <= 0:
            logger.info("SKIP %s reason=ZERO_VOLUME entry=%s sl=%s", raw_pair, signal.get("entry"), signal.get("sl"))
            return {"status": "SKIPPED", "reason": "ZERO_VOLUME", "pair": raw_pair}
        payload: Dict[str, Any] = {
            "pair": kraken_pair,
            "type": order_type,
            "ordertype": "limit",
            "price": self.fmt(entry),
            "volume": self.fmt(volume),
            "leverage": str(self.seatcfg["leverage"]),
            "close[ordertype]": "stop-loss",
            "close[price]": self.fmt(sl),
            "userref": self.userref(signal),
        }
        if tp > 0:
            payload["takeprofit"] = self.fmt(tp)
        if self.dryrun:
            logger.info("DRYRUN would AddOrder %s", json.dumps(payload))
            return {"status": "SIMULATED", "dryrun": True, "pair": raw_pair, "pair_base": pair_base, "kraken_pair": kraken_pair, "volume": volume, "payload": payload}
        try:
            resp = self.private_request(ADD_ORDER_PATH, payload)
        except Exception as exc:
            return {"status": "ERROR", "dryrun": False, "pair": raw_pair, "pair_base": pair_base, "error": str(exc)}
        if resp.get("error"):
            return {"status": "ERROR", "dryrun": False, "pair": raw_pair, "pair_base": pair_base, "error": resp["error"]}
        return {"status": "PLACED", "dryrun": False, "pair": raw_pair, "pair_base": pair_base, "kraken_pair": kraken_pair, "volume": volume, "result": resp.get("result"), "payload": payload}

    def get_open_positions(self) -> List[Dict[str, Any]]:
        if self.dryrun:
            return []
        try:
            resp = self.private_request(OPEN_POSITIONS_PATH, {})
        except Exception:
            return []
        if resp.get("error"):
            return []
        result = resp.get("result", {}) or {}
        return [{"txid": k, **v} for k, v in result.items()]

    def close_position(self, position: Dict[str, Any]) -> Dict[str, Any]:
        pos_type = str(position.get("type", "")).lower()
        close_type = "sell" if pos_type == "buy" else "buy"
        kraken_pair = position.get("pair") or PAIR_MAP.get(str(position.get("pair_base", "")).upper(), "")
        volume = position.get("vol") or position.get("volume") or 0
        payload = {
            "pair": kraken_pair,
            "type": close_type,
            "ordertype": "market",
            "volume": self.fmt(float(volume)),
            "leverage": str(self.seatcfg["leverage"]),
            "reduce_only": True,
        }
        if self.dryrun:
            logger.info("DRYRUN would close position %s", json.dumps(payload))
            return {"status": "SIMULATED", "dryrun": True, "payload": payload}
        try:
            resp = self.private_request(ADD_ORDER_PATH, payload)
        except Exception as exc:
            return {"status": "ERROR", "dryrun": False, "error": str(exc)}
        if resp.get("error"):
            return {"status": "ERROR", "dryrun": False, "error": resp["error"]}
        return {"status": "CLOSED", "dryrun": False, "result": resp.get("result")}

    @staticmethod
    def ladder_levels(entry: float, sl: float, bias: str, sprint_mode: bool = False) -> List[float]:
        r_dist = abs(entry - sl)
        multiples = LADDER_TP_SPRINT if sprint_mode else LADDER_TP_NORMAL
        sign = 1 if str(bias).upper() == "LONG" else -1
        return [entry + sign * r_dist * m for m in multiples]

    def manage_ladder_tp(self, position_id: str, current_price: float, entry: float, sl: float, sprint_mode: bool = False, bias: str = "LONG") -> Dict[str, Any]:
        levels = self.ladder_levels(entry, sl, bias, sprint_mode)
        bus = self.load_bus(self.buspath)
        open_positions = bus.get("open_positions", [])
        pos_record = next((p for p in open_positions if str(p.get("position_id")) == str(position_id)), None)
        if pos_record is None:
            pos_record = {"position_id": position_id, "entry": entry, "sl": sl, "bias": bias, "sprint_mode": sprint_mode, "tps_hit": [False, False, False, False]}
            open_positions.append(pos_record)
        tps_hit = pos_record.setdefault("tps_hit", [False, False, False, False])
        is_long = str(bias).upper() == "LONG"
        newly_hit: List[int] = []
        closes: List[Dict[str, Any]] = []
        for i, level in enumerate(levels):
            if tps_hit[i]:
                continue
            hit = current_price >= level if is_long else current_price <= level
            if hit:
                tps_hit[i] = True
                newly_hit.append(i + 1)
                fraction = LADDER_TP_FRACTIONS[i]
                closes.append({"status": "SIMULATED" if self.dryrun else "PLACED", "dryrun": self.dryrun, "position_id": position_id, "tp_leg": i + 1, "tp_price": level, "fraction_closed": fraction})
        pos_record["tps_hit"] = tps_hit
        pos_record["ladder_levels"] = levels
        pos_record["fully_closed"] = all(tps_hit)
        bus["open_positions"] = open_positions
        self.save_bus(self.buspath, bus)
        return {"position_id": position_id, "levels": levels, "newly_hit": newly_hit, "closes": closes}

    @staticmethod
    def load_bus(buspath: Path) -> Dict[str, Any]:
        try:
            return json.loads(buspath.read_text())
        except (OSError, json.JSONDecodeError):
            return {"open_positions": []}

    @staticmethod
    def save_bus(buspath: Path, bus: Dict[str, Any]) -> None:
        tmp_path = buspath.with_suffix(".tmp.json")
        tmp_path.write_text(json.dumps(bus, indent=2, default=str))
        tmp_path.replace(buspath)

    def get_balance(self) -> Dict[str, Any]:
        if self.dryrun:
            return {}
        try:
            resp = self.private_request(BALANCE_PATH, {})
        except Exception:
            return {}
        if resp.get("error"):
            return {}
        return resp.get("result", {}) or {}

    def private_request(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.apikey or not self.apisecret or "YOUR_API" in self.apisecret:
            raise RuntimeError("Kraken API credentials not configured")
        post = dict(data)
        post["nonce"] = str(int(time.time() * 1000))
        headers = {
            "API-Key": self.apikey,
            "API-Sign": self.sign(path, post),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = self.session.post(KRAKEN_API_BASE + path, data=post, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def sign(self, path: str, data: Dict[str, Any]) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = path.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.apisecret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    @staticmethod
    def fmt(v: float) -> str:
        return f"{float(v):.8f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def userref(signal: Dict[str, Any]) -> int:
        base = f"{signal.get('pair')}-{signal.get('engine')}"
        return int(hashlib.sha256(base.encode()).hexdigest(), 16) % (2**31)
