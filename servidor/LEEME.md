# Sacar el ERP de la Mac

Todo lo que hace falta ya está escrito en esta carpeta. Faltan dos cosas que
solo puede hacer Cristina: contratar el servidor y apuntar el dominio.

## Qué hay aquí

| Archivo | Para qué |
|---|---|
| `instalar.sh` | Monta el ERP en un servidor Ubuntu recién creado. Se corre una vez. |
| `publicar.sh` | Sube los cambios desde la Mac. Se corre cada vez que hay algo nuevo. |
| `decopet.service` | Hace que el ERP arranque solo y se levante si se cae. |
| `Caddyfile` | Pone el candado (HTTPS) solo, gratis, y lo renueva solo. |
| `respaldo.timer` | Respalda dos veces al día. |

## Cómo queda montado

```
Internet ──HTTPS──> Caddy (puerto 443) ──> ERP (puerto 8765, solo local)
                                             │
                                    /home/decopet/datos      la base, fotos y documentos
                                    /home/decopet/respaldos  las copias
```

El ERP **no** se asoma a internet directamente: solo Caddy. Y el programa vive
aparte de los datos, así que actualizar el ERP nunca toca la información.

## Los dos pasos que hace Cristina

**1 · Contratar el servidor.** El más chico alcanza de sobra: 1 GB de memoria.
Hetzner (~$4/mes) o DigitalOcean (~$6/mes). Al crearlo, elegir **Ubuntu 24.04**
y entrar con **llave SSH**, nunca con contraseña.

**2 · Apuntar el dominio.** Donde se administre el DNS de shopdecopet.com,
agregar un registro **A** de `erp` a la dirección IP del servidor.

## Y después

```bash
ssh root@LA-IP
bash instalar.sh erp.shopdecopet.com
```

Tarda unos diez minutos. Al terminar, abrir `https://erp.shopdecopet.com` y
poner la primera clave, igual que se hizo en la Mac.

Los datos de la Mac se llevan con el último respaldo:

```bash
scp decopet-ultimo.db decopet@erp.shopdecopet.com:~/datos/plataforma.db
```

## Lo que hay que revisar el día de la mudanza

- Que `Configuración → Respaldo` diga que hay copias nuevas
- Que `Revisión` diga "Todo cuadra"
- Entrar como Logística, Taller y Despachador y comprobar que cada uno solo ve lo suyo
- Que la Mac deje de usarse: dos ERP con datos distintos es peor que ninguno
