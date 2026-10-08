-- La caja "Binance USDT Investment" pasa a llamarse solo "Binance USDT" (Cristina, 8 oct 2026). Solo cambia el nombre: ningún monto se toca.
UPDATE cuentas SET nombre = 'Binance USDT' WHERE nombre = 'Binance USDT Investment' AND NOT EXISTS (SELECT 1 FROM cuentas WHERE nombre = 'Binance USDT');
UPDATE pagos SET forma = 'Binance USDT' WHERE forma = 'Binance USDT Investment';
UPDATE pagos SET cuenta = 'Binance USDT' WHERE cuenta = 'Binance USDT Investment';
UPDATE ordenes SET forma_pago_prevista = 'Binance USDT' WHERE forma_pago_prevista = 'Binance USDT Investment';
UPDATE movimientos SET concepto = replace(concepto, 'Binance USDT Investment', 'Binance USDT') WHERE concepto LIKE '%Binance USDT Investment%';
UPDATE movimientos SET notas = replace(notas, 'Binance USDT Investment', 'Binance USDT') WHERE notas LIKE '%Binance USDT Investment%';
