#!/usr/bin/env python3
# capture_dates.py

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from media_tools.media_common import sidecarPathFor


DATETIME_RE = re.compile(
    r"(?P<year>\d{4})[-:](?P<month>\d{2})[-:](?P<day>\d{2})"
    r"[T\s]"
    r"(?P<hour>\d{2})[:-](?P<minute>\d{2})[:-](?P<second>\d{2})"
    r"(?:\.\d+)?"
    r"(?:\s*(?P<offset>Z|[+-]\d{2}:?\d{2}))?"
)

XMP_DATE_PRIORITY = [
    "subsecdatetimeoriginal",
    "datetimeoriginal",
    "subseccreatedate",
    "createdate",
    "creationdate",
    "mediacreatedate",
    "subsecmediacreatedate",
    "datetimecreated",
    "datecreated",
]


@dataclass(frozen=True)
class CaptureDate:
    filenameValue: str
    exiftoolValue: str
    displayValue: str
    offset: str
    source: str


def normalizeOffset(offset):
    if not offset:
        return ""

    offset = offset.strip()

    if offset == "Z":
        return "+00:00"

    if re.match(r"^[+-]\d{2}:\d{2}$", offset):
        return offset

    if re.match(r"^[+-]\d{4}$", offset):
        return f"{offset[:3]}:{offset[3:]}"

    return ""


def parseCaptureDate(value, source):
    if value is None:
        return None

    match = DATETIME_RE.search(str(value).strip())

    if not match:
        return None

    parts = match.groupdict()
    filenameValue = (
        f"{parts['year']}-{parts['month']}-{parts['day']}"
        f"T{parts['hour']}-{parts['minute']}-{parts['second']}"
    )
    exiftoolValue = (
        f"{parts['year']}:{parts['month']}:{parts['day']}"
        f" {parts['hour']}:{parts['minute']}:{parts['second']}"
    )
    displayValue = (
        f"{parts['year']}-{parts['month']}-{parts['day']}"
        f" {parts['hour']}:{parts['minute']}:{parts['second']}"
    )

    return CaptureDate(
        filenameValue=filenameValue,
        exiftoolValue=exiftoolValue,
        displayValue=displayValue,
        offset=normalizeOffset(parts.get("offset")),
        source=source,
    )


def xmlLocalName(name):
    return str(name).split("}", 1)[-1].split(":", 1)[-1].lower()


def datePriority(name):
    local = xmlLocalName(name)

    for index, candidate in enumerate(XMP_DATE_PRIORITY):
        if local == candidate:
            return index

    if "date" in local or "time" in local:
        return len(XMP_DATE_PRIORITY)

    return len(XMP_DATE_PRIORITY) + 1


def captureDateFromXml(path):
    path = Path(path)
    candidates = []

    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None

    for elem in root.iter():
        elemName = xmlLocalName(elem.tag)
        elemPriority = datePriority(elemName)

        if elem.text:
            parsed = parseCaptureDate(elem.text, f"sidecar:{path.name}:{elemName}")

            if parsed:
                candidates.append((elemPriority, parsed))

        for attrName, attrValue in elem.attrib.items():
            attrLocalName = xmlLocalName(attrName)
            attrOwnPriority = datePriority(attrLocalName)
            attrPriority = min(elemPriority, attrOwnPriority)
            sourceName = elemName if elemPriority <= attrOwnPriority else attrLocalName
            parsed = parseCaptureDate(attrValue, f"sidecar:{path.name}:{sourceName}")

            if parsed:
                candidates.append((attrPriority, parsed))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])

    return candidates[0][1]


def associatedSidecarPaths(path):
    path = Path(path)
    candidates = [
        sidecarPathFor(path, ".xmp"),
        path.with_suffix(".xmp"),
    ]

    if path.parent.exists():
        regularSidecarNames = {candidate.name.casefold() for candidate in candidates}
        sonyPattern = re.compile(
            rf"^(?:{re.escape(path.stem)}M\d+|{re.escape(path.name)}\.M\d+)\.xml$",
            re.IGNORECASE,
        )

        for candidate in path.parent.iterdir():
            if not candidate.is_file():
                continue

            if candidate.name.casefold() in regularSidecarNames or sonyPattern.match(candidate.name):
                candidates.append(candidate)

    return candidates


def captureDateFromAssociatedSidecars(path, extraSidecars=None):
    seen = set()
    candidates = []

    for candidate in associatedSidecarPaths(path):
        key = str(candidate).casefold()

        if key in seen or not candidate.exists():
            continue

        seen.add(key)
        candidates.append(candidate)

    for candidate in extraSidecars or []:
        candidate = Path(candidate)
        key = str(candidate).casefold()

        if key not in seen and candidate.exists():
            seen.add(key)
            candidates.append(candidate)

    candidates.sort(key=lambda p: (p.suffix.lower() != ".xmp", p.name.lower()))

    for candidate in candidates:
        parsed = captureDateFromXml(candidate)

        if parsed:
            return parsed

    return None


def offsetTags(offset):
    if not offset:
        return []

    return [
        f"-EXIF:OffsetTime={offset}",
        f"-EXIF:OffsetTimeOriginal={offset}",
        f"-EXIF:OffsetTimeDigitized={offset}",
    ]


def exiftoolDateWithOffset(exiftoolValue, offset):
    return f"{exiftoolValue}{offset}" if offset else exiftoolValue
