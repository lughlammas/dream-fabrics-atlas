"""Testes de tabelas Markdown, \\newpage & cia e marcador [PREENCHER]."""
import os
import shutil
import subprocess
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_atlas import Base, ROOT, tree_hash, _find_epubcheck  # noqa: E402,F401
import atlas  # noqa: E402

TABLE_MD = (
    "# Tabelas\n\nAntes da tabela.\n\n"
    "| Alimento | Energia | Nota |\n"
    "|:---|---:|:---:|\n"
    "| Ovo <b> & cia | 146 kcal | `a\\|b` |\n"
    "| Milho | 138 kcal | **ok** |\n\n"
    "Depois da tabela.\n"
)


def have_weasyprint():
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


class TableParsing(unittest.TestCase):
    def test_find_table_alignment_and_rows(self):
        lines = TABLE_MD.split("\n")
        start = lines.index("| Alimento | Energia | Nota |")
        headers, aligns, rows, nxt = atlas.find_table(lines, start)
        self.assertEqual(headers, ["Alimento", "Energia", "Nota"])
        self.assertEqual(aligns, ["left", "right", "center"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], "`a|b`")  # pipe escapado em codigo nao divide
        self.assertEqual(lines[nxt], "")

    def test_pipes_in_prose_are_not_a_table(self):
        self.assertIsNone(atlas.find_table(["a | b", "texto normal"], 0))
        self.assertIsNone(atlas.find_table(["a | b", "--- | ---x"], 0))
        self.assertIsNone(atlas.find_table(["a | b | c", "---|---"], 0))  # colunas diferentes

    def test_ragged_rows_are_padded_and_truncated(self):
        _h, _a, rows, _n = atlas.find_table(["a|b", "-|-", "1|", "1|2|3"], 0)
        self.assertEqual(rows, [["1", ""], ["1", "2"]])

    def test_table_interrupts_paragraph_and_blocks(self):
        blocks = atlas.parse_blocks("texto\n| a | b |\n|---|---|\n| 1 | 2 |\nfim\n")
        self.assertEqual([b[0] for b in blocks], ["p", "table", "p"])


class TableOutputs(Base):
    def setUp(self):
        super().setUp()
        self.write_chapter("01_capitulo_um.md", TABLE_MD.encode("utf-8"))

    def build(self):
        r = self.run_cli("build", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def out(self, name):
        with open(os.path.join(self.proj, "build", name), encoding="utf-8") as fh:
            return fh.read()

    def test_epub_has_real_table(self):
        self.build()
        with zipfile.ZipFile(os.path.join(self.proj, "build", "livro_exemplo.epub")) as zf:
            ch = zf.read("OEBPS/chap002.xhtml").decode("utf-8")
            css = zf.read("OEBPS/style.css").decode("utf-8")
        self.assertIn("<table>", ch)
        self.assertIn("<thead>", ch)
        self.assertIn("<tbody>", ch)
        self.assertIn("<th>Alimento</th>", ch)
        self.assertIn('<th style="text-align:right">Energia</th>', ch)
        self.assertIn('<td style="text-align:center"><strong>ok</strong></td>', ch)
        self.assertIn("Ovo &lt;b&gt; &amp; cia", ch)  # escape HTML
        self.assertIn("<code>a|b</code>", ch)  # \\| em codigo vira | literal
        self.assertNotIn("|---", ch)
        self.assertNotIn("| Alimento", ch)
        self.assertIn("border", css)

    @unittest.skipUnless(_find_epubcheck(), "EPUBCheck nao configurado")
    def test_epubcheck_with_table(self):
        java, jar = _find_epubcheck()
        self.manifest(outputs=["epub"])
        self.build()
        r = subprocess.run([java, "-jar", jar, os.path.join(self.proj, "build", "livro_exemplo.epub")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No errors or warnings detected", r.stdout + r.stderr)

    def test_txt_aligned_without_pipes(self):
        self.build()
        txt = self.out("livro_exemplo.txt")
        lines = txt.split("\n")
        self.assertFalse([l for l in lines if l.startswith("|")])
        self.assertNotIn("|---", txt)
        i = next(k for k, l in enumerate(lines) if l.startswith("Alimento"))
        self.assertTrue(set(lines[i + 1]) <= {"-", " "})
        # colunas alinhadas: "Energia" comeca na mesma coluna em todas as linhas (alinhada a direita)
        ends = {lines[i].index("Energia") + len("Energia"),
                lines[i + 2].index("146 kcal") + len("146 kcal"),
                lines[i + 3].index("138 kcal") + len("138 kcal")}
        self.assertEqual(len(ends), 1)
        self.assertEqual(lines[i - 1], "")  # linha em branco antes
        self.assertIn("Depois da tabela.", txt)
        self.assertIn("Ovo <b> & cia", txt)

    def test_txt_keeps_table_inside_code_fence(self):
        fenced = "# T\n\n```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n"
        self.write_chapter("01_capitulo_um.md", fenced.encode())
        self.build()
        self.assertIn("| a | b |\n|---|---|\n| 1 | 2 |", self.out("livro_exemplo.txt"))

    def test_ragnarok_sentences(self):
        self.build()
        aud = self.out("audiobook.txt")
        self.assertFalse([l for l in aud.split("\n") if l.startswith("|")])
        self.assertNotIn("|---", aud)
        self.assertIn("Alimento: Ovo & cia, Energia: 146 kcal, Nota: a|b.", aud)  # tag <b> removida na narracao
        self.assertIn("Alimento: Milho, Energia: 138 kcal, Nota: ok.", aud)
        self.assertIn("Antes da tabela.\n\n" + atlas.PAUSE_SHORT + "\n\nAlimento: Ovo & cia", aud)
        self.assertIn("kcal, Nota: a|b.\nAlimento: Milho", aud)  # linhas consecutivas
        self.assertIn("Depois da tabela.", aud)

    def test_pdf_has_table_or_skipped(self):
        self.manifest(outputs=["pdf"])
        self.build()
        pdf = os.path.join(self.proj, "build", "livro_exemplo.pdf")
        if not have_weasyprint():
            self.assertFalse(os.path.exists(pdf))
            return
        with open(pdf, "rb") as fh:
            self.assertEqual(fh.read(5), b"%PDF-")
        if shutil.which("pdftotext"):
            text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True,
                                  text=True).stdout
            self.assertIn("Alimento", text)
            self.assertIn("146 kcal", text)
            self.assertIn("Milho", text)
            self.assertFalse([l for l in text.split("\n") if l.startswith("| ")])
            self.assertNotIn("|---", text)

    def test_chapters_untouched_by_table_build(self):
        before = tree_hash(os.path.join(self.proj, "chapters"))
        self.build()
        self.assertEqual(tree_hash(os.path.join(self.proj, "chapters")), before)


class PageBreakTests(Base):
    CHAPTER = ("# Um\n\nPrimeiro.\n\\newpage\nSegundo colado.\n\n\\pagebreak\n\n\\clearpage\n\n"
               "Terceiro com \\newpage no meio.\n\n```\n\\newpage\n```\n")

    def setUp(self):
        super().setUp()
        self.write_chapter("01_capitulo_um.md", self.CHAPTER.encode())
        r = self.run_cli("build", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def out(self, name):
        with open(os.path.join(self.proj, "build", name), encoding="utf-8") as fh:
            return fh.read()

    def test_is_pagebreak(self):
        for ok in ("\\newpage", "  \\pagebreak ", "\\clearpage", "\\cleardoublepage", "\\newpage{}"):
            self.assertTrue(atlas.is_pagebreak(ok), ok)
        for bad in ("texto \\newpage", "\\newpages", "newpage", "\\newline"):
            self.assertFalse(atlas.is_pagebreak(bad), bad)

    def test_txt_blank_line_not_text(self):
        txt = self.out("livro_exemplo.txt")
        chapter = txt.split("# Um", 1)[1]
        # a unica ocorrencia literal e a do bloco de codigo
        self.assertEqual(chapter.count("\\newpage"), 1)
        self.assertIn("Terceiro com no meio.", chapter)
        self.assertNotIn("\\pagebreak", chapter)
        self.assertNotIn("\\clearpage", chapter)
        self.assertIn("Primeiro.\n\nSegundo colado.", chapter)

    def test_ragnarok_long_pause(self):
        aud = self.out("audiobook.txt")
        self.assertNotIn("\\pagebreak", aud)
        self.assertNotIn("\\clearpage", aud)
        self.assertEqual(aud.count("newpage"), 1)  # so o do bloco de codigo (literal)
        self.assertIn("Primeiro.\n\n" + atlas.PAUSE_LONG + "\n\nSegundo colado.", aud)
        self.assertEqual(aud.count(atlas.PAUSE_LONG + "\n\n" + atlas.PAUSE_LONG), 0)  # sem pausas duplicadas
        self.assertIn("Terceiro com no meio.", aud)  # comando no meio da linha e removido

    def test_epub_page_break_div_not_text(self):
        with zipfile.ZipFile(os.path.join(self.proj, "build", "livro_exemplo.epub")) as zf:
            ch = zf.read("OEBPS/chap002.xhtml").decode("utf-8")
            css = zf.read("OEBPS/style.css").decode("utf-8")
        self.assertIn('<div class="pagebreak"></div>', ch)
        # \\newpage + (\\pagebreak, \\clearpage seguidos, que viram 1) = 2 quebras
        self.assertEqual(ch.count('<div class="pagebreak"></div>'), 2)
        self.assertIn("page-break-after:always", css)
        self.assertNotIn("pagebreak</p>", ch)
        self.assertNotIn("\\pagebreak", ch)
        self.assertNotIn("\\clearpage", ch)
        # unica ocorrencia literal: dentro do bloco de codigo
        self.assertEqual(ch.count("\\newpage"), 1)
        self.assertIn("Terceiro com no meio.", ch)
        self.assertIn("<pre><code>\\newpage</code></pre>", ch)

    @unittest.skipUnless(have_weasyprint() and shutil.which("pdftotext"), "WeasyPrint/pdftotext ausentes")
    def test_pdf_page_break(self):
        pdf = os.path.join(self.proj, "build", "livro_exemplo.pdf")
        text = subprocess.run(["pdftotext", pdf, "-"], capture_output=True, text=True).stdout
        self.assertNotIn("pagebreak", text)
        self.assertNotIn("clearpage", text)
        # "Primeiro." e "Segundo colado." ficam em paginas diferentes
        pages = text.split("\f")
        p1 = [k for k, p in enumerate(pages) if "Primeiro." in p]
        p2 = [k for k, p in enumerate(pages) if "Segundo colado." in p]
        self.assertTrue(p1 and p2 and p1[0] < p2[0])


class PendingMarkerTests(Base):
    def test_author_placeholder_fails(self):
        self.manifest(author="[PREENCHER — dado do autor]")
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("metadado pendente: author", r.stderr)
        self.assertIn("[PREENCHER", r.stderr)

    def test_case_insensitive_and_other_fields(self):
        for field, value in (("title", "Titulo [preencher]"), ("author", "Fulano [Preencher: nome]"),
                             ("id", "[PREENCHER]"), ("language", "pt-[PREENCHER]"),
                             ("outputs", ["txt", "[PREENCHER]"]), ("volume", "[PREENCHER]")):
            with self.subTest(field=field):
                shutil.rmtree(self.proj)
                shutil.copytree(os.path.join(ROOT, "examples", "livro-exemplo"), self.proj)
                self.manifest(**{field: value})
                r = self.run_cli("validate", self.proj)
                self.assertEqual(r.returncode, 1)
                self.assertIn("metadado pendente: " + field, r.stderr)

    def test_unknown_extra_field_also_checked(self):
        self.manifest(editora="[PREENCHER — editora]")
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("metadado pendente: editora", r.stderr)

    def test_build_refuses_pending_metadata(self):
        self.manifest(author="[PREENCHER]")
        r = self.run_cli("build", self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "build")))

    def test_chapter_marker_is_warning_only(self):
        self.write_chapter("01_capitulo_um.md",
                           "# Um\n\n**[PREENCHER — dado do autor]**\n\n- [ ] Respondi o bloco [preencher].\n"
                           "\nMais um [ PREENCHER ].\n".encode())
        r = self.run_cli("validate", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AVISO: chapters/01_capitulo_um.md contem 3 marcador(es) [PREENCHER]", r.stderr)
        self.assertNotIn("ERRO", r.stderr)
        b = self.run_cli("build", self.proj)
        self.assertEqual(b.returncode, 0, b.stderr)  # build continua possivel
        self.assertIn("3 marcador(es)", b.stderr)

    def test_clean_chapters_have_no_marker_warning(self):
        r = self.run_cli("validate", self.proj)
        self.assertNotIn("PREENCHER", r.stderr)

    def test_init_template_still_fails_on_empty_author(self):
        new = os.path.join(self.tmp, "nova")
        self.assertEqual(self.run_cli("init", new).returncode, 0)
        self.assertEqual(self.run_cli("validate", new).returncode, 1)


if __name__ == "__main__":
    unittest.main()
