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

### Import files directly from iCloud Photos on Windows:

```powershell
dateframe import-icloud "C:\Users\You\Pictures\iCloud Photos\Photos" "C:\Users\You\Pictures\iCloud-Renamed"
```

### Copy and rename media from a camera folder or export:

```powershell
dateframe rename --copy "/path/to/originals" "/path/to/renamed"
```

### Write filename dates back into media metadata:

```powershell
dateframe write-dates "/path/to/renamed"
```

For detailed workflows, date-source priority, resume behavior, iCloud caveats, Live Photos, and metadata repair notes, see the [User Guide](docs/user-guide.md).

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

## iCloud Notes

There are important differences between iCloud export paths.

`dateframe import-icloud` works with iCloud Photos for Windows and can preserve useful iCloud/Windows date information. When possible, leave iCloud placeholder files undownloaded and resume failed copies from the generated CSV, because Windows may expose better capture-date information before a file is manually hydrated.

iCloud Web exports may provide Live Photos as image/video pairs, which `dateframe rename` can identify and keep together. For the tradeoffs between both workflows, see the [iCloud section of the user guide](docs/user-guide.md#icloud-photos-for-windows).

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

DateFrame is licensed under the GNU Affero General Public License v3.0 (`AGPL-3.0-only`). See [LICENSE.md](LICENSE.md).
