#!/usr/bin/env python3
# copy_icloud.py

import sys
import os
import re
import html
import time
import shutil
import argparse
import datetime
import threading
import concurrent.futures
import subprocess
from pathlib import Path
from collections import defaultdict

import pythoncom
from win32com.client import Dispatch


# ----------------------
# Config / constants
# ----------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".arw", ".webp", ".dng", ".thm"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp"}

UNSUPPORTED_EMBED_WRITE = {".avi", ".mpg", ".mpeg"}

DATE_COLUMNS_IMAGE = ["Date taken", "Media created", "Date acquired", "Content created"]
DATE_COLUMNS_VIDEO = ["Media created", "Date taken", "Date acquired", "Content created"]

SHELL_TO_EXIFTOOL_IMAGE = {
    "Date taken": ["EXIF:DateTimeOriginal", "EXIF:CreateDate", "XMP:DateTimeOriginal", "XMP:CreateDate"],
    "Camera maker": ["EXIF:Make", "XMP:Make"],
    "Camera model": ["EXIF:Model", "XMP:Model"],
    "Authors": ["XMP:Creator"],
    "Title": ["XMP:Title"],
    "Subject": ["XMP:Subject"],
    "Tags": ["XMP:Subject"],
    "Comments": ["EXIF:UserComment", "XMP:Description"],
}

SHELL_TO_EXIFTOOL_VIDEO = {
    "Date taken": ["QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate", "XMP:CreateDate"],
    "Media created": ["QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate", "XMP:CreateDate"],
    "Camera maker": ["Keys:Make", "XMP:Make"],
    "Camera model": ["Keys:Model", "XMP:Model"],
    "Title": ["XMP:Title"],
    "Subject": ["XMP:Subject"],
    "Tags": ["XMP:Subject"],
    "Comments": ["XMP:Description"],
}

filenameLock = threading.Lock()
printLock = threading.Lock()
copySemaphore = None
stopEvent = threading.Event()
reservedPaths = set()


# ----------------------
# Stats helper
# ----------------------

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = defaultdict(int)

        self.copyErroredImages = set()
        self.copyErroredVideos = set()
        self.metadataErroredImages = set()
        self.metadataErroredVideos = set()
        self.otherErroredFiles = set()

    def inc(self, key, n=1):
        with self.lock:
            self.data[key] += n

    def addCopied(self, filename):
        path = Path(filename)

        with self.lock:
            if isImage(path):
                self.data["copied_images"] += 1
            elif isVideo(path):
                self.data["copied_videos"] += 1

    def addCopyErrored(self, filename):
        filename = str(filename)
        path = Path(filename)

        with self.lock:
            if isImage(path):
                if filename not in self.copyErroredImages:
                    self.data["copy_errored_images"] += 1
                    self.copyErroredImages.add(filename)
            elif isVideo(path):
                if filename not in self.copyErroredVideos:
                    self.data["copy_errored_videos"] += 1
                    self.copyErroredVideos.add(filename)
            else:
                self.otherErroredFiles.add(filename)

    def addMetadataWritten(self, filename):
        path = Path(filename)

        with self.lock:
            if isImage(path):
                self.data["metadata_written_images"] += 1
            elif isVideo(path):
                self.data["metadata_written_videos"] += 1

    def addMetadataErrored(self, filename):
        filename = str(filename)
        path = Path(filename)

        with self.lock:
            if isImage(path):
                if filename not in self.metadataErroredImages:
                    self.data["metadata_errored_images"] += 1
                    self.metadataErroredImages.add(filename)
            elif isVideo(path):
                if filename not in self.metadataErroredVideos:
                    self.data["metadata_errored_videos"] += 1
                    self.metadataErroredVideos.add(filename)
            else:
                self.otherErroredFiles.add(filename)

    def summary(self):
        return dict(self.data)


# ----------------------
# Logging
# ----------------------

def saveLog(stats, filename=f"copy_icloud_{datetime.datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}.txt"):
    data = stats.summary()

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"processed_images: {data.get('processed_images', 0)}\n")
        f.write(f"processed_videos: {data.get('processed_videos', 0)}\n")
        f.write(f"copied_images: {data.get('copied_images', 0)}\n")
        f.write(f"copied_videos: {data.get('copied_videos', 0)}\n")
        f.write(f"copy_errored_images: {data.get('copy_errored_images', 0)}\n")
        f.write(f"copy_errored_videos: {data.get('copy_errored_videos', 0)}\n")
        f.write(f"metadata_written_images: {data.get('metadata_written_images', 0)}\n")
        f.write(f"metadata_written_videos: {data.get('metadata_written_videos', 0)}\n")
        f.write(f"metadata_errored_images: {data.get('metadata_errored_images', 0)}\n")
        f.write(f"metadata_errored_videos: {data.get('metadata_errored_videos', 0)}\n")

        f.write(f"\nskipped_not_media: {data.get('skipped_not_media', 0)}\n")
        f.write(f"skipped_no_date: {data.get('skipped_no_date', 0)}\n")
        f.write(f"skipped_outside_date_range: {data.get('skipped_outside_date_range', 0)}\n")
        f.write(f"copy_retries: {data.get('copy_retries', 0)}\n")
        f.write(f"exiftool_timeouts: {data.get('exiftool_timeouts', 0)}\n")
        f.write(f"exiftool_errors: {data.get('exiftool_errors', 0)}\n")
        f.write(f"tmp_removed: {data.get('tmp_removed', 0)}\n")

        if stats.copyErroredImages:
            f.write("\nCopy errored images:\n")
            for file in sorted(stats.copyErroredImages):
                f.write(file + "\n")

        if stats.copyErroredVideos:
            f.write("\nCopy errored videos:\n")
            for file in sorted(stats.copyErroredVideos):
                f.write(file + "\n")

        if stats.metadataErroredImages:
            f.write("\nMetadata errored images:\n")
            for file in sorted(stats.metadataErroredImages):
                f.write(file + "\n")

        if stats.metadataErroredVideos:
            f.write("\nMetadata errored videos:\n")
            for file in sorted(stats.metadataErroredVideos):
                f.write(file + "\n")

        if stats.otherErroredFiles:
            f.write("\nOther errored files:\n")
            for file in sorted(stats.otherErroredFiles):
                f.write(file + "\n")

    print(f"\nLog saved to {filename}")


# ----------------------
# CLI
# ----------------------

def parseArgs():
    parser = argparse.ArgumentParser(
        description="Copy iCloud media preserving Shell metadata and writing mappable metadata into files."
    )
    parser.add_argument("src", help="iCloud Photos source file/folder, or .txt file if --input-txt is used.")
    parser.add_argument("dest", help="Destination folder.")

    parser.add_argument("--input-txt", action="store_true", help="Treat src as a .txt file containing one media path per line.")
    parser.add_argument("--from-date", help="Start date inclusive. Format: YYYY-MM-DD.")
    parser.add_argument("--to-date", help="End date inclusive. Format: YYYY-MM-DD.")

    parser.add_argument("-r", "--recursive", action="store_true", help="Process recursively when src is a folder.")
    parser.add_argument("-k", "--keep-structure", action="store_true", help="Keep source subfolders inside dest. Requires -r and folder src.")

    parser.add_argument("--workers", type=int, default=8, help="General worker threads. Default: 8.")
    parser.add_argument("--copy-workers", type=int, default=2, help="Concurrent iCloud copy/download operations. Default: 2.")
    parser.add_argument("--copy-retries", type=int, default=5, help="Retries for iCloud copy timeout errors. Default: 5.")
    parser.add_argument("--copy-retry-delay", type=float, default=3.0, help="Base retry delay in seconds. Default: 3.")

    parser.add_argument("--timeout", type=int, default=120, help="ExifTool timeout per file in seconds. Default: 120.")
    parser.add_argument("--date-order", choices=["dmy", "mdy"], default="dmy", help="Ambiguous Shell date order. Default: dmy.")
    parser.add_argument("--exiftool", default="exiftool", help="ExifTool executable path. Default: exiftool.")
    parser.add_argument("--write-xmp", action="store_true", help="Write Windows Shell metadata to .xmp sidecar files.")

    return parser.parse_args()


# ----------------------
# Parallel runner
# ----------------------

def runParallel(paths, workerFn, stats, maxWorkers=8):
    if not paths:
        return

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=maxWorkers)
    futures = {}

    try:
        for p in paths:
            if stopEvent.is_set():
                break
            futures[ex.submit(workerFn, p)] = p

        for fut, path in futures.items():
            if stopEvent.is_set():
                break

            try:
                fut.result()
            except KeyboardInterrupt:
                stopEvent.set()
                raise
            except Exception as e:
                with printLock:
                    print(f"[ERROR] {path}")
                    print(e)
                    print()
                stats.addCopyErrored(str(path))

    except KeyboardInterrupt:
        stopEvent.set()

        with printLock:
            print("\nStopping... cancelling pending tasks.")

        for f in futures:
            f.cancel()

        ex.shutdown(wait=False, cancel_futures=True)
        raise

    else:
        ex.shutdown(wait=True, cancel_futures=False)


# ----------------------
# File helpers
# ----------------------

def isImage(path):
    return Path(path).suffix.lower() in IMAGE_EXTS


def isVideo(path):
    return Path(path).suffix.lower() in VIDEO_EXTS


def isMedia(path):
    return isImage(path) or isVideo(path)


def readPathsFromTxt(txtPath):
    paths = []

    with open(txtPath, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            line = line.strip('"').strip("'")
            p = Path(os.path.normpath(line)).expanduser().resolve()

            if p.exists() and p.is_file():
                paths.append(p)
            else:
                with printLock:
                    print(f"[SKIP INVALID PATH] {line}")

    return paths


def iterFiles(src, recursive, inputTxt=False):
    if inputTxt:
        return readPathsFromTxt(src)

    if src.is_file():
        return [src]

    return src.rglob("*") if recursive else src.iterdir()


def isSubpath(child: Path, parent: Path):
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def relDirFor(pathStr, srcRootStr):
    rel = os.path.relpath(os.path.dirname(pathStr), start=srcRootStr)
    return "" if rel == "." else rel


def reserveUniquePath(filename, ext, pathDir):
    base = filename
    counter = 1

    with filenameLock:
        while True:
            candidate = Path(pathDir) / f"{filename}{ext}"
            key = str(candidate).lower()

            if not candidate.exists() and key not in reservedPaths:
                reservedPaths.add(key)
                return candidate

            filename = f"{base}_({counter})"
            counter += 1


def releaseReservedPath(path):
    with filenameLock:
        reservedPaths.discard(str(path).lower())


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


# ----------------------
# Windows Shell metadata
# ----------------------

def cleanShellValue(value):
    if value is None:
        return ""

    value = str(value).strip()
    value = value.replace("\u200e", "").replace("\u200f", "")
    value = value.replace("\u202a", "").replace("\u202c", "")

    return value.strip()


def parseWindowsShellDate(value, dateOrder="dmy"):
    value = cleanShellValue(value)

    if not value:
        return None

    commonFormats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M",
    ]

    dmyFormats = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",
    ]

    mdyFormats = [
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
    ]

    formats = commonFormats + (dmyFormats + mdyFormats if dateOrder == "dmy" else mdyFormats + dmyFormats)

    for fmt in formats:
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def getShellNamespaceAndItem(path):
    shell = Dispatch("Shell.Application")
    namespace = shell.Namespace(str(path.parent))

    if namespace is None:
        return None, None

    item = namespace.ParseName(path.name)

    if item is None:
        return namespace, None

    return namespace, item


def getShellDate(path, dateOrder):
    namespace, item = getShellNamespaceAndItem(path)

    if namespace is None or item is None:
        return None, None

    wantedColumns = DATE_COLUMNS_VIDEO if isVideo(path) else DATE_COLUMNS_IMAGE

    for column in wantedColumns:
        for i in range(0, 400):
            columnName = cleanShellValue(namespace.GetDetailsOf(None, i))

            if columnName != column:
                continue

            value = cleanShellValue(namespace.GetDetailsOf(item, i))
            parsed = parseWindowsShellDate(value, dateOrder=dateOrder)

            if parsed:
                return parsed, column

    return None, None


def getAllShellMetadata(path):
    namespace, item = getShellNamespaceAndItem(path)

    if namespace is None or item is None:
        return {}

    metadata = {}

    for i in range(0, 400):
        columnName = cleanShellValue(namespace.GetDetailsOf(None, i))
        value = cleanShellValue(namespace.GetDetailsOf(item, i))

        if columnName and value:
            metadata[columnName] = value

    return metadata


# ----------------------
# XMP sidecar writer
# ----------------------

def sanitizeXmlName(name):
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    name = name.strip("_")

    if not name:
        name = "Field"

    if name[0].isdigit():
        name = "_" + name

    return name


def buildXmpSidecarContent(sourcePath, copiedPath, metadata):
    lines = []

    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<x:xmpmeta xmlns:x="adobe:ns:meta/">')
    lines.append('  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">')
    lines.append('    <rdf:Description')
    lines.append('      xmlns:icloud="https://example.local/icloud-shell/1.0/"')
    lines.append('      rdf:about="">')

    lines.append(f'      <icloud:OriginalPath>{html.escape(str(sourcePath))}</icloud:OriginalPath>')
    lines.append(f'      <icloud:CopiedPath>{html.escape(str(copiedPath))}</icloud:CopiedPath>')

    for key in sorted(metadata.keys()):
        value = metadata[key]
        tag = sanitizeXmlName(key)
        lines.append(f'      <icloud:{tag}>{html.escape(str(value))}</icloud:{tag}>')

    lines.append('    </rdf:Description>')
    lines.append('  </rdf:RDF>')
    lines.append('</x:xmpmeta>')
    lines.append("")

    return "\n".join(lines)


def writeXmpSidecar(sourcePath, copiedPath, metadata):
    xmpPath = Path(str(copiedPath) + ".xmp")
    content = buildXmpSidecarContent(sourcePath, copiedPath, metadata)

    with open(xmpPath, "w", encoding="utf-8") as f:
        f.write(content)

    return xmpPath


# ----------------------
# ExifTool helpers
# ----------------------

def shellDateToExiftoolValue(value, dateOrder):
    parsed = parseWindowsShellDate(value, dateOrder=dateOrder)

    if not parsed:
        return None

    return datetimeToExiftool(parsed)


def buildMappedTags(metadata, isImg, isVid, dateOrder):
    tags = []
    mapping = SHELL_TO_EXIFTOOL_IMAGE if isImg else SHELL_TO_EXIFTOOL_VIDEO

    def put(tag, val):
        if val:
            tags.append(f"-{tag}={val}")

    for shellKey, exiftoolTags in mapping.items():
        if shellKey not in metadata:
            continue

        value = cleanShellValue(metadata[shellKey])

        if not value:
            continue

        if "date" in shellKey.lower() or "created" in shellKey.lower():
            value = shellDateToExiftoolValue(value, dateOrder)

            if not value:
                continue

        for tag in exiftoolTags:
            put(tag, value)

    return tags


def cleanupExiftoolTmp(path, stats=None):
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
                with printLock:
                    print(f"[TMP REMOVED] {candidate}")
        except Exception as e:
            with printLock:
                print(f"[TMP REMOVE ERROR] {candidate}")
                print(e)
                print()


def runExiftool(exiftoolPath, argsList, targetPath=None, timeout=120, stats=None):
    if stopEvent.is_set():
        return 130

    cmd = [exiftoolPath, "-overwrite_original", "-m", "-P"] + argsList

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        if targetPath:
            cleanupExiftoolTmp(targetPath, stats=stats)
        return 124
    except KeyboardInterrupt:
        stopEvent.set()
        return 130

    if targetPath:
        cleanupExiftoolTmp(targetPath, stats=stats)

    if proc.returncode != 0 and proc.stderr:
        with printLock:
            sys.stderr.write(proc.stderr.strip() + "\n")

    return proc.returncode


def writeEmbeddedMetadata(copiedPath, metadata, timeout, dateOrder, exiftoolPath, stats):
    path = Path(copiedPath)
    ext = path.suffix.lower()

    isImg = isImage(path)
    isVid = isVideo(path)

    if not (isImg or isVid):
        return 1

    tags = buildMappedTags(
        metadata=metadata,
        isImg=isImg,
        isVid=isVid,
        dateOrder=dateOrder,
    )

    if not tags:
        return 0

    if ext in UNSUPPORTED_EMBED_WRITE:
        argsList = tags + ["-o", "%d%f.xmp", str(path)]
    else:
        argsList = tags + [str(path)]

    return runExiftool(
        exiftoolPath=exiftoolPath,
        argsList=argsList,
        targetPath=path,
        timeout=timeout,
        stats=stats,
    )


# ----------------------
# iCloud copy helpers
# ----------------------

def isCloudTimeoutError(error):
    return isinstance(error, OSError) and getattr(error, "winerror", None) == 426


def copyWithRetry(src, dest, stats, retries=5, delay=3.0):
    global copySemaphore

    lastError = None

    for attempt in range(1, retries + 1):
        if stopEvent.is_set():
            raise KeyboardInterrupt()

        try:
            with copySemaphore:
                if stopEvent.is_set():
                    raise KeyboardInterrupt()
                shutil.copy2(src, dest)
            return

        except KeyboardInterrupt:
            raise

        except OSError as e:
            lastError = e

            if not isCloudTimeoutError(e):
                raise

            stats.inc("copy_retries")

            sleepSeconds = delay * attempt

            with printLock:
                print(f"[COPY RETRY {attempt}/{retries}] {src}")
                print(f"  reason: {e}")
                print(f"  waiting: {sleepSeconds:.1f}s")
                print()

            time.sleep(sleepSeconds)

    raise lastError


# ----------------------
# Core logic
# ----------------------

def copyIcloudMedia(
    src,
    dest,
    recursive,
    keepStructure,
    inputTxt,
    fromDate,
    toDate,
    dateOrder,
    exiftoolPath,
    writeXmp,
    stats,
    timeout=120,
    maxWorkers=8,
    copyRetries=5,
    copyRetryDelay=3.0,
):
    files = [p for p in iterFiles(src, recursive, inputTxt=inputTxt) if p.is_file()]
    srcMode = "txt" if inputTxt else ("file" if src.is_file() else "folder")

    def worker(path):
        processOne(
            path=path,
            src=src,
            srcMode=srcMode,
            dest=dest,
            keepStructure=keepStructure,
            fromDate=fromDate,
            toDate=toDate,
            dateOrder=dateOrder,
            exiftoolPath=exiftoolPath,
            writeXmp=writeXmp,
            stats=stats,
            timeout=timeout,
            copyRetries=copyRetries,
            copyRetryDelay=copyRetryDelay,
        )

    runParallel(
        files,
        workerFn=worker,
        stats=stats,
        maxWorkers=maxWorkers,
    )


def processOne(
    path,
    src,
    srcMode,
    dest,
    keepStructure,
    fromDate,
    toDate,
    dateOrder,
    exiftoolPath,
    writeXmp,
    stats,
    timeout=120,
    copyRetries=5,
    copyRetryDelay=3.0,
):
    pythoncom.CoInitialize()

    outPath = None

    try:
        if stopEvent.is_set():
            return

        if not isMedia(path):
            stats.inc("skipped_not_media")
            return

        shellDate, shellDateColumn = getShellDate(path, dateOrder=dateOrder)

        if shellDate is None:
            stats.inc("skipped_no_date")
            return

        if srcMode == "folder" and not inDateRange(shellDate, fromDate, toDate):
            stats.inc("skipped_outside_date_range")
            return

        if isImage(path):
            stats.inc("processed_images")
        elif isVideo(path):
            stats.inc("processed_videos")

        metadata = getAllShellMetadata(path)

        if keepStructure and srcMode == "folder":
            relDir = relDirFor(str(path), str(src))
            targetDir = dest / relDir if relDir else dest
        else:
            targetDir = dest

        targetDir.mkdir(parents=True, exist_ok=True)

        filename = datetimeToFilename(shellDate)
        ext = path.suffix.lower()

        outPath = reserveUniquePath(filename, ext, str(targetDir))

        try:
            copyWithRetry(
                src=path,
                dest=outPath,
                stats=stats,
                retries=copyRetries,
                delay=copyRetryDelay,
            )
            stats.addCopied(str(outPath))

        except KeyboardInterrupt:
            stopEvent.set()
            releaseReservedPath(outPath)
            raise

        except Exception as e:
            stats.addCopyErrored(str(path))
            releaseReservedPath(outPath)

            with printLock:
                print(f"[COPY ERROR] {path}")
                print(e)
                print()

            return

        with printLock:
            print("[COPY]")
            print(f"  DATE: {shellDate} ({shellDateColumn})")
            print(f"  FROM: {path}")
            print(f"  TO:   {outPath}")
            print()

        if writeXmp:
            writeXmpSidecar(path, outPath, metadata)

        rc = writeEmbeddedMetadata(
            copiedPath=outPath,
            metadata=metadata,
            timeout=timeout,
            dateOrder=dateOrder,
            exiftoolPath=exiftoolPath,
            stats=stats,
        )

        if rc == 0:
            stats.addMetadataWritten(str(outPath))
        elif rc == 124:
            stats.inc("exiftool_timeouts")
            stats.addMetadataErrored(str(outPath))
        elif rc == 130:
            stopEvent.set()
            stats.addMetadataErrored(str(outPath))
        else:
            stats.inc("exiftool_errors")
            stats.addMetadataErrored(str(outPath))

    except KeyboardInterrupt:
        stopEvent.set()
        raise

    except Exception as e:
        if outPath and outPath.exists():
            stats.addMetadataErrored(str(outPath))
        else:
            stats.addCopyErrored(str(path))

        with printLock:
            print(f"[ERROR] {path}")
            print(e)
            print()

    finally:
        pythoncom.CoUninitialize()


# ----------------------
# Main
# ----------------------

def main():
    global copySemaphore

    args = parseArgs()

    src = Path(os.path.normpath(args.src)).expanduser().resolve()
    dest = Path(os.path.normpath(args.dest)).expanduser().resolve()

    if not src.exists():
        print(f"Error: source doesn't exist: {src}")
        sys.exit(2)

    if args.input_txt and not src.is_file():
        print("Error: --input-txt requires src to be a .txt file.")
        sys.exit(2)

    if args.input_txt and src.suffix.lower() != ".txt":
        print("Error: --input-txt requires src to have .txt extension.")
        sys.exit(2)

    if not args.input_txt and not src.is_file() and not src.is_dir():
        print(f"Error: source is not a file or folder: {src}")
        sys.exit(2)

    if not dest.exists():
        try:
            dest.mkdir(parents=True, exist_ok=True)
            print(f"Destination folder created: {dest}")
        except Exception as e:
            print(f"Error creating destination folder '{dest}': {e}")
            sys.exit(3)
    elif not dest.is_dir():
        print(f"Error: destination is not a folder: {dest}")
        sys.exit(4)

    if src == dest:
        print("Error: source and destination are the same.")
        sys.exit(5)

    if src.is_dir() and isSubpath(dest, src):
        print("Error: destination folder is inside source folder. Choose a different destination.")
        sys.exit(6)

    if args.keep_structure and not args.recursive:
        print("Error: you need the recursive flag (-r) to keep structure (-k).")
        sys.exit(7)

    if args.keep_structure and (args.input_txt or not src.is_dir()):
        print("Error: keep structure (-k) is only valid when source is a folder.")
        sys.exit(8)

    if args.workers < 1:
        print("Error: --workers must be >= 1.")
        sys.exit(9)

    if args.copy_workers < 1:
        print("Error: --copy-workers must be >= 1.")
        sys.exit(10)

    copySemaphore = threading.Semaphore(args.copy_workers)

    fromDate, toDate = parseOptionalDateRange(args.from_date, args.to_date)

    stats = Stats()

    try:
        copyIcloudMedia(
            src=src,
            dest=dest,
            recursive=args.recursive,
            keepStructure=args.keep_structure,
            inputTxt=args.input_txt,
            fromDate=fromDate,
            toDate=toDate,
            dateOrder=args.date_order,
            exiftoolPath=args.exiftool,
            writeXmp=args.write_xmp,
            stats=stats,
            timeout=args.timeout,
            maxWorkers=args.workers,
            copyRetries=args.copy_retries,
            copyRetryDelay=args.copy_retry_delay,
        )

    except KeyboardInterrupt:
        stopEvent.set()
        print("\nExecution interrupted by the user")

    finally:
        saveLog(stats)


if __name__ == "__main__":
    main()