from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List
import matplotlib.pyplot as plt
from PIL import Image
import importlib.util
from datetime import date


def load_images(json_path: Path) -> List[Dict[str, Any]]:
    """Loads an image list from a JSON file."""
    if not json_path.is_file():
        raise FileNotFoundError(f"Image JSON file not found: {json_path}")
    with open(json_path, "r") as f:
        data = json.load(f)
    return (
        data.get("images", [])
        if isinstance(data, dict)
        else data
        if isinstance(data, list)
        else []
    )


def natural_key(text: str) -> tuple:
    """Provides a key for natural sorting of strings containing numbers."""
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    )


def load_delete_helpers():
    """Dynamically loads the delete_helpers.py module if available."""
    try:
        module_path = Path(__file__).resolve().parent / "delete_helpers.py"
        spec = importlib.util.spec_from_file_location(
            "delete_helpers", str(module_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


DELETE_HELPER = load_delete_helpers()


class ImageReviewer:
    """A matplotlib-based GUI to review images and mark them for deletion."""

    def __init__(
        self, images: List[Dict[str, Any]], base_dir: Path, json_path: Path, **kwargs
    ):
        self.images = images
        self.base_dir = base_dir
        self.json_path = json_path
        self.output_path = kwargs.get("output_path")
        self.apply_deletions = kwargs.get("apply_deletions", False)
        self.auto_confirm = kwargs.get("auto_confirm", False)
        self.index = 0
        self.decisions = ["keep"] * len(images)
        self.fig, self.ax = plt.subplots(figsize=(10, 8))
        self.fig.canvas.manager.set_window_title("Image Reviewer")
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.update_display()

    def update_display(self):
        """Renders the current image and its status in the matplotlib window."""
        self.ax.clear()
        if not self.images:
            self.ax.text(0.5, 0.5, "No images to review.", ha="center", va="center")
        else:
            img_data = self.images[self.index]
            path_str = img_data.get("path") or img_data.get("thumbnail") or ""
            img_path = (
                self.base_dir / path_str
                if not Path(path_str).is_absolute()
                else Path(path_str)
            )

            try:
                with Image.open(img_path) as img:
                    self.ax.imshow(img)
            except Exception as e:
                self.ax.text(
                    0.5,
                    0.5,
                    f"Error opening {img_path.name}:\n{e}",
                    ha="center",
                    va="center",
                )

        status = self.decisions[self.index]
        title = f"{self.index + 1}/{len(self.images)} | {'DELETE' if status == 'delete' else 'KEEP'}"
        self.ax.set_title(title, color="red" if status == "delete" else "green")
        self.ax.axis("off")
        self.fig.canvas.draw_idle()

    def on_key(self, event: Any):
        """Handles key press events for navigation and marking."""
        key_map = {
            "up": lambda: self.mark("keep"),
            "down": lambda: self.mark("delete"),
            "right": self.next_image,
            "left": self.prev_image,
            "q": self.finish,
            "escape": self.finish,
        }
        if event.key in key_map:
            key_map[event.key]()

    def mark(self, decision: str):
        self.decisions[self.index] = decision
        self.next_image()

    def next_image(self):
        if self.index < len(self.images) - 1:
            self.index += 1
            self.update_display()
        else:
            self.finish()

    def prev_image(self):
        if self.index > 0:
            self.index -= 1
            self.update_display()

    def finish(self):
        """Saves results, optionally applies deletions, and closes the viewer."""
        plt.close(self.fig)
        if self.output_path:
            self.save_marks()
        if self.apply_deletions:
            self.apply_deletions_now()

    def save_marks(self):
        """Saves the review decisions to the specified output JSON file."""
        to_delete_count = sum(1 for d in self.decisions if d == "delete")
        payload = {
            "metadata": {
                "total": len(self.images),
                "marked_for_deletion": to_delete_count,
            },
            "images": [
                dict(img, delete=(dec == "delete"))
                for img, dec in zip(self.images, self.decisions)
            ],
        }
        with open(self.output_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(
            f"\nSaved decisions for {to_delete_count} deletions to {self.output_path}"
        )

    def apply_deletions_now(self):
        """Applies the deletions based on the review session."""
        to_delete = [
            self.base_dir / (img.get("path") or "")
            for img, dec in zip(self.images, self.decisions)
            if dec == "delete" and (img.get("path"))
        ]
        if not to_delete:
            print("\nNo files marked for deletion.")
            return

        if (
            not self.auto_confirm
            and input(f"\nDelete {len(to_delete)} files? [y/N]: ").lower() != "y"
        ):
            print("Deletion cancelled.")
            return

        if DELETE_HELPER:
            print(f"Using delete helper to update {self.json_path} and delete files.")
            res = DELETE_HELPER.remove_paths_from_image_json(
                json_path=self.json_path,
                targets=to_delete,
                base_dir=self.base_dir,
                delete_files=True,
            )
            print(
                f"Removed from JSON: {res['removed_from_json']}, Deleted: {res['deleted_files']}, Missing: {res['missing_files']}"
            )
        else:
            deleted = sum(1 for path in to_delete if path.unlink(missing_ok=True))
            print(f"\nDeleted {deleted} file(s).")

    def run(self):
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Review images and mark them for deletion."
    )
    parser.add_argument(
        "--image-json", required=True, type=str, help="Path to the image JSON file."
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        default=".",
        help="Base directory for resolving image paths.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="delete_marks.json",
        help="Output file for deletion marks.",
    )
    parser.add_argument(
        "--apply-deletions",
        action="store_true",
        help="Immediately delete marked files after review.",
    )
    parser.add_argument(
        "--sort-by",
        type=str,
        choices=["filename", "date"],
        default="date",
        help="Sort order for review.",
    )
    parser.add_argument("--yes", action="store_true", help="Auto-confirm deletions.")
    args = parser.parse_args()

    json_path = Path(args.image_json).expanduser().resolve()
    base_dir = Path(args.image_dir).expanduser().resolve()

    images = load_images(json_path)
    if not images:
        raise SystemExit("No images found in JSON file.")

    def sort_key(img):
        if args.sort_by == "date":
            ts = img.get("timestamp") or img.get("date")
            try:
                return (
                    date.fromisoformat(str(ts)[:10]),
                    natural_key(img.get("filename", "")),
                )
            except (ValueError, TypeError):
                return (date.max, natural_key(img.get("filename", "")))
        return natural_key(img.get("filename", ""))

    images.sort(key=sort_key)

    print(f"Found {len(images)} images. Launching reviewer...")
    print("Controls: ↓ delete | ↑ keep | ← previous | → next | q quit")

    reviewer = ImageReviewer(images, base_dir, json_path, **vars(args))
    reviewer.run()


if __name__ == "__main__":
    main()
