import datetime
import os
import threading

import pytest

import copy_icloud
from media_tools.capture_dates import CaptureDate
from media_tools.copy_icloud_config import CopyOptions, ResumeState


@pytest.fixture(autouse=True)
def resetIcloudGlobals(monkeypatch):
    copy_icloud.stopEvent.clear()
    copy_icloud.reservedPaths.clear()
    copy_icloud.copySemaphore = threading.Semaphore(1)
    monkeypatch.setattr(copy_icloud, "WINDOWS_SHELL_AVAILABLE", True)
    monkeypatch.setattr(copy_icloud, "initializeCom", lambda: None)
    monkeypatch.setattr(copy_icloud, "uninitializeCom", lambda: None)
    monkeypatch.setattr(copy_icloud, "getAllShellMetadata", lambda path: {"Date taken": "2026-03-02 10:20:30"})
    yield
    copy_icloud.stopEvent.clear()
    copy_icloud.reservedPaths.clear()


def copyOptions(**overrides):
    values = {
        "recursive": False,
        "keepStructure": False,
        "inputTxt": False,
        "fromDate": None,
        "toDate": None,
        "dateOrder": "dmy",
        "exiftoolPath": "exiftool",
        "writeXmp": False,
        "noMetadata": False,
        "verify": False,
        "skipVideoMetadata": False,
        "quiet": True,
        "timeout": 90,
        "maxWorkers": 1,
        "copyRetries": 1,
        "copyRetryDelay": 0,
    }
    values.update(overrides)
    return CopyOptions(**values)


def testProcessOneCopiesMediaAndWritesMetadata(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "IMG_0001.JPG"
    source.write_bytes(b"icloud image")
    shellDate = datetime.datetime(2026, 3, 2, 10, 20, 30)
    written = []

    monkeypatch.setattr(copy_icloud, "getShellDate", lambda path, dateOrder: (shellDate, "Date taken"))

    def fakeWriteEmbeddedMetadata(**kwargs):
        written.append(kwargs["copiedPath"])
        return 0, ""

    monkeypatch.setattr(copy_icloud, "writeEmbeddedMetadata", fakeWriteEmbeddedMetadata)

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    copied = dest / "2026-03-02T10-20-30.jpg"
    assert copied.read_bytes() == b"icloud image"
    assert written == [copied]

    row = stats.getCsvRows()[0]
    assert row["source"] == str(source)
    assert row["dest"] == str(copied)
    assert row["copied_ok"] is True
    assert row["metadata_ok"] is True
    assert row["error"] == ""


def testCopyWithRetryStopsBeforeWaitingForNextAttemptWhenInterrupted(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    copied = tmp_path / "copied.jpg"
    source.write_bytes(b"image")
    attempts = []
    waits = []
    cloudTimeout = OSError("iCloud hydration timeout")
    cloudTimeout.winerror = 426

    def timeoutCopy(src, dest):
        attempts.append((src, dest))
        raise cloudTimeout

    def interruptedWait(seconds):
        waits.append(seconds)
        return True

    monkeypatch.setattr(copy_icloud.shutil, "copy2", timeoutCopy)
    monkeypatch.setattr(copy_icloud.stopEvent, "wait", interruptedWait)

    with pytest.raises(KeyboardInterrupt):
        copy_icloud.copyWithRetry(source, copied, copy_icloud.Stats(), retries=5, delay=30)

    assert attempts == [(source, copied)]
    assert waits == [30]


def testProcessOnePreservesEmbeddedSecondsMatchingShellMinute(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "IMG_0275.HEIC"
    source.write_bytes(b"live photo still")
    written = []

    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2026, 5, 25, 15, 36, 0), "Date taken"),
    )
    monkeypatch.setattr(
        copy_icloud,
        "getAllShellMetadata",
        lambda path: {"Date taken": "2026-05-25 15:36"},
    )
    monkeypatch.setattr(
        copy_icloud,
        "captureDateFromEmbeddedMedia",
        lambda *args, **kwargs: CaptureDate(
            filenameValue="2026-05-25T15-36-28",
            exiftoolValue="2026:05:25 15:36:28",
            displayValue="2026-05-25 15:36:28",
            offset="",
            source="embedded:DateTimeOriginal",
        ),
    )

    def fakeWriteEmbeddedMetadata(**kwargs):
        written.append(kwargs["metadata"])
        return 0, ""

    monkeypatch.setattr(copy_icloud, "writeEmbeddedMetadata", fakeWriteEmbeddedMetadata)

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    copied = dest / "2026-05-25T15-36-28.heic"
    assert copied.exists()
    assert written == [{"Date taken": "2026:05:25 15:36:28"}]
    assert stats.getCsvRows()[0]["date"] == "2026-05-25 15:36:28"


def testProcessOneDoesNotUseEmbeddedDateFromDifferentMinute(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "IMG_0001.HEIC"
    source.write_bytes(b"image")

    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2026, 5, 25, 15, 36, 0), "Date taken"),
    )
    monkeypatch.setattr(
        copy_icloud,
        "captureDateFromEmbeddedMedia",
        lambda *args, **kwargs: CaptureDate(
            filenameValue="2020-01-01T10-00-28",
            exiftoolValue="2020:01:01 10:00:28",
            displayValue="2020-01-01 10:00:28",
            offset="",
            source="embedded:DateTimeOriginal",
        ),
    )
    monkeypatch.setattr(copy_icloud, "writeEmbeddedMetadata", lambda **kwargs: (0, ""))

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    assert (dest / "2026-05-25T15-36-00.heic").exists()
    assert stats.getCsvRows()[0]["date"] == "2026-05-25 15:36:00"


def testProcessOneRefinesShellDateWithMatchingFilesystemSeconds(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "ICLOUD.JPG"
    source.write_bytes(b"image without exif date")
    timestamp = datetime.datetime(2024, 2, 10, 5, 22, 59).timestamp()
    os.utime(source, (timestamp, timestamp))
    written = []

    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2024, 2, 10, 5, 22, 0), "Date taken"),
    )
    monkeypatch.setattr(copy_icloud, "getAllShellMetadata", lambda path: {})
    monkeypatch.setattr(copy_icloud, "captureDateFromEmbeddedMedia", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        copy_icloud,
        "writeEmbeddedMetadata",
        lambda **kwargs: (written.append(kwargs["metadata"]) or (0, "")),
    )

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    assert (dest / "2024-02-10T05-22-59.jpg").exists()
    assert written == [{"Date taken": "2024:02:10 05:22:59"}]
    assert stats.getCsvRows()[0]["date"] == "2024-02-10 05:22:59"


def testProcessOneDoesNotUseFilesystemSecondsFromDifferentMinute(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "ICLOUD.JPG"
    source.write_bytes(b"image without exif date")
    timestamp = datetime.datetime(2024, 2, 10, 5, 23, 59).timestamp()
    os.utime(source, (timestamp, timestamp))
    written = []

    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2024, 2, 10, 5, 22, 0), "Date taken"),
    )
    monkeypatch.setattr(copy_icloud, "getAllShellMetadata", lambda path: {})
    monkeypatch.setattr(copy_icloud, "captureDateFromEmbeddedMedia", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        copy_icloud,
        "writeEmbeddedMetadata",
        lambda **kwargs: (written.append(kwargs["metadata"]) or (0, "")),
    )

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    assert (dest / "2024-02-10T05-22-00.jpg").exists()
    assert written == [{"Date taken": "2024:02:10 05:22:00"}]
    assert stats.getCsvRows()[0]["date"] == "2024-02-10 05:22:00"


def testProcessOneIgnoresInvalidEmbeddedVideoDateAndUsesShellDate(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "VID_0001.MP4"
    source.write_bytes(b"video")

    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2024, 3, 5, 19, 42, 0), "Media created"),
    )

    class Result:
        returncode = 0
        stdout = (
            '[{"CreateDate":"0000:00:00 00:00:00",'
            '"MediaCreateDate":"0000:00:00 00:00:00",'
            '"TrackCreateDate":"0000:00:00 00:00:00"}]'
        )
        stderr = ""

    monkeypatch.setattr("media_tools.capture_dates.subprocess.run", lambda *args, **kwargs: Result())
    written = []
    monkeypatch.setattr(
        copy_icloud,
        "writeEmbeddedMetadata",
        lambda **kwargs: (written.append(kwargs["metadata"]) or (0, "")),
    )

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    assert (dest / "2024-03-05T19-42-00.mp4").exists()
    row = stats.getCsvRows()[0]
    assert row["date"] == "2024-03-05 19:42:00"
    assert row["copied_ok"] is True
    assert row["metadata_ok"] is True
    assert row["error"] == ""
    assert written[0]["Media created"] == "2024:03:05 19:42:00"


def testProcessOneFallsBackToEmbeddedDateWithoutShellDate(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "IMG_0275.HEIC"
    source.write_bytes(b"live photo still")

    monkeypatch.setattr(copy_icloud, "getShellDate", lambda path, dateOrder: (None, None))
    monkeypatch.setattr(
        copy_icloud,
        "captureDateFromEmbeddedMedia",
        lambda *args, **kwargs: CaptureDate(
            filenameValue="2026-05-25T15-36-28",
            exiftoolValue="2026:05:25 15:36:28",
            displayValue="2026-05-25 15:36:28",
            offset="",
            source="embedded:DateTimeOriginal",
        ),
    )
    monkeypatch.setattr(copy_icloud, "writeEmbeddedMetadata", lambda **kwargs: (0, ""))

    stats = copy_icloud.Stats()
    copy_icloud.processOne(source, src, "folder", dest, copyOptions(), stats)

    assert (dest / "2026-05-25T15-36-28.heic").exists()
    assert stats.getCsvRows()[0]["date"] == "2026-05-25 15:36:28"
    assert stats.getCsvRows()[0]["error"] == ""


def testProcessOneOutsideDateRangeDoesNotCopy(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "IMG_0001.JPG"
    source.touch()
    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2026, 3, 2, 10, 20, 30), "Date taken"),
    )

    stats = copy_icloud.Stats()
    copy_icloud.processOne(
        source,
        src,
        "folder",
        dest,
        copyOptions(fromDate=datetime.datetime(2026, 3, 3)),
        stats,
    )

    row = stats.getCsvRows()[0]
    assert not dest.exists()
    assert row["copied_ok"] == ""
    assert row["metadata_ok"] == ""
    assert row["error"] == "outside date range"


def testMetadataRetryUsesExistingCopyWithoutCopyingAgain(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    source = src / "IMG_0001.JPG"
    source.write_bytes(b"source")
    copied = dest / "2026-03-02T10-20-30.jpg"
    copied.write_bytes(b"existing copy")
    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: (datetime.datetime(2026, 3, 2, 10, 20, 30), "Date taken"),
    )
    monkeypatch.setattr(
        copy_icloud,
        "copyWithRetry",
        lambda *args, **kwargs: pytest.fail("metadata retry must not copy source again"),
    )
    monkeypatch.setattr(copy_icloud, "writeEmbeddedMetadata", lambda **kwargs: (0, ""))

    stats = copy_icloud.Stats()
    copy_icloud.processOne(
        source,
        src,
        "folder",
        dest,
        copyOptions(),
        stats,
        resumeCopiedPath=str(copied),
    )

    row = stats.getCsvRows()[0]
    assert copied.read_bytes() == b"existing copy"
    assert row["dest"] == str(copied)
    assert row["copied_ok"] is True
    assert row["metadata_ok"] is True


def testPendingCopyUsesDateSavedBeforeIcloudFileWasDownloaded(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    source = src / "OFFLINE.JPG"
    source.write_bytes(b"downloaded image")
    written = []

    monkeypatch.setattr(
        copy_icloud,
        "getShellDate",
        lambda path, dateOrder: pytest.fail("resume must not replace the saved iCloud date"),
    )
    monkeypatch.setattr(copy_icloud, "getAllShellMetadata", lambda path: {})

    def fakeWriteEmbeddedMetadata(**kwargs):
        written.append(kwargs["metadata"])
        return 0, ""

    monkeypatch.setattr(copy_icloud, "writeEmbeddedMetadata", fakeWriteEmbeddedMetadata)

    stats = copy_icloud.Stats()
    copy_icloud.processOne(
        source,
        src,
        "folder",
        dest,
        copyOptions(),
        stats,
        resumeDate="2021-02-19 16:23:00",
    )

    copied = dest / "2021-02-19T16-23-00.jpg"
    assert copied.exists()
    assert written == [{"Date taken": "2021:02:19 16:23:00"}]
    assert stats.getCsvRows()[0]["date"] == "2021-02-19 16:23:00"


def testCopyIcloudMediaSkipsCompletedResumeSources(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    completed = src / "DONE.JPG"
    pending = src / "PENDING.JPG"
    completed.touch()
    pending.touch()
    visited = []

    monkeypatch.setattr(
        copy_icloud,
        "processOne",
        lambda path, **kwargs: visited.append(path),
    )

    stats = copy_icloud.Stats()
    copy_icloud.copyIcloudMedia(
        src,
        dest,
        copyOptions(),
        ResumeState(completedSources={copy_icloud.pathKey(completed)}, copiedDestinations={}),
        stats,
    )

    assert visited == [pending]
    assert stats.summary()["skipped_resume_completed"] == 1
