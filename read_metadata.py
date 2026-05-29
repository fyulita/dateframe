#!/usr/bin/env python3
# read_metadata.py

import sys
import os
import datetime
import argparse
import multiprocessing
import platform
import xml.etree.ElementTree as ET
import wand.image as wand
from PIL import Image, ExifTags
import ffmpeg

from media_tools.capture_dates import associatedSidecarPaths, captureDateFromAssociatedSidecars, captureDateFromXml
from media_tools.media_common import isImage, isVideo, positiveInt, resolvePath


IS_WINDOWS = platform.system() == "Windows"


def useWand(path):
    try:
        with wand.Image(filename=path) as img:
            print("Wand Metadata (raw ImageMagick properties; date:create/date:modify may be filesystem timestamps):\n")
            for key, value in img.metadata.items():
                print(f"{key}: {value}")
        print("\n")

    except Exception as e:
        print("Error getting metadata with Wand:")
        print(f"{e}\n")


def usePillow(path):
    try:
        with Image.open(path) as image:
            exifdata = image.getexif()
            print("Pillow Metadata:\n")

            for tag_id in exifdata:
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                data = exifdata.get(tag_id)

                if isinstance(data, bytes):
                    data = data.decode(errors="ignore")

                print(f"{tag:25}: {data}")
        print("\n")

    except Exception as e:
        print("Error getting metadata with Pillow:")
        print(f"{e}\n")


def useFFMPEG(path):
    try:
        data = ffmpeg.probe(path)
        print("FFMPEG Metadata:\n")

        for stream in data.get("streams", []):
            tags = stream.get("tags", {}) or {}
            for key, value in tags.items():
                print(f"{key}: {value}")

        format_tags = (data.get("format", {}) or {}).get("tags", {}) or {}
        for key, value in format_tags.items():
            print(f"{key}: {value}")
        
        print("\n")

    except Exception as e:
        print("Error getting metadata with ffmpeg:")
        print(f"{e}\n")


def useWindows(path):
    try:
        ts = os.path.getmtime(path)
        dt = datetime.datetime.fromtimestamp(ts)
        print(f"Windows Modified Date (local filesystem time, not embedded capture metadata): {dt}\n")

    except Exception as e:
        print("Error getting Windows Modified Date:")
        print(f"{e}\n")


def printXmlPreview(path):
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        print(f"  XML parse error: {e}")
        return

    print(f"  XML root: {root.tag}")
    parsed = captureDateFromXml(path)

    if parsed:
        print(f"  Parsed date: {parsed.displayValue}")
        print(f"  Parsed offset: {parsed.offset or '[none]'}")
        print(f"  Parsed source: {parsed.source}")


def useSidecars(path):
    print("Associated Sidecars:\n")
    seen = set()
    found = False

    for candidate in associatedSidecarPaths(path):
        candidate = resolvePath(candidate)
        key = str(candidate).lower()

        if key in seen:
            continue

        seen.add(key)

        if not candidate.exists():
            continue

        found = True
        print(candidate)

        if candidate.suffix.lower() in {".xmp", ".xml"}:
            printXmlPreview(candidate)

    if not found:
        print("[none]")

    parsed = captureDateFromAssociatedSidecars(path)

    if parsed:
        print("\nSelected sidecar capture date:")
        print(f"  date: {parsed.displayValue}")
        print(f"  offset: {parsed.offset or '[none]'}")
        print(f"  source: {parsed.source}")

    print("\n")


def runTimeout(func, path, timeout=30):
    process = multiprocessing.Process(target=func, args=(path,))
    process.start()
    process.join(timeout)

    timedOut = process.is_alive()

    if timedOut:
        process.terminate()
        process.join()
        print(f"Timeout: {func.__name__} took more than {timeout} seconds.\n")

    process.close()
    return not timedOut


def parseArgs():
    parser = argparse.ArgumentParser(
        description="Read metadata using different methods: Wand, Pillow, FFMPEG and Windows (optionally)."
    )
    parser.add_argument("path", help="File path")
    parser.add_argument("--wand", action="store_true", help="Run only the Wand reader, or include it with other selected readers.")
    parser.add_argument("--pillow", action="store_true", help="Run only the Pillow reader, or include it with other selected readers.")
    parser.add_argument("--ffmpeg", action="store_true", help="Run only the ffmpeg reader, or include it with other selected readers.")
    parser.add_argument("-w", "--windows", action="store_true", help="Run only the Windows filesystem date reader, or include it with other selected readers.")
    parser.add_argument("--sidecars", action="store_true", help="Run only associated sidecar inspection, or include it with other selected readers.")
    parser.add_argument("--timeout", type=positiveInt, default=30, help="Maximum runtime per metadata reader in seconds (default: 30).")

    return parser.parse_args()


def selectedReaders(args, path):
    selected = any([args.wand, args.pillow, args.ffmpeg, args.windows, args.sidecars])

    if selected:
        return [
            (useWand, args.wand),
            (usePillow, args.pillow),
            (useFFMPEG, args.ffmpeg),
            (useWindows, args.windows),
            (useSidecars, args.sidecars),
        ]

    return [
        (useWand, isImage(path)),
        (usePillow, isImage(path)),
        (useFFMPEG, isVideo(path)),
        (useWindows, IS_WINDOWS),
        (useSidecars, True),
    ]


def main():
    args = parseArgs()

    path = resolvePath(args.path)

    if not path.exists() or not path.is_file():
        print(f"Error: file doesn't exist or is not a file: {path}")
        sys.exit(1)

    for reader, enabled in selectedReaders(args, path):
        if enabled:
            runTimeout(reader, path, timeout=args.timeout)


if __name__ == "__main__":
    main()
