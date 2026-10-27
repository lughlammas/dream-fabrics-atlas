#!/usr/bin/env python3
"""ATLAS v1.0 - compilador local de livros (Dream Fabrics).

Somente biblioteca padrao do Python 3, sem rede. PDF usa WeasyPrint se estiver
instalado; caso contrario a saida pdf e pulada com aviso.

Comandos:
    atlas.py init <dir>      cria manifest.json + chapters/00_abertura.md
    atlas.py validate <dir>  valida manifesto e capitulos (saida != 0 em erro)
    atlas.py build <dir>     valida e gera <dir>/build/ (txt, epub, pdf, ragnarok)

Regras: a ordem de leitura vem so do manifest.json; chapters/ e somente lido,
nunca escrito; todas as saidas ficam em <dir>/build/.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import uuid
import zipfile

ATLAS_VERSION = "1.0"
KNOWN_OUTPUTS = ("txt", "epub", "pdf", "ragnarok")
REQUIRED_FIELDS = ("id", "title", "author", "language", "chapters", "outputs")
CHAPTER_EXTS = (".md", ".txt")

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2

MANIFEST_TEMPLATE = {
    "atlas_version": "1.0",
    "id": "tomo_02_calistenia",
    "title": "Calistenia",
    "author": "",
    "volume": 2,
    "language": "pt-BR",
    "chapters": ["00_abertura.md", "01_capitulo_um.md"],
    "outputs": ["txt", "epub", "pdf", "ragnarok"],
}

OPENING_TEMPLATE = "# Abertura\n\nEscreva aqui a abertura da obra.\n"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


# --------------------------------------------------------------------------
# Manifesto e validacao
# --------------------------------------------------------------------------

def _is_nonempty_str(value):
    return isinstance(value, str) and value.strip() != ""


def load_manifest(project):
    """Retorna (manifest|None, erros)."""
    path = os.path.join(project, "manifest.json")
    if not os.path.isfile(path):
        return None, ["manifest.json nao encontrado em %s" % project]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        manifest = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None, ["manifest.json nao esta em UTF-8"]
    except ValueError as exc:
        return None, ["manifest.json invalido (JSON): %s" % exc]
    if not isinstance(manifest, dict):
        return None, ["manifest.json deve ser um objeto JSON"]
    return manifest, []


def check_chapter_file(chapters_dir, name):
    """Retorna (texto|None, erro|None) para um capitulo listado no manifesto."""
    if (not isinstance(name, str) or name.strip() == "" or "/" in name
            or "\\" in name or name.startswith(".") or "\x00" in name):
        return None, "capitulo com nome invalido: %r" % (name,)
    if not name.lower().endswith(CHAPTER_EXTS):
        return None, "capitulo %r: extensao deve ser .md ou .txt" % name
    path = os.path.join(chapters_dir, name)
    if not os.path.isfile(path):
        return None, "capitulo ausente: chapters/%s" % name
    real_dir = os.path.realpath(chapters_dir)
    if os.path.commonpath([real_dir, os.path.realpath(path)]) != real_dir:
        return None, "capitulo %r aponta para fora de chapters/" % name
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "capitulo %r ilegivel: %s" % (name, exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "capitulo %r nao esta em UTF-8 valido" % name
    if text.startswith("\ufeff"):
        text = text[1:]
    if "\x00" in text:
        return None, "capitulo %r contem bytes nulos (arquivo invalido)" % name
    if text.strip() == "":
        return None, "capitulo %r esta vazio" % name
    return text, None


def validate_project(project):
    """Retorna (erros, avisos, manifest, textos) -- textos: lista na ordem do manifesto."""
    warnings = []
    if not os.path.isdir(project):
        return ["diretorio nao encontrado: %s" % project], warnings, None, []
    manifest, errors = load_manifest(project)
    if manifest is None:
        return errors, warnings, None, []

    # Campos obrigatorios
    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append("metadado ausente: %s" % field)
    for field in ("id", "title", "author", "language"):
        if field in manifest and not _is_nonempty_str(manifest[field]):
            errors.append("metadado vazio ou invalido: %s (deve ser texto nao vazio)" % field)
    if _is_nonempty_str(manifest.get("id")) and not _ID_RE.match(manifest["id"]):
        errors.append("id invalido: %r (use letras, digitos, '.', '_' ou '-')" % manifest["id"])
    if _is_nonempty_str(manifest.get("language")) and not _LANG_RE.match(manifest["language"]):
        errors.append("language invalido: %r (esperado algo como pt-BR)" % manifest["language"])
    if "atlas_version" in manifest and manifest["atlas_version"] != ATLAS_VERSION:
        errors.append("atlas_version %r nao suportada (esperado %r)"
                      % (manifest["atlas_version"], ATLAS_VERSION))
    if "atlas_version" not in manifest:
        warnings.append("atlas_version ausente (assumindo %s)" % ATLAS_VERSION)
    if "volume" in manifest:
        vol = manifest["volume"]
        if isinstance(vol, bool) or not isinstance(vol, int) or vol < 1:
            errors.append("volume deve ser inteiro >= 1")

    # outputs
    outputs = manifest.get("outputs")
    if "outputs" in manifest:
        if not isinstance(outputs, list) or not outputs:
            errors.append("outputs deve ser uma lista nao vazia")
        else:
            seen = set()
            for out in outputs:
                if out not in KNOWN_OUTPUTS:
                    errors.append("output desconhecido: %r (validos: %s)"
                                  % (out, ", ".join(KNOWN_OUTPUTS)))
                elif out in seen:
                    errors.append("output duplicado: %s" % out)
                seen.add(out if isinstance(out, str) else repr(out))

    # chapters
    chapters = manifest.get("chapters")
    texts = []
    if "chapters" in manifest:
        if not isinstance(chapters, list) or not chapters:
            errors.append("chapters deve ser uma lista nao vazia")
        else:
            chapters_dir = os.path.join(project, "chapters")
            if not os.path.isdir(chapters_dir):
                errors.append("diretorio chapters/ nao encontrado")
            seen = set()
            for name in chapters:
                if isinstance(name, str) and name in seen:
                    errors.append("capitulo duplicado no manifesto: %s" % name)
                    continue
                if isinstance(name, str):
                    seen.add(name)
                if not os.path.isdir(chapters_dir):
                    continue
                text, err = check_chapter_file(chapters_dir, name)
                if err:
                    errors.append(err)
                else:
                    texts.append(text)
            if os.path.isdir(chapters_dir):
                listed = set(n for n in chapters if isinstance(n, str))
                for extra in sorted(os.listdir(chapters_dir)):
                    if extra not in listed and not extra.startswith("."):
                        warnings.append("chapters/%s existe mas nao esta no manifesto (ignorado)" % extra)
    return errors, warnings, manifest, texts


# --------------------------------------------------------------------------
# Markdown minimo -> blocos
# --------------------------------------------------------------------------
# Suportado: titulos ATX, paragrafos, listas (um nivel), citacoes, blocos de
# codigo com cerca, linha horizontal, enfase/negrito, codigo inline, links.
# Nao suportado: tabelas, listas aninhadas, HTML cru, imagens (viram o texto alt).

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_UL_RE = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s{0,3}\d{1,9}[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def parse_blocks(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return _parse_lines(lines)


def _parse_lines(lines):
    blocks = []
    i = 0
    n = len(lines)
    para = []

    def flush():
        if para:
            blocks.append(("p", " ".join(s.strip() for s in para)))
            del para[:]

    while i < n:
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            flush()
            marker = fence.group(1)
            i += 1
            code = []
            while i < n and not lines[i].lstrip().startswith(marker):
                code.append(lines[i])
                i += 1
            i += 1  # cerca de fechamento
            blocks.append(("code", "\n".join(code)))
            continue
        if line.strip() == "":
            flush()
            i += 1
            continue
        m = _HEADING_RE.match(line)
        if m:
            flush()
            blocks.append(("h", len(m.group(1)), m.group(2)))
            i += 1
            continue
        if _HR_RE.match(line):
            flush()
            blocks.append(("hr",))
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                q = lines[i].lstrip()[1:]
                quote.append(q[1:] if q.startswith(" ") else q)
                i += 1
            blocks.append(("quote", _parse_lines(quote)))
            continue
        ul, ol = _UL_RE.match(line), _OL_RE.match(line)
        if ul or ol:
            flush()
            regex = _UL_RE if ul else _OL_RE
            items = []
            while i < n:
                mm = regex.match(lines[i])
                if mm and not _HR_RE.match(lines[i]):
                    items.append(mm.group(1).strip())
                    i += 1
                elif (lines[i].strip() != "" and lines[i][:1] in (" ", "\t") and items
                      and not _UL_RE.match(lines[i]) and not _OL_RE.match(lines[i])):
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            blocks.append(("ul" if ul else "ol", items))
            continue
        para.append(line)
        i += 1
    flush()
    return blocks


# --------------------------------------------------------------------------
# Inline
# --------------------------------------------------------------------------

_ESC_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")
_CODE_RE = re.compile(r"`+([^`]+?)`+")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_EM_STAR_RE = re.compile(r"\*(?=\S)(.+?)(?<=\S)\*")
_EM_UND_RE = re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")
_PUA = "\ue000"


def _protect(text, store):
    def esc(m):
        store.append(m.group(1))
        return _PUA + chr(0xE100 + len(store) - 1) + _PUA
    return _ESC_RE.sub(esc, text)


def _restore(text, store, escape_html):
    def back(m):
        ch = store[ord(m.group(1)) - 0xE100]
        return html.escape(ch) if escape_html else ch
    return re.sub(_PUA + "([\ue100-\uf8ff])" + _PUA, back, text)


def inline_html(text):
    store = []
    codes = []

    def grab_code(m):
        codes.append(m.group(1).strip())
        return "\ue001%d\ue001" % (len(codes) - 1)
    text = _CODE_RE.sub(grab_code, text)  # codigo inline antes dos escapes
    text = _protect(text, store)
    text = html.escape(text, quote=False)
    text = _IMG_RE.sub(lambda m: m.group(1), text)

    def link(m):
        url = html.unescape(m.group(2))
        if re.match(r"^https?://", url):
            return '<a href="%s">%s</a>' % (html.escape(url, quote=True), m.group(1))
        return m.group(1)
    text = _LINK_RE.sub(link, text)
    text = _BOLD_RE.sub(r"<strong>\2</strong>", text)
    text = _EM_STAR_RE.sub(r"<em>\1</em>", text)
    text = _EM_UND_RE.sub(r"<em>\1</em>", text)
    text = re.sub("\ue001(\\d+)\ue001",
                  lambda m: "<code>%s</code>" % html.escape(codes[int(m.group(1))], quote=False), text)
    return _restore(text, store, True)


def inline_plain(text):
    store = []
    codes = []

    def grab_code(m):
        codes.append(m.group(1).strip())
        return "\ue001%d\ue001" % (len(codes) - 1)
    text = _CODE_RE.sub(grab_code, text)  # codigo inline antes dos escapes
    text = _protect(text, store)
    text = _IMG_RE.sub(lambda m: m.group(1), text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_RE.sub(r"\2", text)
    text = _EM_STAR_RE.sub(r"\1", text)
    text = _EM_UND_RE.sub(r"\1", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)  # tags HTML cruas
    text = re.sub("\ue001(\\d+)\ue001", lambda m: codes[int(m.group(1))], text)
    return _restore(text, store, False)


def blocks_to_html(blocks):
    out = []
    for b in blocks:
        kind = b[0]
        if kind == "h":
            out.append("<h%d>%s</h%d>" % (b[1], inline_html(b[2]), b[1]))
        elif kind == "p":
            out.append("<p>%s</p>" % inline_html(b[1]))
        elif kind in ("ul", "ol"):
            items = "".join("<li>%s</li>" % inline_html(t) for t in b[1])
            out.append("<%s>%s</%s>" % (kind, items, kind))
        elif kind == "quote":
            out.append("<blockquote>%s</blockquote>" % blocks_to_html(b[1]))
        elif kind == "code":
            out.append("<pre><code>%s</code></pre>" % html.escape(b[1], quote=False))
        elif kind == "hr":
            out.append("<hr/>")
    return "\n".join(out)


PAUSE_SHORT = "[pausa]"
PAUSE_LONG = "[pausa longa]"


def blocks_to_narration(blocks):
    """Lista de paragrafos (strings) sem sintaxe Markdown."""
    out = []
    for b in blocks:
        kind = b[0]
        if kind == "h":
            out.append(inline_plain(b[2]))
        elif kind == "p":
            out.append(inline_plain(b[1]))
        elif kind in ("ul", "ol"):
            out.append("\n".join(inline_plain(t) for t in b[1]))
        elif kind == "quote":
            out.extend(blocks_to_narration(b[1]))
        elif kind == "code":
            if b[1].strip():
                out.append(b[1].strip("\n"))
        elif kind == "hr":
            out.append(PAUSE_SHORT)
    return out


def first_heading(blocks):
    for b in blocks:
        if b[0] == "h":
            return inline_plain(b[2])
    return None


# --------------------------------------------------------------------------
# Saidas
# --------------------------------------------------------------------------

def build_txt(manifest, texts):
    parts = ["%s\n%s" % (manifest["title"], manifest["author"])]
    for t in texts:
        parts.append(t.replace("\r\n", "\n").replace("\r", "\n").strip("\n"))
    return "\n\n".join(parts) + "\n"


def build_ragnarok(manifest, texts):
    parts = ["%s\npor %s" % (manifest["title"], manifest["author"])]
    for t in texts:
        paras = blocks_to_narration(parse_blocks(t))
        parts.append(PAUSE_LONG)
        parts.append("\n\n".join(paras))
    return "\n\n".join(parts) + "\n"


_CSS = ("body{font-family:serif;line-height:1.4}h1,h2,h3{font-family:sans-serif}"
        "pre{white-space:pre-wrap}blockquote{margin-left:1.5em;font-style:italic}\n")


def _xhtml(lang, title, body):
    esc_lang = html.escape(lang, quote=True)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'lang="%s" xml:lang="%s">\n<head>\n<meta charset="utf-8"/>\n<title>%s</title>\n'
            '<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n<body>\n%s\n</body>\n</html>\n'
            % (esc_lang, esc_lang, html.escape(title, quote=False), body))


def _chapter_docs(manifest, names, texts):
    docs = []
    for idx, (name, text) in enumerate(zip(names, texts), 1):
        blocks = parse_blocks(text)
        title = first_heading(blocks) or os.path.splitext(name)[0]
        docs.append(("chap%03d.xhtml" % idx, title, blocks_to_html(blocks)))
    return docs


def build_epub(manifest, names, texts, dest):
    lang = manifest["language"]
    title = manifest["title"]
    docs = _chapter_docs(manifest, names, texts)
    book_uuid = uuid.uuid5(uuid.NAMESPACE_URL, "atlas:" + manifest["id"])
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", time.time()))
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    ztime = time.gmtime(max(epoch, 315532800))[:6]  # zip nao aceita < 1980

    manifest_items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                      '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for idx, (fname, _t, _b) in enumerate(docs, 1):
        manifest_items.append('<item id="c%03d" href="%s" media-type="application/xhtml+xml"/>' % (idx, fname))
        spine.append('<itemref idref="c%03d"/>' % idx)
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" '
           'xml:lang="%s">\n<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           '<dc:identifier id="bookid">urn:uuid:%s</dc:identifier>\n<dc:title>%s</dc:title>\n'
           '<dc:creator>%s</dc:creator>\n<dc:language>%s</dc:language>\n'
           '<meta property="dcterms:modified">%s</meta>\n</metadata>\n<manifest>\n%s\n</manifest>\n'
           '<spine>\n%s\n</spine>\n</package>\n'
           % (html.escape(lang, quote=True), book_uuid, html.escape(title, quote=False),
              html.escape(manifest["author"], quote=False), html.escape(lang, quote=False),
              modified, "\n".join(manifest_items), "\n".join(spine)))
    nav_items = "\n".join('<li><a href="%s">%s</a></li>' % (f, html.escape(t, quote=False))
                          for f, t, _b in docs)
    nav_body = ('<nav epub:type="toc" id="toc">\n<h1>%s</h1>\n<ol>\n%s\n</ol>\n</nav>'
                % (html.escape(title, quote=False), nav_items))
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')

    def add(zf, name, data, compress):
        info = zipfile.ZipInfo(name, date_time=ztime)
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        zf.writestr(info, data.encode("utf-8"))

    with zipfile.ZipFile(dest, "w") as zf:
        add(zf, "mimetype", "application/epub+zip", False)  # primeiro e sem compressao
        add(zf, "META-INF/container.xml", container, True)
        add(zf, "OEBPS/content.opf", opf, True)
        add(zf, "OEBPS/nav.xhtml", _xhtml(lang, title, nav_body), True)
        add(zf, "OEBPS/style.css", _CSS, True)
        for fname, ctitle, body in docs:
            add(zf, "OEBPS/" + fname, _xhtml(lang, ctitle, body), True)


def build_pdf(manifest, names, texts, dest):
    """Retorna True se gerou, False se WeasyPrint indisponivel."""
    try:
        import weasyprint  # noqa: WPS433
    except Exception:  # ImportError ou libs nativas ausentes
        return False
    docs = _chapter_docs(manifest, names, texts)
    lang = html.escape(manifest["language"], quote=True)
    body = ('<h1 class="title">%s</h1><p class="author">%s</p>'
            % (html.escape(manifest["title"], quote=False), html.escape(manifest["author"], quote=False)))
    body += "".join('<section class="chapter">%s</section>' % b for _f, _t, b in docs)
    page = ('<!DOCTYPE html><html lang="%s"><head><meta charset="utf-8"/><title>%s</title>'
            '<style>%s@page{size:A5;margin:2cm}.title{text-align:center;margin-top:5cm}'
            '.author{text-align:center}.chapter{page-break-before:always}</style></head><body>%s</body></html>'
            % (lang, html.escape(manifest["title"], quote=False), _CSS, body))
    weasyprint.HTML(string=page).write_pdf(dest)
    return True


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

def cmd_init(args):
    project = args.dir
    manifest_path = os.path.join(project, "manifest.json")
    if os.path.exists(manifest_path):
        print("ERRO: %s ja existe; init nao sobrescreve." % manifest_path, file=sys.stderr)
        return EXIT_ERROR
    chapters_dir = os.path.join(project, "chapters")
    os.makedirs(chapters_dir, exist_ok=True)
    manifest = dict(MANIFEST_TEMPLATE)
    manifest["id"] = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(os.path.abspath(project))).strip("._-") or "obra"
    manifest["chapters"] = ["00_abertura.md"]
    opening = os.path.join(chapters_dir, "00_abertura.md")
    if not os.path.exists(opening):
        with open(opening, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(OPENING_TEMPLATE)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("Projeto criado em %s" % project)
    print("Preencha o campo 'author' em manifest.json (vazio falha no validate).")
    return EXIT_OK


def _report(errors, warnings):
    for w in warnings:
        print("AVISO: %s" % w, file=sys.stderr)
    for e in errors:
        print("ERRO: %s" % e, file=sys.stderr)


def cmd_validate(args):
    errors, warnings, _m, _t = validate_project(args.dir)
    _report(errors, warnings)
    if errors:
        print("Validacao FALHOU: %d erro(s)." % len(errors))
        return EXIT_INVALID
    print("Validacao OK.")
    return EXIT_OK


def cmd_build(args):
    errors, warnings, manifest, texts = validate_project(args.dir)
    _report(errors, warnings)
    if errors:
        print("Build abortado: validacao falhou (%d erro(s))." % len(errors))
        return EXIT_INVALID
    names = manifest["chapters"]
    build_dir = os.path.join(args.dir, "build")
    os.makedirs(build_dir, exist_ok=True)
    book_id = manifest["id"]
    produced = []
    for out in manifest["outputs"]:
        if out == "txt":
            path = os.path.join(build_dir, book_id + ".txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(build_txt(manifest, texts))
        elif out == "ragnarok":
            path = os.path.join(build_dir, "audiobook.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(build_ragnarok(manifest, texts))
        elif out == "epub":
            path = os.path.join(build_dir, book_id + ".epub")
            build_epub(manifest, names, texts, path)
        elif out == "pdf":
            path = os.path.join(build_dir, book_id + ".pdf")
            if not build_pdf(manifest, names, texts, path):
                print("AVISO: WeasyPrint indisponivel; saida pdf PULADA (nenhum PDF gerado).",
                      file=sys.stderr)
                continue
        produced.append(path)
    for p in produced:
        print("gerado: %s" % p)
    print("Build concluido (%d saida(s))." % len(produced))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(prog="atlas.py", description="ATLAS v1.0 - compilador local de livros")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, helptext in (("init", cmd_init, "cria manifest.json e chapters/00_abertura.md"),
                               ("validate", cmd_validate, "valida manifesto e capitulos"),
                               ("build", cmd_build, "valida e gera build/")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("dir", help="diretorio do projeto")
        p.set_defaults(func=fn)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
