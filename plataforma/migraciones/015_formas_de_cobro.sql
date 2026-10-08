-- Las formas en que un cliente puede pagar (Cristina, 8 oct 2026): Efectivo USD, Zelle, Pago Móvil, Mercado Pago,
-- Facebank, Venmo, Pipol Pay, BNC Cashea, Efectivo Euros, Binance USDT, PayPal y Wise.
-- Wise pasa a cobrarse a clientes; Amerant y "Despachador Juan" dejan de salir como forma de pago (las cajas siguen, con su saldo).
UPDATE cuentas SET cobra = 1 WHERE nombre = 'Wise';
UPDATE cuentas SET cobra = 0 WHERE nombre IN ('Amerant', 'Despachador Juan');
INSERT INTO cuentas (codigo, nombre, moneda, tipo, saldo_inicial, activa, orden, cobra)
  SELECT printf('%03d', (SELECT COUNT(*) FROM cuentas) + 1), 'PayPal', 'USD', 'operativa', 0, 1,
         COALESCE((SELECT orden FROM cuentas WHERE nombre = 'Wise'), 100), 1
  WHERE EXISTS (SELECT 1 FROM cuentas WHERE nombre = 'Zelle') AND NOT EXISTS (SELECT 1 FROM cuentas WHERE nombre = 'PayPal');
