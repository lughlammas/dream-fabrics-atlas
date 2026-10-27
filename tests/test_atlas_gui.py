import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import atlas  # noqa: E402
import atlas_gui  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "livro-exemplo")


def tree_hash(path):
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

    def lines(self, command, project=None):
        got = []
        code = atlas_gui.run_atlas(command, project or self.proj, lambda s, l: got.append((s, l)))
        return code, got


class TestLogic(Base):
    def test_line_writer_splits_lines_and_flushes_rest(self):
        got = []
        w = atlas_gui.LineWriter(lambda s, l: got.append((s, l)), "out")
        w.write("a\nb")
        w.write("c\n")
        w.write("resto")
        self.assertEqual(got, [("out", "a"), ("out", "bc")])
        w.flush()
        self.assertEqual(got[-1], ("out", "resto"))

    def test_run_validate_ok(self):
        code, got = self.lines("validate")
        self.assertEqual(code, 0)
        self.assertIn(("out", "Validacao OK."), got)

    def test_run_validate_errors_go_to_err_stream(self):
        path = os.path.join(self.proj, "manifest.json")
        data = json.load(open(path, encoding="utf-8"))
        data["author"] = ""
        json.dump(data, open(path, "w", encoding="utf-8"))
        code, got = self.lines("validate")
        self.assertEqual(code, 1)
        self.assertTrue(any(s == "err" and l.startswith("ERRO") and "author" in l for s, l in got))

    def test_run_matches_cli_exit_code_and_output(self):
        import subprocess
        cli = subprocess.run([sys.executable, os.path.join(ROOT, "atlas.py"), "validate", self.proj],
                             capture_output=True, text=True)
        code, got = self.lines("validate")
        self.assertEqual(cli.returncode, code)
        self.assertEqual(cli.stdout.strip().splitlines(), [l for s, l in got if s == "out"])

    def test_run_build_creates_only_build_dir_and_keeps_chapters(self):
        before = tree_hash(os.path.join(self.proj, "chapters"))
        code, got = self.lines("build")
        self.assertEqual(code, 0)
        self.assertEqual(before, tree_hash(os.path.join(self.proj, "chapters")))
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "build", "livro_exemplo.epub")))
        self.assertTrue(any(l.startswith("gerado:") for s, l in got))
        self.assertEqual(sorted(os.listdir(self.proj)), ["build", "chapters", "manifest.json"])

    def test_run_init_and_init_refuses_existing(self):
        new = os.path.join(self.tmp, "nova")
        os.mkdir(new)
        code, _got = self.lines("init", new)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(new, "manifest.json")))
        code, got = self.lines("init", new)
        self.assertEqual(code, atlas.EXIT_ERROR)
        self.assertTrue(any("ja existe" in l for s, l in got))

    def test_run_unknown_command_does_not_raise(self):
        code, got = self.lines("xyz")
        self.assertNotEqual(code, 0)

    def test_run_restores_std_streams(self):
        out, err = sys.stdout, sys.stderr
        self.lines("validate")
        self.assertIs(sys.stdout, out)
        self.assertIs(sys.stderr, err)

    def test_run_works_when_std_streams_are_none(self):
        # pythonw.exe: sys.stdout/stderr sao None
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            code, got = self.lines("validate")
        self.assertEqual(code, 0)

    def test_load_manifest_for_edit(self):
        m, err = atlas_gui.load_manifest_for_edit(self.proj)
        self.assertIsNone(err)
        self.assertEqual(m["id"], "livro_exemplo")
        m, err = atlas_gui.load_manifest_for_edit(self.tmp)
        self.assertIsNone(m)
        self.assertIn("manifest.json", err)

    def test_build_manifest_preserves_extra_keys_and_orders_outputs(self):
        orig = {"atlas_version": "1.0", "extra": {"x": 1}, "id": "a"}
        m = atlas_gui.build_manifest(
            orig, {"id": " b ", "title": "T", "author": "A", "language": "pt-BR", "volume": "3"},
            ["x.md", "y.md"], ["ragnarok", "txt"])
        self.assertEqual(m["id"], "b")
        self.assertEqual(m["volume"], 3)
        self.assertEqual(m["outputs"], ["txt", "ragnarok"])
        self.assertEqual(m["extra"], {"x": 1})
        self.assertEqual(list(m)[:3], ["atlas_version", "id", "title"])
        self.assertEqual(m["chapters"], ["x.md", "y.md"])

    def test_build_manifest_volume_blank_removed_and_invalid_rejected(self):
        f = {"id": "a", "title": "T", "author": "A", "language": "pt-BR", "volume": ""}
        m = atlas_gui.build_manifest({"volume": 2}, f, ["a.md"], ["txt"])
        self.assertNotIn("volume", m)
        f["volume"] = "dois"
        with self.assertRaises(ValueError):
            atlas_gui.build_manifest({}, f, ["a.md"], ["txt"])

    def test_save_manifest_roundtrip_validates_and_touches_only_manifest(self):
        before = tree_hash(os.path.join(self.proj, "chapters"))
        m, _ = atlas_gui.load_manifest_for_edit(self.proj)
        m = atlas_gui.build_manifest(
            m, {"id": "livro_exemplo", "title": "Título Ação", "author": "Fulana", "language": "pt-BR",
                "volume": "1"}, ["01_capitulo_um.md", "00_abertura.md"], ["txt", "epub"])
        atlas_gui.save_manifest(self.proj, m)
        self.assertEqual(before, tree_hash(os.path.join(self.proj, "chapters")))
        self.assertEqual(sorted(os.listdir(self.proj)), ["chapters", "manifest.json"])  # sem .tmp
        raw = open(os.path.join(self.proj, "manifest.json"), encoding="utf-8").read()
        self.assertIn("Título Ação", raw)  # UTF-8 sem escape
        self.assertEqual(json.loads(raw)["chapters"], ["01_capitulo_um.md", "00_abertura.md"])
        errors, _w, _m, _t = atlas.validate_project(self.proj)
        self.assertEqual(errors, [])

    def test_save_manifest_missing_dir(self):
        with self.assertRaises(OSError):
            atlas_gui.save_manifest(os.path.join(self.tmp, "nao_existe"), {})

    def test_list_chapter_files_readonly_and_filtered(self):
        cdir = os.path.join(self.proj, "chapters")
        open(os.path.join(cdir, "nota.docx"), "w").close()
        open(os.path.join(cdir, ".oculto.md"), "w").close()
        before = tree_hash(cdir)
        self.assertEqual(atlas_gui.list_chapter_files(self.proj), ["00_abertura.md", "01_capitulo_um.md"])
        self.assertEqual(before, tree_hash(cdir))
        self.assertEqual(atlas_gui.list_chapter_files(self.tmp), [])

    def test_move_item(self):
        self.assertEqual(atlas_gui.move_item(["a", "b", "c"], 1, -1), (["b", "a", "c"], 0))
        self.assertEqual(atlas_gui.move_item(["a", "b", "c"], 1, 1), (["a", "c", "b"], 2))
        self.assertEqual(atlas_gui.move_item(["a", "b"], 0, -1), (["a", "b"], 0))
        self.assertEqual(atlas_gui.move_item(["a", "b"], 1, 1), (["a", "b"], 1))
        self.assertEqual(atlas_gui.move_item([], 0, 1), ([], 0))

    def test_classify_line(self):
        c = atlas_gui.classify_line
        self.assertEqual(c("err", "AVISO: x"), "warn")
        self.assertEqual(c("err", "ERRO: x"), "err")
        self.assertEqual(c("out", "Validacao FALHOU: 1 erro(s)."), "err")
        self.assertEqual(c("out", "Validacao OK."), "ok")
        self.assertEqual(c("out", "gerado: /x"), "ok")
        self.assertEqual(c("out", "qualquer"), "")

    def test_open_folder_dispatch(self):
        with mock.patch.object(atlas_gui.subprocess, "Popen") as popen, \
                mock.patch.object(atlas_gui.sys, "platform", "linux"):
            atlas_gui.open_folder("/x")
            popen.assert_called_once_with(["xdg-open", "/x"])
        with mock.patch.object(atlas_gui.os, "startfile", create=True) as sf, \
                mock.patch.object(atlas_gui.sys, "platform", "win32"):
            atlas_gui.open_folder("C:\\x")
            sf.assert_called_once_with("C:\\x")

    def test_gui_source_is_python39_compatible(self):
        import ast
        for name in ("atlas_gui.py", os.path.join("tests", "test_atlas_gui.py")):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                ast.parse(fh.read(), feature_version=(3, 9))

    def test_gui_imports_atlas_and_does_not_duplicate_validation(self):
        src = open(os.path.join(ROOT, "atlas_gui.py"), encoding="utf-8").read()
        self.assertIn("import atlas\n", src)
        self.assertIn("atlas.main(", src)
        self.assertNotIn("def validate_project", src)
        self.assertNotIn("def build_epub", src)


class TestBat(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "ATLAS.bat"), "rb") as fh:
            self.raw = fh.read()
        self.text = self.raw.decode("ascii")

    def test_crlf_and_ascii(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))

    def test_order_py_then_localappdata_then_python(self):
        t = self.text
        i_py = t.index("py -3 --version")
        i_loc = t.index("Programs\\Python\\Python3*")
        i_python = t.index("python --version")
        self.assertTrue(i_py < i_loc < i_python)

    def test_uses_windowless_pythonw_and_portuguese_message(self):
        self.assertIn("pyw -3", self.text)
        self.assertIn("pythonw.exe", self.text)
        self.assertIn("nao encontrei um Python", self.text)
        self.assertIn("atlas_gui.py", self.text)


# --------------------------------------------------------------------------
# Testes da janela (precisam de tkinter e de um display, ex. xvfb-run)
# --------------------------------------------------------------------------

def _tk_available():
    try:
        import tkinter
    except ImportError:
        return "tkinter indisponivel"
    try:
        r = tkinter.Tk()
        r.destroy()
    except Exception as exc:  # TclError sem display
        return "sem display: %s" % exc
    return None


_TK_SKIP = _tk_available()


@unittest.skipIf(_TK_SKIP, _TK_SKIP or "")
class TestWindow(Base):
    def setUp(self):
        Base.setUp(self)
        import tkinter
        self.root = tkinter.Tk()
        self.addCleanup(self.root.destroy)
        self.app = atlas_gui.AtlasApp(self.root, interactive=False)

    def pump(self, until, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if until():
                return True
            time.sleep(0.02)
        return False

    def test_load_project_fills_form(self):
        self.app.set_project(self.proj)
        self.assertEqual(self.app.var_id.get(), "livro_exemplo")
        self.assertEqual(self.app.var_author.get(), "Autor de Exemplo")
        self.assertEqual(self.app.var_volume.get(), "1")
        self.assertEqual(self.app.listbox.get(0, "end"), ("00_abertura.md", "01_capitulo_um.md"))
        self.assertTrue(all(v.get() for v in self.app.var_outputs.values()))
        self.assertFalse(self.app.dirty)

    def test_no_manifest_message_and_buttons(self):
        self.app.set_project(self.tmp)
        self.assertIn("Iniciar obra", self.app.log_text())
        self.assertEqual(str(self.app.btn_validate["state"]), "normal")

    def test_buttons_disabled_without_project(self):
        self.assertEqual(str(self.app.btn_build["state"]), "disabled")

    def test_move_and_save_changes_order_on_disk_only_in_manifest(self):
        self.app.set_project(self.proj)
        before = tree_hash(os.path.join(self.proj, "chapters"))
        self.app.listbox.selection_set(1)
        self.app.move_selected(-1)
        self.assertTrue(self.app.dirty)
        self.app.var_title.set("Novo título")
        self.app.var_outputs["pdf"].set(False)
        self.assertTrue(self.app.save())
        data = json.load(open(os.path.join(self.proj, "manifest.json"), encoding="utf-8"))
        self.assertEqual(data["chapters"], ["01_capitulo_um.md", "00_abertura.md"])
        self.assertEqual(data["title"], "Novo título")
        self.assertEqual(data["outputs"], ["txt", "epub", "ragnarok"])
        self.assertEqual(before, tree_hash(os.path.join(self.proj, "chapters")))
        self.assertFalse(self.app.dirty)

    def test_invalid_volume_not_saved(self):
        self.app.set_project(self.proj)
        raw = open(os.path.join(self.proj, "manifest.json"), "rb").read()
        self.app.var_volume.set("abc")
        self.assertFalse(self.app.save())
        self.assertEqual(raw, open(os.path.join(self.proj, "manifest.json"), "rb").read())

    def test_remove_and_add_chapters(self):
        self.app.set_project(self.proj)
        self.app.listbox.selection_set(0)
        self.app.remove_selected()
        self.assertEqual(self.app.chapters, ["01_capitulo_um.md"])
        self.app.add_chapters()
        self.assertEqual(self.app.chapters, ["01_capitulo_um.md", "00_abertura.md"])

    def test_validate_runs_in_thread_and_logs(self):
        self.app.set_project(self.proj)
        self.app.run("validate")
        self.assertTrue(self.app.busy)
        self.assertEqual(str(self.app.btn_build["state"]), "disabled")
        self.assertTrue(self.pump(lambda: not self.app.busy))
        self.assertEqual(self.app.last_exit, 0)
        self.assertIn("Validacao OK.", self.app.log_text())
        self.assertEqual(str(self.app.btn_build["state"]), "normal")

    def test_validate_failure_is_logged(self):
        self.app.set_project(self.proj)
        self.app.var_author.set("")
        self.app.run("validate")  # nao interativo: salva antes
        self.assertTrue(self.pump(lambda: not self.app.busy))
        self.assertEqual(self.app.last_exit, 1)
        self.assertIn("ERRO: metadado vazio ou invalido: author", self.app.log_text())
        self.assertTrue(self.app.log.tag_ranges("err"))

    def test_build_then_open_build_folder(self):
        self.app.set_project(self.proj)
        self.assertFalse(self.app.open_build())  # ainda nao existe
        self.app.run("build")
        self.assertTrue(self.pump(lambda: not self.app.busy, 60))
        self.assertEqual(self.app.last_exit, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "build", "audiobook.txt")))
        with mock.patch.object(atlas_gui, "open_folder") as of:
            self.assertTrue(self.app.open_build())
            of.assert_called_once_with(os.path.join(self.proj, "build"))

    def test_init_creates_work_and_loads_form(self):
        new = os.path.join(self.tmp, "minha_obra")
        os.mkdir(new)
        self.app.set_project(new)
        self.app.run("init")
        self.assertTrue(self.pump(lambda: not self.app.busy))
        self.assertEqual(self.app.last_exit, 0)
        self.assertEqual(self.app.var_id.get(), "minha_obra")
        self.assertEqual(self.app.chapters, ["00_abertura.md"])
        # autor vazio => Validar falha com mensagem clara
        self.app.run("validate")
        self.assertTrue(self.pump(lambda: not self.app.busy))
        self.assertEqual(self.app.last_exit, 1)

    def test_ui_stays_responsive_while_worker_runs(self):
        self.app.set_project(self.proj)
        ticks = []

        def slow(command, project, emit):
            emit("out", "linha 1")
            time.sleep(0.4)
            emit("out", "linha 2")
            return 0

        with mock.patch.object(atlas_gui, "run_atlas", slow):
            self.app.run("build")
            self.root.after(50, lambda: ticks.append(1))
            self.assertTrue(self.pump(lambda: not self.app.busy))
        self.assertTrue(ticks)
        self.assertIn("linha 1", self.app.log_text())
        self.assertIn("linha 2", self.app.log_text())


if __name__ == "__main__":
    unittest.main()
