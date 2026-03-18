#!/usr/bin/env bash
# =============================================================================
# Backup Script for Crypto Trading Bot
#
# What it backs up:
#   - SQLite database (trades.db) using sqlite3 .backup for consistency
#   - Configuration files (config.json, .env)
#   - Bot logs
#
# Retention: keeps last 30 days of backups
# Optional: push to S3 or remote server via rsync
#
# Usage:
#   ./deploy/backup.sh                    # Local backup only
#   ./deploy/backup.sh --s3               # Local + push to S3
#   ./deploy/backup.sh --remote user@host # Local + rsync to remote
#
# Environment variables:
#   BOT_DATA_DIR     - Bot data directory (default: /app/data)
#   BOT_APP_DIR      - Bot application directory (default: /opt/trading-bot/app)
#   BACKUP_DIR       - Where to store backups (default: /opt/trading-bot/backups)
#   BACKUP_RETENTION - Days to keep backups (default: 30)
#   S3_BUCKET        - S3 bucket name for remote backups (e.g., s3://my-bot-backups)
#   REMOTE_BACKUP    - Remote destination for rsync (e.g., user@host:/backups)
#
# Can be run as a cron job or systemd timer.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BOT_DATA_DIR="${BOT_DATA_DIR:-/app/data}"
BOT_APP_DIR="${BOT_APP_DIR:-/opt/trading-bot/app}"
BACKUP_DIR="${BACKUP_DIR:-/opt/trading-bot/backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-30}"

# Timestamp for this backup
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_NAME="tradingbot_backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

# Logging
log_info()  { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] [INFO]  $1"; }
log_error() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] [ERROR] $1" >&2; }
log_warn()  { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] [WARN]  $1"; }

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
log_info "Starting backup: ${BACKUP_NAME}"

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"
mkdir -p "${BACKUP_PATH}"

# ---------------------------------------------------------------------------
# Step 1: Back up SQLite database
# ---------------------------------------------------------------------------
log_info "Step 1/4: Backing up SQLite database..."

# Find the database file (check Docker volume or local path)
DB_FILE=""
if [ -f "${BOT_DATA_DIR}/trades.db" ]; then
    DB_FILE="${BOT_DATA_DIR}/trades.db"
elif [ -f "${BOT_APP_DIR}/trades.db" ]; then
    DB_FILE="${BOT_APP_DIR}/trades.db"
fi

if [ -n "${DB_FILE}" ]; then
    # Use sqlite3 .backup command for a consistent copy
    # This is safe even if the bot is actively writing to the database
    if command -v sqlite3 &> /dev/null; then
        sqlite3 "${DB_FILE}" ".backup '${BACKUP_PATH}/trades.db'"
        log_info "Database backed up using sqlite3 .backup (consistent snapshot)"
    else
        # Fallback: copy the file (less safe but better than nothing)
        cp "${DB_FILE}" "${BACKUP_PATH}/trades.db"
        log_warn "sqlite3 not available, used file copy (may be inconsistent if bot is writing)"
    fi

    # Record database stats
    if command -v sqlite3 &> /dev/null; then
        TRADE_COUNT=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo "unknown")
        LAST_TRADE=$(sqlite3 "${DB_FILE}" "SELECT timestamp FROM trades ORDER BY id DESC LIMIT 1;" 2>/dev/null || echo "none")
        log_info "Database stats: ${TRADE_COUNT} trades, last trade: ${LAST_TRADE}"
    fi
else
    log_warn "No database file found, skipping database backup"
fi

# Also check for the database inside Docker volume
if docker volume inspect ccbt_bot-data &> /dev/null 2>&1; then
    # Copy database from Docker volume using a temporary container
    if docker run --rm -v ccbt_bot-data:/data alpine cat /data/trades.db > "${BACKUP_PATH}/trades_docker.db" 2>/dev/null; then
        log_info "Also backed up database from Docker volume"
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: Back up configuration files
# ---------------------------------------------------------------------------
log_info "Step 2/4: Backing up configuration files..."

# Config file
if [ -f "${BOT_APP_DIR}/config.json" ]; then
    cp "${BOT_APP_DIR}/config.json" "${BACKUP_PATH}/config.json"
    log_info "config.json backed up"
fi

# .env is intentionally NOT included in the backup archive.
# It contains API keys (Bybit, Anthropic, Telegram) and must never land in a
# world-readable tar.gz.  Back it up separately with encryption, e.g.:
#   gpg --symmetric --cipher-algo AES256 .env && mv .env.gpg /secure/location/
log_info ".env excluded from backup archive (back up separately with encryption)"

# ---------------------------------------------------------------------------
# Step 3: Compress the backup
# ---------------------------------------------------------------------------
log_info "Step 3/4: Compressing backup..."

cd "${BACKUP_DIR}"
tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}/"

# Restrict archive permissions immediately after creation (defense-in-depth).
# The directory had a world-readable umask by default; this overrides it so
# the tar.gz is readable only by its owner.
chmod 600 "${BACKUP_NAME}.tar.gz"

# Verify SQLite integrity of the backed-up database before discarding staging dir
if [ -f "${BACKUP_PATH}/trades.db" ] && command -v sqlite3 &> /dev/null; then
    if sqlite3 "${BACKUP_PATH}/trades.db" "PRAGMA integrity_check;" | grep -q "^ok$"; then
        log_info "SQLite integrity check passed"
    else
        log_error "BACKUP INTEGRITY FAILED: trades.db integrity check did not pass"
        # Continue anyway — the archive is still created, but flag the failure
    fi
fi

# Remove uncompressed directory
rm -rf "${BACKUP_PATH}"

# Calculate size
BACKUP_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" | cut -f1)
log_info "Backup compressed: ${BACKUP_NAME}.tar.gz (${BACKUP_SIZE})"

# ---------------------------------------------------------------------------
# Step 4: Clean up old backups (retention policy)
# ---------------------------------------------------------------------------
log_info "Step 4/4: Cleaning up backups older than ${BACKUP_RETENTION} days..."

DELETED_COUNT=0
find "${BACKUP_DIR}" -name "tradingbot_backup_*.tar.gz" -type f -mtime "+${BACKUP_RETENTION}" | while read -r old_backup; do
    rm -f "${old_backup}"
    log_info "Deleted old backup: $(basename "${old_backup}")"
    DELETED_COUNT=$((DELETED_COUNT + 1))
done

# Count remaining backups
REMAINING=$(find "${BACKUP_DIR}" -name "tradingbot_backup_*.tar.gz" -type f | wc -l)
log_info "Backup retention: ${REMAINING} backups on disk"

# ---------------------------------------------------------------------------
# Optional: Push to S3
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--s3" ] || [ -n "${S3_BUCKET:-}" ]; then
    S3_BUCKET="${S3_BUCKET:-}"

    if [ -z "${S3_BUCKET}" ]; then
        log_error "S3_BUCKET environment variable not set"
    elif command -v aws &> /dev/null; then
        log_info "Pushing backup to S3: ${S3_BUCKET}"
        aws s3 cp "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" \
            "${S3_BUCKET}/tradingbot/${BACKUP_NAME}.tar.gz" \
            --storage-class STANDARD_IA

        # Clean up old S3 backups (keep last 30)
        log_info "Cleaning old S3 backups..."
        aws s3 ls "${S3_BUCKET}/tradingbot/" | \
            sort -r | \
            tail -n +$((BACKUP_RETENTION + 1)) | \
            awk '{print $4}' | \
            while read -r old_s3_file; do
                aws s3 rm "${S3_BUCKET}/tradingbot/${old_s3_file}"
                log_info "Deleted S3 backup: ${old_s3_file}"
            done

        log_info "S3 upload complete"
    else
        log_error "AWS CLI not installed. Install with: pip install awscli"
    fi
fi

# ---------------------------------------------------------------------------
# Optional: Push to remote server via rsync
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--remote" ] && [ -n "${2:-}" ]; then
    REMOTE_DEST="${2}"
    log_info "Syncing backup to remote: ${REMOTE_DEST}"

    rsync -avz --progress \
        "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" \
        "${REMOTE_DEST}/tradingbot/${BACKUP_NAME}.tar.gz"

    log_info "Remote sync complete"
elif [ -n "${REMOTE_BACKUP:-}" ]; then
    log_info "Syncing backup to remote: ${REMOTE_BACKUP}"

    rsync -avz --progress \
        "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" \
        "${REMOTE_BACKUP}/tradingbot/${BACKUP_NAME}.tar.gz"

    log_info "Remote sync complete"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
log_info "=========================================="
log_info "Backup complete: ${BACKUP_NAME}.tar.gz"
log_info "Size: ${BACKUP_SIZE}"
log_info "Location: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
log_info "Backups on disk: ${REMAINING}"
log_info "=========================================="
