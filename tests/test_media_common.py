import datetime

from media_tools.media_common import (
    correctedMediaExtension,
    dateValueToDisplay,
    dateValueToFilename,
    dtFromFilename,
    effectiveCommandPrefix,
    inDateRange,
    iterFiles,
    parseOptionalDateRange,
    sidecarPathFor,
)


def testDateRangeIncludesWholeToDate():
    fromDate, toDate = parseOptionalDateRange("2026-03-02", "2026-03-04")

    assert inDateRange(datetime.datetime(2026, 3, 2, 0, 0, 0), fromDate, toDate)
    assert inDateRange(datetime.datetime(2026, 3, 4, 23, 59, 59), fromDate, toDate)
    assert not inDateRange(datetime.datetime(2026, 3, 5, 0, 0, 0), fromDate, toDate)


def testFilenameAndSidecarHelpersUseMediaFilename():
    assert dtFromFilename("2026-03-02T10-20-30.JPG") == "2026:03:02 10:20:30"
    assert dtFromFilename("IMG_0001.JPG") is None
    assert str(sidecarPathFor("2026-03-02T10-20-30.MP4", ".xmp")).endswith(
        "2026-03-02T10-20-30.MP4.xmp"
    )


def testDateValueConversionAcceptsFilenameAndDisplayForms():
    assert dateValueToDisplay("2026-03-02T10-20-30") == "2026-03-02 10:20:30"
    assert dateValueToDisplay("2026-03-02 10:20:30") == "2026-03-02 10:20:30"
    assert dateValueToFilename("2026-03-02 10:20:30") == "2026-03-02T10-20-30"
    assert dateValueToFilename("2026-03-02T10-20-30") == "2026-03-02T10-20-30"


def testCorrectedMediaExtensionDetectsJpegContentInsidePng(tmp_path):
    mislabeled = tmp_path / "IMG_0001.PNG"
    mislabeled.write_bytes(b"\xff\xd8\xff\xe0fake jpeg bytes")

    assert correctedMediaExtension(mislabeled) == ".jpg"


def testTxtInputIgnoresCommentsAndInvalidPaths(tmp_path):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.mp4"
    first.touch()
    second.touch()
    inputTxt = tmp_path / "files.txt"
    inputTxt.write_text(
        f"# media selected for a retry\n{first}\n\n\"{second}\"\n{tmp_path / 'missing.jpg'}\n",
        encoding="utf-8",
    )

    assert iterFiles(inputTxt, recursive=False, inputTxt=True) == [first.resolve(), second.resolve()]


def testEffectiveCommandPrefixAlwaysUsesPublicCli(monkeypatch):
    assert effectiveCommandPrefix("rename_media.py", "rename") == ["dateframe", "rename"]

    monkeypatch.setenv("DATEFRAME_SUBCOMMAND", "rename")

    assert effectiveCommandPrefix("rename_media.py", "rename") == ["dateframe", "rename"]
