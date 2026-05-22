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


def completedSourcesFromRows(rows, currentFromDate=None, currentToDate=None):
    completed = set()

    for row in rows:
        source = (row.get("source") or "").strip()

        if not source:
            continue

        if isCompletedCsvRow(
            row,
            currentFromDate=currentFromDate,
            currentToDate=currentToDate,
            enforceDateFilter=True,
        ):
            completed.add(pathKey(source))

    return completed


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


def loadResumeSources(csvPath):
    seen = set()
    context = {}
    rows = []

    with open(csvPath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or "source" not in reader.fieldnames:
            raise ValueError("Resume CSV must contain a 'source' column.")

        for row in reader:
            if not context:
                context = {field: row.get(field, "") for field in RUN_CONTEXT_FIELDS}

            source = (row.get("source") or "").strip()

            if not source:
                continue

            rows.append({field: row.get(field, "") for field in CSV_FIELDS + RUN_CONTEXT_FIELDS})
            key = pathKey(source)
            seen.add(key)

    completed = completedSourcesFromRows(rows)
    return completed, seen, context, rows


def mergedCsvRows(stats):
    currentRows = stats.getCsvRows()
    currentKeys = {pathKey(row["source"]) for row in currentRows if row.get("source")}

    return [
        row
        for row in stats.getPreviousCsvRows()
        if row.get("source") and pathKey(row["source"]) not in currentKeys
    ] + currentRows


def countCompletedRows(rows):
    return sum(1 for row in rows if isCompletedCsvRow(row))


def logPaths(prefix, logDir, runStartedAt):
    runStamp = runStartedAt.strftime("%Y-%m-%dT%H-%M-%S")

    return (
        timestampedName(prefix, logDir=logDir, stamp=runStamp),
        timestampedName(prefix, ext="csv", logDir=logDir, stamp=runStamp),
        timestampedName(prefix, ext="csv", logDir=logDir, stamp=f"{runStamp}_checkpoint"),
    )


def withRunContext(row, runContext):
    outRow = dict(row)
    outRow.update(runContext)

    return outRow


def saveCsvLog(rows, csvPath, runContext, checkpointAt=None, atomic=False):
    fieldnames = CSV_FIELDS + RUN_CONTEXT_FIELDS

    if checkpointAt is not None:
        fieldnames = fieldnames + ["run_checkpoint_at"]

    writePath = Path(str(csvPath) + ".tmp") if atomic else csvPath
    checkpointValue = "" if checkpointAt is None else checkpointAt.isoformat(timespec="seconds")

    with open(writePath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            if any(field in row for field in RUN_CONTEXT_FIELDS):
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


def saveCheckpoint(stats, checkpointPath, runContext):
    rows = mergedCsvRows(stats)

    if not rows:
        return False

    saveCsvLog(rows, checkpointPath, runContext, checkpointAt=datetime.datetime.now(), atomic=True)
    return True


def runCheckpointLoop(stats, checkpointPath, runContext, checkpointSeconds, checkpointStopEvent, printLock=None):
    while not checkpointStopEvent.wait(checkpointSeconds):
        try:
            saveCheckpoint(stats, checkpointPath, runContext)
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


def saveRunLog(stats, logPrefix, logDir, runContext, runStartedAt, runEndedAt, interrupted):
    logName, csvLogName, _checkpointName = logPaths(logPrefix, logDir, runStartedAt)

    data = stats.summary()
    currentRows = stats.getCsvRows()
    previousRows = stats.getPreviousCsvRows()
    accumulatedRows = mergedCsvRows(stats)
    accumulatedCompleted = countCompletedRows(accumulatedRows)
    runContext = dict(runContext)
    runContext["run_interrupted"] = interrupted

    lines = [
        "Run:",
        f"started_at: {runStartedAt.isoformat(timespec='seconds')}",
        f"ended_at: {runEndedAt.isoformat(timespec='seconds')}",
        f"effective_command: {runContext.get('run_effective_command', '')}",
        f"interrupted: {interrupted}",
        f"resume_csv: {runContext.get('run_resume_csv', '')}",
        "",
        "Current run counts:",
        f"processed_images: {data.get('processed_images', 0)}",
        f"processed_videos: {data.get('processed_videos', 0)}",
        f"copied_images: {data.get('copied_images', 0)}",
        f"copied_videos: {data.get('copied_videos', 0)}",
        f"copy_errored_images: {data.get('copy_errored_images', 0)}",
        f"copy_errored_videos: {data.get('copy_errored_videos', 0)}",
        f"metadata_written_images: {data.get('metadata_written_images', 0)}",
        f"metadata_written_videos: {data.get('metadata_written_videos', 0)}",
        f"metadata_errored_images: {data.get('metadata_errored_images', 0)}",
        f"metadata_errored_videos: {data.get('metadata_errored_videos', 0)}",
        "",
        f"metadata_skipped_disabled: {data.get('metadata_skipped_disabled', 0)}",
        f"metadata_skipped_videos: {data.get('metadata_skipped_videos', 0)}",
        f"metadata_verified: {data.get('metadata_verified', 0)}",
        f"metadata_verify_failed: {data.get('metadata_verify_failed', 0)}",
        f"xmp_sidecars_written: {data.get('xmp_sidecars_written', 0)}",
        f"xmp_sidecar_errors: {data.get('xmp_sidecar_errors', 0)}",
        "",
        f"skipped_not_media: {data.get('skipped_not_media', 0)}",
        f"skipped_no_date: {data.get('skipped_no_date', 0)}",
        f"skipped_outside_date_range: {data.get('skipped_outside_date_range', 0)}",
        f"skipped_resume_completed: {data.get('skipped_resume_completed', 0)}",
        f"copy_retries: {data.get('copy_retries', 0)}",
        f"exiftool_timeouts: {data.get('exiftool_timeouts', 0)}",
        f"exiftool_errors: {data.get('exiftool_errors', 0)}",
        f"tmp_removed: {data.get('tmp_removed', 0)}",
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
        saveCsvLog(accumulatedRows, csvLogName, runContext)
    else:
        print("CSV log skipped because no rows were recorded.")
