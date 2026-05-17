#!/usr/bin/env python3
# rename_media.py

import sys
import os
import threading
import concurrent.futures
import warnings
import argparse
import shutil
import wand.image as wand
from wand.exceptions import CorruptImageWarning
from PIL import Image, ExifTags, UnidentifiedImageError
import ffmpeg

from media_common import (
    BaseStats,
    IMAGE_EXTS,
    VIDEO_EXTS,
    defaultWorkers,
    getUniqueFilename,
    isSubpath,
    relDirFor,
    resolvePath,
    saveSimpleLog,
    timestampedName,
)

# ----------------------
# Config / constants
# ----------------------

# Ignore Wand's warnings for corrupted images
warnings.simplefilter("ignore", CorruptImageWarning)

# Global lock for filename generation
filenameLock = threading.Lock()

# Limit ImageMagick internal threads (prevents each decode from spawning many threads)
os.environ.setdefault("MAGICK_THREAD_LIMIT", "1")

# Optional: cap ImageMagick resource usage (RAM / memory-mapped / disk spill)
try:
    from wand import resource as wand_resource
    # memory in bytes (e.g., 512MB), map 1GB, disk 2GB;
    wand_resource.limit(wand_resource.MEMORY, 4 * 1024 * 1024 * 1024)
    wand_resource.limit(wand_resource.MAP,    1 * 1024 * 1024 * 1024)
    wand_resource.limit(wand_resource.DISK,   2 * 1024 * 1024 * 1024)

except Exception:
    pass

# Gate full Wand opens so you never run many RAW decodes at once (default 5)
WAND_SEM = threading.Semaphore(int(os.environ.get("WAND_MAX_CONCURRENT", "5")))


# ----------------------
# Stats helper
# ----------------------

class Stats(BaseStats):
    def __init__(self):
        super().__init__()
        self.damagedFiles = []
        self.unchangedFiles = []

    def addDamaged(self, filename):
        with self.lock:
            self.damagedFiles.append(filename)

    def addUnchanged(self, filename):
        with self.lock:
            self.unchangedFiles.append(filename)

    def summary(self):
        return dict(self.data)

    def getDamaged(self):
        return list(self.damagedFiles)

    def getUnchanged(self):
        return list(self.unchangedFiles)


# ----------------------
# Logging
# ----------------------

def saveLog(stats, filename=None):
    if filename is None:
        filename = timestampedName("rename_media")

    lines = []

    for key in stats.summary().keys():
        lines.append(f"{key}: {stats.summary()[key]}")

    damaged = stats.getDamaged()
    if damaged:
        lines.append("")
        lines.append("Damaged files:")
        lines.extend(damaged)

    unchanged = stats.getUnchanged()
    if unchanged:
        lines.append("")
        lines.append("Unchanged files:")
        lines.extend(unchanged)

    saveSimpleLog(lines, filename)


# ----------------------
# CLI
# ----------------------

def parseArgs():
    parser = argparse.ArgumentParser(
        description="Move or copy media files using capture date from metadata for file naming."
    )
    parser.add_argument("-c", "--copy", action="store_true", help="Copy files instead of moving.")
    parser.add_argument("-m", "--move", action="store_true", help="Move files instead of copying.")
    parser.add_argument("-r", "--recursive", action="store_true", help="Process recursively (including subfolders).")
    parser.add_argument("-k", "--keep-structure", action="store_true", help="Keep 'src' subfolders inside 'dest' (use with -r).")
    parser.add_argument("-w", "--windows", action="store_true", help="Use Windows file times as a fallback.")
    parser.add_argument("--timeout", type=int, default=30, help="Per-file timeout in seconds (default: 30).")
    parser.add_argument("--workers", type=int, default=0, help="Max threads. 0 = auto (default).")
    parser.add_argument("src", help="Source folder.")
    parser.add_argument("dest", help="Destination folder.")

    return parser.parse_args()


# ----------------------
# Parallel runner
# ----------------------

def runParallel(paths, workerFn, stats, timeout=30, isVideo=False, maxWorkers=None):
    if not paths:
        return

    if maxWorkers is None or maxWorkers <= 0:
        maxWorkers = defaultWorkers()

    keyTimeout = "damaged_videos" if isVideo else "damaged_images"

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=maxWorkers)
    futures = {}

    try:
        for path in paths:
            futures[ex.submit(workerFn, path)] = path

        for fut, path in futures.items():
            try:
                fut.result(timeout=timeout)

            except concurrent.futures.TimeoutError:
                print(f"Timeout while processing: {path}")
                stats.inc(keyTimeout)
                stats.addDamaged(str(path))

            except Exception:
                stats.inc(keyTimeout)
                stats.addDamaged(str(path))

    except KeyboardInterrupt:
        for fut in futures:
            fut.cancel()

        ex.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        ex.shutdown(wait=True, cancel_futures=False)


# ----------------------
# Core logic
# ----------------------

def renameMedia(src, dest, recursive, doCopy, useWindows, stats, keepStructure=False, timeout=30, maxWorkers=None):
    files = [p for p in (src.rglob("*") if recursive else src.iterdir()) if p.is_file()]

    if keepStructure:
        srcStr = str(src)
        images = [(str(f), relDirFor(str(f), srcStr)) for f in files if isImage(f)]
        videos = [(str(f), relDirFor(str(f), srcStr)) for f in files if isVideo(f)]
        others = [(str(f), relDirFor(str(f), srcStr)) for f in files if not (isImage(f) or isVideo(f))]
    else:
        images = [str(f) for f in files if isImage(f)]
        videos = [str(f) for f in files if isVideo(f)]
        others = [str(f) for f in files if not (isImage(f) or isVideo(f))]

    runParallel(
        images,
        workerFn=lambda p: renameImage(p, str(dest), doCopy, useWindows, stats),
        stats=stats,
        timeout=timeout,
        isVideo=False,
        maxWorkers=maxWorkers,
    )

    runParallel(
        videos,
        workerFn=lambda p: renameVideo(p, str(dest), doCopy, useWindows, stats),
        stats=stats,
        timeout=timeout,
        isVideo=True,
        maxWorkers=maxWorkers,
    )

    runParallel(
        others,
        workerFn=lambda p: renameOther(p, str(dest), doCopy, useWindows, stats),
        stats=stats,
        timeout=timeout,
        isVideo=False,
        maxWorkers=maxWorkers,
    )


def isImage(file):
    ext = os.path.splitext(file.name)[-1].lower()

    return ext in IMAGE_EXTS


def isVideo(file):
    ext = os.path.splitext(file.name)[-1].lower()

    return ext in VIDEO_EXTS


def renameImage(imagePath, outPath, doCopy, useWindows, stats):
    stats.inc("total_images")

    relDir = ""
    if isinstance(imagePath, tuple):
        imagePath, relDir = imagePath

    filename, ext = os.path.splitext(os.path.basename(imagePath))
    newName = filename

    # method_order = [{"method1": ["tag1", "tag2", ...]}, {"method2": ["tag1", "tag2", ...]}, ...]
    methodOrder = [
        {"wand": ["photoshop:DateCreated", "exif:DateTime", "exif:DateTimeOriginal", "exif:DateTimeDigitized", "dng:create.date"]},
        {"pillow": ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]},
        {"wand": ["date:modify"]}
    ]

    if useWindows:
        methodOrder.append({"windows": None})

    try:
        i = 0
        found = False
        while not found and i < len(methodOrder):
            method = methodOrder[i]

            for tag in method.get("wand", []):
                newNameCandidate = useWand(imagePath, tag)
                if newNameCandidate:
                    newName = newNameCandidate
                    stats.inc("wand_images")
                    found = True
                    break

            for tag in method.get("pillow", []):
                newNameCandidate = usePillow(imagePath, tag)
                if newNameCandidate:
                    newName = newNameCandidate
                    stats.inc("pillow_images")
                    found = True
                    break

            if "windows" in method:
                newNameCandidate = useWin(imagePath)
                if newNameCandidate:
                    newName = newNameCandidate
                    stats.inc("windows_images")
                    found = True

            i += 1

        if newName == filename:
            stats.inc("unchanged_images")
            stats.addUnchanged(imagePath)

        with filenameLock:
            targetDir = os.path.join(outPath, relDir) if relDir else outPath
            os.makedirs(targetDir, exist_ok=True)

            newName = getUniqueFilename(newName, ext, str(targetDir))
            fullPath = os.path.join(targetDir, newName + ext)

            if doCopy:
                shutil.copy2(imagePath, fullPath)
            else:
                shutil.move(imagePath, fullPath)

        print(f"{imagePath} -> {fullPath}")

    except (CorruptImageWarning, UnidentifiedImageError, Exception):
        stats.inc("damaged_images")
        stats.addDamaged(imagePath)


def renameVideo(videoPath, outPath, doCopy, useWindows, stats):
    stats.inc("total_videos")

    relDir = ""
    if isinstance(videoPath, tuple):
        videoPath, relDir = videoPath

    filename, ext = os.path.splitext(os.path.basename(videoPath))
    newName = filename

    methodOrder = [
        {"ffmpeg": ["creation_time", "CREATION_TIME", "com.apple.quicktime.creationdate", "DateTimeOriginal", "DateTime", "DateTimeDigitized"]}
    ]

    if useWindows:
        methodOrder.append({"windows": None})

    try:
        i = 0
        found = False
        while not found and i < len(methodOrder):
            method = methodOrder[i]

            for tag in method.get("ffmpeg", []):
                newNameCandidate = useFFMPEG(videoPath, tag)
                if newNameCandidate:
                    newName = newNameCandidate
                    stats.inc("ffmpeg_videos")
                    found = True
                    break

            if "windows" in method:
                newNameCandidate = useWin(videoPath)
                if newNameCandidate:
                    newName = newNameCandidate
                    stats.inc("windows_videos")
                    found = True

            i += 1

        if newName == filename:
            stats.inc("unchanged_videos")
            stats.addUnchanged(videoPath)

        with filenameLock:
            targetDir = os.path.join(outPath, relDir) if relDir else outPath
            os.makedirs(targetDir, exist_ok=True)

            newName = getUniqueFilename(newName, ext, str(targetDir))
            fullPath = os.path.join(targetDir, newName + ext)

            if doCopy:
                shutil.copy2(videoPath, fullPath)
            else:
                shutil.move(videoPath, fullPath)

        print(f"{videoPath} -> {fullPath}")

    except Exception:
        stats.inc("damaged_videos")
        stats.addDamaged(videoPath)


def renameOther(filePath, outPath, doCopy, useWindows, stats):
    stats.inc("total_others")

    relDir = ""
    if isinstance(filePath, tuple):
        filePath, relDir = filePath

    filename = os.path.basename(filePath)
    name, ext = os.path.splitext(filename)

    try:
        with filenameLock:
            targetDir = os.path.join(outPath, relDir) if relDir else outPath
            os.makedirs(targetDir, exist_ok=True)

            # Keep original basename, but ensure uniqueness to avoid overwrites
            safeName = getUniqueFilename(name, ext, str(targetDir))
            fullPath = os.path.join(targetDir, safeName + ext)

            if doCopy:
                shutil.copy2(filePath, fullPath)
            else:
                shutil.move(filePath, fullPath)

        print(f"{filePath} -> {fullPath}")
        stats.inc("processed_others")

    except Exception:
        stats.inc("damaged_others")
        stats.addDamaged(filePath)


# ----------------------
# Metadata readers
# ----------------------

def useWand(path, tag):
    tag_lower = tag.lower()
    try:
        with WAND_SEM:
            with wand.Image(filename=path) as img:
                for key, value in img.metadata.items():
                    if key.lower() == tag_lower:
                        s = (value or "")
                        # Normalize DNG/modify formats that include timezone
                        if tag_lower.startswith("dng") or tag_lower == "date:modify":
                            s = s.split("+")[0]
                        return s.replace(":", "-").replace(" ", "T")

    except Exception:
        pass

    return None


def usePillow(path, tag):
    _, ext = os.path.splitext(os.path.basename(path))
    if ext.lower() == ".heic":
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
    creationTime = None
    try:
        metadata = ffmpeg.probe(path)

    except Exception:
        return None

    for stream in metadata.get("streams", []):
        tags = stream.get("tags", {}) or {}
        lower = {k.lower(): v for k, v in tags.items()}
        if tag.lower() in lower:
            creationTime = lower[tag.lower()]
            break

    if not creationTime:
        tags = (metadata.get("format", {}) or {}).get("tags", {}) or {}
        lower = {k.lower(): v for k, v in tags.items()}
        if tag.lower() in lower:
            creationTime = lower[tag.lower()]

    if creationTime:
        return creationTime.rstrip("Z").split(".")[0].replace(":", "-").replace(" ", "T")

    return None


def useWin(path):
    try:
        ts = os.path.getmtime(path)
        dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime('%Y-%m-%dT%H-%M-%S')

    except Exception:
        return None


# ----------------------
# Main
# ----------------------

def main():
    args = parseArgs()

    src = resolvePath(args.src)
    dest = resolvePath(args.dest)

    if not src.exists() or not src.is_dir():
        print(f"Error: source folder doesn't exist or is not a folder: {src}")
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

    if isSubpath(dest, src):
        print("Error: destination folder is inside source folder. Choose a different destination.")
        sys.exit(6)

    if (args.copy and args.move) or not (args.copy or args.move):
        print("Error: choose either Copy (-c) or Move (-m).")
        sys.exit(7)

    if args.keep_structure and not args.recursive:
        print("Error: you need the recursive flag (-r) to keep structure (-k).")
        sys.exit(8)

    doCopy = args.copy
    recursive = args.recursive
    keep = args.keep_structure
    useWindows = args.windows
    timeout = args.timeout
    maxWorkers = args.workers if args.workers > 0 else None

    stats = Stats()

    try:
        renameMedia(src, dest, recursive, doCopy, useWindows, stats,
                    keepStructure=keep, timeout=timeout, maxWorkers=maxWorkers)

    except KeyboardInterrupt:
        print("\nExecution interrupted by the user")

    finally:
        saveLog(stats)


if __name__ == "__main__":
    main()
