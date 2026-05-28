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


def renameArgs(**overrides):
    values = dict(
        copy=True,
        recursive=False,
        input_txt=False,
        keep_structure=False,
        windows=False,
        live_photos=True,
        exiftool="exiftool",
        timeout=30,
        quiet=True,
        workers=1,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


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
    assert rows[0]["date"] == "2026-03-02 10:20:30"
    assert rows[0]["date_source"] == "pillow:DateTimeOriginal"
    assert rows[0]["processed_ok"] is True


def testRenameMediaCorrectsPngExtensionWhenContentIsJpeg(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.PNG"
    image.write_bytes(b"\xff\xd8\xff\xe1fake jpeg bytes")

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "Pillow:DateTimeOriginal"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    renamed = dest / "2026-03-02T10-20-30.jpg"
    assert renamed.read_bytes() == b"\xff\xd8\xff\xe1fake jpeg bytes"
    assert not (dest / "2026-03-02T10-20-30.PNG").exists()

    row = stats.getCsvRows()[0]
    assert row["dest"] == str(renamed)
    assert row["date"] == "2026-03-02 10:20:30"
    assert row["processed_ok"] is True


def testImageDateUsesReadableDateSource(monkeypatch):
    monkeypatch.setattr(rename_media, "useWand", lambda path, tag: "2026-03-02T10-20-30")

    dateValue, dateSource = rename_media.imageDate("image.jpg", useWindows=False)

    assert dateValue == "2026-03-02T10-20-30"
    assert dateSource == "Wand:photoshop:DateCreated"


def testImageDateDoesNotUseWandFilesystemModifyDate(monkeypatch):
    def fakeUseWand(path, tag):
        return "2026-03-02T10-20-30" if tag == "date:modify" else None

    monkeypatch.setattr(rename_media, "useWand", fakeUseWand)
    monkeypatch.setattr(rename_media, "usePillow", lambda path, tag: None)

    dateValue, dateSource = rename_media.imageDate("image.jpg", useWindows=False)

    assert dateValue is None
    assert dateSource == ""


def testVideoDateUsesReadableDateSource(monkeypatch):
    monkeypatch.setattr(rename_media, "useFFMPEG", lambda path, tag: "2026-03-02T10-20-30")

    dateValue, dateSource = rename_media.videoDate("video.mov", useWindows=False)

    assert dateValue == "2026-03-02T10-20-30"
    assert dateSource == "FFMPEG:creation_time"


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


def testRenameMediaKeepsLivePhotoPairOnImageDate(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.HEIC"
    video = src / "DIFFERENT_NAME.MOV"
    image.write_bytes(b"live photo image")
    video.write_bytes(b"live photo video")

    monkeypatch.setattr(
        rename_media,
        "readLivePhotoIdentifiers",
        lambda paths, args: {pathKey(image): "PAIR-1", pathKey(video): "PAIR-1"},
    )
    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "wand:exif:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30.HEIC").exists()
    assert (dest / "2026-03-02T10-20-30.MOV").exists()
    rows = {row["source"]: row for row in stats.getCsvRows()}
    assert rows[str(image)]["pair_type"] == "live_photo"
    assert rows[str(image)]["pair_id"] == "PAIR-1"
    assert rows[str(image)]["paired_source"] == str(video)
    assert rows[str(video)]["pair_type"] == "live_photo"
    assert rows[str(video)]["paired_source"] == str(image)
    assert rows[str(video)]["date_source"] == "live-photo:wand:exif:DateTimeOriginal"
    assert stats.summary()["live_photo_pairs"] == 1


def testLivePhotoIdentifierAcceptsAppleAndQuickTimeExiftoolGroups():
    assert rename_media.livePhotoIdentifierFromMetadata(
        {"MakerNotes:ContentIdentifier": "PAIR-1"},
        True,
    ) == "PAIR-1"
    assert rename_media.livePhotoIdentifierFromMetadata(
        {"XAttr:MediaGroupUUID": "PAIR-2"},
        True,
    ) == "PAIR-2"
    assert rename_media.livePhotoIdentifierFromMetadata(
        {"Keys:ContentIdentifier": "PAIR-1"},
        False,
    ) == "PAIR-1"


def testRenameMediaAssignsSameStemSidecarOnlyToLivePhotoImage(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.JPG"
    video = src / "IMG_0001.MOV"
    sidecar = src / "IMG_0001.XMP"
    image.write_bytes(b"live photo image")
    video.write_bytes(b"live photo video")
    sidecar.write_text("<xmpmeta />", encoding="utf-8")

    monkeypatch.setattr(
        rename_media,
        "readLivePhotoIdentifiers",
        lambda paths, args: {pathKey(image): "PAIR-1", pathKey(video): "PAIR-1"},
    )
    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30.JPG.XMP").exists()
    assert not (dest / "2026-03-02T10-20-30.MOV.XMP").exists()
    assert sum(1 for row in stats.getCsvRows() if row["media_type"] == "sidecar") == 1


def testRenameMediaKeepsLivePhotoBasenameTogetherOnCollision(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.HEIC"
    video = src / "IMG_0001.MOV"
    image.write_bytes(b"new image")
    video.write_bytes(b"new video")
    (dest / "2026-03-02T10-20-30.HEIC").write_bytes(b"existing image")

    monkeypatch.setattr(
        rename_media,
        "readLivePhotoIdentifiers",
        lambda paths, args: {pathKey(image): "PAIR-1", pathKey(video): "PAIR-1"},
    )
    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "wand:exif:DateTimeOriginal"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30_(1).HEIC").exists()
    assert (dest / "2026-03-02T10-20-30_(1).MOV").exists()


def testRenameMediaDoesNotPairSameStemMp4(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.JPG"
    video = src / "IMG_0001.MP4"
    image.write_bytes(b"image")
    video.write_bytes(b"video")

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30.JPG").exists()
    assert (dest / "2026-03-02T10-20-33.MP4").exists()
    assert all(not row["pair_type"] for row in stats.getCsvRows())


def testRenameMediaDoesNotPairSameStemMovWithoutMatchingIdentifier(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.JPG"
    video = src / "IMG_0001.MOV"
    image.write_bytes(b"image")
    video.write_bytes(b"video")

    monkeypatch.setattr(
        rename_media,
        "readLivePhotoIdentifiers",
        lambda paths, args: {pathKey(image): "IMAGE-ID", pathKey(video): "VIDEO-ID"},
    )
    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30.JPG").exists()
    assert (dest / "2026-03-02T10-20-33.MOV").exists()
    assert all(not row["pair_type"] for row in stats.getCsvRows())


def testRenameMediaDoesNotPairVideoThumbnailAsLivePhoto(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "CLIP_0001.THM").write_bytes(b"thumbnail")
    (src / "CLIP_0001.MOV").write_bytes(b"video")

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30.THM").exists()
    assert (dest / "2026-03-02T10-20-33.MOV").exists()
    assert all(not row["pair_type"] for row in stats.getCsvRows())


def testRenameMediaCanDisableLivePhotoPairing(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "IMG_0001.JPG").write_bytes(b"image")
    (src / "IMG_0001.MOV").write_bytes(b"video")

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "pillow:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(live_photos=False), set(), [], stats)

    assert (dest / "2026-03-02T10-20-30.JPG").exists()
    assert (dest / "2026-03-02T10-20-33.MOV").exists()
    assert all(not row["pair_type"] for row in stats.getCsvRows())


def testRenameMediaResumeMovedLivePhotoVideoFromCompletedImageRow(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.HEIC"
    video = src / "IMG_0001.MOV"
    video.write_bytes(b"remaining video")
    previousImageDest = dest / "2026-03-02T10-20-30.HEIC"
    previousImageDest.write_bytes(b"already moved image")
    resumeRows = [
        {
            "source": str(image),
            "dest": str(previousImageDest),
            "date": "2026-03-02T10-20-30",
            "date_offset": "",
            "media_type": "image",
            "action": "move",
            "date_source": "wand:exif:DateTimeOriginal",
            "pair_type": "live_photo",
            "pair_id": "PAIR-1",
            "paired_source": str(video),
            "processed_ok": "True",
            "error": "",
        }
    ]
    monkeypatch.setattr(
        rename_media,
        "videoDate",
        lambda path, useWindows: ("2026-03-02T10-20-33", "ffmpeg:creation_time"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(copy=False), {pathKey(image)}, resumeRows, stats)

    assert not video.exists()
    assert (dest / "2026-03-02T10-20-30.MOV").exists()
    assert stats.getCsvRows()[0]["paired_source"] == str(image)
    assert stats.getCsvRows()[0]["pair_id"] == "PAIR-1"
    assert stats.getCsvRows()[0]["date_source"] == "live-photo:wand:exif:DateTimeOriginal"


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


def testRenameMediaResumeRepairsPendingMislabeledPngOutput(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    image = src / "IMG_0001.PNG"
    image.write_bytes(b"source should not be recopied")
    pending = dest / "2026-03-02T10-20-30.png"
    pending.write_bytes(b"\xff\xd8\xff\xe1pending jpeg bytes")
    resumeRows = [
        {
            "source": str(image),
            "dest": str(pending),
            "date": "2026-03-02 10:20:30",
            "date_offset": "",
            "media_type": "image",
            "action": "copy",
            "date_source": "Pillow:DateTimeOriginal",
            "pair_type": "",
            "pair_id": "",
            "paired_source": "",
            "processed_ok": "False",
            "error": "previous failure",
        }
    ]

    monkeypatch.setattr(
        rename_media,
        "imageDate",
        lambda path, useWindows: ("2026-03-02T10-20-30", "Pillow:DateTimeOriginal"),
    )
    monkeypatch.setattr(
        rename_media,
        "copyOrMove",
        lambda *args, **kwargs: pytest.fail("pending output should be reused"),
    )

    stats = rename_media.Stats()
    rename_media.renameMedia(src, dest, renameArgs(), set(), resumeRows, stats)

    repaired = dest / "2026-03-02T10-20-30.jpg"
    assert not pending.exists()
    assert repaired.read_bytes() == b"\xff\xd8\xff\xe1pending jpeg bytes"
    assert stats.getCsvRows()[0]["dest"] == str(repaired)
    assert stats.getCsvRows()[0]["processed_ok"] is True


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
    assert firstCsv.name.startswith("dateframe_rename_")
    firstTxt = next(logDir.glob("*.txt"))
    assert "effective_command: dateframe rename" in firstTxt.read_text(encoding="utf-8")
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
