import argparse
from collections import defaultdict
from pathlib import Path
import importlib.util

import imagehash
from PIL import Image, ImageEnhance
from tqdm import tqdm

EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".JPG",
    ".JPEG",
    ".PNG",
    ".WEBP",
    ".HEIC",
}


def compute_dhash(path: Path, hash_size: int, preprocess: bool):
    """Computes the difference hash (dhash) for an image."""
    try:
        with Image.open(path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            if preprocess:
                img = ImageEnhance.Contrast(img).enhance(1.15)
                img = ImageEnhance.Brightness(img).enhance(1.05)
            return imagehash.dhash(img, hash_size=hash_size)
    except Exception:
        return None


def find_connected_components(pairs):
    """Finds clusters of connected items from a list of pairs."""
    graph = defaultdict(set)
    for a, b in pairs:
        graph[a].add(b)
        graph[b].add(a)

    visited = set()
    clusters = []
    for node in graph:
        if node not in visited:
            comp = set()
            q = [node]
            visited.add(node)
            head = 0
            while head < len(q):
                curr = q[head]
                head += 1
                comp.add(curr)
                for neighbor in graph.get(curr, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        q.append(neighbor)
            if len(comp) > 1:
                clusters.append(sorted(list(comp)))
    return clusters


def evaluate_thresholds(hashes, thresholds):
    """Evaluates clustering for a range of hash distance thresholds."""
    files_list = list(hashes.keys())
    distances = [
        (i, j, hashes[files_list[i]] - hashes[files_list[j]])
        for i in range(len(files_list))
        for j in range(i + 1, len(files_list))
    ]

    results = {}
    for thr in thresholds:
        pairs = [(files_list[i], files_list[j]) for i, j, d in distances if d <= thr]
        clusters = find_connected_components(pairs)
        to_delete = sum(len(c) - 1 for c in clusters)
        results[thr] = {"clusters": clusters, "to_delete": to_delete}
    return results


def select_representative(cluster):
    """Selects the image with the shortest filename as the representative."""
    return min(cluster, key=lambda p: len(p.name))


def main():
    parser = argparse.ArgumentParser(
        description="Find and remove similar images using dhash."
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        default="frontend/web_export/",
        help="Directory containing images.",
    )
    parser.add_argument("--hash-size", type=int, default=8, help="Hash size for dhash.")
    parser.add_argument(
        "--min-threshold", type=int, default=2, help="Start threshold for evaluation."
    )
    parser.add_argument(
        "--max-threshold", type=int, default=62, help="End threshold for evaluation."
    )
    parser.add_argument("--step", type=int, default=4, help="Step between thresholds.")
    args = parser.parse_args()

    image_dir = Path(args.image_dir).expanduser()
    if not image_dir.is_dir():
        print(f"Directory not found: {image_dir}")
        return 1

    files = sorted(
        [
            f
            for ext in EXTENSIONS
            for f in image_dir.glob(f"*{ext}")
            if not f.name.startswith("._")
        ]
    )
    if len(files) < 2:
        print("At least 2 images are required for comparison.")
        return 0

    print(f"Found {len(files)} images. Computing dhash (preprocess enabled)...")
    hashes = {
        f: h
        for f in tqdm(files, desc="Hashing")
        if (h := compute_dhash(f, args.hash_size, True))
    }

    if not hashes:
        print("Could not hash any images.")
        return 1

    thresholds = list(range(args.min_threshold, args.max_threshold + 1, args.step))
    results = evaluate_thresholds(hashes, thresholds)

    print("\nThreshold sweep results:")
    for thr in thresholds:
        print(
            f"  Threshold {thr}: would remove {results[thr]['to_delete']} of {len(files)} files."
        )

    try:
        choice = input("\nEnter threshold to apply (or blank to exit): ").strip()
        if not choice:
            print("Exiting.")
            return 0
        chosen_threshold = int(choice)
        if chosen_threshold not in results:
            print("Invalid threshold.")
            return 1
    except (ValueError, KeyboardInterrupt, EOFError):
        print("Invalid input or interrupted. Exiting.")
        return 1

    chosen_clusters = results[chosen_threshold]["clusters"]
    to_delete = [p for c in chosen_clusters for p in c if p != select_representative(c)]

    if not to_delete:
        print("No files to delete for this threshold.")
        return 0

    print(
        f"\nApplying threshold {chosen_threshold} -> {len(to_delete)} files marked for removal."
    )
    if input("Continue with deletion? [y/N]: ").strip().lower() != "y":
        print("Cancelled.")
        return 1

    # Use delete_helpers if available to keep JSON in sync
    helper_path = Path(__file__).resolve().parent / "delete_helpers.py"
    if helper_path.is_file():
        spec = importlib.util.spec_from_file_location(
            "delete_helpers", str(helper_path)
        )
        helper_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper_mod)

        image_json_path = image_dir / "image_data.json"
        print(f"Using delete helper; will update JSON at: {image_json_path}")
        res = helper_mod.remove_paths_from_image_json(
            json_path=image_json_path,
            targets=to_delete,
            base_dir=image_dir,
            delete_files=True,
        )
        print(
            f"Removed from JSON: {res['removed_from_json']}, Deleted files: {res['deleted_files']}, Missing: {res['missing_files']}"
        )
    else:
        # Fallback to simple deletion
        deleted_count = sum(
            1 for p in tqdm(to_delete, desc="Deleting") if p.unlink(missing_ok=True)
        )
        print(f"\nDeleted {deleted_count} file(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
