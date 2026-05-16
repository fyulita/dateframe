#!/usr/bin/env python3
# read_metadata.py

import sys
import os
import datetime
import argparse
from pathlib import Path
import concurrent.futures
import wand.image as wand
from PIL import Image, ExifTags
import ffmpeg


def useWand(path):
    try:
        with wand.Image(filename=path) as img:
            print("Wand Metadata:\n")
            for key, value in img.metadata.items():
                print(f"{key}: {value}")
        print("\n")

    except Exception as e:
        print("Error getting metadata with Wand:")
        print(f"{e}\n")


def usePillow(path):
    try:
        image = Image.open(path)
        exifdata = image.getexif()
        print("Pillow Metadata:\n")

        for tag_id in exifdata:
            # get the tag name, instead of human unreadable tag id
            tag = ExifTags.TAGS.get(tag_id, tag_id)
            data = exifdata.get(tag_id)
            # decode bytes 
            if isinstance(data, bytes):
                data = data.decode()
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
        print(f"Windows Modified Date: {dt}\n")

    except Exception as e:
        print("Error getting Windows Modified Date:")
        print(f"{e}\n")


def runTimeout(func, path, timeout=30):
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, path)
            future.result(timeout=timeout)

    except concurrent.futures.TimeoutError:
        print(f"Timeout: {func.__name__} took more than {timeout} seconds.\n")


def parseArgs():
    parser = argparse.ArgumentParser(
        description="Read metadata using different methods: Wand, Pillow, FFMPEG and Windows (optionally)."
    )
    parser.add_argument("path", help="File path")
    parser.add_argument("-w", "--windows", action="store_true", help="Use Windows method.")
    parser.add_argument("--timeout", type=int, default=30, help="Method timeout in seconds (default: 30).")

    return parser.parse_args()


def main():
    args = parseArgs()

    path = Path(os.path.normpath(args.path)).expanduser().resolve()

    if not path.exists() or not path.is_file():
        print(f"Error: file doesn't exist or is not a file: {path}")
        sys.exit(1)

    timeout_length = args.timeout
    if not timeout_length:
        timeout_length = 30

    runTimeout(useWand, path, timeout=timeout_length)
    runTimeout(usePillow, path, timeout=timeout_length)
    runTimeout(useFFMPEG, path, timeout=timeout_length)
    if args.windows:
        runTimeout(useWindows, path, timeout=timeout_length)


if __name__ == "__main__":
    main()