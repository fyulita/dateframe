#!/usr/bin/env python3
# media_logging.py

import csv
import datetime
import os
from pathlib import Path

from media_common import resolvePath, saveSimpleLog, timestampedName


CSV_FIELDS = ["source", "dest", "date", "copied_ok", "metadata_ok", "error"]

RUN_CONTEXT_FIELDS = [
    "run_src",
    "run_dest",
    "run_resume_csv",
    "run_input_mode",
    "run_recursive",
    "run_keep_structure",
    "run_from_date",
    "run_to_date",
    "run_date_order",
    "run_exiftool",
    "run_write_xmp",
    "run_no_metadata",
    "run_verify",
    "run_skip_video_metadata",
    "run_quiet",
    "run_timeout",
    "run_workers",
    "run_copy_workers",
    "run_copy_retries",
    "run_copy_retry_delay",
    "run_interrupted",
]

SUMMARY_COUNT_GROUPS = [
    ["processed_images", "processed_videos", "copied_images", "copied_videos"],
    ["copy_errored_images", "copy_errored_videos"],
    ["metadata_written_images", "metadata_written_videos"],
    ["metadata_errored_images", "metadata_errored_videos"],
    [
        "metadata_skipped_disabled",
        "metadata_skipped_videos",
        "metadata_verified",
        "metadata_verify_failed",
        "xmp_sidecars_written",
        "xmp_sidecar_errors",
    ],
    [
        "skipped_not_media",
        "skipped_no_date",
        "skipped_outside_date_range",
        "skipped_resume_completed",
        "copy_retries",
        "exiftool_timeouts",
        "exiftool_errors",
        "tmp_removed",
    ],
]

SUMMARY_COUNT_KEYS = [
    key
    for group in SUMMARY_COUNT_GROUPS
    for key in group
]


def truthyCsvValue(value):
    return str(value).strip().lower() in {"true", "1", "yes"}


def falseyCsvValue(value):
    return str(value).strip().lower() in {"false", "0", "no"}


def metadataBool(value, default=False):
    text = str(value).strip().lower()

    if text in {"true", "1", "yes"}:
        return True

    if text in {"false", "0", "no"}:
        return False

    return default


def metadataInt(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def metadataFloat(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pathKey(path):
    return str(resolvePath(str(path))).casefold()


def isCompletedCsvRow(row, currentFromDate=None, currentToDate=None, enforceDateFilter=False):
    copiedOk = truthyCsvValue(row.get("copied_ok", ""))
    metadataOk = row.get("metadata_ok", "")
    error = (row.get("error") or "").strip()

    if copiedOk and not falseyCsvValue(metadataOk) and not error:
        return True

    if error in {"not media", "no shell date"}:
        return True

    if error == "outside date range":
        if not enforceDateFilter:
            return True

        return (
            (row.get("run_from_date") or "") == (currentFromDate or "")
            and (row.get("run_to_date") or "") == (currentToDate or "")
        )

    return False


def completedSourcesFromRows(rows, isCompletedFn=None, sourceField="source", **isCompletedKwargs):
    completed = set()
    isCompletedFn = isCompletedFn or isCompletedCsvRow

    for row in rows:
        source = (row.get(sourceField) or "").strip()

        if not source:
            continue

        if isCompletedFn(row, **isCompletedKwargs):
            completed.add(pathKey(source))

    return completed


def completedIcloudSourcesFromRows(rows, currentFromDate=None, currentToDate=None):
    return completedSourcesFromRows(
        rows,
        isCompletedFn=isCompletedCsvRow,
        currentFromDate=currentFromDate,
        currentToDate=currentToDate,
        enforceDateFilter=True,
    )


def loadResumeCopiedDestinations(rows):
    copied = {}

    for row in rows:
        source = (row.get("source") or "").strip()
        dest = (row.get("dest") or "").strip()

        if not source or not dest:
            continue

        if truthyCsvValue(row.get("copied_ok", "")) and falseyCsvValue(row.get("metadata_ok", "")):
            copied[pathKey(source)] = dest

    return copied


def loadResumeRows(csvPath, csvFields, runContextFields, sourceField="source", isCompletedFn=None):
    seen = set()
    context = {}
    rows = []

    with open(csvPath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or sourceField not in reader.fieldnames:
            raise ValueError(f"Resume CSV must contain a '{sourceField}' column.")

        for row in reader:
            if not context:
                context = {field: row.get(field, "") for field in runContextFields}

            source = (row.get(sourceField) or "").strip()

            if not source:
                continue

            rows.append({field: row.get(field, "") for field in csvFields + runContextFields})
            key = pathKey(source)
            seen.add(key)

    completed = completedSourcesFromRows(rows, isCompletedFn=isCompletedFn, sourceField=sourceField)
    return completed, seen, context, rows


def loadResumeSources(csvPath):
    return loadResumeRows(
        csvPath,
        csvFields=CSV_FIELDS,
        runContextFields=RUN_CONTEXT_FIELDS,
        isCompletedFn=isCompletedCsvRow,
    )


def mergedCsvRows(stats, sourceField="source"):
    currentRows = stats.getCsvRows()
    currentKeys = {pathKey(row[sourceField]) for row in currentRows if row.get(sourceField)}

    return [
        row
        for row in stats.getPreviousCsvRows()
        if row.get(sourceField) and pathKey(row[sourceField]) not in currentKeys
    ] + currentRows


def countCompletedRows(rows, isCompletedFn=None):
    isCompletedFn = isCompletedFn or isCompletedCsvRow
    return sum(1 for row in rows if isCompletedFn(row))


def logPaths(prefix, logDir, runStartedAt):
    baseStamp = runStartedAt.strftime("%Y-%m-%dT%H-%M-%S")
    runStamp = baseStamp
    counter = 1

    while True:
        logName = timestampedName(prefix, logDir=logDir, stamp=runStamp)
        csvLogName = timestampedName(prefix, ext="csv", logDir=logDir, stamp=runStamp)
        checkpointName = timestampedName(prefix, ext="csv", logDir=logDir, stamp=f"{runStamp}_checkpoint")

        if not Path(logName).exists() and not Path(csvLogName).exists() and not Path(checkpointName).exists():
            return logName, csvLogName, checkpointName

        runStamp = f"{baseStamp}_{counter}"
        counter += 1


def logPathsFromCheckpoint(checkpointPath):
    checkpointPath = Path(checkpointPath)
    stem = checkpointPath.stem

    if stem.endswith("_checkpoint"):
        stem = stem[:-len("_checkpoint")]

    return (
        str(checkpointPath.with_name(f"{stem}.txt")),
        str(checkpointPath.with_name(f"{stem}.csv")),
        str(checkpointPath),
    )


def withRunContext(row, runContext):
    outRow = dict(row)
    outRow.update(runContext)

    return outRow


def saveCsvLog(
    rows,
    csvPath,
    runContext,
    checkpointAt=None,
    atomic=False,
    csvFields=None,
    runContextFields=None,
):
    csvFields = csvFields or CSV_FIELDS
    runContextFields = runContextFields or RUN_CONTEXT_FIELDS
    fieldnames = csvFields + runContextFields

    if checkpointAt is not None:
        fieldnames = fieldnames + ["run_checkpoint_at"]

    writePath = Path(str(csvPath) + ".tmp") if atomic else csvPath
    checkpointValue = "" if checkpointAt is None else checkpointAt.isoformat(timespec="seconds")

    with open(writePath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            if any(field in row for field in runContextFields):
                outRow = dict(row)
            else:
                outRow = withRunContext(row, runContext)

            if checkpointAt is not None:
                outRow["run_checkpoint_at"] = checkpointValue

            writer.writerow(outRow)

    if atomic:
        os.replace(writePath, csvPath)

    if checkpointAt is None:
        print(f"CSV log saved to {csvPath}")
    else:
        print(f"Checkpoint CSV saved to {csvPath}")


def saveCheckpoint(stats, checkpointPath, runContext, csvFields=None, runContextFields=None, sourceField="source"):
    rows = mergedCsvRows(stats, sourceField=sourceField)

    if not rows:
        return False

    saveCsvLog(
        rows,
        checkpointPath,
        runContext,
        checkpointAt=datetime.datetime.now(),
        atomic=True,
        csvFields=csvFields,
        runContextFields=runContextFields,
    )
    return True


def runCheckpointLoop(
    stats,
    checkpointPath,
    runContext,
    checkpointSeconds,
    checkpointStopEvent,
    printLock=None,
    csvFields=None,
    runContextFields=None,
    sourceField="source",
):
    while not checkpointStopEvent.wait(checkpointSeconds):
        try:
            saveCheckpoint(
                stats,
                checkpointPath,
                runContext,
                csvFields=csvFields,
                runContextFields=runContextFields,
                sourceField=sourceField,
            )
        except Exception as e:
            if printLock:
                with printLock:
                    print(f"Warning: checkpoint CSV could not be saved: {e}")
            else:
                print(f"Warning: checkpoint CSV could not be saved: {e}")


def removeCheckpoint(checkpointPath, printLock=None):
    checkpointPath = Path(checkpointPath)

    for path in (checkpointPath, Path(str(checkpointPath) + ".tmp")):
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            message = f"Warning: checkpoint CSV could not be removed: {path}: {e}"

            if printLock:
                with printLock:
                    print(message)
            else:
                print(message)


def buildDefaultSummaryLines(data, countKeys=None):
    countKeys = countKeys or SUMMARY_COUNT_KEYS
    return [f"{key}: {data.get(key, 0)}" for key in countKeys]


def buildGroupedSummaryLines(data, countGroups=None):
    countGroups = countGroups or SUMMARY_COUNT_GROUPS
    lines = []

    for index, group in enumerate(countGroups):
        if index > 0:
            lines.append("")

        for key in group:
            lines.append(f"{key}: {data.get(key, 0)}")

    return lines


def saveRunLog(
    stats,
    logPrefix,
    logDir,
    runContext,
    runStartedAt,
    runEndedAt,
    interrupted,
    csvFields=None,
    runContextFields=None,
    isCompletedFn=None,
    sourceField="source",
    summaryLinesFn=None,
    summaryCountKeys=None,
    logName=None,
    csvLogName=None,
):
    if logName is None or csvLogName is None:
        logName, csvLogName, _checkpointName = logPaths(logPrefix, logDir, runStartedAt)

    csvFields = csvFields or CSV_FIELDS
    runContextFields = runContextFields or RUN_CONTEXT_FIELDS
    isCompletedFn = isCompletedFn or isCompletedCsvRow

    data = stats.summary()
    currentRows = stats.getCsvRows()
    previousRows = stats.getPreviousCsvRows()
    accumulatedRows = mergedCsvRows(stats, sourceField=sourceField)
    accumulatedCompleted = countCompletedRows(accumulatedRows, isCompletedFn=isCompletedFn)
    runContext = dict(runContext)
    runContext["run_interrupted"] = interrupted
    summaryLines = (
        summaryLinesFn(data, stats)
        if summaryLinesFn
        else (
            buildGroupedSummaryLines(data)
            if summaryCountKeys is None
            else buildDefaultSummaryLines(data, countKeys=summaryCountKeys)
        )
    )

    lines = [
        "Run:",
        f"started_at: {runStartedAt.isoformat(timespec='seconds')}",
        f"ended_at: {runEndedAt.isoformat(timespec='seconds')}",
        f"effective_command: {runContext.get('run_effective_command', '')}",
        f"interrupted: {interrupted}",
        f"resume_csv: {runContext.get('run_resume_csv', '')}",
        "",
        "Current run counts:",
        *summaryLines,
        "",
        "CSV accumulated counts:",
        f"previous_csv_rows: {len(previousRows)}",
        f"current_csv_rows: {len(currentRows)}",
        f"accumulated_csv_rows: {len(accumulatedRows)}",
        f"accumulated_completed_rows: {accumulatedCompleted}",
        f"accumulated_pending_rows: {len(accumulatedRows) - accumulatedCompleted}",
    ]

    saveSimpleLog(lines, logName)

    if accumulatedRows:
        saveCsvLog(
            accumulatedRows,
            csvLogName,
            runContext,
            csvFields=csvFields,
            runContextFields=runContextFields,
        )
    else:
        print("CSV log skipped because no rows were recorded.")
