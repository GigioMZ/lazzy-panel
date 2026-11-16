#!/bin/sh
# Instala el servicio del bot. Las claves se pegan después en el panel (página «Bot de Telegram»).
# Uso: sudo sh /opt/lazzy-panel/deploy/install_bot.sh
set -e
mkdir -p /var/lib/lazzy/archivos && chown lazzy:lazzy /var/lib/lazzy/archivos && chmod 700 /var/lib/lazzy/archivos
cp /opt/lazzy-panel/deploy/lazzy-bot.service /etc/systemd/system/
echo "lazzy ALL=(root) NOPASSWD: /usr/bin/systemctl restart lazzy-panel, /usr/bin/systemctl restart lazzy-bot" > /etc/sudoers.d/lazzy-panel
sudo -u lazzy /opt/lazzy-panel/.venv/bin/pip install -q -r /opt/lazzy-panel/requirements.txt
systemctl daemon-reload
systemctl enable --now lazzy-bot
systemctl restart lazzy-panel
sleep 2
systemctl is-active lazzy-bot >/dev/null && echo "Listo: bot instalado. Ahora pega las claves en el panel, página Bot de Telegram." \
  || { echo "El bot no arrancó:"; journalctl -u lazzy-bot -n 20 --no-pager; }
