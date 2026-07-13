#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/sci-platform}"
APP_USER="${APP_USER:-sci-platform}"
SERVER_NAME="${SCI_SERVER_NAME:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root." >&2
  exit 1
fi

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3
  else
    echo "python3 is required, but no supported package manager was found." >&2
    exit 1
  fi
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR"

if [ -d "$APP_DIR/backend" ] || [ -d "$APP_DIR/frontend" ]; then
  BACKUP_DIR="${APP_DIR}.backup.$(date +%Y%m%d%H%M%S)"
  mkdir -p "$BACKUP_DIR"
  cp -a "$APP_DIR/." "$BACKUP_DIR/"
  echo "Existing deployment backed up to $BACKUP_DIR"
fi

mkdir -p "$APP_DIR/data/exports" "$APP_DIR/data/connection_tests"
rm -rf "$APP_DIR/backend" "$APP_DIR/frontend"
cp -a backend "$APP_DIR/"
cp -a frontend "$APP_DIR/"
cp -a README.md "$APP_DIR/"
if [ -f data/sci_platform.sqlite3 ]; then
  cp -a data/sci_platform.sqlite3 "$APP_DIR/data/"
fi
if [ -d data/exports ]; then
  cp -a data/exports/. "$APP_DIR/data/exports/" 2>/dev/null || true
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

install -m 0644 deploy/sci-platform.service /etc/systemd/system/sci-platform.service
systemctl daemon-reload
systemctl enable sci-platform
systemctl restart sci-platform

if [ -n "$SERVER_NAME" ]; then
  if ! command -v nginx >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update
      apt-get install -y nginx
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y nginx
    elif command -v yum >/dev/null 2>&1; then
      yum install -y nginx
    else
      echo "nginx is not installed and no supported package manager was found." >&2
      exit 1
    fi
  fi

  sed "s/SCI_SERVER_NAME/$SERVER_NAME/g" deploy/nginx-sci-platform.conf > /etc/nginx/conf.d/sci-platform.conf
  nginx -t
  systemctl enable nginx
  systemctl reload nginx || systemctl restart nginx
fi

systemctl --no-pager --full status sci-platform
echo "SCI platform service is deployed."
if [ -n "$SERVER_NAME" ]; then
  echo "Public URL: http://$SERVER_NAME/"
else
  echo "Internal URL: http://127.0.0.1:8000/"
fi
