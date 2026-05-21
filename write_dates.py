#!/usr/bin/env python3
# write_dates.py

import sys
import threading
import argparse

from media_common import (
    BaseStats,
    UNSUPPORTED_EMBED_WRITE,
    dtFromFilename,
    isImage,
    isVideo,
    iterFiles,
    resolvePath,
    runExiftool,
    runParallel,
    saveSimpleLog,
    timestampedName,
)


# ----------------------
# Config / constants
# ----------------------

printLock = threading.Lock()


# ----------------------
# Stats helper
# ----------------------

class Stats(BaseStats):
    def __init__(self):
        super().__init__()
        self.failed_files = []
        self.skipped_files = []

    def addFailed(self, filename):
        with self.lock:
            self.failed_files.append(filename)

    def addSkipped(self, filename):
        with self.lock:
            self.skipped_files.append(filename)

    def getFailed(self):
        with self.lock:
            return list(self.failed_files)

    def getSkipped(self):
        with self.lock:
            return list(self.skipped_files)


# ----------------------
# Logging
# ----------------------

def saveLog(stats, filename=None):
    if filename is None:
        filename = timestampedName("write_metadata")

    lines = []

    for key in sorted(stats.summary().keys()):
        lines.append(f"{key}: {stats.summary()[key]}")

    failed = stats.getFailed()
    if failed:
        lines.append("")
        lines.append("Failed files:")
        lines.extend(failed)

    skipped = stats.getSkipped()
    if skipped:
        lines.append("")
        lines.append("Skipped files:")
        lines.extend(skipped)

    saveSimpleLog(lines, filename)


# ----------------------
# CLI
# ----------------------

def parseArgs():
    parser = argparse.ArgumentParser(
        description="Write capture/create date metadata based on filename (YYYY-MM-DDTHH-MM-SS)."
    )
    parser.add_argument("src", help="Source folder (files already renamed).")
    parser.add_argument("-r", "--recursive", action="store_true", help="Process recursively (including subfolders).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without applying changes.")
    parser.add_argument("--if-missing", action="store_true", help="Only write tags if they are currently empty.")
    parser.add_argument("--set-filetime", action="store_true", help="Also set filesystem dates (FileModifyDate/CreateDate).")
    parser.add_argument("--exiftool", default="exiftool", help="Path to exiftool binary (default: exiftool in PATH).")
    parser.add_argument("--timeout", type=int, default=30, help="Per-file timeout in seconds (default: 30).")
    parser.add_argument("--workers", type=int, default=0, help="Max threads (0 = auto).")

    return parser.parse_args()


# ----------------------
# Tag building
# ----------------------

def buildTagsForImage(dt, onlyIfMissing):
    tags = []

    def put(tag, value):
        tags.append(f"-{tag}={value}")

    put("EXIF:DateTimeOriginal", dt)
    put("EXIF:CreateDate", dt)
    put("XMP:CreateDate", dt)

    return tags


def buildTagsForVideo(dt, onlyIfMissing):
    tags = []

    def put(tag, value):
        tags.append(f"-{tag}={value}")

    put("QuickTime:CreateDate", dt)
    put("QuickTime:TrackCreateDate", dt)
    put("QuickTime:MediaCreateDate", dt)
    put("XMP:CreateDate", dt)

    return tags


def buildXmpDateTags(dt, onlyIfMissing):
    tags = []

    def put(tag, value):
        tags.append(f"-{tag}={value}")

    put("XMP:DateTimeOriginal", dt)
    put("XMP:CreateDate", dt)
    put("XMP:ModifyDate", dt)

    return tags


def buildFiletimeTags(dt):
    return [f"-FileModifyDate={dt}", f"-FileCreateDate={dt}"]


# ----------------------
# Main
# ----------------------

def main():
    args = parseArgs()

    src = resolvePath(args.src)
    if not src.exists() or not src.is_dir():
        print(f"Error: '{src}' does not exist or is not a directory.")
        sys.exit(2)

    stats = Stats()
    files = list(iterFiles(src, args.recursive))

    if not files:
        print("No files found to process.")
        saveLog(stats)
        sys.exit(0)

    def processOne(path):
        dt = dtFromFilename(path)

        if not dt:
            stats.inc("skipped_no_date_in_name")
            stats.addSkipped(str(path))
            with printLock:
                print(f"[skip] {path} (no date found in filename)")
            return

        isImg = isImage(path)
        isVid = isVideo(path)

        if not (isImg or isVid):
            stats.inc("skipped_unsupported_ext")
            stats.addSkipped(str(path))
            with printLock:
                print(f"[skip] {path} (unsupported extension)")
            return

        ext = path.suffix.lower()

        if ext in UNSUPPORTED_EMBED_WRITE:
            tags = buildXmpDateTags(dt, args.if_missing)
            if args.set_filetime:
                tags += buildFiletimeTags(dt)
            argsList = tags + ["-o", "%d%f.xmp", str(path)]
        else:
            tags = buildTagsForImage(dt, args.if_missing) if isImg else buildTagsForVideo(dt, args.if_missing)
            if args.set_filetime:
                tags += buildFiletimeTags(dt)
            argsList = tags + [str(path)]

        if args.if_missing:
            argsList = ["-wm", "cg"] + argsList

        rc = runExiftool(
            exiftoolPath=args.exiftool,
            argsList=argsList,
            dryRun=args.dry_run,
            timeout=args.timeout,
            printLock=printLock,
            targetPath=path,
            stats=stats,
        )

        if rc == 0:
            if ext in UNSUPPORTED_EMBED_WRITE:
                stats.inc("written_sidecars")
                kind = "xmp"
            else:
                stats.inc("written")
                kind = "meta"

            with printLock:
                print(f"[ok:{kind}] {path} <- {dt}")
        elif rc == 124:
            stats.inc("timeouts")
            stats.addFailed(str(path))
            with printLock:
                print(f"[timeout] {path}")
        else:
            stats.inc("errors")
            stats.addFailed(str(path))
            with printLock:
                print(f"[err]  {path}")

    def onError(path, _error):
        stats.inc("errors")
        stats.addFailed(str(path))

    try:
        runParallel(
            files,
            workerFn=processOne,
            maxWorkers=args.workers,
            onError=onError,
        )
    except KeyboardInterrupt:
        print("\nExecution interrupted by the user")
    finally:
        saveLog(stats)


if __name__ == "__main__":
    main()
