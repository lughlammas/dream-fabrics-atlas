"""ACE-Step 1.5 adapter and local music project state.

The adapter contacts only 127.0.0.1. It never installs models, starts an ACE
process, or calls a hosted service. The user starts ACE-Step separately.
"""
import json
import io
import mimetypes
import os
import re
import threading
import time
import uuid
import urllib.error
import urllib.request
import wave
from pathlib import Path
from urllib.parse import unquote, urlsplit

from factory_store import StoreError, atomic_json


class MusicError(ValueError):
    pass


MAX_REFERENCE_BYTES = 25_000_000


def inspect_reference(data):
    """Check the bytes, not the supplied extension or MIME type."""
    if not data or len(data) > MAX_REFERENCE_BYTES:
        raise MusicError("Referência vazia ou maior que 25 MB")
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(data), "rb") as source:
                if source.getnframes() < 1 or source.getframerate() < 1:
                    raise MusicError("WAV sem áudio")
                frames = source.readframes(source.getnframes())
                if len(frames) != source.getnframes() * source.getnchannels() * source.getsampwidth():
                    raise MusicError("WAV incompleto")
                return "wav"
        except (wave.Error, EOFError) as exc:
            raise MusicError("WAV inválido") from exc
    # MPEG layer III: require two consecutive, well-formed frames after ID3.
    start = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        if any(byte & 0x80 for byte in data[6:10]):
            raise MusicError("Cabeçalho MP3 inválido")
        start = 10 + sum(data[6+i] << (21-7*i) for i in range(4))
    bitrates = {3: [0,32,40,48,56,64,80,96,112,128,160,192,224,256,320],
                2: [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160],
                0: [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160]}
    def frame(offset):
        if offset + 4 > len(data):
            return None
        header = int.from_bytes(data[offset:offset+4], "big")
        version, layer = (header >> 19) & 3, (header >> 17) & 3
        rate_index, sample_index = (header >> 12) & 15, (header >> 10) & 3
        if header >> 21 != 0x7ff or version == 1 or layer != 1 or rate_index in (0,15) or sample_index == 3:
            return None
        sample_rate = [44100,48000,32000][sample_index] // (1 if version == 3 else 2 if version == 2 else 4)
        bitrate = bitrates[version][rate_index] * 1000
        length = (144 if version == 3 else 72) * bitrate // sample_rate + ((header >> 9) & 1)
        return (version, sample_rate, length) if length >= 24 else None
    for offset in range(start, min(len(data)-8, start+4096)):
        first = frame(offset)
        if first and (second := frame(offset+first[2])) and first[:2] == second[:2] and offset+first[2]+second[2] <= len(data):
            return "mp3"
    raise MusicError("Use um MP3 ou WAV válido")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _int(value, low, high, label):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MusicError(f"{label} inválido") from exc
    if not low <= number <= high:
        raise MusicError(f"{label} deve estar entre {low} e {high}")
    return number


class AceStepClient:
    """Small, fixed-origin client for the ACE-Step async REST contract."""
    def __init__(self, port=None):
        self.port = _int(port if port is not None else os.environ.get("DREAM_FABRICS_ACE_PORT", "8001"), 1, 65535, "Porta ACE-Step")
        self.base = f"http://127.0.0.1:{self.port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, path, payload=None, binary=False, multipart=None):
        if not path.startswith("/") or path.startswith("//"):
            raise MusicError("Caminho ACE-Step inválido")
        headers = {}
        key = os.environ.get("ACESTEP_API_KEY")
        if key:
            headers["Authorization"] = "Bearer " + key
        data = None
        if multipart is not None:
            boundary = "dreamfabrics" + uuid.uuid4().hex
            parts = []
            for name, value in multipart[0].items():
                parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode(), b'\r\n'])
            parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="reference_audio"; filename="reference.{multipart[2]}"\r\nContent-Type: {mimetypes.guess_type("reference."+multipart[2])[0]}\r\n\r\n'.encode(), multipart[1], b'\r\n', f'--{boundary}--\r\n'.encode()])
            data = b''.join(parts)
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        try:
            with self.opener.open(request, timeout=15 if not binary else 120) as response:
                raw = response.read(120_000_001 if binary else 2_000_001)
        except urllib.error.HTTPError as exc:
            raise MusicError(f"ACE-Step respondeu HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise MusicError("ACE-Step não está disponível em 127.0.0.1:" + str(self.port)) from None
        if binary:
            if not raw or len(raw) > 120_000_000:
                raise MusicError("Áudio vazio ou grande demais")
            return raw
        if len(raw) > 2_000_000:
            raise MusicError("Resposta ACE-Step grande demais")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise MusicError("Resposta ACE-Step inválida") from exc
        if not isinstance(envelope, dict) or envelope.get("code") != 200:
            raise MusicError("ACE-Step: " + str(envelope.get("error", "resposta inválida"))[:200])
        return envelope.get("data")

    def health(self):
        try:
            data = self._request("/health")
            return {"available": isinstance(data, dict) and data.get("status") == "ok", "port": self.port}
        except MusicError:
            return {"available": False, "port": self.port}

    def submit(self, track, reference_path=None):
        oriented_prompt = track["prompt"]
        if track["mood"]:
            oriented_prompt += "\nMood: " + track["mood"]
        if track["structure"]:
            oriented_prompt += "\nStructure: " + track["structure"]
        payload = {"prompt": oriented_prompt, "lyrics": track["lyrics"],
                   "audio_duration": track["duration"], "vocal_language": track["language"],
                   "audio_format": "wav", "batch_size": 1,
                   "thinking": False, "sample_mode": False, "use_format": False,
                   "use_random_seed": False, "seed": track["seed"],
                   "inference_steps": track["steps"]}
        for source, target in (("bpm", "bpm"), ("key_scale", "key_scale"),
                               ("time_signature", "time_signature")):
            if track[source]:
                payload[target] = track[source]
        payload["task_type"] = "text2music"
        if reference_path:
            wire = {key: json.dumps(value) if isinstance(value, bool) else value for key, value in payload.items()}
            result = self._request("/release_task", multipart=(wire, reference_path.read_bytes(), reference_path.suffix[1:]))
        else:
            result = self._request("/release_task", payload)
        task_id = result.get("task_id") if isinstance(result, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", task_id):
            raise MusicError("ACE-Step não devolveu um identificador de tarefa válido")
        return task_id

    def query(self, task_id):
        result = self._request("/query_result", {"task_id_list": [task_id]})
        if not isinstance(result, list) or not result:
            raise MusicError("ACE-Step não devolveu estado da tarefa")
        row = next((r for r in result if isinstance(r, dict) and r.get("task_id") == task_id), None)
        if row is None or row.get("status") not in (0, 1, 2):
            raise MusicError("Estado da tarefa ACE-Step inválido")
        return row

    def download(self, file_ref):
        # The API may return a relative URL. Never follow an absolute URL or
        # redirect to another host, even if it appears in model output.
        if not isinstance(file_ref, str):
            raise MusicError("Referência de áudio inválida")
        parsed = urlsplit(file_ref)
        if parsed.scheme or parsed.netloc or parsed.path != "/v1/audio" or not parsed.query.startswith("path="):
            raise MusicError("ACE-Step devolveu uma referência de áudio inesperada")
        return self._request(file_ref, binary=True)


class MusicStudio:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or AceStepClient()
        self.lock = threading.RLock()

    def _paths(self, project_id):
        project = self.store._dir(project_id)
        music = project / "music"
        music.mkdir(exist_ok=True)
        return music, music / "tracks.json"

    def _load(self, project_id):
        _, path = self._paths(project_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise MusicError("Catálogo musical inválido")
        return data

    def _save(self, project_id, tracks):
        _, path = self._paths(project_id)
        atomic_json(path, tracks)

    def list(self, project_id, refresh=False):
        with self.lock:
            tracks = self._load(project_id)
            if refresh:
                changed = False
                for track in tracks:
                    if track["status"] in ("queued", "running"):
                        try:
                            row = self.client.query(track["task_id"])
                            if row["status"] == 0:
                                track["status"] = "running"
                            elif row["status"] == 2:
                                track["status"] = "failed"
                                track["error"] = "Geração falhou no ACE-Step"
                            else:
                                self._complete(project_id, track, row)
                            changed = True
                        except MusicError as exc:
                            track["error"] = str(exc)
                            changed = True
                if changed:
                    self._save(project_id, tracks)
            return tracks

    def _complete(self, project_id, track, row):
        raw = row.get("result")
        try:
            results = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError as exc:
            raise MusicError("Resultado ACE-Step inválido") from exc
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise MusicError("ACE-Step não devolveu áudio")
        audio = self.client.download(results[0].get("file"))
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise MusicError("ACE-Step não devolveu um WAV válido")
        music, _ = self._paths(project_id)
        name = f"{track['id']}-{len(track['takes'])+1:02d}.wav"
        temporary = music / (name + ".tmp")
        temporary.write_bytes(audio)
        os.replace(temporary, music / name)
        track["takes"].append({"name": name, "created_at": time.time(), "url": f"/api/projects/{project_id}/music/audio/{name}"})
        track["status"] = "done"
        track["error"] = ""

    def save_draft(self, project_id, data, track_id=None):
        with self.lock:
            tracks = self._load(project_id)
            track = next((t for t in tracks if t["id"] == track_id), None) if track_id else None
            if track_id and track is None:
                raise MusicError("Faixa não encontrada")
            if track and track["status"] in ("queued", "running"):
                raise MusicError("Aguarde a geração atual terminar")
            title = str(data.get("title", "")).strip()[:180]
            prompt = str(data.get("prompt", "")).strip()[:3000]
            lyrics = str(data.get("lyrics", ""))[:15000]
            structure = str(data.get("structure", "")).strip()[:1000]
            mood = str(data.get("mood", "")).strip()[:300]
            if not title or not prompt:
                raise MusicError("Informe título e intenção musical")
            duration = _int(data.get("duration", 30), 10, 600, "Duração")
            bpm = data.get("bpm")
            bpm = _int(bpm, 30, 300, "BPM") if bpm not in (None, "") else None
            seed = _int(data.get("seed", 42), 0, 2**31-1, "Semente")
            steps = _int(data.get("steps", 8), 1, 20, "Passos")
            key = str(data.get("key_scale", "")).strip()[:40]
            signature = str(data.get("time_signature", "")).strip()
            if signature and signature not in ("2", "3", "4", "6"):
                raise MusicError("Compasso deve ser 2, 3, 4 ou 6")
            language = str(data.get("language", "pt")).strip().lower()
            if not re.fullmatch(r"[a-z]{2,3}", language):
                raise MusicError("Idioma de vocal inválido")
            chapter = str(data.get("chapter", ""))
            if chapter and chapter not in self.store.get(project_id)["manifest"]["chapters"]:
                raise MusicError("Capítulo associado inválido")
            values = {"title": title, "prompt": prompt, "lyrics": lyrics,
                      "structure": structure, "mood": mood, "duration": duration,
                      "bpm": bpm, "seed": seed, "steps": steps, "key_scale": key,
                      "time_signature": signature, "language": language, "chapter": chapter}
            if track:
                track.update(values)
                track["status"] = "draft"
                track["error"] = ""
            else:
                track = {"id": uuid.uuid4().hex, **values, "status": "draft",
                         "task_id": None, "takes": [], "error": "", "style_profile": None}
                tracks.append(track)
            self._save(project_id, tracks)
            return track

    def generate(self, project_id, track_id, consent):
        if consent is not True:
            raise MusicError("Confirme a produção musical local")
        with self.lock:
            tracks = self._load(project_id)
            track = next((t for t in tracks if t["id"] == track_id), None)
            if not track:
                raise MusicError("Faixa não encontrada")
            if track["status"] in ("queued", "running"):
                raise MusicError("Esta faixa já está em produção")
            if not self.client.health()["available"]:
                raise MusicError("Inicie a API local do ACE-Step antes de produzir")
            reference = track.get("reference")
            reference_path = self.reference_file(project_id, track_id) if reference else None
            task_id = self.client.submit(track, reference_path)
            track["task_id"] = task_id
            track["status"] = "queued"
            track["error"] = ""
            self._save(project_id, tracks)
            return track

    def save_reference(self, project_id, track_id, data, original_name=""):
        kind = inspect_reference(data)
        with self.lock:
            tracks = self._load(project_id)
            track = next((t for t in tracks if t["id"] == track_id), None)
            if not track:
                raise MusicError("Faixa não encontrada")
            if track["status"] in ("queued", "running"):
                raise MusicError("Aguarde a geração atual terminar")
            music, _ = self._paths(project_id)
            name = f"{track_id}-reference-{uuid.uuid4().hex}.{kind}"
            temporary = music / (name + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, music / name)
            old = track.get("reference")
            track["reference"] = {"name": name, "format": kind, "size": len(data),
                                  "label": Path(unquote(original_name).replace("\\", "/")).name[:180],
                                  "url": f"/api/projects/{project_id}/music/reference/{name}"}
            try:
                self._save(project_id, tracks)
            except Exception:
                (music / name).unlink(missing_ok=True)
                raise
            if old and re.fullmatch(rf"{track_id}-reference-[a-f0-9]{{32}}\.(?:mp3|wav)", old.get("name", "")):
                (music / old["name"]).unlink(missing_ok=True)
            return track

    def reference_file(self, project_id, track_id, name=None):
        if not re.fullmatch(r"[a-f0-9]{32}", track_id or ""):
            raise MusicError("Faixa inválida")
        tracks = self._load(project_id)
        track = next((t for t in tracks if t["id"] == track_id), None)
        reference = track.get("reference") if track else None
        if not reference or (name and name != reference.get("name")):
            raise MusicError("Referência não encontrada")
        filename = reference.get("name", "")
        if not re.fullmatch(rf"{track_id}-reference-[a-f0-9]{{32}}\.(?:mp3|wav)", filename):
            raise MusicError("Referência inválida")
        target = self._paths(project_id)[0] / filename
        if not target.is_file():
            raise MusicError("Referência não encontrada")
        return target

    def audio_file(self, project_id, name):
        if not re.fullmatch(r"[a-f0-9]{32}-[0-9]{2}\.wav", name or ""):
            raise MusicError("Arquivo musical inválido")
        tracks = self._load(project_id)
        if not any(take["name"] == name for track in tracks for take in track["takes"]):
            raise MusicError("Áudio não encontrado")
        target = self._paths(project_id)[0] / name
        if not target.is_file():
            raise MusicError("Áudio não encontrado")
        return target
