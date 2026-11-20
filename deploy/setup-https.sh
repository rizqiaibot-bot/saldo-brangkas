#!/usr/bin/env bash
# Aktifkan HTTPS untuk saldo.brangkas.biz.id.
# JALANKAN SETELAH DNS aktif: record A "saldo" -> 103.127.96.23 di Domainesia.
#   bash deploy/setup-https.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-saldo.brangkas.biz.id}"
EMAIL="${EMAIL:-rizqi.aibot@gmail.com}"
IP="${TARGET_IP:-103.127.96.23}"

echo "==> cek DNS $DOMAIN"
if ! getent hosts "$DOMAIN" | grep -q "$IP"; then
  echo "GALAT: DNS $DOMAIN belum menunjuk ke $IP (atau belum ter-propagasi)."
  echo "Tambahkan record A host 'saldo' -> $IP di DNS management Domainesia, lalu ulangi."
  exit 1
fi
echo "DNS OK: $(getent hosts "$DOMAIN")"

echo "==> pasang server block"
sudo cp "$PROJECT_DIR/deploy/nginx-brangkas-https.conf" /etc/nginx/sites-available/brangkas-https
sudo ln -sf /etc/nginx/sites-available/brangkas-https /etc/nginx/sites-enabled/brangkas-https
sudo nginx -t
sudo systemctl reload nginx

echo "==> minta sertifikat Let's Encrypt (certbot)"
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --no-eff-email -m "$EMAIL" --redirect

echo "==> selesai. Cek: curl -sI https://$DOMAIN/"
