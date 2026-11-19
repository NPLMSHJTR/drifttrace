"""Report, metrics JSON, and the manifest consumed by the timeline page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_series(path: Path) -> list[float]:
    values: list[float] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if index == 0 and len(parts) > 1:
            try:
                float(parts[1])
            except ValueError:
                continue
        if len(parts) >= 2:
            try:
                values.append(float(parts[1]))
            except ValueError:
                continue
    return values


def _headline(result: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    lines = [
        "| detector | precision | recall | F1 | detection rate | false alarms / series |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| rolling median + MAD | {result['precision']:.3f} | {result['recall']:.3f} "
        f"| {result['f1']:.3f} | {result['detection_rate']:.1%} "
        f"| {result['false_alarms_per_series']:.2f} |",
    ]
    if baseline:
        lines.append(
            f"| global 3-sigma (baseline) | {baseline['precision']:.3f} "
            f"| {baseline['recall']:.3f} | {baseline['f1']:.3f} "
            f"| {baseline['detection_rate']:.1%} "
            f"| {baseline['false_alarms_per_series']:.2f} |"
        )
    return lines


def write_reports(
    truth: dict[str, Any],
    detections: dict[str, Any],
    result: dict[str, Any],
    baseline_result: dict[str, Any] | None,
    series_dir: Path,
    out_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    predicted = {
        series["id"]: series.get("intervals", []) for series in detections.get("series", [])
    }

    manifest_series: list[dict[str, Any]] = []
    for series in truth.get("series", []):
        series_id = series["id"]
        values = _read_series(series_dir / f"{series_id}.csv")
        manifest_series.append(
            {
                "id": series_id,
                "values": [round(value, 2) for value in values],
                "anomalies": series.get("anomalies", []),
                "intervals": predicted.get(series_id, []),
            }
        )

    metrics = {key: value for key, value in result.items() if key != "per_series"}
    baseline_metrics = (
        {key: value for key, value in baseline_result.items() if key != "per_series"}
        if baseline_result
        else None
    )
    manifest = {
        "schema_version": 1,
        "step_minutes": truth.get("step_minutes", 5),
        "metrics": metrics,
        "baseline": baseline_metrics,
        "series": manifest_series,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")

    metrics_path = out_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    lines = [
        "# drifttrace report",
        "",
        f"- series: {result['series']}",
        f"- points per series: {result['points']}",
        f"- labelled anomalies: {result['anomalies']}",
        f"- detector time: {detections.get('elapsed_s')} s",
        "",
        "## Headline",
        "",
        *_headline(result, baseline_result),
        "",
        "## Detection by anomaly type",
        "",
        "| type | rolling | baseline |",
        "| --- | --- | --- |",
    ]
    types = sorted(set(result["by_type"]) | set((baseline_result or {}).get("by_type", {})))
    for name in types:
        current = result["by_type"].get(name, {})
        old = (baseline_result or {}).get("by_type", {}).get(name, {})
        lines.append(
            f"| {name} | {current.get('detected', 0)}/{current.get('total', 0)} "
            f"({current.get('rate', 0):.0%}) | {old.get('detected', 0)}/{old.get('total', 0)} "
            f"({old.get('rate', 0):.0%}) |"
        )

    worst = sorted(result["per_series"], key=lambda item: item["detected"] - item["anomalies"])[:5]
    lines.extend(
        [
            "",
            "## Series to review first",
            "",
            "| series | anomalies | detected | predicted intervals |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in worst:
        lines.append(
            f"| {item['id']} | {item['anomalies']} | {item['detected']} | "
            f"{item['predicted_intervals']} |"
        )

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"report": report_path, "metrics": metrics_path, "manifest": manifest_path}
