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

app = FastAPI(title="Oracle Signal API", version="1.0.0")

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


@app.get("/api/signals")
async def get_signals() -> JSONResponse:
    """Serve the latest Oracle payload with a last-good fallback.

    Primary source: BUS_PATH written by scheduler/OracleRunner.
    Fallback: LAST_GOOD_PATH if the primary read fails.
    """
    try:
        payload = _read_json(BUS_PATH)
        source = "bus"
    except Exception as e:
        logger.warning("Primary bus read failed: %s", e)
        try:
            payload = _read_json(LAST_GOOD_PATH)
            source = "last_good"
        except Exception as e2:
            logger.error("Fallback last-good read failed: %s", e2)
            raise HTTPException(status_code=503, detail="No signal payload available")

    payload.setdefault("api_source", source)
    payload.setdefault("api_served_at", datetime.utcnow().isoformat() + "Z")

    return JSONResponse(payload)


@app.get("/")
async def root() -> Dict[str, Any]:
    return {"status": "ok", "bus_path": str(BUS_PATH), "last_good_path": str(LAST_GOOD_PATH)}
