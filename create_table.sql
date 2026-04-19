CREATE TABLE IF NOT EXISTS eth_binance_trading_v2 (
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

CREATE INDEX IF NOT EXISTS idx_eth_binance_trading_v2_fecha
    ON eth_binance_trading_v2 (fecha);
