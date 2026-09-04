# withings-garmin-sync

Pulls new weight and body-composition measurements from Withings and posts them to Garmin Connect.
Runs daily at 09:00 via launchd (edit `StartCalendarInterval` in the plist to change).

## Setup

1. **Install dependencies.**

   ```sh
   sh setup.sh
   ```

   This creates `.env` from the included example if needed.

2. **Withings credentials.** Register an OAuth app at
   https://account.withings.com/partner/add_oauth2 and put its three `WITHINGS_*` values
   in `.env`. Note Withings rejects `localhost` callbacks (it HEAD-checks the URL on
   save), so a registration of your own needs a public HTTPS URL; `sync.py auth` handles
   both that and a localhost callback.

3. **Garmin credentials.** Put `GARMIN_EMAIL` / `GARMIN_PASSWORD` in `.env`
   (`chmod 600`). Garmin has no official write API — `garminconnect` logs in like the
   mobile app. Unofficial, so Garmin can break it whenever they like.

4. **Authorize Withings:**

   ```sh
   .venv/bin/python sync.py auth                  # prints the authorize URL
   .venv/bin/python sync.py auth '<redirect URL>' # paste it back within ~30s
   ```

5. **First sync must run in a real terminal** — Garmin asks for an emailed MFA code and
   there's no way to type it from a non-interactive shell:

   ```sh
   .venv/bin/python sync.py
   ```

   Tokens then cache in `~/.garminconnect` and every later run is unattended.
   Repeated login attempts get you a 429 IP rate limit from Garmin; wait it out.

6. **Schedule it:**

   ```sh
   sh setup.sh --launchd
   tail -f sync.log
   ```

## Notes

- `state.json` holds the Withings tokens and the `lastupdate` cursor — that's the
  whole database. `auth` starts the cursor at *now*; to backfill, lower `lastupdate`
  to an epoch timestamp and run the sync. All result pages are fetched automatically.
- Successfully uploaded measurement IDs are checkpointed in `state.json`, so a partial
  Garmin failure can resume without replaying earlier entries.
- Withings rotates refresh tokens on every use. If `state.json` is lost or clobbered,
  run `sync.py auth` again.
- `sync.py selftest` checks the parsing and token-persistence logic offline.
- When the scale provides them, Garmin receives body-fat percentage, hydration percentage,
  muscle mass, bone mass, visceral-fat rating, BMR, and metabolic age.

## License

[MIT](LICENSE)
