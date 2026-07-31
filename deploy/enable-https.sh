#!/usr/bin/env bash
set -euo pipefail

DOMAIN="nikitaabashevst.fvds.ru"
PROJECT_DIR="/opt/volnoyebot"
WEBROOT="/var/www/letsencrypt"

certbot certonly \
  --webroot \
  --webroot-path "$WEBROOT" \
  --domain "$DOMAIN" \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email

install -o root -g root -m 0644 \
  "$PROJECT_DIR/deploy/nginx-volnoyebot.conf" \
  /etc/nginx/sites-available/volnoyebot

install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -o root -g root -m 0755 \
  "$PROJECT_DIR/deploy/certbot-renewal-hook.sh" \
  /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

nginx -t
systemctl reload nginx

# После первого успешного выпуска повторные попытки больше не нужны.
# Дальнейшее продление выполняет штатный certbot.timer.
if systemctl list-unit-files volnoyebot-enable-https.timer \
  --no-legend 2>/dev/null | grep -q '^volnoyebot-enable-https.timer'; then
  systemctl disable --now volnoyebot-enable-https.timer
fi
