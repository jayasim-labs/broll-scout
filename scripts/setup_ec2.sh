#!/bin/bash
set -euo pipefail

DOMAIN="broll.jayasim.com"
APP_DIR="/opt/broll-scout"
APP_USER="broll"

echo "=== B-Roll Scout EC2 Setup ==="

apt-get update && apt-get upgrade -y
apt-get install -y python3.12 python3.12-venv python3-pip nginx certbot python3-certbot-nginx git

if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$APP_USER"
fi

mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

echo "=== Setting up Python venv ==="
sudo -u "$APP_USER" python3.12 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip

echo "=== Installing Python dependencies ==="
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== Creating systemd service ==="
cat > /etc/systemd/system/broll-scout.service <<EOF
[Unit]
Description=B-Roll Scout API
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=/opt/broll-scout/.env
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable broll-scout

echo "=== Configuring Nginx ==="
cat > /etc/nginx/sites-available/broll-scout <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/broll-scout /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Point DNS: $DOMAIN -> $(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)"
echo "  2. Deploy code to $APP_DIR"
echo "  3. Create $APP_DIR/.env with API keys"
echo "  4. Run: systemctl start broll-scout"
echo "  5. Run: certbot --nginx -d $DOMAIN"
