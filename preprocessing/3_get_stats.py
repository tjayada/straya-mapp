from __future__ import annotations
import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Dict, Iterable, List, Optional


def load_images(path: Path) -> List[Dict[str, Any]]:
    """Loads an image list from a JSON file."""
    if not path.is_file():
        raise FileNotFoundError(f"Image JSON file not found: {path}")
    with open(path, "r") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return data.get("images", [])
    if isinstance(data, list):
        return data
    raise ValueError("JSON does not contain a valid image list.")


def parse_date_from_item(item: Dict[str, Any]) -> Optional[date]:
    """Parses a date object from an image dictionary's 'date' or 'timestamp' field."""
    for key in ("date", "timestamp"):
        val = item.get(key)
        if val:
            try:
                return date.fromisoformat(str(val)[:10])
            except ValueError:
                continue
    return None


def safe_float(x: Any) -> Optional[float]:
    """Safely converts a value to a float."""
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def summarize_scores(scores: Iterable[float]) -> Dict[str, Optional[float]]:
    """Calculates summary statistics for a list of scores."""
    s = [x for x in scores if x is not None]
    if not s:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "std": None,
        }
    return {
        "count": len(s),
        "mean": mean(s),
        "median": median(s),
        "min": min(s),
        "max": max(s),
        "std": stdev(s) if len(s) > 1 else 0.0,
    }


def compute_stats(images: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes a dictionary of statistics from a list of image data."""
    per_day = Counter(d for i in images if (d := parse_date_from_item(i)))
    scores = [safe_float(i.get("score")) for i in images]
    lat_vals = [lat for i in images if (lat := safe_float(i.get("lat"))) is not None]
    lon_vals = [lon for i in images if (lon := safe_float(i.get("lng"))) is not None]

    unique_days = sorted(per_day.keys())
    earliest, latest = (
        (unique_days[0], unique_days[-1]) if unique_days else (None, None)
    )
    span_days = (latest - earliest).days + 1 if earliest and latest else 0

    return {
        "total_images": len(images),
        "unique_days": len(unique_days),
        "earliest_date": earliest.isoformat() if earliest else None,
        "latest_date": latest.isoformat() if latest else None,
        "span_days": span_days,
        "images_per_active_day_mean": mean(per_day.values()) if per_day else 0,
        "busiest_day_count": max(per_day.values()) if per_day else 0,
        "score_summary": summarize_scores(scores),
        "geo_summary": {
            "with_coords": len(lat_vals),
            "lat_min": min(lat_vals) if lat_vals else None,
            "lat_max": max(lat_vals) if lat_vals else None,
            "lon_min": min(lon_vals) if lon_vals else None,
            "lon_max": max(lon_vals) if lon_vals else None,
        },
        "per_day_counts": {d.isoformat(): c for d, c in per_day.items()},
    }


def print_summary(stats: Dict[str, Any]) -> None:
    """Prints a formatted summary of the computed statistics."""
    print("\n=== Image Data Summary ===")
    print(
        f"Total images: {stats['total_images']}, spanning {stats['unique_days']} unique days over a {stats['span_days']}-day period."
    )
    print(f"Date range: {stats['earliest_date']} to {stats['latest_date']}")

    ss = stats["score_summary"]
    if ss["count"]:
        print(
            f"Scores: Mean={ss['mean']:.2f}, Median={ss['median']:.2f}, Range=[{ss['min']:.2f}, {ss['max']:.2f}]"
        )

    geo = stats["geo_summary"]
    if geo["with_coords"]:
        print(
            f"Geo: {geo['with_coords']} images with coordinates. Lat=[{geo['lat_min']:.4f}, {geo['lat_max']:.4f}], Lon=[{geo['lon_min']:.4f}, {geo['lon_max']:.4f}]"
        )

    print("\nTop 5 busiest days:")
    top_days = sorted(
        stats["per_day_counts"].items(), key=lambda x: x[1], reverse=True
    )[:5]
    for day, count in top_days:
        print(f"  {day}: {count} images")


def launch_script(script_path: Path, args: List[str]) -> int:
    """Launches a Python script in a subprocess."""
    cmd = [sys.executable, str(script_path)] + args
    print(f"\nLaunching: {' '.join(cmd)}")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute stats from image JSON and optionally run other tools."
    )
    parser.add_argument(
        "--json-path",
        type=str,
        default="frontend/web_export/image_data.json",
        help="Path to image_data.json.",
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        default="frontend/web_export/",
        help="Base directory for image files.",
    )
    parser.add_argument(
        "--save-stats", type=str, help="Optional path to save computed stats as JSON."
    )
    args = parser.parse_args()

    # Load from config.json
    with open("config.json", "r") as f:
        config = json.load(f)
    args.image_dir = config.get("export_path", args.image_dir)
    args.json_path = str(Path(args.image_dir) / "image_data.json")

    json_path = Path(args.json_path).expanduser().resolve()

    try:
        images = load_images(json_path)
    except Exception as e:
        print(f"Error: {e}")
        return 1

    if not images:
        print("No images found in JSON file.")
        return 0

    stats = compute_stats(images)
    print_summary(stats)

    if args.save_stats:
        out_path = Path(args.save_stats).expanduser().resolve()
        with open(out_path, "w") as f:
            json.dump(stats, f, indent=2, default=str)
        print(f"\nSaved stats to: {out_path}")

    # Interactive follow-up
    print("\nNext action:\n  1) Review images\n  2) Cluster similar images\n  3) Exit")
    choice = input("Enter choice [3]: ").strip() or "3"

    repo_root = Path(__file__).resolve().parent
    if choice == "1":
        script = repo_root / "review_images.py"
        script_args = [
            "--image-json",
            str(json_path),
            "--image-dir",
            str(json_path.parent),
            "--apply-deletions",
        ]
        return launch_script(script, script_args) if script.is_file() else 1
    elif choice == "2":
        script = repo_root / "cluster_similar_images.py"
        script_args = ["--image-dir", str(json_path.parent)]
        return launch_script(script, script_args) if script.is_file() else 1

    print("Exiting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
