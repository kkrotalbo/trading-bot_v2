"""
Trading en vivo ETH/USDT 1H — Paper Trading (registra en BD, no ejecuta órdenes reales)
══════════════════════════════════════════════════════════════════════════════════════════
Lee config_trading.ini para todos los parámetros.

Estrategia v2 (SHORT invertido):
  SHORT : RSI(14) entre 20 y 35  +  EMA10 < EMA55
  CIERRE SHORT : RSI ≥ 65  |  Take Profit 7%  |  Stop Loss 0.5%

Cuenta:
  Capital inicial  : $1,000 USDT
  Por operación    : $100 USDT × leverage (configurable en .ini)

Uso:
    python3 trading_live.py
    Ctrl+C para detener
"""

import os
import sys
import time
import signal
import requests
import psycopg2
import pandas as pd
import numpy as np
import configparser
from datetime import datetime, timezone
from colorama import Fore, Style, init

init(autoreset=True)

# ── Leer configuración ────────────────────────────────────────────────────────
cfg = configparser.ConfigParser()
cfg.read("config_trading.ini")

def _cfg(section, key, fallback=None):
    return os.environ.get(key.upper(), cfg.get(section, key, fallback=fallback, raw=True))

CAPITAL_INICIAL     = float(_cfg("cuenta",    "capital_inicial",     "1000.0"))
MONTO_OP            = float(_cfg("cuenta",    "monto_por_operacion", "100.0"))
LEVERAGE            = int(_cfg("leverage",    "leverage",            "3"))

SYMBOL              = _cfg("estrategia", "symbol",           "ETHUSDT")
PAR_DISPLAY         = _cfg("estrategia", "par_display",      "ETH/USDT")
TIMEFRAME           = _cfg("estrategia", "timeframe",        "1h")
LOOKBACK            = int(_cfg("estrategia", "lookback_candles", "200"))
RSI_PERIODO         = int(_cfg("estrategia", "rsi_periodo",      "14"))
RSI_LONG_MAX        = int(_cfg("estrategia", "rsi_long_max",     "35"))
RSI_EXIT_LONG       = int(_cfg("estrategia", "rsi_exit_long",    "65"))
RSI_EXIT_SHORT      = int(_cfg("estrategia", "rsi_exit_short",   "35"))
SL_PCT              = float(_cfg("estrategia", "sl_pct", "0.005"))
TP_PCT              = float(_cfg("estrategia", "tp_pct", "0.070"))

DB_HOST             = os.environ.get("DB_HOST",     cfg.get("database", "host",     fallback="localhost", raw=True))
DB_PORT             = int(os.environ.get("DB_PORT", cfg.get("database", "port",     fallback="5432",      raw=True)))
DB_NAME             = os.environ.get("DB_NAME",     cfg.get("database", "dbname",   fallback="postgres",  raw=True))
DB_USER             = os.environ.get("DB_USER",     cfg.get("database", "user",     fallback="postgres",  raw=True))
DB_PASSWORD         = os.environ.get("DB_PASSWORD", cfg.get("database", "password", fallback="",          raw=True)) or None

BINANCE_URL         = "https://api.binance.com/api/v3/klines"
TABLE               = "eth_binance_trading_v2"

print(f"[DEBUG] DB_HOST={DB_HOST!r}  DB_PORT={DB_PORT}  DB_NAME={DB_NAME!r}  DB_USER={DB_USER!r}")


# ── Control de ejecución ──────────────────────────────────────────────────────
_running = True
def _stop(sig, frame):
    global _running
    print(f"\n{Fore.YELLOW}[INFO] Deteniendo sistema...")
    _running = False
signal.signal(signal.SIGINT,  _stop)
signal.signal(signal.SIGTERM, _stop)


# ── Base de datos ─────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER,
        sslmode="require",
        **({} if not DB_PASSWORD else {"password": DB_PASSWORD})
    )


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id                SERIAL PRIMARY KEY,
                fecha             TIMESTAMP NOT NULL,
                par               VARCHAR(15) NOT NULL,
                operacion         VARCHAR(20) NOT NULL,   -- LONG_OPEN | LONG_CLOSE | SHORT_OPEN | SHORT_CLOSE
                precio            NUMERIC(18,8) NOT NULL,
                monto_operacion   NUMERIC(18,4) NOT NULL, -- USDT usados como margen
                apalancamiento    INTEGER NOT NULL,
                exposicion_usdt   NUMERIC(18,4) NOT NULL, -- monto × leverage
                rsi               NUMERIC(8,4),
                pnl_usdt          NUMERIC(18,4),          -- ganancia/pérdida al cerrar (NULL en apertura)
                pnl_pct           NUMERIC(10,4),          -- % sobre monto operación (NULL en apertura)
                saldo_acumulado   NUMERIC(18,4) NOT NULL, -- capital disponible tras la operación
                variacion_pct     NUMERIC(10,4),          -- % cambio de saldo vs operación anterior
                razon             TEXT,
                created_at        TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_fecha ON {TABLE} (fecha);")
    conn.commit()


def get_last_balance(conn) -> float:
    """Retorna el último saldo acumulado registrado o el capital inicial."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT saldo_acumulado FROM {TABLE} ORDER BY id DESC LIMIT 1;")
        row = cur.fetchone()
    return float(row[0]) if row else CAPITAL_INICIAL


def insert_operacion(conn, fecha: datetime, operacion: str, precio: float,
                     rsi: float, pnl_usdt: float | None, pnl_pct: float | None,
                     saldo_acumulado: float, saldo_anterior: float, razon: str = ""):
    variacion = ((saldo_acumulado - saldo_anterior) / saldo_anterior * 100) if saldo_anterior else 0.0
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {TABLE}
                (fecha, par, operacion, precio, monto_operacion, apalancamiento,
                 exposicion_usdt, rsi, pnl_usdt, pnl_pct,
                 saldo_acumulado, variacion_pct, razon)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
        """, (
            fecha, PAR_DISPLAY, operacion,
            float(precio), float(MONTO_OP), int(LEVERAGE),
            float(MONTO_OP * LEVERAGE),
            float(rsi) if rsi is not None else None,
            float(pnl_usdt) if pnl_usdt is not None else None,
            float(pnl_pct)  if pnl_pct  is not None else None,
            float(saldo_acumulado),
            float(variacion),
            str(razon),
        ))
    conn.commit()


# ── Binance ───────────────────────────────────────────────────────────────────
def fetch_candles(limit: int = LOOKBACK) -> pd.DataFrame:
    resp = requests.get(BINANCE_URL, params={
        "symbol": SYMBOL, "interval": TIMEFRAME, "limit": limit
    }, timeout=10)
    resp.raise_for_status()
    raw = resp.json()
    df  = pd.DataFrame(raw, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tb","tq","ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df[["open_time","open","high","low","close","volume"]].copy()


def wait_next_candle(df: pd.DataFrame) -> pd.DataFrame:
    """Polling cada 15s hasta que aparezca una vela nueva."""
    last_ts = df["open_time"].iloc[-1]
    while _running:
        time.sleep(15)
        try:
            new_df = fetch_candles()
            if new_df["open_time"].iloc[-1] != last_ts:
                return new_df
        except Exception as e:
            print(f"{Fore.RED}[Binance] Error: {e}")
    return df


# ── Indicadores ───────────────────────────────────────────────────────────────
def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]

    # EMA 10 y 55
    df["ema10"] = close.ewm(span=10, adjust=False).mean()
    df["ema55"] = close.ewm(span=55, adjust=False).mean()

    # RSI 14
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=RSI_PERIODO - 1, adjust=False).mean()
    avg_l = loss.ewm(com=RSI_PERIODO - 1, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + avg_g / avg_l.replace(0, np.nan)))

    df["ema10_below"] = df["ema10"] < df["ema55"]
    df["cross_down"]  = (~df["ema10_below"].shift(1).fillna(False)) & df["ema10_below"]

    return df


# ── Display ───────────────────────────────────────────────────────────────────
def log(msg: str, color: str = Fore.WHITE):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.WHITE}[{ts}] {color}{msg}{Style.RESET_ALL}")


def print_header():
    print(f"""
{Style.BRIGHT}{'═'*65}
  TRADING LIVE — ETH/USDT 1H  |  Paper Trading
  Capital: ${CAPITAL_INICIAL:,.2f}  |  Por operación: ${MONTO_OP:.0f} × {LEVERAGE}x = ${MONTO_OP*LEVERAGE:.0f} exposición
  SL: {SL_PCT*100:.1f}%  TP: {TP_PCT*100:.0f}%  SHORT cuando RSI≤{RSI_LONG_MAX}+EMA10<EMA55  Cierre RSI≥{RSI_EXIT_LONG}
{'═'*65}{Style.RESET_ALL}""")


def print_candle(row, rsi, ema10, ema55):
    ts    = row["open_time"].strftime("%Y-%m-%d %H:%M")
    precio = row["close"]
    diff_ema = ema10 - ema55
    trend = f"{Fore.GREEN}EMA10>EMA55" if diff_ema > 0 else f"{Fore.RED}EMA10<EMA55"
    print(f"\n{'─'*65}")
    print(f"  {Fore.YELLOW}{ts}{Fore.WHITE}  Precio: {Fore.CYAN}${precio:,.2f}  "
          f"{Fore.WHITE}RSI: {Fore.CYAN}{rsi:.1f}  {Fore.WHITE}{trend}")


def print_op(operacion: str, precio: float, pnl: float | None,
             saldo: float, variacion: float, razon: str):
    if "OPEN" in operacion:
        color = Fore.GREEN if "LONG" in operacion else Fore.RED
        icono = "▲ LONG  ABIERTO" if "LONG" in operacion else "▼ SHORT ABIERTO"
        print(f"  {color}{Style.BRIGHT}{icono} @ ${precio:,.2f}{Style.RESET_ALL}")
        print(f"  Margen: ${MONTO_OP:.0f}  |  Exposición: ${MONTO_OP*LEVERAGE:.0f}  "
              f"|  Saldo disponible: ${saldo:,.2f}")
    else:
        color   = Fore.GREEN if (pnl or 0) >= 0 else Fore.RED
        icono   = "▲ LONG  CERRADO" if "LONG" in operacion else "▼ SHORT CERRADO"
        pnl_str = f"${pnl:>+.2f}" if pnl is not None else ""
        print(f"  {color}{Style.BRIGHT}{icono} @ ${precio:,.2f}  [{razon}]{Style.RESET_ALL}")
        print(f"  {color}P&L: {pnl_str}  |  Saldo: ${saldo:,.2f}  ({variacion:+.2f}%){Style.RESET_ALL}")


# ── Lógica de cuenta ──────────────────────────────────────────────────────────
class Cuenta:
    def __init__(self, saldo_inicial: float):
        self.saldo_disponible = saldo_inicial   # capital no comprometido
        self.en_posicion      = False
        self.tipo_posicion    = None            # 'LONG' | 'SHORT'
        self.entry_price      = 0.0
        self.entry_rsi        = 0.0
        self.monto_bloqueado  = 0.0             # margen apartado en la operación

    @property
    def saldo_total(self):
        return self.saldo_disponible + self.monto_bloqueado

    def abrir(self, tipo: str, precio: float, rsi: float,
              conn, fecha: datetime, saldo_anterior: float):
        self.tipo_posicion   = tipo
        self.entry_price     = precio
        self.entry_rsi       = rsi
        self.monto_bloqueado = MONTO_OP
        self.saldo_disponible -= MONTO_OP
        self.en_posicion     = True

        operacion = f"{tipo}_OPEN"
        log(f"{operacion} @ ${precio:,.2f}  RSI={rsi:.1f}  "
            f"Saldo disponible: ${self.saldo_disponible:,.2f}",
            Fore.GREEN if tipo == "LONG" else Fore.RED)

        insert_operacion(
            conn, fecha, operacion, precio, rsi,
            pnl_usdt=None, pnl_pct=None,
            saldo_acumulado=self.saldo_disponible,
            saldo_anterior=saldo_anterior,
            razon=f"RSI={rsi:.1f} EMA10<EMA55",
        )
        print_op(operacion, precio, None, self.saldo_disponible,
                 (self.saldo_disponible - saldo_anterior) / saldo_anterior * 100, "")

    def cerrar(self, precio_salida: float, rsi: float,
               conn, fecha: datetime, razon: str):
        tipo = self.tipo_posicion

        if tipo == "LONG":
            pnl_pct_precio = (precio_salida - self.entry_price) / self.entry_price
        else:
            pnl_pct_precio = (self.entry_price - precio_salida) / self.entry_price

        pnl_usdt   = self.monto_bloqueado * LEVERAGE * pnl_pct_precio
        pnl_pct_op = pnl_pct_precio * LEVERAGE * 100

        saldo_antes = self.saldo_disponible
        # Devolver margen + ganancia/pérdida
        self.saldo_disponible += self.monto_bloqueado + pnl_usdt
        self.saldo_disponible  = max(self.saldo_disponible, 0.0)  # no negativo
        self.monto_bloqueado   = 0.0
        self.en_posicion       = False
        variacion = ((self.saldo_disponible - saldo_antes) / saldo_antes * 100) if saldo_antes else 0

        operacion = f"{tipo}_CLOSE"
        log(f"{operacion} @ ${precio_salida:,.2f}  RSI={rsi:.1f}  "
            f"P&L: ${pnl_usdt:+.2f} ({pnl_pct_op:+.2f}%)  "
            f"Saldo: ${self.saldo_disponible:,.2f}",
            Fore.GREEN if pnl_usdt >= 0 else Fore.RED)

        insert_operacion(
            conn, fecha, operacion, precio_salida, rsi,
            pnl_usdt=pnl_usdt, pnl_pct=pnl_pct_op,
            saldo_acumulado=self.saldo_disponible,
            saldo_anterior=saldo_antes,
            razon=razon,
        )
        print_op(operacion, precio_salida, pnl_usdt,
                 self.saldo_disponible, variacion, razon)

        self.tipo_posicion = None


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    print_header()

    conn = get_conn()
    create_table(conn)
    log("Tabla lista.", Fore.GREEN)

    # Recuperar saldo desde la BD (para reanudar si se reinicia)
    saldo_previo = get_last_balance(conn)
    cuenta = Cuenta(saldo_previo)
    log(f"Saldo inicial cargado: ${cuenta.saldo_disponible:,.2f}", Fore.CYAN)

    # Cargar velas históricas para inicializar indicadores
    log(f"Cargando {LOOKBACK} velas históricas ({SYMBOL} {TIMEFRAME})...")
    df = fetch_candles(LOOKBACK)
    df = calc_indicators(df)
    log(f"{len(df)} velas cargadas. Esperando cierre de vela...", Fore.GREEN)

    while _running:
        # Usar la vela cerrada (antepenúltima para evitar vela en formación)
        row  = df.iloc[-2]
        rsi  = float(row["rsi"])
        ema10= float(row["ema10"])
        ema55= float(row["ema55"])
        precio = float(row["close"])
        fecha  = row["open_time"].to_pydatetime().replace(tzinfo=None)

        print_candle(row, rsi, ema10, ema55)

        saldo_antes = cuenta.saldo_disponible

        # ── Gestión de posición abierta ───────────────────────────────────
        if cuenta.en_posicion:
            entry = cuenta.entry_price
            tipo  = cuenta.tipo_posicion

            if tipo == "SHORT":
                up   = (precio - entry) / entry   # sube → pérdida para SHORT
                down = (entry - precio) / entry   # baja → ganancia para SHORT
                if up >= SL_PCT:
                    cuenta.cerrar(entry * (1 + SL_PCT), rsi, conn, fecha, "Stop Loss")
                elif down >= TP_PCT:
                    cuenta.cerrar(precio, rsi, conn, fecha, f"Take Profit {TP_PCT*100:.0f}%")
                elif rsi >= RSI_EXIT_LONG:
                    cuenta.cerrar(precio, rsi, conn, fecha, f"RSI≥{RSI_EXIT_LONG}")

        # ── Señales de entrada ────────────────────────────────────────────
        if not cuenta.en_posicion:

            # SHORT: RSI entre 20-35 y EMA10 < EMA55 (condición antigua de LONG invertida)
            if 20 <= rsi <= RSI_LONG_MAX and row["ema10_below"]:
                if cuenta.saldo_disponible >= MONTO_OP:
                    cuenta.abrir("SHORT", precio, rsi, conn, fecha, saldo_antes)
                else:
                    log(f"Saldo insuficiente para abrir SHORT (${cuenta.saldo_disponible:.2f})", Fore.YELLOW)

            else:
                log(f"Sin señal  |  RSI={rsi:.1f}  EMA10={ema10:.2f}  EMA55={ema55:.2f}  "
                    f"Saldo: ${cuenta.saldo_disponible:,.2f}", Fore.WHITE)

        # ── Esperar siguiente vela ─────────────────────────────────────────
        if _running:
            log(f"Esperando siguiente vela 1h...", Fore.WHITE + Style.DIM)
            df = wait_next_candle(df)
            df = calc_indicators(df)

    conn.close()
    log("Sistema detenido.", Fore.YELLOW)
    print(f"\n  Saldo final: ${cuenta.saldo_disponible:,.2f}")
    print(f"  Variación vs capital inicial: "
          f"${cuenta.saldo_disponible - CAPITAL_INICIAL:+,.2f} "
          f"({(cuenta.saldo_disponible/CAPITAL_INICIAL - 1)*100:+.2f}%)\n")


if __name__ == "__main__":
    main()
