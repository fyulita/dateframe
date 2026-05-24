import csv
import datetime
from pathlib import Path

import write_dates
from media_tools.media_logging import removeCheckpoint, saveCheckpoint, saveRunLog


def readCsv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def runContext(src):
    return {
        "run_src": str(src),
        "run_resume_csv": "",
        "run_input_mode": "folder",
        "run_recursive": False,
        "run_if_missing": False,
        "run_set_filetime": False,
        "run_dry_run": False,
        "run_exiftool": "exiftool",
        "run_timeout": 90,
        "run_workers": 1,
        "run_quiet": True,
        "run_interrupted": False,
        "run_effective_command": "python write_dates.py source",
    }


def testCheckpointContainsHistoryAndTimestampWhileFinalCsvDoesNot(tmp_path):
    source = tmp_path / "2026-03-02T10-20-30.JPG"
    previousSource = tmp_path / "2026-03-02T10-20-29.JPG"
    checkpoint = tmp_path / "write_dates_checkpoint.csv"
    finalCsv = tmp_path / "write_dates.csv"
    finalTxt = tmp_path / "write_dates.txt"
    stats = write_dates.Stats()
    stats.setPreviousCsvRows(
        [
            {
                "source": str(previousSource),
                "date": "2026-03-02 10:20:29",
                "date_offset": "",
                "date_source": "filename",
                "metadata_ok": True,
                "write_target": "embedded",
                "error": "",
            }
        ]
    )
    stats.addCsvRow(source, "2026-03-02 10:20:30", "", "filename", True, "embedded", "")
    context = runContext(tmp_path)

    assert saveCheckpoint(
        stats,
        checkpoint,
        context,
        csvFields=write_dates.CSV_FIELDS,
        runContextFields=write_dates.RUN_CONTEXT_FIELDS,
    )

    checkpointRows = readCsv(checkpoint)
    assert len(checkpointRows) == 2
    assert checkpointRows[0]["run_checkpoint_at"]
    assert checkpointRows[1]["run_checkpoint_at"]

    started = datetime.datetime(2026, 5, 23, 10, 0, 0)
    ended = datetime.datetime(2026, 5, 23, 10, 1, 0)
    saveRunLog(
        stats,
        logPrefix="write_dates",
        logDir=tmp_path,
        runContext=context,
        runStartedAt=started,
        runEndedAt=ended,
        interrupted=False,
        csvFields=write_dates.CSV_FIELDS,
        runContextFields=write_dates.RUN_CONTEXT_FIELDS,
        isCompletedFn=write_dates.isCompletedCsvRow,
        summaryCountKeys=write_dates.SUMMARY_COUNT_KEYS,
        logName=str(finalTxt),
        csvLogName=str(finalCsv),
    )
    removeCheckpoint(checkpoint)

    finalRows = readCsv(finalCsv)
    assert len(finalRows) == 2
    assert "run_checkpoint_at" not in finalRows[0]
    assert not checkpoint.exists()
    assert "accumulated_csv_rows: 2" in finalTxt.read_text(encoding="utf-8")


def testCurrentRowReplacesPreviousVersionInFinalCsv(tmp_path):
    source = tmp_path / "2026-03-02T10-20-30.JPG"
    finalCsv = tmp_path / "write_dates.csv"
    finalTxt = tmp_path / "write_dates.txt"
    stats = write_dates.Stats()
    stats.setPreviousCsvRows(
        [
            {
                "source": str(source),
                "date": "2026-03-02 10:20:30",
                "date_offset": "",
                "date_source": "filename",
                "metadata_ok": False,
                "write_target": "embedded",
                "error": "exiftool timeout",
            }
        ]
    )
    stats.addCsvRow(source, "2026-03-02 10:20:30", "", "filename", True, "embedded", "")

    saveRunLog(
        stats,
        logPrefix="write_dates",
        logDir=tmp_path,
        runContext=runContext(tmp_path),
        runStartedAt=datetime.datetime(2026, 5, 23, 10, 0, 0),
        runEndedAt=datetime.datetime(2026, 5, 23, 10, 1, 0),
        interrupted=False,
        csvFields=write_dates.CSV_FIELDS,
        runContextFields=write_dates.RUN_CONTEXT_FIELDS,
        isCompletedFn=write_dates.isCompletedCsvRow,
        summaryCountKeys=write_dates.SUMMARY_COUNT_KEYS,
        logName=str(finalTxt),
        csvLogName=str(finalCsv),
    )

    rows = readCsv(finalCsv)
    assert len(rows) == 1
    assert rows[0]["metadata_ok"] == "True"
    assert rows[0]["error"] == ""
