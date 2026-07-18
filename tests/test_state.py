import os
import subprocess
import sys

from state import (
    is_watcher_running,
    kill_watcher,
    list_watched_pane_ids,
    remove_watcher_pidfile,
    watcher_pidfile_path,
    write_watcher_pidfile,
)


def test_pidfile_path_sanitizes_pane_id(tmp_path):
    path = watcher_pidfile_path(tmp_path, "w1:p1")
    assert path == tmp_path / "watchers" / "w1_p1.pid"


def test_is_watcher_running_false_when_no_pidfile(tmp_path):
    assert is_watcher_running(tmp_path, "w1:p1") is False


def test_write_then_is_running_true_for_self_pid(tmp_path):
    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())
    assert is_watcher_running(tmp_path, "w1:p1") is True


def test_is_watcher_running_false_for_dead_pid(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    write_watcher_pidfile(tmp_path, "w1:p1", process.pid)
    assert is_watcher_running(tmp_path, "w1:p1") is False


def test_remove_pidfile_is_idempotent(tmp_path):
    remove_watcher_pidfile(tmp_path, "w1:p1")
    write_watcher_pidfile(tmp_path, "w1:p1", 123)
    remove_watcher_pidfile(tmp_path, "w1:p1")
    remove_watcher_pidfile(tmp_path, "w1:p1")
    assert is_watcher_running(tmp_path, "w1:p1") is False


def test_kill_watcher_terminates_and_removes_pidfile(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    write_watcher_pidfile(tmp_path, "w1:p1", process.pid)
    assert kill_watcher(tmp_path, "w1:p1") is True
    process.wait(timeout=5)
    assert process.returncode is not None
    assert not watcher_pidfile_path(tmp_path, "w1:p1").exists()


def test_kill_watcher_false_when_no_pidfile(tmp_path):
    assert kill_watcher(tmp_path, "w1:p1") is False


def test_list_watched_pane_ids_returns_only_alive_ones(tmp_path):
    dead_process = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_process.wait(timeout=5)

    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())
    write_watcher_pidfile(tmp_path, "w2:p2", dead_process.pid)

    result = list_watched_pane_ids(tmp_path)
    assert result == ["w1:p1"]


def test_list_watched_pane_ids_empty_when_no_watchers_dir(tmp_path):
    assert list_watched_pane_ids(tmp_path) == []
