from __future__ import annotations

import csv
import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path


SNAPSHOT_ROOT = Path(r"F:\Heal by FON\data\prototype\tier1-105-human-signed-20260823")
SNAPSHOT = SNAPSHOT_ROOT / "tier1_prototype_snapshot_v1_human_signed.json"
COVERAGE = SNAPSHOT_ROOT / "prototype_coverage_manifest_v1.json"
REGISTRY = Path(r"F:\Heal by FON\data\canon\curation\mechanism_registry_v1.csv")
EXPECTED_REGISTRY_SHA256 = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Tier1SignedPrototypeSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SNAPSHOT.exists():
            raise unittest.SkipTest("The operational signed snapshot is unavailable")
        cls.snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        cls.coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))

    def test_snapshot_has_exact_signed_decision_counts(self):
        groups = self.snapshot["groups"]
        self.assertEqual(len(groups), 105)
        self.assertEqual(len({row["group_id"] for row in groups}), 105)
        self.assertEqual(Counter(row["prototype_inference_ceiling"] for row in groups), {
            "initial_guide_candidate": 77,
            "context_only": 28,
        })
        self.assertTrue(all(row["human_review"]["decision"] == "approved" for row in groups))

    def test_decision_and_allowlist_provenance_remain_separate(self):
        counts = Counter(row["provenance"]["decision_signature"] for row in self.snapshot["groups"])
        self.assertEqual(counts, {
            "signed_gold_unchanged": 7,
            "human_resigned_after_prototype_replay": 5,
            "human_adjudicated": 1,
            "packet_selected_human_reviewed": 92,
        })
        prkaa2 = next(row for row in self.snapshot["groups"] if row["group_id"] == "PRKAA2:T1.1")
        self.assertEqual(prkaa2["provenance"]["decision_signature"], "human_adjudicated")
        self.assertFalse(prkaa2["provenance"]["base_pair_consensus"])
        self.assertIn(prkaa2["provenance"]["allowlist"], {"signed_gold", "packet_selected_human_reviewed"})

    def test_coverage_is_closed_and_registry_is_untouched(self):
        self.assertEqual(self.coverage["canonical_group_count"], 180)
        self.assertEqual(self.coverage["covered_count"], 105)
        self.assertEqual(self.coverage["not_covered_count"], 75)
        self.assertEqual(len(self.coverage["groups"]), 180)
        self.assertEqual(sha256(REGISTRY), EXPECTED_REGISTRY_SHA256)

    def test_signed_human_export_has_105_unique_packet_hashes_bound_by_group(self):
        source = SNAPSHOT_ROOT / "signed_sources" / "REVISION_HUMANA_TIER1_105_FIRMADA_2026-08-23.csv"
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 105)
        self.assertEqual(len({row["grupo"] for row in rows}), 105)
        snapshot_hashes = {row["group_id"]: row["provenance"]["packet_sha256"] for row in self.snapshot["groups"]}
        self.assertEqual({row["grupo"]: row["packet_sha256"] for row in rows}, snapshot_hashes)


if __name__ == "__main__":
    unittest.main()
