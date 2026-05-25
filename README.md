# DateFrame

[![PyPI version](https://img.shields.io/pypi/v/dateframe.svg)](https://pypi.org/project/dateframe/)
[![Tests](https://github.com/fyulita/dateframe/actions/workflows/tests.yml/badge.svg)](https://github.com/fyulita/dateframe/actions/workflows/tests.yml)

DateFrame organizes photo and video libraries around their capture date. It can rename media, repair date metadata, import from iCloud Photos on Windows, keep supported sidecars and Apple Live Photo pairs together, and resume long operations from detailed logs.

## Install

Install the published package with Python 3.10 or newer:

```bash
pip install dateframe
```

This installs a single command:

```bash
dateframe --help
```

You do not need to clone this repository to use DateFrame. Cloning is only needed for development or contributing.

## Quick Start

Import files directly from iCloud Photos on Windows:

```powershell
dateframe import-icloud "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud-Renamed"
```

Copy and rename media from a camera folder or export:

```powershell
dateframe rename --copy "/path/to/originals" "/path/to/renamed"
```

Write filename dates back into media metadata:

```powershell
dateframe write-dates "/path/to/renamed"
```

## Commands

| Command | Purpose |
| --- | --- |
| `dateframe import-icloud` | Import and rename media from iCloud Photos for Windows using available iCloud/Windows date metadata. |
| `dateframe rename` | Copy or move media into timestamp-based filenames using embedded metadata and associated sidecars. |
| `dateframe write-dates` | Write capture dates from timestamped filenames or sidecars into metadata. |
| `dateframe inspect` | Inspect a file's available metadata and associated sidecars. |
| `dateframe extensions` | Count file extensions in a folder or input list. |

Use command-specific help for all options:

```bash
dateframe rename --help
dateframe import-icloud --help
```

## Media Handling

`dateframe rename` uses dates found in embedded metadata and supported sidecars. It keeps associated `.xmp` and `.xml` sidecars with renamed media, including Sony-style video XML sidecars:

```text
C0001.MP4    -> 2026-03-02T03-20-52.MP4
C0001M01.XML -> 2026-03-02T03-20-52.MP4.M01.XML
```

Apple Live Photo image/video pairs are confirmed with embedded identifiers through ExifTool, then renamed to a common timestamp:

```text
IMG_1234.JPG -> 2026-03-02T03-20-52.JPG
IMG_1234.MOV -> 2026-03-02T03-20-52.MOV
```

## Logging And Resume

The processing commands (`import-icloud`, `rename`, and `write-dates`) write logs to `./logs` by default:

- A CSV log records per-file results and is the source used to resume.
- A TXT log records run times, the effective command, interruption state, and summary counts.
- A periodic checkpoint CSV preserves recent progress if a run is interrupted suddenly.

Resume an interrupted run from its most recent CSV or checkpoint:

```bash
dateframe rename --resume-csv "./logs/rename_media_2026-05-22T13-37-05.csv"
dateframe import-icloud --resume-csv "./logs/copy_icloud_2026-05-21T22-54-12_checkpoint.csv"
```

The latest resumed CSV includes previously recorded history, so it becomes the next file to use when continuing.

## iCloud Notes

There are important differences between iCloud export paths:

- `dateframe import-icloud` operates on the iCloud Photos folder exposed by the Windows application. It can preserve useful iCloud/Windows date information, but Live Photo video components are not available there when iCloud exposes only the image file.
- Downloads from iCloud Web may provide Live Photos as image and video pairs, which `dateframe rename` can identify and keep together. Some exported files may not contain reliable capture-date metadata.

Extended workflow guidance and findings about iCloud exports, Live Photos, metadata sources, and recovery strategies are good candidates for the project wiki.

## Requirements

DateFrame itself is installed through `pip`, but some operations require external tools:

| Dependency | Used for |
| --- | --- |
| [ExifTool](https://exiftool.org/) | Writing metadata and confirming Live Photo pairs. |
| [ffmpeg](https://ffmpeg.org/download.html) | Reading video metadata. |
| [ImageMagick](https://imagemagick.org/script/download.php) | Image metadata access through Wand. |

`dateframe import-icloud` is Windows-only because it uses Windows Shell/iCloud metadata through `pywin32`. The other commands are designed for Windows, Linux, and macOS when their required external tools are available.

If you only use `dateframe import-icloud`, you need ExifTool but do not need ffmpeg or ImageMagick.

## Safety

Before running a large import or rename operation, test on a small copied sample and inspect the produced CSV/TXT logs. Logs may contain local file paths, filenames, metadata values, and command arguments; redact private information before sharing them publicly.

Keep an independent backup of important media until you have verified the results.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and validation guidance.

For security-sensitive reports, see [SECURITY.md](SECURITY.md).

## License

DateFrame is licensed under the GNU General Public License v3.0. See [LICENSE.md](LICENSE.md).
