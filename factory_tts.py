"""Replaceable speech providers and resumable chapter rendering."""
import io
import hashlib
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
import wave
import zipfile
from pathlib import Path

from factory_store import atomic_json

import atlas

VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar")


class TTSError(ValueError):
    pass


def segments(text, limit=3500):
    """Split on paragraph/sentence/space boundaries, always under API's 4096 chars."""
    if not text.strip():
        raise TTSError("O capítulo não tem texto para narrar")
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    result, current = [], ""
    for piece in pieces:
        if not piece:
            continue
        words = re.findall(r"\S+\s*", piece)
        for word in words:
            while len(word) > limit:
                if current:
                    result.append(current.strip()); current = ""
                result.append(word[:limit]); word = word[limit:]
            if len(current) + len(word) > limit and current:
                result.append(current.strip()); current = ""
            current += word
        current += " "
    if current.strip():
        result.append(current.strip())
    return result


class OpenAISpeech:
    name = "openai"
    model = "gpt-4o-mini-tts-2025-12-15"

    def configured(self):
        return bool(os.environ.get("OPENAI_API_KEY"))

    def synthesize(self, text, voice, fmt, speed, instructions):
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise TTSError("Defina OPENAI_API_KEY no ambiente antes de iniciar o aplicativo")
        if voice not in VOICES or fmt not in ("mp3", "wav") or not 0.25 <= speed <= 4:
            raise TTSError("Voz, formato ou velocidade inválidos")
        payload = {"model": self.model, "voice": voice, "input": text,
                   "response_format": fmt, "speed": speed}
        if instructions:
            payload["instructions"] = instructions[:4096]
        req = urllib.request.Request("https://api.openai.com/v1/audio/speech",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                data = response.read(25_000_001)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read(2048).decode("utf-8"))
                message = detail.get("error", {}).get("message", "Falha no serviço de voz")
            except Exception:
                message = "Falha no serviço de voz"
            raise TTSError(f"OpenAI HTTP {exc.code}: {message}") from None
        except urllib.error.URLError as exc:
            raise TTSError("Não foi possível conectar ao serviço de voz") from exc
        if len(data) > 25_000_000 or not data:
            raise TTSError("Resposta de áudio vazia ou grande demais")
        return data


PROVIDERS = {"openai": OpenAISpeech()}


def combine_wav(chunks):
    output = io.BytesIO()
    spec = None
    with wave.open(output, "wb") as writer:
        for chunk in chunks:
            with wave.open(io.BytesIO(chunk), "rb") as reader:
                current = reader.getparams()[:4]
                if spec is None:
                    spec = current
                    writer.setnchannels(reader.getnchannels())
                    writer.setsampwidth(reader.getsampwidth())
                    writer.setframerate(reader.getframerate())
                elif current != spec:
                    raise TTSError("Os segmentos WAV têm formatos incompatíveis")
                writer.writeframes(reader.readframes(reader.getnframes()))
    return output.getvalue()


def combine_audio(chunks, fmt):
    if fmt == "wav":
        return combine_wav(chunks)
    # MP3 frames can be concatenated for chapter playback; some players may show
    # an incorrect duration. ffmpeg is used for a final book export when present.
    return b"".join(chunks)


class Production:
    def __init__(self, store):
        self.store = store
        self.jobs = {}
        self.lock = threading.Lock()

    def status(self, project_id):
        return self.jobs.get(project_id, {"state": "idle", "completed": 0, "total": 0})

    def start(self, project_id, names, options):
        project = self.store.get(project_id)
        if not options.get("consent"):
            raise TTSError("Confirme o envio do texto ao provedor antes de produzir")
        provider = PROVIDERS.get(options.get("provider", "openai"))
        if not provider:
            raise TTSError("Provedor indisponível")
        if not provider.configured():
            raise TTSError("Configure OPENAI_API_KEY no ambiente")
        available = {chapter["name"] for chapter in project["chapters"]}
        if not names or any(name not in available for name in names):
            raise TTSError("Selecione capítulos válidos")
        fmt = options.get("format", "mp3")
        voice = options.get("voice", "coral")
        speed = float(options.get("speed", 1))
        instructions = str(options.get("instructions", ""))
        if fmt not in ("mp3", "wav") or voice not in VOICES or not 0.25 <= speed <= 4:
            raise TTSError("Opções de voz inválidas")
        with self.lock:
            if self.jobs.get(project_id, {}).get("state") == "running":
                raise TTSError("Já existe uma produção em andamento")
            job = {"state": "running", "completed": 0, "total": len(names), "current": "", "error": "", "failed": []}
            self.jobs[project_id] = job
        thread = threading.Thread(target=self._run, args=(project_id, names, provider, fmt, voice, speed, instructions, job), daemon=True)
        thread.start()
        return job

    def _run(self, project_id, names, provider, fmt, voice, speed, instructions, job):
        path = self.store._dir(project_id)
        project = self.store.get(project_id)
        chapters = {chapter["name"]: chapter for chapter in project["chapters"]}
        for name in names:
            job["current"] = name
            try:
                text = "\n\n".join(atlas.blocks_to_narration(atlas.parse_blocks(chapters[name]["text"])))
                parts = segments(text)
                target = path / "audio" / (Path(name).stem + "." + fmt)
                # Segment cache lets a retry keep successfully generated parts.
                cache = path / "audio" / (Path(name).stem + ".parts")
                cache.mkdir(exist_ok=True)
                digest = hashlib.sha256((voice + fmt + str(speed) + instructions + text).encode()).hexdigest()[:16]
                audio = []
                for idx, part in enumerate(parts):
                    item = cache / f"{digest}-{idx:03d}.{fmt}"
                    if not item.exists():
                        data = provider.synthesize(part, voice, fmt, speed, instructions)
                        tmp = item.with_suffix(item.suffix + ".tmp")
                        tmp.write_bytes(data)
                        os.replace(tmp, item)
                    audio.append(item.read_bytes())
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_bytes(combine_audio(audio, fmt))
                os.replace(tmp, target)
                atomic_json(target.with_suffix(target.suffix + ".json"),
                            {"source_sha256": hashlib.sha256(chapters[name]["text"].encode("utf-8")).hexdigest(),
                             "provider": provider.name if hasattr(provider, "name") else "test", "voice": voice,
                             "format": fmt, "speed": speed})
                job["completed"] += 1
            except Exception as exc:
                job["failed"].append(name)
                job["error"] = str(exc) if isinstance(exc, TTSError) else "Erro inesperado ao produzir áudio"
        job["state"] = "failed" if job["failed"] else "done"
        job["current"] = ""

    def export_zip(self, project_id):
        path = self.store._dir(project_id)
        project = self.store.get(project_id)
        files = []
        for chapter in project["chapters"]:
            for entry in chapter["audio"]:
                audio = path / "audio" / (Path(chapter["name"]).stem + "." + entry["format"])
                if audio.is_file():
                    files.append(audio)
        if not files:
            raise TTSError("Ainda não há áudio para exportar")
        target = path / "audio" / "capitulos.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in files:
                archive.write(file, file.name)
        return target
