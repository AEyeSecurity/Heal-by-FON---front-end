# LLM1 v7 — estado de implementación productiva

Fecha de control: 2026-08-05  
Cutoff científico: 2026-07-28  
Modelo/perfil preparados: `gpt-5.6-luna` + `luna_v7`

## Estado general

La arquitectura, el contrato v7, el preflight de 180 grupos, las tarjetas bilingües, la cuarentena por grupo y la revisión interna están implementados. La ejecución automática permanece deshabilitada y no se realizaron llamadas productivas a Luna.

Configuración segura actual:

- `HEAL_V2_LLM1_ENABLED=false`
- `HEAL_LLM1_EXECUTION_MODE=disabled`
- `HEAL_LLM1_ACTIVE_TIERS=T1`
- `HEAL_LLM1_ACTIVE_AGE_BANDS=age_0_7`
- `HEAL_LLM1_EXPERIMENTAL_CANARIES=IFNG:T3.5`

## Preflight de los dos VCF autorizados

| Run | Grupos | Tier 1 | Tier 1 clasificados | Elegibles actuales | Resultado |
|---|---:|---:|---:|---:|---|
| `1304f27f-3b62-4cfb-be66-2d9246125cc3` | 180 | 105 | 3 | 3 | Bloqueado |
| `e2985250-a8a5-4874-879d-13541c1ddeb5` | 180 | 105 | 4 | 3 | Bloqueado |

Bloqueos del primer run:

- seis grupos de la preparación histórica no pertenecen al registro persistente actual: `GPX1:T1.1`, `HRH3:T3.1`, `MAOA:T2.1`, `MAOA:T3.2`, `MAOA:T3.4` y `TIMP1:T1.5`;
- Tier 1 no está completamente clasificado.

Bloqueos del segundo run:

- Tier 1 no está completamente clasificado.

Los artifacts nuevos se generaron en carpetas separadas `llm1-preflight-v7-20260805`; los runs y artifacts previos permanecen intactos.

## Candidato de curación Tier 1 (histórico, no usar como estado científico actual)

Se preparó, sin activar, el candidato:

`F:\Heal by FON\data\curation-candidates\llm1-tier1-20260728-v1`

Resumen:

- 105 grupos Tier 1;
- mecanismos: 1 `approved`, 104 `withheld`;
- 1.643 filas GWAS: 4 `approved`, 418 `valid_but_excluded`, 1.221 `rejected`;
- cero estudios posteriores al cutoff y cero estudios nuevos agregados;
- requiere revisión de las decisiones y aprobación interna del snapshot versionado completo antes de subirlo al registro activo; no exige aprobar cada publicación individualmente.

La clasificación conservadora `withheld` no significa que el mecanismo sea falso. Sin embargo, el recuento `1 approved / 104 withheld` de este candidato no fue una evaluación científica de los 105 grupos: 101 estados `draft` fueron convertidos por fallback. Este archivo queda preservado sólo para auditoría y no debe usarse para decisiones nuevas.

La fuente de trabajo posterior es la fundación humana de doce grupos
`tier1-human-review-20260810`, que conserva propuestas `pending` y bloquea
activación hasta que se recreen los paquetes y exista firma humana final.

## Orden de desbloqueo

1. Revisar y aprobar/corregir el candidato Tier 1.
2. Resolver si los seis grupos históricos deben incorporarse al registro o eliminarse de la proyección upstream del primer VCF.
3. Subir los registros aprobados mediante la interfaz autenticada de curación interna.
4. Regenerar el preflight v7 desde la interfaz.
5. Confirmar `180/180`, Tier 1 `105/105` y gates en `pass` para ambos VCF.
6. Recién entonces habilitar conjuntamente `HEAL_V2_LLM1_ENABLED=true` y `HEAL_LLM1_EXECUTION_MODE=internal_auto` para ejecutar Luna.
7. Revisar manualmente todas las tarjetas habilitadas de ambos runs y registrar la decisión interna.

## Verificación técnica

- 81 pruebas automatizadas aprobadas.
- Build de Vite aprobado.
- Sintaxis Node aprobada.
- Dry-run v7: 180/180 en ambos VCF. `LPL:T1.1` se compacta determinísticamente a 23.644 tokens sin eliminar identidades, referencias ni trazabilidad.
- API reiniciada y saludable con la ejecución LLM1 deshabilitada.
