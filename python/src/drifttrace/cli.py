"""Command-line interface for drifttrace."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .evaluate import evaluate
from .gen import generate, write_truth
from .report import write_reports

EXIT_OK = 0
EXIT_ERROR = 2
TIMELINE = Path(__file__).resolve().parents[3] / "web" / "timeline.ts"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return payload


def _line(label: str, result: dict[str, Any]) -> str:
    return (
        f"{label:<14} P {result['precision']:.3f}  R {result['recall']:.3f}  "
        f"F1 {result['f1']:.3f}   detected {result['detected']}/{result['anomalies']}  "
        f"false alarms {result['false_alarm_intervals']}"
    )


def cmd_gen(args: argparse.Namespace) -> int:
    truth = generate(Path(args.out), series_count=args.series, points=args.points, seed=args.seed)
    write_truth(truth, Path(args.truth))
    print(f"wrote {args.series} series x {args.points} points to {args.out}; truth at {args.truth}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    series_dir = workdir / "series"
    truth_path = workdir / "truth.json"
    detections_path = workdir / "detections.json"
    baseline_path = workdir / "baseline.json"

    truth = generate(series_dir, series_count=args.series, points=args.points, seed=args.seed)
    write_truth(truth, truth_path)

    detector = Path(__file__).resolve().parents[3] / "java" / "src" / "drifttrace" / "Detector.java"
    if not detector.is_file():
        raise ValueError(f"detector source not found at {detector}")
    classes = workdir / "classes"
    subprocess.run(["javac", "-d", str(classes), str(detector)], check=True)
    base = ["java", "-cp", str(classes), "drifttrace.Detector", "--input", str(series_dir)]
    subprocess.run([*base, "--out", str(detections_path)], check=True)
    subprocess.run([*base, "--out", str(baseline_path), "--baseline"], check=True)

    detections = _load(detections_path)
    baseline = _load(baseline_path)
    result = evaluate(truth, detections)
    baseline_result = evaluate(truth, baseline)
    paths = write_reports(
        truth, detections, result, baseline_result, series_dir, workdir / "reports"
    )

    print(f"drifttrace {__version__}  {result['series']} series  {result['points']} points each")
    print(_line("rolling", result))
    print(_line("baseline", baseline_result))
    print(f"report: {paths['report']}")
    print(f"manifest: {paths['manifest']}")

    if args.ui:
        node = shutil.which("node")
        if node is None:
            raise ValueError("node is required to build the timeline page")
        subprocess.run(
            [
                node,
                "--experimental-strip-types",
                str(TIMELINE),
                "--manifest",
                str(paths["manifest"]),
                "--out",
                str(workdir / "reports" / "timeline.html"),
            ],
            check=True,
        )
        print(f"timeline: {workdir / 'reports' / 'timeline.html'}")
    return EXIT_OK


def cmd_eval(args: argparse.Namespace) -> int:
    truth = _load(Path(args.truth))
    detections = _load(Path(args.detections))
    baseline = _load(Path(args.baseline)) if args.baseline else None
    result = evaluate(truth, detections)
    baseline_result = evaluate(truth, baseline) if baseline else None
    paths = write_reports(
        truth, detections, result, baseline_result, Path(args.series), Path(args.out)
    )
    print(_line("rolling", result))
    if baseline_result:
        print(_line("baseline", baseline_result))
    print(f"report: {paths['report']}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drifttrace",
        description="Detect metric anomalies and score them against labelled incidents.",
    )
    parser.add_argument("--version", action="version", version=f"drifttrace {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("gen", help="generate series with labelled anomalies")
    gen.add_argument("--series", type=int, default=20)
    gen.add_argument("--points", type=int, default=2880)
    gen.add_argument("--seed", type=int, default=5)
    gen.add_argument("--out", default="data/series")
    gen.add_argument("--truth", default="data/truth.json")
    gen.set_defaults(func=cmd_gen)

    run = subparsers.add_parser("run", help="generate, detect, and score in one step")
    run.add_argument("--series", type=int, default=20)
    run.add_argument("--points", type=int, default=2880)
    run.add_argument("--seed", type=int, default=5)
    run.add_argument("--workdir", default="drifttrace-run")
    run.add_argument("--ui", action="store_true", help="also build the timeline page")
    run.set_defaults(func=cmd_run)

    evaluate_cmd = subparsers.add_parser("eval", help="score existing detections")
    evaluate_cmd.add_argument("--truth", required=True)
    evaluate_cmd.add_argument("--detections", required=True)
    evaluate_cmd.add_argument("--baseline", default=None)
    evaluate_cmd.add_argument("--series", default="data/series")
    evaluate_cmd.add_argument("--out", default="reports")
    evaluate_cmd.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"drifttrace: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
