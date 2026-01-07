from pathlib import Path
import json
import argparse
import torch
from PIL import Image, ExifTags
from tqdm import tqdm
from datetime import datetime
import math
from aesthetic_predictor_v2_5 import convert_v2_5_from_siglip


def get_device():
    """Detects and returns the best available torch device."""
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def get_decimal_from_dms(dms, ref):
    """Converts DMS (degrees, minutes, seconds) to decimal degrees."""
    degrees, minutes, seconds = dms[0], dms[1] / 60.0, dms[2] / 3600.0
    sign = -1 if ref in ["S", "W"] else 1
    return sign * (degrees + minutes + seconds)


def get_exif_location(image_path):
    """Extracts GPS latitude and longitude from image EXIF data."""
    try:
        image = Image.open(image_path)
        exifdata = image.getexif()
        if not exifdata:
            return None, None

        gps_ifd = exifdata.get_ifd(0x8825)  # GPS IFD tag ID
        if not gps_ifd:
            return None, None

        gps_data = {ExifTags.GPSTAGS.get(key, key): val for key, val in gps_ifd.items()}

        lat_data = gps_data.get("GPSLatitude")
        lon_data = gps_data.get("GPSLongitude")
        lat_ref = gps_data.get("GPSLatitudeRef")
        lon_ref = gps_data.get("GPSLongitudeRef")

        if lat_data and lon_data and lat_ref and lon_ref:
            lat = get_decimal_from_dms(lat_data, lat_ref)
            lon = get_decimal_from_dms(lon_data, lon_ref)
            return lat, lon
    except Exception:
        return None, None
    return None, None


def get_exif_datetime(image_path):
    """Extracts the original or creation datetime from image EXIF data."""
    try:
        image = Image.open(image_path)
        exifdata = image.getexif()
        if not exifdata:
            return None

        # Tag 36867 is DateTimeOriginal, 306 is DateTime
        datetime_str = exifdata.get(36867) or exifdata.get(306)
        if datetime_str:
            return datetime.strptime(datetime_str, "%Y:%m:%d %H:%M:%S").isoformat()
    except Exception:
        return None
    return None


def load_existing_results(output_file):
    """Loads existing results from a JSON file, if available."""
    if not output_file.exists():
        return {"images": [], "metadata": {}}
    try:
        with open(output_file, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load {output_file}: {e}")
        return {"images": [], "metadata": {}}


def save_results(output_file, results, metadata):
    """Saves results and metadata to a JSON file atomically."""
    output_data = {"images": results, "metadata": metadata}
    temp_file = output_file.with_suffix(".json.tmp")
    with open(temp_file, "w") as f:
        json.dump(output_data, f, indent=2)
    temp_file.replace(output_file)


def compute_score_bins(scores, bins=10):
    """Computes equal-width bins for a list of scores."""
    if not scores:
        return []

    smin, smax = min(scores), max(scores)
    if smin == smax:
        return {
            "ranges": [
                {
                    "min": smin,
                    "max": smax,
                    "count": len(scores),
                    "cumulative": len(scores),
                }
            ],
            "step": 0.0,
            "lower": smin,
        }

    raw_step = (smax - smin) / float(bins)

    def nicenum(x):
        if x <= 0:
            return x
        exp = math.floor(math.log10(x))
        f = x / (10**exp)
        nf = 1 if f < 1.5 else 2 if f < 3 else 5 if f < 7 else 10
        return nf * (10**exp)

    step = nicenum(raw_step)
    lower = math.floor(smin / step) * step

    ranges = [
        {"min": lower + i * step, "max": lower + (i + 1) * step, "count": 0}
        for i in range(bins)
    ]

    for s in scores:
        idx = min(bins - 1, max(0, int((s - lower) / step))) if s != smax else bins - 1
        ranges[idx]["count"] += 1

    cum = 0
    for r in ranges:
        cum += r["count"]
        r["cumulative"] = cum

    return {"ranges": ranges, "step": step, "lower": lower}


def process_images_in_batches(
    image_paths,
    model,
    preprocessor,
    device,
    batch_size,
    output_file,
    checkpoint_interval,
    existing_results,
):
    """Processes images in batches to get aesthetic scores and EXIF data."""
    processed_paths = {
        str(Path(img["path"]).resolve()) for img in existing_results.get("images", [])
    }
    remaining_paths = [
        p for p in image_paths if str(p.resolve()) not in processed_paths
    ]

    if processed_paths and remaining_paths:
        print(
            f"Resuming: {len(processed_paths)} images already processed, {len(remaining_paths)} remaining."
        )
    if not remaining_paths:
        return existing_results.get("images", []), []

    results = existing_results.get("images", []).copy()
    skipped_images = []

    with tqdm(
        total=len(remaining_paths), desc="Processing images", unit="img", ncols=100
    ) as progress_bar:
        for i in range(0, len(remaining_paths), batch_size):
            batch_paths = remaining_paths[i : i + batch_size]
            batch_images, valid_paths = [], []
            for path in batch_paths:
                try:
                    batch_images.append(Image.open(path).convert("RGB"))
                    valid_paths.append(path)
                except Exception as e:
                    skipped_images.append(str(path))
                    if not path.name.startswith("._"):
                        progress_bar.write(f"Warning: Skipping {path.name}: {e}")

            if not batch_images:
                progress_bar.update(len(batch_paths))
                continue

            pixel_values = (
                preprocessor(images=batch_images, return_tensors="pt")
                .pixel_values.to(torch.bfloat16)
                .to(device)
            )
            with torch.inference_mode():
                scores = model(pixel_values).logits.squeeze().float().cpu().numpy()

            scores = scores.tolist() if scores.ndim > 0 else [scores.item()]

            for path, score in zip(valid_paths, scores):
                lat, lng = get_exif_location(path)
                datetime_str = get_exif_datetime(path)
                date_str = (
                    datetime_str.split("T")[0]
                    if datetime_str and "T" in datetime_str
                    else None
                )

                results.append(
                    {
                        "filename": path.name,
                        "path": str(path),
                        "score": float(score),
                        "lat": lat,
                        "lng": lng,
                        "timestamp": datetime_str,
                        "date": date_str,
                    }
                )

            progress_bar.update(len(batch_paths))

            if output_file and (i // batch_size + 1) % checkpoint_interval == 0:
                current_scores = [r["score"] for r in results if "score" in r]
                metadata = {
                    "stats": {
                        "totalImages": len(results),
                        "averageScore": sum(current_scores) / len(current_scores)
                        if current_scores
                        else None,
                    }
                }
                save_results(output_file, results, metadata)

    return results, skipped_images


def main(image_dir, batch_size, output_file, checkpoint_interval, bins):
    """Main function to run the image processing pipeline."""
    image_dir_path = Path(image_dir).expanduser()
    if not image_dir_path.is_dir():
        raise NotADirectoryError(f"Image directory not found: {image_dir_path}")

    output_file_path = Path(output_file).expanduser()
    existing_results = load_existing_results(output_file_path)

    all_files = list(image_dir_path.rglob("*.*", recursive=True))
    image_paths = [
        f for f in all_files if not f.name.startswith("._") and f.name != ".DS_Store"
    ]

    if not image_paths:
        print(f"No valid images found in {image_dir_path}")
        return

    device = get_device()
    print(f"Using device: {device}")

    model, preprocessor = convert_v2_5_from_siglip(
        low_cpu_mem_usage=True, trust_remote_code=True
    )
    model = model.to(torch.bfloat16).to(device)
    model.eval()
    print("Model loaded.")

    results, skipped_files = process_images_in_batches(
        image_paths,
        model,
        preprocessor,
        device,
        batch_size,
        output_file_path,
        checkpoint_interval,
        existing_results,
    )

    if skipped_files:
        print(
            f"\n{len(skipped_files)} images couldn't be processed (corrupt/unreadable)."
        )
        if (
            input("Delete these files from disk? [y/N]: ")
            .strip()
            .lower()
            .startswith("y")
        ):
            deleted = sum(1 for p in skipped_files if Path(p).unlink(missing_ok=True))
            print(f"Deleted {deleted} files.")

    scores = [r["score"] for r in results if "score" in r]
    if not scores:
        print("No scores were generated.")
        return

    bins_info = compute_score_bins(scores, bins=bins)
    if bins_info and bins_info.get("ranges"):
        print("\nScore distribution:")
        for i, b in enumerate(bins_info["ranges"], 1):
            print(
                f"  Bin {i}: {b['min']:.2f} - {b['max']:.2f} -> {b['count']} images (removes: {b['cumulative']}/{len(scores)})"
            )

    try:
        threshold_resp = input(
            "\nEnter minimum score to delete images below (or Enter to skip): "
        ).strip()
        if threshold_resp:
            threshold = float(threshold_resp)
            to_remove = [r for r in results if r.get("score", float("inf")) < threshold]
            if to_remove and input(
                f"Marked {len(to_remove)} images for removal. Also delete files? [y/N]: "
            ).strip().lower().startswith("y"):
                deleted_files = sum(
                    1 for r in to_remove if Path(r["path"]).unlink(missing_ok=True)
                )
                print(f"Deleted {deleted_files} files.")
            results = [r for r in results if r.get("score", float("inf")) >= threshold]
    except (ValueError, EOFError, KeyboardInterrupt):
        print("Skipping deletion.")

    # Final save
    final_scores = [r["score"] for r in results if "score" in r]
    images_with_location = [r for r in results if r.get("lat") and r.get("lng")]
    images_with_dates = [r for r in results if r.get("date")]
    date_range = (
        {
            "min": min(r["date"] for r in images_with_dates),
            "max": max(r["date"] for r in images_with_dates),
        }
        if images_with_dates
        else None
    )

    stats = {
        "totalImages": len(results),
        "imagesWithLocation": len(images_with_location),
        "imagesWithDates": len(images_with_dates),
        "averageScore": sum(final_scores) / len(final_scores) if final_scores else None,
        "minScore": min(final_scores) if final_scores else None,
        "maxScore": max(final_scores) if final_scores else None,
    }

    metadata = {"dateRange": date_range, "stats": stats}
    save_results(output_file_path, results, metadata)

    print(f"\n✓ Processed {len(results)} images. Results saved to {output_file_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process images with an aesthetic predictor model."
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        default="frontend/web_export/",
        help="Directory of images.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=8, help="Batch size for processing."
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="frontend/web_export/image_data.json",
        help="Output JSON file.",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=1,
        help="Save checkpoint every N batches.",
    )
    parser.add_argument(
        "--bins", type=int, default=10, help="Number of score bins for histogram."
    )
    args = parser.parse_args()

    with open("config.json", "r") as f:
        config = json.load(f)

    args.image_dir = config.get("export_path", args.image_dir)
    args.output_file = str(Path(args.image_dir) / "image_data.json")
    args.batch_size = config.get("model_batch_size", args.batch_size)

    main(
        image_dir=args.image_dir,
        batch_size=args.batch_size,
        output_file=args.output_file,
        checkpoint_interval=args.checkpoint_interval,
        bins=args.bins,
    )
