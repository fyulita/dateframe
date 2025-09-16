#!/usr/bin/env python3
# write_dates.py

import sys
import os
import threading
import concurrent.futures
from collections import defaultdict
import argparse
from pathlib import Path
import datetime
import re
import subprocess

# ----------------------
# Config / constants
# ----------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".arw", ".webp", ".dng", ".thm"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp"}

# Unsupported file extensions for embedding
UNSUPPORTED_EMBED_WRITE = {".avi", ".mpg", ".mpeg"}

# Expect names like: 2020-07-15T18-43-12.jpg  (prefix only; suffix like _(2) is ok)
FILENAME_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

# Global lock for log-safe prints if needed later
printLock = threading.Lock()


# ----------------------
# Stats helper
# ----------------------

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = defaultdict(int)
        self.failed_files = []
        self.skipped_files = []

    def inc(self, key):
        with self.lock:
            self.data[key] += 1

    def addFailed(self, filename):
        with self.lock:
            self.failed_files.append(filename)

    def addSkipped(self, filename):
        with self.lock:
            self.skipped_files.append(filename)

    def summary(self):
        return dict(self.data)

    def getFailed(self):
        return list(self.failed_files)

    def getSkipped(self):
        return list(self.skipped_files)


# ----------------------
# Logging
# ----------------------

def saveLog(stats, filename=f"write_metadata_{datetime.datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        for key in sorted(stats.summary().keys()):
            f.write(f"{key}: {stats.summary()[key]}\n")

        failed = stats.getFailed()
        if failed:
            f.write("\nFailed files:\n")
            for file in failed:
                f.write(file + "\n")

        skipped = stats.getSkipped()
        if skipped:
            f.write("\nSkipped files:\n")
            for file in skipped:
                f.write(file + "\n")

    print(f"\nLog saved to {filename}")


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
# Parallel runner
# ----------------------

def defaultWorkers():
    # IO-bound: many threads help; tune if needed.
    cpu = os.cpu_count() or 4
    return min(64, cpu * 5)

def runParallel(paths, workerFn, stats, timeout=30, max_workers=None):
    if not paths:
        return
    if max_workers is None or max_workers <= 0:
        max_workers = defaultWorkers()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(workerFn, p, timeout): p for p in paths}
        for fut, path in futures.items():
            try:
                fut.result(timeout=timeout + 5)  # small cushion over subprocess timeout
            except concurrent.futures.TimeoutError:
                with printLock:
                    print(f"Timeout in worker while processing: {path}")
                stats.inc("timeouts")
                stats.addFailed(str(path))
            except Exception:
                stats.inc("errors")
                stats.addFailed(str(path))


# ----------------------
# File helpers
# ----------------------

def isImage(path):
    return Path(path).suffix.lower() in IMAGE_EXTS

def isVideo(path):
    return Path(path).suffix.lower() in VIDEO_EXTS

def iterFiles(src, recursive):
    src = Path(src)
    if recursive:
        for p in src.rglob("*"):
            if p.is_file():
                yield p
    else:
        for p in src.iterdir():
            if p.is_file():
                yield p

def dtFromFilename(path):
    """
    Extract 'YYYY:MM:DD HH:MM:SS' from filename prefix 'YYYY-MM-DDTHH-MM-SS...'
    """
    stem = Path(path).stem
    m = FILENAME_DT_RE.match(stem)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}:{mo}:{d} {h}:{mi}:{s}"


# ----------------------
# Tag building
# ----------------------

def buildTagsForImage(dt, onlyIfMissing):
    tags = []
    def put(tag, val):
        # exiftool: -TAG-= only writes if tag is empty
        tags.append(f"-{tag}={val}" if not onlyIfMissing else f"-{tag}-={val}")
    put("EXIF:DateTimeOriginal", dt)
    put("EXIF:CreateDate", dt)
    put("XMP:CreateDate", dt)
    return tags

def buildTagsForVideo(dt, onlyIfMissing):
    tags = []
    def put(tag, val):
        tags.append(f"-{tag}={val}" if not onlyIfMissing else f"-{tag}-={val}")
    put("QuickTime:CreateDate", dt)
    put("QuickTime:TrackCreateDate", dt)
    put("QuickTime:MediaCreateDate", dt)
    put("XMP:CreateDate", dt)
    return tags

def buildXmpDateTags(dt, onlyIfMissing):
    # For sidecar XMP (e.g., AVI) where embedded write isn't supported
    tags = []
    def put(tag, val):
        tags.append(f"-{tag}={val}" if not onlyIfMissing else f"-{tag}-={val}")
    put("XMP:DateTimeOriginal", dt)
    put("XMP:CreateDate", dt)
    put("XMP:ModifyDate", dt)
    return tags

def buildFiletimeTags(dt):
    # Align filesystem timestamps as well.
    return [f"-FileModifyDate={dt}", f"-FileCreateDate={dt}"]


# ----------------------
# exiftool runner
# ----------------------

def runExiftool(exiftoolBin, argsList, dryRun=False, timeout=30):
    cmd = [exiftoolBin, "-overwrite_original", "-m", "-P"] + argsList
    if dryRun:
        with printLock:
            printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
            print("DRY-RUN:", printable)
        return 0
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        # Let caller classify as timeout; we return nonzero
        return 124
    if proc.returncode != 0:
        if proc.stderr:
            with printLock:
                sys.stderr.write(proc.stderr.strip() + "\n")
    else:
        out = proc.stdout.strip()
        if out:
            with printLock:
                print(out)
    return proc.returncode


# ----------------------
# Per-file processing
# ----------------------

def processOneFactory(src, recursive, dryRun, onlyIfMissing, setFiletime, exiftoolBin, stats):
    def processOne(path, timeout):
        dt = dtFromFilename(path)
        if not dt:
            stats.inc("skipped_no_date_in_name")
            stats.addSkipped(str(path))
            with printLock:
                print(f"[skip] {path} (no date found in filename)")
            return

        is_img = isImage(path)
        is_vid = isVideo(path)

        if not (is_img or is_vid):
            stats.inc("skipped_unsupported_ext")
            stats.addSkipped(str(path))
            with printLock:
                print(f"[skip] {path} (unsupported extension)")
            return

        ext = Path(path).suffix.lower()

        if ext in UNSUPPORTED_EMBED_WRITE:
            # Use XMP sidecar for AVI/MPG/MPEG
            tags = buildXmpDateTags(dt, onlyIfMissing)
            if setFiletime:
                tags += buildFiletimeTags(dt)
            argsList = tags + ["-o", "%d%f.xmp", str(path)]
        else:
            # Embedded write for supported formats
            if is_img:
                tags = buildTagsForImage(dt, onlyIfMissing)
            else:
                tags = buildTagsForVideo(dt, onlyIfMissing)
            if setFiletime:
                tags += buildFiletimeTags(dt)
            argsList = tags + [str(path)]

        rc = runExiftool(exiftoolBin, argsList, dryRun=dryRun, timeout=timeout)
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
    return processOne


# ----------------------
# Main
# ----------------------

def main():
    args = parseArgs()

    src = Path(os.path.normpath(args.src)).expanduser().resolve()
    if not src.exists() or not src.is_dir():
        print(f"Error: '{src}' does not exist or is not a directory.")
        sys.exit(2)

    stats = Stats()

    # Collect files up-front to avoid iterator invalidation and to parallelize
    files = list(iterFiles(src, args.recursive))
    if not files:
        print("No files found to process.")
        saveLog(stats)
        sys.exit(0)

    processOne = processOneFactory(
        src=str(src),
        recursive=args.recursive,
        dryRun=args.dry_run,
        onlyIfMissing=args.if_missing,
        setFiletime=args.set_filetime,
        exiftoolBin=args.exiftool,
        stats=stats,
    )

    try:
        runParallel(
            files,
            workerFn=processOne,
            stats=stats,
            timeout=args.timeout,
            max_workers=args.workers,
        )
    except KeyboardInterrupt:
        print("\nExecution interrupted by the user")
    finally:
        saveLog(stats)


if __name__ == "__main__":
    main()