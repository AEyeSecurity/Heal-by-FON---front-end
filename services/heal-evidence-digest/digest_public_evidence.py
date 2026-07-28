#!/usr/bin/env python3
"""Create citation-bound public evidence digests for oversized HEAL groups."""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path


OPENAI_URL = "https://api.openai.com/v1/responses"
CHUNK_TOKEN_LIMIT = 8_000
SYSTEM_PROMPT = """You compress public scientific evidence without interpreting a person.
Return only the requested JSON. Use only supplied evidence IDs. Do not create clinical
classifications, numerical scores, counts, diagnoses, treatment advice or individual
predictions. Preserve contradictions and distinguish reported observations from study
limitations. Every output item must cite one or more supplied evidence_refs."""

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence_refs", "reported_observation", "study_design", "reported_direction", "limitations"],
                "properties": {
                    "evidence_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "reported_observation": {"type": "string"},
                    "study_design": {"type": "string"},
                    "reported_direction": {"type": "string"},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def read_token_audit(path: Path) -> dict[str, dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["group_id"]: row for row in csv.DictReader(handle)}


def read_packets(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def public_records(packet: dict) -> list[dict]:
    records = []
    for row in packet.get("clinvar_assertions") or []:
        text = clean(row.get("description"))
        ref = clean(row.get("scv_accession"))
        if ref and text:
            records.append({"evidence_id": ref, "source": "clinvar_scv", "text": text})
    for row in packet.get("publications") or []:
        text = clean(row.get("abstract"))
        pmid = clean(row.get("pmid"))
        if pmid and text:
            records.append({"evidence_id": f"PMID:{pmid}", "source": "pubmed", "text": text})
    dedup = {}
    for row in records:
        dedup.setdefault(row["evidence_id"], row)
    return list(dedup.values())


def estimate_tokens(value) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False).encode("utf-8")) // 3)


def chunks(records: list[dict]) -> list[list[dict]]:
    output: list[list[dict]] = []
    current: list[dict] = []
    for record in records:
        candidate = current + [record]
        if current and estimate_tokens(candidate) > CHUNK_TOKEN_LIMIT:
            output.append(current)
            current = [record]
        else:
            current = candidate
    if current:
        output.append(current)
    return output


def response_text(payload: dict) -> str:
    if payload.get("output_text"):
        return clean(payload["output_text"])
    values = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                values.append(content["text"])
    if not values:
        raise ValueError("Digest response contained no output text.")
    return "\n".join(values)


def call_model(records: list[dict], api_key: str, model: str, timeout: int) -> dict:
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"public_evidence": records}, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": "heal_public_evidence_digest", "strict": True, "schema": OUTPUT_SCHEMA}},
    }
    request = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response_text(json.loads(response.read().decode("utf-8"))))


def validate_digest(digest: dict, allowed_refs: set[str]) -> None:
    for item in digest.get("items") or []:
        refs = set(item.get("evidence_refs") or [])
        if not refs or not refs.issubset(allowed_refs):
            raise ValueError(f"Digest introduced unknown evidence refs: {sorted(refs - allowed_refs)}")


def cache_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS digests (cache_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    return connection


def process(payload: dict) -> dict:
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_path = Path(payload["packetsPath"]).resolve()
    audit = read_token_audit(Path(payload["tokenAuditPath"]).resolve())
    requested = {clean(value) for value in payload.get("groupIds") or [] if clean(value)}
    model = clean(payload.get("model"))
    api_key = clean(payload.get("apiKey")) or clean(os.environ.get("HEAL_OPENAI_API_KEY")) or clean(os.environ.get("OPENAI_API_KEY"))
    if not model:
        raise ValueError("HEAL_V2_EVIDENCE_DIGEST_MODEL is required.")
    if not api_key:
        raise ValueError("An OpenAI API key is required for evidence digest generation.")
    candidates = {
        group_id for group_id, row in audit.items()
        if row.get("budget_status") in {"requires_hierarchical_compression", "compression_review_required"}
    }
    if requested:
        candidates &= requested
    cache = cache_connection(Path(payload["cachePath"]).resolve())
    digests = []
    errors = []
    calls = 0
    hits = 0
    for packet in read_packets(packet_path):
        group_id = clean(packet.get("group_id"))
        if group_id not in candidates:
            continue
        records = public_records(packet)
        group_items = []
        try:
            for chunk in chunks(records):
                fingerprint = hashlib.sha256(
                    json.dumps({"model": model, "prompt": SYSTEM_PROMPT, "schema": OUTPUT_SCHEMA, "records": chunk}, sort_keys=True).encode("utf-8")
                ).hexdigest()
                cached = cache.execute("SELECT payload_json FROM digests WHERE cache_key=?", (fingerprint,)).fetchone()
                if cached:
                    digest = json.loads(cached[0])
                    hits += 1
                else:
                    last_error = None
                    for attempt in range(2):
                        try:
                            digest = call_model(chunk, api_key, model, int(payload.get("timeoutSeconds") or 90))
                            calls += 1
                            break
                        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as error:
                            last_error = error
                            if attempt == 0:
                                time.sleep(2)
                    else:
                        raise RuntimeError(str(last_error))
                    validate_digest(digest, {row["evidence_id"] for row in chunk})
                    cache.execute("INSERT OR REPLACE INTO digests(cache_key,payload_json) VALUES(?,?)", (fingerprint, json.dumps(digest)))
                    cache.commit()
                validate_digest(digest, {row["evidence_id"] for row in chunk})
                group_items.extend(digest.get("items") or [])
            digests.append({"group_id": group_id, "digest_mode": "generative_public_evidence", "model": model, "items": group_items})
        except Exception as error:  # noqa: BLE001
            errors.append({"group_id": group_id, "error": str(error), "fallback": "referenced_only"})
    cache.close()
    digest_path = output_dir / "group_evidence_digests.jsonl"
    error_path = output_dir / "group_evidence_digest_errors.csv"
    with digest_path.open("w", encoding="utf-8") as handle:
        for row in digests:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    with error_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["group_id", "error", "fallback"])
        writer.writeheader()
        writer.writerows(errors)
    summary = {
        "status": "valid" if not errors else "warning",
        "groupsRequested": len(candidates),
        "groupsDigested": len(digests),
        "groupsFailed": len(errors),
        "networkCalls": calls,
        "cacheHits": hits,
        "outputs": {"groupEvidenceDigestsJsonl": str(digest_path), "groupEvidenceDigestErrorsCsv": str(error_path)},
    }
    print(json.dumps(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    process(json.loads(base64.b64decode(args.input_json_base64).decode("utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
