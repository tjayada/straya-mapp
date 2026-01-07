import sys
import json
import subprocess
import argparse
from pathlib import Path
from remove_duplicates import apply_threshold


def cleanup_metadata_files(image_dir, dry_run=False, remove_videos=False):
    """Recursively remove macOS metadata files (._*, .DS_Store) and optionally video files."""
    image_dir = Path(image_dir).expanduser()

    if not image_dir.is_dir():
        raise NotADirectoryError(f"Directory not found: {image_dir}")

    # Collect files to remove
    files_to_remove = list(image_dir.rglob("._*")) + list(image_dir.rglob(".DS_Store"))

    if remove_videos:
        video_extensions = (".mov", ".mp4", ".MOV", ".MP4")
        for ext in video_extensions:
            files_to_remove.extend(image_dir.rglob(f"*{ext}"))

    files_to_remove = sorted(list(set(files_to_remove)))

    if not files_to_remove:
        print(f"No metadata or video files found in {image_dir}")
        return

    kind = "metadata and video files" if remove_videos else "metadata files"
    print(
        f"Found {len(files_to_remove)} {kind} to {'remove' if not dry_run else 'remove (dry run)'}:"
    )

    total_size = 0
    for file_path in files_to_remove:
        try:
            size = file_path.stat().st_size
            total_size += size
            if not dry_run:
                file_path.unlink()
        except Exception as e:
            print(f"    Error removing {file_path.name}: {e}")

    total_mb = total_size / (1024 * 1024)
    if not dry_run:
        print(f"\n✓ Cleaned up {len(files_to_remove)} files, freed {total_mb:.2f} MB")
    else:
        print(f"\nWould free {total_mb:.2f} MB")


def main():
    parser = argparse.ArgumentParser(
        description="Export images from macOS Photos and deduplicate."
    )
    parser.add_argument("--library-path", type=str, help="Path to Photos library.")
    parser.add_argument(
        "--from-date", type=str, help="Start date for export (YYYY-MM-DD)."
    )
    parser.add_argument("--to-date", type=str, help="End date for export (YYYY-MM-DD).")
    parser.add_argument(
        "--export-path",
        type=str,
        default="frontend/web_export",
        help="Directory for exported images.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a dry run without exporting or deleting.",
    )
    parser.add_argument(
        "--remove-videos",
        action="store_true",
        help="Remove video files from export directory.",
    )
    args = parser.parse_args()

    # Load config and override with args
    with open("config.json", "r") as f:
        config = json.load(f)

    args.library_path = args.library_path or config.get("library_path")
    args.from_date = args.from_date or config.get("start_date")
    args.to_date = args.to_date or config.get("end_date")
    args.export_path = args.export_path or config.get("export_path")

    # Deduplication settings from config
    dedup_threshold = config.get("deduplication_threshold", 8)
    dedup_hash_size = config.get("deduplication_hash_size", 8)
    dedup_delete = config.get("deduplication_delete", True)

    export_dir = Path(args.export_path).expanduser()
    export_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "uv",
        "run",
        "osxphotos",
        "export",
        args.export_path,
        "--library",
        args.library_path,
        "--location",
        "--only-photos",
        "--skip-original-if-edited",
        "--skip-live",
        "--skip-bursts",
    ]
    if args.from_date:
        command.extend(["--from-date", args.from_date])
    if args.to_date:
        command.extend(["--to-date", args.to_date])
    if args.dry_run:
        command.append("--dry-run")

    subprocess.run(command)

    # Clean up metadata files
    print("\nStarting metadata cleanup...")
    try:
        cleanup_metadata_files(
            args.export_path, dry_run=args.dry_run, remove_videos=args.remove_videos
        )
    except Exception as e:
        print(f"Error during metadata cleanup: {e}")
        sys.exit(1)
    print("Metadata cleanup completed.")

    exported_files_count = len(list(Path(args.export_path).rglob("*.*")))
    print(f"\nTotal exported files: {exported_files_count}")

    # Cluster and remove similar images
    print("\nClustering and removing similar images...")
    print(
        f"Applying threshold {dedup_threshold} with hash size {dedup_hash_size} (delete={dedup_delete})..."
    )

    try:
        result = apply_threshold(
            args.export_path,
            threshold=dedup_threshold,
            hash_size=dedup_hash_size,
            delete=dedup_delete and not args.dry_run,
        )
    except NotADirectoryError as e:
        print(e)
        return

    if not result["clusters"]:
        print("No similar images found for threshold", dedup_threshold)
    else:
        print(
            f"Threshold {dedup_threshold}: {len(result['clusters'])} cluster(s), {len(result['to_delete'])} file(s) marked for removal."
        )

    if dedup_delete and not args.dry_run:
        print(
            f"\nDeleted {result['deleted_count']} file(s). Failed: {result['failed_delete_count']}"
        )
    elif not args.dry_run:
        print("\nDry run: no files deleted.")


if __name__ == "__main__":
    main()
    exit(0)
