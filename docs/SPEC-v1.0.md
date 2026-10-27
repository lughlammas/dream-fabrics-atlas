# ATLAS — Fábrica de Livros (Dream Fabrics)
## Arquitetura operacional v1.0 (especificação inicial)

Status: proposta de especificação. Nada aqui está instalado ou executado ainda.

ATLAS é um sistema de produção editorial baseado em código-fonte textual: transforma capítulos independentes em obras compiladas, publicáveis e reutilizáveis por outros motores.

Fluxo: TXT / MD (texto soberano) -> compilação -> EPUB / PDF (publicação) e RAGNAROK (síntese vocal).

## Três elementos
- Obra: o conteúdo intelectual, preservado em texto puro.
- Compilador: interpreta manifestos, ordena capítulos e constrói os arquivos finais.
- Distribuição: os destinos da obra (livro digital, impresso, audiolivro).

## Cinco decisões de engenharia
1. `manifest.json` é a autoridade editorial. A ordem de leitura vem do manifesto, não dos nomes dos arquivos.
2. `chapters/` é imutável durante a compilação. A ATLAS nunca sobrescreve os originais.
3. Cada compilação tem validação automática: capítulos ausentes, duplicados, arquivos inválidos e metadados incompletos são identificados antes do build.
4. O Ragnarok recebe saída específica: `audiobook.txt`, texto linear, sem sintaxe Markdown, preservando pausas estruturais e títulos adequados à narração.
5. Versionamento de obras, preferencialmente com Git local, para manter o histórico editorial entre revisões.

## Manifesto mínimo proposto
```json
{
  "atlas_version": "1.0",
  "id": "tomo_02_calistenia",
  "title": "Calistenia",
  "author": "",
  "volume": 2,
  "language": "pt-BR",
  "chapters": [
    "00_abertura.md",
    "01_capitulo_um.md"
  ],
  "outputs": ["txt", "epub", "pdf", "ragnarok"]
}
```

## Nota sobre soberania
Independência de nuvens e serviços comerciais não significa ausência de dependências. EPUB e PDF exigem bibliotecas ou ferramentas locais. A soberania se cumpre quando todas elas funcionam offline, sem autenticação externa.

## Próximo marco
Compilador local `atlas.py` com três comandos: `init`, `validate` e `build`.

## Pendências técnicas (os 6 pontos restantes da avaliação 94/100)
A formalizar: [PREENCHER — dado do autor]
