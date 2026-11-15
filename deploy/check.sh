#!/bin/sh
# Solo lectura: muestra qué usa los puertos web y qué servicios propios corren.
clear
echo "== puertos escuchando =="
sudo ss -tlnpH | awk '{print $4, $6}' | sed 's/users:((//; s/,pid.*//' | sort -u | cut -c1-58
echo "== web servers instalados =="
for b in caddy nginx apache2 docker; do command -v $b >/dev/null && echo "$b SI" || echo "$b no"; done
echo "== servicios propios activos =="
ls /etc/systemd/system/*.service 2>/dev/null | xargs -n1 basename | tr '\n' ' ' | fold -w 58
echo
echo "== memoria MB / python =="
free -m | awk 'NR==2{print "total",$2,"libre",$7}'
python3 --version; lsb_release -ds
