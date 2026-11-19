"""Synthetic metric series with labelled anomalies.

Each series has a daily shape, sensor noise, and a slow random walk. The weekly
cycle is deliberately omitted: the detector compares a sample with the same
time-of-day on previous days, and a seven-day component would not cancel at that
lag. Keeping the generator and the detector assumption aligned is what makes the
comparison meaningful. Anomalies are injected at known positions and never overlap, so a
detection can be attributed to exactly one cause.

The four injected types cover the shapes that matter in practice: a spike and a
dip are short and loud, a level shift is long and quiet, and a variance burst
changes the noise floor without moving the mean.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STEP_MINUTES = 5
POINTS_PER_DAY = 24 * 60 // STEP_MINUTES


@dataclass
class Anomaly:
    start: int
    end: int
    kind: str
    magnitude: float

    def as_dict(self) -> dict[str, object]:
        return {
            "start": self.start,
            "end": self.end,
            "type": self.kind,
            "magnitude": round(self.magnitude, 3),
        }


@dataclass
class Series:
    id: str
    anomalies: list[Anomaly] = field(default_factory=list)


def _base_signal(points: int, rng: random.Random) -> list[float]:
    phase = rng.random() * math.tau
    drift = 0.0
    values: list[float] = []
    for index in range(points):
        daily = 18.0 * math.sin(math.tau * index / POINTS_PER_DAY + phase)
        drift += rng.gauss(0, 0.05)
        drift = max(-12.0, min(12.0, drift))
        noise = rng.gauss(0, 2.5)
        values.append(100.0 + daily + drift + noise)
    return values


def _inject(values: list[float], series_id: str, rng: random.Random) -> list[Anomaly]:
    points = len(values)
    anomalies: list[Anomaly] = []
    margin = min(3 * POINTS_PER_DAY + 60, points // 3)
    attempts = 0
    target = rng.randint(4, 6)
    while len(anomalies) < target and attempts < 200:
        attempts += 1
        kind = rng.choice(("spike", "dip", "level", "variance"))
        if kind in ("spike", "dip"):
            length = rng.randint(1, 2)
        elif kind == "level":
            length = rng.randint(24, 72)
        else:
            length = rng.randint(36, 72)
        start = rng.randint(margin, points - margin - length)
        end = start + length - 1
        if any(not (end + 40 < other.start or start - 40 > other.end) for other in anomalies):
            continue

        if kind == "spike":
            factor = rng.uniform(1.7, 2.4)
            for index in range(start, end + 1):
                values[index] *= factor
            magnitude = factor - 1.0
        elif kind == "dip":
            factor = rng.uniform(0.35, 0.6)
            for index in range(start, end + 1):
                values[index] *= factor
            magnitude = 1.0 - factor
        elif kind == "level":
            offset = rng.choice((-1, 1)) * rng.uniform(16, 28)
            for index in range(start, end + 1):
                values[index] += offset
            magnitude = abs(offset)
        else:
            scale = rng.uniform(3.0, 5.0)
            for index in range(start, end + 1):
                values[index] += rng.gauss(0, 2.5 * (scale - 1.0))
            magnitude = scale

        anomalies.append(Anomaly(start, end, kind, magnitude))
    anomalies.sort(key=lambda item: item.start)
    return anomalies


def generate(
    out_dir: Path,
    series_count: int = 20,
    points: int = 2880,
    seed: int = 5,
) -> dict[str, object]:
    """Write one CSV per series and return the ground-truth payload."""
    if points < 200:
        raise ValueError("points must be at least 200")
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "step_minutes": STEP_MINUTES,
        "points": points,
        "seed": seed,
        "series": [],
    }
    for index in range(series_count):
        series_id = f"s{index + 1:02d}"
        values = _base_signal(points, rng)
        anomalies = _inject(values, series_id, rng)
        with (out_dir / f"{series_id}.csv").open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("minute,value\n")
            for step, value in enumerate(values):
                handle.write(f"{step * STEP_MINUTES},{value:.3f}\n")
        series = Series(series_id, anomalies)
        payload["series"].append(
            {"id": series.id, "anomalies": [item.as_dict() for item in series.anomalies]}
        )
    return payload


def write_truth(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
