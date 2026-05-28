#!/usr/bin/env python3
# copy_icloud.py

import sys
import datetime
import shutil
import threading
from pathlib import Path

from media_tools.copy_icloud_config import (
    CopyOptions,
    ResumeState,
    applyRunDefaults,
    buildRunContext,
    parseArgs,
)
from media_tools.capture_dates import captureDateFromEmbeddedMedia
from media_tools.media_common import (
    BaseStats,
    detectedImageExtension,
    datetimeToExiftool,
    datetimeToFilename,
    inDateRange,
    isImage,
    isMedia,
    isSubpath,
    isVideo,
    iterFiles,
    parseOptionalDateRange,
    relDirFor,
    releaseReservedPath,
    reserveUniquePath,
    resolvePath,
    runParallel,
)
from media_tools.media_logging import (
    completedIcloudSourcesFromRows,
    loadResumeCopiedDestinations,
    loadResumeDetectedDates,
    loadResumeSources,
    logPaths,
    pathKey,
    removeCheckpoint,
    runCheckpointLoop,
    saveRunLog,
)
from media_tools.metadata_writer import (
    verifyWrittenDate,
    writeEmbeddedMetadata,
    writeXmpSidecar,
)
from media_tools.windows_metadata import (
    WINDOWS_SHELL_AVAILABLE,
    getAllShellMetadata,
    getShellDate,
    initializeCom,
    uninitializeCom,
)

filenameLock = threading.Lock()
printLock = threading.Lock()
copySemaphore = None
stopEvent = threading.Event()
reservedPaths = set()
LOG_PREFIX = "dateframe_import-icloud"


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
        self.csvRowIndexBySource = {}
        self.previousCsvRows = []

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

    def addCsvRow(self, source, dest, dateValue, copiedOk, metadataOk, error, dateSource=""):
        source = str(source)
        sourceKey = pathKey(source)
        row = {
            "source": source,
            "dest": "" if dest is None else str(dest),
            "date": "" if dateValue is None else str(dateValue),
            "date_source": dateSource,
            "copied_ok": copiedOk,
            "metadata_ok": metadataOk,
            "error": error,
        }

        with self.lock:
            existingIndex = self.csvRowIndexBySource.get(sourceKey)

            if existingIndex is not None:
                self.csvRows[existingIndex] = row
                return

            self.csvRowIndexBySource[sourceKey] = len(self.csvRows)
            self.csvRows.append(row)

    def getCsvRows(self):
        with self.lock:
            return list(self.csvRows)

    def setPreviousCsvRows(self, rows):
        with self.lock:
            self.previousCsvRows = list(rows)

    def getPreviousCsvRows(self):
        with self.lock:
            return list(self.previousCsvRows)


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

            if stopEvent.wait(sleepSeconds):
                raise KeyboardInterrupt()

    raise lastError


def dateWithEmbeddedSeconds(path, shellDate, options):
    if shellDate.second != 0:
        return shellDate, False

    embeddedDate = captureDateFromEmbeddedMedia(
        path,
        exiftoolPath=options.exiftoolPath,
        timeout=options.timeout,
    )

    if embeddedDate is None:
        return shellDate, False

    candidate = datetime.datetime.strptime(embeddedDate.displayValue, "%Y-%m-%d %H:%M:%S")

    if candidate.replace(second=0, microsecond=0) != shellDate.replace(second=0, microsecond=0):
        return shellDate, False

    return candidate, candidate != shellDate


def dateWithFilesystemSeconds(path, shellDate):
    if shellDate.second != 0:
        return shellDate, ""

    try:
        statResult = path.stat()
    except OSError:
        return shellDate, ""

    candidates = [
        ("filesystem created seconds", statResult.st_ctime),
        ("filesystem modified seconds", statResult.st_mtime),
    ]

    for source, timestamp in candidates:
        candidate = datetime.datetime.fromtimestamp(timestamp).replace(microsecond=0)

        if candidate.replace(second=0, microsecond=0) != shellDate.replace(second=0, microsecond=0):
            continue

        if candidate != shellDate:
            return candidate, source

    return shellDate, ""


def embeddedCaptureDate(path, options):
    embeddedDate = captureDateFromEmbeddedMedia(
        path,
        exiftoolPath=options.exiftoolPath,
        timeout=options.timeout,
    )

    if embeddedDate is None:
        return None

    return datetime.datetime.strptime(embeddedDate.displayValue, "%Y-%m-%d %H:%M:%S")


def metadataWithSelectedDate(metadata, path, dateValue):
    adjusted = dict(metadata)

    if isImage(path):
        adjusted["Date taken"] = datetimeToExiftool(dateValue)
    elif isVideo(path):
        adjusted["Media created"] = datetimeToExiftool(dateValue)

    return adjusted


def dateDetail(shellDateColumn, usedEmbeddedSeconds, filesystemSecondsSource):
    if usedEmbeddedSeconds:
        return f"{shellDateColumn} + embedded seconds"

    if filesystemSecondsSource:
        return f"{shellDateColumn} + {filesystemSecondsSource}"

    return shellDateColumn


def correctedPendingOutputPath(path, filename, ext):
    if path.stem == filename or path.stem.startswith(f"{filename}_("):
        return path

    correctedPath = reserveUniquePath(filename, ext, str(path.parent), reservedPaths, filenameLock)

    try:
        shutil.move(str(path), str(correctedPath))
    except Exception:
        releaseReservedPath(correctedPath, reservedPaths, filenameLock)
        raise

    return correctedPath


def correctedOutputExtension(path):
    actualExt = detectedImageExtension(path)

    if not actualExt or path.suffix.lower() == actualExt:
        return path

    if path.suffix.lower() != ".png" or actualExt != ".jpg":
        return path

    correctedPath = reserveUniquePath(path.stem, actualExt, str(path.parent), reservedPaths, filenameLock)

    try:
        shutil.move(str(path), str(correctedPath))
    except Exception:
        releaseReservedPath(correctedPath, reservedPaths, filenameLock)
        raise

    releaseReservedPath(path, reservedPaths, filenameLock)
    return correctedPath


# ----------------------
# Core logic
# ----------------------

def copyIcloudMedia(
    src,
    dest,
    options,
    resumeState,
    stats,
):
    files = list(iterFiles(src, options.recursive, inputTxt=options.inputTxt, printLock=printLock))
    srcMode = "txt" if options.inputTxt else ("file" if src.is_file() else "folder")

    if resumeState.completedSources:
        beforeCount = len(files)
        files = [path for path in files if pathKey(path) not in resumeState.completedSources]
        skippedCount = beforeCount - len(files)
        stats.inc("skipped_resume_completed", skippedCount)

        with printLock:
            print(f"[RESUME] skipped already completed sources: {skippedCount}")

    def worker(path):
        processOne(
            path=path,
            src=src,
            srcMode=srcMode,
            dest=dest,
            options=options,
            resumeCopiedPath=resumeState.copiedDestinations.get(pathKey(path)),
            resumeDate=resumeState.detectedDates.get(pathKey(path)),
            stats=stats,
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
        maxWorkers=options.maxWorkers,
        stopEvent=stopEvent,
        onError=onError,
    )


def processOne(
    path,
    src,
    srcMode,
    dest,
    options,
    stats,
    resumeCopiedPath=None,
    resumeDate=None,
):
    if not WINDOWS_SHELL_AVAILABLE:
        raise RuntimeError("copy_icloud.py requires Windows Shell support (pywin32) and only works on Windows.")

    initializeCom()

    outPath = None
    shellDate = None
    metadataOk = ""
    selectedDateSource = ""

    try:
        if stopEvent.is_set():
            return

        if not isMedia(path):
            stats.inc("skipped_not_media")
            stats.addCsvRow(path, None, None, "", "", "not media")
            return

        shellDate = None
        shellDateColumn = None

        if resumeDate:
            try:
                shellDate = datetime.datetime.strptime(resumeDate, "%Y-%m-%d %H:%M:%S")
                shellDateColumn = "resume CSV date"
            except ValueError:
                pass

        if shellDate is None:
            shellDate, shellDateColumn = getShellDate(path, dateOrder=options.dateOrder)

        usedEmbeddedSeconds = False
        filesystemSecondsSource = ""

        if shellDate is None:
            shellDate = embeddedCaptureDate(path, options)
            shellDateColumn = "embedded metadata"

            if shellDate is None:
                stats.inc("skipped_no_date")
                stats.addCsvRow(path, None, None, "", "", "no shell or embedded date")
                return

        if not inDateRange(shellDate, options.fromDate, options.toDate):
            stats.inc("skipped_outside_date_range")
            stats.addCsvRow(path, None, shellDate, "", "", "outside date range", dateSource=shellDateColumn)
            return

        if shellDateColumn != "embedded metadata":
            shellDate, usedEmbeddedSeconds = dateWithEmbeddedSeconds(path, shellDate, options)

            if not usedEmbeddedSeconds:
                shellDate, filesystemSecondsSource = dateWithFilesystemSeconds(path, shellDate)

        selectedDateSource = dateDetail(shellDateColumn, usedEmbeddedSeconds, filesystemSecondsSource)

        if isImage(path):
            stats.inc("processed_images")
        elif isVideo(path):
            stats.inc("processed_videos")

        metadata = getAllShellMetadata(path)
        metadata = metadataWithSelectedDate(metadata, path, shellDate)

        if options.keepStructure and srcMode == "folder":
            relDir = relDirFor(str(path), str(src))
            targetDir = dest / relDir if relDir else dest
        else:
            targetDir = dest

        targetDir.mkdir(parents=True, exist_ok=True)

        filename = datetimeToFilename(shellDate)
        ext = path.suffix.lower()
        resumeCopiedPath = Path(resumeCopiedPath) if resumeCopiedPath else None

        if resumeCopiedPath and resumeCopiedPath.exists() and resumeCopiedPath.is_file():
            outPath = resumeCopiedPath
            outPath = correctedPendingOutputPath(resumeCopiedPath, filename, ext)
            outPath = correctedOutputExtension(outPath)

            if not options.quiet:
                with printLock:
                    print("[METADATA RETRY]")
                    print(f"  DATE: {shellDate} ({selectedDateSource})")
                    print(f"  FROM: {path}")
                    print(f"  TO:   {outPath}")
                    print()
        else:
            outPath = reserveUniquePath(filename, ext, str(targetDir), reservedPaths, filenameLock)

            try:
                copyWithRetry(
                    src=path,
                    dest=outPath,
                    stats=stats,
                    retries=options.copyRetries,
                    delay=options.copyRetryDelay,
                )
                outPath = correctedOutputExtension(outPath)
                stats.addCopied(str(outPath))
                stats.addCsvRow(path, outPath, shellDate, True, False, "metadata pending", dateSource=selectedDateSource)
            except KeyboardInterrupt:
                stopEvent.set()
                releaseReservedPath(outPath, reservedPaths, filenameLock)
                raise
            except Exception as e:
                stats.addCopyErrored(str(path))
                releaseReservedPath(outPath, reservedPaths, filenameLock)
                stats.addCsvRow(path, outPath, shellDate, False, "", str(e), dateSource=selectedDateSource)

                with printLock:
                    print(f"[COPY ERROR] {path}")
                    print(e)
                    print()

                return

            if not options.quiet:
                with printLock:
                    print("[COPY]")
                    print(f"  DATE: {shellDate} ({selectedDateSource})")
                    print(f"  FROM: {path}")
                    print(f"  TO:   {outPath}")
                    print()

        if options.writeXmp:
            try:
                writeXmpSidecar(path, outPath, metadata)
                stats.inc("xmp_sidecars_written")
            except Exception as e:
                stats.inc("xmp_sidecar_errors")
                stats.addMetadataErrored(str(path))
                stats.addCsvRow(path, outPath, shellDate, True, False, f"xmp sidecar error: {e}", dateSource=selectedDateSource)

                with printLock:
                    print(f"[XMP SIDECAR ERROR] {path}")
                    print(e)
                    print()

                return

        if options.noMetadata:
            stats.inc("metadata_skipped_disabled")
            metadataOk = ""
            stats.addCsvRow(path, outPath, shellDate, True, metadataOk, "", dateSource=selectedDateSource)
            return

        if options.skipVideoMetadata and isVideo(path):
            stats.inc("metadata_skipped_videos")
            metadataOk = ""
            stats.addCsvRow(path, outPath, shellDate, True, metadataOk, "", dateSource=selectedDateSource)
            return

        rc, exiftoolError = writeEmbeddedMetadata(
            copiedPath=outPath,
            metadata=metadata,
            timeout=options.timeout,
            dateOrder=options.dateOrder,
            exiftoolPath=options.exiftoolPath,
            stats=stats,
            printLock=printLock,
            stopEvent=stopEvent,
        )

        if rc == 0:
            stats.addMetadataWritten(str(outPath))
        elif rc == 124:
            stats.inc("exiftool_timeouts")
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, "exiftool timeout", dateSource=selectedDateSource)
            return
        elif rc == 130:
            stopEvent.set()
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, "interrupted", dateSource=selectedDateSource)
            return
        else:
            stats.inc("exiftool_errors")
            stats.addMetadataErrored(str(path))
            detail = exiftoolError or f"return code {rc}"
            stats.addCsvRow(path, outPath, shellDate, True, False, f"ExifTool Error: {detail}", dateSource=selectedDateSource)
            return

        if options.verify:
            expectedDate = datetimeToExiftool(shellDate)
            verified, verifyError = verifyWrittenDate(
                copiedPath=outPath,
                expectedDate=expectedDate,
                exiftoolPath=options.exiftoolPath,
                timeout=options.timeout,
                printLock=printLock,
            )

            if verified:
                stats.inc("metadata_verified")
                metadataOk = True
                stats.addCsvRow(path, outPath, shellDate, True, True, "", dateSource=selectedDateSource)
            else:
                stats.inc("metadata_verify_failed")
                stats.addMetadataErrored(str(path))
                metadataOk = False
                stats.addCsvRow(path, outPath, shellDate, True, False, verifyError, dateSource=selectedDateSource)
        else:
            metadataOk = True
            stats.addCsvRow(path, outPath, shellDate, True, True, "", dateSource=selectedDateSource)

    except KeyboardInterrupt:
        stopEvent.set()
        raise

    except Exception as e:
        if outPath and outPath.exists():
            stats.addMetadataErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, True, False, str(e), dateSource=selectedDateSource)
        else:
            stats.addCopyErrored(str(path))
            stats.addCsvRow(path, outPath, shellDate, False, metadataOk, str(e), dateSource=selectedDateSource)

        with printLock:
            print(f"[ERROR] {path}")
            print(e)
            print()

    finally:
        if outPath is not None:
            releaseReservedPath(outPath, reservedPaths, filenameLock)
        uninitializeCom()


# ----------------------
# Main
# ----------------------

def main():
    global copySemaphore

    runStartedAt = datetime.datetime.now()
    args = parseArgs()

    if not WINDOWS_SHELL_AVAILABLE:
        print("Error: copy_icloud.py requires pywin32 / pythoncom and Windows Shell integration.")
        print("This script is Windows-only and is not expected to run on Linux.")
        sys.exit(2)

    resumeCompletedSources = set()
    resumeContext = {}
    resumeRows = []
    resumeCopiedDestinations = {}
    resumeCheckpointPath = None
    srcProvided = args.src is not None
    destProvided = args.dest is not None

    if args.resume_csv:
        resumeCsv = resolvePath(args.resume_csv)
        resumeCheckpointPath = resumeCsv if resumeCsv.stem.endswith("_checkpoint") else None

        if not resumeCsv.exists() or not resumeCsv.is_file():
            print(f"Error: resume CSV doesn't exist or is not a file: {resumeCsv}")
            sys.exit(10)

        if resumeCsv.suffix.lower() != ".csv":
            print("Error: --resume-csv requires a .csv file.")
            sys.exit(10)

        try:
            resumeCompletedSources, _resumeSeenSources, resumeContext, resumeRows = loadResumeSources(resumeCsv)
        except Exception as e:
            print(f"Error reading resume CSV '{resumeCsv}': {e}")
            sys.exit(10)

        print(f"Resume CSV loaded: {len(resumeRows)} rows")

    usingSavedContext = args.resume_csv and (not srcProvided or not destProvided)

    if usingSavedContext:
        if not resumeContext.get("run_src") or not resumeContext.get("run_dest"):
            print("Error: this resume CSV does not include run context. Pass src and dest explicitly.")
            sys.exit(10)

        if args.src is None:
            args.src = resumeContext["run_src"]

        if args.dest is None:
            args.dest = resumeContext["run_dest"]

    if args.src is None or args.dest is None:
        print("Error: src and dest are required unless --resume-csv contains run context.")
        sys.exit(2)

    applyRunDefaults(args, resumeContext, inheritInputMode=not srcProvided)
    resumeCompletedSources = completedIcloudSourcesFromRows(
        resumeRows,
        currentFromDate=args.from_date,
        currentToDate=args.to_date,
    )

    if args.resume_csv:
        print(f"Resume completed sources to skip after date filters: {len(resumeCompletedSources)}")

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

    if args.checkpoint_seconds < 0:
        print("Error: --checkpoint-seconds must be >= 0.")
        sys.exit(9)

    logDir = resolvePath(args.log_path)
    txtLogName, csvLogName, checkpointPath = logPaths(LOG_PREFIX, logDir, runStartedAt)
    runContext = buildRunContext(args, src, dest)
    copySemaphore = threading.Semaphore(args.copy_workers)
    fromDate, toDate = parseOptionalDateRange(args.from_date, args.to_date)
    options = CopyOptions(
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
        quiet=args.quiet,
        timeout=args.timeout,
        maxWorkers=args.workers,
        copyRetries=args.copy_retries,
        copyRetryDelay=args.copy_retry_delay,
    )
    stats = Stats()
    stats.setPreviousCsvRows(resumeRows)
    resumeCopiedDestinations = loadResumeCopiedDestinations(resumeRows)
    resumeDetectedDates = loadResumeDetectedDates(resumeRows)
    resumeState = ResumeState(
        completedSources=resumeCompletedSources,
        copiedDestinations=resumeCopiedDestinations,
        detectedDates=resumeDetectedDates,
    )
    checkpointStopEvent = threading.Event()
    checkpointThread = None

    if args.checkpoint_seconds > 0:
        checkpointThread = threading.Thread(
            target=runCheckpointLoop,
            args=(stats, checkpointPath, runContext, args.checkpoint_seconds, checkpointStopEvent, printLock),
            daemon=True,
        )
        checkpointThread.start()

    try:
        copyIcloudMedia(
            src=src,
            dest=dest,
            options=options,
            resumeState=resumeState,
            stats=stats,
        )
    except KeyboardInterrupt:
        stopEvent.set()
        print("\nExecution interrupted by the user")
    finally:
        checkpointStopEvent.set()

        if checkpointThread is not None:
            checkpointThread.join()

        runEndedAt = datetime.datetime.now()
        saveRunLog(
            stats,
            logPrefix=LOG_PREFIX,
            logDir=logDir,
            runContext=runContext,
            runStartedAt=runStartedAt,
            runEndedAt=runEndedAt,
            interrupted=stopEvent.is_set(),
            logName=txtLogName,
            csvLogName=csvLogName,
        )
        removeCheckpoint(checkpointPath, printLock=printLock)

        if resumeCheckpointPath is not None and resumeCheckpointPath != checkpointPath:
            removeCheckpoint(resumeCheckpointPath, printLock=printLock)


if __name__ == "__main__":
    main()
