#!/usr/bin/env python3
# windows_metadata.py

import datetime

from media_common import isVideo

try:
    import pythoncom
    from win32com.client import Dispatch
except ImportError:
    pythoncom = None
    Dispatch = None


WINDOWS_SHELL_AVAILABLE = pythoncom is not None and Dispatch is not None

DATE_COLUMNS_IMAGE = ["Date taken", "Media created", "Date acquired", "Content created"]
DATE_COLUMNS_VIDEO = ["Media created", "Date taken", "Date acquired", "Content created"]


def initializeCom():
    pythoncom.CoInitialize()


def uninitializeCom():
    pythoncom.CoUninitialize()


def cleanShellValue(value):
    if value is None:
        return ""

    value = str(value).strip()
    value = value.replace("\u200e", "").replace("\u200f", "")
    value = value.replace("\u202a", "").replace("\u202c", "")

    return value.strip()


def parseWindowsShellDate(value, dateOrder="dmy"):
    value = cleanShellValue(value)

    if not value:
        return None

    commonFormats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M",
    ]

    dmyFormats = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",
    ]

    mdyFormats = [
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
    ]

    formats = commonFormats + (dmyFormats + mdyFormats if dateOrder == "dmy" else mdyFormats + dmyFormats)

    for fmt in formats:
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def getShellNamespaceAndItem(path):
    if not WINDOWS_SHELL_AVAILABLE:
        return None, None

    shell = Dispatch("Shell.Application")
    namespace = shell.Namespace(str(path.parent))

    if namespace is None:
        return None, None

    item = namespace.ParseName(path.name)
    if item is None:
        return namespace, None

    return namespace, item


def getShellDate(path, dateOrder):
    namespace, item = getShellNamespaceAndItem(path)

    if namespace is None or item is None:
        return None, None

    wantedColumns = DATE_COLUMNS_VIDEO if isVideo(path) else DATE_COLUMNS_IMAGE

    for column in wantedColumns:
        for index in range(0, 400):
            columnName = cleanShellValue(namespace.GetDetailsOf(None, index))

            if columnName != column:
                continue

            value = cleanShellValue(namespace.GetDetailsOf(item, index))
            parsed = parseWindowsShellDate(value, dateOrder=dateOrder)

            if parsed:
                return parsed, column

    return None, None


def getAllShellMetadata(path):
    namespace, item = getShellNamespaceAndItem(path)

    if namespace is None or item is None:
        return {}

    metadata = {}

    for index in range(0, 400):
        columnName = cleanShellValue(namespace.GetDetailsOf(None, index))
        value = cleanShellValue(namespace.GetDetailsOf(item, index))

        if columnName and value:
            metadata[columnName] = value

    return metadata
