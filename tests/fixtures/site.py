"""Isolated smoke fixture. Run only on the smoke network, never publish port 8000.

POST /_admin/state uses Authorization: Bearer $SMOKE_TOKEN; GET /api/data
is deliberately public. Framework runtimes are vendored official npm releases.
"""
from __future__ import annotations

import copy
import gzip
import hmac
import json
import os
import threading
from decimal import Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).with_name("static")
LOCK = threading.Lock()
DEFAULT_STATE = {
    "current_price": "100", "original_price": "150", "currency": "USD",
    "items": [{"id": "A", "name": "Alpha", "price": "10"}],
    "http_500": False, "empty": False, "duplicate": False,
    "selector_changed": False, "pagination": False, "page_size": 1,
    "private_resource": False,
}
STATE = copy.deepcopy(DEFAULT_STATE)
TOKEN = ""
PRIVATE_TARGETS = {
    "loopback": "http://127.0.0.1/", "metadata": "http://169.254.169.254/latest/meta-data/",
    "ipv6": "http://[::1]/",
}


def validated_patch(value: object) -> dict:
    if not isinstance(value, dict) or not value or set(value) - set(DEFAULT_STATE):
        raise ValueError("Expected a nonempty object with known state keys")
    for key, entry in value.items():
        default = DEFAULT_STATE[key]
        if isinstance(default, bool):
            if type(entry) is not bool:
                raise ValueError(f"{key} must be boolean")
        elif key == "page_size":
            if type(entry) is not int or not 1 <= entry <= 100:
                raise ValueError("page_size must be 1..100")
        elif key == "items":
            if not isinstance(entry, list) or len(entry) > 100:
                raise ValueError("items must be a list of at most 100 rows")
            for row in entry:
                if not isinstance(row, dict) or set(row) != {"id", "name", "price"}:
                    raise ValueError("Each item requires exactly id, name, price")
                for field in ("id", "name"):
                    if not isinstance(row[field], str) or not 1 <= len(row[field]) <= 256:
                        raise ValueError("Item id/name must be nonempty strings up to 256 characters")
                validate_decimal(row["price"])
        elif key in {"current_price", "original_price"}:
            validate_decimal(entry)
        elif key == "currency":
            if not isinstance(entry, str) or len(entry) != 3 or not entry.isascii() or not entry.isupper() or not entry.isalpha():
                raise ValueError("currency must be a three-letter uppercase code")
    return value


def validate_decimal(value: object) -> None:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Prices must be decimal strings")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal price") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Prices must be finite and nonnegative")


def document(title: str, body: str, scripts: str = "") -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<link rel="stylesheet" href="/assets/site.css"></head><body><main><h1>{escape(title)}</h1>
{body}</main>{scripts}</body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "SmokeFixture/1"

    def log_message(self, format: str, *args: object) -> None:
        # No request target, headers, body or query values (and thus no tokens).
        pass

    def send(self, status: int, data: bytes | str, content_type: str, **headers: str) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        if content_type == "text/html; charset=utf-8" and "gzip" in self.headers.get("Accept-Encoding", "").replace(" ", "").split(","):
            payload = gzip.compress(payload, mtime=0)
            headers["Content_Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(payload)

    def json_response(self, status: int, value: object) -> None:
        self.send(status, json.dumps(value, ensure_ascii=False), "application/json; charset=utf-8")

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return bool(TOKEN) and hmac.compare_digest(supplied.encode(), ("Bearer " + TOKEN).encode())

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/_admin/state", "/_admin/reset"}:
            self.json_response(404, {"error": "not_found"})
            return
        if not self.authorized():
            self.json_response(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Expected a JSON body up to 65536 bytes")
            value = json.loads(self.rfile.read(length))
            if path == "/_admin/reset":
                if value != {}:
                    raise ValueError("Reset body must be {}")
                patch = copy.deepcopy(DEFAULT_STATE)
            else:
                patch = validated_patch(value)
        except (ValueError, UnicodeDecodeError) as exc:
            self.json_response(400, {"error": "invalid_state", "message": str(exc)})
            return
        with LOCK:
            STATE.update(copy.deepcopy(patch))
            result = copy.deepcopy(STATE)
        self.json_response(200, result)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/health":
            self.json_response(200, {"status": "ok"})
            return
        if path == "/_admin/state":
            if not self.authorized():
                self.json_response(403, {"error": "forbidden"})
                return
            with LOCK:
                state = copy.deepcopy(STATE)
            self.json_response(200, state)
            return
        if path.startswith("/assets/"):
            relative = path.removeprefix("/assets/")
            allowed = {
                "site.css", "list.js", "private.js", "vendor/react-18.3.1.min.js",
                "vendor/react-dom-18.3.1.min.js", "vendor/vue-3.5.43.min.js",
            }
            if relative not in allowed:
                self.json_response(404, {"error": "not_found"})
                return
            self.send(200, (STATIC / relative).read_bytes(), "text/css" if relative.endswith(".css") else "text/javascript; charset=utf-8")
            return
        if path == "/redirect-private":
            target = parse_qs(parsed.query).get("target", ["loopback"])
            if len(target) != 1:
                self.json_response(400, {"error": "invalid_target"})
                return
            location = PRIVATE_TARGETS.get(target[0])
            if location is None and target[0] in PRIVATE_TARGETS.values():
                location = target[0]
            if location is None:
                self.json_response(400, {"error": "invalid_target"})
                return
            self.send(302, "Redirecting", "text/plain", Location=location)
            return
        with LOCK:
            state = copy.deepcopy(STATE)
        known = {"/static/product", "/ssr/product", "/react/list", "/vue/list", "/api/data", "/private-resource"}
        if path not in known:
            self.json_response(404, {"error": "not_found"})
            return
        if state["http_500"]:
            self.json_response(500, {"error": "fixture_failure"})
            return
        if path == "/api/data":
            if state["empty"]:
                state["items"] = []
                state["current_price"] = ""
            if state["duplicate"] and state["items"]:
                state["items"].append(copy.deepcopy(state["items"][0]))
            self.json_response(200, state)
            return
        private_script = '<script defer src="/assets/private.js"></script>'
        if path == "/private-resource":
            self.send(200, document("Private resource probe", '<p id="probe-status" role="status">Starting blocked resource probes</p>', private_script), "text/html; charset=utf-8")
            return
        if path.endswith("/product"):
            price = "" if state["empty"] else escape(state["current_price"])
            price_id = "replacement-price" if state["selector_changed"] else "current-price"
            body = f'''<article id="product"><h2 id="product-name">Fixture Coffee</h2>
<p>Current price: <span id="{price_id}" data-field="current_price">{price}</span>
<span id="currency">{escape(state['currency'])}</span></p>
<p>Original price: <del id="original-price">{escape(state['original_price'])}</del></p>
<p id="availability">In stock</p></article>'''
            self.send(200, document("Server-rendered product" if path.startswith("/ssr") else "Static product", body, private_script if state["private_resource"] else ""), "text/html; charset=utf-8")
            return
        framework = path.split("/")[1]
        scripts = ('<script defer src="/assets/vendor/react-18.3.1.min.js"></script><script defer src="/assets/vendor/react-dom-18.3.1.min.js"></script>'
                   if framework == "react" else '<script defer src="/assets/vendor/vue-3.5.43.min.js"></script>')
        scripts += '<script defer src="/assets/list.js"></script>'
        self.send(200, document(framework.title() + " inventory", f'<div id="app" data-framework="{framework}"><p role="status">Loading inventory…</p></div><noscript>JavaScript is required to load inventory.</noscript>', scripts), "text/html; charset=utf-8")


def main() -> None:
    global TOKEN
    TOKEN = os.environ.get("SMOKE_TOKEN", "")
    if len(TOKEN) < 32 or any(char.isspace() for char in TOKEN):
        raise SystemExit("SMOKE_TOKEN must contain at least 32 random non-whitespace characters")
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
