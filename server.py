from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

BUS_PATH = Path("/app/data/signal_bus.json") if Path("/app/data").exists() else Path(__file__).resolve().parent / "signal_bus.json"
LAST_GOOD_PATH = Path("/app/data/last_good_signal_bus.json") if Path("/app/data").exists() else Path(__file__).resolve().parent / "last_good_signal_bus.json"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("server")

app = FastAPI(title="Oracle Panel API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_payload() -> tuple[Dict[str, Any], str]:
    try:
        payload = _read_json(BUS_PATH)
        return payload, "bus"
    except Exception as e:
        logger.warning("Primary bus read failed: %s", e)
        try:
            payload = _read_json(LAST_GOOD_PATH)
            return payload, "last_good"
        except Exception as e2:
            logger.error("Fallback last-good read failed: %s", e2)
            raise HTTPException(status_code=503, detail="No Oracle payload available")


def _annotate_payload(payload: Dict[str, Any], source: str) -> Dict[str, Any]:
    payload.setdefault("api_source", source)
    payload.setdefault("api_served_at", datetime.utcnow().isoformat() + "Z")
    return payload


@app.get("/api/panel")
async def get_panel() -> JSONResponse:
    """Serve the latest Oracle panel payload with a last-good fallback."""
    payload, source = _load_payload()
    payload = _annotate_payload(payload, source)
    return JSONResponse(payload)


@app.get("/api/signals")
async def get_signals_compat() -> JSONResponse:
    """Compatibility alias for older clients; returns the same Oracle panel payload."""
    payload, source = _load_payload()
    payload = _annotate_payload(payload, source)
    return JSONResponse(payload)


@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "oracle-panel-api",
        "bus_path": str(BUS_PATH),
        "last_good_path": str(LAST_GOOD_PATH),
        "primary_endpoint": "/api/panel",
        "compat_endpoint": "/api/signals",
    }
