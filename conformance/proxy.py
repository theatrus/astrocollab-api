"""Recording proxy that checks an AstroCollab client.

    python -m conformance.proxy --listen 127.0.0.1:8081 --upstream http://127.0.0.1:8080/v1 \
        [--allow-http-loopback] [--report out.json]

Point the client under test at http://127.0.0.1:8081/v1. The proxy forwards
each request to the upstream server, checks the request (client faults) and
the response (server faults), and prints a report when stopped.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal
import sys
import threading
from typing import Any
from urllib.parse import urlsplit

from .contract import Contract

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "host", "content-length"}


@dataclass
class Recorder:
    requests: int = 0
    client: list[dict[str, Any]] = field(default_factory=list)
    server: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, side: str, method: str, path: str, issues: list[str]) -> None:
        with self.lock:
            target = self.client if side == "client" else self.server
            target += [{"request": f"{method} {path.split('?')[0]}", "issue": i} for i in issues]

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {"requests": self.requests, "client_faults": list(self.client), "server_faults": list(self.server)}


def make_proxy(listen: tuple[str, int], upstream: str, contract: Contract,
               recorder: Recorder | None = None) -> tuple[ThreadingHTTPServer, Recorder]:
    recorder = recorder or Recorder()
    parts = urlsplit(upstream.rstrip("/"))
    upstream_prefix = parts.path
    connection = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def handle_any(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            if not self.path.startswith(upstream_prefix + "/") and self.path != upstream_prefix:
                self.reply(404, {"content-type": "text/plain"}, b"Not under the API root\n")
                recorder.add("client", self.command, self.path, [f"path outside the API root {upstream_prefix}"])
                return
            relative = self.path[len(upstream_prefix):] or "/"
            with recorder.lock:
                recorder.requests += 1
            issues = contract.check_request(self.command, relative, headers, body)
            if "?" in relative and any(word in relative.lower() for word in ("key=", "token=", "authorization=")):
                issues.append("credential-like value in the URL")
            recorder.add("client", self.command, relative, issues)

            forward = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            if body or self.command in ("POST", "PUT", "PATCH"):
                forward["Content-Length"] = str(len(body))
            conn = connection(parts.netloc, timeout=120)
            try:
                conn.request(self.command, self.path, body=body, headers=forward)
                raw = conn.getresponse()
                data = raw.read()
                status = raw.status
                response_headers = {k.lower(): v for k, v in raw.getheaders()}
            except OSError as error:
                recorder.add("server", self.command, relative, [f"upstream unreachable: {error}"])
                self.reply(502, {"content-type": "text/plain"}, b"Upstream unreachable\n")
                return
            finally:
                conn.close()
            recorder.add("server", self.command, relative,
                         contract.check_response(self.command, relative, status, response_headers, data))
            if relative.split("?")[0] == "/capabilities" and status == 200:
                data = self.rewrite_api_root(data)
            self.reply(status, response_headers, data)

        def rewrite_api_root(self, data: bytes) -> bytes:
            """Keep the client on the proxy after discovery."""
            try:
                value = json.loads(data)
            except ValueError:
                return data
            host, port = self.server.server_address[:2]
            value["api_root"] = f"http://{host}:{port}{upstream_prefix}"
            return json.dumps(value).encode()

        def reply(self, status: int, headers: dict[str, str], data: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                if name not in HOP_HEADERS:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

    server = ThreadingHTTPServer(listen, Handler)
    server.daemon_threads = True
    return server, recorder


def format_report(report: dict[str, Any]) -> str:
    lines = [f"{report['requests']} requests checked."]
    for title, key in (("Client faults", "client_faults"), ("Server faults", "server_faults")):
        lines.append(f"{title}: {len(report[key])}")
        lines += [f"  {item['request']}: {item['issue']}" for item in report[key]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an AstroCollab client through a recording proxy.")
    parser.add_argument("--listen", default="127.0.0.1:8081", help="host:port to listen on")
    parser.add_argument("--upstream", required=True, help="server API root, such as http://127.0.0.1:8080/v1")
    parser.add_argument("--allow-http-loopback", action="store_true",
                        help="accept http:// URLs on localhost, for local test servers")
    parser.add_argument("--report", help="write the report as JSON to this file")
    parser.add_argument("--schemas", help="validate bodies against standalone JSON Schemas in this directory")
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    contract = Contract(allow_http_loopback=args.allow_http_loopback, schema_dir=args.schemas)
    server, recorder = make_proxy((host, int(port)), args.upstream, contract)
    prefix = urlsplit(args.upstream.rstrip("/")).path
    print(f"Point the client at http://{host}:{server.server_address[1]}{prefix}. Press Ctrl-C to stop.",
          flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    report = recorder.report()
    print(format_report(report))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as out:
            json.dump(report, out, indent=2)
    return 1 if report["client_faults"] else 0


if __name__ == "__main__":
    sys.exit(main())
