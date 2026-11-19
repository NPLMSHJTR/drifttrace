"""Tests for report and manifest generation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from drifttrace.evaluate import evaluate
from drifttrace.report import write_reports


class ReportTests(unittest.TestCase):
    def test_writes_report_metrics_and_manifest(self) -> None:
        truth = {
            "step_minutes": 5,
            "points": 10,
            "series": [
                {
                    "id": "s01",
                    "anomalies": [{"start": 4, "end": 5, "type": "spike", "magnitude": 0.8}],
                }
            ],
        }
        detections = {
            "method": "rolling",
            "elapsed_s": 0.1,
            "series": [{"id": "s01", "intervals": [{"start": 4, "end": 4, "peak": 7.0}]}],
        }
        result = evaluate(truth, detections)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            series_dir = root / "series"
            series_dir.mkdir()
            (series_dir / "s01.csv").write_text(
                "minute,value\n" + "".join(f"{i * 5},{100 + i}\n" for i in range(10)),
                encoding="utf-8",
            )
            paths = write_reports(truth, detections, result, None, series_dir, root / "reports")
            for key in ("report", "metrics", "manifest"):
                self.assertTrue(paths[key].is_file(), key)

            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(len(manifest["series"][0]["values"]), 10)
            self.assertEqual(manifest["series"][0]["intervals"][0]["start"], 4)

            report = paths["report"].read_text(encoding="utf-8")
            self.assertIn("## Headline", report)
            self.assertIn("rolling median + MAD", report)


if __name__ == "__main__":
    unittest.main()
