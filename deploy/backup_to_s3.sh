#!/usr/bin/env bash
# Nightly database backup, copied to Amazon S3 when a bucket is configured.
# Run from cron (see deploy/DEPLOY_AWS_LIGHTSAIL.md). Cron starts it with an
# almost empty environment, so the settings it needs are read from the
# project's .env file here rather than expected in the environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
BACKUP_DIR="$PROJECT_ROOT/backups"
TIMESTAMP="$(date +'%Y%m%dT%H%M%S')"
BACKUP_FILE="$BACKUP_DIR/apartment-$TIMESTAMP.dump"

# A value from .env, unless the environment already sets it. Read line by line
# rather than sourced, so a password with shell characters cannot run as code.
setting() {
  local name="$1" default="${2:-}" value="${!1:-}"
  if [[ -z "$value" && -f "$ENV_FILE" ]]; then
    value="$(sed -n "s/^${name}=//p" "$ENV_FILE" | tail -n 1)"
  fi
  printf '%s' "${value:-$default}"
}

DB_NAME="$(setting AEM_DB_NAME aem)"
DB_OWNER="$(setting AEM_DB_MIGRATION_USER aem_owner)"
S3_BUCKET="$(setting AEM_S3_BACKUP_BUCKET)"

mkdir -p "$BACKUP_DIR"

# 1. A consistent dump taken from the running database container.
echo "==> [$(date)] Dumping the production database..."
docker compose -f "$PROJECT_ROOT/docker-compose.prod.yml" exec -T db \
  pg_dump -U "$DB_OWNER" -d "$DB_NAME" --format=custom --no-owner --no-privileges > "$BACKUP_FILE"
if [[ ! -s "$BACKUP_FILE" ]]; then
  echo "ERROR: the dump is empty; not keeping it." >&2
  rm -f "$BACKUP_FILE"
  exit 1
fi
echo "==> Backup created: $BACKUP_FILE ($(( $(stat -c%s "$BACKUP_FILE") / 1024 )) KiB)"

# 2. Off the server, to S3.
if [[ -n "$S3_BUCKET" ]]; then
  if command -v aws >/dev/null 2>&1; then
    aws s3 cp "$BACKUP_FILE" "s3://$S3_BUCKET/aem-backups/$(basename "$BACKUP_FILE")"
    echo "==> Uploaded to s3://$S3_BUCKET/aem-backups/"
  else
    echo "ERROR: AEM_S3_BACKUP_BUCKET is set but the aws CLI is not installed;" \
         "the backup is only on this server at $BACKUP_FILE." >&2
    exit 1
  fi
else
  echo "==> No AEM_S3_BACKUP_BUCKET set: the backup is kept on this server only."
fi

# 3. Keep 30 days of local copies.
find "$BACKUP_DIR" -type f -name "apartment-*.dump" -mtime +30 -delete
echo "==> Local copies older than 30 days removed."
