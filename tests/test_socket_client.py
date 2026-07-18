import json
import socket
import threading

import pytest

from socket_client import HerdrRequestError, HerdrSocket


def _make_server(sock_path):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    return server


def _accept_and_handle(server, handle_conn):
    conn, _ = server.accept()
    try:
        handle_conn(conn)
    finally:
        conn.close()
        server.close()


def _start_server_thread(server, handle_conn):
    thread = threading.Thread(target=_accept_and_handle, args=(server, handle_conn), daemon=True)
    thread.start()
    return thread


def test_request_returns_result(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        assert request["method"] == "ping"
        assert request["params"] == {}
        response = json.dumps({"id": request["id"], "result": {"type": "pong"}}) + "\n"
        conn.sendall(response.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    result = client.request("req_1", "ping", {})
    assert result == {"type": "pong"}
    thread.join(timeout=2)


def test_request_raises_on_error(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        response = (
            json.dumps({"id": request["id"], "error": {"code": "not_found", "message": "pane not found"}})
            + "\n"
        )
        conn.sendall(response.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    with pytest.raises(HerdrRequestError) as exc_info:
        client.request("req_1", "pane.get", {"pane_id": "w1:p1"})
    assert exc_info.value.code == "not_found"
    thread.join(timeout=2)


def test_subscribe_yields_pushed_events(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        assert request["method"] == "events.subscribe"
        ack = json.dumps({"id": request["id"], "result": {"type": "subscribed"}}) + "\n"
        conn.sendall(ack.encode())
        event = (
            json.dumps(
                {
                    "event": "pane.output_matched",
                    "data": {"matched_line": "5-hour limit reached - resets 3pm"},
                }
            )
            + "\n"
        )
        conn.sendall(event.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    events = client.subscribe("sub_1", {"subscriptions": [{"type": "pane.output_matched"}]})
    first_event = next(events)
    assert first_event["data"]["matched_line"] == "5-hour limit reached - resets 3pm"
    thread.join(timeout=2)


def test_subscribe_does_not_time_out_waiting_for_a_delayed_event(tmp_path):
    # Regression test: a rate-limit subscription may legitimately need to
    # wait far longer than the regular request timeout for the next
    # matching output line. Only the connect + initial-ack read should use
    # the short timeout; waiting for the pushed event itself must block
    # rather than raise TimeoutError. Uses a short client timeout (0.2s) and
    # a server delay longer than it (0.5s) so a regression fails fast.
    import time

    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        ack = json.dumps({"id": request["id"], "result": {"type": "subscribed"}}) + "\n"
        conn.sendall(ack.encode())
        time.sleep(0.5)
        event = json.dumps({"event": "pane.output_matched", "data": {"matched_line": "delayed"}}) + "\n"
        conn.sendall(event.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path, timeout=0.2)
    events = client.subscribe("sub_1", {"subscriptions": [{"type": "pane.output_matched"}]})
    first_event = next(events)
    assert first_event["data"]["matched_line"] == "delayed"
    thread.join(timeout=2)


def test_subscribe_raises_on_error_ack(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        response = (
            json.dumps({"id": request["id"], "error": {"code": "invalid_regex", "message": "bad pattern"}})
            + "\n"
        )
        conn.sendall(response.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    events = client.subscribe("sub_1", {"subscriptions": [{"type": "pane.output_matched"}]})
    with pytest.raises(HerdrRequestError) as exc_info:
        next(events)
    assert exc_info.value.code == "invalid_regex"
    thread.join(timeout=2)
