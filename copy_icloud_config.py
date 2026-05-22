#!/usr/bin/env python3
# copy_icloud_config.py

import argparse
import datetime
from dataclasses import dataclass
from typing import Optional

from media_common import positiveInt, resolvePath
from media_logging import metadataBool, metadataFloat, metadataInt


@dataclass
class CopyOptions:
    recursive: bool
    keepStructure: bool
    inputTxt: bool
    fromDate: Optional[datetime.datetime]
    toDate: Optional[datetime.datetime]
    dateOrder: str
    exiftoolPath: str
    writeXmp: bool
    noMetadata: bool
    verify: bool
    skipVideoMetadata: bool
    quiet: bool
    timeout: int
    maxWorkers: int
    copyRetries: int
    copyRetryDelay: float


@dataclass
class ResumeState:
    completedSources: set
    copiedDestinations: dict


def buildEffectiveCommand(args, src, dest):
    parts = ["python", "copy_icloud.py"]

    if args.input_txt:
        parts.append("--input-txt")

    if args.resume_csv:
        parts.extend(["--resume-csv", str(resolvePath(args.resume_csv))])

    if args.from_date:
        parts.extend(["--from-date", args.from_date])

    if args.to_date:
        parts.extend(["--to-date", args.to_date])

    if args.recursive:
        parts.append("--recursive")

    if args.keep_structure:
        parts.append("--keep-structure")

    parts.extend(["--workers", str(args.workers)])
    parts.extend(["--copy-workers", str(args.copy_workers)])
    parts.extend(["--copy-retries", str(args.copy_retries)])
    parts.extend(["--copy-retry-delay", str(args.copy_retry_delay)])
    parts.extend(["--checkpoint-seconds", str(args.checkpoint_seconds)])
    parts.extend(["--timeout", str(args.timeout)])
    parts.extend(["--date-order", args.date_order])
    parts.extend(["--exiftool", args.exiftool])

    if args.write_xmp:
        parts.append("--write-xmp")

    if args.no_metadata:
        parts.append("--no-metadata")

    if args.verify:
        parts.append("--verify")

    if args.skip_video_metadata:
        parts.append("--skip-video-metadata")

    if args.quiet:
        parts.append("--quiet")

    parts.extend(["--log-path", str(resolvePath(args.log_path))])
    parts.extend([str(src), str(dest)])

    return " ".join(f'"{part}"' if " " in part else part for part in parts)


def buildRunContext(args, src, dest):
    inputMode = "txt" if args.input_txt else ("file" if src.is_file() else "folder")

    return {
        "run_src": str(src),
        "run_dest": str(dest),
        "run_resume_csv": "" if args.resume_csv is None else str(resolvePath(args.resume_csv)),
        "run_input_mode": inputMode,
        "run_recursive": args.recursive,
        "run_keep_structure": args.keep_structure,
        "run_from_date": "" if args.from_date is None else args.from_date,
        "run_to_date": "" if args.to_date is None else args.to_date,
        "run_date_order": args.date_order,
        "run_exiftool": args.exiftool,
        "run_write_xmp": args.write_xmp,
        "run_no_metadata": args.no_metadata,
        "run_verify": args.verify,
        "run_skip_video_metadata": args.skip_video_metadata,
        "run_quiet": args.quiet,
        "run_timeout": args.timeout,
        "run_workers": args.workers,
        "run_copy_workers": args.copy_workers,
        "run_copy_retries": args.copy_retries,
        "run_copy_retry_delay": args.copy_retry_delay,
        "run_interrupted": False,
        "run_effective_command": buildEffectiveCommand(args, src, dest),
    }


def applyRunDefaults(args, resumeContext, inheritInputMode):
    if inheritInputMode and args.input_txt is False:
        args.input_txt = resumeContext.get("run_input_mode") == "txt"

    if args.recursive is None:
        args.recursive = metadataBool(resumeContext.get("run_recursive"), False)

    if args.keep_structure is None:
        args.keep_structure = metadataBool(resumeContext.get("run_keep_structure"), False)

    if not args.clear_date_filter:
        args.from_date = args.from_date if args.from_date is not None else (resumeContext.get("run_from_date") or None)
        args.to_date = args.to_date if args.to_date is not None else (resumeContext.get("run_to_date") or None)

    args.date_order = args.date_order if args.date_order is not None else (resumeContext.get("run_date_order") or "dmy")
    args.exiftool = args.exiftool if args.exiftool is not None else (resumeContext.get("run_exiftool") or "exiftool")

    if args.write_xmp is None:
        args.write_xmp = metadataBool(resumeContext.get("run_write_xmp"), False)

    if args.no_metadata is None:
        args.no_metadata = metadataBool(resumeContext.get("run_no_metadata"), False)

    if args.verify is None:
        args.verify = metadataBool(resumeContext.get("run_verify"), False)

    if args.skip_video_metadata is None:
        args.skip_video_metadata = metadataBool(resumeContext.get("run_skip_video_metadata"), False)

    args.timeout = args.timeout if args.timeout is not None else metadataInt(resumeContext.get("run_timeout"), 120)
    args.workers = args.workers if args.workers is not None else metadataInt(resumeContext.get("run_workers"), 8)
    args.copy_workers = args.copy_workers if args.copy_workers is not None else metadataInt(resumeContext.get("run_copy_workers"), 2)
    args.copy_retries = args.copy_retries if args.copy_retries is not None else metadataInt(resumeContext.get("run_copy_retries"), 5)
    args.copy_retry_delay = (
        args.copy_retry_delay
        if args.copy_retry_delay is not None
        else metadataFloat(resumeContext.get("run_copy_retry_delay"), 3.0)
    )


def parseArgs():
    parser = argparse.ArgumentParser(
        description="Copy iCloud media preserving Shell metadata and writing mappable metadata into files."
    )
    parser.add_argument("src", nargs="?", help="iCloud Photos source file/folder, or .txt file if --input-txt is used.")
    parser.add_argument("dest", nargs="?", help="Destination folder.")

    parser.add_argument("--input-txt", action="store_true", help="Treat src as a .txt file containing one media path per line.")
    parser.add_argument("--resume-csv", help="Resume from a previous copy_icloud CSV log. If src/dest are omitted, use the saved run context.")
    parser.add_argument("--from-date", help="Start date inclusive. Format: YYYY-MM-DD.")
    parser.add_argument("--to-date", help="End date inclusive. Format: YYYY-MM-DD.")
    parser.add_argument("--clear-date-filter", action="store_true", help="Clear date filters saved in the resume CSV.")

    parser.add_argument("-r", "--recursive", action="store_true", default=None, help="Process recursively when src is a folder.")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="Disable recursive processing when resuming.")
    parser.add_argument("-k", "--keep-structure", action="store_true", default=None, help="Keep source subfolders inside dest. Requires -r and folder src.")
    parser.add_argument("--no-keep-structure", dest="keep_structure", action="store_false", help="Disable keeping source subfolders when resuming.")

    parser.add_argument("--workers", type=positiveInt, default=None, help="General worker threads. Default: 8.")
    parser.add_argument("--copy-workers", type=positiveInt, default=None, help="Concurrent iCloud copy/download operations. Default: 2.")
    parser.add_argument("--copy-retries", type=positiveInt, default=None, help="Retries for iCloud copy timeout errors. Default: 5.")
    parser.add_argument("--copy-retry-delay", type=float, default=None, help="Base retry delay in seconds. Default: 3.")
    parser.add_argument("--checkpoint-seconds", type=float, default=60.0, help="Write a resumable checkpoint CSV every N seconds. Use 0 to disable. Default: 60.")

    parser.add_argument("--timeout", type=positiveInt, default=None, help="ExifTool timeout per file in seconds. Default: 120.")
    parser.add_argument("--date-order", choices=["dmy", "mdy"], default=None, help="Ambiguous Shell date order. Default: dmy.")
    parser.add_argument("--exiftool", default=None, help="ExifTool executable path. Default: exiftool.")
    parser.add_argument("--write-xmp", dest="write_xmp", action="store_true", default=None, help="Write Windows Shell metadata to .xmp sidecar files.")
    parser.add_argument("--no-write-xmp", dest="write_xmp", action="store_false", help="Do not write .xmp sidecar files when resuming.")
    parser.add_argument("--no-metadata", dest="no_metadata", action="store_true", default=None, help="Copy files without writing embedded metadata with ExifTool.")
    parser.add_argument("--write-metadata", dest="no_metadata", action="store_false", help="Write embedded metadata when resuming.")
    parser.add_argument("--verify", dest="verify", action="store_true", default=None, help="Verify that the destination metadata contains the expected date after writing.")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="Disable metadata verification when resuming.")
    parser.add_argument("--skip-video-metadata", dest="skip_video_metadata", action="store_true", default=None, help="Skip embedded metadata writing for video files.")
    parser.add_argument("--write-video-metadata", dest="skip_video_metadata", action="store_false", help="Write embedded video metadata when resuming.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-file copy messages. Errors, retries, checkpoints, and final logs are still printed.")
    parser.add_argument("--log-path", default="./logs", help="Folder where TXT and CSV logs are written. Default: ./logs.")

    return parser.parse_args()
