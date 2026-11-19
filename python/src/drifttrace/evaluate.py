"""Point-wise and interval-level scoring for anomaly detection.

Two views are reported because they answer different questions. Point-wise F1
says how many labelled samples were classified correctly, which is the standard
benchmark number. Detection rate says how many injected incidents were noticed
at all, which is what an on-call engineer cares about.
"""

from __future__ import annotations

from typing import Any

DEFAULT_OVERLAP = 0.1


def _mask_from_intervals(length: int, intervals: list[dict[str, Any]]) -> list[bool]:
    mask = [False] * length
    for interval in intervals:
        start = max(0, int(interval["start"]))
        end = min(length - 1, int(interval["end"]))
        for index in range(start, end + 1):
            mask[index] = True
    return mask


def evaluate(
    truth: dict[str, Any],
    detections: dict[str, Any],
    overlap: float = DEFAULT_OVERLAP,
) -> dict[str, Any]:
    """Score predictions against labelled anomalies.

    ``overlap`` is the fraction of a labelled interval that a predicted interval
    must cover to count as detected; the default of 0.1 tolerates the lag a
    rolling median introduces at a level shift.
    """
    points = int(truth.get("points") or 0)
    predicted = {
        series["id"]: series.get("intervals", []) for series in detections.get("series", [])
    }
    per_series: list[dict[str, Any]] = []
    totals = {"tp": 0, "fp": 0, "fn": 0}
    total_anomalies = 0
    detected_anomalies = 0
    false_alarm_intervals = 0
    by_type: dict[str, dict[str, int]] = {}
    delays: list[float] = []

    for series in truth.get("series", []):
        series_id = series["id"]
        anomalies = series.get("anomalies", [])
        predictions = predicted.get(series_id, [])
        truth_mask = _mask_from_intervals(points, anomalies)
        pred_mask = _mask_from_intervals(points, predictions)

        tp = sum(1 for index in range(points) if truth_mask[index] and pred_mask[index])
        fp = sum(1 for index in range(points) if pred_mask[index] and not truth_mask[index])
        fn = sum(1 for index in range(points) if truth_mask[index] and not pred_mask[index])
        totals["tp"] += tp
        totals["fp"] += fp
        totals["fn"] += fn

        series_detected = 0
        for anomaly in anomalies:
            length = int(anomaly["end"]) - int(anomaly["start"]) + 1
            best = 0
            delay = None
            for prediction in predictions:
                start = max(int(prediction["start"]), int(anomaly["start"]))
                end = min(int(prediction["end"]), int(anomaly["end"]))
                covered = max(0, end - start + 1)
                if covered > best:
                    best = covered
                    delay = int(prediction["start"]) - int(anomaly["start"])
            hit = best >= max(1, int(length * overlap))
            bucket = by_type.setdefault(str(anomaly["type"]), {"total": 0, "detected": 0})
            bucket["total"] += 1
            total_anomalies += 1
            if hit:
                bucket["detected"] += 1
                detected_anomalies += 1
                series_detected += 1
                if delay is not None:
                    delays.append(float(delay))

        for prediction in predictions:
            covered = False
            for anomaly in anomalies:
                start = max(int(prediction["start"]), int(anomaly["start"]))
                end = min(int(prediction["end"]), int(anomaly["end"]))
                if end >= start:
                    covered = True
                    break
            if not covered:
                false_alarm_intervals += 1

        per_series.append(
            {
                "id": series_id,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "anomalies": len(anomalies),
                "detected": series_detected,
                "predicted_intervals": len(predictions),
            }
        )

    precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
    recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "method": detections.get("method", "unknown"),
        "points": points,
        "series": len(per_series),
        "tp": totals["tp"],
        "fp": totals["fp"],
        "fn": totals["fn"],
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "anomalies": total_anomalies,
        "detected": detected_anomalies,
        "detection_rate": round(detected_anomalies / total_anomalies, 4)
        if total_anomalies
        else 0.0,
        "false_alarm_intervals": false_alarm_intervals,
        "false_alarms_per_series": round(false_alarm_intervals / len(per_series), 3)
        if per_series
        else 0.0,
        "median_delay_points": sorted(delays)[len(delays) // 2] if delays else None,
        "by_type": {
            name: {
                **counts,
                "rate": round(counts["detected"] / counts["total"], 4) if counts["total"] else 0.0,
            }
            for name, counts in sorted(by_type.items())
        },
        "per_series": per_series,
    }
