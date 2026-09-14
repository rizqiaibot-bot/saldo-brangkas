#!/usr/bin/env bash
# Setup VPS Saldo Brangkas (idempotent). Jalankan dari root project.
#   bash deploy/setup-vps.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_NAME="${DB_NAME:-saldo_brangkas}"
DB_USER="${DB_USER:-saldo_app}"
WEBROOT="/var/www/saldo-brangkas"
BACKUP="$PROJECT_DIR/saldo-brangkas-supabase-backup.sql"
ENV_FILE="$PROJECT_DIR/.env"

echo "==> Saldo Brangkas VPS setup (project: $PROJECT_DIR)"

if ! sudo -u postgres psql -Atc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  sudo -u postgres createdb "$DB_NAME"
  echo "database $DB_NAME dibuat"
else
  echo "database $DB_NAME sudah ada"
fi

if ! sudo -u postgres psql -Atc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN;"
  echo "role $DB_USER dibuat"
else
  echo "role $DB_USER sudah ada"
fi

if [ ! -f "$ENV_FILE" ]; then
  PW="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-32)"
  sudo -u postgres psql -c "ALTER ROLE $DB_USER WITH PASSWORD '$PW';" >/dev/null
  umask 077
  cat > "$ENV_FILE" <<EOF
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$PW
BIND_HOST=127.0.0.1
BIND_PORT=8787
SESSION_TTL_HOURS=720
COOKIE_NAME=sb_session
APP_TZ=Asia/Jakarta
EOF
  chmod 600 "$ENV_FILE"
  echo ".env dibuat (password DB acak, tidak ditampilkan)"
else
  echo ".env sudah ada (dibiarkan)"
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" < "$BACKUP" >/dev/null
echo "restore backup selesai"

sudo cp "$PROJECT_DIR/deploy/saldo-brangkas.service" /etc/systemd/system/saldo-brangkas.service
sudo systemctl daemon-reload
sudo systemctl enable saldo-brangkas >/dev/null 2>&1 || true
sudo systemctl restart saldo-brangkas
echo "service saldo-brangkas dijalankan"

sudo mkdir -p "$WEBROOT"
sudo cp "$PROJECT_DIR/index.html" "$WEBROOT/index.html"
sudo chown -R www-data:www-data "$WEBROOT"
sudo cp "$PROJECT_DIR/deploy/nginx-saldo-brangkas.conf" /etc/nginx/sites-available/saldo-brangkas
sudo ln -sf /etc/nginx/sites-available/saldo-brangkas /etc/nginx/sites-enabled/saldo-brangkas
sudo nginx -t
sudo systemctl reload nginx
echo "nginx di-reload"

echo "==> Selesai. Cek: curl -s http://127.0.0.1/api/health"
