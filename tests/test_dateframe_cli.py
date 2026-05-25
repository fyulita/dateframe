import sys
from types import SimpleNamespace

import dateframe_cli


def testCliDispatchesCommandAndRemovesSubcommandFromArgv(monkeypatch):
    captured = {}

    def commandMain():
        captured["argv"] = list(sys.argv)
        captured["subcommand"] = dateframe_cli.os.environ.get("DATEFRAME_SUBCOMMAND")
        return 0

    monkeypatch.setattr(
        dateframe_cli.importlib,
        "import_module",
        lambda moduleName: SimpleNamespace(main=commandMain),
    )
    monkeypatch.setattr(sys, "argv", ["dateframe", "rename", "--copy", "source", "dest"])

    assert dateframe_cli.main() == 0
    assert captured["argv"] == ["dateframe rename", "--copy", "source", "dest"]
    assert captured["subcommand"] == "rename"
    assert "DATEFRAME_SUBCOMMAND" not in dateframe_cli.os.environ


def testCliRejectsUnknownCommand(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dateframe", "missing"])

    assert dateframe_cli.main() == 2
    output = capsys.readouterr()
    assert "unknown command: missing" in output.err
    assert "usage: dateframe <command>" in output.out
