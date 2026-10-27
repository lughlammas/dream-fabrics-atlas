#!/usr/bin/env python3
"""ATLAS v1.0 - interface grafica (tkinter) para o compilador local atlas.py.

Somente biblioteca padrao, offline. Funciona no Windows 10/11 e no Linux.
Nao duplica logica: validacao e build vem de atlas.py (atlas.main).
A GUI nunca escreve em chapters/ (so le a listagem); o unico arquivo que ela
grava por conta propria e manifest.json (botao Salvar manifesto).
Compativel com Python 3.9+.
"""

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading

import atlas

# --------------------------------------------------------------------------
# Logica nao visual (testavel sem display)
# --------------------------------------------------------------------------

LANG_DEFAULT = "pt-BR"


class LineWriter(object):
    """Objeto tipo arquivo que entrega cada linha completa a um callback."""

    def __init__(self, emit, stream):
        self._emit = emit
        self._stream = stream
        self._buf = ""

    def write(self, data):
        if not isinstance(data, str):
            data = str(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(self._stream, line)
        return len(data)

    def flush(self):
        if self._buf:
            self._emit(self._stream, self._buf)
            self._buf = ""

    def isatty(self):
        return False


def run_atlas(command, project, emit):
    """Executa 'atlas.py <command> <project>' em-processo, enviando as linhas
    de stdout/stderr para emit(stream, linha). Retorna o codigo de saida."""
    out = LineWriter(emit, "out")
    err = LineWriter(emit, "err")
    code = atlas.EXIT_ERROR
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = atlas.main([command, project])
            except SystemExit as exc:  # argparse
                code = exc.code if isinstance(exc.code, int) else atlas.EXIT_ERROR
            except Exception as exc:  # erro inesperado nao pode matar a GUI
                emit("err", "ERRO inesperado: %s: %s" % (type(exc).__name__, exc))
                code = atlas.EXIT_ERROR
    finally:
        out.flush()
        err.flush()
    return code


def load_manifest_for_edit(project):
    """Retorna (manifest|None, erro|None). Usa atlas.load_manifest."""
    manifest, errors = atlas.load_manifest(project)
    if manifest is None:
        return None, errors[0]
    return manifest, None


def build_manifest(original, fields, chapters, outputs):
    """Monta o dict do manifesto a partir dos campos da GUI.

    Preserva chaves desconhecidas do original e a ordem das chaves.
    fields: dict com id, title, author, language, volume (texto).
    Levanta ValueError se o volume nao for inteiro. Nao exige campos
    preenchidos: quem decide se esta valido e o 'Validar' (atlas.py).
    """
    manifest = dict(original) if original else {}
    manifest.setdefault("atlas_version", atlas.ATLAS_VERSION)
    for key in ("id", "title", "author", "language"):
        manifest[key] = fields.get(key, "").strip()
    vol = fields.get("volume", "").strip()
    if vol == "":
        manifest.pop("volume", None)
    else:
        try:
            manifest["volume"] = int(vol)
        except ValueError:
            raise ValueError("volume deve ser um numero inteiro (ou vazio)")
    manifest["chapters"] = list(chapters)
    manifest["outputs"] = [o for o in atlas.KNOWN_OUTPUTS if o in set(outputs)]
    # ordem estavel: chaves conhecidas primeiro, como no modelo
    ordered = {}
    for key in ("atlas_version", "id", "title", "author", "volume", "language", "chapters", "outputs"):
        if key in manifest:
            ordered[key] = manifest[key]
    for key in manifest:
        if key not in ordered:
            ordered[key] = manifest[key]
    return ordered


def manifest_to_json(manifest):
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def save_manifest(project, manifest):
    """Grava <project>/manifest.json (gravacao atomica). Retorna o caminho."""
    if not os.path.isdir(project):
        raise OSError("pasta nao encontrada: %s" % project)
    path = os.path.join(project, "manifest.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(manifest_to_json(manifest))
    os.replace(tmp, path)
    return path


def list_chapter_files(project):
    """Arquivos .md/.txt em chapters/ (somente leitura), em ordem alfabetica."""
    cdir = os.path.join(project, "chapters")
    if not os.path.isdir(cdir):
        return []
    return sorted(n for n in os.listdir(cdir)
                  if n.lower().endswith(atlas.CHAPTER_EXTS) and not n.startswith(".")
                  and os.path.isfile(os.path.join(cdir, n)))


def move_item(items, index, delta):
    """Move items[index] em delta posicoes. Retorna (nova_lista, novo_indice)."""
    new = list(items)
    target = index + delta
    if index < 0 or index >= len(new) or target < 0 or target >= len(new):
        return new, index
    new[index], new[target] = new[target], new[index]
    return new, target


def classify_line(stream, line):
    """Etiqueta de cor para uma linha de log: 'err', 'warn', 'ok' ou ''."""
    if line.startswith("AVISO"):
        return "warn"
    if line.startswith("ERRO") or "FALHOU" in line or "abortado" in line:
        return "err"
    if stream == "err":
        return "err"
    if line.startswith("Validacao OK") or line.startswith("Build concluido") \
            or line.startswith("gerado:") or line.startswith("Projeto criado"):
        return "ok"
    return ""


def build_dir_of(project):
    return os.path.join(project, "build")


def open_folder(path):
    """Abre a pasta no gerenciador de arquivos do sistema."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: pylint
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------
# Interface grafica
# --------------------------------------------------------------------------

def _import_tk():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    return tk, ttk, filedialog, messagebox


class AtlasApp(object):
    TITLE = "ATLAS v1.0 - Fábrica de Livros"

    def __init__(self, root, project=None, interactive=True):
        tk, ttk, filedialog, messagebox = _import_tk()
        self.tk, self.ttk = tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.interactive = interactive
        self.root = root
        self.queue = queue.Queue()
        self.busy = False
        self.original = None
        self.dirty = False
        self._loading = False
        self.chapters = []
        self.last_exit = None
        self._closed = False
        self._after_id = None

        root.title(self.TITLE)
        root.geometry("920x720")
        root.minsize(760, 560)

        self.var_project = tk.StringVar()
        self.var_id = tk.StringVar()
        self.var_title = tk.StringVar()
        self.var_author = tk.StringVar()
        self.var_volume = tk.StringVar()
        self.var_lang = tk.StringVar(value=LANG_DEFAULT)
        self.var_outputs = dict((o, tk.BooleanVar(value=True)) for o in atlas.KNOWN_OUTPUTS)
        self.var_status = tk.StringVar(value="Escolha a pasta da obra.")
        for var in (self.var_id, self.var_title, self.var_author, self.var_volume, self.var_lang):
            var.trace_add("write", self._mark_dirty)
        for var in self.var_outputs.values():
            var.trace_add("write", self._mark_dirty)

        root.report_callback_exception = self._callback_exception
        root.bind("<Destroy>", self._on_destroy, add="+")
        self._build_ui()
        self._poll()
        if project:
            self.set_project(project)

    # ---- construcao da janela -------------------------------------------
    def _build_ui(self):
        tk, ttk = self.tk, self.ttk
        pad = {"padx": 6, "pady": 4}
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        main.rowconfigure(3, weight=1)

        # pasta da obra
        top = ttk.LabelFrame(main, text="Pasta da obra")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.entry_project = ttk.Entry(top, textvariable=self.var_project)
        self.entry_project.grid(row=0, column=0, sticky="ew", **pad)
        self.entry_project.bind("<Return>", lambda e: self.set_project(self.var_project.get()))
        self.btn_choose = ttk.Button(top, text="Escolher pasta", command=self.choose_folder)
        self.btn_choose.grid(row=0, column=1, **pad)
        self.btn_open = ttk.Button(top, text="Abrir pasta build", command=self.open_build)
        self.btn_open.grid(row=0, column=2, **pad)

        # acoes
        act = ttk.Frame(main)
        act.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.btn_init = ttk.Button(act, text="Iniciar obra", command=lambda: self.run("init"))
        self.btn_validate = ttk.Button(act, text="Validar", command=lambda: self.run("validate"))
        self.btn_build = ttk.Button(act, text="Compilar", command=lambda: self.run("build"))
        for b in (self.btn_init, self.btn_validate, self.btn_build):
            b.pack(side="left", padx=(0, 8))
        ttk.Label(act, textvariable=self.var_status).pack(side="left", padx=8)

        # manifesto
        mf = ttk.LabelFrame(main, text="manifest.json")
        mf.grid(row=2, column=0, sticky="nsew", pady=(4, 4))
        mf.columnconfigure(1, weight=1)
        mf.columnconfigure(3, weight=1)
        mf.rowconfigure(3, weight=1)
        ttk.Label(mf, text="id").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(mf, textvariable=self.var_id).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(mf, text="Título").grid(row=0, column=2, sticky="e", **pad)
        ttk.Entry(mf, textvariable=self.var_title).grid(row=0, column=3, sticky="ew", **pad)
        ttk.Label(mf, text="Autor").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(mf, textvariable=self.var_author).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(mf, text="Volume").grid(row=1, column=2, sticky="e", **pad)
        ttk.Entry(mf, textvariable=self.var_volume, width=8).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(mf, text="Idioma").grid(row=2, column=0, sticky="e", **pad)
        ttk.Entry(mf, textvariable=self.var_lang, width=12).grid(row=2, column=1, sticky="w", **pad)
        outs = ttk.Frame(mf)
        outs.grid(row=2, column=2, columnspan=2, sticky="w", **pad)
        ttk.Label(outs, text="Saídas:").pack(side="left", padx=(0, 6))
        for o in atlas.KNOWN_OUTPUTS:
            ttk.Checkbutton(outs, text=o, variable=self.var_outputs[o]).pack(side="left", padx=4)

        ttk.Label(mf, text="Capítulos\n(ordem de leitura)").grid(row=3, column=0, sticky="ne", **pad)
        lb_frame = ttk.Frame(mf)
        lb_frame.grid(row=3, column=1, columnspan=2, sticky="nsew", **pad)
        lb_frame.columnconfigure(0, weight=1)
        lb_frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(lb_frame, height=6, exportselection=False, activestyle="dotbox")
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        side = ttk.Frame(mf)
        side.grid(row=3, column=3, sticky="nw", **pad)
        self.btn_up = ttk.Button(side, text="Subir", command=lambda: self.move_selected(-1))
        self.btn_down = ttk.Button(side, text="Descer", command=lambda: self.move_selected(1))
        self.btn_add = ttk.Button(side, text="Adicionar de chapters/", command=self.add_chapters)
        self.btn_rem = ttk.Button(side, text="Remover da lista", command=self.remove_selected)
        for b in (self.btn_up, self.btn_down, self.btn_add, self.btn_rem):
            b.pack(fill="x", pady=2)

        bar = ttk.Frame(mf)
        bar.grid(row=4, column=0, columnspan=4, sticky="ew", **pad)
        self.btn_save = ttk.Button(bar, text="Salvar manifesto", command=self.save)
        self.btn_reload = ttk.Button(bar, text="Recarregar do disco", command=self.reload)
        self.btn_save.pack(side="left", padx=(0, 8))
        self.btn_reload.pack(side="left")
        ttk.Label(bar, text="(a lista de capítulos só altera o manifesto; chapters/ nunca é escrita)").pack(
            side="left", padx=12)

        # log
        lg = ttk.LabelFrame(main, text="Log")
        lg.grid(row=3, column=0, sticky="nsew")
        lg.columnconfigure(0, weight=1)
        lg.rowconfigure(0, weight=1)
        self.log = tk.Text(lg, height=10, wrap="word", state="disabled", font=("TkFixedFont", 10))
        lsb = ttk.Scrollbar(lg, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        lsb.grid(row=0, column=1, sticky="ns")
        self.log.tag_configure("err", foreground="#b00020")
        self.log.tag_configure("warn", foreground="#a65e00")
        self.log.tag_configure("ok", foreground="#1b7f2a")
        self.log.tag_configure("info", foreground="#555555")
        ttk.Button(lg, text="Limpar log", command=self.clear_log).grid(row=1, column=0, sticky="e", padx=6, pady=3)
        self._update_buttons()

    def _callback_exception(self, exc_type, exc, _tb):
        # erros inesperados de callbacks vao para o log (pythonw nao tem console)
        try:
            self.log_line("ERRO inesperado na interface: %s: %s" % (exc_type.__name__, exc), "err")
        except Exception:
            pass

    # ---- estado ----------------------------------------------------------
    def _mark_dirty(self, *_args):
        if not self._loading:
            self.dirty = True
            self._update_buttons()

    def project(self):
        return self.var_project.get().strip()

    def _update_buttons(self):
        has = bool(self.project()) and os.path.isdir(self.project())
        run_state = "normal" if (has and not self.busy) else "disabled"
        for b in (self.btn_init, self.btn_validate, self.btn_build):
            b.configure(state=run_state)
        for b in (self.btn_save, self.btn_reload):
            b.configure(state="normal" if (has and not self.busy) else "disabled")
        self.btn_choose.configure(state="disabled" if self.busy else "normal")
        self.btn_open.configure(state="normal" if has else "disabled")

    def set_status(self, text):
        self.var_status.set(text)

    # ---- log -------------------------------------------------------------
    def log_line(self, text, tag=""):
        self.log.configure(state="normal")
        if tag:
            self.log.insert("end", text + "\n", tag)
        else:
            self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def log_text(self):
        return self.log.get("1.0", "end")

    # ---- pasta / manifesto -----------------------------------------------
    def choose_folder(self):
        path = self.filedialog.askdirectory(title="Escolha a pasta da obra",
                                            initialdir=self.project() or os.path.expanduser("~"))
        if path:
            self.set_project(os.path.normpath(path))

    def set_project(self, path):
        self.var_project.set(path)
        if not os.path.isdir(path):
            self.log_line("Pasta não encontrada: %s" % path, "err")
            self.set_status("Pasta inválida.")
            self._clear_manifest_fields()
            self._update_buttons()
            return
        self.log_line("Pasta da obra: %s" % path, "info")
        self.load_into_form()
        self._update_buttons()

    def _clear_manifest_fields(self):
        self._loading = True
        try:
            for v in (self.var_id, self.var_title, self.var_author, self.var_volume):
                v.set("")
            self.var_lang.set(LANG_DEFAULT)
            for v in self.var_outputs.values():
                v.set(True)
            self.chapters = []
            self.original = None
            self._refresh_listbox()
        finally:
            self._loading = False
            self.dirty = False

    def load_into_form(self):
        manifest, err = load_manifest_for_edit(self.project())
        if manifest is None:
            self._clear_manifest_fields()
            self.log_line("%s. Use 'Iniciar obra' para criar uma obra nova." % err, "warn")
            self.set_status("Sem manifest.json nesta pasta.")
            return False
        self._loading = True
        try:
            self.original = manifest
            self.var_id.set(str(manifest.get("id", "")))
            self.var_title.set(str(manifest.get("title", "")))
            self.var_author.set(str(manifest.get("author", "")))
            self.var_volume.set("" if "volume" not in manifest else str(manifest["volume"]))
            self.var_lang.set(str(manifest.get("language", LANG_DEFAULT)))
            outs = manifest.get("outputs", [])
            outs = outs if isinstance(outs, list) else []
            for o, v in self.var_outputs.items():
                v.set(o in outs)
            chs = manifest.get("chapters", [])
            self.chapters = [str(c) for c in chs] if isinstance(chs, list) else []
            self._refresh_listbox()
        finally:
            self._loading = False
            self.dirty = False
        self.set_status("Manifesto carregado.")
        return True

    def _refresh_listbox(self, select=None):
        self.listbox.delete(0, "end")
        for c in self.chapters:
            self.listbox.insert("end", c)
        if select is not None and 0 <= select < len(self.chapters):
            self.listbox.selection_set(select)
            self.listbox.see(select)

    def _selected(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def move_selected(self, delta):
        idx = self._selected()
        if idx is None:
            return
        self.chapters, new = move_item(self.chapters, idx, delta)
        self._refresh_listbox(select=new)
        self._mark_dirty()

    def remove_selected(self):
        idx = self._selected()
        if idx is None:
            return
        del self.chapters[idx]
        self._refresh_listbox(select=min(idx, len(self.chapters) - 1))
        self._mark_dirty()

    def add_chapters(self):
        if not self.project():
            return
        missing = [n for n in list_chapter_files(self.project()) if n not in self.chapters]
        if not missing:
            self.log_line("Nenhum arquivo novo em chapters/ para adicionar.", "info")
            return
        self.chapters.extend(missing)
        self._refresh_listbox(select=len(self.chapters) - 1)
        self.log_line("Adicionados à lista: %s" % ", ".join(missing), "info")
        self._mark_dirty()

    def current_fields(self):
        return {"id": self.var_id.get(), "title": self.var_title.get(), "author": self.var_author.get(),
                "language": self.var_lang.get(), "volume": self.var_volume.get()}

    def current_manifest(self):
        outs = [o for o, v in self.var_outputs.items() if v.get()]
        return build_manifest(self.original, self.current_fields(), self.chapters, outs)

    def save(self):
        """Salva manifest.json. Retorna True se gravou."""
        if not self.project() or not os.path.isdir(self.project()):
            self.log_line("Escolha uma pasta válida antes de salvar.", "err")
            return False
        try:
            manifest = self.current_manifest()
        except ValueError as exc:
            self.log_line("ERRO: %s" % exc, "err")
            self._info("Não foi possível salvar", str(exc), error=True)
            return False
        try:
            path = save_manifest(self.project(), manifest)
        except OSError as exc:
            self.log_line("ERRO ao salvar manifest.json: %s" % exc, "err")
            return False
        self.original = manifest
        self.dirty = False
        self.log_line("Manifesto salvo: %s" % path, "ok")
        self.set_status("Manifesto salvo.")
        return True

    def reload(self):
        if self.dirty and not self._ask_yesno("Descartar as alterações não salvas e recarregar do disco?"):
            return
        self.load_into_form()

    # ---- caixas de dialogo (desligadas em modo nao interativo) ----------
    def _info(self, title, text, error=False):
        if not self.interactive:
            return
        (self.messagebox.showerror if error else self.messagebox.showinfo)(title, text)

    def _ask_yesno(self, text):
        if not self.interactive:
            return True
        return self.messagebox.askyesno(self.TITLE, text)

    def _ask_save_before_run(self):
        """True = pode continuar; False = cancelar a execucao."""
        if not self.dirty:
            return True
        if not self.interactive:
            return self.save()
        ans = self.messagebox.askyesnocancel(
            self.TITLE, "Há alterações não salvas no manifesto.\n\nSalvar antes de continuar?\n"
            "(Não = executar com o manifest.json que está no disco)")
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    # ---- execucao em thread ----------------------------------------------
    def run(self, command):
        if self.busy:
            return
        project = self.project()
        if not project or not os.path.isdir(project):
            self.log_line("Escolha uma pasta válida primeiro.", "err")
            return
        if command != "init" and not self._ask_save_before_run():
            self.log_line("Execução cancelada.", "info")
            return
        self.busy = True
        self._update_buttons()
        self.set_status("Executando: %s ..." % command)
        self.log_line("$ atlas %s %s" % (command, project), "info")

        def worker():
            code = run_atlas(command, project, lambda s, l: self.queue.put(("line", s, l)))
            self.queue.put(("done", command, code))

        threading.Thread(target=worker, name="atlas-" + command, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "line":
                    self.log_line(item[2], classify_line(item[1], item[2]))
                else:
                    self._finish(item[1], item[2])
        except queue.Empty:
            pass
        if not self._closed:
            self._after_id = self.root.after(60, self._poll)

    def _on_destroy(self, event):
        if event.widget is self.root:
            self._closed = True
            if self._after_id is not None:
                try:
                    self.root.after_cancel(self._after_id)
                except Exception:
                    pass

    def _finish(self, command, code):
        self.busy = False
        self.last_exit = code
        if code == atlas.EXIT_OK:
            self.log_line("[%s] concluído com sucesso (código 0)." % command, "ok")
            self.set_status("%s: OK" % command)
        else:
            self.log_line("[%s] terminou com erro (código %s)." % (command, code), "err")
            self.set_status("%s: falhou (código %s)" % (command, code))
        if command == "init" and code == atlas.EXIT_OK:
            self.load_into_form()
        self._update_buttons()

    def open_build(self):
        bdir = build_dir_of(self.project())
        if not os.path.isdir(bdir):
            self.log_line("A pasta build ainda não existe. Clique em Compilar primeiro.", "warn")
            self._info(self.TITLE, "A pasta build ainda não existe.\nClique em Compilar primeiro.")
            return False
        try:
            open_folder(bdir)
        except Exception as exc:
            self.log_line("ERRO ao abrir a pasta: %s" % exc, "err")
            return False
        return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        tk, _ttk, _fd, messagebox = _import_tk()
    except ImportError:
        msg = ("tkinter nao esta disponivel nesta instalacao do Python.\n"
               "Windows: reinstale o Python marcando 'tcl/tk and IDLE'.\n"
               "Linux (Debian/Ubuntu): sudo apt install python3-tk")
        print(msg, file=sys.stderr)
        return 2
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    project = argv[0] if argv else None
    AtlasApp(root, project=project)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
