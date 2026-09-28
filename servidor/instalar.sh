#!/bin/bash
# Monta el ERP Decopet en un servidor Ubuntu recién creado.
# Se corre UNA vez, como root:    bash instalar.sh
set -e
DOMINIO="${1:-erp.shopdecopet.com}"
USUARIO=decopet

echo "==> 1/7 Actualizando el sistema"
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq python3-venv python3-pip sqlite3 git ufw debian-keyring debian-archive-keyring apt-transport-https curl

echo "==> 2/7 Cortafuegos: solo web y el acceso remoto"
ufw allow OpenSSH >/dev/null
ufw allow 80,443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> 3/7 Actualizaciones de seguridad automáticas"
apt-get install -y -qq unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> 4/7 Usuario propio para el ERP (no root)"
id -u $USUARIO >/dev/null 2>&1 || adduser --disabled-password --gecos "" $USUARIO
mkdir -p /home/$USUARIO/{datos,respaldos}
chown -R $USUARIO:$USUARIO /home/$USUARIO

echo "==> 5/7 Caddy, que pone el HTTPS solo"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq && apt-get install -y -qq caddy

echo "==> 6/7 Instalando el ERP"
sudo -u $USUARIO bash <<EOF
cd /home/$USUARIO
[ -d erp ] || git clone /home/$USUARIO/erp.git erp 2>/dev/null || mkdir -p erp
cd erp
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
EOF

echo "==> 7/7 Encendiendo"
sed "s/erp.shopdecopet.com/$DOMINIO/" /home/$USUARIO/erp/servidor/Caddyfile > /etc/caddy/Caddyfile
cp /home/$USUARIO/erp/servidor/decopet.service /etc/systemd/system/
cp /home/$USUARIO/erp/servidor/respaldo.service /etc/systemd/system/
cp /home/$USUARIO/erp/servidor/respaldo.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now decopet respaldo.timer
systemctl reload caddy

echo
echo "Listo. El ERP debería estar en https://$DOMINIO"
echo "Si no abre, revisa:  systemctl status decopet  y  journalctl -u caddy -n 30"
