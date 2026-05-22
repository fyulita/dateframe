#!/usr/bin/env python3
# list_extensions.py

import sys
import argparse
from collections import Counter

from media_tools.media_common import iterFiles, resolvePath


def parseArgs():
    parser = argparse.ArgumentParser(
        description="List unique file extensions in a folder."
    )
    parser.add_argument("path", help="Target folder, or .txt file if --input-txt is used.")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan subfolders recursively.")
    parser.add_argument("--input-txt", action="store_true", help="Treat path as a .txt file containing one file path per line.")

    return parser.parse_args()


def getExtensions(path, recursive=False, inputTxt=False):
    counts = Counter()

    for entry in iterFiles(resolvePath(path), recursive, inputTxt=inputTxt):
        ext = entry.suffix.lower() or "[no extension]"
        counts[ext] += 1

    return counts


def main():
    args = parseArgs()

    path = resolvePath(args.path)

    if args.input_txt:
        if not path.exists() or not path.is_file() or path.suffix.lower() != ".txt":
            print("Error: --input-txt requires a valid .txt file.")
            sys.exit(1)
    elif not path.exists() or not path.is_dir():
        print("Error: the specified path is not a valid folder.")
        sys.exit(1)

    counts = getExtensions(path, recursive=args.recursive, inputTxt=args.input_txt)

    print("Found extensions:")
    for ext, count in sorted(counts.items()):
        print(f"{ext}: {count}")


if __name__ == "__main__":
    main()
