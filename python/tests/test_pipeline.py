"""Tests for the synthetic generator and the scorer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from drifttrace.evaluate import evaluate
from drifttrace.gen import generate, write_truth


class GeneratorTests(unittest.TestCase):
    def test_writes_one_csv_per_series_with_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            truth = generate(root / "series", series_count=3, points=600, seed=2)
            files = sorted((root / "series").glob("*.csv"))
            self.assertEqual(len(files), 3)
            lines = files[0].read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "minute,value")
            self.assertEqual(len(lines) - 1, 600)
            self.assertEqual(truth["points"], 600)
            self.assertEqual(len(truth["series"]), 3)

            write_truth(truth, root / "truth.json")
            stored = json.loads((root / "truth.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["schema_version"], 1)

    def test_anomalies_stay_inside_the_warmup_free_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            truth = generate(Path(tmp) / "series", series_count=2, points=2880, seed=3)
            warmup = 3 * 288
            for series in truth["series"]:
                spans = []
                for anomaly in series["anomalies"]:
                    self.assertGreaterEqual(anomaly["start"], warmup)
                    self.assertLess(anomaly["end"], 2880)
                    spans.append((anomaly["start"], anomaly["end"]))
                spans.sort()
                for first, second in zip(spans, spans[1:], strict=False):
                    self.assertGreater(second[0], first[1], "anomalies must not overlap")


class ScoringTests(unittest.TestCase):
    def _truth(self, anomalies):
        return {
            "points": 100,
            "series": [{"id": "s01", "anomalies": anomalies}],
        }

    def test_perfect_prediction(self) -> None:
        truth = self._truth([{"start": 10, "end": 19, "type": "spike"}])
        detections = {"series": [{"id": "s01", "intervals": [{"start": 10, "end": 19}]}]}
        result = evaluate(truth, detections)
        self.assertAlmostEqual(result["precision"], 1.0)
        self.assertAlmostEqual(result["recall"], 1.0)
        self.assertEqual(result["detected"], 1)

    def test_missed_incident_lowers_detection_rate(self) -> None:
        truth = self._truth(
            [
                {"start": 10, "end": 19, "type": "spike"},
                {"start": 50, "end": 59, "type": "level"},
            ]
        )
        detections = {"series": [{"id": "s01", "intervals": [{"start": 11, "end": 18}]}]}
        result = evaluate(truth, detections)
        self.assertEqual(result["detected"], 1)
        self.assertAlmostEqual(result["detection_rate"], 0.5)
        self.assertEqual(result["by_type"]["level"]["detected"], 0)

    def test_false_positive_counts_as_alarm(self) -> None:
        truth = self._truth([{"start": 10, "end": 19, "type": "spike"}])
        detections = {
            "series": [
                {
                    "id": "s01",
                    "intervals": [{"start": 10, "end": 19}, {"start": 70, "end": 72}],
                }
            ]
        }
        result = evaluate(truth, detections)
        self.assertEqual(result["false_alarm_intervals"], 1)
        self.assertLess(result["precision"], 1.0)

    def test_partial_overlap_needs_the_configured_fraction(self) -> None:
        truth = self._truth([{"start": 10, "end": 29, "type": "level"}])
        partial = {"series": [{"id": "s01", "intervals": [{"start": 10, "end": 11}]}]}
        self.assertEqual(evaluate(truth, partial, overlap=0.1)["detected"], 1)
        self.assertEqual(evaluate(truth, partial, overlap=0.5)["detected"], 0)


if __name__ == "__main__":
    unittest.main()
