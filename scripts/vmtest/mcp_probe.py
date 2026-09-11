#!/usr/bin/env python
"""Speak MCP to `uvx inkscape_mcp` over stdio, the way an agent would.

The `mcp_servers` domain registers a server; this asks whether the thing behind
the registration serves anything. It is deliberately dependency-free (stdlib
JSON-RPC over a pipe) so it runs in a guest that has nothing installed but
python and uv.

Exit code 0 means: the server initialized, listed its tools, and one
`tools/call` left an SVG at --output. Anything else prints why and fails.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - launching the MCP server is the whole point
import sys
import threading

PROTOCOL = "2025-06-18"


class Server:
    """One stdio MCP server, spoken to in newline-delimited JSON-RPC."""

    def __init__(self, argv: list[str], timeout: float) -> None:
        self._timeout = timeout
        self._proc = subprocess.Popen(  # nosec B603 - argv is built here, no shell
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        self._id = 0
        # Drain stderr in the background: a server that logs a lot would
        # otherwise fill the pipe and block forever, which looks like a hang.
        self._errors: list[str] = []
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._errors.append(line.rstrip())

    def _send(self, message: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self._id += 1
        wanted = self._id
        self._send({"jsonrpc": "2.0", "id": wanted, "method": method,
                    "params": params})
        result: dict = {}
        error: list = []

        def read() -> None:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue          # a stray log line is not an answer
                if message.get("id") == wanted:
                    if "error" in message:
                        error.append(message["error"])
                    else:
                        result.update(message.get("result") or {})
                    return

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(self._timeout)
        if thread.is_alive():
            raise TimeoutError(f"{method} did not answer in {self._timeout}s")
        if error:
            raise RuntimeError(f"{method} failed: {error[0]}")
        return result

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            self._proc.wait(timeout=10)
        except Exception:             # nosec B110 - a dead server needs no goodbye
            self._proc.kill()

    @property
    def errors(self) -> list[str]:
        return self._errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default="uvx",
                        help="the MCP server's program (default: uvx)")
    parser.add_argument("--args", nargs="*", default=["inkscape_mcp"],
                        help="its arguments (default: inkscape_mcp)")
    parser.add_argument("--output", default="/tmp/chorra.svg",  # nosec B108
                        help="where the drawing has to end up")
    parser.add_argument("--data", default="dasik",
                        help="payload for the QR code that gets drawn")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="per-request timeout: uvx downloads on first run")
    options = parser.parse_args()

    if os.path.exists(options.output):
        os.unlink(options.output)     # a stale file would pass the check

    server = Server([options.command, *options.args], options.timeout)
    try:
        info = server.request("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "dasik-vmtest", "version": "1"}})
        print("server:", json.dumps(info.get("serverInfo") or {}))
        server.notify("notifications/initialized", {})

        tools = [t["name"] for t in server.request("tools/list", {})
                 .get("tools", [])]
        print("tools:", ", ".join(sorted(tools)))
        if "inkscape_vector" not in tools:
            print("inkscape_vector is not among the tools", file=sys.stderr)
            return 2

        answer = server.request("tools/call", {
            "name": "inkscape_vector",
            "arguments": {"operation": "generate_barcode_qr",
                          "barcode_data": options.data,
                          "output_path": options.output}})
        for block in answer.get("content", []):
            if block.get("type") == "text":
                print("result:", block["text"][:400])
        if answer.get("isError"):
            print("the tool call reported an error", file=sys.stderr)
            return 3
    except Exception as failure:
        print(f"probe failed: {failure}", file=sys.stderr)
        for line in server.errors[-10:]:
            print("server stderr:", line, file=sys.stderr)
        return 4
    finally:
        server.close()

    if not os.path.exists(options.output) \
            or os.path.getsize(options.output) == 0:
        print(f"no drawing at {options.output}", file=sys.stderr)
        for line in server.errors[-10:]:
            print("server stderr:", line, file=sys.stderr)
        return 5
    head = open(options.output, encoding="utf-8", errors="replace").read(400)
    if "<svg" not in head:
        print(f"{options.output} is not an SVG:\n{head}", file=sys.stderr)
        return 6
    print(f"drew {os.path.getsize(options.output)} bytes of SVG at "
          f"{options.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
