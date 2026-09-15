-- Consultas frecuentes sobre la base de datos del SGDS.
--
-- Uso:  sqlite3 -header -box instance/logistica.db < consultas.sql
-- O abrir la consola:  sqlite3 instance/logistica.db
-- y pegar la consulta que interese.

.headers on
.mode box

SELECT '--- Usuarios y roles ---' AS '';
SELECT id, nombre, correo, rol, activo FROM usuarios;

SELECT '--- Inventario y alertas de minimo ---' AS '';
SELECT sku, nombre, stock_actual, stock_minimo,
       CASE WHEN stock_actual <= stock_minimo THEN 'BAJO' ELSE 'ok' END AS estado
FROM productos ORDER BY stock_actual;

SELECT '--- Clientes y sus puntos de entrega ---' AS '';
SELECT c.nombre, c.documento AS nit,
       COUNT(DISTINCT d.id) AS sedes,
       COUNT(DISTINCT p.id) AS pedidos,
       CASE WHEN c.usuario_id IS NULL THEN 'no' ELSE 'si' END AS portal
FROM clientes c
LEFT JOIN direcciones_cliente d ON d.cliente_id = c.id
LEFT JOIN pedidos p ON p.cliente_id = c.id
GROUP BY c.id, c.nombre, c.documento, c.usuario_id
ORDER BY pedidos DESC;

SELECT '--- Grado de normalizacion alcanzado ---' AS '';
-- Antes del cambio habia tantas copias de los datos del cliente como pedidos.
SELECT (SELECT COUNT(*) FROM pedidos)  AS pedidos,
       (SELECT COUNT(*) FROM clientes) AS clientes_unicos,
       (SELECT COUNT(*) FROM direcciones_cliente) AS sedes,
       (SELECT COUNT(*) FROM pedidos WHERE cliente_id IS NULL) AS pedidos_sin_vincular;

SELECT '--- Historial de un cliente (portal de seguimiento) ---' AS '';
SELECT p.codigo, p.fecha_despacho, p.estado, p.direccion
FROM pedidos p
JOIN clientes c ON c.id = p.cliente_id
JOIN usuarios u ON u.id = c.usuario_id
WHERE u.rol = 'CLIENTE'
ORDER BY p.fecha_despacho DESC, p.codigo DESC
LIMIT 15;

SELECT '--- Pedidos de hoy con su ruta y conductor ---' AS '';
SELECT p.codigo, p.cliente_nombre, p.estado, p.orden_en_ruta,
       r.codigo AS ruta, u.nombre AS conductor
FROM pedidos p
LEFT JOIN rutas r ON r.id = p.ruta_id
LEFT JOIN usuarios u ON u.id = r.conductor_id
WHERE p.fecha_despacho = date('now')
ORDER BY r.codigo, p.orden_en_ruta;

SELECT '--- KPIs del dia (los tres del RF6) ---' AS '';
SELECT COUNT(*) AS total_dia,
       SUM(estado = 'ENTREGADO') AS entregados,
       SUM(estado IN ('PENDIENTE','ASIGNADO','EN_RUTA')) AS pendientes,
       ROUND(100.0 * SUM(estado = 'ENTREGADO') /
             NULLIF(SUM(estado IN ('ENTREGADO','FALLIDO')), 0), 1) AS pct_exito
FROM pedidos WHERE fecha_despacho = date('now');

SELECT '--- Ultimos movimientos de inventario (RF5) ---' AS '';
SELECT m.registrado_en, pr.sku, m.tipo, m.cantidad, m.stock_resultante,
       pe.codigo AS pedido, u.nombre AS registro
FROM movimientos_inventario m
JOIN productos pr ON pr.id = m.producto_id
LEFT JOIN pedidos pe ON pe.id = m.pedido_id
LEFT JOIN usuarios u ON u.id = m.usuario_id
ORDER BY m.id DESC LIMIT 15;

SELECT '--- Trazabilidad de un pedido (cambie el codigo) ---' AS '';
SELECT e.registrado_en, e.estado_anterior, e.estado_nuevo, e.nota, u.nombre AS usuario
FROM eventos_pedido e
LEFT JOIN usuarios u ON u.id = e.usuario_id
WHERE e.pedido_id = (SELECT id FROM pedidos ORDER BY id DESC LIMIT 1)
ORDER BY e.registrado_en;

SELECT '--- Causas de entrega fallida ---' AS '';
SELECT motivo_fallo, COUNT(*) AS casos
FROM pruebas_entrega WHERE motivo_fallo IS NOT NULL
GROUP BY motivo_fallo ORDER BY casos DESC;

SELECT '--- Tiempos de respuesta medidos (RNF2) ---' AS '';
SELECT endpoint, COUNT(*) AS muestras,
       ROUND(AVG(duracion_ms), 1) AS promedio_ms,
       ROUND(MAX(duracion_ms), 1) AS maximo_ms
FROM mediciones_rendimiento
GROUP BY endpoint ORDER BY maximo_ms DESC LIMIT 10;
