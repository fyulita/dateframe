#!/usr/bin/env python3
# metadata_writer.py

import html
import re
import subprocess
import sys
from pathlib import Path

from media_common import (
    UNSUPPORTED_EMBED_WRITE,
    datetimeToExiftool,
    isImage,
    isVideo,
    runExiftool,
)
from windows_metadata import cleanShellValue, parseWindowsShellDate


SHELL_TO_EXIFTOOL_IMAGE = {
    "Date taken": ["EXIF:DateTimeOriginal", "EXIF:CreateDate", "XMP:DateTimeOriginal", "XMP:CreateDate"],
    "Camera maker": ["EXIF:Make", "XMP:Make"],
    "Camera model": ["EXIF:Model", "XMP:Model"],
    "Authors": ["XMP:Creator"],
    "Title": ["XMP:Title"],
    "Subject": ["XMP:Subject"],
    "Tags": ["XMP:Subject"],
    "Comments": ["EXIF:UserComment", "XMP:Description"],
}

SHELL_TO_EXIFTOOL_VIDEO = {
    "Date taken": ["QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate", "XMP:CreateDate"],
    "Media created": ["QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate", "XMP:CreateDate"],
    "Camera maker": ["Keys:Make", "XMP:Make"],
    "Camera model": ["Keys:Model", "XMP:Model"],
    "Title": ["XMP:Title"],
    "Subject": ["XMP:Subject"],
    "Tags": ["XMP:Subject"],
    "Comments": ["XMP:Description"],
}

VERIFY_TAGS_IMAGE = ["EXIF:DateTimeOriginal", "EXIF:CreateDate", "XMP:DateTimeOriginal", "XMP:CreateDate"]
VERIFY_TAGS_VIDEO = ["QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate", "XMP:CreateDate"]
VERIFY_TAGS_XMP = ["XMP:DateTimeOriginal", "XMP:CreateDate", "XMP:ModifyDate"]


def sanitizeXmlName(name):
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    name = name.strip("_")

    if not name:
        name = "Field"

    if name[0].isdigit():
        name = "_" + name

    return name


def buildXmpSidecarContent(sourcePath, copiedPath, metadata):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '    <rdf:Description',
        '      xmlns:icloud="https://example.local/icloud-shell/1.0/"',
        '      rdf:about="">',
        f'      <icloud:OriginalPath>{html.escape(str(sourcePath))}</icloud:OriginalPath>',
        f'      <icloud:CopiedPath>{html.escape(str(copiedPath))}</icloud:CopiedPath>',
    ]

    for key in sorted(metadata.keys()):
        value = metadata[key]
        name = sanitizeXmlName(key)
        lines.append(f'      <icloud:{name}>{html.escape(str(value))}</icloud:{name}>')

    lines.extend([
        '    </rdf:Description>',
        '  </rdf:RDF>',
        '</x:xmpmeta>',
        "",
    ])

    return "\n".join(lines)


def writeXmpSidecar(sourcePath, copiedPath, metadata):
    xmpPath = Path(str(copiedPath) + ".xmp")
    content = buildXmpSidecarContent(sourcePath, copiedPath, metadata)

    with open(xmpPath, "w", encoding="utf-8") as f:
        f.write(content)

    return xmpPath


def shellDateToExiftoolValue(value, dateOrder):
    parsed = parseWindowsShellDate(value, dateOrder=dateOrder)

    if not parsed:
        return None

    return datetimeToExiftool(parsed)


def buildMappedTags(metadata, isImg, dateOrder):
    mapping = SHELL_TO_EXIFTOOL_IMAGE if isImg else SHELL_TO_EXIFTOOL_VIDEO
    tags = []

    def put(tag, value):
        if value:
            tags.append(f"-{tag}={value}")

    for shellKey, exiftoolTags in mapping.items():
        if shellKey not in metadata:
            continue

        value = cleanShellValue(metadata[shellKey])
        if not value:
            continue

        if "date" in shellKey.lower() or "created" in shellKey.lower():
            value = shellDateToExiftoolValue(value, dateOrder)
            if not value:
                continue

        for tag in exiftoolTags:
            put(tag, value)

    return tags


def writeEmbeddedMetadata(copiedPath, metadata, timeout, dateOrder, exiftoolPath, stats, printLock, stopEvent):
    path = Path(copiedPath)
    isImg = isImage(path)
    isVid = isVideo(path)

    if not (isImg or isVid):
        return 1, "unsupported media type"

    tags = buildMappedTags(metadata=metadata, isImg=isImg, dateOrder=dateOrder)

    if not tags:
        return 0, ""

    if path.suffix.lower() in UNSUPPORTED_EMBED_WRITE:
        argsList = tags + ["-o", "%d%f.xmp", str(path)]
    else:
        argsList = tags + [str(path)]

    return runExiftool(
        exiftoolPath=exiftoolPath,
        argsList=argsList,
        timeout=timeout,
        printLock=printLock,
        targetPath=path,
        stats=stats,
        stopEvent=stopEvent,
        printStdout=False,
        returnStderr=True,
    )


def readExiftoolTagValues(targetPath, tags, exiftoolPath, timeout, printLock):
    cmd = [exiftoolPath, "-s3"] + [f"-{tag}" for tag in tags] + [str(targetPath)]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, []

    if proc.returncode != 0:
        if printLock:
            with printLock:
                if proc.stderr:
                    sys.stderr.write(proc.stderr.strip() + "\n")
        return proc.returncode, []

    values = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return 0, values


def verifyWrittenDate(copiedPath, expectedDate, exiftoolPath, timeout, printLock):
    path = Path(copiedPath)

    if path.suffix.lower() in UNSUPPORTED_EMBED_WRITE:
        targetPath = path.with_suffix(".xmp")
        tags = VERIFY_TAGS_XMP
    elif isImage(path):
        targetPath = path
        tags = VERIFY_TAGS_IMAGE
    else:
        targetPath = path
        tags = VERIFY_TAGS_VIDEO

    if not targetPath.exists():
        return False, "verify target missing"

    rc, values = readExiftoolTagValues(targetPath, tags, exiftoolPath, timeout, printLock)

    if rc == 124:
        return False, "verify timeout"

    if rc != 0:
        return False, "verify exiftool error"

    expected = expectedDate.strip()

    for value in values:
        if value.strip() == expected:
            return True, ""

    return False, "expected date not found after write"
