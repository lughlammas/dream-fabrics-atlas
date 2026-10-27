import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import atlas  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "livro-exemplo")


def tree_hash(path):
    """Hash de nomes + conteudo de todos os arquivos sob path."""
    h = hashlib.sha256()
    for base, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(base, f)
            h.update(os.path.relpath(p, path).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "livro")
        shutil.copytree(EXAMPLE, self.proj)

    def manifest(self, **changes):
        path = os.path.join(self.proj, "manifest.json")
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        for k, v in changes.items():
            if v is atlas_MISSING:
                m.pop(k, None)
            else:
                m[k] = v
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(m, fh)

    def write_chapter(self, name, data):
        with open(os.path.join(self.proj, "chapters", name), "wb") as fh:
            fh.write(data)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "atlas.py")] + list(args),
                              capture_output=True, text=True)


atlas_MISSING = object()


class ValidateTests(Base):
    def test_example_is_valid(self):
        errors, _w, _m, _t = atlas.validate_project(self.proj)
        self.assertEqual(errors, [])
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 0)

    def test_missing_chapter(self):
        os.remove(os.path.join(self.proj, "chapters", "01_capitulo_um.md"))
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("capitulo ausente", r.stderr)

    def test_duplicate_chapter_in_manifest(self):
        self.manifest(chapters=["00_abertura.md", "00_abertura.md", "01_capitulo_um.md"])
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("duplicado", r.stderr)

    def test_empty_chapter(self):
        self.write_chapter("01_capitulo_um.md", b"  \n\n")
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("vazio", r.stderr)

    def test_zero_byte_chapter(self):
        self.write_chapter("01_capitulo_um.md", b"")
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 1)

    def test_non_utf8_chapter(self):
        self.write_chapter("01_capitulo_um.md", "ação".encode("latin-1"))
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UTF-8", r.stderr)

    def test_nul_bytes_chapter(self):
        self.write_chapter("01_capitulo_um.md", b"texto\x00texto")
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 1)

    def test_missing_metadata_each_field(self):
        for field in ("id", "title", "author", "language", "chapters", "outputs"):
            with self.subTest(field=field):
                shutil.rmtree(self.proj)
                shutil.copytree(EXAMPLE, self.proj)
                self.manifest(**{field: atlas_MISSING})
                r = self.run_cli("validate", self.proj)
                self.assertEqual(r.returncode, 1)
                self.assertIn("metadado ausente: " + field, r.stderr)

    def test_empty_author_fails(self):
        self.manifest(author="")
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("author", r.stderr)

    def test_whitespace_author_fails(self):
        self.manifest(author="   ")
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 1)

    def test_unknown_output_and_bad_language(self):
        self.manifest(outputs=["txt", "docx"], language="portugues do brasil")
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("docx", r.stderr)
        self.assertIn("language", r.stderr)

    def test_path_traversal_chapter_rejected(self):
        self.manifest(chapters=["../manifest.json"])
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 1)

    def test_invalid_json_and_missing_manifest(self):
        with open(os.path.join(self.proj, "manifest.json"), "w") as fh:
            fh.write("{nao e json")
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 1)
        os.remove(os.path.join(self.proj, "manifest.json"))
        self.assertEqual(self.run_cli("validate", self.proj).returncode, 1)

    def test_missing_directory(self):
        self.assertEqual(self.run_cli("validate", os.path.join(self.tmp, "nada")).returncode, 1)

    def test_unlisted_chapter_is_warning_only(self):
        self.write_chapter("99_extra.md", b"# Extra\n\ntexto\n")
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 0)
        self.assertIn("99_extra.md", r.stderr)


class InitTests(Base):
    def test_init_creates_files_and_author_must_be_filled(self):
        new = os.path.join(self.tmp, "nova_obra")
        r = self.run_cli("init", new)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(new, "manifest.json")))
        self.assertTrue(os.path.isfile(os.path.join(new, "chapters", "00_abertura.md")))
        with open(os.path.join(new, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertEqual(m["id"], "nova_obra")
        self.assertEqual(m["author"], "")
        # autor vazio: validate falha ate o autor preencher
        self.assertEqual(self.run_cli("validate", new).returncode, 1)
        m["author"] = "Alguem"
        with open(os.path.join(new, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        self.assertEqual(self.run_cli("validate", new).returncode, 0)

    def test_init_does_not_overwrite(self):
        r = self.run_cli("init", self.proj)
        self.assertNotEqual(r.returncode, 0)

    def test_template_matches_example_file(self):
        with open(os.path.join(ROOT, "examples", "manifest.example.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), atlas.MANIFEST_TEMPLATE)


class BuildTests(Base):
    def build(self):
        r = self.run_cli("build", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_build_refuses_invalid_project(self):
        self.manifest(author="")
        r = self.run_cli("build", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "build")))

    def test_txt(self):
        self.build()
        with open(os.path.join(self.proj, "build", "livro_exemplo.txt"), encoding="utf-8") as fh:
            txt = fh.read()
        self.assertTrue(txt.startswith("Livro de Exemplo\nAutor de Exemplo\n"))
        self.assertLess(txt.index("# Abertura"), txt.index("# Capítulo um"))
        self.assertIn("**livro de exemplo**", txt)  # texto soberano preservado

    def test_order_comes_from_manifest_not_filenames(self):
        self.manifest(chapters=["01_capitulo_um.md", "00_abertura.md"])
        self.build()
        with open(os.path.join(self.proj, "build", "livro_exemplo.txt"), encoding="utf-8") as fh:
            txt = fh.read()
        self.assertLess(txt.index("# Capítulo um"), txt.index("# Abertura"))
        with open(os.path.join(self.proj, "build", "audiobook.txt"), encoding="utf-8") as fh:
            aud = fh.read()
        self.assertLess(aud.index("Capítulo um"), aud.index("Abertura"))

    def test_ragnarok(self):
        self.build()
        with open(os.path.join(self.proj, "build", "audiobook.txt"), encoding="utf-8") as fh:
            aud = fh.read()
        for syntax in ("#", "**", "`", "](", "> ", "\n- ", "\n1. ", "---"):
            self.assertNotIn(syntax, aud)
        self.assertIn("Abertura", aud)
        self.assertIn("Capítulo um", aud)
        self.assertIn("Primeiros passos", aud)
        self.assertIn("livro de exemplo", aud)
        self.assertIn("link", aud)
        self.assertNotIn("example.com", aud)
        self.assertIn("primeiro item\nsegundo item", aud)
        self.assertIn(atlas.PAUSE_LONG, aud)
        self.assertIn("\n\n" + atlas.PAUSE_SHORT + "\n\n", aud)  # linha horizontal -> pausa
        self.assertTrue(aud.startswith("Livro de Exemplo\npor Autor de Exemplo\n"))
        self.assertIn("ação, coração, vovó", aud)

    def test_epub_structure(self):
        self.build()
        path = os.path.join(self.proj, "build", "livro_exemplo.epub")
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            self.assertEqual(infos[0].filename, "mimetype")
            self.assertEqual(infos[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
            names = zf.namelist()
            for required in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml",
                             "OEBPS/chap001.xhtml", "OEBPS/chap002.xhtml"):
                self.assertIn(required, names)
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            self.assertLess(opf.index("chap001"), opf.index("chap002"))
            self.assertIn("<dc:creator>Autor de Exemplo</dc:creator>", opf)
            ch = zf.read("OEBPS/chap002.xhtml").decode("utf-8")
            self.assertIn("<strong>", zf.read("OEBPS/chap001.xhtml").decode("utf-8"))
            self.assertIn('<a href="https://example.com">link</a>', ch)
            self.assertIn("<ul>", ch)
            self.assertIn("<blockquote>", ch)
            self.assertIsNone(zf.testzip())

    def test_epub_escapes_html_in_text(self):
        self.write_chapter("01_capitulo_um.md", "# A & B <script>\n\ntexto <b>x</b> & mais\n".encode())
        self.build()
        with zipfile.ZipFile(os.path.join(self.proj, "build", "livro_exemplo.epub")) as zf:
            ch = zf.read("OEBPS/chap002.xhtml").decode("utf-8")
        self.assertNotIn("<script>", ch)
        self.assertIn("&amp;", ch)

    def test_chapters_immutable_during_build(self):
        chapters = os.path.join(self.proj, "chapters")
        before = tree_hash(chapters)
        mtimes = {f: os.stat(os.path.join(chapters, f)).st_mtime_ns for f in os.listdir(chapters)}
        self.build()
        self.build()  # segunda execucao tambem
        self.assertEqual(tree_hash(chapters), before)
        self.assertEqual(sorted(os.listdir(chapters)), sorted(mtimes))
        for f, mt in mtimes.items():
            self.assertEqual(os.stat(os.path.join(chapters, f)).st_mtime_ns, mt)

    def test_outputs_only_inside_build(self):
        before = set()
        for base, _d, files in os.walk(self.proj):
            before.update(os.path.join(base, f) for f in files)
        self.build()
        build_dir = os.path.join(self.proj, "build")
        for base, _d, files in os.walk(self.proj):
            for f in files:
                p = os.path.join(base, f)
                if p not in before:
                    self.assertTrue(p.startswith(build_dir + os.sep), p)

    def test_only_requested_outputs(self):
        self.manifest(outputs=["txt"])
        self.build()
        self.assertEqual(os.listdir(os.path.join(self.proj, "build")), ["livro_exemplo.txt"])

    def test_pdf_generated_or_clearly_skipped(self):
        self.manifest(outputs=["pdf"])
        r = self.build()
        pdf = os.path.join(self.proj, "build", "livro_exemplo.pdf")
        try:
            import weasyprint  # noqa: F401
            have = True
        except Exception:
            have = False
        if have:
            with open(pdf, "rb") as fh:
                self.assertEqual(fh.read(5), b"%PDF-")
        else:
            self.assertFalse(os.path.exists(pdf))
            self.assertIn("PULADA", r.stderr)

    def test_pdf_skipped_when_weasyprint_missing(self):
        saved = sys.modules.get("weasyprint", atlas_MISSING)
        sys.modules["weasyprint"] = None  # faz "import weasyprint" falhar
        try:
            self.assertFalse(atlas.build_pdf(atlas.MANIFEST_TEMPLATE, ["a.md"], ["# a"],
                                             os.path.join(self.tmp, "x.pdf")))
        finally:
            if saved is atlas_MISSING:
                del sys.modules["weasyprint"]
            else:
                sys.modules["weasyprint"] = saved
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.pdf")))


def _find_epubcheck():
    jar = os.environ.get("ATLAS_EPUBCHECK_JAR")
    java = os.environ.get("ATLAS_JAVA") or shutil.which("java")
    if jar and java and os.path.isfile(jar):
        return java, jar
    return None


class EpubCheckTests(Base):
    @unittest.skipUnless(_find_epubcheck(),
                         "defina ATLAS_EPUBCHECK_JAR (e java no PATH ou ATLAS_JAVA) para rodar o EPUBCheck")
    def test_epubcheck_passes(self):
        java, jar = _find_epubcheck()
        self.manifest(outputs=["epub"])
        self.assertEqual(self.run_cli("build", self.proj).returncode, 0)
        r = subprocess.run([java, "-jar", jar, os.path.join(self.proj, "build", "livro_exemplo.epub")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No errors or warnings detected", r.stdout + r.stderr)


class MarkdownTests(unittest.TestCase):
    def test_escapes_and_code(self):
        self.assertEqual(atlas.inline_plain(r"a \*b\* `c*d*`"), "a *b* c*d*")
        self.assertEqual(atlas.inline_plain(r"`a\*b`"), r"a\*b")
        self.assertEqual(atlas.inline_html("snake_case_name"), "snake_case_name")

    def test_fenced_code_block(self):
        blocks = atlas.parse_blocks("```\n# nao titulo\n```\n")
        self.assertEqual(blocks, [("code", "# nao titulo")])


if __name__ == "__main__":
    unittest.main()
