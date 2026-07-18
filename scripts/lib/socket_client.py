"""Minimal raw client for herdr's local socket API (newline-delimited JSON
over a Unix domain socket). No dependency beyond the standard library --
mirrors the approach herdr's own bundled Claude integration script uses.

herdr's socket server handles exactly one request per accepted connection
(it reads one line, dispatches, writes one response, and closes) except for
`events.subscribe`, which keeps its connection open to push further event
lines. Each call below opens its own fresh connection accordingly -- reusing
one connection across multiple `.request()` calls raises BrokenPipeError,
since the server has already closed its end after the first response.
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
        self.socket_path = socket_path
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        return sock

    @staticmethod
    def _read_line(sock: socket.socket, buffer: bytes) -> tuple[dict, bytes]:
        while b"\n" not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("herdr socket closed")
            buffer += chunk
        line, _, rest = buffer.partition(b"\n")
        return json.loads(line.decode("utf-8")), rest

    def request(self, request_id: str, method: str, params: dict) -> dict:
        sock = self._connect()
        try:
            payload = json.dumps({"id": request_id, "method": method, "params": params})
            sock.sendall((payload + "\n").encode("utf-8"))
            response, _ = self._read_line(sock, b"")
            if "error" in response:
                error = response["error"]
                raise HerdrRequestError(error.get("code", "unknown"), error.get("message", ""))
            return response["result"]
        finally:
            sock.close()

    def subscribe(self, request_id: str, params: dict) -> Iterator[dict]:
        """Send an events.subscribe request and yield each pushed event
        after the initial ack. This holds one connection open for as long
        as the caller keeps consuming the iterator.

        The connect and initial-ack read use the regular short timeout, so
        an unreachable server fails fast. After that, the socket switches to
        blocking mode (no timeout): a rate-limit subscription may
        legitimately need to wait hours for the next matching output line,
        and a fixed short timeout would wrongly raise TimeoutError while
        correctly waiting for that.
        """
        sock = self._connect()
        try:
            payload = json.dumps({"id": request_id, "method": "events.subscribe", "params": params})
            sock.sendall((payload + "\n").encode("utf-8"))
            buffer = b""
            ack, buffer = self._read_line(sock, buffer)
            if "error" in ack:
                error = ack["error"]
                raise HerdrRequestError(error.get("code", "unknown"), error.get("message", ""))
            sock.settimeout(None)
            while True:
                event, buffer = self._read_line(sock, buffer)
                yield event
        finally:
            sock.close()
