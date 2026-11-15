#!/bin/sh
# Instala la copia diaria de la base (03:30). Uso: sudo sh /opt/lazzy-panel/deploy/install_respaldo.sh
set -e
cat > /etc/systemd/system/lazzy-respaldo.service <<EOF
[Service]
Type=oneshot
User=lazzy
ExecStart=/opt/lazzy-panel/.venv/bin/python /opt/lazzy-panel/deploy/backup.py /var/lib/lazzy/lazzy.db
EOF
cat > /etc/systemd/system/lazzy-respaldo.timer <<EOF
[Timer]
OnCalendar=*-*-* 03:30
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now lazzy-respaldo.timer
systemctl start lazzy-respaldo.service
ls -la /var/lib/lazzy/respaldos | tail -3
