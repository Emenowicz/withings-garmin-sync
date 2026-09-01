# weight-sync

Pulls new weight measurements from Withings and posts them to Garmin Connect.
Runs daily at 09:00 via launchd (edit `StartCalendarInterval` in the plist to change). Weight only, no body composition.

## Setup

1. **Withings credentials.** `.env` ships with `withings-sync`'s published app
   credentials, so no registration is needed. They're shared across everyone using that
   project — if Withings rate-limits them or the secret is rotated, register your own at
   https://account.withings.com/partner/add_oauth2 and replace the three `WITHINGS_*`
   values. Note Withings rejects `localhost` callbacks (it HEAD-checks the URL on save),
   so a registration of your own needs a public HTTPS URL; `sync.py auth` handles both
   that and a localhost callback.

2. **Garmin credentials.** Put `GARMIN_EMAIL` / `GARMIN_PASSWORD` in `.env`
   (`chmod 600`). Garmin has no official write API — `garminconnect` logs in like the
   mobile app. Unofficial, so Garmin can break it whenever they like.

3. **Install and authorize:**

   ```sh
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python sync.py auth                  # prints the authorize URL
   .venv/bin/python sync.py auth '<redirect URL>' # paste it back within ~30s
   ```

4. **First sync must run in a real terminal** — Garmin asks for an emailed MFA code and
   there's no way to type it from a non-interactive shell:

   ```sh
   .venv/bin/python sync.py
   ```

   Tokens then cache in `~/.garminconnect` and every later run is unattended.
   Repeated login attempts get you a 429 IP rate limit from Garmin; wait it out.

5. **Schedule it:**

   ```sh
   cp com.local.weight-sync.plist.example ~/Library/LaunchAgents/com.local.weight-sync.plist
   # Replace /Users/YOU/path/to/weight-sync in the copied file with the project path.
   launchctl load -w ~/Library/LaunchAgents/com.local.weight-sync.plist
   tail -f sync.log
   ```

## Notes

- `state.json` holds the Withings tokens and the `lastupdate` cursor — that's the
  whole database. `auth` starts the cursor at *now*; to backfill, lower `lastupdate`
  to an epoch timestamp and run the sync. Each run fetches one page (~200 groups) — for
  years of history, lower `lastupdate` in steps rather than all at once.
- Withings rotates refresh tokens on every use. If `state.json` is lost or clobbered,
  run `sync.py auth` again.
- `sync.py selftest` checks the parsing and token-persistence logic offline.

## License

[MIT](LICENSE)
