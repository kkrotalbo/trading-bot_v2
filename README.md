# Trading Bot v2 — ETH/USDT 1H (Short Invertido)

Bot de paper trading para ETH/USDT en timeframe de 1 hora. Consulta precios en Binance y registra señales de venta en corto en una base de datos PostgreSQL. No ejecuta órdenes reales.

## Estrategia v2

Esta versión invierte la estrategia original de LONG: las condiciones que antes abrían un LONG ahora abren un SHORT, y las condiciones que antes cerraban el LONG ahora cierran el SHORT.

| Señal | Condición de entrada | Condición de cierre |
|---|---|---|
| **SHORT** | RSI(14) entre 20 y 35 + EMA10 < EMA55 | RSI ≥ 65, Take Profit 7%, Stop Loss 0.5% |

**Parámetros de cuenta:**
- Capital inicial: $1,000 USDT
- Monto por operación: $100 USDT × 3x leverage = $300 de exposición

## Requisitos

- Python 3.10+
- PostgreSQL (local o Supabase)

## Instalación local

```bash
git clone https://github.com/kkrotalbo/trading-bot_v2.git
cd trading-bot_v2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Crea el archivo de configuración copiando el ejemplo:

```bash
cp config_trading.ini.example config_trading.ini
```

Edita `config_trading.ini` con tus datos de base de datos y parámetros deseados.

Crea la tabla en tu base de datos ejecutando `create_table.sql`:

```bash
psql -h <host> -U <usuario> -d <dbname> -f create_table.sql
```

Ejecuta el bot:

```bash
python3 trading_live.py
```

Detén el bot con `Ctrl+C`.

## Configuración

Todos los parámetros se configuran en `config_trading.ini` (nunca se sube a GitHub):

```ini
[cuenta]
capital_inicial      = 1000.0
monto_por_operacion  = 100.0

[leverage]
leverage = 3

[estrategia]
symbol           = ETHUSDT
par_display      = ETH/USDT
timeframe        = 1h
lookback_candles = 200
rsi_periodo      = 14
rsi_long_max     = 35
rsi_exit_long    = 65
rsi_exit_short   = 35
sl_pct           = 0.005
tp_pct           = 0.070

[database]
host     = localhost
port     = 5432
dbname   = trading
user     = tu_usuario
password = tu_password
```

## Deploy en Railway

El bot está configurado para desplegarse en Railway como un worker continuo.

### Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `DB_HOST` | Host de la base de datos |
| `DB_PORT` | Puerto (5432) |
| `DB_NAME` | Nombre de la base de datos |
| `DB_USER` | Usuario |
| `DB_PASSWORD` | Contraseña |

### Pasos

1. Haz fork o clona este repo en tu GitHub
2. En [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
3. Selecciona el repo y configura las variables de entorno
4. En Settings → Region, selecciona **Europe West** (Binance bloquea peticiones desde servidores en EE.UU.)
5. El bot se despliega automáticamente con cada push a `main`

### Base de datos

Se recomienda usar [Supabase](https://supabase.com) (PostgreSQL gratuito). Crea la tabla ejecutando `create_table.sql` desde el SQL Editor de Supabase.

Si usas el Connection Pooler de Supabase, el usuario debe tener el formato `postgres.PROJECT_REF` y la región debe ser `Session pooler`.

## Estructura del proyecto

```
trading-bot_v2/
├── trading_live.py           # Bot principal
├── requirements.txt          # Dependencias Python
├── Procfile                  # Comando de inicio para Railway
├── railway.toml              # Configuración de Railway
├── create_table.sql          # Script para crear la tabla en PostgreSQL
├── config_trading.ini.example # Plantilla de configuración
└── .gitignore                # Excluye config_trading.ini (contiene credenciales)
```

## Tabla de base de datos

Las operaciones se registran en la tabla `eth_binance_trading_v2` con los siguientes campos:

| Campo | Descripción |
|---|---|
| `fecha` | Timestamp de la vela |
| `operacion` | `SHORT_OPEN`, `SHORT_CLOSE` |
| `precio` | Precio de entrada o salida |
| `rsi` | Valor del RSI en el momento |
| `pnl_usdt` | Ganancia/pérdida en USDT (solo en cierres) |
| `pnl_pct` | Ganancia/pérdida en % sobre el monto (solo en cierres) |
| `saldo_acumulado` | Capital disponible tras la operación |
| `razon` | Motivo del cierre (Stop Loss, Take Profit, RSI) |
