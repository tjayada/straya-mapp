import argparse
import json
import time
from pathlib import Path
from typing import List
from supabase import create_client, Client
from tqdm import tqdm


def upload_file_with_retry(
    supabase: Client,
    bucket: str,
    local_path: Path,
    storage_path: str,
    content_type: str,
    max_retries: int,
    retry_delay: float,
):
    """Uploads a file to Supabase Storage with exponential backoff on failure."""
    for attempt in range(max_retries):
        try:
            with open(local_path, "rb") as f:
                # Use upsert=True to either create or overwrite the file.
                supabase.storage.from_(bucket).upload(
                    path=storage_path.lstrip("/"),
                    file=f,
                    file_options={"upsert": True, "content-type": content_type},
                )
            return True, None
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "unauthorized" in error_msg.lower():
                return False, f"Authentication error: {error_msg}"

            if attempt < max_retries - 1:
                delay = retry_delay * (2**attempt)
                time.sleep(delay)
            else:
                return False, error_msg
    return False, f"Failed after {max_retries} attempts."


def retry_failed_uploads(
    failed_uploads_file: Path, supabase: Client, max_retries: int, retry_delay: float
):
    """Retries uploading files listed in the failed uploads JSON file."""
    if not failed_uploads_file.is_file():
        print(f"Error: Failed uploads file not found: {failed_uploads_file}")
        return 1

    with open(failed_uploads_file, "r") as f:
        failed_uploads = json.load(f).get("failed_uploads", [])

    if not failed_uploads:
        print("No failed uploads to retry.")
        return 0

    print(f"Retrying {len(failed_uploads)} failed uploads...")

    still_failed = []
    for item in tqdm(failed_uploads, desc="Retrying uploads", unit="file"):
        local_path = Path(item["local_path"])
        if not local_path.is_file():
            still_failed.append(item)
            continue

        success, _ = upload_file_with_retry(
            supabase,
            item["bucket"],
            local_path,
            item["storage_path"],
            item.get("content_type", "image/webp"),
            max_retries,
            retry_delay,
        )
        if not success:
            still_failed.append(item)

    if still_failed:
        with open(failed_uploads_file, "w") as f:
            json.dump({"failed_uploads": still_failed}, f, indent=2)
        print(
            f"\n{len(still_failed)} file(s) still failed. Updated {failed_uploads_file}"
        )
    else:
        backup_path = failed_uploads_file.with_suffix(".json.backup")
        failed_uploads_file.rename(backup_path)
        print(f"\nAll uploads succeeded! Renamed failed log to: {backup_path}")

    return 0 if not still_failed else 1


def upload_single_files(
    files_to_upload: List[str], supabase: Client, max_retries: int, retry_delay: float
):
    """Uploads a list of specified single files."""
    success_count, fail_count = 0, 0
    for file_spec in files_to_upload:
        try:
            local_path_str, bucket, storage_path = file_spec.split(":")
            local_path = Path(local_path_str).expanduser()
            content_type = (
                "application/json" if local_path.suffix == ".json" else "image/webp"
            )

            success, error = upload_file_with_retry(
                supabase,
                bucket,
                local_path,
                storage_path,
                content_type,
                max_retries,
                retry_delay,
            )
            if success:
                print(f"✓ Successfully uploaded {local_path.name}")
                success_count += 1
            else:
                print(f"✗ Failed to upload {local_path.name}: {error}")
                fail_count += 1
        except ValueError:
            print(
                f"Error: Invalid file spec format: {file_spec}. Expected local_path:bucket:storage_path"
            )
            fail_count += 1

    print(f"\nSummary: {success_count} successful, {fail_count} failed.")
    return 1 if fail_count > 0 else 0


def main():
    parser = argparse.ArgumentParser(
        description="Retry failed Supabase uploads or upload individual files."
    )
    parser.add_argument(
        "--supabase-url", required=True, type=str, help="Supabase project URL."
    )
    parser.add_argument(
        "--supabase-key", required=True, type=str, help="Supabase service role key."
    )
    parser.add_argument(
        "--failed-uploads-file",
        type=str,
        default="failed_uploads.json",
        help="JSON file with failed uploads to retry.",
    )
    parser.add_argument(
        "--file",
        type=str,
        action="append",
        help="Upload a single file in 'local_path:bucket:storage_path' format.",
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, help="Max retry attempts per file."
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Initial delay between retries in seconds.",
    )
    args = parser.parse_args()

    try:
        supabase = create_client(args.supabase_url, args.supabase_key)
    except Exception as e:
        print(f"Error initializing Supabase client: {e}")
        return 1

    if args.file:
        return upload_single_files(
            args.file, supabase, args.max_retries, args.retry_delay
        )
    else:
        return retry_failed_uploads(
            Path(args.failed_uploads_file), supabase, args.max_retries, args.retry_delay
        )


if __name__ == "__main__":
    exit(main())
