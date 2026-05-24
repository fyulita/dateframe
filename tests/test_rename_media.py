import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

import rename_media
from media_tools.media_logging import pathKey


@pytest.fixture(autouse=True)
def resetStopEvent():
    rename_media.stopEvent.clear()
    yield
    rename_media.stopEvent.clear()


def renameArgs():
    return SimpleNamespace(
        copy=True,
        recursive=False,
        input_txt=False,
        keep_structure=False,
        windows=False,
        quiet=True,
        workers=1,
    )


def testRenameMediaCopiesImageUsingDetectedDate(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.JPG"
    image.write_bytes(b"fake image")

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    renamed = dest / "2026-03-02T10-20-30.JPG"
    assert renamed.read_bytes() == b"fake image"
    assert image.exists()

    rows = stats.getCsvRows()
    assert len(rows) == 1
    assert rows[0]["dest"] == str(renamed)
    assert rows[0]["date"] == "2026-03-02T10-20-30"
    assert rows[0]["date_source"] == "pillow:DateTimeOriginal"
    assert rows[0]["processed_ok"] is True


def testRenameMediaCopiesSonySidecarWithRenamedVideo(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    video = src / "C0001.MP4"
    xml = src / "C0001M01.XML"
    video.write_bytes(b"fake video")
    xml.write_text(
        "<root><CreationDate>2026-03-02T10:20:30-03:00</CreationDate></root>",
        encoding="utf-8",
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    renamedVideo = dest / "2026-03-02T10-20-30.MP4"
    renamedXml = dest / "2026-03-02T10-20-30.MP4.M01.XML"
    assert renamedVideo.exists()
    assert renamedXml.exists()

    rows = {row["media_type"]: row for row in stats.getCsvRows()}
    assert rows["video"]["date_offset"] == "-03:00"
    assert rows["video"]["processed_ok"] is True
    assert rows["sidecar"]["dest"] == str(renamedXml)
    assert rows["sidecar"]["processed_ok"] is True


def testRenameMediaResumeSkipsCompletedSource(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    completed = src / "DONE.JPG"
    pending = src / "PENDING.JPG"
    completed.write_bytes(b"already handled")
    pending.write_bytes(b"needs processing")

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(
        src,
        dest,
        renameArgs(),
        {pathKey(completed)},
        [],
        stats,
    )

    assert stats.summary()["skipped_resume_completed"] == 1
    assert len(stats.getCsvRows()) == 1
    assert stats.getCsvRows()[0]["source"] == str(pending)
    assert (dest / "2026-03-02T10-20-30.JPG").exists()


def testMainWritesLogsAndResumesFromGeneratedCsv(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    logDir = tmp_path / "logs"
    src.mkdir()
    first = src / "IMG_0001.JPG"
    first.write_bytes(b"first image")

    def fakeImageDate(path, useWindows):
        stem = Path(path).stem
        date = "2026-03-02T10-20-30" if stem == "IMG_0001" else "2026-03-02T10-20-31"
        return date, "pillow:DateTimeOriginal"

    monkeypatch.setattr(rename_media, "imageDate", fakeImageDate)
    monkeypatch.setattr(
        "sys.argv",
        [
            "rename_media.py",
            "--copy",
            "--workers",
            "1",
            "--checkpoint-seconds",
            "0",
            "--quiet",
            "--log-path",
            str(logDir),
            str(src),
            str(dest),
        ],
    )
    rename_media.main()

    firstCsv = next(logDir.glob("*.csv"))
    with open(firstCsv, newline="", encoding="utf-8-sig") as f:
        firstRows = list(csv.DictReader(f))

    assert len(firstRows) == 1
    assert firstRows[0]["processed_ok"] == "True"
    assert (dest / "2026-03-02T10-20-30.JPG").exists()

    second = src / "IMG_0002.JPG"
    second.write_bytes(b"second image")
    existingCsvs = set(logDir.glob("*.csv"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "rename_media.py",
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
    rename_media.main()

    newestCsv = (set(logDir.glob("*.csv")) - existingCsvs).pop()
    with open(newestCsv, newline="", encoding="utf-8-sig") as f:
        resumedRows = list(csv.DictReader(f))

    assert len(resumedRows) == 2
    assert {row["source"] for row in resumedRows} == {str(first), str(second)}
    assert (dest / "2026-03-02T10-20-31.JPG").exists()
