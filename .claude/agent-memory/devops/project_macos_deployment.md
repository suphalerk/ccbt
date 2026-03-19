---
name: macOS launchd deployment
description: CCBT is deployed as macOS launchd services on a local Mac Mini, not just via Docker on a VPS
type: project
---

CCBT runs as persistent background services on a Mac Mini via launchd, in addition to the VPS Docker path.

**Why:** User runs the bot locally on macOS for development/production, not just on a Hetzner VPS.

**How to apply:** When infrastructure work is requested, consider both the Docker/VPS path (`deploy/`) and the macOS launchd path (`deploy/macos/`). The macOS path is the primary runtime for this user.

## Key details

- Python: `/opt/homebrew/bin/python3` (Homebrew) or `/usr/bin/python3` (system) — no venv exists yet
- Streamlit: not installed at time of setup (2026-03-18); scripts handle missing streamlit gracefully
- .env: not present at setup time; scripts warn but do not block
- Log dir: `~/Library/Logs/ccbt/`
- LaunchAgents: `~/Library/LaunchAgents/`

## Files in deploy/macos/

| File | Purpose |
|------|---------|
| `com.ccbt.trading-bot.plist` | launchd plist — bot, RunAtLoad + KeepAlive |
| `com.ccbt.dashboard.plist` | launchd plist — Streamlit dashboard |
| `com.ccbt.rotate-logs.plist` | launchd timer — daily log rotation at 03:00 |
| `start.sh` | Sources .env, activates venv, runs main.py |
| `start-dashboard.sh` | Sources .env, activates venv, runs streamlit |
| `install.sh` | Creates logs dir, copies plists, loads all 3 services |
| `uninstall.sh` | Unloads and removes all 3 plists |
| `status.sh` | Shows PID/exit status + last 5 log lines |
| `rotate-logs.sh` | Pure-bash log rotation (10 MB threshold, 7 copies gzipped) |
| `ccbt.newsyslog.conf` | Alternative: system newsyslog config (requires sudo) |
| `ngrok_dashboard.sh` | Start dashboard + open ngrok HTTPS tunnel, print public URL |

## ngrok remote access

ngrok v3.36.0 is installed at `/opt/homebrew/bin/ngrok`. Auth token configured (2026-03-19).
`ngrok_dashboard.sh` starts Streamlit if needed, kills any stale ngrok, opens a fresh HTTPS tunnel on port 8501, and prints the public URL. Logs go to `logs/ngrok.log`.

```bash
bash deploy/macos/ngrok_dashboard.sh
# Stop tunnel: pkill -x ngrok
```

## Quick commands

```bash
# Install
bash deploy/macos/install.sh

# Status
bash deploy/macos/status.sh

# Start/stop individual service
launchctl start com.ccbt.trading-bot
launchctl stop com.ccbt.trading-bot

# Follow logs
tail -f ~/Library/Logs/ccbt/bot.log
```
