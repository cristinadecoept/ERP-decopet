# Sacar el ERP de la Mac

## La idea

El servidor **no tiene ninguna puerta web abierta a internet**. Es él quien llama hacia
Cloudflare, y por ese túnel entra el tráfico. Si alguien escanea la IP del servidor
buscando por dónde entrar, no encuentra nada que golpear.

Encima de eso, Cloudflare pide un código por correo **antes** de mostrar el ERP. Un
extraño que escriba la dirección nunca llega a ver la pantalla de entrada.

```
  internet  →  Cloudflare (pide correo)  →  túnel  →  ERP  (sin puertas abiertas)
```

Quedan tres candados en fila: el correo autorizado, el código que llega, y la clave del ERP.

## Lo que hace Cristina (una vez)

### 1. El servidor
Hetzner → Ubuntu 24.04 → **CPX21** en Ashburn → Backups activados → pegar la llave SSH.

### 2. El túnel
En **one.dash.cloudflare.com** → *Networks* → *Tunnels* → *Create a tunnel* → Cloudflared.
- Nombre: `decopet`
- Copiar la **ficha** larga que aparece en el comando de instalación (no correr ese comando)
- En *Public Hostname*: dominio `shopdecopet.com`, subdominio `erp`,
  Service → **HTTP** → `localhost:8765`

### 3. El candado del correo
*Access* → *Applications* → *Add an application* → Self-hosted
- Dominio: `erp.shopdecopet.com`
- Política: *Allow* → **Emails** → la lista de quién puede entrar
- Duración de la sesión: 1 mes

### 4. Pasarme la IP y la ficha
Con eso corro la instalación completa.

## La instalación (la corro yo)

```bash
ssh root@<IP>
# subir la carpeta y:
bash instalar.sh <ficha-del-tunel>
```

Deja puesto: cortafuegos sin puertas web, acceso remoto solo con llave, fail2ban,
actualizaciones de seguridad automáticas, el ERP como usuario sin privilegios,
respaldo diario y todo arrancando solo si el servidor se reinicia.

## Cambios del día a día

Desde la Mac, en la carpeta del ERP:

```bash
bash servidor/publicar.sh
```

Corre las 21 pruebas antes de subir nada. Si algo falla, no publica.

La IP de publicación va en `servidor/destino` (ej: `decopet@5.161.1.2`). No se guarda
en el historial a propósito.

## Si algo no abre

```bash
systemctl status decopet          # el ERP
systemctl status cloudflared      # el túnel
journalctl -u decopet -n 40       # qué dijo el ERP
ss -ltnp | grep -v 127.0.0.1      # qué puertas hay abiertas (debería estar casi vacío)
```
