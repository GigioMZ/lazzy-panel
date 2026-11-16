#!/bin/sh
# Instala el bot de Telegram. Uso: sudo sh /opt/lazzy-panel/deploy/install_bot.sh
# Pide el token de Telegram y la clave de Anthropic; quedan solo en /etc/lazzy/bot.env (solo root puede leerlo).
set -e
mkdir -p /etc/lazzy && chmod 700 /etc/lazzy
printf "Token del bot de Telegram (de @BotFather): "; stty -echo; read TOKEN; stty echo; echo
printf "Clave de la API de Anthropic (empieza con sk-ant-): "; stty -echo; read CLAVE; stty echo; echo
[ -n "$TOKEN" ] && [ -n "$CLAVE" ] || { echo "Faltan datos, no se instaló nada."; exit 1; }
NOMBRE=$(curl -fsS "https://api.telegram.org/bot$TOKEN/getMe" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['username'])") \
  || { echo "El token de Telegram no funciona. Revísalo en @BotFather."; exit 1; }
CODIGO=$(curl -s -o /dev/null -w "%{http_code}" https://api.anthropic.com/v1/models -H "x-api-key: $CLAVE" -H "anthropic-version: 2023-06-01")
[ "$CODIGO" = "200" ] || { echo "La clave de Anthropic no funciona (respuesta $CODIGO). Revísala en console.anthropic.com."; exit 1; }
umask 077
printf 'TELEGRAM_TOKEN=%s\nANTHROPIC_API_KEY=%s\n' "$TOKEN" "$CLAVE" > /etc/lazzy/bot.env
chmod 600 /etc/lazzy/bot.env
mkdir -p /var/lib/lazzy/archivos && chown lazzy:lazzy /var/lib/lazzy/archivos && chmod 700 /var/lib/lazzy/archivos
cp /opt/lazzy-panel/deploy/lazzy-bot.service /etc/systemd/system/
mkdir -p /etc/systemd/system/lazzy-panel.service.d
printf '[Service]\nEnvironment=LAZZY_BOT_NOMBRE=%s\n' "$NOMBRE" > /etc/systemd/system/lazzy-panel.service.d/bot.conf
echo "lazzy ALL=(root) NOPASSWD: /usr/bin/systemctl restart lazzy-panel, /usr/bin/systemctl restart lazzy-bot" > /etc/sudoers.d/lazzy-panel
sudo -u lazzy /opt/lazzy-panel/.venv/bin/pip install -q -r /opt/lazzy-panel/requirements.txt
systemctl daemon-reload
systemctl enable --now lazzy-bot
systemctl restart lazzy-panel
sleep 3
systemctl is-active lazzy-bot >/dev/null && echo "Listo: el bot @$NOMBRE está funcionando." || { echo "El bot no arrancó:"; journalctl -u lazzy-bot -n 20 --no-pager; }
