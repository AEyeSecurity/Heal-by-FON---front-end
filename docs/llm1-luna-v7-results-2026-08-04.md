# Resultado provisional LLM1 Luna v7 - 04/08/2026

## Resultado

Luna v7 ganó provisionalmente frente a las 25 respuestas almacenadas de GPT-5 mini. Luna superó todas las puertas; mini quedó inelegible por sobrederivación genérica, pese a conservar una puntuación media alta.

| Métrica | GPT-5 mini almacenado | GPT-5.6 Luna + luna_v7 |
|---|---:|---:|
| Salidas | 25 | 25 |
| Puntuación global | 97,1333 | 100,0000 |
| Elegible | No | Sí |
| Errores críticos | 0 | 0 |
| Costo medio por salida | USD 0,00847219 | USD 0,00434140 |
| Latencia p95 | 60,7516 s | 14,0920 s |

El intervalo bootstrap 95% de Luna menos mini fue `[1,4667; 4,7333]`. No fue necesario reejecutar mini porque sólo Luna resultó elegible. El costo y la latencia de mini son históricos y orientativos, no un desempate operacional en la misma ventana.

## Calibración

- Candidata 01: 2/12; predominó sobrederivación.
- Candidata 02: 8/12; fallaron fronteras entre contexto, guía y abstención.
- Candidata 03: 12/12; aprobada.
- Las 36 llamadas de calibración tuvieron un solo intento técnico cada una.
- Hash del prompt aprobado: `005fcbfc992572dd4d257ee55708cdf4962be2033714cf1f4cbd39c862b48e43`.

## Evaluación congelada

- 25/25 respuestas válidas.
- Cero errores automáticos o semánticos.
- Estabilidad 5/5 en los cinco casos.
- PEMT, reservado como holdout, aprobó 5/5.
- Modelo efectivo registrado: `gpt-5.6-luna`.
- Sin retries: 25 llamadas con un solo intento.
- Tokens: 299.735 de entrada y 40.490 de salida.
- Costo total Luna: USD 0,108535.

## Activación

El runtime quedó saludable con:

- `HEAL_LLM1_MODEL=gpt-5.6-luna`
- `HEAL_LLM1_PROMPT_PROFILE=luna_v7`
- `HEAL_LLM1_REASONING_EFFORT=low`

La ventana está en `collecting`. El piloto v2 sigue deshabilitado, por lo que aún no comenzaron a acumularse las 50 salidas reales. Un error crítico obliga a rollback a mini + `default_v6` + `low`, cuarentena y regeneración.

## Límites

El resultado es un silver benchmark provisional autoevaluado. No reemplaza validación genética, bioinformática o clínica independiente. La segunda ronda debe incorporar un caso farmacogenómico positivo y ampliar genes, módulos y tipos de evidencia.
