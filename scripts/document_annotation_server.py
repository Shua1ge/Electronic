"""Loopback-only annotation HTTP server. Project data never leave this process."""
from __future__ import annotations

import json
import mimetypes
import secrets
import subprocess
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from document_annotation import AnnotationConflict, AnnotationStore, export_project
from document_eval_core import require


def make_handler(store, token, ocr_language="chi_sim+eng"):
    web = Path(__file__).resolve().parents[1] / "web" / "document-annotation"

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data, kind="application/json; charset=utf-8", headers=None):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def check_host(self):
            require(self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}", "请使用启动时显示的127.0.0.1地址")

        def do_GET(self):
            try:
                self.check_host()
                url = urlparse(self.path)
                if url.path == "/api/session":
                    return self.respond(200, {"token": token})
                if url.path == "/api/project":
                    return self.respond(200, store.load())
                if url.path == "/api/page":
                    page = int(parse_qs(url.query).get("page", ["0"])[0])
                    return self.respond(200, store.page_image(page).read_bytes(), "image/png")
                if url.path.startswith("/api/download/"):
                    export_id = url.path.removeprefix("/api/download/")
                    require(len(export_id) == 24 and all(c in "0123456789abcdef" for c in export_id), "导出ID无效")
                    file = store.root / "exports" / export_id / "annotation-export.zip"
                    require(file.is_file(), "导出不存在")
                    return self.respond(200, file.read_bytes(), "application/zip", {"Content-Disposition": 'attachment; filename="annotation-export.zip"'})
                if url.path.startswith("/api/"):
                    return self.respond(404, {"error": "接口不存在"})
                relative = "index.html" if url.path == "/" else unquote(url.path).lstrip("/")
                file = (web / relative).resolve()
                require(file.is_relative_to(web.resolve()) and file.is_file(), "页面文件不存在")
                return self.respond(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.respond(400, {"error": str(exc)})

        def do_POST(self):
            try:
                self.check_host()
                require(self.headers.get("X-Annotation-Token") == token, "会话失效，请刷新页面")
                origin = self.headers.get("Origin")
                require(not origin or origin == f"http://{self.headers.get('Host')}", "请求来源无效")
                require(self.headers.get("Content-Type", "").split(";")[0] == "application/json", "需要JSON请求")
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 16000000, "请求大小无效")
                from document_eval_core import _bad_constant, _unique_keys
                body = json.loads(self.rfile.read(size), parse_constant=_bad_constant, object_pairs_hook=_unique_keys)
                path = urlparse(self.path).path
                if path == "/api/save":
                    return self.respond(200, store.save(body.get("revision"), body.get("regions"), body.get("links"), body.get("actor")))
                if path == "/api/suggest":
                    return self.respond(200, store.suggest_text(body.get("region_id"), ocr_language))
                if path == "/api/export":
                    condition = body.get("condition", "A")
                    require(condition in {"A", "B", "C"}, "条件无效")
                    export_id = secrets.token_hex(12)
                    output = export_project(store.root, store.root / "exports" / export_id, condition)
                    with zipfile.ZipFile(output / "annotation-export.zip", "x", zipfile.ZIP_DEFLATED) as archive:
                        for name in ("manifest.json", "gold.json", "annotation_snapshot.json"):
                            archive.write(output / name, name)
                    return self.respond(200, {"directory": str(output), "download_url": f"/api/download/{export_id}"})
                return self.respond(404, {"error": "接口不存在"})
            except AnnotationConflict as exc:
                self.respond(409, {"error": str(exc)})
            except (ValueError, OSError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                self.respond(400, {"error": str(exc)})

    return Handler


def serve_project(project, port=8766, ocr_language="chi_sim+eng"):
    store = AnnotationStore(project)
    store.check_source(store.load())
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, secrets.token_urlsafe(32), ocr_language))
    print(f"文档标注：http://127.0.0.1:{server.server_port} （Ctrl+C停止；保存到 {store.root}）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
