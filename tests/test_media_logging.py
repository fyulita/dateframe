from media_tools.media_logging import (
    completedIcloudSourcesFromRows,
    isCompletedCsvRow,
    loadResumeCopiedDestinations,
    pathKey,
)


def testSuccessfulRowsAndDefinitiveSkipsAreCompleted():
    assert isCompletedCsvRow({"copied_ok": "True", "metadata_ok": "True", "error": ""})
    assert isCompletedCsvRow({"copied_ok": "", "metadata_ok": "", "error": "not media"})
    assert isCompletedCsvRow({"copied_ok": "", "metadata_ok": "", "error": "no shell date"})


def testFailedOrPendingRowsAreNotCompleted():
    assert not isCompletedCsvRow({"copied_ok": "False", "metadata_ok": "", "error": "copy failed"})
    assert not isCompletedCsvRow({"copied_ok": "True", "metadata_ok": "False", "error": "metadata pending"})


def testOutsideDateRangeIsReevaluatedWhenResumeFilterChanges(tmp_path):
    source = tmp_path / "photo.jpg"
    rows = [
        {
            "source": str(source),
            "copied_ok": "",
            "metadata_ok": "",
            "error": "outside date range",
            "run_from_date": "2020-01-01",
            "run_to_date": "2020-12-31",
        }
    ]

    sameFilter = completedIcloudSourcesFromRows(rows, "2020-01-01", "2020-12-31")
    changedFilter = completedIcloudSourcesFromRows(rows, "2019-01-01", "2020-12-31")

    assert pathKey(source) in sameFilter
    assert pathKey(source) not in changedFilter


def testCopiedFileWithPendingMetadataCanResumeWithoutRecopying(tmp_path):
    source = tmp_path / "photo.jpg"
    destination = tmp_path / "renamed.jpg"
    rows = [
        {
            "source": str(source),
            "dest": str(destination),
            "copied_ok": "True",
            "metadata_ok": "False",
            "error": "metadata pending",
        }
    ]

    copied = loadResumeCopiedDestinations(rows)

    assert copied[pathKey(source)] == str(destination)
