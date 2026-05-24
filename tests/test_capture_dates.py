from media_tools.capture_dates import (
    captureDateFromAssociatedSidecars,
    captureDateFromXml,
    exiftoolDateWithOffset,
    normalizeOffset,
    offsetTags,
    parseCaptureDate,
)


def testParseCaptureDatePreservesLocalTimeAndNormalizesOffset():
    parsed = parseCaptureDate("2026-03-02T10:20:30-0300", "xml:creationdate")

    assert parsed is not None
    assert parsed.filenameValue == "2026-03-02T10-20-30"
    assert parsed.exiftoolValue == "2026:03:02 10:20:30"
    assert parsed.displayValue == "2026-03-02 10:20:30"
    assert parsed.offset == "-03:00"
    assert parsed.source == "xml:creationdate"


def testParseCaptureDateSupportsUtcAndRejectsMissingDates():
    assert normalizeOffset("Z") == "+00:00"
    assert parseCaptureDate("no capture date", "invalid") is None


def testCaptureDateFromXmlUsesHigherPriorityOriginalDate(tmp_path):
    sidecar = tmp_path / "IMG.JPG.xmp"
    sidecar.write_text(
        """
        <xmpmeta>
          <CreateDate>2026-03-03T11:00:00-03:00</CreateDate>
          <DateTimeOriginal>2026-03-02T10:20:30-03:00</DateTimeOriginal>
        </xmpmeta>
        """,
        encoding="utf-8",
    )

    parsed = captureDateFromXml(sidecar)

    assert parsed is not None
    assert parsed.displayValue == "2026-03-02 10:20:30"
    assert parsed.offset == "-03:00"
    assert parsed.source.endswith(":datetimeoriginal")


def testAssociatedSidecarPrefersXmpOverSonyXml(tmp_path):
    media = tmp_path / "C0001.MP4"
    media.touch()
    (tmp_path / "C0001.MP4.XMP").write_text(
        "<xmpmeta><DateTimeOriginal>2026-03-02T10:20:30-03:00</DateTimeOriginal></xmpmeta>",
        encoding="utf-8",
    )
    (tmp_path / "C0001M01.XML").write_text(
        "<root><CreationDate>2026-03-04T12:00:00-03:00</CreationDate></root>",
        encoding="utf-8",
    )

    parsed = captureDateFromAssociatedSidecars(media)

    assert parsed is not None
    assert parsed.displayValue == "2026-03-02 10:20:30"
    assert parsed.source.casefold().startswith("sidecar:c0001.mp4.xmp:")


def testAssociatedSidecarFindsUppercaseSonyXml(tmp_path):
    media = tmp_path / "C0002.MP4"
    media.touch()
    (tmp_path / "C0002M01.XML").write_text(
        "<root><CreationDate>2026-03-04T12:00:00-03:00</CreationDate></root>",
        encoding="utf-8",
    )

    parsed = captureDateFromAssociatedSidecars(media)

    assert parsed is not None
    assert parsed.displayValue == "2026-03-04 12:00:00"


def testExiftoolOffsetHelpersKeepTimezoneSeparate():
    assert offsetTags("-03:00") == [
        "-EXIF:OffsetTime=-03:00",
        "-EXIF:OffsetTimeOriginal=-03:00",
        "-EXIF:OffsetTimeDigitized=-03:00",
    ]
    assert exiftoolDateWithOffset("2026:03:02 10:20:30", "-03:00") == "2026:03:02 10:20:30-03:00"
