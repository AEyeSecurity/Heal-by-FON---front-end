# Prueba manual del prototipo agrupado Tier 1

Estado objetivo: `prototype_ready_for_owner_manual_ui_test`.

Este flujo es un prototipo de desarrollo con cobertura científica firmada para 105 de 180 grupos. La validación formal permanece pendiente de un nuevo holdout unseen.

## Cómo probarlo

1. Abrir `https://healbyfon.aeye.com.ar`.
2. Seleccionar **Análisis superficial — Prototipo agrupado Tier 1**.
3. Elegir un archivo `.vcf` o `.vcf.gz` GRCh38.
4. Completar la verificación de seguridad y enviar el archivo.
5. Mantener la pestaña abierta o volver más tarde: el navegador conserva el Job ID y reanuda el seguimiento del mismo job.
6. Confirmar el avance de las etapas: upload, validación, normalización, VEP/enrichment, matching, LLM1, validación, LLM2 y reporte.
7. Al finalizar, revisar los contadores de cobertura y descargar el DOCX y el PDF.

## Resultado esperado

- La cobertura indica 105 grupos cubiertos y 75 no cubiertos.
- Los grupos no cubiertos aparecen explícitamente como fuera del snapshot; no reciben interpretación ni fallback legacy.
- Sólo las tarjetas válidas llegan a LLM2.
- Una cuarentena legítima puede coexistir con un job completo, siempre que quede aislada y no exista un error estructural.
- El reporte indica que se trata de un prototipo, explica los límites de un VCF sparse y no formula diagnóstico, tratamiento, suplementación ni farmacogenómica accionable.

## Si se actualiza la página

La UI recupera el último Job ID y su token de acceso desde el almacenamiento local del navegador y continúa el polling. Estado, logs, tarjetas y descargas requieren ese token; no son públicos sólo por conocer el Job ID.

## Cuándo reportar un problema

Registrar el Job ID visible si ocurre cualquiera de estos casos:

- una etapa deja de actualizarse durante un período prolongado;
- el job termina sin DOCX o PDF;
- aparece contenido clínico accionable o una afirmación de riesgo individual;
- un grupo fuera de cobertura recibe una interpretación;
- la recarga de la página no recupera el job activo.

No compartir el VCF ni el token de acceso en tickets o capturas. El Job ID es suficiente para la investigación interna.
