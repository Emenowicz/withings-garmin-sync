#!/usr/bin/env python3
"""Withings -> Garmin Connect weight sync.

  ./sync.py auth       one-time OAuth bootstrap (browser)
  ./sync.py            push new weights (run from launchd)
  ./sync.py selftest   asserts, no network
"""

import fcntl
import json
import os
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


def extract_code(pasted):
    """Accept a bare code or the whole redirect URL pasted from the address bar."""
    query = parse_qs(urlparse(pasted.strip()).query)
    return query["code"][0] if "code" in query else pasted.strip()


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
    """measuregrps -> ([(epoch, kg)] sorted, cursor). Pure; see selftest."""
    weights, cursor = [], 0
    for g in groups:
        cursor = max(cursor, g.get("modified") or g["date"])
        if g.get("category") != 1:
            continue  # category 2 is a weight *goal*, not a measurement
        for m in g["measures"]:
            if m["type"] == 1:
                weights.append((g["date"], round(m["value"] * 10 ** m["unit"], 2)))
    return sorted(weights), cursor


def new_weights(state):
    body = unwrap(
        requests.get(
            f"{API}/measure",
            params={
                "action": "getmeas",
                "meastype": 1,
                "category": 1,
                "lastupdate": state["lastupdate"],
            },
            headers={"Authorization": "Bearer " + state["access_token"]},
            timeout=30,
        )
    )
    return parse_groups(body["measuregrps"])


def push(weights):
    from garminconnect import Garmin

    garmin = Garmin(cfg("GARMIN_EMAIL"), cfg("GARMIN_PASSWORD"),
                    prompt_mfa=lambda: input("Garmin MFA code: "))
    garmin.login(GARMIN_TOKENS)
    for epoch, kg in weights:
        stamp = datetime.fromtimestamp(epoch)
        garmin.add_weigh_in(kg, timestamp=stamp.isoformat())
        print(f"{stamp:%Y-%m-%d %H:%M}  {kg} kg -> garmin")


def sync():
    # ponytail: whole-run lock. Two syncs at once race the refresh-token rotation and
    # Withings kills the chain, forcing a manual re-auth.
    fcntl.flock(open(HERE / ".lock", "w"), fcntl.LOCK_EX)
    if not STATE.exists():
        sys.exit(f"no {STATE} — run `{sys.argv[0]} auth` first")
    state = refresh(json.loads(STATE.read_text()))
    weights, cursor = new_weights(state)
    if weights:
        push(weights)
    else:
        print("no new weights")
    if cursor:
        state["lastupdate"] = cursor + 1
        save(state)


def auth(pasted=None):
    redirect = redirect_uri()
    url = "https://account.withings.com/oauth2_user/authorize2?" + urlencode(
        {
            "response_type": "code",
            "client_id": cfg("WITHINGS_CLIENT_ID"),
            "scope": "user.metrics",
            "redirect_uri": redirect,
            "state": "weight-sync",
        }
    )
    # Withings rejects localhost callbacks for some app types. With a public https
    # callback we can't catch the redirect, so the code comes back as an argument.
    if pasted:
        code = extract_code(pasted)
    else:
        print(f"authorize here:\n{url}\n")
        webbrowser.open(url)
        if urlparse(redirect).hostname not in ("localhost", "127.0.0.1"):
            sys.exit(f"then, within ~30s:\n  {sys.argv[0]} auth '<the redirect URL>'")
        code = catch_code()

    state = token(
        grant_type="authorization_code", code=code, redirect_uri=redirect
    )
    # ponytail: start from now, not 0 — otherwise the first sync replays your whole
    # scale history into Garmin. Backfill by lowering lastupdate in state.json.
    state["lastupdate"] = int(time.time())
    save(state)
    print(f"wrote {STATE}")


def catch_code():
    query = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query.update(parse_qs(urlparse(self.path).query))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok, close this tab")

        def log_message(self, *_):
            pass

    with HTTPServer(("localhost", 8080), Handler) as server:
        while not query:
            server.handle_request()
    if "code" not in query:
        sys.exit(f"no code in callback: {query}")
    return query["code"][0]


def selftest():
    groups = [
        {"date": 200, "modified": 900, "category": 1,
         "measures": [{"type": 1, "value": 7563, "unit": -2},
                      {"type": 6, "value": 210, "unit": -1}]},
        {"date": 100, "modified": 500, "category": 1,
         "measures": [{"type": 1, "value": 76, "unit": 0}]},
        {"date": 300, "modified": 1200, "category": 2,
         "measures": [{"type": 1, "value": 7000, "unit": -2}]},
    ]
    weights, cursor = parse_groups(groups)
    assert weights == [(100, 76.0), (200, 75.63)], weights  # sorted, fat% ignored
    assert cursor == 1200, cursor  # max modified, incl. groups we skipped
    assert parse_groups([{"date": 5, "category": 1, "measures": []}]) == ([], 5)

    assert extract_code("https://x.dev/cb?code=abc123&state=weight-sync") == "abc123"
    assert extract_code("  abc123 ") == "abc123"

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
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        auth(*sys.argv[2:3])
    elif cmd == "selftest":
        selftest()
    else:
        sync()
