#!/usr/bin/env python3
# copy_icloud.py

import sys
import re
import html
import time
import csv
import datetime
import shutil
import argparse
import threading
import subprocess
from pathlib import Path

try:
    import pythoncom
    from win32com.client import Dispatch
except ImportError:
    pythoncom = None
    Dispatch = None

from media_common import (
    BaseStats,
    UNSUPPORTED_EMBED_WRITE,
    datetimeToExiftool,
    datetimeToFilename,
    inDateRange,
    isImage,
    isMedia,
    isSubpath,
    isVideo,
    iterFiles,
    parseOptionalDateRange,
    positiveInt,
    relDirFor,
    releaseReservedPath,
    reserveUniquePath,
    resolvePath,
    runExiftool,
    runParallel,
    savePathList,
    saveSimpleLog,
    timestampedName,
)


# ----------------------
# Config / constants
# ----------------------

WINDOWS_SHELL_AVAILABLE = pythoncom is not None and Dispatch is not None

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

VERIFY_TAGS_IMAGE = ["EXIF:DateTimeOriginal", "EXIF:CreateDate", "XMP:DateTimeOriginal", "XMP:CreateDate"]
VERIFY_TAGS_VIDEO = ["QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate", "XMP:CreateDate"]
VERIFY_TAGS_XMP = ["XMP:DateTimeOriginal", "XMP:CreateDate", "XMP:ModifyDate"]

filenameLock = threading.Lock()
printLock = threading.Lock()
copySemaphore = None
stopEvent = threading.Event()
reservedPaths = set()


# ----------------------
# Stats helper
# ----------------------

class Stats(BaseStats):
    def __init__(self):
        super().__init__()
        self.copyErroredSources = set()
        self.metadataErroredSources = set()
        self.otherErroredFiles = set()
        self.csvRows = []

    def addCopied(self, filename):
        path = Path(filename)

        with self.lock:
            if isImage(path):
                self.data["copied_images"] += 1
            elif isVideo(path):
                self.data["copied_videos"] += 1

    def addCopyErrored(self, sourcePath):
        sourcePath = str(sourcePath)
        path = Path(sourcePath)

        with self.lock:
            if isImage(path):
                if sourcePath not in self.copyErroredSources:
                    self.data["copy_errored_images"] += 1
                    self.copyErroredSources.add(sourcePath)
            elif isVideo(path):
                if sourcePath not in self.copyErroredSources:
                    self.data["copy_errored_videos"] += 1
                    self.copyErroredSources.add(sourcePath)
            else:
                self.otherErroredFiles.add(sourcePath)

    def addMetadataWritten(self, filename):
        path = Path(filename)

        with self.lock:
            if isImage(path):
                self.data["metadata_written_images"] += 1
            elif isVideo(path):
                self.data["metadata_written_videos"] += 1

    def addMetadataErrored(self, sourcePath):
        sourcePath = str(sourcePath)
        path = Path(sourcePath)

        with self.lock:
            if isImage(path):
                if sourcePath not in self.metadataErroredSources:
                    self.data["metadata_errored_images"] += 1
                    self.metadataErroredSources.add(sourcePath)
            elif isVideo(path):
                if sourcePath not in self.metadataErroredSources:
                    self.data["metadata_errored_videos"] += 1
                    self.metadataErroredSources.add(sourcePath)
            else:
                self.otherErroredFiles.add(sourcePath)

    def addCsvRow(self, source, dest, dateValue, copiedOk, metadataOk, error):
        with self.lock:
            self.csvRows.append({
                "source": str(source),
                "dest": "" if dest is None else str(dest),
                "date": "" if dateValue is None else str(dateValue),
                "copied_ok": copiedOk,
                "metadata_ok": metadataOk,
                "error": error,
            })

    def getCsvRows(self):
        with self.lock:
            return list(self.csvRows)


# ----------------------
# Logging
# ----------------------

def saveCsvLog(stats, csvPath):
    rows = stats.getCsvRows()

    with open(csvPath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source", "dest", "date", "copied_ok", "metadata_ok", "error"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"CSV log saved to {csvPath}")


def saveLog(stats, csvLogPath=None):
    logName = timestampedName("copy_icloud")
    retryName = timestampedName("failed_paths")
    copyRetryName = timestampedName("copy_failed_paths")
    metadataRetryName = timestampedName("metadata_failed_paths")

    data = stats.summary()
    lines = [
        f"processed_images: {data.get('processed_images', 0)}",
        f"processed_videos: {data.get('processed_videos', 0)}",
        f"copied_images: {data.get('copied_images', 0)}",
        f"copied_videos: {data.get('copied_videos', 0)}",
        f"copy_errored_images: {data.get('copy_errored_images', 0)}",
        f"copy_errored_videos: {data.get('copy_errored_videos', 0)}",
        f"metadata_written_images: {data.get('metadata_written_images', 0)}",
        f"metadata_written_videos: {data.get('metadata_written_videos', 0)}",
        f"metadata_errored_images: {data.get('metadata_errored_images', 0)}",
        f"metadata_errored_videos: {data.get('metadata_errored_videos', 0)}",
        "",
        f"metadata_skipped_disabled: {data.get('metadata_skipped_disabled', 0)}",
        f"metadata_skipped_videos: {data.get('metadata_skipped_videos', 0)}",
        f"metadata_verified: {data.get('metadata_verified', 0)}",
        f"metadata_verify_failed: {data.get('metadata_verify_failed', 0)}",
        f"xmp_sidecars_written: {data.get('xmp_sidecars_written', 0)}",
        f"xmp_sidecar_errors: {data.get('xmp_sidecar_errors', 0)}",
        "",
        f"skipped_not_media: {data.get('skipped_not_media', 0)}",
        f"skipped_no_date: {data.get('skipped_no_date', 0)}",
        f"skipped_outside_date_range: {data.get('skipped_outside_date_range', 0)}",
        f"copy_retries: {data.get('copy_retries', 0)}",
        f"exiftool_timeouts: {data.get('exiftool_timeouts', 0)}",
        f"exiftool_errors: {data.get('exiftool_errors', 0)}",
        f"tmp_removed: {data.get('tmp_removed', 0)}",
    ]

    if stats.copyErroredSources:
        lines.append("")
        lines.append("Copy errored source paths:")
        lines.extend(sorted(stats.copyErroredSources))

    if stats.metadataErroredSources:
        lines.append("")
        lines.append("Metadata errored source paths:")
        lines.extend(sorted(stats.metadataErroredSources))

    if stats.otherErroredFiles:
        lines.append("")
        lines.append("Other errored files:")
        lines.extend(sorted(stats.otherErroredFiles))

    saveSimpleLog(lines, logName)

    failedPaths = set(stats.copyErroredSources) | set(stats.metadataErroredSources)

    if failedPaths:
        savePathList(failedPaths, retryName)
        print(f"Retry list saved to {retryName}")

    if stats.copyErroredSources:
        savePathList(stats.copyErroredSources, copyRetryName)
        print(f"Copy retry list saved to {copyRetryName}")

    if stats.metadataErroredSources:
        savePathList(stats.metadataErroredSources, metadataRetryName)
        print(f"Metadata retry list saved to {metadataRetryName}")

    if csvLogPath:
        if csvLogPath == "auto":
            csvLogPath = timestampedName("copy_icloud", ext="csv")
        saveCsvLog(stats, csvLogPath)


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

    parser.add_argument("--workers", type=positiveInt, default=8, help="General worker threads. Default: 8.")
    parser.add_argument("--copy-workers", type=positiveInt, default=2, help="Concurrent iCloud copy/download operations. Default: 2.")
    parser.add_argument("--copy-retries", type=positiveInt, default=5, help="Retries for iCloud copy timeout errors. Default: 5.")
    parser.add_argument("--copy-retry-delay", type=float, default=3.0, help="Base retry delay in seconds. Default: 3.")

    parser.add_argument("--timeout", type=positiveInt, default=120, help="ExifTool timeout per file in seconds. Default: 120.")
    parser.add_argument("--date-order", choices=["dmy", "mdy"], default="dmy", help="Ambiguous Shell date order. Default: dmy.")
    parser.add_argument("--exiftool", default="exiftool", help="ExifTool executable path. Default: exiftool.")
    parser.add_argument("--write-xmp", action="store_true", help="Write Windows Shell metadata to .xmp sidecar files.")
    parser.add_argument("--no-metadata", action="store_true", help="Copy files without writing embedded metadata with ExifTool.")
    parser.add_argument("--verify", action="store_true", help="Verify that the destination metadata contains the expected date after writing.")
    parser.add_argument("--skip-video-metadata", action="store_true", help="Skip embedded metadata writing for video files.")
    parser.add_argument("--csv-log", nargs="?", const="auto", help="Write an optional CSV log. Pass a path or omit the value for an auto-generated name.")

    return parser.parse_args()


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
    if not WINDOWS_SHELL_AVAILABLE:
        return None, None

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
        for index in range(0, 400):
            columnName = cleanShellValue(namespace.GetDetailsOf(None, index))

            if columnName != column:
                continue

            value = cleanShellValue(namespace.GetDetailsOf(item, index))
            parsed = parseWindowsShellDate(value, dateOrder=dateOrder)

            if parsed:
                return parsed, column

    return None, None


def getAllShellMetadata(path):
    namespace, item = getShellNamespaceAndItem(path)

    if namespace is None or item is None:
        return {}

    metadata = {}

    for index in range(0, 400):
        columnName = cleanShellValue(namespace.GetDetailsOf(None, index))
        value = cleanShellValue(namespace.GetDetailsOf(item, index))

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
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '    <rdf:Description',
        '      xmlns:icloud="https://example.local/icloud-shell/1.0/"',
        '      rdf:about="">',
        f'      <icloud:OriginalPath>{html.escape(str(sourcePath))}</icloud:OriginalPath>',
        f'      <icloud:CopiedPath>{html.escape(str(copiedPath))}</icloud:CopiedPath>',
    ]

    for key in sorted(metadata.keys()):
        value = metadata[key]
        tag = sanitizeXmlName(key)
        lines.append(f'      <icloud:{tag}>{html.escape(str(value))}</icloud:{tag}>')

    lines.extend([
        '    </rdf:Description>',
        '  </rdf:RDF>',
        '</x:xmpmeta>',
        "",
    ])

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


def buildMappedTags(metadata, isImg, dateOrder):
    tags = []
    mapping = SHELL_TO_EXIFTOOL_IMAGE if isImg else SHELL_TO_EXIFTOOL_VIDEO

    def put(tag, value):
        if value:
            tags.append(f"-{tag}={value}")

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


def writeEmbeddedMetadata(copiedPath, metadata, timeout, dateOrder, exiftoolPath, stats):
    path = Path(copiedPath)
    isImg = isImage(path)
    isVid = isVideo(path)

    if not (isImg or isVid):
        return 1

    tags = buildMappedTags(metadata=metadata, isImg=isImg, dateOrder=dateOrder)

    if not tags:
        return 0

    if path.suffix.lower() in UNSUPPORTED_EMBED_WRITE:
        argsList = tags + ["-o", "%d%f.xmp", str(path)]
    else:
        argsList = tags + [str(path)]

    return runExiftool(
        exiftoolPath=exiftoolPath,
        argsList=argsList,
        timeout=timeout,
        printLock=printLock,
        targetPath=path,
        stats=stats,
        stopEvent=stopEvent,
    )


def readExiftoolTagValues(targetPath, tags, exiftoolPath, timeout):
    cmd = [exiftoolPath, "-s3"] + [f"-{tag}" for tag in tags] + [str(targetPath)]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, []

    if proc.returncode != 0:
        with printLock:
            if proc.stderr:
                sys.stderr.write(proc.stderr.strip() + "\n")
        return proc.returncode, []

    values = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return 0, values


def verifyWrittenDate(copiedPath, expectedDate, exiftoolPath, timeout):
    path = Path(copiedPath)

    if path.suffix.lower() in UNSUPPORTED_EMBED_WRITE:
        targetPath = path.with_suffix(".xmp")
        tags = VERIFY_TAGS_XMP
    elif isImage(path):
        targetPath = path
        tags = VERIFY_TAGS_IMAGE
    else:
        targetPath = path
        tags = VERIFY_TAGS_VIDEO

    if not targetPath.exists():
        return False, "verify target missing"

    rc, values = readExiftoolTagValues(targetPath, tags, exiftoolPath, timeout)

    if rc == 124:
        return False, "verify timeout"

    if rc != 0:
        return False, "verify exiftool error"

    expected = expectedDate.strip()

    for value in values:
        if value.strip() == expected:
            return True, ""

    return False, "expected date not found after write"


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
    noMetadata,
    verify,
    skipVideoMetadata,
    stats,
    timeout=120,
    maxWorkers=8,
    copyRetries=5,
    copyRetryDelay=3.0,
):
    files = list(iterFiles(src, recursive, inputTxt=inputTxt, printLock=printLock))
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
            noMetadata=noMetadata,
            verify=verify,
            skipVideoMetadata=skipVideoMetadata,
            stats=stats,
            timeout=timeout,
            copyRetries=copyRetries,
            copyRetryDelay=copyRetryDelay,
        )

    def onError(path, error):
        with printLock:
            print(f"[ERROR] {path}")
            print(error)
            print()
        stats.addCopyErrored(path)
        stats.addCsvRow(path, None, None, False, False, str(error))

    runParallel(
        files,
        workerFn=worker,
        maxWorkers=maxWorkers,
        stopEvent=stopEvent,
        onError=onError,
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
    noMetadata,
    verify,
    skipVideoMetadata,
    stats,
    timeout=120,
    copyRetries=5,
    copyRetryDelay=3.0,
):
    if not WINDOWS_SHELL_AVAILABLE:
        raise RuntimeError("copy_icloud.py requires Windows Shell support (pywin32) and only works on Windows.")

    pythoncom.CoInitialize()

    outPath = None
    shellDate = None
    metadataOk = ""

    try:
        if stopEvent.is_set():
            return

        if not isMedia(path):
            stats.inc("skipped_not_media")
            stats.addCsvRow(path, None, None, False, "", "not media")
            return

        shellDate, shellDateColumn = getShellDate(path, dateOrder=dateOrder)

        if shellDate is None:
            stats.inc("skipped_no_date")
            stats.addCsvRow(path, None, None, False, "", "no shell date")
            return

        if srcMode == "folder" and not inDateRange(shellDate, fromDate, toDate):
            stats.inc("skipped_outside_date_range")
            stats.addCsvRow(path, None, shellDate, False, "", "outside date range")
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
        outPath = reserveUniquePath(filename, ext, str(targetDir), reservedPaths, filenameLock)

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
            releaseReservedPath(outPath, reservedPaths, filenameLock)
            raise
        except Exception as e:
            stats.addCopyErrored(str(path))
            releaseReservedPath(outPath, reservedPaths, filenameLock)
            stats.addCsvRow(path, outPath, shellDate, False, "", str(e))

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
            try:
                writeXmpSidecar(path, outPath, metadata)
                stats.inc("xmp_sidecars_written")
            except Exception as e:
                stats.inc("xmp_sidecar_errors")
                stats.addMetadataErrored(str(path))
                stats.addCsvRow(path, outPath, shellDate, True, False, f"xmp sidecar error: {e}")

                with printLock:
                    print(f"[XMP SIDECAR ERROR] {path}")
                    print(e)
                    print()

                return

        if noMetadata:
            stats.inc("metadata_skipped_disabled")
            metadataOk = ""
            stats.addCsvRow(path, outPath, shellDate, True, metadataOk, "")
            return

        if skipVideoMetadata and isVideo(path):
            stats.inc("metadata_skipped_videos")
            metadataOk = ""
            stats.addCsvRow(path, outPath, shellDate, True, metadataOk, "")
            return

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
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, "exiftool timeout")
            return
        elif rc == 130:
            stopEvent.set()
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, "interrupted")
            return
        else:
            stats.inc("exiftool_errors")
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, "exiftool error")
            return

        if verify:
            expectedDate = datetimeToExiftool(shellDate)
            verified, verifyError = verifyWrittenDate(
                copiedPath=outPath,
                expectedDate=expectedDate,
                exiftoolPath=exiftoolPath,
                timeout=timeout,
            )

            if verified:
                stats.inc("metadata_verified")
                metadataOk = True
                stats.addCsvRow(path, outPath, shellDate, True, True, "")
            else:
                stats.inc("metadata_verify_failed")
                stats.addMetadataErrored(str(path))
                metadataOk = False
                stats.addCsvRow(path, outPath, shellDate, True, False, verifyError)
        else:
            metadataOk = True
            stats.addCsvRow(path, outPath, shellDate, True, True, "")

    except KeyboardInterrupt:
        stopEvent.set()
        raise

    except Exception as e:
        if outPath and outPath.exists():
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, str(e))
        else:
            stats.addCopyErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, False, metadataOk, str(e))

        with printLock:
            print(f"[ERROR] {path}")
            print(e)
            print()

    finally:
        if outPath is not None:
            releaseReservedPath(outPath, reservedPaths, filenameLock)
        pythoncom.CoUninitialize()


# ----------------------
# Main
# ----------------------

def main():
    global copySemaphore

    args = parseArgs()

    if not WINDOWS_SHELL_AVAILABLE:
        print("Error: copy_icloud.py requires pywin32 / pythoncom and Windows Shell integration.")
        print("This script is Windows-only and is not expected to run on Linux.")
        sys.exit(2)

    src = resolvePath(args.src)
    dest = resolvePath(args.dest)

    if args.no_metadata and args.verify:
        print("Warning: --verify has no effect when --no-metadata is enabled.")
        args.verify = False

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

    if args.copy_retry_delay < 0:
        print("Error: --copy-retry-delay must be >= 0.")
        sys.exit(9)

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
            noMetadata=args.no_metadata,
            verify=args.verify,
            skipVideoMetadata=args.skip_video_metadata,
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
        saveLog(stats, csvLogPath=args.csv_log)


if __name__ == "__main__":
    main()
