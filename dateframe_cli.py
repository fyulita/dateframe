#!/usr/bin/env python3
# dateframe_cli.py

import importlib
import os
import sys


COMMANDS = {
    "import-icloud": ("copy_icloud", "Import and rename media from iCloud Photos on Windows."),
    "rename": ("rename_media", "Copy or move media using capture dates for filenames."),
    "write-dates": ("write_dates", "Write filename-based dates into media metadata."),
    "inspect": ("read_metadata", "Inspect metadata and associated sidecars."),
    "extensions": ("list_extensions", "List file extensions in a source folder."),
}


def printHelp():
    print("usage: dateframe <command> [options]")
    print()
    print("Organize photos and videos by capture date while preserving metadata.")
    print()
    print("commands:")

    for command, (_moduleName, description) in COMMANDS.items():
        print(f"  {command:<13} {description}")

    print()
    print("Run 'dateframe <command> --help' for command-specific options.")


def main():
    args = sys.argv[1:]

    if not args or args[0] in {"-h", "--help"}:
        printHelp()
        return 0

    command = args[0]

    if command not in COMMANDS:
        print(f"Error: unknown command: {command}", file=sys.stderr)
        printHelp()
        return 2

    moduleName, _description = COMMANDS[command]
    module = importlib.import_module(moduleName)
    originalArgv = sys.argv
    originalCommand = os.environ.get("DATEFRAME_SUBCOMMAND")
    sys.argv = [f"dateframe {command}", *args[1:]]
    os.environ["DATEFRAME_SUBCOMMAND"] = command

    try:
        return module.main()
    finally:
        sys.argv = originalArgv

        if originalCommand is None:
            os.environ.pop("DATEFRAME_SUBCOMMAND", None)
        else:
            os.environ["DATEFRAME_SUBCOMMAND"] = originalCommand


if __name__ == "__main__":
    main()
