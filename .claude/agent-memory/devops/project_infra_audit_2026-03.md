---
name: Infrastructure Audit Findings March 2026
description: Key gaps and risks found during full infra audit of deploy/, Dockerfile, docker-compose.yml
type: project
---

Critical gap: main.py does NOT write a heartbeat file. The monitoring script (deploy/monitoring.py) expects /app/data/heartbeat to exist and be updated every loop iteration, but no write call exists in main.py. This means the HEARTBEAT STALE alert will always fire from minute one.

**Why:** Heartbeat check was implemented in monitoring before the corresponding write was added to main.py.

**How to apply:** When user asks about monitoring false alerts, this is the root cause. The fix is a one-liner in the main trading loop: `Path("/app/data/heartbeat").write_text(str(time.time()))` after each successful iteration.

Backup stores .env in cleartext inside tar.gz on local disk at /opt/trading-bot/backups/. This contains live API keys. Permissions on the .env file within the archive are 600 but the tar.gz itself has default umask permissions. Risk: if VPS is compromised, attacker gets API keys from backup.

Docker compose: dashboard service uses `depends_on: bot` but this only waits for container start, not for the bot to be healthy. Dashboard will come up before bot data is ready on first boot — acceptable for this use case.

No auto-restart of Docker on VPS reboot: docker.service is enabled, and restart: unless-stopped handles container restart after Docker itself restarts, so VPS reboot recovery is covered.

Monitor state file at /tmp/ — this is wiped on VPS reboot, meaning all alert dedup state is lost. After a reboot, you'll get a flood of alerts if bot was previously down. Low severity but worth noting.

No disk space monitoring — SQLite and Docker logs could fill the disk on a $7 CX21 with small disk (20GB). json-file logging is capped at 10m×5=50MB for bot, 10m×3=30MB for dashboard, which is fine. But no alert if disk goes above 80%.

setup.sh certbot email is hardcoded as admin@${DOMAIN} — this will receive expiry warnings. Fine for now.

The nginx /static location bypasses auth_basic — this is intentional for Streamlit to load CSS/JS but means static Streamlit assets are publicly accessible. Not a data leak risk.
