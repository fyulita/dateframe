#!/usr/bin/env python3
# list_extensions.py

import os
import sys
import argparse

from media_common import iterFiles, resolvePath


def parseArgs():
    parser = argparse.ArgumentParser(
        description="List unique file extensions in a folder."
    )
    parser.add_argument("folder", help="Target folder.")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan subfolders recursively.")

    return parser.parse_args()


def getExtensions(folderPath, recursive=False):
    exts = set()

    for entry in iterFiles(resolvePath(folderPath), recursive):
        exts.add(entry.suffix.lower())

    return exts


def main():
    args = parseArgs()

    if not os.path.isdir(args.folder):
        print("The specified path is not a valid folder.")
        sys.exit(1)

    extensions = getExtensions(args.folder, recursive=args.recursive)

    print("Found extensions:")
    for ext in sorted(extensions):
        print(ext)


if __name__ == "__main__":
    main()
