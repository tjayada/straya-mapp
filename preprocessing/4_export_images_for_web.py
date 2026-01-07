import argparse
import json
from pathlib import Path
from typing import Optional, Dict, Any

from PIL import Image
from tqdm import tqdm


def generate_thumbnail(source_path: Path, output_path: Path, size: int) -> bool:
    """Generates a square, centered WebP thumbnail for an image."""
    try:
        with Image.open(source_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail((size, size), Image.Resampling.LANCZOS)

            width, height = img.size
            if width != height:
                crop_size = min(width, height)
                left, top = (width - crop_size) // 2, (height - crop_size) // 2
                img = img.crop((left, top, left + crop_size, top + crop_size))
                img = img.resize((size, size), Image.Resampling.LANCZOS)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "WEBP", quality=75, method=6)
            return True
    except Exception as e:
        print(f"Error generating thumbnail for {source_path.name}: {e}")
        return False


def resize_and_convert_image(
    source_path: Path, output_path: Path, max_dims: tuple[int, int], quality: int
) -> bool:
    """Resizes an image to fit within max_dims and converts it to WebP."""
    try:
        with Image.open(source_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail(max_dims, Image.Resampling.LANCZOS)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Strip metadata by creating a new image
            data = list(img.getdata())
            new_img = Image.new(img.mode, img.size)
            new_img.putdata(data)
            new_img.save(output_path, format="WEBP", quality=quality, method=6)
            return True
    except Exception as e:
        print(f"Error processing {source_path.name}: {e}")
        return False


def process_image(
    img_data: Dict[str, Any],
    json_path: Path,
    output_dir: Path,
    thumbnails_dir: Path,
    max_dims: tuple[int, int],
    quality: int,
    thumb_size: int,
) -> Optional[Dict[str, Any]]:
    """Processes a single image: resizes, converts, and generates a thumbnail."""
    source_path_str = img_data.get("path")
    if not source_path_str:
        return None

    source_path = Path(source_path_str)
    if not source_path.is_absolute():
        # Try resolving relative to JSON's parent dir
        source_path = (json_path.parent / source_path).resolve()

    if not source_path.is_file():
        return None

    base_name = Path(img_data.get("filename", source_path.name)).stem
    webp_filename = f"{base_name}.webp"
    output_path = output_dir / webp_filename
    thumbnail_path = thumbnails_dir / webp_filename

    if not resize_and_convert_image(source_path, output_path, max_dims, quality):
        return None
    if not generate_thumbnail(source_path, thumbnail_path, thumb_size):
        return None

    new_data = img_data.copy()
    new_data["path"] = str(output_path.relative_to(output_dir.parent))
    new_data["thumbnail"] = str(thumbnail_path.relative_to(output_dir.parent))
    return new_data


def export_web_images(
    json_path: Path,
    output_dir: Path,
    score_min: Optional[float],
    max_dims: tuple[int, int],
    quality: int,
    thumb_size: int,
    dry_run: bool,
):
    """Exports and optimizes images for web use based on specified criteria."""
    if not json_path.is_file():
        print(f"Error: JSON file not found: {json_path}")
        return 1

    with open(json_path, "r") as f:
        data = json.load(f)

    images = data.get("images", [])
    if not images:
        print("No images found in JSON.")
        return 1

    filtered_images = [
        img
        for img in images
        if score_min is None
        or (img.get("score") is not None and float(img["score"]) >= score_min)
    ]

    if not filtered_images:
        print("No images match the score criteria.")
        return 1

    thumbnails_dir = output_dir / "thumbnails"
    if dry_run:
        print(f"[DRY RUN] Would process {len(filtered_images)} images to {output_dir}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    exported_images = []
    for img_data in tqdm(filtered_images, desc="Processing images", unit="image"):
        result = process_image(
            img_data,
            json_path,
            output_dir,
            thumbnails_dir,
            max_dims,
            quality,
            thumb_size,
        )
        if result:
            exported_images.append(result)

    if not exported_images:
        print("No images were successfully processed.")
        return 1

    # Recalculate metadata and save new JSON
    final_scores = [img["score"] for img in exported_images if "score" in img]
    stats = {
        "totalImages": len(exported_images),
        "averageScore": sum(final_scores) / len(final_scores) if final_scores else None,
        "minScore": min(final_scores) if final_scores else None,
        "maxScore": max(final_scores) if final_scores else None,
    }
    exported_data = {"images": exported_images, "metadata": {"stats": stats}}

    exported_json_path = output_dir / "image_data.json"
    with open(exported_json_path, "w") as f:
        json.dump(exported_data, f, indent=2)

    print(
        f"\n✓ Successfully exported {len(exported_images)} images and created {exported_json_path}"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Export and optimize images for the web application."
    )
    parser.add_argument(
        "--json-path",
        type=str,
        default="frontend/web_export/image_data.json",
        help="Path to image_data.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="frontend/web_export",
        help="Output directory for processed images.",
    )
    parser.add_argument("--score-min", type=float, help="Minimum score to include.")
    parser.add_argument(
        "--max-width", type=int, default=1920, help="Maximum width for exported images."
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=1920,
        help="Maximum height for exported images.",
    )
    parser.add_argument("--quality", type=int, default=85, help="WebP quality (1-100).")
    parser.add_argument(
        "--thumbnail-size", type=int, default=96, help="Thumbnail size in pixels."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing.",
    )
    args = parser.parse_args()

    # Load from config.json
    with open("config.json", "r") as f:
        config = json.load(f)

    args.output_dir = config.get("export_path", args.output_dir)
    args.json_path = str(Path(args.output_dir) / "image_data.json")

    return export_web_images(
        json_path=Path(args.json_path).expanduser(),
        output_dir=Path(args.output_dir).expanduser(),
        score_min=args.score_min,
        max_dims=(args.max_width, args.max_height),
        quality=args.quality,
        thumb_size=args.thumbnail_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    exit(main())
