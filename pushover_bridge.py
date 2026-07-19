import os
import time
import json
import pathlib
import datetime
import requests

# --- CONFIG -------------------------------------------------------------

# Source worker
SOURCE_URL = "https://giving-wisdom-production-9b27.up.railway.app/api/signals"
SOURCE_SECRET = "test123"  # same secret you used in the worker env

# Event filter – adjust if you want more than signals for PropOne
EVENT_TYPE = "signal"          # or "order", "status", or leave None for all
EVENT_ACCOUNT = "PropOne"      # matches your source-worker routes

# Pushover credentials (from your env file)
PUSHOVER_USER_KEY = "u4v2rgci4vm95ezqx4czssz2t2du6a"
PUSHOVER_API_TOKEN = "a144kiwuifpzpjmbpjfei63dvyqfuu"

# Polling interval in seconds
POLL_INTERVAL = 15  # tweak later once you see how it feels

# Where we store the last timestamp we’ve seen
STATE_FILE = pathlib.Path("pushover_bridge_state.json")


# --- STATE HANDLING -----------------------------------------------------

def load_state():
    """Load last_since timestamp from disk (if present)."""
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("since") or ""
        except Exception:
            return ""
    return ""


def save_state(since_ts: str):
    """Persist last_since timestamp to disk."""
    try:
        with STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump({"since": since_ts}, f)
    except Exception:
        # If we can't write state, we still keep running — just may resend some signals
        pass


# --- SOURCE WORKER POLL -------------------------------------------------

def fetch_events(since: str):
    """
    Call /events on the source worker and return (events, newest_ts).
    """
    url = f"{SOURCE_URL}/events"
    params = {
        "limit": "50"
    }
    if since:
        params["since"] = since
    if EVENT_TYPE:
        params["type"] = EVENT_TYPE
    if EVENT_ACCOUNT:
        params["account"] = EVENT_ACCOUNT

    headers = {
        "x-source-secret": SOURCE_SECRET
    }

    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()

    try:
        data = resp.json()
    except Exception:
        data = {}

    events = data.get("events") or []
    newest_ts = since

    for ev in events:
        ts = ev.get("ts")
        if isinstance(ts, str):
            if not newest_ts or ts > newest_ts:
                newest_ts = ts

    return events, newest_ts


# --- Pushover SEND ------------------------------------------------------

def send_pushover(title: str, message: str, priority: int = 0):
    """
    Send a single Pushover notification.
    """
    payload = {
        "token": PUSHOVER_API_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title,
        "message": message,
        "priority": str(priority),
    }

    resp = requests.post(
        "https://api.pushover.net/1/messages.json",
        data=payload,
        timeout=10
    )
    resp.raise_for_status()


def format_event_for_pushover(ev: dict) -> tuple[str, str]:
    """
    Turn a source-worker event into (title, message) for Pushover.

    Adjust this mapping once you see the exact event structure
    coming out of /events.
    """
    pair = ev.get("pair") or ev.get("symbol") or "UNKNOWN"
    side = ev.get("side") or ev.get("direction") or "-"
    score = ev.get("score") or ev.get("rank") or ""
    reason = ev.get("reason") or ev.get("label") or ""
    ts = ev.get("ts") or ""

    # Title: quick at-a-glance read
    title = f"{pair} {side}".strip()

    # Message: compact council cue
    parts = []
    if score != "":
        parts.append(f"score {score}")
    if reason:
        parts.append(reason)
    if ts:
        parts.append(f"@ {ts}")

    message = " — ".join(parts) if parts else "signal"

    return title, message


# --- MAIN LOOP ----------------------------------------------------------

def main():
    print("Starting Pushover bridge for rtssnipercouncil...")
    last_since = load_state()
    if last_since:
        print(f"Resuming from last_since={last_since}")
    else:
        print("Starting fresh (no last_since state found)")

    while True:
        try:
            events, newest_ts = fetch_events(last_since)

            if events:
                print(f"[{datetime.datetime.utcnow().isoformat()}] "
                      f"Fetched {len(events)} events from source worker")

                for ev in events:
                    # Only send events newer than our last_since to avoid duplicates
                    ts = ev.get("ts") or ""
                    if last_since and isinstance(ts, str) and ts <= last_since:
                        continue

                    title, message = format_event_for_pushover(ev)

                    try:
                        send_pushover(title, message)
                        print(f"  Sent Pushover: {title} :: {message}")
                    except Exception as e:
                        print(f"  ERROR sending Pushover: {e}")

                # Move our cursor forward
                if newest_ts and newest_ts != last_since:
                    last_since = newest_ts
                    save_state(last_since)
                    print(f"Updated last_since to {last_since}")
            else:
                # No new events; stay quiet
                pass

        except Exception as e:
            print(f"ERROR polling source worker: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
