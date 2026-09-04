#!/usr/bin/env python3
"""Withings -> Garmin Connect body-composition sync.

  ./sync.py auth       one-time OAuth bootstrap (browser)
  ./sync.py            push new weights (run from launchd)
  ./sync.py selftest   asserts, no network
"""

import fcntl
import json
import os
import secrets
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
AUTH_STATE = HERE / ".auth-state"
ENV = HERE / ".env"
API = "https://wbsapi.withings.net"
GARMIN_TOKENS = os.path.expanduser("~/.garminconnect")

# ponytail: 3-line .env parser instead of python-dotenv. launchd runs with an empty
# environment, so shell exports are invisible to the scheduled job.
_env = {}
if ENV.exists():
    _env = {
        k.strip(): v.strip()
        for k, v in (l.split("=", 1) for l in ENV.read_text().splitlines() if "=" in l)
        if not k.strip().startswith("#")
    }


def cfg(key, default=None):
    val = os.environ.get(key) or _env.get(key) or default
    if not val:
        sys.exit(f"missing {key}: put it in {ENV} or the environment")
    return val


def redirect_uri():
    """Must match the callback registered in the Withings dashboard, exactly."""
    return cfg("WITHINGS_REDIRECT", "http://localhost:8080")


def extract_code(pasted, expected_state):
    """Validate an OAuth redirect URL and return its authorization code."""
    query = parse_qs(urlparse(pasted.strip()).query)
    if query.get("state") != [expected_state]:
        sys.exit("invalid OAuth state — run auth again and paste the whole redirect URL")
    if "code" not in query:
        sys.exit(f"no code in OAuth redirect: {query}")
    return query["code"][0]


def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.chmod(0o600)
    os.replace(tmp, STATE)


def unwrap(response):
    response.raise_for_status()
    body = response.json()
    if body.get("status"):
        sys.exit(f"withings error {body['status']}: {body.get('error')}")
    return body["body"]


def token(**params):
    body = unwrap(
        requests.post(
            f"{API}/v2/oauth2",
            data={
                "action": "requesttoken",
                "client_id": cfg("WITHINGS_CLIENT_ID"),
                "client_secret": cfg("WITHINGS_SECRET"),
                **params,
            },
            timeout=30,
        )
    )
    return {"access_token": body["access_token"], "refresh_token": body["refresh_token"]}


def refresh(state):
    """Withings rotates refresh tokens: persist the new one before anything else can fail."""
    state.update(token(grant_type="refresh_token", refresh_token=state["refresh_token"]))
    save(state)
    return state


def parse_groups(groups):
    """measuregrps -> ([(epoch, id, Garmin fields)] sorted, cursor)."""
    readings, cursor = [], 0
    for g in groups:
        modified = g.get("modified") or g["date"]
        cursor = max(cursor, modified)
        if g.get("category") != 1:
            continue  # category 2 is a body-composition *goal*, not a measurement
        measures = {m["type"]: m["value"] * 10 ** m["unit"] for m in g["measures"]}
        if 1 not in measures:
            continue  # Garmin requires weight for a body-composition entry
        reading = {"weight": round(measures[1], 2)}
        for measure_type, field in ((6, "percent_fat"), (76, "muscle_mass"),
                                    (88, "bone_mass"), (170, "visceral_fat_rating"),
                                    (226, "basal_met"), (227, "metabolic_age")):
            if measure_type in measures:
                reading[field] = round(measures[measure_type], 2)
        if 77 in measures:
            reading["percent_hydration"] = round(measures[77] / measures[1] * 100, 2)
        readings.append((g["date"], f"{g['grpid']}:{modified}", reading))
    return sorted(readings), cursor


def new_weights(state):
    groups, offsets = [], set()
    params = {
        "action": "getmeas",
        "meastypes": "1,6,76,77,88,170,226,227",
        "category": 1,
        # Re-read the boundary second. This also repairs cursors written by versions
        # that incorrectly stored max(modified) + 1.
        "lastupdate": max(0, state["lastupdate"] - 1),
    }
    while True:
        body = unwrap(
            requests.get(
                f"{API}/measure", params=params,
                headers={"Authorization": "Bearer " + state["access_token"]}, timeout=30,
            )
        )
        groups.extend(body["measuregrps"])
        if not body.get("more"):
            return parse_groups(groups)
        offset = body.get("offset")
        if offset is None or offset in offsets:
            sys.exit("withings returned an invalid pagination offset")
        offsets.add(offset)
        params["offset"] = offset


def push(readings, state):
    from garminconnect import Garmin

    garmin = Garmin(cfg("GARMIN_EMAIL"), cfg("GARMIN_PASSWORD"),
                    prompt_mfa=lambda: input("Garmin MFA code: "))
    garmin.login(GARMIN_TOKENS)
    uploaded = set(state.get("uploaded", []))
    for epoch, reading_id, reading in readings:
        stamp = datetime.fromtimestamp(epoch)
        garmin.add_body_composition(timestamp=stamp.isoformat(), **reading)
        uploaded.add(reading_id)
        state["uploaded"] = sorted(uploaded)
        save(state)  # resume after a partial Garmin failure without replaying successes
        fields = ", ".join(k for k in reading if k != "weight") or "weight"
        print(f"{stamp:%Y-%m-%d %H:%M}  {reading['weight']} kg ({fields}) -> garmin")


def pending_readings(readings, state):
    uploaded = set(state.get("uploaded", []))
    return [reading for reading in readings if reading[1] not in uploaded]


def prune_uploaded(state, cursor):
    state["uploaded"] = [
        key for key in state.get("uploaded", [])
        if int(key.rsplit(":", 1)[1]) >= cursor - 1
    ]


def sync():
    # ponytail: whole-run lock. Two syncs at once race the refresh-token rotation and
    # Withings kills the chain, forcing a manual re-auth.
    with open(HERE / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not STATE.exists():
            sys.exit(f"no {STATE} — run `{sys.argv[0]} auth` first")
        state = refresh(json.loads(STATE.read_text()))
        readings, cursor = new_weights(state)
        readings = pending_readings(readings, state)
        if readings:
            push(readings, state)
        else:
            print("no new weights")
        if cursor:
            state["lastupdate"] = cursor
            prune_uploaded(state, cursor)
            save(state)


def auth(pasted=None):
    redirect = redirect_uri()
    if pasted:
        if not AUTH_STATE.exists():
            sys.exit(f"no pending OAuth flow — run `{sys.argv[0]} auth` first")
        code = extract_code(pasted, AUTH_STATE.read_text().strip())
    else:
        oauth_state = secrets.token_urlsafe(24)
        url = "https://account.withings.com/oauth2_user/authorize2?" + urlencode(
            {
                "response_type": "code",
                "client_id": cfg("WITHINGS_CLIENT_ID"),
                "scope": "user.metrics",
                "redirect_uri": redirect,
                "state": oauth_state,
            }
        )
        local_callback = urlparse(redirect).hostname in ("localhost", "127.0.0.1")
        if not local_callback:
            AUTH_STATE.write_text(oauth_state)
            AUTH_STATE.chmod(0o600)
        print(f"authorize here:\n{url}\n")
        webbrowser.open(url)
        if not local_callback:
            sys.exit(f"then, within ~30s:\n  {sys.argv[0]} auth '<the redirect URL>'")
        code = catch_code(oauth_state, redirect)

    state = token(
        grant_type="authorization_code", code=code, redirect_uri=redirect
    )
    # ponytail: start from now, not 0 — otherwise the first sync replays your whole
    # scale history into Garmin. Backfill by lowering lastupdate in state.json.
    state["lastupdate"] = int(time.time())
    save(state)
    AUTH_STATE.unlink(missing_ok=True)
    print(f"wrote {STATE}")


def callback_address(redirect):
    parsed = urlparse(redirect)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        sys.exit("local Withings callback must use http://localhost or http://127.0.0.1")
    return parsed.hostname, parsed.port or 80, parsed.path or "/"


def catch_code(expected_state, redirect):
    query = {}
    host, port, expected_path = callback_address(redirect)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlparse(self.path)
            if request.path != expected_path:
                self.send_error(404)
                return
            query.update(parse_qs(request.query))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok, close this tab")

        def log_message(self, *_):
            pass

    with HTTPServer((host, port), Handler) as server:
        while not query:
            server.handle_request()
    if "code" not in query:
        sys.exit(f"no code in callback: {query}")
    if query.get("state") != [expected_state]:
        sys.exit("invalid OAuth state")
    return query["code"][0]


def selftest():
    groups = [
        {"grpid": 20, "date": 200, "modified": 900, "category": 1,
         "measures": [{"type": 1, "value": 7563, "unit": -2},
                      {"type": 6, "value": 210, "unit": -1},
                      {"type": 76, "value": 6000, "unit": -2},
                      {"type": 77, "value": 4200, "unit": -2},
                      {"type": 88, "value": 320, "unit": -2},
                      {"type": 170, "value": 8, "unit": 0},
                      {"type": 226, "value": 1800, "unit": 0},
                      {"type": 227, "value": 35, "unit": 0}]},
        {"grpid": 10, "date": 100, "modified": 500, "category": 1,
         "measures": [{"type": 1, "value": 76, "unit": 0}]},
        {"grpid": 30, "date": 300, "modified": 1200, "category": 2,
         "measures": [{"type": 1, "value": 7000, "unit": -2}]},
    ]
    readings, cursor = parse_groups(groups)
    assert readings == [
        (100, "10:500", {"weight": 76.0}),
        (200, "20:900", {"weight": 75.63, "percent_fat": 21.0, "muscle_mass": 60.0,
               "percent_hydration": 55.53, "bone_mass": 3.2,
               "visceral_fat_rating": 8, "basal_met": 1800, "metabolic_age": 35}),
    ], readings
    assert cursor == 1200, cursor  # max modified, incl. groups we skipped
    assert parse_groups([{"grpid": 1, "date": 5, "category": 1,
                          "measures": []}]) == ([], 5)
    assert pending_readings(readings, {"uploaded": ["10:500"]}) == [readings[1]]
    ledger = {"uploaded": ["10:500", "20:900"]}
    prune_uploaded(ledger, 900)
    assert ledger == {"uploaded": ["20:900"]}

    assert extract_code("https://x.dev/cb?code=abc123&state=random", "random") == "abc123"
    assert callback_address("http://localhost:8765/oauth/callback") == (
        "localhost", 8765, "/oauth/callback"
    )
    assert callback_address("http://127.0.0.1") == ("127.0.0.1", 80, "/")
    try:
        extract_code("https://x.dev/cb?code=abc123&state=wrong", "random")
        assert False, "mismatched OAuth state accepted"
    except SystemExit:
        pass

    global STATE
    STATE = HERE / "state.selftest.json"
    try:
        save({"refresh_token": "old", "lastupdate": 1})
        state = json.loads(STATE.read_text())
        state.update({"refresh_token": "rotated"})
        save(state)
        assert json.loads(STATE.read_text()) == {"refresh_token": "rotated", "lastupdate": 1}
        assert STATE.stat().st_mode & 0o777 == 0o600
    finally:
        STATE.unlink(missing_ok=True)
    print("selftest ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args == ["sync"]:
        sync()
    elif args[0] == "auth" and len(args) <= 2:
        auth(*sys.argv[2:3])
    elif args == ["selftest"]:
        selftest()
    else:
        sys.exit(f"usage: {sys.argv[0]} [sync | auth [redirect-url] | selftest]")
