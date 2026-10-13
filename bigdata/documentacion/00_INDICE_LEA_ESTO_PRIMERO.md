# ÍNDICE: QUÉ HAY EN ESTA CARPETA Y EN QUÉ ORDEN LEERLO

Documentación del módulo de Big Data del proyecto de logística (SGDS), pensada para
**entenderlo y sustentarlo**. Cada archivo responde una pregunta.

| # | Archivo | Responde la pregunta |
|---|---|---|
| 01 | [QUE_ES_Y_COMO_FUNCIONA](01_QUE_ES_Y_COMO_FUNCIONA_CON_UN_EJEMPLO.md) | ¿Qué hace este módulo? Se explica siguiendo un pedido real de principio a fin |
| 02 | [GLOSARIO](02_GLOSARIO_DE_SIGLAS_Y_TERMINOS_TECNICOS.md) | ¿Qué significa cada sigla o término técnico (ETL, Parquet, Spark, AUC...)? |
| 03 | [ARCHIVOS](03_PARA_QUE_SIRVE_CADA_ARCHIVO.md) | ¿Para qué sirve cada archivo de la carpeta `bigdata/`? |
| 04 | [INSTALAR](04_QUE_HAY_QUE_INSTALAR_Y_POR_QUE.md) | ¿Qué hay que instalar y por qué? |
| 05 | [RUBRICA](05_RUBRICA_4_1_A_4_9_Y_DONDE_ESTA_CADA_PUNTO.md) | ¿En qué archivo está cada punto del Excel y con qué ejemplo se demuestra? |
| 06 | [DATOS](06_DONDE_ESTAN_LOS_DATOS_SIMULADOS.md) | ¿Dónde están los datos simulados si el profesor los pide? |
| 07 | [CURSO](07_RELACION_CON_LOS_TEMAS_DEL_CURSO.md) | ¿Qué tiene que ver con las semanas 1 a 10, y qué falta? |
| 08 | [COMPAÑEROS](08_COMO_SUBIRLO_AL_REPOSITORIO_Y_CARGARLO_EN_OTRO_PC.md) | ¿Cómo lo subo al repositorio y cómo lo carga mi equipo en la app con Make y Retell? |
| 09 | [PREGUNTAS](09_PREGUNTAS_PROBABLES_DEL_PROFESOR_Y_RESPUESTAS.md) | ¿Qué me van a preguntar y qué respondo? |

Además, en la carpeta superior (`bigdata/`):
- [SUSTENTACION.md](../SUSTENTACION.md): el detalle de **cada decisión técnica**: qué se hizo, por qué, y por qué
  así y no de otra forma (Parquet vs CSV, Spark vs pandas, Lambda vs Kappa, ETL vs ELT...).
- [README.md](../README.md): resumen técnico con diagrama y comandos.
- [INSTALACION_COMPANEROS.md](../INSTALACION_COMPANEROS.md): pasos y problemas frecuentes al cargarlo en otro PC.

## Orden recomendado

1. Lea el **01** para entender la idea, y el **02** para no perderse con las siglas.
2. Ejecute el pipeline y abra la pantalla (comando en el 04, paso final).
3. Estudie el **05** y el **07**: son el mapa entre lo que pide el profesor y lo que hay hecho.
4. Antes de sustentar, repase el **09** y la sección de limitaciones del **07**.

## Una advertencia que vale para todo

Los datos masivos son **simulados** (la app real solo trae ~220 pedidos). Los resultados demuestran
que el pipeline funciona, **no** describen una operación real. Dígalo usted antes de que se lo pregunten.
