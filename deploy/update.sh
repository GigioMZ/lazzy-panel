#!/bin/sh
# Trae la última versión de GitHub y reinicia solo si cambió. Lo corre el timer cada 5 minutos.
set -e
cd /opt/lazzy-panel
old=$(git rev-parse HEAD)
git pull -q --ff-only
[ "$old" = "$(git rev-parse HEAD)" ] && exit 0
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart lazzy-panel
if systemctl is-active -q lazzy-bot; then sudo systemctl restart lazzy-bot; fi
