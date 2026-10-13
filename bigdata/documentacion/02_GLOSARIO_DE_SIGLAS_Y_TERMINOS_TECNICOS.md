# GLOSARIO: QUÉ SIGNIFICA CADA SIGLA Y TÉRMINO TÉCNICO

## Datos y arquitectura

| Término | Qué es |
|---|---|
| **Big Data** | Datos tan grandes, rápidos o variados que una sola base de datos no los maneja bien |
| **5V** | Volumen, Velocidad, Variedad, Veracidad (calidad) y Valor: las 5 características de Big Data (semana 1) |
| **OLTP** | La base que opera el negocio día a día. Aquí es la app con MySQL o SQLite. Se optimiza para guardar rápido |
| **OLAP** | La capa que analiza el histórico. Aquí es este módulo. Se optimiza para consultar y resumir |
| **Pipeline** | La cadena de pasos que lleva el dato crudo hasta el resultado |
| **Batch** | Procesar un montón de datos de una vez, por ejemplo el histórico de dos años |
| **Streaming** | Procesar cada dato apenas llega, por ejemplo el evento "entregado" desde el celular |
| **Arquitectura Lambda** | Usar batch y streaming a la vez. **Kappa** usa solo streaming (semana 2) |
| **Data lake** | Una carpeta grande donde se guardan datos de todo tipo, ordenada en capas |
| **Capas raw / bronze / silver / gold** | Crudo, ingerido, limpio y resumido. Es el orden de calidad creciente (arquitectura "medallion") |
| **Linaje** | Saber de qué archivo y de qué fuente vino cada dato |
| **Data warehouse (almacén de datos)** | Tablas de resumen orientadas a consulta, separadas del sistema que opera el negocio. Aquí son las tablas `gold` |

## Procesos

| Término | Qué es |
|---|---|
| **Ingesta** | Traer los datos desde las fuentes al lake |
| **ETL** | *Extract, Transform, Load*. Se limpia el dato **antes** de guardarlo |
| **ELT** | *Extract, Load, Transform*. Se guarda primero y se transforma **después**, usando el motor (aquí, SQL en Spark) |
| **Deduplicar** | Quitar filas repetidas |
| **Imputar** | Rellenar un dato malo o ausente con uno razonable |
| **Mediana** | El valor del medio de una lista ordenada. No se distorsiona con valores extremos, a diferencia del promedio |
| **Particionar** | Dividir los datos por carpetas, como `anio=2025/mes=3/`, para leer solo lo necesario |
| **Normalizar** | Unificar escrituras distintas de lo mismo: `BOGOTA`, `Bogotá` y `Bogota D.C.` pasan a `Bogota` |

## Herramientas

| Término | Qué es |
|---|---|
| **Apache Spark** | Motor que reparte el trabajo entre varios núcleos o máquinas |
| **PySpark** | Spark usado desde Python |
| **DataFrame** | Una tabla que Spark reparte en pedazos (particiones) |
| **Transformación** | Operación como `filter` o `groupBy`. Solo arma un plan, no ejecuta |
| **Acción** | Operación como `count`, `collect` o `write`. Ejecuta el plan acumulado. Por eso se dice que Spark es **perezoso** (*lazy*) |
| **Shuffle** | Redistribuir datos entre particiones, por ejemplo al agrupar. Es lo más costoso de Spark |
| **Caché (`cache`)** | Guardar un resultado en memoria para no recalcularlo cada vez que se use |
| **Spark SQL** | Hacer SQL normal sobre los DataFrames |
| **Parquet** | Formato de archivo por columnas, comprimido y con los tipos guardados. Es mejor que CSV para análisis |
| **CSV / JSON** | Formatos de texto: CSV es una tabla separada por comas; JSON guarda cada registro como un objeto con campos |
| **Hadoop** | Ecosistema de herramientas para datos distribuidos (semana 3) |
| **HDFS** | Sistema de archivos distribuido de Hadoop. No está instalado aquí, pero el código está listo para usarlo |
| **MapReduce** | Modelo de procesamiento original de Hadoop; escribe a disco entre etapas. Spark lo sustituye y es más rápido porque trabaja en memoria |
| **Kafka** | Cola de mensajes para streaming. No está instalado |
| **Structured Streaming** | La parte de Spark que hace streaming |
| **Ventana (de tiempo)** | Agrupar eventos por intervalos, por ejemplo cada 30 segundos |
| **MLlib** | Librería de aprendizaje automático de Spark |
| **Random Forest** | Conjunto de "árboles de decisión" que votan entre sí para clasificar |
| **Winutils** | Ayudante que Spark necesita en Windows para escribir archivos |

## Análisis y modelo

| Término | Qué es |
|---|---|
| **Funciones de ventana (`OVER`)** | Cálculos que miran las filas vecinas sin colapsarlas |
| **`LAG`** | Función de ventana que trae el valor de la fila anterior; sirve para medir el tiempo entre un estado y el anterior |
| **`RANK`** | Función de ventana que numera el puesto de cada fila según un orden |
| **Clasificación ABC** | Los pocos productos que mueven el 80 % de la demanda son **A**, los intermedios **B** y el resto **C** |
| **Percentil 90 (P90)** | El valor por debajo del cual está el 90 % de los casos |
| **AUC** | Nota de 0,5 (puro azar) a 1 (perfecto) de qué tan bien separa el modelo los casos que fallan de los que no |
| **Precisión** | De los pedidos que el modelo marcó como "fallará", qué porcentaje falló de verdad |
| **Recall (sensibilidad)** | De los pedidos que fallaron de verdad, qué porcentaje detectó el modelo |
| **Fuga de datos (*data leakage*)** | Darle al modelo una pista que ya contiene la respuesta, lo que lo hace parecer mejor de lo que es |
| **Peso por clase** | Hacer que los casos poco frecuentes (los fallos) cuenten más al entrenar, para que el modelo no aprenda a decir siempre "entrega" |
| **Semilla (*seed*)** | Número que fija el azar para obtener siempre los mismos resultados |

## Siglas del proyecto

| Sigla | Significado |
|---|---|
| **SGDS** | Sistema de Gestión de Despachos (la app) |
| **ERP** | Sistema de gestión empresarial del cliente, de donde llegan los pedidos |
| **WMS** | Sistema del almacén (*Warehouse Management System*) |
| **SKU** | Código único de cada producto |
| **RF / RNF** | Requisito funcional / no funcional del documento del proyecto |
| **KPI** | Indicador clave de desempeño |
| **PoD** | Prueba de entrega (*Proof of Delivery*) |
