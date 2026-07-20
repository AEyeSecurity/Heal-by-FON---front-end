"""Offline cache/worker benchmark for the v2 enrichment secondary-source stage.

This deliberately mocks external APIs. It measures local cache access, SQLite
serialization, worker scheduling, and artifact-independent source accounting.
Use a real run's performance summary for live API latency and rate limits.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "services" / "heal-variant-enrichment" / "enrich_gene_module_v2.py"
sys.path.insert(0, str(SERVICE_PATH.parent))


def load_service():
    spec = importlib.util.spec_from_file_location("heal_v2_enrichment_benchmark", SERVICE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {SERVICE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mocked_payload(source: str):
    if source == "fetch_clinvar":
        return {"count": "1"}, ""
    if source == "fetch_gwas_catalog":
        return {"association_count": "1"}, ""
    if source == "fetch_clinpgx":
        return {"clinical_annotation_count": "1", "variant_id": "pa1"}, ""
    return {"mocked": True}, ""


def run(count: int, warm: bool) -> dict:
    service = load_service()
    variants = [
        {"variant_key": f"GRCh38:chr1:{index}:A:G", "resolved_rsid": f"rs{index + 1}"}
        for index in range(count)
    ]
    with tempfile.TemporaryDirectory() as temporary:
        cache = service.EnrichmentCache(Path(temporary) / "enrichment_cache.sqlite", ttl_days=1)
        patches = [
            patch.object(service.legacy, "fetch_ensembl_variation", side_effect=lambda *_: mocked_payload("fetch_ensembl_variation")),
            patch.object(service.legacy, "fetch_clinvar", side_effect=lambda *_: mocked_payload("fetch_clinvar")),
            patch.object(service.legacy, "fetch_myvariant", side_effect=lambda *_: mocked_payload("fetch_myvariant")),
            patch.object(service.legacy, "fetch_gwas_catalog", side_effect=lambda *_: mocked_payload("fetch_gwas_catalog")),
            patch.object(service.legacy, "fetch_clinpgx", side_effect=lambda *_: mocked_payload("fetch_clinpgx")),
        ]
        for context in patches:
            context.start()
        try:
            if warm:
                service.fetch_secondary_sources(variants, "GRCh38", cache, 1)
            started = time.perf_counter()
            _, metrics = service.fetch_secondary_sources(variants, "GRCh38", cache, 1)
            elapsed = time.perf_counter() - started
        finally:
            for context in reversed(patches):
                context.stop()
            cache.close()
    return {
        "count": count,
        "cache_mode": "hot" if warm else "cold",
        "elapsed_seconds": round(elapsed, 4),
        "calls_per_second": round(metrics["calls_completed"] / max(elapsed, 0.001), 2),
        "cache_hits": metrics["cache_hits"],
        "network_calls": sum(item["network_calls"] for item in metrics["source_stats"].values()),
        "source_stats": metrics["source_stats"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, nargs="+", default=[100, 1000])
    args = parser.parse_args()
    results = [run(count, warm) for count in args.count for warm in (False, True)]
    print(json.dumps(results, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
