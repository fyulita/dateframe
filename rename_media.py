#!/usr/bin/env python3
# rename_media.py

import argparse
import datetime
import os
import re
import shutil
import sys
import threading
import warnings
from pathlib import Path

import ffmpeg
import wand.image as wand
from PIL import ExifTags, Image, UnidentifiedImageError
from wand.exceptions import CorruptImageWarning

from capture_dates import captureDateFromAssociatedSidecars
from media_common import (
    BaseStats,
    isImage,
    isSubpath,
    isVideo,
    iterFiles,
    positiveInt,
    relDirFor,
    resolvePath,
    runParallel,
    sidecarPathFor,
)
from media_logging import (
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


# ----------------------
# Config / constants
# ----------------------

warnings.simplefilter("ignore", CorruptImageWarning)

os.environ.setdefault("MAGICK_THREAD_LIMIT", "1")

try:
    from wand import resource as wandResource

    wandResource.limit(wandResource.MEMORY, 4 * 1024 * 1024 * 1024)
    wandResource.limit(wandResource.MAP, 1 * 1024 * 1024 * 1024)
    wandResource.limit(wandResource.DISK, 2 * 1024 * 1024 * 1024)
except Exception:
    pass

CSV_FIELDS = ["source", "dest", "date", "date_offset", "media_type", "action", "date_source", "processed_ok", "error"]
SIDECAR_EXTS = {".xmp", ".xml"}

RUN_CONTEXT_FIELDS = [
    "run_src",
    "run_dest",
    "run_resume_csv",
    "run_input_mode",
    "run_recursive",
    "run_keep_structure",
    "run_windows",
    "run_timeout",
    "run_workers",
    "run_quiet",
    "run_interrupted",
]

SUMMARY_COUNT_KEYS = [
    "total_images",
    "total_videos",
    "total_others",
    "total_sidecars",
    "wand_images",
    "pillow_images",
    "ffmpeg_videos",
    "windows_images",
    "windows_videos",
    "unchanged_images",
    "unchanged_videos",
    "damaged_images",
    "damaged_videos",
    "damaged_others",
    "damaged_sidecars",
    "errors",
    "skipped_resume_completed",
]

filenameLock = threading.Lock()
printLock = threading.Lock()
stopEvent = threading.Event()
WAND_SEM = threading.Semaphore(int(os.environ.get("WAND_MAX_CONCURRENT", "5")))
DEFAULT_WORKERS = 4


# ----------------------
# Stats helper
# ----------------------

class Stats(BaseStats):
    def __init__(self):
        super().__init__()
        self.damagedFiles = []
        self.unchangedFiles = []
        self.csvRows = []
        self.csvRowIndexBySource = {}
        self.previousCsvRows = []

    def addDamaged(self, filename):
        with self.lock:
            self.damagedFiles.append(filename)

    def addUnchanged(self, filename):
        with self.lock:
            self.unchangedFiles.append(filename)

    def addCsvRow(self, source, dest, dateValue, dateOffset, mediaType, action, dateSource, processedOk, error):
        source = str(source)
        sourceKey = pathKey(source)
        row = {
            "source": source,
            "dest": "" if dest is None else str(dest),
            "date": "" if dateValue is None else str(dateValue),
            "date_offset": "" if dateOffset is None else str(dateOffset),
            "media_type": mediaType,
            "action": action,
            "date_source": dateSource,
            "processed_ok": processedOk,
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
    return truthyCsvValue(row.get("processed_ok", "")) and not (row.get("error") or "").strip()


def completedSourcesFromRows(rows):
    completed = set()

    for row in rows:
        source = (row.get("source") or "").strip()

        if source and isCompletedCsvRow(row):
            completed.add(pathKey(source))

    return completed


def rowsBySource(rows):
    return {
        pathKey(row["source"]): row
        for row in rows
        if row.get("source")
    }


def inferResumeAction(rows):
    actions = {
        (row.get("action") or "").strip().lower()
        for row in rows
        if (row.get("action") or "").strip()
    }

    if len(actions) == 1:
        return next(iter(actions))

    return ""


def buildRunContext(args, src, dest):
    inputMode = "txt" if args.input_txt else ("file" if src.is_file() else "folder")

    return {
        "run_src": str(src),
        "run_dest": str(dest),
        "run_resume_csv": "" if args.resume_csv is None else str(resolvePath(args.resume_csv)),
        "run_input_mode": inputMode,
        "run_recursive": args.recursive,
        "run_keep_structure": args.keep_structure,
        "run_windows": args.windows,
        "run_timeout": args.timeout,
        "run_workers": args.workers,
        "run_quiet": args.quiet,
        "run_interrupted": False,
        "run_effective_command": buildEffectiveCommand(args, src, dest),
    }


def buildEffectiveCommand(args, src, dest):
    parts = ["python", "rename_media.py"]

    if args.copy:
        parts.append("--copy")
    else:
        parts.append("--move")

    if args.input_txt:
        parts.append("--input-txt")

    if args.resume_csv:
        parts.extend(["--resume-csv", str(resolvePath(args.resume_csv))])

    if args.recursive:
        parts.append("--recursive")

    if args.keep_structure:
        parts.append("--keep-structure")

    if args.windows:
        parts.append("--windows")

    if args.quiet:
        parts.append("--quiet")

    parts.extend(["--timeout", str(args.timeout)])
    parts.extend(["--workers", str(args.workers)])
    parts.extend(["--checkpoint-seconds", str(args.checkpoint_seconds)])
    parts.extend(["--log-path", str(resolvePath(args.log_path))])
    parts.extend([str(src), str(dest)])

    return " ".join(f'"{part}"' if " " in part else part for part in parts)


def applyRunDefaults(args, resumeContext, inheritInputMode):
    if inheritInputMode and args.input_txt is False:
        args.input_txt = resumeContext.get("run_input_mode") == "txt"

    if args.recursive is None:
        args.recursive = metadataBool(resumeContext.get("run_recursive"), False)

    if args.keep_structure is None:
        args.keep_structure = metadataBool(resumeContext.get("run_keep_structure"), False)

    if args.windows is None:
        args.windows = metadataBool(resumeContext.get("run_windows"), False)

    if args.quiet is None:
        args.quiet = metadataBool(resumeContext.get("run_quiet"), False)

    args.timeout = args.timeout if args.timeout is not None else metadataInt(resumeContext.get("run_timeout"), 30)
    args.workers = args.workers if args.workers is not None else metadataInt(resumeContext.get("run_workers"), DEFAULT_WORKERS)

    if not args.copy and not args.move:
        savedAction = (resumeContext.get("run_action") or "").strip().lower()
        args.copy = savedAction == "copy"
        args.move = savedAction == "move"


# ----------------------
# CLI
# ----------------------

def parseArgs():
    parser = argparse.ArgumentParser(
        description="Copy or move media files using capture date metadata for file naming."
    )
    parser.add_argument("src", nargs="?", help="Source folder/file, or .txt file if --input-txt is used.")
    parser.add_argument("dest", nargs="?", help="Destination folder.")

    parser.add_argument("-c", "--copy", action="store_true", help="Copy files instead of moving.")
    parser.add_argument("-m", "--move", action="store_true", help="Move files instead of copying.")

    parser.add_argument("--input-txt", action="store_true", help="Treat src as a .txt file containing one source path per line.")
    parser.add_argument("--resume-csv", help="Resume from a previous rename_media CSV log. If src/dest are omitted, use saved run context.")

    parser.add_argument("-r", "--recursive", action="store_true", default=None, help="Process recursively when src is a folder.")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="Disable recursive processing when resuming.")
    parser.add_argument("-k", "--keep-structure", action="store_true", default=None, help="Keep src subfolders inside dest. Requires recursive folder mode.")
    parser.add_argument("--no-keep-structure", dest="keep_structure", action="store_false", help="Disable keep-structure when resuming.")

    parser.add_argument("-w", "--windows", action="store_true", default=None, help="Use filesystem modified time as a fallback.")
    parser.add_argument("--no-windows", dest="windows", action="store_false", help="Disable Windows/filesystem fallback when resuming.")

    parser.add_argument("--timeout", type=positiveInt, default=None, help="Legacy per-file timeout setting recorded in logs. Default: 30.")
    parser.add_argument("--workers", type=int, default=None, help=f"Max threads. 0 = auto. Default: {DEFAULT_WORKERS}.")
    parser.add_argument("--checkpoint-seconds", type=float, default=60.0, help="Write a resumable checkpoint CSV every N seconds. Use 0 to disable. Default: 60.")
    parser.add_argument("--quiet", dest="quiet", action="store_true", default=None, help="Suppress per-file success messages. Errors, checkpoints, and final logs are still printed.")
    parser.add_argument("--no-quiet", dest="quiet", action="store_false", help="Disable quiet output when resuming.")
    parser.add_argument("--log-path", default="./logs", help="Folder where TXT and CSV logs are written. Default: ./logs.")

    return parser.parse_args()


# ----------------------
# Metadata readers
# ----------------------

def useWand(path, tag):
    tagLower = tag.lower()

    try:
        with WAND_SEM:
            with wand.Image(filename=path) as img:
                for key, value in img.metadata.items():
                    if key.lower() == tagLower:
                        s = value or ""

                        if tagLower.startswith("dng") or tagLower == "date:modify":
                            s = s.split("+")[0]

                        return s.replace(":", "-").replace(" ", "T")
    except Exception:
        pass

    return None


def usePillow(path, tag):
    if os.path.splitext(os.path.basename(path))[1].lower() == ".heic":
        return None

    try:
        with Image.open(path) as image:
            exifdata = image.getexif()

            for tagId in exifdata:
                currentTag = ExifTags.TAGS.get(tagId, tagId)

                if str(currentTag).lower() == tag.lower():
                    data = exifdata.get(tagId)

                    if isinstance(data, bytes):
                        data = data.decode(errors="ignore")

                    return str(data).replace(":", "-").replace(" ", "T")
    except Exception:
        pass

    return None


def useFFMPEG(path, tag):
    try:
        metadata = ffmpeg.probe(path)
    except Exception:
        return None

    for stream in metadata.get("streams", []):
        tags = stream.get("tags", {}) or {}
        lower = {k.lower(): v for k, v in tags.items()}

        if tag.lower() in lower:
            return lower[tag.lower()].rstrip("Z").split(".")[0].replace(":", "-").replace(" ", "T")

    tags = (metadata.get("format", {}) or {}).get("tags", {}) or {}
    lower = {k.lower(): v for k, v in tags.items()}

    if tag.lower() in lower:
        return lower[tag.lower()].rstrip("Z").split(".")[0].replace(":", "-").replace(" ", "T")

    return None


def useWin(path):
    try:
        dt = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        return dt.strftime("%Y-%m-%dT%H-%M-%S")
    except Exception:
        return None


def imageDate(path, useWindows):
    methodOrder = [
        ("wand", ["photoshop:DateCreated", "exif:DateTime", "exif:DateTimeOriginal", "exif:DateTimeDigitized", "dng:create.date"]),
        ("pillow", ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]),
        ("wand", ["date:modify"]),
    ]

    for method, tags in methodOrder:
        for tag in tags:
            candidate = useWand(path, tag) if method == "wand" else usePillow(path, tag)

            if candidate:
                return candidate, f"{method}:{tag}"

    if useWindows:
        candidate = useWin(path)

        if candidate:
            return candidate, "filesystem:mtime"

    return None, ""


def videoDate(path, useWindows):
    tags = [
        "creation_time",
        "CREATION_TIME",
        "com.apple.quicktime.creationdate",
        "DateTimeOriginal",
        "DateTime",
        "DateTimeDigitized",
    ]

    for tag in tags:
        candidate = useFFMPEG(path, tag)

        if candidate:
            return candidate, f"ffmpeg:{tag}"

    if useWindows:
        candidate = useWin(path)

        if candidate:
            return candidate, "filesystem:mtime"

    return None, ""


# ----------------------
# Core logic
# ----------------------

def targetDirFor(path, srcRoot, dest, keepStructure):
    if keepStructure:
        return dest / relDirFor(str(path), str(srcRoot))

    return dest


def isSidecar(path):
    return path.suffix.lower() in SIDECAR_EXTS


def sonySidecarRegex(stem):
    return re.compile(rf"^{re.escape(stem)}M\d+$", re.IGNORECASE)


def findAssociatedSidecars(mediaPath, sidecarCandidates):
    sidecars = []
    mediaStem = mediaPath.stem
    sonyPattern = sonySidecarRegex(mediaStem)

    for candidate in sidecarCandidates:
        if candidate.parent != mediaPath.parent:
            continue

        candidateStem = candidate.stem

        if candidateStem.lower() == mediaStem.lower() or sonyPattern.match(candidateStem):
            sidecars.append(candidate)

    return sorted(sidecars, key=lambda p: p.name.lower())


def sidecarSuffixFor(mediaPath, sidecarPath, allSidecars):
    sidecarExt = sidecarPath.suffix
    mediaStem = mediaPath.stem
    sidecarStem = sidecarPath.stem
    sonyMatch = re.match(rf"^{re.escape(mediaStem)}(M\d+)$", sidecarStem, re.IGNORECASE)

    if sonyMatch:
        return f".{sonyMatch.group(1).upper()}{sidecarExt}"

    sameExtSidecars = [
        sidecar
        for sidecar in allSidecars
        if sidecar.suffix.lower() == sidecarExt.lower()
    ]

    if len(sameExtSidecars) <= 1:
        return sidecarExt

    if sidecarStem.lower().startswith(mediaStem.lower()):
        remainder = sidecarStem[len(mediaStem):]

        if remainder:
            return f"{remainder}{sidecarExt}"

    return sidecarExt


def buildSidecarMap(files):
    mediaFiles = [path for path in files if isImage(path) or isVideo(path)]
    sidecarCandidates = [path for path in files if isSidecar(path)]
    sidecarMap = {}
    associated = set()

    for mediaPath in mediaFiles:
        sidecars = findAssociatedSidecars(mediaPath, sidecarCandidates)

        if sidecars:
            sidecarMap[pathKey(mediaPath)] = sidecars
            associated.update(pathKey(sidecar) for sidecar in sidecars)

    return sidecarMap, associated


def uniqueMediaPath(name, ext, targetDir, sidecars):
    base = name
    counter = 1

    while True:
        candidate = targetDir / f"{name}{ext}"

        if not candidate.exists() and not any(sidecarPathFor(candidate, sidecarSuffixFor(candidate, sidecar, sidecars)).exists() for sidecar in sidecars):
            return candidate

        name = f"{base}_({counter})"
        counter += 1


def copyOrMove(source, destPath, doCopy):
    if doCopy:
        shutil.copy2(source, destPath)
    else:
        shutil.move(source, destPath)


def copyOrMoveSidecars(sidecars, mediaDestPath, args, stats, dateValue, sourceMedia):
    for sidecar in sidecars:
        if stopEvent.is_set():
            return

        sidecarDest = sidecarPathFor(mediaDestPath, sidecarSuffixFor(Path(sourceMedia), sidecar, sidecars))

        try:
            copyOrMove(str(sidecar), sidecarDest, args.copy)
            stats.inc("total_sidecars")
            stats.addCsvRow(
                sidecar,
                sidecarDest,
                dateValue,
                "",
                "sidecar",
                "copy" if args.copy else "move",
                f"sidecar:{sourceMedia}",
                True,
                "",
            )

            if not args.quiet:
                with printLock:
                    print(f"{sidecar} -> {sidecarDest}")
        except Exception as e:
            stats.inc("total_sidecars")
            stats.inc("damaged_sidecars")
            stats.inc("errors")
            stats.addDamaged(str(sidecar))
            stats.addCsvRow(
                sidecar,
                sidecarDest,
                dateValue,
                "",
                "sidecar",
                "copy" if args.copy else "move",
                f"sidecar:{sourceMedia}",
                False,
                str(e),
            )

            with printLock:
                print(f"[err] {sidecar}: {e}")


def retrySidecarsForCompletedMedia(files, sidecarMap, resumeCompletedSources, resumeRowBySource, args, stats):
    for mediaPath in files:
        mediaKey = pathKey(mediaPath)

        if mediaKey not in resumeCompletedSources:
            continue

        sidecars = [
            sidecar
            for sidecar in sidecarMap.get(mediaKey, [])
            if pathKey(sidecar) not in resumeCompletedSources
        ]

        if not sidecars:
            continue

        mediaRow = resumeRowBySource.get(mediaKey, {})
        mediaDest = mediaRow.get("dest")

        if not mediaDest:
            continue

        copyOrMoveSidecars(
            sidecars,
            mediaDest,
            args,
            stats,
            mediaRow.get("date") or None,
            str(mediaPath),
        )


def incMediaTotal(stats, mediaType):
    if mediaType == "image":
        stats.inc("total_images")
    elif mediaType == "video":
        stats.inc("total_videos")
    elif mediaType == "sidecar":
        stats.inc("total_sidecars")
    else:
        stats.inc("total_others")


def incDateSource(stats, mediaType, dateSource):
    if not dateSource:
        return

    if mediaType == "image":
        if dateSource.startswith("wand:"):
            stats.inc("wand_images")
        elif dateSource.startswith("pillow:"):
            stats.inc("pillow_images")
        elif dateSource == "filesystem:mtime":
            stats.inc("windows_images")
    elif mediaType == "video":
        if dateSource.startswith("ffmpeg:"):
            stats.inc("ffmpeg_videos")
        elif dateSource == "filesystem:mtime":
            stats.inc("windows_videos")


def processOne(path, srcRoot, dest, args, stats, sidecarMap):
    if stopEvent.is_set():
        return

    mediaType = "other"
    dateValue = None
    dateOffset = ""
    dateSource = ""
    errorKey = "damaged_others"
    unchanged = False

    if isImage(path):
        mediaType = "image"
        errorKey = "damaged_images"
    elif isVideo(path):
        mediaType = "video"
        errorKey = "damaged_videos"

    try:
        source = str(path)
        originalName, ext = os.path.splitext(os.path.basename(source))
        newName = originalName

        sidecarDate = captureDateFromAssociatedSidecars(source, extraSidecars=sidecarMap.get(pathKey(path), []))

        if sidecarDate:
            dateValue = sidecarDate.filenameValue
            dateOffset = sidecarDate.offset
            dateSource = sidecarDate.source
        elif mediaType == "image":
            dateValue, dateSource = imageDate(source, args.windows)
        elif mediaType == "video":
            dateValue, dateSource = videoDate(source, args.windows)

        if dateValue:
            newName = dateValue
        elif mediaType == "image":
            unchanged = True
        elif mediaType == "video":
            unchanged = True

        if stopEvent.is_set():
            return

        with filenameLock:
            if stopEvent.is_set():
                return

            targetDir = targetDirFor(path, srcRoot, dest, args.keep_structure)
            targetDir.mkdir(parents=True, exist_ok=True)
            sidecars = sidecarMap.get(pathKey(path), [])
            fullPath = uniqueMediaPath(newName, ext, targetDir, sidecars)
            copyOrMove(source, fullPath, args.copy)

        incMediaTotal(stats, mediaType)
        incDateSource(stats, mediaType, dateSource)
        if unchanged and mediaType == "image":
            stats.inc("unchanged_images")
            stats.addUnchanged(source)
        elif unchanged and mediaType == "video":
            stats.inc("unchanged_videos")
            stats.addUnchanged(source)

        stats.addCsvRow(source, fullPath, dateValue, dateOffset, mediaType, "copy" if args.copy else "move", dateSource, True, "")

        if not args.quiet:
            with printLock:
                print(f"{source} -> {fullPath}")

        copyOrMoveSidecars(sidecarMap.get(pathKey(path), []), fullPath, args, stats, dateValue, source)

    except (CorruptImageWarning, UnidentifiedImageError, Exception) as e:
        source = str(path)
        incMediaTotal(stats, mediaType)
        incDateSource(stats, mediaType, dateSource)
        if unchanged and mediaType == "image":
            stats.inc("unchanged_images")
            stats.addUnchanged(source)
        elif unchanged and mediaType == "video":
            stats.inc("unchanged_videos")
            stats.addUnchanged(source)
        stats.inc(errorKey)
        stats.inc("errors")
        stats.addDamaged(source)
        stats.addCsvRow(source, None, dateValue, dateOffset, mediaType, "copy" if args.copy else "move", dateSource, False, str(e))

        with printLock:
            print(f"[err] {source}: {e}")


def renameMedia(src, dest, args, resumeCompletedSources, resumeRows, stats):
    files = list(iterFiles(src, args.recursive, inputTxt=args.input_txt, printLock=printLock))
    sidecarMap, associatedSidecars = buildSidecarMap(files)
    resumeRowBySource = rowsBySource(resumeRows)
    retrySidecarsForCompletedMedia(files, sidecarMap, resumeCompletedSources, resumeRowBySource, args, stats)
    files = [path for path in files if pathKey(path) not in associatedSidecars]

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

    srcRoot = src if src.is_dir() and not args.input_txt else None

    def onError(path, error):
        mediaType = "image" if isImage(path) else ("video" if isVideo(path) else "other")
        key = "damaged_images" if mediaType == "image" else ("damaged_videos" if mediaType == "video" else "damaged_others")
        incMediaTotal(stats, mediaType)
        stats.inc(key)
        stats.inc("errors")
        stats.addDamaged(str(path))
        stats.addCsvRow(path, None, None, "", mediaType, "copy" if args.copy else "move", "", False, str(error))

    runParallel(
        files,
        workerFn=lambda path: processOne(path, srcRoot, dest, args, stats, sidecarMap),
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

        resumeContext["run_action"] = inferResumeAction(resumeRows)
        print(f"Resume CSV loaded: {len(resumeRows)} rows")

    if args.resume_csv and args.src is None:
        if not resumeContext.get("run_src"):
            print("Error: this resume CSV does not include run context. Pass src explicitly.")
            sys.exit(10)

        args.src = resumeContext["run_src"]

    if args.resume_csv and args.dest is None:
        if not resumeContext.get("run_dest"):
            print("Error: this resume CSV does not include destination context. Pass dest explicitly.")
            sys.exit(10)

        args.dest = resumeContext["run_dest"]

    if args.src is None or args.dest is None:
        print("Error: src and dest are required unless --resume-csv contains run context.")
        sys.exit(2)

    applyRunDefaults(args, resumeContext, inheritInputMode=not srcProvided)
    resumeCompletedSources = completedSourcesFromRows(resumeRows)

    if args.resume_csv:
        print(f"Resume completed sources to skip: {len(resumeCompletedSources)}")

    if args.workers is None:
        args.workers = DEFAULT_WORKERS

    if args.copy and args.move:
        print("Error: choose either Copy (-c) or Move (-m), not both.")
        sys.exit(7)

    if not (args.copy or args.move):
        print("Error: choose either Copy (-c) or Move (-m).")
        sys.exit(7)

    if args.workers is not None and args.workers < 0:
        print("Error: --workers must be >= 0.")
        sys.exit(2)

    if args.checkpoint_seconds < 0:
        print("Error: --checkpoint-seconds must be >= 0.")
        sys.exit(2)

    src = resolvePath(args.src)
    dest = resolvePath(args.dest)

    if not src.exists():
        print(f"Error: source doesn't exist: {src}")
        sys.exit(2)

    if args.input_txt and (not src.is_file() or src.suffix.lower() != ".txt"):
        print("Error: --input-txt requires src to be a .txt file.")
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

    if not args.input_txt and src.is_dir() and isSubpath(dest, src):
        print("Error: destination folder is inside source folder. Choose a different destination.")
        sys.exit(6)

    if args.keep_structure and (not args.recursive or args.input_txt or not src.is_dir()):
        print("Error: --keep-structure requires recursive folder mode.")
        sys.exit(8)

    logDir = resolvePath(args.log_path)
    txtLogName, csvLogName, checkpointPath = logPaths("rename_media", logDir, runStartedAt)
    runContext = buildRunContext(args, src, dest)
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
        renameMedia(src, dest, args, resumeCompletedSources, resumeRows, stats)
    except KeyboardInterrupt:
        stopEvent.set()
        checkpointStopEvent.set()
        print("\nExecution interrupted by the user")
    finally:
        checkpointStopEvent.set()

        if checkpointThread is not None:
            checkpointThread.join()

        runEndedAt = datetime.datetime.now()
        saveRunLog(
            stats,
            logPrefix="rename_media",
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
