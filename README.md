# rename-media

Python scripts for inspecting metadata of media files, renaming photos and videos using their real capture date, writing dates back into file metadata, and copying media from iCloud Photos on Windows.

## Included scripts

### `list_extensions.py`

Lists file extensions found in a folder. Useful for quickly checking which file types exist before processing them.

### `read_metadata.py`

Reads and prints file metadata using multiple methods:

- `Wand` / ImageMagick
- `Pillow`
- `ffmpeg`
- Windows filesystem dates with `-w`

This is useful for diagnosing which dates or tags are actually present in an image or video before renaming files or writing metadata.

### `rename_media.py`

Copies or moves media files from a source folder to a destination folder and renames them using the best date found in metadata.

- uses `Wand` / `Pillow` for images
- uses `ffmpeg` for videos
- can use Windows file dates as a fallback with `--windows`
- uses `shutil.copy2` when copying, to preserve file metadata
- keeps folder structure with `--keep-structure`

### `write_dates.py`

Takes files already renamed in the format `YYYY-MM-DDTHH-mm-SS.ext` and writes that date into the file using `ExifTool`.

- supports recursive processing
- can write only missing tags with `--if-missing`
- can also update filesystem dates with `--set-filetime`

### `copy_icloud.py`

Windows-only script designed for iCloud Photos on Windows.

It depends on Windows Shell / COM metadata through `pywin32`, so it is not expected to run on Linux or macOS.

- reads the Windows Shell / iCloud date first
- filters by date range with `--from-date` and `--to-date`
- copies only files that pass the filter
- renames files using that Shell date
- writes embedded metadata with `ExifTool`
- can optionally create `.xmp` sidecar files with `--write-xmp`
- can skip embedded metadata entirely with `--no-metadata`
- can verify written date metadata with `--verify`
- can skip video metadata writing with `--skip-video-metadata`
- always writes a CSV log that can be used to resume later
- writes a TXT summary log
- writes periodic checkpoint CSVs during long runs
- limits concurrent iCloud downloads with `--copy-workers` to avoid overloading iCloud

Basic usage:

```powershell
python copy_icloud.py "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud Renamed"
```

Date filter usage:

```powershell
python copy_icloud.py --from-date 2020-01-01 --to-date 2020-12-31 "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud Renamed"
```

Low-resource usage:

```powershell
python copy_icloud.py --workers 2 --copy-workers 1 --checkpoint-seconds 300 --quiet "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud Renamed"
```

TXT input list usage:

```powershell
python copy_icloud.py --input-txt ".\files-to-process.txt" "D:\Media\Pictures\iCloud"
```

When `--input-txt` is used, each non-empty, non-comment line is treated as one source file path. Date filters still apply.

Resume from the latest CSV log:

```powershell
python copy_icloud.py --resume-csv ".\logs\copy_icloud_2026-05-21T22-54-12.csv"
```

Resume from a checkpoint after a power loss:

```powershell
python copy_icloud.py --resume-csv ".\logs\copy_icloud_2026-05-21T22-54-12_checkpoint.csv"
```

When resuming, `src` and `dest` can be omitted if the CSV includes run context. You can still override operational flags such as `--workers`, `--copy-workers`, `--exiftool`, `--write-xmp`, `--no-metadata`, `--verify`, and `--quiet`.

Date filters saved in the resume CSV are reused by default. Passing new `--from-date` or `--to-date` values overrides the saved filters. Use `--clear-date-filter` to remove saved date filters when resuming.


#### `copy_icloud.py` logs

By default logs are written to `.\logs`. Change this with `--log-path`.

Each run writes:

- `copy_icloud_<start-time>.csv`: main log and resume source
- `copy_icloud_<start-time>.txt`: summary log

During a running operation, the script also writes:

- `copy_icloud_<start-time>_checkpoint.csv`

The checkpoint CSV is overwritten periodically according to `--checkpoint-seconds` and contains the accumulated history up to the last checkpoint. It is useful if the computer loses power or the process ends instantly before the final logs are written.

If the run ends normally or with `Ctrl + C`, the final CSV/TXT logs are written and the checkpoint file is removed.

The checkpoint CSV includes one extra column:

- `run_checkpoint_at`: timestamp of the checkpoint write

The final CSV does not include `run_checkpoint_at`.

Important CSV columns:

- `source`: original file path
- `dest`: copied destination path, when a copy exists
- `date`: Windows Shell date used for naming and metadata
- `copied_ok`: `True` if the file was copied; empty for non-copy skips such as date filters or missing Shell date
- `metadata_ok`: `True` if embedded metadata finished successfully, `False` if metadata failed, empty when metadata was intentionally skipped
- `error`: empty for success; otherwise contains the skip/error reason
- `run_resume_csv`: CSV used as the resume source for that row's run
- `run_interrupted`: whether that row's run ended after interruption

During processing, a copied file may temporarily appear as:

```text
copied_ok=True
metadata_ok=False
error=metadata pending
```

This protects long runs from power loss: if a file was copied but metadata had not finished yet, a later resume can retry metadata on the already-copied destination instead of copying the file again.

Rows with copy or metadata errors are retried automatically when using `--resume-csv`. Rows that completed successfully, were outside the date range, were not media, or had no Shell date are skipped on resume.

If date filters change when resuming, rows that were previously skipped as `outside date range` are reevaluated with the new effective filter.

## Requirements

This project is primarily intended for Windows but it works partially in other operating systems.

`copy_icloud.py` is the exception: it is Windows-only because it depends on Windows Shell metadata and `pywin32`.

### 1. Install Python

Install Python 3.10 or newer from:

- https://www.python.org/downloads/windows/

During installation, enable `Add Python to PATH`.

Verify:

```powershell
python --version
pip --version
```

### 2. Install ffmpeg

You need the `ffmpeg` and `ffprobe` executables, because the scripts use `ffmpeg-python` to read video metadata.

You can get it from:

- https://ffmpeg.org/download.html

Make sure both `ffmpeg` and `ffprobe` are available in `PATH`.

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

### 3. Install ImageMagick

`Wand` requires ImageMagick to be installed on Windows.

You can download it from:

- https://imagemagick.org/script/download.php#windows

During installation, enable the command-line integration needed by `Wand`.

### 4. Install ExifTool

`write_dates.py` and `copy_icloud.py` use `ExifTool`.

You can download it from:

- https://exiftool.org/

Make sure the executable is available in `PATH`, or pass its full path with `--exiftool`.

Verify:

```powershell
exiftool -ver
```

### 5. Install Python dependencies

From the project folder:

```powershell
pip install -r requirements.txt
```

Notes:

- If you only use `copy_icloud.py`, you need Python, `pywin32`, and `ExifTool`. `ffmpeg` and ImageMagick are used by the other scripts.
- `pywin32` is required for `copy_icloud.py` and is only available on Windows
- `Wand` requires ImageMagick to already be installed
- `ffmpeg-python` is only a wrapper and does not replace the `ffmpeg` binaries

## Quick setup

Clone or copy this repository, then install the dependencies:

```powershell
git clone https://github.com/fyulita/rename-media.git
cd rename-media
pip install -r requirements.txt
```

If you are not using `git`, copying the project folder and running `pip install ...` inside it is enough.
