"""Minimal raw client for herdr's local socket API (newline-delimited JSON
over a Unix domain socket). No dependency beyond the standard library --
mirrors the approach herdr's own bundled Claude integration script uses.
"""
from __future__ import annotations

import json
import socket
from typing import Iterator


class HerdrRequestError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HerdrSocket:
    def __init__(self, socket_path: str, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._buffer = b""

    def _read_line(self) -> dict:
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("herdr socket closed")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def request(self, request_id: str, method: str, params: dict) -> dict:
        payload = json.dumps({"id": request_id, "method": method, "params": params})
        self._sock.sendall((payload + "\n").encode("utf-8"))
        response = self._read_line()
        if "error" in response:
            error = response["error"]
            raise HerdrRequestError(error.get("code", "unknown"), error.get("message", ""))
        return response["result"]

    def subscribe(self, request_id: str, params: dict) -> Iterator[dict]:
        """Send an events.subscribe request and yield each pushed event
        after the initial ack. This call blocks between yields."""
        payload = json.dumps({"id": request_id, "method": "events.subscribe", "params": params})
        self._sock.sendall((payload + "\n").encode("utf-8"))
        ack = self._read_line()
        if "error" in ack:
            error = ack["error"]
            raise HerdrRequestError(error.get("code", "unknown"), error.get("message", ""))
        while True:
            yield self._read_line()

    def close(self) -> None:
        self._sock.close()
