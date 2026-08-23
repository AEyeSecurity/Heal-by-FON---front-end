#!/usr/bin/env python3
"""Create human and technical ZIPs for the candidate-1 audit without secrets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


HUMAN_EXTENSIONS = {".xlsx", ".docx", ".pdf", ".csv", ".txt"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(root: Path, output: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.resolve() != output.resolve())
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["ruta_relativa", "sha256", "tamano_bytes"])
        writer.writeheader()
        for path in files:
            writer.writerow({"ruta_relativa": path.relative_to(root).as_posix(), "sha256": sha256(path), "tamano_bytes": path.stat().st_size})


def zip_tree(root: Path, output: Path, *, allowed_extensions: set[str] | None = None) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if allowed_extensions is not None and path.suffix.lower() not in allowed_extensions:
                continue
            archive.write(path, path.relative_to(root.parent))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--human-dir", required=True)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--reconciliation-root", required=True)
    parser.add_argument("--service-root", required=True)
    parser.add_argument("--technical-dir", required=True)
    parser.add_argument("--human-zip", required=True)
    parser.add_argument("--technical-zip", required=True)
    parser.add_argument("--refresh-human-only", action="store_true")
    args = parser.parse_args()

    human = Path(args.human_dir).resolve()
    campaign = Path(args.campaign_root).resolve()
    reconciliation = Path(args.reconciliation_root).resolve()
    service = Path(args.service_root).resolve()
    technical = Path(args.technical_dir).resolve()
    human_zip = Path(args.human_zip).resolve()
    technical_zip = Path(args.technical_zip).resolve()
    if args.refresh_human_only:
        write_manifest(human, human / "MANIFEST_SHA256.csv")
        zip_tree(human, human_zip, allowed_extensions=HUMAN_EXTENSIONS)
        print(json.dumps({
            "status": "human_package_refreshed", "human_zip": str(human_zip),
            "human_zip_sha256": sha256(human_zip),
            "human_json_files": [str(path) for path in human.rglob("*.json")],
        }, ensure_ascii=False))
        return 0
    if technical.exists() or human_zip.exists() or technical_zip.exists():
        raise FileExistsError("Refusing to overwrite an existing package or technical audit directory")

    intermediate_dir = technical / "report_build_intermediates"
    technical.mkdir(parents=True)
    intermediate_dir.mkdir(parents=True)
    for name in ("workbook_data.json", "LLM1_Tier1_Sol_candidate-1_Auditoria.xlsx.inspect.ndjson"):
        source = human / name
        if source.exists():
            shutil.copy2(source, intermediate_dir / name)
            source.unlink()

    shutil.copytree(campaign, technical / "campaign")
    shutil.copytree(reconciliation / "evidence", technical / "evidence")
    shutil.copytree(reconciliation / "gold-v4", technical / "gold-v4")
    shutil.copytree(reconciliation / "pubmed-snapshot", technical / "pubmed-snapshot")
    shutil.copy2(reconciliation / "qa_pmid_title_doi.csv", technical / "qa_pmid_title_doi.csv")

    implementation = technical / "implementation"
    implementation.mkdir()
    for name in (
        "mechanism_curation_v2.schema.json", "run_curation_v2.py", "curation_v2.py",
        "prompt_mechanism_curator_v2.md", "prompt_mechanism_critic_v2.md",
        "prompt_mechanism_arbiter_v2.md", "prompt_optimizer_v2.md",
    ):
        shutil.copy2(service / name, implementation / name)

    human_exports = technical / "human_exports"
    human_exports.mkdir()
    for path in human.iterdir():
        if path.is_file() and path.suffix.lower() in {".csv", ".txt"}:
            shutil.copy2(path, human_exports / path.name)

    (human / "LEEME.txt").write_text(
        "LLM1 Tier 1 - Evaluacion Sol candidate-1\n\n"
        "Estado: la ejecucion se detuvo antes de calibration por HTTP 429 insufficient_quota.\n"
        "No hay resultados cientificos de Sol ni holdout ejecutado.\n\n"
        "Lectura recomendada:\n"
        "1. Abrir el PDF para el resumen y los proximos pasos.\n"
        "2. Abrir el XLSX para revisar Gold, fases, fuentes, telemetria y hashes.\n"
        "3. Usar los CSV si se necesita filtrar o importar los datos.\n\n"
        "El ZIP compartible no contiene JSON. La auditoria tecnica se entrega en un ZIP separado.\n",
        encoding="utf-8",
    )
    (technical / "README_AUDITORIA_TECNICA.txt").write_text(
        "Esta auditoria corresponde a un run detenido en preflight.\n"
        "No existen raw Responses API exitosas ni decisiones curator/critic/arbiter porque la API rechazo ambos intentos antes de generar una Response.\n"
        "Los errores HTTP, estados terminales, Gold, packets, schemas, prompts y snapshots utilizados se conservan aqui.\n"
        "No se incluyeron API keys, headers Authorization ni archivos de configuracion con secretos.\n",
        encoding="utf-8",
    )

    write_manifest(human, human / "MANIFEST_SHA256.csv")
    write_manifest(technical, technical / "MANIFEST_SHA256.csv")
    zip_tree(human, human_zip, allowed_extensions=HUMAN_EXTENSIONS)
    zip_tree(technical, technical_zip)

    result = {
        "status": "packaged", "human_zip": str(human_zip), "technical_zip": str(technical_zip),
        "human_zip_sha256": sha256(human_zip), "technical_zip_sha256": sha256(technical_zip),
        "human_json_files": [str(path) for path in human.rglob("*.json")],
        "technical_files": sum(1 for path in technical.rglob("*") if path.is_file()),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
