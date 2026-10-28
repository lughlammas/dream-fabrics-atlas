#!/usr/bin/env python3
"""Dream Fabrics Audiobook Factory: local-only web app, standard library server."""
import argparse
import json
import mimetypes
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import atlas
from factory_store import FactoryStore, StoreError
from factory_tts import Production, PROVIDERS, TTSError, VOICES
from factory_music import MusicStudio, MusicError, MAX_REFERENCE_BYTES

BASE = Path(__file__).resolve().parent
WEB = BASE / "factory_web"


def create_handler(store):
    production = Production(store)
    music = MusicStudio(store)

    class Handler(BaseHTTPRequestHandler):
        server_version = "DreamFabrics/1.0"

        def log_message(self, fmt, *args):
            # Never log request bodies, keys, or manuscript text.
            if os.environ.get("FACTORY_DEBUG") == "1":
                super().log_message(fmt, *args)

        def _json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path, download=False):
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(content)

        def _body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 15_000_000:
                raise StoreError("Requisição vazia ou grande demais")
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise StoreError("JSON inválido") from exc
            if not isinstance(data, dict):
                raise StoreError("Dados inválidos")
            return data

        def _reference_body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise MusicError("Tamanho de referência inválido") from exc
            if not 0 < length <= MAX_REFERENCE_BYTES:
                raise MusicError("Referência vazia ou maior que 25 MB")
            if self.headers.get("Content-Type", "").split(";")[0].strip() not in ("audio/mpeg", "audio/mp3", "audio/wav", "audio/wave", "audio/x-wav", "application/octet-stream"):
                raise MusicError("Tipo de áudio não aceito")
            return self.rfile.read(length)

        def _check_origin(self):
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and origin not in (f"http://{host}", f"https://{host}"):
                raise StoreError("Origem não permitida")
            if not host.startswith(("127.0.0.1:", "localhost:")):
                raise StoreError("Host não permitido")

        def _route(self, method):
            path = unquote(urlsplit(self.path).path)
            if method == "GET" and path == "/":
                return self._file(WEB / "index.html")
            if method == "GET" and path in ("/app.css", "/app.js"):
                return self._file(WEB / path[1:])
            if method == "GET" and path == "/api/health":
                return self._json({"ok": True, "provider_configured": PROVIDERS["openai"].configured()})
            if method == "GET" and path == "/api/music/health":
                return self._json(music.client.health())
            if method == "GET" and path == "/api/projects":
                return self._json(store.list())
            if method == "POST" and path == "/api/projects":
                data = self._body()
                return self._json(store.create(data.get("title", ""), data.get("author", ""),
                                               data.get("language", "pt-BR"), data.get("description", ""),
                                               data.get("cover")), 201)
            match = re.fullmatch(r"/api/projects/([a-f0-9]{32})(?:/(.*))?", path)
            if not match:
                return self._json({"error": "Rota não encontrada"}, 404)
            pid, sub = match.groups()
            if method == "GET" and not sub:
                return self._json(store.get(pid))
            if method == "PATCH" and not sub:
                return self._json(store.update(pid, self._body()))
            if method == "POST" and sub == "chapters":
                data = self._body()
                return self._json(store.add_chapter(pid, data.get("title", ""), data.get("text", "")))
            if method == "PUT" and sub == "chapters/order":
                return self._json(store.reorder(pid, self._body().get("names", [])))
            chapter = re.fullmatch(r"chapters/([a-zA-Z0-9_-]+\.(?:md|txt))", sub or "")
            if method == "PUT" and chapter:
                return self._json(store.save_chapter(pid, chapter.group(1), self._body().get("text", "")))
            if method == "POST" and sub == "import":
                data = self._body()
                return self._json(store.import_text(pid, data.get("filename", ""), data.get("content", "")))
            if method == "GET" and sub == "music":
                return self._json(music.list(pid, refresh=True))
            if method == "POST" and sub == "music":
                return self._json(music.save_draft(pid, self._body()), 201)
            track_route = re.fullmatch(r"music/([a-f0-9]{32})(?:/(generate))?", sub or "")
            if track_route and method == "PUT" and not track_route.group(2):
                return self._json(music.save_draft(pid, self._body(), track_route.group(1)))
            if track_route and method == "POST" and track_route.group(2) == "generate":
                return self._json(music.generate(pid, track_route.group(1), self._body().get("consent")))
            reference_route = re.fullmatch(r"music/([a-f0-9]{32})/reference", sub or "")
            if method == "PUT" and reference_route:
                return self._json(music.save_reference(pid, reference_route.group(1), self._reference_body(),
                                                       self.headers.get("X-File-Name", "")))
            reference_audio = re.fullmatch(r"music/reference/([a-f0-9]{32}-reference-[a-f0-9]{32}\.(?:mp3|wav))", sub or "")
            if method == "GET" and reference_audio:
                name = reference_audio.group(1)
                return self._file(music.reference_file(pid, name[:32], name))
            music_audio = re.fullmatch(r"music/audio/([a-f0-9]{32}-[0-9]{2}\.wav)", sub or "")
            if method == "GET" and music_audio:
                return self._file(music.audio_file(pid, music_audio.group(1)),
                                  download="download" in urlsplit(self.path).query)
            if method == "GET" and sub == "production":
                return self._json(production.status(pid))
            if method == "POST" and sub == "production":
                data = self._body()
                return self._json(production.start(pid, data.get("names", []), data))
            if method == "POST" and sub == "preview":
                data = self._body()
                if not data.get("consent"):
                    raise TTSError("Confirme o envio da frase de amostra")
                phrase = str(data.get("text", ""))[:280].strip()
                if not phrase:
                    raise TTSError("Digite uma frase de amostra")
                voice = data.get("voice", "coral")
                audio = PROVIDERS["openai"].synthesize(phrase, voice, "mp3", 1, "")
                target = store._dir(pid) / "audio" / "preview.mp3"
                target.write_bytes(audio)
                return self._json({"url": f"/api/projects/{pid}/audio/preview.mp3"})
            if method == "GET" and sub == "cover":
                return self._file(store.file(pid, "cover"))
            audio = re.fullmatch(r"audio/([a-zA-Z0-9_-]+\.(?:mp3|wav|zip|m4b))", sub or "")
            if method == "GET" and audio:
                return self._file(store.file(pid, "audio", audio.group(1)), download="download" in urlsplit(self.path).query)
            if method == "POST" and sub == "export/audio":
                target = production.export_zip(pid)
                return self._json({"url": f"/api/projects/{pid}/audio/{target.name}?download=1"})
            if method == "POST" and sub == "export/book":
                errors, _, _, _ = atlas.validate_project(str(store._dir(pid)))
                if errors:
                    raise StoreError("Validação editorial: " + "; ".join(errors[:3]))
                code = atlas.main(["build", str(store._dir(pid))])
                if code != 0:
                    raise StoreError("Falha na compilação editorial")
                manifest = store.get(pid)["manifest"]
                files = []
                for ext in ("txt", "epub", "pdf"):
                    file = store._dir(pid) / "build" / (manifest["id"] + "." + ext)
                    if file.exists():
                        files.append(file.name)
                return self._json({"files": files})
            book = re.fullmatch(r"book/([a-zA-Z0-9_-]+\.(?:txt|epub|pdf))", sub or "")
            if method == "GET" and book:
                target = store._dir(pid) / "build" / book.group(1)
                if not target.is_file():
                    raise StoreError("Arquivo não encontrado")
                return self._file(target, download=True)
            return self._json({"error": "Rota não encontrada"}, 404)

        def do_GET(self):
            try:
                self._route("GET")
            except (StoreError, TTSError, MusicError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception:
                self._json({"error": "Erro interno"}, 500)

        def do_POST(self):
            self._write("POST")

        def do_PUT(self):
            self._write("PUT")

        def do_PATCH(self):
            self._write("PATCH")

        def _write(self, method):
            try:
                self._check_origin()
                self._route(method)
            except (StoreError, TTSError, MusicError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception:
                self._json({"error": "Erro interno"}, 500)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dream Fabrics Audiobook Factory")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--data", default=str(BASE / ".factory-data"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("porta inválida")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), create_handler(FactoryStore(args.data)))
    print(f"Dream Fabrics: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
