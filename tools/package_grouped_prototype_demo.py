#!/usr/bin/env python3
"""Create human and technical audit packages for the grouped prototype demo."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


EXPECTED_REGISTRY = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def zip_files(path: Path, files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, name in files:
            archive.write(source, name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-dir", required=True)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    smoke = Path(args.smoke_dir).resolve()
    snapshot = Path(args.snapshot_dir).resolve()
    output = Path(args.output_dir).resolve()
    human = output / "Paquete_humano"
    technical = output / "Auditoria_tecnica"
    human.mkdir(parents=True, exist_ok=True)
    technical.mkdir(parents=True, exist_ok=True)
    summary = read_json(smoke / "grouped_prototype_run_summary.json")
    if summary.get("status") not in {"prototype_demo_ready_automatic", "prototype_demo_incomplete"}:
        raise ValueError("The source is not a completed grouped prototype run")
    if summary.get("active_registry_sha256_after") != EXPECTED_REGISTRY:
        raise ValueError("Active registry hash is not the protected baseline")

    human_files = {
        "Reporte_HEAL_Prototipo.docx": smoke / "HEAL_prototipo_desarrollo.docx",
        "Reporte_HEAL_Prototipo.pdf": smoke / "HEAL_prototipo_desarrollo.pdf",
        "Tarjetas_LLM1.csv": smoke / "cards.csv",
        "Cobertura_180_grupos.csv": smoke / "coverage.csv",
        "Telemetria_y_costos.csv": smoke / "telemetry_costs.csv",
        "Snapshot_Tier1_105_Firmado.csv": snapshot / "tier1_prototype_snapshot_groups.csv",
    }
    for name, source in human_files.items():
        copy(source, human / name)

    covered = summary["counts"]["scientifically_covered"]
    canonical = summary["counts"]["canonical_groups"]
    not_covered = summary["counts"]["not_covered"]
    valid = summary["counts"]["valid_llm1_cards"]
    quarantined = summary["counts"]["quarantined"]
    calls = summary["telemetry"]["calls"]
    guide = f"""# Guía breve de lectura — prototipo HEAL

## Qué demuestra

Este paquete demuestra el recorrido agrupado LLM1 → LLM2 → reporte sobre un VCF autorizado ya normalizado por HEAL. Es un **prototipo de desarrollo**, no una validación clínica.

## Orden recomendado

1. Abra `Reporte_HEAL_Prototipo.pdf` para ver la experiencia de una familia.
2. Abra `HEAL_Prototipo_Tier1_105_Auditoria.xlsx` y lea primero `RESUMEN`.
3. En `TARJETAS`, filtre `status=valid` para revisar las {valid} tarjetas generadas por Luna.
4. En `COBERTURA`, compare los {covered} grupos cubiertos con los {not_covered} no cubiertos, sobre {canonical} grupos canónicos.
5. En `TELEMETRIA`, revise tokens, latencia y costo de las {calls} llamadas registradas.

## Qué buscar

- Ningún grupo fuera del snapshot firmado de {covered} debe contener una interpretación.
- Una ausencia en el VCF debe figurar como callability desconocida, nunca como homocigosis de referencia.
- No debe haber diagnóstico, indicación terapéutica, suplementación ni farmacogenómica accionable.
- LLM2 sólo puede resumir grupos, variantes y evidencia ya validados por LLM1.

## Estado

- `prototype_readiness`: `{summary['status']}`.
- `formal_validation_readiness`: `pending_new_unseen_holdout`.
- Tarjetas en cuarentena: `{quarantined}`.
- Registro productivo: preservado e intacto.
"""
    (human / "GUIA_DE_LECTURA.md").write_text(guide, encoding="utf-8")

    cards = read_csv(smoke / "cards.csv")
    coverage = read_csv(smoke / "coverage.csv")
    telemetry = read_csv(smoke / "telemetry_costs.csv")
    quarantine = read_json(smoke / "quarantine.json")
    snapshot_groups = read_csv(snapshot / "tier1_prototype_snapshot_groups.csv")
    card_columns = (
        "group_id", "coverage_status", "status", "inference_mode",
        "final_confidence_level", "review_priority", "focus_variant_refs",
        "interpretation_one_sentence_es", "evidence_limitations",
        "eligible_for_llm2", "scientific_inference_ceiling", "effective_runtime_ceiling",
    )
    compact_cards = [
        {column: row.get(column, "") for column in card_columns}
        for row in cards
    ]
    workbook_data = {
        "RESUMEN": [
            {"indicador": "Estado del prototipo", "valor": summary["status"], "detalle": "Resultado automático del smoke"},
            {"indicador": "Validación formal", "valor": summary["formal_validation_readiness"], "detalle": "Requiere nuevo holdout unseen"},
            {"indicador": "Grupos canónicos", "valor": summary["counts"]["canonical_groups"], "detalle": "Manifest cerrado"},
            {"indicador": "Cobertura científica", "valor": covered, "detalle": f"Snapshot sandbox firmado ({covered}/{canonical})"},
            {"indicador": "Con variante observada", "valor": summary["counts"]["covered_with_observed_variant"], "detalle": "Grupos cubiertos que habilitaron evaluación LLM1"},
            {"indicador": "Sin variante observada", "valor": summary["counts"]["covered_no_observed_variant"], "detalle": "Tarjeta determinística, sin llamada LLM"},
            {"indicador": "Tarjetas Luna válidas", "valor": valid, "detalle": "Disponibles para LLM2"},
            {"indicador": "Tarjetas en cuarentena", "valor": quarantined, "detalle": "No alimentan LLM2"},
            {"indicador": "Grupos no cubiertos", "valor": summary["counts"]["not_covered"], "detalle": "Sin fallback al registry activo"},
            {"indicador": "Costo estimado", "valor": summary["telemetry"]["estimated_cost_usd"], "detalle": f"USD, {calls} llamadas registradas"},
            {"indicador": "Registry SHA-256", "valor": summary["active_registry_sha256_after"], "detalle": "Intacto antes y después"},
        ],
        "TARJETAS": compact_cards,
        "COBERTURA": coverage,
        "CUARENTENAS": quarantine,
        "SNAPSHOT_TIER1": snapshot_groups,
        "TELEMETRIA": telemetry,
        "HASHES": [
            {"artefacto": "Active registry antes", "sha256": summary["active_registry_sha256_before"], "estado": "intacto"},
            {"artefacto": "Active registry después", "sha256": summary["active_registry_sha256_after"], "estado": "intacto"},
        ],
    }
    data_path = output / "workbook_data.json"
    data_path.write_text(json.dumps(workbook_data, ensure_ascii=False, indent=2), encoding="utf-8")

    technical_names = [
        "llm1_prototype_envelopes.json", "llm1_cards.json", "llm2_grouped_payload_v1.json",
        "grouped_global_interpretation_v1.json", "raw_responses_audit.json", "quarantine.json",
        "report_view_model_v1.json", "telemetry_costs.csv", "cards.csv", "coverage.csv",
        "grouped_prototype_execution_state.json", "grouped_prototype_progress.json",
    ]
    for name in technical_names:
        source = smoke / name
        if source.exists():
            copy(source, technical / name)
    for name in (
        "tier1_prototype_snapshot_v1_human_signed.json", "prototype_candidate_manifest.json",
        "prototype_coverage_manifest_v1.json", "snapshot_manifest.json", "registry_protection.json",
    ):
        source = snapshot / name
        if source.exists():
            copy(source, technical / name)
    sanitized = dict(summary)
    sanitized["outputs"] = {key: Path(value).name for key, value in summary.get("outputs", {}).items()}
    (technical / "grouped_prototype_run_summary.json").write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifests = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name not in {"manifest_sha256.csv", "workbook_data.json"}:
            manifests.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    with (output / "manifest_sha256.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "bytes", "sha256"])
        writer.writeheader(); writer.writerows(manifests)
    copy(output / "manifest_sha256.csv", human / "MANIFEST_SHA256.csv")
    copy(output / "manifest_sha256.csv", technical / "MANIFEST_SHA256.csv")
    print(json.dumps({"status": "package_sources_ready", "output": str(output), "workbook_data": str(data_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
