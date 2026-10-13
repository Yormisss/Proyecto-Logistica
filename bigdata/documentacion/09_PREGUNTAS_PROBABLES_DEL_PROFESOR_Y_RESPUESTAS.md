# PREGUNTAS PROBABLES DEL PROFESOR Y CÓMO RESPONDERLAS

## Sobre los datos

**¿Por qué los datos son simulados?**
La app solo trae unos 220 pedidos de demostración; con eso Spark no se justifica. Se generaron 300 mil con el
mismo esquema y errores típicos de un ERP. Los 223 reales entran como una fuente más. Los resultados
demuestran el pipeline, no una operación real.

**¿Dónde están los datos?**
En `bigdata/datalake/raw/` (volumen completo, no va a GitHub) y `bigdata/muestras/` (2.000 filas por
fuente, sí va). Se pueden regenerar con `python bigdata/pipeline.py`; la semilla fija produce siempre los
mismos datos.

**¿Por qué metieron errores a propósito?**
Un ETL sin datos sucios no demuestra nada. Los defectos (duplicados, ciudades mal escritas, nulos, valores
negativos) son los que realmente traen los exports de un ERP.

## Sobre la arquitectura

**¿Por qué Lambda y no Kappa?**
Hay un histórico grande que se reprocesa por lotes y una parte pequeña que necesita inmediatez; es el caso de
Lambda. Kappa trata todo como stream y exige un broker (Kafka) con retención larga.

**¿Por qué capas raw / bronze / silver / gold?**
Si se descubre un error en el ETL, se reprocesa desde `raw` sin volver a pedirles los datos a las fuentes.
Cada capa tiene un nivel de calidad y un consumidor distinto.

**¿Por qué Parquet y no CSV?**
Es columnar (una consulta lee solo las columnas que necesita), comprime, guarda los tipos y permite
particionar por año y mes. El CSV de 55 MB baja a 22 MB.

**¿Por qué no modificaron la app para hacer esto?**
Mezclar consultas analíticas pesadas con la operación diaria ralentiza a los conductores. Se separa OLTP (la
app) de OLAP (este módulo).

**¿Por qué la pantalla no ejecuta Spark?**
Arrancar Spark tarda segundos y consume gigas de memoria; bloquearía el servidor. El pipeline se ejecuta
aparte y la web lee los resultados, igual que Power BI.

## Sobre las herramientas

**¿Por qué Spark y no pandas o MySQL?**
pandas carga todo en la memoria de un solo proceso; una base relacional ejecuta la consulta en un servidor.
Spark divide los datos en particiones y las procesa en paralelo (aquí, los núcleos del PC; en producción, un
clúster) con el mismo código.

**¿Por qué Spark y no MapReduce?**
MapReduce escribe a disco entre cada etapa; Spark mantiene los datos en memoria y expresa los cálculos como
DataFrames. Es el sucesor natural.

**¿Qué es un DataFrame y por qué es perezoso?**
Una tabla distribuida. Spark acumula las transformaciones (`filter`, `groupBy`) en un plan y lo optimiza antes
de ejecutarlo cuando llega una acción (`count`, `write`).

**¿Qué diferencia hay entre ETL y ELT, y por qué usaron los dos?**
ETL limpia antes de guardar (`etl.py`); ELT guarda primero y transforma después con SQL dentro del motor
(`elt_gold.py`). El ETL es para limpiar; el ELT, para agregar con la potencia del motor y en el lenguaje que
conocen los analistas.

**¿Escala?**
El mismo código corre en un clúster cambiando `SPARK_MASTER` y `BIGDATA_LAKE`. Aquí se probó con 300 mil
pedidos en un PC.

## Sobre lo que falta (respóndalo usted antes de que lo pregunten)

**¿Dónde está Hadoop?**
Se usa su formato (Parquet) y sus librerías; no hay clúster HDFS. El lake pasa a HDFS cambiando una variable.

**¿Dónde están Kafka, Hive y MongoDB?**
No se desplegaron. El streaming y el SQL se hicieron con Spark. Los eventos JSON de la app son el caso
natural para MongoDB.

**¿Por qué no hay Power BI?**
Los CSV para el dashboard están listos en `bigdata/resultados/powerbi/`; el dashboard se arma sobre ellos.

## Sobre el modelo predictivo

**¿Por qué el modelo tiene un AUC de 0,62?**
El fallo en los datos tiene un componente aleatorio que ninguna variable explica. El modelo sirve para
**priorizar seguimiento** (detecta ~70 % de los fallos reales, con muchos falsos positivos), no para
descartar pedidos. Está evaluado con el 20 % de los datos que no vio al entrenar.

**¿Qué variables usa y cuál es la más importante?**
Zona, distancia, prioridad, hora planeada, ciudad, conductor, día, items y tasa de fallo previa del conductor.
La **zona** pesa ~55 %, y coincide con la regla que se usó para simular los datos, lo que valida que el
modelo aprende lo correcto.

**¿Por qué no usaron la hora real de entrega?**
Porque es consecuencia del resultado: incluirla sería **fuga de datos** y haría parecer bueno al modelo sin
serlo. Solo se usan variables conocidas antes de despachar.

**¿Por qué calcularon la tasa de fallo del conductor solo con el set de entrenamiento?**
Para que la prueba no vea sus propias respuestas; si se calculara con todos los datos, el modelo estaría
haciendo trampa.
