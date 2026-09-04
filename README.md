# withings-garmin-sync

[![CI](https://github.com/Emenowicz/withings-garmin-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/Emenowicz/withings-garmin-sync/actions/workflows/ci.yml)

Pulls new weight and body-composition measurements from Withings and posts them to Garmin Connect.
Runs daily at 09:00 via launchd (edit `StartCalendarInterval` in the plist to change).

## Quick start

Requirements: macOS and Python 3.9 or newer.

1. Download the project:

   ```sh
   git clone https://github.com/Emenowicz/withings-garmin-sync.git
   cd withings-garmin-sync
   ```

2. Register an OAuth app at https://account.withings.com/partner/add_oauth2. Keep its
   client ID, client secret, and exact redirect URL ready. If the dashboard rejects a
   localhost callback, use a public HTTPS URL you control.

3. Run the guided setup:

   ```sh
   sh setup.sh --launchd
   ```

   The wizard installs dependencies, asks for Withings and Garmin credentials, opens
   Withings authorization, performs the first Garmin login (including MFA), tests a sync,
   and installs the daily 09:00 job. Existing values are kept when you press Enter.

4. Confirm the installation:

   ```sh
   .venv/bin/python sync.py doctor
   tail -f sync.log
   ```

Run `sh setup.sh` without `--launchd` if you want to sync manually. The wizard stores
credentials in a private `.env` file and caches Garmin login tokens in
`~/.garminconnect`. Garmin has no official write API, so the integration can break when
Garmin changes its private endpoints. Repeated login attempts may cause a temporary 429
rate limit.

## Commands

```text
.venv/bin/python sync.py sync         synchronize new measurements
.venv/bin/python sync.py auth         authorize Withings again
.venv/bin/python sync.py garmin-auth  authenticate Garmin again
.venv/bin/python sync.py configure    update credentials interactively
.venv/bin/python sync.py doctor       check configuration and authorization
.venv/bin/python sync.py selftest     run offline checks
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
