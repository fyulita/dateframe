#!/usr/bin/env python3
# media_common.py

import os
import sys
import re
import argparse
import datetime
import threading
import concurrent.futures
import subprocess
from pathlib import Path
from collections import defaultdict


# ----------------------
# Shared constants
# ----------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".arw", ".webp", ".dng", ".thm"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp"}
UNSUPPORTED_EMBED_WRITE = {".avi", ".mpg", ".mpeg"}

FILENAME_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")


# ----------------------
# Shared stats base
# ----------------------

class BaseStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = defaultdict(int)

    def inc(self, key, n=1):
        with self.lock:
            self.data[key] += n

    def summary(self):
        with self.lock:
            return dict(self.data)


# ----------------------
# Extension helpers
# ----------------------

def isImage(path):
    return Path(path).suffix.lower() in IMAGE_EXTS


def isVideo(path):
    return Path(path).suffix.lower() in VIDEO_EXTS


def isMedia(path):
    return isImage(path) or isVideo(path)


# ----------------------
# Path helpers
# ----------------------

def resolvePath(pathStr):
    return Path(os.path.normpath(pathStr)).expanduser().resolve()


def isSubpath(child: Path, parent: Path):
    try:
        child.resolve().relative_to(parent.resolve())

        return True

    except ValueError:
        return False


def relDirFor(pathStr, srcRootStr):
    rel = os.path.relpath(os.path.dirname(pathStr), start=srcRootStr)

    return "" if rel == "." else rel


def getUniqueFilename(filename, ext, pathDir):
    base = filename
    counter = 1

    while os.path.exists(os.path.join(pathDir, filename + ext)):
        filename = f"{base}_({counter})"
        counter += 1

    return filename


def reserveUniquePath(filename, ext, pathDir, reservedPaths, lock):
    base = filename
    counter = 1

    with lock:
        while True:
            candidate = Path(pathDir) / f"{filename}{ext}"
            key = str(candidate).lower()

            if not candidate.exists() and key not in reservedPaths:
                reservedPaths.add(key)

                return candidate

            filename = f"{base}_({counter})"
            counter += 1


def releaseReservedPath(path, reservedPaths, lock):
    with lock:
        reservedPaths.discard(str(path).lower())


# ----------------------
# File iteration helpers
# ----------------------

def readPathsFromTxt(txtPath, printLock=None):
    paths = []

    with open(txtPath, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            line = line.strip('"').strip("'")
            p = resolvePath(line)

            if p.exists() and p.is_file():
                paths.append(p)
            elif printLock:
                with printLock:
                    print(f"[SKIP INVALID PATH] {line}")

    return paths


def iterFiles(src, recursive, inputTxt=False, printLock=None):
    if inputTxt:
        return readPathsFromTxt(src, printLock=printLock)

    if src.is_file():
        return [src]

    if recursive:
        return [p for p in src.rglob("*") if p.is_file()]

    return [p for p in src.iterdir() if p.is_file()]


# ----------------------
# Date helpers
# ----------------------

def parseOptionalDateRange(fromDateStr, toDateStr):
    fromDate = None
    toDate = None

    if fromDateStr:
        fromDate = datetime.datetime.strptime(fromDateStr, "%Y-%m-%d")

    if toDateStr:
        toDate = datetime.datetime.strptime(toDateStr, "%Y-%m-%d")
        toDate = toDate.replace(hour=23, minute=59, second=59)

    return fromDate, toDate


def inDateRange(value, fromDate, toDate):
    if fromDate and value < fromDate:
        return False

    if toDate and value > toDate:
        return False

    return True


def datetimeToFilename(dt):
    return dt.strftime("%Y-%m-%dT%H-%M-%S")


def datetimeToExiftool(dt):
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def dtFromFilename(path):
    stem = Path(path).stem
    match = FILENAME_DT_RE.match(stem)

    if not match:
        return None

    y, mo, d, h, mi, s = match.groups()

    return f"{y}:{mo}:{d} {h}:{mi}:{s}"


# ----------------------
# Logging helpers
# ----------------------

def timestampedName(prefix, ext="txt"):
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    logDir = Path("logs")
    logDir.mkdir(exist_ok=True)

    return str(logDir / f"{prefix}_{stamp}.{ext}")


def saveSimpleLog(lines, filename):
    with open(filename, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")

    print(f"\nLog saved to {filename}")


def savePathList(paths, filename):
    with open(filename, "w", encoding="utf-8") as f:
        for path in sorted({str(p) for p in paths}):
            f.write(path + "\n")


# ----------------------
# Parallel helpers
# ----------------------

def defaultWorkers():
    cpu = os.cpu_count() or 4

    return min(64, cpu * 5)


def positiveInt(value):
    ivalue = int(value)

    if ivalue < 1:
        raise argparse.ArgumentTypeError("Value must be >= 1.")

    return ivalue


def runParallel(paths, workerFn, maxWorkers=None, stopEvent=None, onError=None):
    if not paths:
        return

    if maxWorkers is None or maxWorkers <= 0:
        maxWorkers = defaultWorkers()

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=maxWorkers)
    futures = {}

    try:
        for path in paths:
            if stopEvent and stopEvent.is_set():
                break

            futures[ex.submit(workerFn, path)] = path

        for fut, path in futures.items():
            if stopEvent and stopEvent.is_set():
                break

            try:
                fut.result()
            except KeyboardInterrupt:
                if stopEvent:
                    stopEvent.set()
                raise
            except Exception as e:
                if onError:
                    onError(path, e)

    except KeyboardInterrupt:
        for fut in futures:
            fut.cancel()

        ex.shutdown(wait=False, cancel_futures=True)
        raise

    else:
        ex.shutdown(wait=True, cancel_futures=False)


# ----------------------
# ExifTool helpers
# ----------------------

def cleanupExiftoolTmp(path, printLock=None, stats=None):
    path = Path(path)
    candidates = [
        Path(str(path) + "_exiftool_tmp"),
        path.with_name(path.name + "_exiftool_tmp"),
    ]

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
                if stats:
                    stats.inc("tmp_removed")
                if printLock:
                    with printLock:
                        print(f"[TMP REMOVED] {candidate}")

        except Exception as e:
            if printLock:
                with printLock:
                    print(f"[TMP REMOVE ERROR] {candidate}")
                    print(e)
                    print()


def runExiftool(exiftoolPath, argsList, dryRun=False, timeout=30, printLock=None, targetPath=None, stats=None, stopEvent=None):
    if stopEvent and stopEvent.is_set():
        return 130

    cmd = [exiftoolPath, "-overwrite_original", "-m", "-P"] + argsList

    if dryRun:
        if printLock:
            with printLock:
                printable = " ".join(f'"{part}"' if " " in part else part for part in cmd)
                print("DRY-RUN:", printable)

        return 0

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)

    except subprocess.TimeoutExpired:
        if targetPath:
            cleanupExiftoolTmp(targetPath, printLock=printLock, stats=stats)

        return 124

    except KeyboardInterrupt:
        if stopEvent:
            stopEvent.set()

        return 130

    if targetPath:
        cleanupExiftoolTmp(targetPath, printLock=printLock, stats=stats)

    if proc.returncode != 0 and proc.stderr:
        if printLock:
            with printLock:
                sys.stderr.write(proc.stderr.strip() + "\n")

    elif proc.stdout:
        out = proc.stdout.strip()
        if out and printLock:
            with printLock:
                print(out)

    return proc.returncode
