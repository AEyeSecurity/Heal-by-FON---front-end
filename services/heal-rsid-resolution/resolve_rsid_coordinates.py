#!/usr/bin/env python3
"""Resolve HEAL rsID master coordinates with Ensembl REST."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


ENSEMBL_SERVER = "https://rest.ensembl.org"
SPECIES = "homo_sapiens"
DEFAULT_BATCH_SIZE = 50


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def parse_first_mapping(variant_payload: dict) -> dict:
    mappings = variant_payload.get("mappings", []) or []
    if not mappings:
        return {
            "assembly_name": "",
            "chrom": "",
            "pos": "",
            "end": "",
            "allele_string": "",
            "ref": "",
            "alt": "",
            "api_note": "No mappings returned",
        }

    mappings_sorted = sorted(mappings, key=lambda item: 0 if item.get("coord_system") == "chromosome" else 1)
    mapping = mappings_sorted[0]
    allele_string = mapping.get("allele_string") or ""
    ref = ""
    alt = ""
    if "/" in allele_string:
        parts = allele_string.split("/")
        if len(parts) >= 2:
            ref = parts[0]
            alt = ",".join(parts[1:])

    return {
        "assembly_name": mapping.get("assembly_name") or "",
        "chrom": mapping.get("seq_region_name") or "",
        "pos": mapping.get("start") or "",
        "end": mapping.get("end") or "",
        "allele_string": allele_string,
        "ref": ref,
        "alt": alt,
        "api_note": "",
    }


def request_ensembl(batch: list[str], retries: int = 3, timeout: int = 60) -> dict:
    url = f"{ENSEMBL_SERVER}/variation/{SPECIES}"
    payload = json.dumps({"ids": batch}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"Ensembl request failed: {last_error}")


def normalize_chrom_for_vcf(chrom_value: str) -> str:
    chrom = str(chrom_value or "").strip().upper()
    if not chrom:
        return ""
    if chrom.startswith("CHR"):
        chrom = chrom[3:]
    if chrom == "M":
        chrom = "MT"
    return f"chr{chrom}"


def normalize_alt(value: str) -> str:
    return str(value or "").strip().upper()


def process(input_path: Path, output_dir: Path, cache_dir: Path, batch_size: int) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_path)
    if not rows:
        raise ValueError("rsid_master.csv is empty.")
    rsids = sorted({str(row.get("rsid", "")).strip().lower() for row in rows if str(row.get("rsid", "")).startswith("rs")})
    if not rsids:
        raise ValueError("No rsIDs found in rsid_master.csv.")

    results: dict[str, dict] = {}
    cache_hits = 0
    api_requests = 0
    api_errors: list[str] = []

    for batch in chunks(rsids, batch_size):
        missing = []
        for rsid in batch:
            cache_path = cache_dir / f"{rsid}.json"
            if cache_path.exists():
                results[rsid] = json.loads(cache_path.read_text(encoding="utf-8"))
                cache_hits += 1
            else:
                missing.append(rsid)
        if not missing:
            continue
        try:
            response = request_ensembl(missing)
            api_requests += 1
        except Exception as exc:  # noqa: BLE001
            api_errors.append(str(exc))
            for rsid in missing:
                results[rsid] = {"api_status": "error", "api_note": str(exc)}
            continue
        for rsid in missing:
            item = response.get(rsid)
            if not item:
                resolved = {"api_status": "not_found", "api_note": "rsID not returned by Ensembl"}
            else:
                resolved = parse_first_mapping(item)
                resolved["api_status"] = "ok" if resolved.get("chrom") and resolved.get("pos") else "warning"
            results[rsid] = resolved
            (cache_dir / f"{rsid}.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(0.2)

    output_rows = []
    for row in rows:
        out = dict(row)
        rsid = str(out.get("rsid", "")).strip().lower()
        resolved = results.get(rsid, {})
        for key in ["assembly_name", "chrom", "pos", "end", "ref", "alt", "allele_string", "api_status", "api_note"]:
            out[key] = resolved.get(key, out.get(key, ""))
        chrom_match = normalize_chrom_for_vcf(out.get("chrom", ""))
        pos_match = str(out.get("pos", "")).strip()
        ref_match = normalize_alt(out.get("ref", ""))
        alt_match = normalize_alt(out.get("alt", ""))
        out["chrom_match"] = chrom_match
        out["pos_match"] = pos_match
        out["end_match"] = str(out.get("end", "")).strip()
        out["ref_match"] = ref_match
        out["alt_match"] = alt_match
        out["match_key_chr_pos"] = f"{chrom_match}:{pos_match}" if chrom_match and pos_match else ""
        out["match_key_full"] = f"{chrom_match}|{pos_match}|{ref_match}|{alt_match}" if chrom_match and pos_match and ref_match and alt_match else ""
        out["has_chr_pos"] = "true" if out["match_key_chr_pos"] else "false"
        out["has_full_match_fields"] = "true" if out["match_key_full"] else "false"
        output_rows.append(out)

    fieldnames = [
        "rsid",
        "n_source_rows",
        "source_rows",
        "source_tables",
        "categories",
        "genes",
        "assembly_name",
        "chrom",
        "pos",
        "end",
        "ref",
        "alt",
        "allele_string",
        "api_status",
        "api_note",
        "chrom_match",
        "pos_match",
        "end_match",
        "ref_match",
        "alt_match",
        "match_key_chr_pos",
        "match_key_full",
        "has_chr_pos",
        "has_full_match_fields",
    ]
    write_csv(output_dir / "rsid_match_ready.csv", output_rows, fieldnames)

    status_counts: dict[str, int] = {}
    for row in output_rows:
        status = row.get("api_status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    summary = {
        "status": "warning" if api_errors else "valid",
        "errors": [],
        "warnings": api_errors,
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "metadata": {
            "rsids_total": len(rsids),
            "rows_total": len(rows),
            "with_chr_pos": sum(1 for row in output_rows if row["has_chr_pos"] == "true"),
            "with_full_match_fields": sum(1 for row in output_rows if row["has_full_match_fields"] == "true"),
            "api_status_counts": status_counts,
            "cache_hits": cache_hits,
            "api_requests": api_requests,
        },
        "outputs": {"rsidMatchReadyCsv": str(output_dir / "rsid_match_ready.csv")},
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "rsid_resolution_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve rsID master coordinates.")
    parser.add_argument("--input", help="Input rsid_master.csv.")
    parser.add_argument("--output-dir", help="Output directory.")
    data_root = os.environ.get("HEAL_DATA_ROOT", "").strip()
    default_cache_dir = (
        Path(data_root) / "legacy-rsid" / "cache"
        if data_root
        else Path(__file__).resolve().parent / "cache"
    )
    parser.add_argument("--cache-dir", default=str(default_cache_dir))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("rsidMasterPath")
        args.output_dir = payload.get("outputDir")
        args.cache_dir = payload.get("cacheDir") or args.cache_dir
        args.batch_size = int(payload.get("batchSize") or args.batch_size)
    if not args.input or not args.output_dir:
        parser.error("--input and --output-dir are required.")
    return args


def main() -> int:
    args = parse_args()
    process(Path(args.input), Path(args.output_dir), Path(args.cache_dir), max(1, min(100, args.batch_size)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
