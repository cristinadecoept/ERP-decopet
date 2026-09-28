#!/bin/bash
# Monta el ERP Decopet en un servidor Ubuntu recién creado.
# Se corre UNA vez, como root:    bash instalar.sh <ficha-del-tunel>
#
# La idea: el servidor NO abre ninguna puerta web a internet. Es él quien llama
# hacia Cloudflare y por ahí entra el tráfico. Un escaneo de la IP no encuentra nada.
set -e
FICHA="$1"
USUARIO=decopet
[ -n "$FICHA" ] || { echo "Falta la ficha del túnel. Uso: bash instalar.sh <ficha>"; exit 1; }

echo "==> 1/8 Actualizando el sistema"
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq python3-venv python3-pip sqlite3 git ufw curl fail2ban

echo "==> 2/8 Cortafuegos: NINGUNA puerta web abierta"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null          # la única entrada, y solo con llave
ufw --force enable >/dev/null

echo "==> 3/8 Cerrando el acceso remoto con contraseña"
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#*KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh || systemctl restart sshd
systemctl enable --now fail2ban >/dev/null

echo "==> 4/8 Actualizaciones de seguridad automáticas"
apt-get install -y -qq unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> 5/8 Usuario propio para el ERP (no root)"
id -u $USUARIO >/dev/null 2>&1 || adduser --disabled-password --gecos "" $USUARIO
mkdir -p /home/$USUARIO/{datos,respaldos}
chmod 700 /home/$USUARIO/datos /home/$USUARIO/respaldos
chown -R $USUARIO:$USUARIO /home/$USUARIO
install -d -m 755 -o $USUARIO -g $USUARIO /home/$USUARIO/erp.git
sudo -u $USUARIO git init --bare -q /home/$USUARIO/erp.git 2>/dev/null || true

echo "==> 6/8 El túnel de Cloudflare"
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg > /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq && apt-get install -y -qq cloudflared
cloudflared service install "$FICHA"

echo "==> 7/8 Instalando el ERP"
sudo -u $USUARIO bash <<EOF
cd /home/$USUARIO
[ -d erp/.git ] || git clone -q /home/$USUARIO/erp.git erp
cd erp
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
EOF

echo "==> 8/8 Encendiendo"
cp /home/$USUARIO/erp/servidor/decopet.service /etc/systemd/system/
cp /home/$USUARIO/erp/servidor/respaldo.service /etc/systemd/system/
cp /home/$USUARIO/erp/servidor/respaldo.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now decopet respaldo.timer

echo
echo "Listo. Comprobando:"
sleep 3
systemctl is-active decopet cloudflared fail2ban
echo
echo "Puertas abiertas a internet:"; ss -ltnp | grep -v 127.0.0.1 | grep -v '::1' || echo "  (ninguna, aparte del acceso remoto)"
