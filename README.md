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

Interface gráfica (tkinter): `python3 atlas_gui.py` ou `ATLAS.bat` no Windows — ver [INSTALAR.md](INSTALAR.md).

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
metadado ausente ou vazio (inclui `author` vazio); **metadado com marcador de pendência**
(qualquer valor do manifesto contendo `[PREENCHER`, sem distinguir maiúsculas, em `author`, `title`, `id`
ou qualquer outro campo: erro "metadado pendente: ..."); `id`/`language` malformados; `outputs`
vazio, desconhecido ou duplicado; manifesto inexistente ou JSON inválido.
Geram apenas **aviso** (exit 0): arquivos em `chapters/` que não estão no manifesto (são ignorados) e
capítulos que contêm `[PREENCHER` (o aviso informa quantas ocorrências por capítulo).

### Saídas do `build` (apenas as listadas em `outputs`)
| output | arquivo | observação |
|---|---|---|
| `txt` | `build/<id>.txt` | título, autor e os capítulos na ordem do manifesto, Markdown preservado, exceto: tabelas viram texto monoespaçado alinhado (cabeçalho, linha de `-`, sem `|`) e `\newpage` & cia viram uma linha em branco |
| `epub` | `build/<id>.epub` | EPUB 3; `mimetype` primeiro e sem compressão; `nav.xhtml`; um XHTML por capítulo; tabelas viram `<table>` com `thead`/`tbody` |
| `pdf` | `build/<id>.pdf` | via WeasyPrint se instalado; tabelas viram `<table>` com borda e cabeçalho repetido entre páginas; senão **aviso e a saída é pulada** (build termina com código 0, sem PDF) |
| `ragnarok` | `build/audiobook.txt` | texto linear sem sintaxe Markdown; títulos preservados como linhas; parágrafos separados por linha em branco; `[pausa longa]` entre capítulos; `[pausa]` no lugar de linha horizontal (`---`) e antes de cada tabela; cada linha de tabela vira uma frase `Cabeçalho1: valor, Cabeçalho2: valor.` (células vazias são omitidas); `\newpage` & cia viram `[pausa longa]` |

Markdown suportado: títulos `#`, parágrafos, ênfase/negrito, código inline e em bloco,
citações, listas de um nível, linha horizontal, links `http(s)` (no EPUB/PDF; na narração
só o texto), **tabelas "pipe"** (cabeçalho + linha `|---|`, alinhamento `:---`, `---:`, `:---:`,
`\|` para barra literal) e os comandos de linha única `\newpage`, `\pagebreak`, `\clearpage`,
`\cleardoublepage` (quebra de página no EPUB/PDF via CSS `page-break-after`; comandos seguidos
valem uma quebra só). Dentro de bloco de código (```) tabelas e `\newpage` ficam literais.
O mesmo comando no meio de uma linha de texto é apenas removido (sem quebra).

**Limitações reais restantes:**
- Não suportado: listas aninhadas, imagens (viram o texto alternativo), notas de rodapé, tabelas sem
  linha separadora ou com células de várias linhas, outros comandos LaTeX (aparecem como texto).
- HTML cru: no ragnarok as tags são removidas; no EPUB/PDF são escapadas (aparecem como texto).
- Tabelas largas (ex.: 8 colunas) no PDF A5: cabem na página, mas cabeçalhos e células estreitas podem
  quebrar no meio da palavra. No EPUB a quebra depende do leitor (não testado em leitores reais).
- No `txt` o texto da tabela não é Markdown: o `txt` deixa de ser reprocessável como Markdown nesse trecho.
- A quebra de página no EPUB é uma `div` com CSS; leitores podem ignorá-la.
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
claramente pulado, inclusive sem WeasyPrint simulado), EPUBCheck (pulado se o jar não for informado),
tabelas em txt/epub/pdf/ragnarok, `\newpage` & cia nas quatro saídas, `[PREENCHER` (erro no manifesto,
aviso nos capítulos). Os testes de PDF só exercitam o PDF de verdade onde há WeasyPrint e `pdftotext`.

### O que foi realmente verificado
Verificado em 27/10/2026 na box: suíte unittest completa (89 testes) passando com o Python
3.13.5 do sistema (WeasyPrint 70.0, sem tkinter: testes da GUI pulados) e com o Python 3.12.15 do
`uv` (com tkinter, sem WeasyPrint: testes de PDF só verificam o aviso de saída pulada); EPUB do exemplo
e de um livro real de teste (Tomo I, 8 tabelas e vários `\newpage`) validados com EPUBCheck 5.1.0
(0 erros, 0 avisos); PDF desse livro gerado com WeasyPrint (76 páginas), texto conferido com
`pdftotext`: tabelas presentes, nenhuma linha começando com `| `, nenhum `\newpage`.
**Não verificado:** EPUB em leitores reais (Kindle, Apple Books etc.); PDF com imagens ou fontes
específicas; Windows/macOS; Python anterior a 3.12; síntese de voz no Ragnarok (o `audiobook.txt`
só foi conferido como texto).

## Pendências
Os pontos técnicos ainda a formalizar estão em `docs/SPEC-v1.0.md` (`[PREENCHER — dado do autor]`).
O campo `author` do exemplo (`Autor de Exemplo`) é apenas um valor de demonstração.
