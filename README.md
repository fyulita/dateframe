# DateFrame

Tools for organizing photo and video libraries by their real capture date while preserving useful metadata and making long operations resumable.

Use this project to:

- copy media from iCloud Photos on Windows while preserving dates from Windows Shell metadata
- rename photos and videos from camera folders using embedded metadata or associated sidecars
- write filename-based capture dates back into media metadata
- inspect metadata and file extensions before processing a library
- recover safely from interrupted long-running operations using CSV logs and checkpoints

`dateframe import-icloud` is Windows-only. The other commands are designed to work across operating systems when their required external tools are installed.

Installing the package provides one `dateframe` command with the subcommands shown below. The corresponding Python modules are kept at the repository root, while shared implementation modules live in `media_tools/`.

## Quick start

Install the dependencies described in [Requirements](#requirements), then choose the workflow that matches your library.

Copy media directly from iCloud Photos on Windows:

```powershell
dateframe import-icloud "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud-Renamed"
```

Copy and rename media from a regular camera or export folder:

```powershell
dateframe rename --copy "C:\Users\You\Pictures\Originals" "C:\Users\You\Pictures\Renamed"
```

Write dates from renamed filenames into metadata:

```powershell
dateframe write-dates "C:\Users\You\Pictures\Renamed"
```

Continue a previous interrupted run using its latest CSV or checkpoint:

```powershell
dateframe rename --resume-csv ".\logs\rename_media_2026-05-22T13-37-05.csv"
dateframe import-icloud --resume-csv ".\logs\copy_icloud_2026-05-21T22-54-12_checkpoint.csv"
```

## Included commands

### `dateframe extensions` (`list_extensions.py`)

Lists file extensions found in a folder. Useful for quickly checking which file types exist before processing them.

It prints counts per extension and can also read paths from a TXT file.

```powershell
dateframe extensions "C:\Users\You\Pictures"
dateframe extensions --input-txt ".\files-to-check.txt"
```

### `dateframe inspect` (`read_metadata.py`)

Reads and prints file metadata using multiple methods:

- `Wand` / ImageMagick
- `Pillow`
- `ffmpeg`
- Windows filesystem dates with `-w`
- associated XMP/XML sidecars with `--sidecars`

This is useful for diagnosing which dates or tags are actually present in an image or video before renaming files or writing metadata.

By default it runs all readers that are appropriate for the current operating system. The Windows filesystem reader is included by default only on Windows. Pass one or more reader flags to run only those methods.

```powershell
dateframe inspect "C:\Users\You\Pictures\IMG_1234.ARW"
dateframe inspect --ffmpeg --sidecars "C:\Users\You\Pictures\VID_1234.MP4"
```

### `dateframe rename` (`rename_media.py`)

Copies or moves media files from a source folder to a destination folder and renames them using the best date found in metadata.

- uses `Wand` / `Pillow` for images
- uses `ffmpeg` for videos
- can use Windows file dates as a fallback with `--windows`
- uses `shutil.copy2` when copying, to preserve file metadata
- keeps folder structure with `--keep-structure`
- can take files from a TXT input list with `--input-txt`
- keeps associated `.xmp` / `.xml` sidecars together with their media file
- keeps Apple Live Photo image/`.MOV` pairs on a common renamed timestamp
- always writes a CSV log that can be used to resume later
- writes a TXT summary log
- writes periodic checkpoint CSVs during long runs
- uses 4 workers by default, which is safer for RAW-heavy folders than automatic thread scaling

Basic copy usage:

```powershell
dateframe rename --copy "C:\Users\You\Pictures\Originals" "C:\Users\You\Pictures\Renamed"
```

Move recursively while keeping the source folder structure:

```powershell
dateframe rename --move --recursive --keep-structure "C:\Users\You\Pictures\Unsorted" "C:\Users\You\Pictures\Sorted"
```

TXT input list usage:

```powershell
dateframe rename --copy --input-txt ".\files-to-rename.txt" "C:\Users\You\Pictures\Renamed"
```

Resume from the latest CSV log:

```powershell
dateframe rename --resume-csv ".\logs\rename_media_2026-05-22T13-37-05.csv"
```

Resume from a checkpoint after a power loss:

```powershell
dateframe rename --resume-csv ".\logs\rename_media_2026-05-22T13-37-05_checkpoint.csv"
```

When resuming, `src` and `dest` can be omitted if the CSV includes run context. You can still override operational flags such as `--workers`, `--windows`, `--quiet`, `--copy`, and `--move`.

`dateframe rename` writes logs to `.\logs` by default. Change this with `--log-path`.

The CSV log records each source path, destination path, detected date, media type, action (`copy` or `move`), date source, whether processing succeeded, errors, and run context. Successful rows are skipped when resuming; failed rows are retried if the source path still exists.

When a sidecar contains a date with timezone offset, the local capture time is used for the filename and the offset is preserved in the CSV. The script does not convert dates to UTC.

Associated sidecars are renamed using the full renamed media filename plus the sidecar extension. For example:

```text
C0001.MP4      -> 2026-03-02T03-20-52.MP4
C0001M01.XML   -> 2026-03-02T03-20-52.MP4.M01.XML
IMG_1234.ARW   -> 2026-03-02T03-20-52.ARW
IMG_1234.XMP   -> 2026-03-02T03-20-52.ARW.XMP
```

The script detects regular same-stem sidecars such as `IMG_1234.XMP`, and Sony-style XML sidecars such as `C0001M01.XML` for `C0001.MP4`.

Apple Live Photo `.HEIC`, `.JPG`, or `.JPEG` images and `.MOV` files are checked with `ExifTool`. They are treated as a pair only when the image identifier (`Apple:ContentIdentifier` / `MediaGroupUUID`) matches the video `QuickTime:ContentIdentifier`, even if their original filenames differ. The image capture date is preferred for both output names so the pair stays visibly associated:

```text
IMG_1234.HEIC   -> 2026-03-02T03-20-52.HEIC
IMG_1234.MOV    -> 2026-03-02T03-20-52.MOV
```

If the image does not provide a usable date, the `.MOV` date is used for both files. Files without a matching embedded identifier remain independent media files. Use `--no-live-photos` to disable this detection; the setting and confirmed pair identifier are recorded and preserved when resuming. Change the ExifTool executable with `--exiftool` when needed.

When a detected Live Photo has a regular same-stem `.XMP` or `.XML` sidecar, that sidecar is kept with the image member of the pair. Sony-style video sidecars such as `C0001M01.XML` continue to stay with their video.

Useful CSV columns:

- `source`: original file path
- `dest`: copied or moved destination path
- `date`: local capture time used for the filename
- `date_offset`: timezone offset found in a sidecar, if available
- `media_type`: `image`, `video`, `sidecar`, or `other`
- `date_source`: where the date came from, such as `wand:dng:create.date` or `sidecar:...:creationdate`
- `pair_type`: `live_photo` for image/video pairs detected as an Apple Live Photo
- `pair_id`: matching embedded identifier used to confirm a Live Photo pair
- `paired_source`: original path of the other file in a Live Photo pair
- `processed_ok`: `True` when the row completed successfully

### `dateframe write-dates` (`write_dates.py`)

Takes files already renamed in the format `YYYY-MM-DDTHH-mm-SS.ext` and writes that date into the file using `ExifTool`.

- supports recursive processing
- can take files from a TXT input list with `--input-txt`
- can write only missing tags with `--if-missing`
- can also update filesystem dates with `--set-filetime`
- always writes a CSV log that can be used to resume later
- writes a TXT summary log
- writes periodic checkpoint CSVs during long runs
- uses 2 workers and a 90-second per-file ExifTool timeout by default

Basic usage:

```powershell
dateframe write-dates "C:\Users\You\Pictures\Renamed"
```

Resume from the latest CSV log:

```powershell
dateframe write-dates --resume-csv ".\logs\write_dates_2026-05-22T12-58-10.csv"
```

Resume with explicit conservative settings:

```powershell
dateframe write-dates --resume-csv ".\logs\write_dates_2026-05-22T12-58-10.csv" --workers 1 --timeout 90
```

Resume from a checkpoint after a power loss:

```powershell
dateframe write-dates --resume-csv ".\logs\write_dates_2026-05-22T12-58-10_checkpoint.csv"
```

TXT input list usage:

```powershell
dateframe write-dates --input-txt ".\files-to-write.txt"
```

`dateframe write-dates` writes logs to `.\logs` by default. Change this with `--log-path`.

The CSV log records each source path, the date parsed from the filename, whether metadata writing succeeded, the write target (`embedded`, generated `.xmp`, or `dry-run`), errors, and run context. On resume, successful rows and definitive skips such as missing dates in filenames are skipped; failed metadata writes are retried.

Generated XMP sidecars use the full media filename plus `.xmp`, for example `2026-03-02T03-20-52.AVI.xmp`.

If a media filename does not contain a date, `write_dates.py` can read a date from an associated sidecar such as `file.ext.xmp`, `file.xmp`, or preserved XML metadata. If the filename already contains a date and an associated sidecar has the same date with a timezone offset, the filename remains the source of truth and the offset is still preserved.

When an offset is available, the local time is written and the offset is written separately where supported. The script does not convert dates to UTC.

Dry-run rows are logged, but they are not treated as completed for later non-dry-run resumes.

Useful CSV columns:

- `source`: file path being updated
- `date`: local date written, formatted as `YYYY-MM-DD HH:mm:ss`
- `date_offset`: timezone offset found in a sidecar, if available
- `date_source`: `filename`, `sidecar:...`, or `filename+sidecar:...`
- `metadata_ok`: `True` when metadata writing finished successfully
- `write_target`: `embedded`, generated `.xmp`, or `dry-run`
- `error`: empty for success; otherwise contains skip/error detail

### `dateframe import-icloud` (`copy_icloud.py`)

Windows-only script designed for iCloud Photos on Windows.

It depends on Windows Shell / COM metadata through `pywin32`, so it is not expected to run on Linux or macOS.

- reads the Windows Shell / iCloud date first
- preserves exact embedded seconds when they match the Windows/iCloud date to the minute
- falls back to embedded capture date when Windows does not expose one
- filters by date range with `--from-date` and `--to-date`
- copies only files that pass the filter
- renames files using the selected capture date
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
dateframe import-icloud "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud Renamed"
```

Date filter usage:

```powershell
dateframe import-icloud --from-date 2020-01-01 --to-date 2020-12-31 "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud Renamed"
```

Low-resource usage:

```powershell
dateframe import-icloud --workers 2 --copy-workers 1 --checkpoint-seconds 300 --quiet "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud Renamed"
```

TXT input list usage:

```powershell
dateframe import-icloud --input-txt ".\files-to-process.txt" "D:\Media\Pictures\iCloud"
```

When `--input-txt` is used, each non-empty, non-comment line is treated as one source file path. Date filters still apply.

Resume from the latest CSV log:

```powershell
dateframe import-icloud --resume-csv ".\logs\copy_icloud_2026-05-21T22-54-12.csv"
```

Resume from a checkpoint after a power loss:

```powershell
dateframe import-icloud --resume-csv ".\logs\copy_icloud_2026-05-21T22-54-12_checkpoint.csv"
```

When resuming, `src` and `dest` can be omitted if the CSV includes run context. You can still override operational flags such as `--workers`, `--copy-workers`, `--exiftool`, `--write-xmp`, `--no-metadata`, `--verify`, and `--quiet`.

Date filters saved in the resume CSV are reused by default. Passing new `--from-date` or `--to-date` values overrides the saved filters. Use `--clear-date-filter` to remove saved date filters when resuming.

#### `dateframe import-icloud` logs

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
- `date`: effective capture date used for naming and metadata
- `copied_ok`: `True` if the file was copied; empty for non-copy skips such as date filters or missing usable date
- `metadata_ok`: `True` if embedded metadata finished successfully, `False` if metadata failed, empty when metadata was intentionally skipped
- `error`: empty for success; otherwise contains the skip/error reason
- `run_resume_csv`: CSV used as the resume source for that row's run
- `run_interrupted`: whether that row's run ended after interruption

Generated XMP sidecars use the full copied media filename plus `.xmp`, for example `2026-03-02T03-20-52.AVI.xmp`.

During processing, a copied file may temporarily appear as:

```text
copied_ok=True
metadata_ok=False
error=metadata pending
```

This protects long runs from power loss: if a file was copied but metadata had not finished yet, a later resume can retry metadata on the already-copied destination instead of copying the file again.

Rows with copy or metadata errors are retried automatically when using `--resume-csv`. Rows that completed successfully, were outside the date range, were not media, or had no usable date are skipped on resume.

If date filters change when resuming, rows that were previously skipped as `outside date range` are reevaluated with the new effective filter.

## Logging and resume behavior

`dateframe import-icloud`, `dateframe rename`, and `dateframe write-dates` share the same logging model:

- every run writes a CSV log and a TXT summary log
- logs are named with the run start time
- logs go to `.\logs` by default and can be changed with `--log-path`
- CSV logs are the source of truth for resume
- TXT logs contain run summary counts, start/end time, interruption state, and the effective command
- periodic checkpoint CSVs are written during long runs
- checkpoint CSVs can be passed to `--resume-csv` after a power loss
- final CSV/TXT logs remove the checkpoint after normal completion or `Ctrl + C`

Use the most recent CSV log when resuming. Each new resume CSV includes accumulated rows from the previous CSV plus newly processed rows, so the newest CSV becomes the next resume point.

## Privacy and safety

These scripts are designed to process personal media libraries. Before processing a large collection, test the command on a small copied sample first and review the produced CSV/TXT logs.

Logs can contain full local file paths, folder names, filenames, selected metadata values, errors, and effective commands. Before sharing logs in an issue, discussion, forum post, or pull request, review and redact any private information.

When using copy or move operations, keep an independent backup of important media until you have verified the output.

## Requirements

Most scripts support Windows, Linux, and macOS when the required external tools are installed.

`dateframe import-icloud` is the exception: it is Windows-only because it depends on Windows Shell metadata and `pywin32`.

### 1. Install Python

Install Python 3.10 or newer from:

- <https://www.python.org/downloads/windows/>

During installation, enable `Add Python to PATH`.

Verify:

```powershell
python --version
pip --version
```

### 2. Install ffmpeg

You need the `ffmpeg` and `ffprobe` executables, because the scripts use `ffmpeg-python` to read video metadata.

You can get it from:

- <https://ffmpeg.org/download.html>

Make sure both `ffmpeg` and `ffprobe` are available in `PATH`.

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

### 3. Install ImageMagick

`Wand` requires ImageMagick to be installed on Windows.

You can download it from:

- <https://imagemagick.org/script/download.php#windows>

During installation, enable the command-line integration needed by `Wand`.

### 4. Install ExifTool

`dateframe write-dates` and `dateframe import-icloud` use `ExifTool` to write metadata. `dateframe rename` uses it to confirm Apple Live Photo pairs through their embedded identifiers.

You can download it from:

- <https://exiftool.org/>

Make sure the executable is available in `PATH`, or pass its full path with `--exiftool`.

Verify:

```powershell
exiftool -ver
```

### 5. Install DateFrame

From a cloned project folder:

```powershell
pip install .
```

After a PyPI release is available, install the published package with:

```powershell
pip install dateframe
```

Notes:

- If you only use `dateframe import-icloud`, you need Python, `pywin32`, and `ExifTool`. `ffmpeg` and ImageMagick are used by the other commands.
- `pywin32` is required for `dateframe import-icloud` and is only available on Windows
- `Wand` requires ImageMagick to already be installed
- `ffmpeg-python` is only a wrapper and does not replace the `ffmpeg` binaries

## Clone and install

Clone this repository, then install DateFrame:

```powershell
git clone https://github.com/fyulita/dateframe.git
cd dateframe
pip install .
```

If you are not using `git`, copying the project folder and running `pip install .` inside it is enough.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, validation guidance, and what information is most useful in a report.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE.md](LICENSE.md).
