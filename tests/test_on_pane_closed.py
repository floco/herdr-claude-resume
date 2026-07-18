import subprocess
import sys

from on_pane_closed import main
from state import watcher_pidfile_path, write_watcher_pidfile


def test_main_kills_running_watcher(tmp_path, monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    write_watcher_pidfile(tmp_path, "w1:p1", process.pid)
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))

    result = main()

    assert result == 0
    process.wait(timeout=5)
    assert process.returncode is not None
    assert not watcher_pidfile_path(tmp_path, "w1:p1").exists()


def test_main_noop_when_pane_id_missing(monkeypatch):
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    assert main() == 0


def test_main_noop_when_no_pidfile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    assert main() == 0
