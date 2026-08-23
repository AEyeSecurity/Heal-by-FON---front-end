# LLM1 silver benchmark provisional autoevaluado

Este flujo compara `gpt-5-mini`, `gpt-5.6-luna` y `gpt-5.6-terra` sin cambiar el prompt entre modelos. El corte científico es 2026-07-28 y la curación no admite publicaciones nuevas.

## Estado y aprobación manual

La curación produce registros candidatos; no reemplaza automáticamente los registros canónicos. Antes de habilitar el benchmark, la persona responsable aprueba o corrige solo las cinco fichas de conducta en `manual_validation.md`. Después de esa aprobación se copian de forma explícita los registros candidatos al registro canónico, se marca `approved_for_pilot=true` en el manifiesto y se regeneran los cinco payloads con `execution_mode=pilot` y `llm1_pilot_ready=true`.

La falta de aprobación bloquea `prepare`/`run`; no se observa primero la salida de ningún modelo.

## Curación reproducible

```powershell
python tools/curate_llm1_silver_evidence.py `
  --mechanisms <mechanism_registry_v1.csv> `
  --gwas-registry <gwas_module_relevance_registry_v1.csv> `
  --gwas-clusters <gwas_evidence_clusters.csv> `
  --publications <publication_evidence.csv> `
  --output-dir <directorio-candidato-nuevo>
```

Se revisan cinco mecanismos y exactamente 41 clusters GWAS de los cinco pilotos. Las demás filas globales quedan intactas. `withheld` es una decisión resuelta y permite contexto o abstención, pero su mecanismo no se expone al modelo.

## Preparación y ejecución

1. Completar un snapshot de precios desde la página oficial vigente usando `config/llm1-benchmark-pricing.example.json`.
2. Congelar el bundle:

```powershell
python tools/run_llm1_silver_benchmark.py prepare --payloads <payloads-v6-aprobados.jsonl> --output-dir <bundle-nuevo>
```

3. Aprobar las cinco fichas dentro del bundle (`approval_status`, responsable y fecha).
4. Probar los tres alias sin datos reales:

```powershell
python tools/run_llm1_silver_benchmark.py probe --output <model-probe.json>
```

5. Ejecutar las 75 llamadas en una única ventana:

```powershell
python tools/run_llm1_silver_benchmark.py run --bundle <bundle> --pricing <pricing-snapshot.json> --output-dir <run-nuevo>
```

El probe del 04/08/2026 confirmó que `gpt-5-mini` rechaza `reasoning.effort=none`; Luna y Terra lo admiten. Por eso el runner falla cerrado con la política original `planned_none` y ofrece dos políticas explícitas: `common_low` (misma configuración para los tres, recomendada para comparabilidad) y `minimum_supported` (`minimal` para mini, `none` para Luna/Terra). La política debe aprobarse en `approved_reasoning_policy` antes del benchmark; `run` rechaza una política distinta de la congelada.

El runner usa Responses API, JSON Schema estricto, un retry, `store=false`, 5 repeticiones por caso/modelo y registra la política de razonamiento, el `response.model` efectivo, tokens, costo, latencia, hashes y salidas crudas.

## Dos pases ciegos y decisión

Se completan `score_pass_1.csv` y `score_pass_2.csv` en sus órdenes distintos. Luego:

```powershell
python tools/run_llm1_silver_benchmark.py score --run-dir <run>
```

Si una respuesta difiere más de 5 puntos totales o más de un nivel en alguna dimensión, se completa `score_adjudication.csv` y se repite `score`. Solo después del lock:

```powershell
python tools/run_llm1_silver_benchmark.py reveal --run-dir <run>
```

La decisión aplica las puertas 25/25, cero errores críticos, media ≥85, caso ≥75, estabilidad 4/5 y las puertas de sobrederivación/sobreabstención. Una victoria de calidad exige diferencia ≥5 e intervalo bootstrap 95% positivo; en el grupo equivalente se exige 10% de mejora de costo o p95.

## Activación vigilada

`tools/llm1_model_activation_guard.py` solo admite Luna o Terra como candidato. Cambia atómicamente modelo, perfil de prompt y esfuerzo (`HEAL_LLM1_MODEL`, `HEAL_LLM1_PROMPT_PROFILE`, `HEAL_LLM1_REASONING_EFFORT`) sin imprimir secretos. El guard exige un match estrecho del proceso, reinicia únicamente HEAL y no declara éxito hasta que `/api/health` confirma el contrato efectivo. Conserva salidas hasta alcanzar 50 válidas y al menos 5 por eje. Un error crítico revierte a `gpt-5-mini` + `default_v6` + `low`, mueve las salidas candidatas a cuarentena y crea una cola de regeneración con mini.

No existe un caso PGx positivo en esta ronda. Debe agregarse en la segunda ronda antes de validar escalamiento farmacogenómico accionable.

## Optimización Luna v7

El perfil `luna_v7` separa tres decisiones: contenido útil, inferencia individual y necesidad real de revisión. Es compatible exclusivamente con `gpt-5.6-luna`; cualquier combinación incompatible falla antes de llamar al modelo.

La calibración se ejecuta con:

```powershell
python tools/run_llm1_luna_prompt_optimization.py calibrate --bundle <bundle-aprobado> --pricing <pricing.json> --output-root <calibration-root> --candidate candidate-01
```

Cada candidata usa 12 llamadas (tres repeticiones de cuatro casos), `common_low`, un retry exclusivamente técnico y cero correcciones semánticas. El runner detiene nuevas candidatas si cinco versiones consecutivas comparten la misma causa dominante. El prompt falla el preflight si contiene genes o IDs de los pilotos.

Una candidata 12/12 habilita la evaluación congelada de 25 llamadas:

```powershell
python tools/run_llm1_luna_prompt_optimization.py evaluate --bundle <bundle-aprobado> --pricing <pricing.json> --calibration-report <calibration_report.json> --output-dir <evaluation-run>
```

El artefacto conserva snapshots y hashes de prompt, schema, payload bundle, precios y configuración. La comparación vuelve a puntuar mini desde sus salidas crudas almacenadas y trata `none`/`optional_contextual` como una misma banda segura:

```powershell
python tools/run_llm1_luna_prompt_optimization.py compare --luna-run <evaluation-run> --mini-run <stored-mini-run> --bundle <bundle-aprobado> --output <comparison_report.json>
```

La activación del 04/08/2026 quedó en vigilancia con `gpt-5.6-luna`, `luna_v7` y esfuerzo `low`. El piloto v2 continúa deshabilitado; habilitar tráfico real es una decisión operativa independiente.
