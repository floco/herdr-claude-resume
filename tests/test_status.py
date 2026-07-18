import os

from state import write_watcher_pidfile
from status import main


def test_main_reports_no_watchers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    assert main() == 0
    assert "No Claude Code panes" in capsys.readouterr().out


def test_main_lists_alive_watchers(tmp_path, monkeypatch, capsys):
    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))

    assert main() == 0
    output = capsys.readouterr().out
    assert "w1:p1" in output
