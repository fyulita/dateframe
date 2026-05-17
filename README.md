# rename-media

Python scripts for inspecting metadata, renaming photos and videos using their real capture date, writing dates back into file metadata, and copying media from iCloud Photos on Windows.

## Included scripts

### `media_common.py`

Shared helpers used by the main scripts.

- shared image/video extension sets
- media type helpers
- shared path and date helpers
- shared ExifTool runner
- shared parallel helpers
- base stats helper

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
- can write a CSV result log with `--csv-log`
- limits concurrent iCloud downloads with `--copy-workers` to avoid overloading iCloud

### `list_extensions.py`

Lists file extensions found in a folder. Useful for quickly checking which file types exist before processing them.

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
