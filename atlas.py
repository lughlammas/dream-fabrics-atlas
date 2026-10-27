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
import unicodedata
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

_PENDING_RE = re.compile(r"\[\s*PREENCHER", re.IGNORECASE)


def _pending_markers(value, path):
    """Percorre strings do manifesto e devolve os caminhos que contem [PREENCHER."""
    found = []
    if isinstance(value, str):
        if _PENDING_RE.search(value):
            found.append(path)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            found.extend(_pending_markers(item, "%s[%d]" % (path, idx)))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_pending_markers(item, "%s.%s" % (path, key)))
    return found

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

    # Marcador de pendencia em qualquer metadado
    for key, value in manifest.items():
        for where in _pending_markers(value, key):
            errors.append("metadado pendente: %s contem o marcador [PREENCHER ...]; "
                          "substitua pelo valor real antes de validar/compilar" % where)

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
                    pending = len(_PENDING_RE.findall(text))
                    if pending:
                        warnings.append("chapters/%s contem %d marcador(es) [PREENCHER] pendente(s)"
                                        % (name, pending))
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
# Tabelas "pipe" (com linha |---|) e \\newpage/\\pagebreak/\\clearpage (linha unica).
# Nao suportado: listas aninhadas, HTML cru, imagens (viram o texto alt).

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_UL_RE = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s{0,3}\d{1,9}[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_PAGEBREAK_RE = re.compile(r"^\s*\\(?:newpage|pagebreak|clearpage|cleardoublepage)\s*(?:\{\}|\[[^\]]*\])?\s*$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def is_pagebreak(line):
    """Linha que contem apenas \\newpage, \\pagebreak, \\clearpage ou \\cleardoublepage."""
    return bool(_PAGEBREAK_RE.match(line))


def split_row(line):
    """Divide uma linha de tabela em celulas (respeita \\| e codigo inline)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            # \| dentro de codigo vira "|" literal; fora, o escape e tratado no inline
            cur.append("|" if (in_code and s[i + 1] == "|") else s[i:i + 2])
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def find_table(lines, i):
    """Se lines[i] abre uma tabela (cabecalho + linha |---|), devolve
    (cabecalhos, alinhamentos, linhas, proximo_indice); senao None."""
    if i + 1 >= len(lines) or "|" not in lines[i] or "|" not in lines[i + 1]:
        return None
    sep = split_row(lines[i + 1])
    if not sep or not all(_TABLE_SEP_CELL_RE.match(c) for c in sep):
        return None
    headers = split_row(lines[i])
    if len(headers) != len(sep):
        return None
    aligns = []
    for c in sep:
        if c.startswith(":") and c.endswith(":"):
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    rows = []
    j = i + 2
    while j < len(lines) and lines[j].strip() != "" and "|" in lines[j]:
        cells = split_row(lines[j])
        cells = (cells + [""] * len(headers))[:len(headers)]
        rows.append(cells)
        j += 1
    return headers, aligns, rows, j


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
        if is_pagebreak(line):
            flush()
            if not blocks or blocks[-1][0] != "pagebreak":
                blocks.append(("pagebreak",))
            i += 1
            continue
        table = find_table(lines, i)
        if table:
            flush()
            blocks.append(("table", table[0], table[1], table[2]))
            i = table[3]
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


_INLINE_LATEX_RE = re.compile(r"[ \t]*\\(?:newpage|pagebreak|clearpage|cleardoublepage)\b(?:\{\})?")


def _protect(text, store):
    text = _INLINE_LATEX_RE.sub("", text)  # nunca vazar como texto
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


def inline_plain(text, strip_tags=True):
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
    if strip_tags:
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
        elif kind == "pagebreak":
            out.append('<div class="pagebreak"></div>')
        elif kind == "table":
            out.append(_table_html(b[1], b[2], b[3]))
    return "\n".join(out)


def _table_html(headers, aligns, rows):
    def cell(tag, text, align):
        style = "" if align == "left" else ' style="text-align:%s"' % align
        return "<%s%s>%s</%s>" % (tag, style, inline_html(text), tag)
    head = "<tr>%s</tr>" % "".join(cell("th", h, a) for h, a in zip(headers, aligns))
    body = "\n".join("<tr>%s</tr>" % "".join(cell("td", c, a) for c, a in zip(r, aligns)) for r in rows)
    out = "<table>\n<thead>\n%s\n</thead>\n" % head
    if rows:
        out += "<tbody>\n%s\n</tbody>\n" % body
    return out + "</table>"


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
        elif kind == "pagebreak":
            out.append(PAUSE_LONG)
        elif kind == "table":
            out.append(PAUSE_SHORT)
            out.append(_table_narration(b[1], b[2], b[3]))
    return out


def _table_narration(headers, aligns, rows):
    """Cada linha vira uma frase: 'Cabecalho1: valor, Cabecalho2: valor.'"""
    sentences = []
    for row in rows:
        parts = []
        for head, cell in zip(headers, row):
            value = " ".join(inline_plain(cell).split())
            if not value:
                continue
            label = inline_plain(head).strip()
            parts.append("%s: %s" % (label, value) if label else value)
        if parts:
            sentence = ", ".join(parts)
            sentences.append(sentence if sentence.endswith(".") else sentence + ".")
    return "\n".join(sentences)


def first_heading(blocks):
    for b in blocks:
        if b[0] == "h":
            return inline_plain(b[2])
    return None


# --------------------------------------------------------------------------
# Saidas
# --------------------------------------------------------------------------

def _display_width(text):
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text, width, align):
    gap = max(width - _display_width(text), 0)
    if align == "right":
        return " " * gap + text
    if align == "center":
        return " " * (gap // 2) + text + " " * (gap - gap // 2)
    return text + " " * gap


def table_to_text(headers, aligns, rows):
    """Tabela monoespacada alinhada, sem barras verticais."""
    grid = [[inline_plain(c, strip_tags=False).strip() for c in headers]]
    grid += [[inline_plain(c, strip_tags=False).strip() for c in r] for r in rows]
    widths = [max(max(_display_width(r[k]), 3) for r in grid) for k in range(len(headers))]

    def line(cells):
        return "  ".join(_pad(c, w, a) for c, w, a in zip(cells, widths, aligns)).rstrip()
    out = [line(grid[0]), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in grid[1:]]
    return out


def chapter_to_txt(text):
    """Texto do capitulo para o txt: igual ao original (Markdown preservado),
    exceto tabelas (alinhadas em texto) e \\newpage & cia (linha em branco)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            out.append(line)
            i += 1
            while i < n and not lines[i].lstrip().startswith(marker):
                out.append(lines[i])
                i += 1
            if i < n:
                out.append(lines[i])
                i += 1
            continue
        if is_pagebreak(line):
            if out and out[-1].strip() != "":
                out.append("")
            i += 1
            while i < n and lines[i].strip() == "":
                i += 1
            continue
        table = find_table(lines, i)
        if table:
            if out and out[-1].strip() != "":
                out.append("")
            out.extend(table_to_text(table[0], table[1], table[2]))
            i = table[3]
            if i < n and lines[i].strip() != "":
                out.append("")
            continue
        out.append(_INLINE_LATEX_RE.sub("", line))
        i += 1
    return "\n".join(out)


def build_txt(manifest, texts):
    parts = ["%s\n%s" % (manifest["title"], manifest["author"])]
    for t in texts:
        parts.append(chapter_to_txt(t).strip("\n"))
    return "\n\n".join(parts) + "\n"


def build_ragnarok(manifest, texts):
    parts = ["%s\npor %s" % (manifest["title"], manifest["author"])]
    for t in texts:
        parts.append(PAUSE_LONG)
        parts.extend(blocks_to_narration(parse_blocks(t)))
    deduped = []
    for p in parts:  # pausas iguais consecutivas viram uma so
        if deduped and p == deduped[-1] and p in (PAUSE_SHORT, PAUSE_LONG):
            continue
        deduped.append(p)
    return "\n\n".join(deduped) + "\n"


_CSS = ("body{font-family:serif;line-height:1.4}h1,h2,h3{font-family:sans-serif}"
        "pre{white-space:pre-wrap}blockquote{margin-left:1.5em;font-style:italic}"
        "table{border-collapse:collapse;width:100%;margin:1em 0;font-size:0.75em}"
        "th,td{border:1px solid #888;padding:0.2em 0.3em;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere}"
        "th{background:#e8e8e8;font-weight:bold}tr{page-break-inside:avoid}"
        "thead{display:table-header-group}"
        ".pagebreak{page-break-after:always;break-after:page}\n")


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
            '<style>%s@page{size:A5;margin:1.7cm}.title{text-align:center;margin-top:5cm}'
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
