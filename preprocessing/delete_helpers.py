from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_image_json(json_path: Path) -> Tuple[Any, List[Dict[str, Any]]]:
    """Loads image data from a JSON file, returning the original structure and the image list."""
    if not json_path.is_file():
        raise FileNotFoundError(f"Image JSON not found: {json_path}")
    with open(json_path, "r") as f:
        data = json.load(f)

    if isinstance(data, dict):
        images = data.get("images", [])
    elif isinstance(data, list):
        images = data
    else:
        raise ValueError("JSON does not contain a valid image list.")

    return data, images


def save_image_json(json_path: Path, original_data: Any, images: List[Dict[str, Any]]):
    """Saves an image list back to a JSON file, preserving the original structure."""
    out_path = Path(json_path)
    if isinstance(original_data, dict):
        data = original_data.copy()
        data["images"] = images
    else:
        data = images

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def remove_paths_from_image_json(
    json_path: Path,
    targets: List[Path],
    base_dir: Optional[Path] = None,
    delete_files: bool = False,
) -> Dict[str, int]:
    """Removes entries from image JSON if their file paths match any target path."""
    original_data, images = load_image_json(json_path)

    base_dir = Path(base_dir) if base_dir else json_path.parent
    target_paths = {p.resolve() for p in targets}

    def should_remove(item: Dict[str, Any]) -> bool:
        for key in ("path", "thumbnail", "filename"):
            path_str = item.get(key)
            if path_str:
                p = Path(path_str)
                resolved_path = (
                    (base_dir / p).resolve() if not p.is_absolute() else p.resolve()
                )
                if resolved_path in target_paths:
                    return True
        return False

    kept_images = []
    removed_count, deleted_count, missing_count = 0, 0, 0

    for item in images:
        if should_remove(item):
            removed_count += 1
            if delete_files:
                for key in ("path", "thumbnail"):
                    path_str = item.get(key)
                    if path_str:
                        p = Path(path_str)
                        resolved_path = (
                            (base_dir / p).resolve()
                            if not p.is_absolute()
                            else p.resolve()
                        )
                        if resolved_path.is_file():
                            resolved_path.unlink()
                            deleted_count += 1
                        else:
                            missing_count += 1
        else:
            kept_images.append(item)

    if removed_count > 0:
        save_image_json(json_path, original_data, kept_images)

    return {
        "removed_from_json": removed_count,
        "deleted_files": deleted_count,
        "missing_files": missing_count,
    }
