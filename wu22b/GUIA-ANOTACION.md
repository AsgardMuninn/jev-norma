# Guía de anotación ciega, jev-norma

Paquete generado: 2026-11-12T11:21:55-05:00
Origen: `wu22/gold_multi.jsonl` (sha256 `596e6c134b36f1aff7db543173ca20d3d38f9b6eccb11c98b74ad8d2c4e2b141`)
Cláusulas: 52
Fuentes:
- `logistral-politica-tratamiento-datos`: 26 cláusulas
- `nequi-politica-tratamiento-datos`: 26 cláusulas

## Qué tienes que hacer

Etiquetar las 52 cláusulas del paquete. Hay dos formatos con el mismo contenido:
`packet_blind.csv` (recomendado: se abre en Excel/Sheets) y `packet_blind.jsonl`.
En el CSV llena las columnas `bucket` y `severity` (y `nota` si quieres); en el JSONL
añade esos mismos campos a cada línea. Devuelve el archivo completo, sin borrar filas.

Reglas:

1. Trabaja sólo con el texto. **No abrir el gold** (`gold_multi.jsonl`, `gold.jsonl`,
   `gold_logistral.jsonl`) ni ningún reporte de evaluación: si ves la etiqueta original
   o la predicción del modelo, el acuerdo deja de medir la rúbrica.
2. Decide siempre una de las tres etiquetas. Si dudas, elige la que más se acerque y
   anótalo en `nota`. No dejes celdas vacías.
3. No consultes a la persona que etiquetó el gold. La independencia es el punto.
4. No hace falta conocimiento legal profundo: aplica la rúbrica tal como está. Si la
   rúbrica no alcanza para decidir, esa es información valiosa, anótala.

## Rúbrica v1 (la misma del gold, sin cambios)

- `obligacion`: la cláusula impone al responsable o encargado un deber concreto y
  exigible (obtener autorización, atender derechos, conservar prueba, aplicar medidas
  de seguridad, avisar incidentes).
- `riesgo`: la cláusula describe una práctica que puede lesionar al titular o exponer a
  sanción si no se controla (transferencia a terceros, recolección amplia, videovigilancia,
  retención sin plazo, deslinde de responsabilidad, uso secundario de datos).
- `sin_accion`: información, definición, vigencia, marco normativo, contacto o
  descripción del documento, sin deber ni riesgo operativo.

Fronteras conocidas que la rúbrica v1 todavía no resuelve (decide con tu criterio y
anota la duda, no intentes adivinar la respuesta "oficial"): listas de finalidades,
avisos de videovigilancia y deslindes de responsabilidad sobre terceros.

## Severidad

- `0`: sin obligación ni riesgo (informativa).
- `1`: deber genérico, informativo con carga menor.
- `2`: exigible o sancionable (autorización, derechos del titular, seguridad, transferencias).

## Formato de respuesta

Una línea JSON por cláusula, en el mismo orden:

```json
{"id": "n000", "source": "nequi-politica-tratamiento-datos", "bucket": "sin_accion", "severity": 0, "nota": "opcional"}
```

## Cómo se evalúa

`tools/kappa_report.py` une tu etiquetado con el gold y calcula κ de Cohen global y por
documento, más la matriz de acuerdo. El criterio pre-registrado es κ >= 0.6 para
considerar la rúbrica reproducible; por debajo, el umbral de producción no se mueve.
Si tu archivo no cubre las 52 cláusulas, el reporte lo dice y no se aprueba nada.
