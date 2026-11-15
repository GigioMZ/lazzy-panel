#!/bin/sh
# Crea un usuario del panel o le cambia la clave. Uso: sudo sh /opt/lazzy-panel/deploy/usuario.sh guillermo
set -e
cd /opt/lazzy-panel
exec sudo -u lazzy /opt/lazzy-panel/.venv/bin/python -m lazzy.cli --db /var/lib/lazzy/lazzy.db user "${1:?Falta el nombre de usuario}"
