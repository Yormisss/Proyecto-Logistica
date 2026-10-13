# QUÉ ES EL MÓDULO DE BIG DATA Y CÓMO FUNCIONA, EXPLICADO CON UN EJEMPLO

## La idea en una frase

La app guarda lo que pasa en la operación (pedidos, entregas, inventario). El módulo de Big Data
toma esos datos, los **limpia** y los **resume** para responder una pregunta que no se puede contestar
mirando pedidos uno por uno:

> **¿Dónde, cuándo y por qué fallan las entregas?**

## Por qué no se toca la app

La app sigue siendo el sistema que opera el negocio (**OLTP**). Big Data es una capa de análisis
(**OLAP**) que está al lado y solo **lee** lo que la app genera. Si se mezclaran las consultas pesadas
con la operación diaria, la app se volvería lenta para los conductores (el requisito RNF2 pide
respuestas de 3 a 5 segundos).

## El recorrido completo

```
 FUENTES               INGESTA            ETL / ELT           ANÁLISIS            SALIDA
 App (SQLite/MySQL) ┐                                      ┌ Spark SQL (9 tablas) ┐ Pantalla "Big Data"
 ERP (CSV)          ├─► raw ─► bronze ─► silver ─► gold ──┼ MLlib (fallos)       ├ CSV para Power BI
 App móvil (JSON)   │  CSV/JSON  Parquet  limpio   SQL    └ Streaming (ventanas) ┘ JSON de resultados
 WMS almacén (CSV)  ┘
```

## Ejemplo práctico: el pedido `SIM-00014342`

Así llegó, tal cual, del archivo del ERP (el sistema del cliente):

```
SIM-00014342, cliente 1178, ciudad "BOGOTA", ventana 08:00-10:00, estado FALLIDO,
conductor 17, motivo "Cliente ausente", entregado 2026-08-18 08:07
```

Fíjese en que la ciudad viene en mayúsculas: es un defecto que se metió a propósito para que el ETL
tenga algo que corregir.

| Paso | Qué pasa con ese pedido | Para qué sirve |
|---|---|---|
| **1. Fuente** | El pedido nace en un CSV del ERP. Sus cambios de estado llegan como JSON desde el celular del conductor. | Muestra de dónde sale el dato |
| **2. Ingesta, capa `raw`** | El archivo se guarda sin tocarlo | Es la copia original: si algo sale mal, se empieza de nuevo desde aquí |
| **3. Ingesta, capa `bronze`** | Se pasa a Parquet y se anota de qué archivo vino y cuándo se cargó | Poder rastrear el origen de cada fila |
| **4. ETL, capa `silver`** | `"BOGOTA"` se corrige a `"Bogota"`. Se calcula `zona = SUR` por sus coordenadas y `franja` por la hora. Si estuviera duplicado, se elimina | Datos confiables para analizar |
| **5. ELT, capa `gold`** | Este pedido suma +1 intento y +1 fallo a una fila de la tabla resumen `fallos_zona_franja` | Ver el patrón, no el pedido suelto |
| **6. Resultado** | Aparece en la pantalla Big Data, por ejemplo: *"En Occidente de noche falla el 29,5 % de las entregas"* | La decisión de negocio |

Sin Big Data se tienen 300 mil filas sueltas. Con él se ve, por ejemplo, que **Occidente de noche falla
el 29,5 %** frente al 17 % general, y se puede reforzar esa zona.

## Cómo se relaciona con el resto de la app

- La app genera los pedidos reales: `fuente_app.py` los exporta como una fuente más.
- La pantalla **Big Data** (`/admin/bigdata`) solo muestra resultados ya calculados.
- **No** se ejecuta Spark cuando alguien abre la página: Spark tarda segundos y consume gigas de memoria, y
  bloquearía el servidor. El pipeline se ejecuta aparte y la web lee los resultados, igual que Power BI.

## Para entenderlo con sus propias manos

1. Abra `bigdata/muestras/erp_pedidos_muestra.csv` en Excel y busque filas con ciudad `BOGOTA` o ` bogota `.
   Esos son los defectos que arregla el ETL.
2. Abra `bigdata/resultados/powerbi/fallos_zona_franja.csv`. Es el resultado final de esos mismos datos.
