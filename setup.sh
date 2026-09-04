#!/bin/sh
# Install dependencies. Add --launchd to install the daily macOS job too.
set -eu

case "${1:-}" in
  "") install_launchd=false ;;
  --launchd) install_launchd=true ;;
  -h|--help) echo "usage: sh setup.sh [--launchd]"; exit 0 ;;
  *) echo "usage: sh setup.sh [--launchd]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")"
project_dir=$PWD
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

if [ ! -e .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env. Fill in your Withings and Garmin credentials before authorizing."
fi

if "$install_launchd"; then
  case "$(uname)" in
    Darwin) ;;
    *) echo "--launchd is supported only on macOS" >&2; exit 1 ;;
  esac
  launchctl bootout "gui/$(id -u)/com.local.weight-sync" 2>/dev/null || true
  agent="$HOME/Library/LaunchAgents/com.local.withings-garmin-sync.plist"
  mkdir -p "$(dirname "$agent")"
  sed "s|/Users/YOU/path/to/withings-garmin-sync|$project_dir|g" \
    com.local.withings-garmin-sync.plist.example > "$agent"
  plutil -lint "$agent"
  launchctl bootout "gui/$(id -u)" "$agent" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$agent"
  echo "Installed daily launchd job: $agent"
fi

echo "Next: edit .env, then run .venv/bin/python sync.py auth"
