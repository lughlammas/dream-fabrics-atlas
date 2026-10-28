"""Local persistence for audiobook projects. No network access."""
import json
import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

import atlas


class StoreError(ValueError):
    pass


def slug(text):
    value = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return value[:48] or "livro"


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class FactoryStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, project_id):
        if not re.fullmatch(r"[a-f0-9]{32}", project_id or ""):
            raise StoreError("Projeto inválido")
        path = self.root / project_id
        if not path.is_dir():
            raise StoreError("Projeto não encontrado")
        return path

    def create(self, title, author, language, description="", cover=None):
        title, author, language = (str(x).strip() for x in (title, author, language))
        if not title or not author or not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", language):
            raise StoreError("Informe título, autor e idioma válido")
        project_id = uuid.uuid4().hex
        path = self.root / project_id
        path.mkdir()
        (path / "chapters").mkdir()
        (path / "originals").mkdir()
        (path / "audio").mkdir()
        manifest = {"atlas_version": "1.0", "id": slug(title) + "-" + project_id[:6],
                    "title": title, "author": author, "language": language,
                    "description": str(description).strip()[:2000], "chapters": [],
                    "outputs": ["txt", "epub", "ragnarok"], "factory_id": project_id,
                    "cover": None}
        if cover:
            manifest["cover"] = self._save_cover(path, cover)
        atomic_json(path / "manifest.json", manifest)
        return self.get(project_id)

    def _save_cover(self, path, cover):
        import base64
        if not isinstance(cover, str) or not cover.startswith("data:image/"):
            raise StoreError("Capa inválida")
        header, encoded = cover.split(",", 1)
        ext = {"data:image/jpeg;base64": ".jpg", "data:image/png;base64": ".png", "data:image/webp;base64": ".webp"}.get(header)
        if not ext:
            raise StoreError("Use capa JPG, PNG ou WebP")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise StoreError("Capa inválida") from exc
        if len(data) > 5_000_000:
            raise StoreError("A capa deve ter até 5 MB")
        name = "cover" + ext
        (path / name).write_bytes(data)
        return name

    def list(self):
        projects = []
        for path in self.root.iterdir():
            if path.is_dir() and (path / "manifest.json").exists():
                try:
                    projects.append(self.get(path.name))
                except (StoreError, ValueError, OSError):
                    continue
        return sorted(projects, key=lambda p: p["updated_at"], reverse=True)

    def get(self, project_id):
        path = self._dir(project_id)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        chapters = []
        for name in manifest["chapters"]:
            if not re.fullmatch(r"[a-zA-Z0-9_-]+\.(?:md|txt)", name):
                raise StoreError("Nome de capítulo inválido")
            text = (path / "chapters" / name).read_text(encoding="utf-8")
            audio = []
            for fmt in ("mp3", "wav"):
                audio_path = path / "audio" / (Path(name).stem + "." + fmt)
                meta_path = audio_path.with_suffix(audio_path.suffix + ".json")
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
                if audio_path.exists() and meta.get("source_sha256") == hashlib.sha256(text.encode("utf-8")).hexdigest():
                    audio.append({"format": fmt, "url": f"/api/projects/{project_id}/audio/{audio_path.name}"})
            chapters.append({"name": name, "title": atlas.first_heading(atlas.parse_blocks(text)) or Path(name).stem,
                             "text": text, "audio": audio})
        return {"id": project_id, "manifest": manifest, "chapters": chapters,
                "updated_at": (path / "manifest.json").stat().st_mtime,
                "cover_url": f"/api/projects/{project_id}/cover" if manifest.get("cover") else None}

    def update(self, project_id, fields):
        path = self._dir(project_id)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        for key in ("title", "author", "language"):
            if key in fields:
                value = str(fields[key]).strip()
                if not value:
                    raise StoreError(f"{key} não pode ficar vazio")
                manifest[key] = value
        if "description" in fields:
            manifest["description"] = str(fields["description"]).strip()[:2000]
        if "cover" in fields and fields["cover"]:
            manifest["cover"] = self._save_cover(path, fields["cover"])
        atomic_json(path / "manifest.json", manifest)
        return self.get(project_id)

    def add_chapter(self, project_id, title, text):
        path = self._dir(project_id)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        title = str(title).strip() or f"Capítulo {len(manifest['chapters']) + 1}"
        name = f"{len(manifest['chapters'])+1:03d}_{slug(title)}_{uuid.uuid4().hex[:5]}.md"
        content = str(text).strip()
        if not content:
            content = f"# {title}\n\n"
        (path / "chapters" / name).write_text(content, encoding="utf-8")
        manifest["chapters"].append(name)
        atomic_json(path / "manifest.json", manifest)
        return self.get(project_id)

    def save_chapter(self, project_id, name, text):
        path = self._dir(project_id)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if name not in manifest["chapters"]:
            raise StoreError("Capítulo não encontrado")
        (path / "chapters" / name).write_text(str(text), encoding="utf-8")
        atomic_json(path / "manifest.json", manifest)
        return self.get(project_id)

    def reorder(self, project_id, names):
        path = self._dir(project_id)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if len(names) != len(manifest["chapters"]) or set(names) != set(manifest["chapters"]):
            raise StoreError("Lista de capítulos inválida")
        manifest["chapters"] = names
        atomic_json(path / "manifest.json", manifest)
        return self.get(project_id)

    def import_text(self, project_id, filename, content):
        path = self._dir(project_id)
        if not filename.lower().endswith((".txt", ".md")):
            raise StoreError("Importação disponível para TXT e Markdown")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 10_000_000:
            raise StoreError("Arquivo grande demais (máximo 10 MB)")
        source = path / "originals" / (uuid.uuid4().hex + Path(filename).suffix.lower())
        source.write_text(content, encoding="utf-8")
        parts = re.split(r"(?=^#{1,2} .+$)", content, flags=re.MULTILINE) if filename.lower().endswith(".md") else [content]
        parts = [part.strip() for part in parts if part.strip()]
        if not parts:
            raise StoreError("Arquivo vazio")
        if len(parts) > 200:
            raise StoreError("Máximo de 200 capítulos por importação")
        for index, part in enumerate(parts, 1):
            title = atlas.first_heading(atlas.parse_blocks(part)) or f"Parte {index}"
            self.add_chapter(project_id, title, part)
        return self.get(project_id)

    def file(self, project_id, category, name=None):
        path = self._dir(project_id)
        if category == "cover":
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            name = manifest.get("cover")
            target = path / name if name else None
        elif category == "audio" and name and re.fullmatch(r"[a-zA-Z0-9_-]+\.(?:mp3|wav|zip|m4b)", name):
            target = path / "audio" / name
        else:
            raise StoreError("Arquivo inválido")
        if not target or not target.is_file():
            raise StoreError("Arquivo não encontrado")
        return target
