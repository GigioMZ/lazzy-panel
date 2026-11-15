#!/bin/sh
# Instalación en un Lightsail Ubuntu. Uso: sudo sh install.sh panel.lazzycoffee.cl
set -e
DOMAIN=${1:?Falta el dominio, ej: panel.lazzycoffee.cl}
apt-get update -q && apt-get install -y -q python3-venv git debian-keyring debian-archive-keyring apt-transport-https curl
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi
id lazzy >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin lazzy
mkdir -p /var/lib/lazzy && chown lazzy:lazzy /var/lib/lazzy && chmod 700 /var/lib/lazzy
[ -d /opt/lazzy-panel ] || git clone https://github.com/GigioMZ/lazzy-panel /opt/lazzy-panel
chown -R lazzy:lazzy /opt/lazzy-panel
sudo -u lazzy python3 -m venv /opt/lazzy-panel/.venv
sudo -u lazzy /opt/lazzy-panel/.venv/bin/pip install -q -r /opt/lazzy-panel/requirements.txt
cp /opt/lazzy-panel/deploy/lazzy-panel.service /etc/systemd/system/
echo "lazzy ALL=(root) NOPASSWD: /usr/bin/systemctl restart lazzy-panel" > /etc/sudoers.d/lazzy-panel
cat > /etc/systemd/system/lazzy-update.service <<EOF
[Service]
Type=oneshot
User=lazzy
ExecStart=/bin/sh /opt/lazzy-panel/deploy/update.sh
EOF
cat > /etc/systemd/system/lazzy-update.timer <<EOF
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
if ! grep -q "$DOMAIN" /etc/caddy/Caddyfile 2>/dev/null; then
  printf '\n%s {\n\treverse_proxy 127.0.0.1:8765\n}\n' "$DOMAIN" >> /etc/caddy/Caddyfile
fi
systemctl daemon-reload
systemctl enable --now lazzy-panel lazzy-update.timer
systemctl reload caddy
echo "Listo. Ahora crea los usuarios:"
echo "  sudo -u lazzy LAZZY_DB=/var/lib/lazzy/lazzy.db /opt/lazzy-panel/.venv/bin/python -m lazzy.cli --db /var/lib/lazzy/lazzy.db user guillermo"
