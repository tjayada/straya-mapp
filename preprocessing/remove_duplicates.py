import argparse
from collections import defaultdict
from pathlib import Path
from typing import Iterable, List, Dict, Any

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


def compute_dhash(path: Path, hash_size=8, preprocess=True):
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


def find_connected_components(pairs: Iterable[tuple[Path, Path]]) -> List[List[Path]]:
    graph: Dict[Path, set] = defaultdict(set)
    for a, b in pairs:
        graph[a].add(b)
        graph[b].add(a)

    visited = set()
    clusters: List[List[Path]] = []

    def dfs(node, comp):
        if node in visited:
            return
        visited.add(node)
        comp.add(node)
        for nb in graph.get(node, []):
            if nb not in visited:
                dfs(nb, comp)

    for node in graph:
        if node not in visited:
            comp = set()
            dfs(node, comp)
            if len(comp) > 1:
                clusters.append(sorted(comp))

    return clusters


def select_representative(cluster: List[Path]) -> Path:
    return min(cluster, key=lambda p: len(p.name))


def apply_threshold(
    image_dir: str,
    threshold: int,
    hash_size: int = 8,
    preprocess: bool = True,
    extensions: Iterable[str] = None,
    delete: bool = False,
) -> Dict[str, Any]:
    """Apply a fixed Hamming threshold and optionally delete non-representatives.

    Returns a dict with keys: `threshold`, `clusters`, `to_delete`, `to_keep`,
    `deleted_count`, `failed_delete_count`.
    """
    if extensions is None:
        exts = EXTENSIONS
    else:
        exts = set(extensions)

    image_dir = Path(image_dir).expanduser()
    if not image_dir.is_dir():
        raise NotADirectoryError(f"Directory not found: {image_dir}")

    files = []
    for ext in exts:
        files.extend(image_dir.glob(f"*{ext}"))
    files = [f for f in files if not f.name.startswith("._")]
    files = sorted(files)

    if len(files) < 2:
        return {
            "threshold": threshold,
            "clusters": [],
            "to_delete": [],
            "to_keep": [],
            "deleted_count": 0,
            "failed_delete_count": 0,
        }

    # compute hashes
    hashes: Dict[Path, Any] = {}
    for f in tqdm(files, desc="hashing"):
        h = compute_dhash(f, hash_size=hash_size, preprocess=preprocess)
        if h is not None:
            hashes[f] = h

    files_list = list(hashes.keys())

    pairs = []
    for i in range(len(files_list)):
        for j in range(i + 1, len(files_list)):
            d = hashes[files_list[i]] - hashes[files_list[j]]
            if d <= threshold:
                pairs.append((files_list[i], files_list[j]))

    clusters = find_connected_components(pairs)

    to_delete: List[Path] = []
    to_keep: List[Path] = []
    for c in clusters:
        rep = select_representative(c)
        to_keep.append(rep)
        for p in c:
            if p != rep:
                to_delete.append(p)

    deleted = 0
    failed_del = 0
    if delete and to_delete:
        for p in tqdm(to_delete, desc="deleting"):
            try:
                p.unlink()
                deleted += 1
            except Exception:
                failed_del += 1

    return {
        "threshold": threshold,
        "clusters": clusters,
        "to_delete": to_delete,
        "to_keep": to_keep,
        "deleted_count": deleted,
        "failed_delete_count": failed_del,
    }


def _cli_main():
    parser = argparse.ArgumentParser(
        description="Apply fixed threshold to remove similar images"
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        default="exported_photos/",
        help="Directory with images",
    )
    parser.add_argument(
        "--hash-size", type=int, default=8, help="Hash size for dhash (default 8)"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        required=True,
        help="Hamming distance threshold to apply",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Actually delete the files (if omitted, dry-run)",
    )

    args = parser.parse_args()

    try:
        result = apply_threshold(
            args.image_dir,
            threshold=args.threshold,
            hash_size=args.hash_size,
            delete=args.force,
        )
    except NotADirectoryError as e:
        print(e)
        return 1

    clusters = result["clusters"]
    to_delete = result["to_delete"]
    to_keep = result["to_keep"]

    if not clusters:
        print("No similar images found for threshold", args.threshold)
        return 0

    print(
        f"Threshold {args.threshold}: {len(clusters)} cluster(s), {len(to_delete)} file(s) marked for removal"
    )
    for rep in to_keep:
        print(f"  ✓ Keep: {rep.name}")
    for p in sorted(to_delete):
        print(f"  ✗ Remove: {p.name}")

    if args.force:
        print(
            f"\nDeleted {result['deleted_count']} file(s). Failed: {result['failed_delete_count']}"
        )
    else:
        print("\nDry run: no files deleted. Re-run with --force to delete.")

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
