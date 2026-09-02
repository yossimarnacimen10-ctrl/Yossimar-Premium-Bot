# ⚡ Yossimar Premium Bot

Bot de Telegram para vender consultas Apple usando la API de SICKW.

## Incluye

- `/start` con menú.
- 🍎 Check Apple.
- 💰 Mi saldo.
- 💳 Recargar saldo (estructura lista; la pasarela bancaria se conecta después).
- 📜 Historial.
- 🆘 Soporte.
- SICKW `service=61`.
- Descuento/reintegro de créditos.
- Historial en SQLite.
- Comandos de administrador para gestionar saldo.
- Token y API Key fuera del código mediante `.env`.

## Instalación

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Luego:

```bash
pip install -r requirements.txt
```

Copia `.env.example` como `.env` y coloca allí el token nuevo de Telegram y la API Key nueva de SICKW. No subas `.env` a GitHub ni compartas esas claves.

## Ejecutar

```bash
python bot.py
```

## Administrador

Añade tu ID de Telegram en `ADMIN_IDS`. Puedes conocerlo con `/id`.

```text
/credito TELEGRAM_ID CANTIDAD
/debito TELEGRAM_ID CANTIDAD
/saldo TELEGRAM_ID
```

## Recargas con tarjeta

El botón `💳 Recargar saldo` ya está preparado, pero todavía no procesa tarjetas. El siguiente módulo será integrar BAC o Atlántida con checkout hospedado, confirmación de pago y acreditación automática. El bot no debe almacenar número de tarjeta, CVV ni vencimiento.

## SICKW

Por defecto usa el Service ID `61`. Antes de producción conviene hacer 2–3 pruebas reales y ajustar el formateador a la respuesta exacta que entregue la API.
