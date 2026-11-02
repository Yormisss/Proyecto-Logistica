-- Verificacion en MySQL Workbench: contrasenas y datos personales de las cuentas.
--
-- Abrir en Workbench: conexion "SGDS Local" -> File -> Open SQL Script... -> este archivo.
-- Ejecutar todo con el primer rayo (Cmd+Shift+Enter) o una consulta a la vez con
-- el cursor dentro de ella y Cmd+Enter. Cada consulta abre su propia pestana de
-- resultados (Result 1, Result 2, ...).

USE logistica;

-- 1. La contrasena de cada usuario, tal como esta en la base: un hash, no la clave.
SELECT id, correo, rol, contrasena_hash
FROM usuarios
ORDER BY rol, correo;

-- 2. Las tres partes del hash: algoritmo $ sal $ resultado.
SELECT correo,
       rol,
       SUBSTRING_INDEX(contrasena_hash, '$', 1)                            AS algoritmo,
       SUBSTRING_INDEX(SUBSTRING_INDEX(contrasena_hash, '$', 2), '$', -1)  AS sal,
       SUBSTRING_INDEX(contrasena_hash, '$', -1)                           AS resultado,
       LENGTH(contrasena_hash)                                             AS longitud,
       CASE WHEN contrasena_hash LIKE 'pbkdf2:sha256:600000$%'
            THEN 'PROTEGIDA' ELSE 'REVISAR' END                            AS estado
FROM usuarios
ORDER BY rol, correo;

-- 3. Resumen por rol. Esperado: sin_proteger = 0 en todos.
SELECT rol,
       COUNT(*)                                       AS total_usuarios,
       SUM(contrasena_hash LIKE 'pbkdf2:sha256:%')     AS protegidas,
       SUM(contrasena_hash NOT LIKE 'pbkdf2:sha256:%') AS sin_proteger
FROM usuarios
GROUP BY rol;

-- 4. Ningun hash se repite, gracias a la sal. Esperado: ninguna fila.
SELECT contrasena_hash, COUNT(*) AS repetidos
FROM usuarios
GROUP BY contrasena_hash
HAVING COUNT(*) > 1;

-- 5. Documento y telefono de cada cuenta, tal como estan en la base.
--    Esperado: textos que empiezan por gAAAAA (Fernet), no el numero real.
--    documento_indice: HMAC-SHA256 de 64 caracteres para buscar sin descifrar.
SELECT correo, rol, documento, telefono, documento_indice
FROM usuarios
ORDER BY rol, correo;

-- 6. Cuentas con algun dato personal sin cifrar. Esperado: en_claro = 0.
SELECT COUNT(*) AS en_claro
FROM usuarios
WHERE (documento IS NOT NULL AND documento NOT LIKE 'gAAAAA%')
   OR (telefono  IS NOT NULL AND telefono  NOT LIKE 'gAAAAA%');
