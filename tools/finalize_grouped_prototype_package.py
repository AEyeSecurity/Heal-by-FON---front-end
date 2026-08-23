#!/usr/bin/env python3
"""Seal the grouped-prototype human and technical deliverables.

The human archive deliberately excludes JSON/NDJSON and QA renderings.  The
technical archive contains the structured audit trail but is scanned for the
configured API secret and authorization headers before it is written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
import zipfile


MANIFEST_NAME = "MANIFEST_SHA256.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(directory: Path) -> Path:
    target = directory / MANIFEST_NAME
    rows = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path == target or path.suffix.lower() == ".zip":
            continue
        rows.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("path", "bytes", "sha256"))
        writer.writeheader()
        writer.writerows(rows)
    return target


def assert_no_secrets(paths: list[Path]) -> None:
    api_key = os.environ.get("HEAL_OPENAI_API_KEY", "").encode("utf-8")
    forbidden = (b"authorization: bearer", b'"authorization"', b"'authorization'")
    for path in paths:
        data = path.read_bytes()
        lowered = data.lower()
        if api_key and len(api_key) >= 16 and api_key in data:
            raise RuntimeError(f"API secret found in audit artifact: {path}")
        if any(marker in lowered for marker in forbidden):
            raise RuntimeError(f"Authorization header found in audit artifact: {path}")


def zip_directory(directory: Path, target: Path, *, reject_json: bool) -> None:
    files = [path for path in sorted(directory.rglob("*")) if path.is_file()]
    if reject_json:
        blocked = [path for path in files if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}]
        if blocked:
            raise RuntimeError(f"Human archive would contain JSON: {blocked[0]}")
    assert_no_secrets(files)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(directory).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True)
    args = parser.parse_args()
    package = Path(args.package_dir).resolve()
    human = package / "Paquete_humano"
    technical = package / "Auditoria_tecnica"
    if not human.is_dir() or not technical.is_dir():
        raise FileNotFoundError("Expected Paquete_humano and Auditoria_tecnica")

    # Inspection output is useful locally but is not part of the distributable.
    for inspect_file in human.glob("*.inspect.ndjson"):
        inspect_file.unlink()

    human_manifest = write_manifest(human)
    technical_manifest = write_manifest(technical)
    human_zip = package / "HEAL_Prototipo_Paquete_Humano.zip"
    technical_zip = package / "HEAL_Prototipo_Auditoria_Tecnica.zip"
    zip_directory(human, human_zip, reject_json=True)
    zip_directory(technical, technical_zip, reject_json=False)

    package_manifest = write_manifest(package)
    print(
        f"sealed human={human_zip} technical={technical_zip} "
        f"human_manifest={human_manifest} technical_manifest={technical_manifest} "
        f"package_manifest={package_manifest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
