import csv
from types import SimpleNamespace

import pytest

import write_dates
from media_tools.media_logging import pathKey


@pytest.fixture(autouse=True)
def resetStopEvent():
    write_dates.stopEvent.clear()
    yield
    write_dates.stopEvent.clear()


def writeArgs(**overrides):
    args = {
        "input_txt": False,
        "recursive": False,
        "dry_run": False,
        "if_missing": False,
        "set_filetime": False,
        "exiftool": "exiftool",
        "timeout": 90,
        "workers": 1,
        "verify": False,
        "quiet": True,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def testWriteDatesProcessesDatedFilenameUsingExiftool(tmp_path, monkeypatch):
    image = tmp_path / "2026-03-02T10-20-30.JPG"
    image.write_bytes(b"fake image")
    captured = {}

    def fakeExiftool(**kwargs):
        captured.update(kwargs)
        return 0, ""

    monkeypatch.setattr(write_dates, "runExiftool", fakeExiftool)

    stats = write_dates.Stats()
    write_dates.processOne(image, writeArgs(), stats)

    row = stats.getCsvRows()[0]
    assert row["source"] == str(image)
    assert row["date"] == "2026-03-02 10:20:30"
    assert row["date_source"] == "filename"
    assert row["metadata_ok"] is True
    assert row["write_target"] == "embedded"
    assert "-EXIF:DateTimeOriginal=2026:03:02 10:20:30" in captured["argsList"]


def testWriteDatesKeepsMatchingOffsetFromSidecar(tmp_path, monkeypatch):
    video = tmp_path / "2026-03-02T10-20-30.MP4"
    video.write_bytes(b"fake video")
    (tmp_path / "2026-03-02T10-20-30.MP4.xmp").write_text(
        "<xmpmeta><DateTimeOriginal>2026-03-02T10:20:30-03:00</DateTimeOriginal></xmpmeta>",
        encoding="utf-8",
    )
    captured = {}

    def fakeExiftool(**kwargs):
        captured.update(kwargs)
        return 0, ""

    monkeypatch.setattr(write_dates, "runExiftool", fakeExiftool)

    stats = write_dates.Stats()
    write_dates.processOne(video, writeArgs(), stats)

    row = stats.getCsvRows()[0]
    assert row["date_offset"] == "-03:00"
    assert row["date_source"].startswith("filename+sidecar:")
    assert "-XMP:CreateDate=2026:03:02 10:20:30-03:00" in captured["argsList"]


def testWriteDatesDryRunRecordsActionWithoutCompletingMetadata(tmp_path):
    image = tmp_path / "2026-03-02T10-20-30.JPG"
    image.touch()

    stats = write_dates.Stats()
    write_dates.processOne(image, writeArgs(dry_run=True), stats)

    row = stats.getCsvRows()[0]
    assert row["metadata_ok"] == ""
    assert row["write_target"] == "dry-run"
    assert stats.summary()["dry_run"] == 1


def testWriteDatesVerifyReadsBackWrittenMetadata(tmp_path, monkeypatch):
    image = tmp_path / "2026-03-02T10-20-30.JPG"
    image.write_bytes(b"fake image")
    verified = {}

    def fakeExiftool(**kwargs):
        return 0, ""

    def fakeVerify(path, expectedDate, exiftoolPath, timeout, printLock):
        verified.update(
            {
                "path": path,
                "expectedDate": expectedDate,
                "exiftoolPath": exiftoolPath,
                "timeout": timeout,
            }
        )
        return True, ""

    monkeypatch.setattr(write_dates, "runExiftool", fakeExiftool)
    monkeypatch.setattr(write_dates, "verifyWrittenDate", fakeVerify)

    stats = write_dates.Stats()
    write_dates.processOne(image, writeArgs(verify=True), stats)

    row = stats.getCsvRows()[0]
    assert verified["path"] == image
    assert verified["expectedDate"] == "2026:03:02 10:20:30"
    assert row["metadata_ok"] is True
    assert row["error"] == ""
    assert stats.summary()["metadata_verified"] == 1


def testWriteDatesVerifyFailureKeepsRowPending(tmp_path, monkeypatch):
    image = tmp_path / "2026-03-02T10-20-30.JPG"
    image.write_bytes(b"fake image")

    def fakeExiftool(**kwargs):
        return 0, ""

    def fakeVerify(path, expectedDate, exiftoolPath, timeout, printLock):
        return False, "expected date not found after write"

    monkeypatch.setattr(write_dates, "runExiftool", fakeExiftool)
    monkeypatch.setattr(write_dates, "verifyWrittenDate", fakeVerify)

    stats = write_dates.Stats()
    write_dates.processOne(image, writeArgs(verify=True), stats)

    row = stats.getCsvRows()[0]
    assert row["metadata_ok"] is False
    assert row["error"] == "expected date not found after write"
    assert not write_dates.isCompletedCsvRow(row)
    assert stats.summary()["metadata_verify_failed"] == 1


def testWriteDatesResumeSkipsCompletedSource(tmp_path, monkeypatch):
    completed = tmp_path / "2026-03-02T10-20-30.JPG"
    pending = tmp_path / "2026-03-02T10-20-31.JPG"
    completed.touch()
    pending.touch()
    processed = []

    def fakeExiftool(**kwargs):
        processed.append(kwargs["targetPath"])
        return 0, ""

    monkeypatch.setattr(write_dates, "runExiftool", fakeExiftool)

    stats = write_dates.Stats()
    write_dates.writeDates(
        tmp_path,
        writeArgs(),
        {pathKey(completed)},
        stats,
    )

    assert processed == [pending]
    assert stats.summary()["skipped_resume_completed"] == 1
    assert stats.getCsvRows()[0]["source"] == str(pending)


def testMainWritesFinalLogsAndResumesFromNewestCsv(tmp_path, monkeypatch):
    src = tmp_path / "src"
    logDir = tmp_path / "logs"
    src.mkdir()
    first = src / "2026-03-02T10-20-30.JPG"
    first.touch()
    processed = []

    def fakeExiftool(**kwargs):
        processed.append(kwargs["targetPath"])
        return 0, ""

    monkeypatch.setattr(write_dates, "runExiftool", fakeExiftool)
    monkeypatch.setattr(
        "sys.argv",
        [
            "write_dates.py",
            "--workers",
            "1",
            "--checkpoint-seconds",
            "0",
            "--quiet",
            "--log-path",
            str(logDir),
            str(src),
        ],
    )
    write_dates.main()

    firstCsv = next(logDir.glob("*.csv"))
    assert firstCsv.name.startswith("dateframe_write-dates_")
    firstTxt = next(logDir.glob("*.txt"))
    assert "effective_command: dateframe write-dates" in firstTxt.read_text(encoding="utf-8")
    with open(firstCsv, newline="", encoding="utf-8-sig") as f:
        firstRows = list(csv.DictReader(f))

    assert processed == [first]
    assert len(firstRows) == 1
    assert firstRows[0]["metadata_ok"] == "True"

    second = src / "2026-03-02T10-20-31.JPG"
    second.touch()
    processed.clear()
    monkeypatch.setattr(
        "sys.argv",
        [
            "write_dates.py",
            "--resume-csv",
            str(firstCsv),
            "--workers",
            "1",
            "--checkpoint-seconds",
            "0",
            "--quiet",
            "--log-path",
            str(logDir),
        ],
    )
    write_dates.main()

    csvFiles = sorted(logDir.glob("*.csv"), key=lambda path: path.stat().st_mtime_ns)
    newestCsv = csvFiles[-1]
    with open(newestCsv, newline="", encoding="utf-8-sig") as f:
        resumedRows = list(csv.DictReader(f))

    assert processed == [second]
    assert len(resumedRows) == 2
    assert {row["source"] for row in resumedRows} == {str(first), str(second)}
