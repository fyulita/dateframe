#!/usr/bin/env python3
# write_dates.py

import argparse
import datetime
import sys
import threading

from media_tools.capture_dates import CaptureDate, captureDateFromAssociatedSidecars, exiftoolDateWithOffset, offsetTags, parseCaptureDate
from media_tools.media_common import (
    BaseStats,
    UNSUPPORTED_EMBED_WRITE,
    datePrecisionFromSource,
    effectiveCommandPrefix,
    isImage,
    isVideo,
    iterFiles,
    positiveInt,
    resolvePath,
    runExiftool,
    runParallel,
    sidecarPathFor,
)
from media_tools.media_logging import (
    loadResumeRows,
    logPaths,
    metadataBool,
    metadataInt,
    pathKey,
    removeCheckpoint,
    runCheckpointLoop,
    saveRunLog,
    truthyCsvValue,
)
from media_tools.metadata_writer import verifyWrittenDate


# ----------------------
# Config / constants
# ----------------------

CSV_FIELDS = ["source", "date", "date_offset", "date_source", "date_precision", "metadata_ok", "write_target", "error"]

RUN_CONTEXT_FIELDS = [
    "run_src",
    "run_resume_csv",
    "run_input_mode",
    "run_recursive",
    "run_if_missing",
    "run_set_filetime",
    "run_dry_run",
    "run_exiftool",
    "run_timeout",
    "run_workers",
    "run_verify",
    "run_quiet",
    "run_interrupted",
]

SUMMARY_COUNT_KEYS = [
    "written",
    "written_sidecars",
    "metadata_verified",
    "metadata_verify_failed",
    "dry_run",
    "skipped_resume_completed",
    "skipped_sidecar",
    "skipped_no_date_in_name",
    "skipped_unsupported_ext",
    "timeouts",
    "errors",
    "tmp_removed",
]

DEFAULT_TIMEOUT = 90
DEFAULT_WORKERS = 2

printLock = threading.Lock()
stopEvent = threading.Event()
LOG_PREFIX = "dateframe_write-dates"


# ----------------------
# Stats helper
# ----------------------

class Stats(BaseStats):
    def __init__(self):
        super().__init__()
        self.failedFiles = []
        self.skippedFiles = []
        self.csvRows = []
        self.csvRowIndexBySource = {}
        self.previousCsvRows = []

    def addFailed(self, filename):
        with self.lock:
            self.failedFiles.append(filename)

    def addSkipped(self, filename):
        with self.lock:
            self.skippedFiles.append(filename)

    def addCsvRow(self, source, dateValue, dateOffset, dateSource, metadataOk, writeTarget, error, datePrecision=""):
        source = str(source)
        sourceKey = pathKey(source)
        if dateValue is not None and not datePrecision:
            datePrecision = datePrecisionFromSource(dateValue, dateSource)
        row = {
            "source": source,
            "date": "" if dateValue is None else str(dateValue),
            "date_offset": "" if dateOffset is None else str(dateOffset),
            "date_source": dateSource,
            "date_precision": datePrecision,
            "metadata_ok": metadataOk,
            "write_target": writeTarget,
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
# CSV / resume helpers
# ----------------------

def isCompletedCsvRow(row):
    error = (row.get("error") or "").strip()

    if truthyCsvValue(row.get("metadata_ok", "")) and not error:
        return True

    return error in {"no date in filename", "unsupported extension", "sidecar file"}


def completedSourcesFromRows(rows):
    completed = set()

    for row in rows:
        source = (row.get("source") or "").strip()

        if source and isCompletedCsvRow(row):
            completed.add(pathKey(source))

    return completed


def buildRunContext(args, src):
    inputMode = "txt" if args.input_txt else ("file" if src.is_file() else "folder")

    return {
        "run_src": str(src),
        "run_resume_csv": "" if args.resume_csv is None else str(resolvePath(args.resume_csv)),
        "run_input_mode": inputMode,
        "run_recursive": args.recursive,
        "run_if_missing": args.if_missing,
        "run_set_filetime": args.set_filetime,
        "run_dry_run": args.dry_run,
        "run_exiftool": args.exiftool,
        "run_timeout": args.timeout,
        "run_workers": args.workers,
        "run_verify": args.verify,
        "run_quiet": args.quiet,
        "run_interrupted": False,
        "run_effective_command": buildEffectiveCommand(args, src),
    }


def buildEffectiveCommand(args, src):
    parts = effectiveCommandPrefix("write_dates.py", "write-dates")

    if args.input_txt:
        parts.append("--input-txt")

    if args.resume_csv:
        parts.extend(["--resume-csv", str(resolvePath(args.resume_csv))])

    if args.recursive:
        parts.append("--recursive")

    if args.dry_run:
        parts.append("--dry-run")

    if args.if_missing:
        parts.append("--if-missing")

    if args.set_filetime:
        parts.append("--set-filetime")

    if args.verify:
        parts.append("--verify")

    if args.quiet:
        parts.append("--quiet")

    parts.extend(["--exiftool", args.exiftool])
    parts.extend(["--timeout", str(args.timeout)])
    parts.extend(["--workers", str(args.workers)])
    parts.extend(["--checkpoint-seconds", str(args.checkpoint_seconds)])
    parts.extend(["--log-path", str(resolvePath(args.log_path))])
    parts.append(str(src))

    return " ".join(f'"{part}"' if " " in part else part for part in parts)


def applyRunDefaults(args, resumeContext, inheritInputMode):
    if inheritInputMode and args.input_txt is False:
        args.input_txt = resumeContext.get("run_input_mode") == "txt"

    if args.recursive is None:
        args.recursive = metadataBool(resumeContext.get("run_recursive"), False)

    if args.if_missing is None:
        args.if_missing = metadataBool(resumeContext.get("run_if_missing"), False)

    if args.set_filetime is None:
        args.set_filetime = metadataBool(resumeContext.get("run_set_filetime"), False)

    if args.verify is None:
        args.verify = metadataBool(resumeContext.get("run_verify"), False)

    if args.dry_run is None:
        args.dry_run = metadataBool(resumeContext.get("run_dry_run"), False)

    if args.quiet is None:
        args.quiet = metadataBool(resumeContext.get("run_quiet"), False)

    args.exiftool = args.exiftool if args.exiftool is not None else (resumeContext.get("run_exiftool") or "exiftool")
    if args.timeout is None:
        args.timeout = max(metadataInt(resumeContext.get("run_timeout"), DEFAULT_TIMEOUT), DEFAULT_TIMEOUT)

    if args.workers is None:
        savedWorkers = metadataInt(resumeContext.get("run_workers"), DEFAULT_WORKERS)
        args.workers = savedWorkers if savedWorkers > 0 else DEFAULT_WORKERS


# ----------------------
# CLI
# ----------------------

def parseArgs():
    parser = argparse.ArgumentParser(
        description="Write capture/create date metadata based on filename (YYYY-MM-DDTHH-MM-SS)."
    )
    parser.add_argument("src", nargs="?", help="Source folder/file, or .txt file if --input-txt is used.")

    parser.add_argument("--input-txt", action="store_true", help="Treat src as a .txt file containing one media path per line.")
    parser.add_argument("--resume-csv", help="Resume from a previous DateFrame write-dates CSV log. If src is omitted, use the saved run context.")

    parser.add_argument("-r", "--recursive", action="store_true", default=None, help="Process recursively when src is a folder.")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="Disable recursive processing when resuming.")

    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None, help="Show what would be written without applying changes.")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Disable dry-run when resuming.")

    parser.add_argument("--if-missing", dest="if_missing", action="store_true", default=None, help="Only write tags if they are currently empty.")
    parser.add_argument("--overwrite-tags", dest="if_missing", action="store_false", help="Overwrite date tags when resuming.")
    parser.add_argument("--set-filetime", dest="set_filetime", action="store_true", default=None, help="Also set filesystem dates (FileModifyDate/CreateDate).")
    parser.add_argument("--no-set-filetime", dest="set_filetime", action="store_false", help="Do not set filesystem dates when resuming.")
    parser.add_argument("--verify", dest="verify", action="store_true", default=None, help="Read metadata back after writing and keep the row pending if the expected date is not found.")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="Disable metadata verification when resuming.")
    parser.add_argument("--exiftool", default=None, help="Path to exiftool binary. Default: exiftool in PATH.")
    parser.add_argument("--timeout", type=positiveInt, default=None, help=f"Per-file ExifTool timeout in seconds. Default: {DEFAULT_TIMEOUT}.")
    parser.add_argument("--workers", type=int, default=None, help=f"Max threads. 0 = auto. Default: {DEFAULT_WORKERS}.")
    parser.add_argument("--checkpoint-seconds", type=float, default=60.0, help="Write a resumable checkpoint CSV every N seconds. Use 0 to disable. Default: 60.")
    parser.add_argument("--quiet", dest="quiet", action="store_true", default=None, help="Suppress per-file success/skip messages. Errors, checkpoints, and final logs are still printed.")
    parser.add_argument("--no-quiet", dest="quiet", action="store_false", help="Disable quiet output when resuming.")
    parser.add_argument("--log-path", default="./logs", help="Folder where TXT and CSV logs are written. Default: ./logs.")

    return parser.parse_args()


# ----------------------
# Tag building
# ----------------------

def buildTagsForImage(dt, onlyIfMissing, offset=""):
    tags = []
    xmpDt = exiftoolDateWithOffset(dt, offset)

    def put(tag, value):
        tags.append(f"-{tag}={value}")

    put("EXIF:DateTimeOriginal", dt)
    put("EXIF:CreateDate", dt)
    put("XMP:CreateDate", xmpDt)

    return tags


def buildTagsForVideo(dt, onlyIfMissing, offset=""):
    tags = []
    xmpDt = exiftoolDateWithOffset(dt, offset)

    def put(tag, value):
        tags.append(f"-{tag}={value}")

    put("QuickTime:CreateDate", dt)
    put("QuickTime:TrackCreateDate", dt)
    put("QuickTime:MediaCreateDate", dt)
    put("XMP:CreateDate", xmpDt)

    return tags


def buildXmpDateTags(dt, onlyIfMissing, offset=""):
    tags = []
    xmpDt = exiftoolDateWithOffset(dt, offset)

    def put(tag, value):
        tags.append(f"-{tag}={value}")

    put("XMP:DateTimeOriginal", xmpDt)
    put("XMP:CreateDate", xmpDt)
    put("XMP:ModifyDate", xmpDt)

    return tags


def buildFiletimeTags(dt):
    return [f"-FileModifyDate={dt}", f"-FileCreateDate={dt}"]


def captureDateForWrite(path):
    filenameDate = parseCaptureDate(path.stem, "filename")

    if filenameDate:
        sidecarDate = captureDateFromAssociatedSidecars(path)

        if sidecarDate and sidecarDate.filenameValue == filenameDate.filenameValue and sidecarDate.offset:
            return CaptureDate(
                filenameValue=filenameDate.filenameValue,
                exiftoolValue=filenameDate.exiftoolValue,
                displayValue=filenameDate.displayValue,
                offset=sidecarDate.offset,
                source=f"filename+{sidecarDate.source}",
            )

        return filenameDate

    return captureDateFromAssociatedSidecars(path)


# ----------------------
# Core logic
# ----------------------

def processOne(path, args, stats):
    if path.suffix.lower() in {".xmp", ".xml"}:
        stats.inc("skipped_sidecar")
        stats.addSkipped(str(path))
        stats.addCsvRow(path, None, "", "", "", "", "sidecar file")
        return

    captureDate = captureDateForWrite(path)

    if not captureDate:
        stats.inc("skipped_no_date_in_name")
        stats.addSkipped(str(path))
        stats.addCsvRow(path, None, "", "", "", "", "no date in filename")
        if not args.quiet:
            with printLock:
                print(f"[skip] {path} (no date found)")
        return

    dt = captureDate.exiftoolValue
    displayDate = captureDate.displayValue
    dateOffset = captureDate.offset
    dateSource = captureDate.source

    isImg = isImage(path)
    isVid = isVideo(path)

    if not (isImg or isVid):
        stats.inc("skipped_unsupported_ext")
        stats.addSkipped(str(path))
        stats.addCsvRow(path, displayDate, dateOffset, dateSource, "", "", "unsupported extension")
        if not args.quiet:
            with printLock:
                print(f"[skip] {path} (unsupported extension)")
        return

    ext = path.suffix.lower()

    if ext in UNSUPPORTED_EMBED_WRITE:
        tags = buildXmpDateTags(dt, args.if_missing, offset=dateOffset)
        tags += offsetTags(dateOffset)
        if args.set_filetime:
            tags += buildFiletimeTags(dt)
        argsList = tags + ["-o", "%d%f.%e.xmp", str(path)]
        writeTarget = str(sidecarPathFor(path, ".xmp"))
    else:
        tags = (
            buildTagsForImage(dt, args.if_missing, offset=dateOffset)
            if isImg
            else buildTagsForVideo(dt, args.if_missing, offset=dateOffset)
        )
        if isImg:
            tags += offsetTags(dateOffset)
        if args.set_filetime:
            tags += buildFiletimeTags(dt)
        argsList = tags + [str(path)]
        writeTarget = "embedded"

    if args.if_missing:
        argsList = ["-wm", "cg"] + argsList

    rc, exiftoolError = runExiftool(
        exiftoolPath=args.exiftool,
        argsList=argsList,
        dryRun=args.dry_run,
        timeout=args.timeout,
        printLock=printLock,
        targetPath=path,
        stats=stats,
        stopEvent=stopEvent,
        printStdout=False,
        returnStderr=True,
    )

    if rc == 0:
        if args.dry_run:
            stats.inc("dry_run")
            metadataOk = ""
            writeTarget = "dry-run"
        elif ext in UNSUPPORTED_EMBED_WRITE:
            stats.inc("written_sidecars")
            metadataOk = True
        else:
            stats.inc("written")
            metadataOk = True

        if args.verify and not args.dry_run:
            verified, verifyError = verifyWrittenDate(path, dt, args.exiftool, args.timeout, printLock)

            if verified:
                stats.inc("metadata_verified")
            else:
                stats.inc("metadata_verify_failed")
                stats.addFailed(str(path))
                stats.addCsvRow(path, displayDate, dateOffset, dateSource, False, writeTarget, verifyError)
                if not args.quiet:
                    with printLock:
                        print(f"[verify failed:{writeTarget}] {path} <- {dt}: {verifyError}")
                return

        stats.addCsvRow(path, displayDate, dateOffset, dateSource, metadataOk, writeTarget, "")
        if not args.quiet:
            with printLock:
                print(f"[ok:{writeTarget}] {path} <- {dt}")
    elif rc == 124:
        stats.inc("timeouts")
        stats.addFailed(str(path))
        stats.addCsvRow(path, displayDate, dateOffset, dateSource, False, writeTarget, "exiftool timeout")
        with printLock:
            print(f"[timeout] {path}")
    elif rc == 130:
        stopEvent.set()
        stats.inc("errors")
        stats.addFailed(str(path))
        stats.addCsvRow(path, displayDate, dateOffset, dateSource, False, writeTarget, "interrupted")
    else:
        stats.inc("errors")
        stats.addFailed(str(path))
        detail = exiftoolError or f"return code {rc}"
        stats.addCsvRow(path, displayDate, dateOffset, dateSource, False, writeTarget, f"ExifTool Error: {detail}")
        with printLock:
            print(f"[err]  {path}")


def writeDates(src, args, resumeCompletedSources, stats):
    files = list(iterFiles(src, args.recursive, inputTxt=args.input_txt, printLock=printLock))

    if resumeCompletedSources:
        beforeCount = len(files)
        files = [path for path in files if pathKey(path) not in resumeCompletedSources]
        skippedCount = beforeCount - len(files)
        stats.inc("skipped_resume_completed", skippedCount)
        with printLock:
            print(f"[RESUME] skipped already completed sources: {skippedCount}")

    if not files:
        print("No files found to process.")
        return

    def onError(path, error):
        stats.inc("errors")
        stats.addFailed(str(path))
        stats.addCsvRow(path, None, "", "", False, "", str(error))

    runParallel(
        files,
        workerFn=lambda path: processOne(path, args, stats),
        maxWorkers=args.workers,
        stopEvent=stopEvent,
        onError=onError,
    )


# ----------------------
# Main
# ----------------------

def main():
    runStartedAt = datetime.datetime.now()
    args = parseArgs()
    resumeContext = {}
    resumeRows = []
    resumeCompletedSources = set()
    resumeCheckpointPath = None
    srcProvided = args.src is not None

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
            resumeCompletedSources, _resumeSeenSources, resumeContext, resumeRows = loadResumeRows(
                resumeCsv,
                csvFields=CSV_FIELDS,
                runContextFields=RUN_CONTEXT_FIELDS,
                isCompletedFn=isCompletedCsvRow,
            )
        except Exception as e:
            print(f"Error reading resume CSV '{resumeCsv}': {e}")
            sys.exit(10)

        print(f"Resume CSV loaded: {len(resumeRows)} rows")

    if args.resume_csv and args.src is None:
        if not resumeContext.get("run_src"):
            print("Error: this resume CSV does not include run context. Pass src explicitly.")
            sys.exit(10)

        args.src = resumeContext["run_src"]

    if args.src is None:
        print("Error: src is required unless --resume-csv contains run context.")
        sys.exit(2)

    applyRunDefaults(args, resumeContext, inheritInputMode=not srcProvided)
    resumeCompletedSources = completedSourcesFromRows(resumeRows)

    if args.resume_csv:
        print(f"Resume completed sources to skip: {len(resumeCompletedSources)}")

    if args.workers is not None and args.workers < 0:
        print("Error: --workers must be >= 0.")
        sys.exit(2)

    if args.checkpoint_seconds < 0:
        print("Error: --checkpoint-seconds must be >= 0.")
        sys.exit(2)

    src = resolvePath(args.src)

    if not src.exists():
        print(f"Error: source doesn't exist: {src}")
        sys.exit(2)

    if args.input_txt and (not src.is_file() or src.suffix.lower() != ".txt"):
        print("Error: --input-txt requires src to be a .txt file.")
        sys.exit(2)

    if not args.input_txt and not src.is_file() and not src.is_dir():
        print(f"Error: source is not a file or folder: {src}")
        sys.exit(2)

    logDir = resolvePath(args.log_path)
    txtLogName, csvLogName, checkpointPath = logPaths(LOG_PREFIX, logDir, runStartedAt)
    runContext = buildRunContext(args, src)
    stats = Stats()
    stats.setPreviousCsvRows(resumeRows)
    checkpointStopEvent = threading.Event()
    checkpointThread = None

    if args.checkpoint_seconds > 0:
        checkpointThread = threading.Thread(
            target=runCheckpointLoop,
            args=(stats, checkpointPath, runContext, args.checkpoint_seconds, checkpointStopEvent, printLock),
            kwargs={
                "csvFields": CSV_FIELDS,
                "runContextFields": RUN_CONTEXT_FIELDS,
            },
            daemon=True,
        )
        checkpointThread.start()

    try:
        writeDates(src, args, resumeCompletedSources, stats)
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
            csvFields=CSV_FIELDS,
            runContextFields=RUN_CONTEXT_FIELDS,
            isCompletedFn=isCompletedCsvRow,
            summaryCountKeys=SUMMARY_COUNT_KEYS,
            logName=txtLogName,
            csvLogName=csvLogName,
        )
        removeCheckpoint(checkpointPath, printLock=printLock)

        if resumeCheckpointPath is not None and resumeCheckpointPath != checkpointPath:
            removeCheckpoint(resumeCheckpointPath, printLock=printLock)


if __name__ == "__main__":
    main()
