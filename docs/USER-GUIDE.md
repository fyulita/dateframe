# DateFrame User Guide

This guide explains how DateFrame behaves in real workflows: what each public
command does, how dates are selected, which files are changed, how logs and
resume work, and what we have learned from iCloud exports, Live Photos, and
metadata repair.

## Command Overview

DateFrame exposes one command with five subcommands:

```bash
dateframe import-icloud
dateframe rename
dateframe write-dates
dateframe inspect
dateframe extensions
```

| Command | Main purpose | Changes media files? | Writes processing logs? |
| --- | --- | --- | --- |
| `import-icloud` | Copy media from iCloud Photos for Windows while preserving and writing dates | Copies files and normally writes metadata to the copies | Yes |
| `rename` | Copy or move exported/local media into capture-date filenames | Copies or moves media and associated sidecars | Yes |
| `write-dates` | Write dates from filenames or sidecars into media metadata | Writes metadata, or XMP for unsupported embed formats | Yes |
| `inspect` | Display available metadata and associated sidecars | No | No |
| `extensions` | Count extensions in a folder or path list | No | No |

DateFrame names media using:

```text
YYYY-MM-DDTHH-MM-SS.ext
```

When a destination name already exists, processing commands use a numbered
suffix such as:

```text
2026-03-02T10-20-30_(1).jpg
```

## Shared Concepts

### Media Types

Recognized images:

```text
.jpg .jpeg .png .heic .tiff .arw .webp .dng .thm
```

Recognized videos:

```text
.mp4 .mkv .mov .avi .webm .mpg .mpeg .wmv .mts .m2ts .3gp
```

### Input Modes

Processing commands accept a file or folder. Where documented, they can also
read an input text file:

```bash
dateframe rename --input-txt files.txt destination
```

Text lists contain one file path per line. Blank lines and lines beginning
with `#` are ignored. Quoted paths are accepted. Paths that do not resolve to
existing files are skipped.

### Logs And Resume

`import-icloud`, `rename`, and `write-dates` create:

| Output | Purpose |
| --- | --- |
| TXT log | Run start/end time, effective public command, interruption state, and counts |
| CSV log | Per-file outcome and data required to continue later |
| Checkpoint CSV | Periodic resumable snapshot while a run is in progress |

Current log name prefixes are:

```text
dateframe_import-icloud_
dateframe_rename_
dateframe_write-dates_
```

Legacy CSV logs using script-based prefixes, such as `copy_icloud_` or
`rename_media_`, remain usable as `--resume-csv` inputs.

A resumed CSV contains previously recorded rows plus new or retried rows. Use
the latest CSV when continuing a repeatedly interrupted run.

## `dateframe import-icloud`

### Purpose

`import-icloud` imports images and videos from the iCloud Photos folder exposed
by the Windows iCloud application. It uses Windows Shell/iCloud metadata as its
primary date source, copies each file into a timestamped destination name, and
normally writes the selected date into the copied media with ExifTool.

This command is Windows-only because it depends on Windows Shell metadata
access through `pywin32`.

Example:

```powershell
dateframe import-icloud "C:\Users\You\Pictures\iCloud Photos\Photos" "D:\Pictures\iCloud"
```

### Important iCloud Workflow Rule

When possible, run this command while the iCloud source files are placeholders
and have not been manually downloaded. Windows may expose useful iCloud capture
metadata before hydration and replace it with local filesystem information
after a file is downloaded.

If a copy fails after DateFrame has already detected the date, resume from its
CSV. The saved date can be reused even if the source file has since changed
state in iCloud.

### Date Selection

For every media file, `import-icloud` establishes a base date and may then
improve its seconds.

#### Base Date Priority

When resuming, the stored `date` from the previous CSV is used first. This
prevents a hydrated or changed iCloud file from replacing a date that was
successfully read earlier.

For a new image, Windows Shell date fields are tried in this order:

```text
Date taken
Media created
Date acquired
Content created
```

For a new video, Windows Shell date fields are tried in this order:

```text
Media created
Date taken
Date acquired
Content created
```

If Windows Shell provides no usable date, DateFrame falls back to embedded
metadata read through ExifTool.

Embedded image date priority:

```text
DateTimeOriginal
CreateDate
DateTimeDigitized
```

Embedded video date priority:

```text
DateTimeOriginal
CreateDate
MediaCreateDate
TrackCreateDate
```

#### Refining Missing Seconds

iCloud/Windows Shell frequently exposes a date only through the minute, such
as `2023-10-26 18:35:00`. When a Shell or resume date ends in `:00`,
DateFrame tries to recover exact seconds in this order:

```text
1. Matching embedded capture date
2. Matching local filesystem creation time
3. Matching local filesystem modification time
```

An embedded or filesystem timestamp is only accepted when its year, month,
day, hour, and minute match the base date. Only the missing seconds are being
trusted.

Example:

```text
Base Date taken:     2023-10-26 18:35:00
FileCreateDate:      2023-10-26 18:35:37  -> accepted
FileModifyDate:      2023-10-26 18:36:06  -> rejected
Selected date:       2023-10-26 18:35:37
```

On Windows, filesystem timestamps obtained by Python are interpreted in local
time. Thus a filesystem timestamp displayed externally as
`2023-10-26T21:35:37+00:00` is used locally as `2023-10-26 18:35:37` on a
machine configured for Buenos Aires time, rather than being written into the
filename as UTC wall-clock time.

### Copy, Extension Correction, And Metadata Writing

After selecting a date, DateFrame:

1. Builds a destination filename from the selected timestamp.
2. Copies the source media to the destination.
3. Corrects known extension/content mismatches when they are detected.
4. Injects the selected date into the metadata being mapped to the copy.
5. Runs ExifTool unless `--no-metadata` or the relevant skip option is set.

iCloud Photos for Windows can expose files named `.PNG` whose actual file
content is JPEG. ExifTool rejects those files as invalid PNGs, even though
image viewers often open them normally. DateFrame detects this specific case
from the file signature after copying and renames the destination to `.jpg`
before writing metadata. On resume, copied-but-pending `.png` files with JPEG
content are repaired the same way before metadata is retried.

For images, the selected date is written as `Date taken` and mapped to image
date tags such as EXIF `DateTimeOriginal` and `CreateDate`.

For videos, it is written as `Media created` and mapped to QuickTime date tags
where supported.

`--write-xmp` additionally writes a sidecar containing Windows Shell metadata.
`--verify` reads back expected date tags after writing.

### Copy Retries And Interruptions

iCloud placeholders may fail during hydration with a cloud timeout. DateFrame
retries these copy operations according to:

```text
--copy-retries
--copy-retry-delay
--copy-workers
```

Interrupting with `Ctrl+C` stops retry waiting promptly and saves available
run logs.

### Resume Behavior

A completed source is skipped on resume. A copied file whose metadata failed
can be retried without copying it again.

If a copied-but-pending file was originally named with `:00` seconds and a
retry recovers exact seconds, DateFrame moves the local copied file to the
corrected timestamp filename before writing metadata. If a copied-but-pending
file was named `.png` but is really JPEG data, DateFrame moves it to `.jpg`
before writing metadata. Completed rows from an older run are not
automatically renamed or reprocessed.

### CSV Fields

The `import-icloud` CSV includes:

| Field | Meaning |
| --- | --- |
| `source` | Original iCloud path |
| `dest` | Copied output path |
| `date` | Selected timestamp used for output naming and metadata |
| `date_source` | Why that timestamp was selected |
| `copied_ok` | Whether copying succeeded |
| `metadata_ok` | Whether metadata writing succeeded or was applicable |
| `error` | Error or pending explanation |

Typical `date_source` values:

```text
Date taken
Media created
Date taken + embedded seconds
Date taken + filesystem created seconds
Date taken + filesystem modified seconds
resume CSV date + filesystem created seconds
embedded metadata
```

### Selected Options

| Option | Function |
| --- | --- |
| `--resume-csv PATH` | Continue from a previous final or checkpoint CSV |
| `--from-date`, `--to-date` | Limit selected capture dates inclusively |
| `--clear-date-filter` | Ignore date filters inherited from a resume CSV |
| `-r`, `--recursive` | Process source subfolders |
| `-k`, `--keep-structure` | Reproduce source subfolders in destination |
| `--input-txt` | Process paths listed in a text file |
| `--no-metadata` | Copy without ExifTool metadata writing |
| `--write-xmp` | Also save mapped Shell data in XMP sidecars |
| `--verify` | Verify written date tags |
| `--skip-video-metadata` | Avoid embedding metadata in video copies |
| `--checkpoint-seconds N` | Periodically write a resume checkpoint |
| `--log-path PATH` | Choose log directory |

### Known Limitation

iCloud Photos for Windows may expose only the still image component of a Live
Photo. When the matching video component is absent from the source folder,
`import-icloud` cannot preserve it. iCloud Web exports can provide Live Photos
as image/video pairs for processing with `dateframe rename`.

Another observed iCloud behavior is that some placeholder files preserve more
useful Windows Shell date information before manual download than after
hydration. If a file has already failed after DateFrame detected its date,
prefer resuming from the generated CSV instead of starting over from changed
source metadata.

## `dateframe rename`

### Purpose

`rename` processes media from folders, individual files, or file lists. It
detects a capture timestamp, then copies or moves each file into a timestamped
name. It is designed for camera folders and exports, including iCloud Web
downloads.

Exactly one operation must be selected:

```bash
dateframe rename --copy source destination
dateframe rename --move source destination
```

### Date Selection Priority

Associated XMP or XML sidecars are considered before embedded media metadata.
If an associated sidecar contains a parseable capture date, that date is used.

For images without a usable sidecar, the current reader order is:

```text
Wand:   photoshop:DateCreated
Wand:   exif:DateTime
Wand:   exif:DateTimeOriginal
Wand:   exif:DateTimeDigitized
Wand:   dng:create.date
Pillow: DateTimeOriginal
Pillow: DateTime
Pillow: DateTimeDigitized
Wand:   date:modify
```

If `--windows` is enabled and no earlier source succeeds, filesystem
modification time is used.

For videos without a usable sidecar, ffmpeg tags are tried in this order:

```text
creation_time
CREATION_TIME
com.apple.quicktime.creationdate
DateTimeOriginal
DateTime
DateTimeDigitized
```

If `--windows` is enabled and no tag succeeds, filesystem modification time is
used.

### Sidecars

The command recognizes `.xmp` and `.xml` sidecars. It associates:

```text
IMAGE.JPG.xmp
IMAGE.xmp
C0001M01.XML
C0001.MP4.M01.XML
```

with their media where the naming matches supported patterns. When the media
is copied or moved, its associated sidecars are copied or moved to the
timestamped name as well.

Example:

```text
C0001.MP4    -> 2026-03-02T10-20-30.MP4
C0001M01.XML -> 2026-03-02T10-20-30.MP4.M01.XML
```

### Apple Live Photos

Live Photo detection is enabled by default. DateFrame reads Apple identifiers
with ExifTool from eligible image files (`.heic`, `.jpg`, `.jpeg`) and `.mov`
videos. A pair is recognized only when exactly one image and exactly one video
share the identifier.

The pair receives one common timestamp name:

```text
IMG_1234.JPG -> 2026-03-02T10-20-30.JPG
IMG_1234.MOV -> 2026-03-02T10-20-30.MOV
```

If one part of a detected pair was already processed before an interrupted
run, resume information restores the pairing for the remaining part.

Use `--no-live-photos` to turn this behavior off.

### Copy Versus Move

`--copy` leaves sources intact and writes new destination files. `--move`
relocates source media and sidecars. Before using `--move` over important
media, validate the operation using a copied sample and preserve an
independent backup.

### CSV Fields

The `rename` CSV includes the output destination, selected date, optional
timezone offset, media type, action (`copy` or `move`), `date_source`, Live
Photo pairing fields, success state, and errors.

### Selected Options

| Option | Function |
| --- | --- |
| `--copy`, `--move` | Select non-destructive copy or source-moving operation |
| `--resume-csv PATH` | Resume an earlier logged run |
| `--input-txt` | Process paths listed in a text file |
| `-r`, `--recursive` | Process subfolders |
| `-k`, `--keep-structure` | Preserve folder layout in destination |
| `--windows` | Allow filesystem modified time as last-resort date source |
| `--live-photos`, `--no-live-photos` | Enable or disable Apple pair handling |
| `--exiftool PATH` | Select ExifTool binary for Live Photo detection |
| `--checkpoint-seconds N` | Periodically write a resume checkpoint |
| `--log-path PATH` | Choose log directory |

### Known Timestamp Caveat

Some Wand values such as `date:create` or `date:modify` can represent
filesystem timestamps with a UTC offset rather than a true local capture date.
The fallback handling for timezone-aware Wand values should be audited before
depending on it for files that do not contain a stronger capture-date source.

## `dateframe write-dates`

### Purpose

`write-dates` writes capture date metadata into already named media files.
Its normal input is a folder produced by `rename` or `import-icloud`, where
file stems begin with:

```text
YYYY-MM-DDTHH-MM-SS
```

Example:

```bash
dateframe write-dates "/path/to/renamed"
```

### Date Selection

The command first parses the timestamp from the filename. If that timestamp
exists, it is used as the date to write.

If an associated sidecar contains the same timestamp and also contains an
offset, the offset is preserved while the filename remains the date source.

If the filename does not contain a valid timestamp, an associated sidecar date
may be used instead.

Standalone `.xmp` and `.xml` files encountered during scanning are skipped;
they serve only as supporting metadata for media files.

### Metadata Written

For supported image formats, DateFrame writes:

```text
EXIF:DateTimeOriginal
EXIF:CreateDate
XMP:CreateDate
```

When an offset is available for an image, it also writes:

```text
EXIF:OffsetTime
EXIF:OffsetTimeOriginal
EXIF:OffsetTimeDigitized
```

For supported video formats, it writes:

```text
QuickTime:CreateDate
QuickTime:TrackCreateDate
QuickTime:MediaCreateDate
XMP:CreateDate
```

For formats that are not written in-place (`.avi`, `.mpg`, `.mpeg`), it writes
an XMP sidecar instead:

```text
XMP:DateTimeOriginal
XMP:CreateDate
XMP:ModifyDate
```

### Safe Operation Options

`--dry-run` records what would be done without changing metadata.

`--if-missing` asks ExifTool to write only missing tags by using ExifTool's
`-wm cg` write mode. DateFrame does not first read all existing tags and make
a per-file skip decision. The decision is made per tag by ExifTool.

This means `--if-missing` can still add missing companion tags. For example,
if a photo already has `EXIF:DateTimeOriginal` but lacks `XMP:CreateDate`,
DateFrame may leave the EXIF tag untouched while adding the missing XMP tag.
Use this when existing correct date tags should be retained but incomplete
metadata can be filled in.

Without `--if-missing`, matching date tags may be overwritten.

`--set-filetime` additionally updates filesystem create and modification
timestamps to the selected date.

### CSV Fields

The `write-dates` CSV records:

```text
source, date, date_offset, date_source, metadata_ok, write_target, error
```

`write_target` identifies whether data was written into the media, into an XMP
sidecar, or only evaluated under dry-run.

### Selected Options

| Option | Function |
| --- | --- |
| `--resume-csv PATH` | Continue an earlier metadata-writing run |
| `--input-txt` | Process paths listed in a text file |
| `-r`, `--recursive` | Process subfolders |
| `--dry-run` | Do not change files |
| `--if-missing` | Only fill tags that are empty |
| `--set-filetime` | Also set filesystem timestamps |
| `--exiftool PATH` | Select ExifTool binary |
| `--timeout N` | Limit ExifTool time per media file |
| `--checkpoint-seconds N` | Periodically write a resume checkpoint |
| `--log-path PATH` | Choose log directory |

## `dateframe inspect`

### Purpose

`inspect` displays the metadata visible through DateFrame's available readers.
It does not change the file and does not write processing logs.

Example:

```bash
dateframe inspect "/path/to/IMG_1234.JPG"
```

### Default Readers

For image files, default inspection runs:

```text
Wand
Pillow
Windows filesystem modified time (on Windows)
Associated sidecars
```

For video files, default inspection runs:

```text
FFMPEG
Windows filesystem modified time (on Windows)
Associated sidecars
```

This avoids waiting on image libraries for video files and avoids probing
ordinary images through ffmpeg without an explicit request.

### Selecting Readers

Supplying reader flags runs exactly those selected readers, even if the reader
is unusual for the file type:

```bash
dateframe inspect --ffmpeg video.mov
dateframe inspect --wand image.heic
dateframe inspect --windows --sidecars image.jpg
```

Available selection flags:

```text
--wand
--pillow
--ffmpeg
--windows
--sidecars
```

Each selected reader is limited independently by `--timeout` (default: 30
seconds). A timed-out reader is terminated and inspection continues.

### Interpreting Results

Metadata displayed by `inspect` is raw diagnostic information. Not every date
is a capture date:

| Displayed data | Meaning |
| --- | --- |
| EXIF `DateTimeOriginal` | Usually a strong photo capture-date source |
| QuickTime creation metadata | Often useful for video capture, but timezone interpretation may matter |
| Wand `date:create` / `date:modify` | May be filesystem date metadata, often displayed with UTC offset |
| Windows Modified Date | Local filesystem modification timestamp, not automatically a capture date |
| Sidecar selected date | DateFrame's parsed date from a supported XMP/XML companion |

Use this command to diagnose which date sources exist before choosing a repair
workflow; do not assume every printed timestamp should be used as a filename.

## `dateframe extensions`

### Purpose

`extensions` inventories file types before processing a media library. It does
not read metadata and does not modify files.

Examples:

```bash
dateframe extensions "/path/to/export"
dateframe extensions --recursive "/path/to/export"
dateframe extensions --input-txt files.txt
```

The output is a count grouped by lowercase extension. Files without an
extension are reported as `[no extension]`.

### Options

| Option | Function |
| --- | --- |
| `-r`, `--recursive` | Include files in subfolders |
| `--input-txt` | Count only files listed in a text file |

## Recommended Workflows

### iCloud Photos For Windows

1. Keep placeholders undownloaded where possible.
2. Run `dateframe import-icloud` into a separate destination.
3. Expect occasional cloud hydration timeouts and resume from logs instead of
   treating them as fatal library failures.
4. Resume failures from the produced CSV rather than starting from newly
   hydrated sources.
5. Inspect representative output files and the CSV `date_source` values.
6. If iCloud exposes files with misleading extensions, such as `.PNG` files
   containing JPEG data, DateFrame corrects the destination extension during
   copy or metadata retry.

### iCloud Web Export With Live Photos

1. Download original export batches, preserving image and `.MOV` components.
2. Run `dateframe rename --copy` on a sample first.
3. Confirm Live Photo pair counts and sidecar behavior in logs.
4. Process the remaining export once results are verified.

iCloud Web exports can preserve Live Photo pairs better than iCloud Photos for
Windows, but large libraries may require multiple batches and some exported
files may still lack reliable embedded capture metadata.

### Metadata Repair After Renaming

1. Run `dateframe write-dates --dry-run` on representative files.
2. Inspect generated logs and intended dates.
3. Run without `--dry-run` after verification.
4. Use `--if-missing` only when existing correct date tags should be retained.

## External Requirements

| Component | Required for |
| --- | --- |
| ExifTool | `import-icloud` metadata writing, `write-dates`, Live Photo confirmation in `rename` |
| ffmpeg | Video metadata reading in `rename` and `inspect` |
| ImageMagick/Wand | Image metadata reading in `rename` and `inspect` |
| `pywin32` | Windows Shell date access for `import-icloud` |

## Data Safety

Media operations can affect filenames, locations, and metadata. Before a large
run:

1. Keep an independent backup.
2. Prefer `--copy` over `--move` until results are verified.
3. Run against a representative sample.
4. Review CSV and TXT logs.
5. Redact local paths, metadata, and location tags before sharing logs or
   inspection output publicly.
