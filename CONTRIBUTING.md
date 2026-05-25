# Contributing to DateFrame

Thanks for helping improve DateFrame. Contributions are welcome for bug fixes, platform compatibility, metadata handling, documentation, and carefully scoped workflow improvements.

## Before contributing

This project works with personal media collections and can copy, move, rename, or modify metadata in files. Use disposable sample files or copies of your media while testing changes.

Never include private media files in an issue or pull request. Logs may expose local paths, filenames, metadata, or effective commands; redact sensitive values before sharing them publicly.

## Development setup

Clone the repository and create a virtual environment:

```powershell
git clone https://github.com/fyulita/dateframe.git
cd dateframe
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

On Linux or macOS, activate the virtual environment with:

```bash
source .venv/bin/activate
```

Depending on the script being changed, you may also need:

- `ffmpeg` / `ffprobe` for video metadata
- ImageMagick for `Wand` image metadata support
- `ExifTool` for writing metadata and confirming Apple Live Photo pairs
- `pywin32` on Windows for `copy_icloud.py`

`copy_icloud.py` is Windows-only because it reads Windows Shell / iCloud metadata.

## Repository layout

The user-facing commands are kept at the repository root:

- `copy_icloud.py`
- `rename_media.py`
- `write_dates.py`
- `read_metadata.py`
- `list_extensions.py`

Shared implementation code lives in `media_tools/`.

## Reporting a bug

Please include:

- operating system and Python version
- script and command used, with private paths redacted if necessary
- relevant file type, such as `.ARW`, `.MP4`, `.HEIC`, `.XML`, or `.XMP`
- whether the operation was new or resumed from a CSV/checkpoint
- expected behavior and actual behavior
- a short, sanitized excerpt from the terminal output or CSV/TXT log

For date-related issues, it is especially useful to include sanitized output from `read_metadata.py` for one representative file.

## Pull requests

Keep changes focused on one problem or feature. When changing processing or resume behavior, consider how it affects:

- ordinary folder input and `--input-txt`
- new runs and `--resume-csv`
- interrupted runs and checkpoint CSVs
- CSV/TXT logging
- sidecar files and naming
- Windows-only versus cross-platform behavior

Please describe how you tested the change and avoid committing logs, generated output, virtual environments, or personal media.

## Basic validation

Before opening a pull request, run the automated tests:

```powershell
python -m pytest
```

Also verify that edited scripts compile:

```powershell
python -m py_compile dateframe_cli.py copy_icloud.py rename_media.py write_dates.py read_metadata.py list_extensions.py media_tools\__init__.py media_tools\capture_dates.py media_tools\copy_icloud_config.py media_tools\media_common.py media_tools\media_logging.py media_tools\metadata_writer.py media_tools\windows_metadata.py
```

For behavior changes, run the relevant command against a small temporary sample folder and inspect its output and logs.
