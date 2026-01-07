import argparse
import json
from pathlib import Path
from supabase import create_client, Client
from tqdm import tqdm


def upload_file(
    supabase: Client, bucket: str, local_path: Path, storage_path: str, upsert: bool
):
    """Uploads a single file to a Supabase storage bucket."""
    try:
        with open(local_path, "rb") as f:
            content_type = (
                "application/json" if local_path.suffix == ".json" else "image/webp"
            )
            supabase.storage.from_(bucket).upload(
                path=storage_path.lstrip("/"),
                file=f,
                file_options={"upsert": upsert, "content-type": content_type},
            )
        return True
    except Exception as e:
        print(f"Error uploading {local_path.name}: {e}")
        return False


def upload_directory(
    supabase: Client, bucket: str, local_dir: Path, files: set, upsert: bool, desc: str
):
    """Uploads all files from a local directory to a Supabase bucket."""
    if not local_dir.is_dir():
        print(f"Warning: Directory not found, skipping: {local_dir}")
        return 0, len(files)

    success_count, fail_count = 0, 0
    failed_uploads = []

    for filename in tqdm(files, desc=desc, unit="file"):
        local_path = local_dir / filename
        if not local_path.is_file():
            fail_count += 1
            continue

        if upload_file(supabase, bucket, local_path, filename, upsert):
            success_count += 1
        else:
            fail_count += 1
            failed_uploads.append(
                {
                    "bucket": bucket,
                    "local_path": str(local_path),
                    "storage_path": filename,
                }
            )

    print(f"{desc}: {success_count} successful, {fail_count} failed.")
    return failed_uploads


def main():
    parser = argparse.ArgumentParser(
        description="Upload web-exported assets to Supabase Storage."
    )
    parser.add_argument(
        "--web-export-dir",
        type=str,
        default="frontend/web_export",
        help="Directory of web-exported files.",
    )
    parser.add_argument(
        "--supabase-url", type=str, required=True, help="Supabase project URL."
    )
    parser.add_argument(
        "--supabase-key", type=str, required=True, help="Supabase service role key."
    )
    parser.add_argument(
        "--images-bucket", type=str, default="images", help="Bucket for main images."
    )
    parser.add_argument(
        "--thumbnails-bucket",
        type=str,
        default="thumbnails",
        help="Bucket for thumbnails.",
    )
    parser.add_argument(
        "--data-bucket", type=str, default="data", help="Bucket for image_data.json."
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Prevent overwriting existing files.",
    )
    args = parser.parse_args()

    web_export_dir = Path(args.web_export_dir).expanduser()
    image_data_path = web_export_dir / "image_data.json"

    if not image_data_path.is_file():
        print(f"Error: image_data.json not found in {web_export_dir}")
        return 1

    try:
        supabase = create_client(args.supabase_url, args.supabase_key)
    except Exception as e:
        print(f"Error initializing Supabase client: {e}")
        return 1

    with open(image_data_path, "r") as f:
        image_data = json.load(f)
    images = image_data.get("images", [])
    if not images:
        print("No images found in JSON file.")
        return 1

    print(f"Found {len(images)} image entries. Starting upload...")

    upsert = not args.no_overwrite
    all_failed_uploads = []

    # Upload main images
    image_files = {Path(img["path"]).name for img in images if "path" in img}
    failed = upload_directory(
        supabase,
        args.images_bucket,
        web_export_dir,
        image_files,
        upsert,
        "Uploading images",
    )
    all_failed_uploads.extend(failed)

    # Upload thumbnails
    thumbnail_files = {
        Path(img["thumbnail"]).name for img in images if "thumbnail" in img
    }
    failed = upload_directory(
        supabase,
        args.thumbnails_bucket,
        web_export_dir / "thumbnails",
        thumbnail_files,
        upsert,
        "Uploading thumbnails",
    )
    all_failed_uploads.extend(failed)

    # Update paths in image_data.json for Supabase
    for img in image_data["images"]:
        if "path" in img:
            img["path"] = f"{args.images_bucket}/{Path(img['path']).name}"
        if "thumbnail" in img:
            img["thumbnail"] = f"{args.thumbnails_bucket}/{Path(img['thumbnail']).name}"

    # Save and upload the updated JSON
    supabase_json_path = web_export_dir / "image_data_supabase.json"
    with open(supabase_json_path, "w") as f:
        json.dump(image_data, f, indent=2)

    print("\nUploading image_data.json...")
    if upload_file(
        supabase, args.data_bucket, supabase_json_path, "image_data.json", upsert
    ):
        print("✓ Successfully uploaded image_data.json.")
        print(f"✓ Updated JSON for Supabase saved locally to {supabase_json_path}")
    else:
        print("✗ Failed to upload image_data.json.")
        all_failed_uploads.append(
            {
                "bucket": args.data_bucket,
                "local_path": str(supabase_json_path),
                "storage_path": "image_data.json",
            }
        )

    # Save failed uploads if any
    if all_failed_uploads:
        failed_uploads_file = Path("failed_uploads.json")
        with open(failed_uploads_file, "w") as f:
            json.dump({"failed_uploads": all_failed_uploads}, f, indent=2)
        print(
            f"\n{len(all_failed_uploads)} failed uploads saved to {failed_uploads_file}."
        )
        print("Run the `retry_upload.py` script to retry them.")
        return 1

    print("\nUpload process completed successfully.")
    return 0


if __name__ == "__main__":
    exit(main())
