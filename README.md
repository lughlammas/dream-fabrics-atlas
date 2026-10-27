# dream-fabrics-atlas

ATLAS v1.0 — Fábrica de Livros (Dream Fabrics). Repositório privado.
Especificação: [docs/SPEC-v1.0.md](docs/SPEC-v1.0.md).

`atlas.py` é um compilador local: Python 3, somente biblioteca padrão, sem rede.
A ordem de leitura vem **só** do `manifest.json`; `chapters/` nunca é escrito;
todas as saídas ficam em `<dir>/build/`.

## Uso

```
python3 atlas.py init <dir>       # cria manifest.json + chapters/00_abertura.md
python3 atlas.py validate <dir>   # sai com código 1 se houver erro
python3 atlas.py build <dir>      # roda validate; gera <dir>/build/
```

Exemplo pronto (2 capítulos): `python3 atlas.py build examples/livro-exemplo`.

### Estrutura de um projeto

```
<dir>/
  manifest.json
  chapters/00_abertura.md, 01_capitulo_um.md, ...   (.md ou .txt, UTF-8)
  build/                                             (gerado)
```

Campos obrigatórios do manifesto: `id`, `title`, `author`, `language`, `chapters`, `outputs`
(`atlas_version` e `volume` são opcionais; `atlas_version`, se presente, deve ser `"1.0"`).
`init` copia o modelo de `examples/manifest.example.json` com `author` **vazio**, e
o `validate` falha até que o autor seja preenchido.

### O que o `validate` checa (erro, código 1)
capítulo ausente; capítulo duplicado no manifesto; capítulo vazio (só espaços), não UTF-8
ou com bytes nulos; nome de capítulo inválido (caminhos, `..`, extensão fora de `.md`/`.txt`);
metadado ausente ou vazio (inclui `author` vazio); `id`/`language` malformados; `outputs`
vazio, desconhecido ou duplicado; manifesto inexistente ou JSON inválido.
Arquivos em `chapters/` que não estão no manifesto geram apenas aviso (são ignorados).

### Saídas do `build` (apenas as listadas em `outputs`)
| output | arquivo | observação |
|---|---|---|
| `txt` | `build/<id>.txt` | título, autor e os capítulos na ordem do manifesto, Markdown preservado |
| `epub` | `build/<id>.epub` | EPUB 3; `mimetype` primeiro e sem compressão; `nav.xhtml`; um XHTML por capítulo |
| `pdf` | `build/<id>.pdf` | via WeasyPrint se instalado; senão **aviso e a saída é pulada** (build termina com código 0, sem PDF) |
| `ragnarok` | `build/audiobook.txt` | texto linear sem sintaxe Markdown; títulos preservados como linhas; parágrafos separados por linha em branco; `[pausa longa]` entre capítulos; `[pausa]` no lugar de linha horizontal (`---`) |

Markdown suportado: títulos `#`, parágrafos, ênfase/negrito, código inline e em bloco,
citações, listas de um nível, linha horizontal, links `http(s)` (no EPUB/PDF; na narração
só o texto). **Não suportado:** tabelas, listas aninhadas, imagens (viram o texto alternativo),
HTML cru (no ragnarok as tags são removidas; no EPUB/PDF são escapadas), notas de rodapé.
O EPUB usa `SOURCE_DATE_EPOCH` se definido; sem ele, a data de modificação é a hora do build.

## Testes

```
python3 -m unittest discover -s tests -v
# opcional, valida o EPUB com EPUBCheck:
ATLAS_EPUBCHECK_JAR=/caminho/epubcheck.jar ATLAS_JAVA=/caminho/java python3 -m unittest discover -s tests -v
```

Cobertura: validate (ausente, duplicado, vazio, não UTF-8, bytes nulos, cada metadado
faltando, autor vazio/só espaços, output/idioma inválido, path traversal, JSON inválido),
init, build de txt/epub/ragnarok, ordem vinda do manifesto, imutabilidade de `chapters/`
(hash e mtime antes/depois, build duas vezes), saídas só em `build/`, PDF (gerado, ou
claramente pulado, inclusive sem WeasyPrint simulado), EPUBCheck (pulado se o jar não for informado).

### O que foi realmente verificado
Verificado em 27/10/2026 na box (Python 3.13.5): suíte unittest completa passando;
EPUB do exemplo validado com EPUBCheck 5.1.0 (0 erros, 0 avisos); PDF do exemplo gerado
com WeasyPrint 70.0 (3 páginas, texto extraído conferido com `pdftotext`).
**Não verificado:** EPUB em leitores reais (Kindle, Apple Books etc.); PDF com obras longas,
imagens ou fontes específicas; Windows/macOS; Python anterior a 3.13; síntese de voz no
Ragnarok (o `audiobook.txt` só foi conferido como texto).

## Pendências
Os pontos técnicos ainda a formalizar estão em `docs/SPEC-v1.0.md` (`[PREENCHER — dado do autor]`).
O campo `author` do exemplo (`Autor de Exemplo`) é apenas um valor de demonstração.
