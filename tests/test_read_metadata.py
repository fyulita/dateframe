import time
import datetime
from types import SimpleNamespace

import pytest

import read_metadata


def testRunTimeoutTerminatesBlockedReaderPromptly(capsys):
    started = time.monotonic()

    completed = read_metadata.runTimeout(time.sleep, 5, timeout=0.05)

    elapsed = time.monotonic() - started
    assert completed is False
    assert elapsed < 2
    assert "Timeout: sleep took more than 0.05 seconds." in capsys.readouterr().out


def testParseArgsRejectsNonPositiveTimeout(monkeypatch):
    monkeypatch.setattr("sys.argv", ["read_metadata.py", "sample.jpg", "--timeout", "0"])

    with pytest.raises(SystemExit):
        read_metadata.parseArgs()


def inspectArgs(**overrides):
    values = {
        "wand": False,
        "pillow": False,
        "ffmpeg": False,
        "windows": False,
        "sidecars": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def enabledReaderNames(args, path):
    return [reader.__name__ for reader, enabled in read_metadata.selectedReaders(args, path) if enabled]


def testDefaultReadersMatchMediaType(tmp_path, monkeypatch):
    monkeypatch.setattr(read_metadata, "IS_WINDOWS", False)

    assert enabledReaderNames(inspectArgs(), tmp_path / "image.jpg") == [
        "useWand",
        "usePillow",
        "useSidecars",
    ]
    assert enabledReaderNames(inspectArgs(), tmp_path / "video.mp4") == [
        "useFFMPEG",
        "useSidecars",
    ]


def testExplicitReaderCanInspectUnusualMediaType(tmp_path):
    assert enabledReaderNames(inspectArgs(wand=True), tmp_path / "video.mp4") == ["useWand"]


def testWindowsReaderLabelsFilesystemTime(tmp_path, monkeypatch, capsys):
    media = tmp_path / "image.jpg"
    media.touch()
    timestamp = datetime.datetime(2026, 3, 2, 10, 20, 30).timestamp()

    monkeypatch.setattr(read_metadata.os.path, "getmtime", lambda path: timestamp)

    read_metadata.useWindows(media)

    output = capsys.readouterr().out
    assert "Windows Modified Date (local filesystem time, not embedded capture metadata)" in output
    assert "2026-03-02 10:20:30" in output
