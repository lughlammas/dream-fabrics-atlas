# Dream Fabrics — Audiobook Factory

Aplicação web local, baseada em Python 3.12+ e biblioteca padrão. O compilador
editorial `atlas.py` e a interface Tkinter `atlas_gui.py` continuam disponíveis.
O novo aplicativo usa as funções de leitura e preparação de narração do ATLAS.

## Iniciar no Windows PowerShell

Na pasta do repositório:

```powershell
.\START_FACTORY.ps1
```

Abra `http://127.0.0.1:18765`. O servidor escuta apenas em `127.0.0.1`.
Se o PowerShell bloquear scripts, execute diretamente com Python 3:

```powershell
py -3 .\factory_server.py
```

Para usar outro diretório de dados ou outra porta:

```powershell
py -3 .\factory_server.py --data 'D:\Meus Audiobooks' --port 8766
```

## Voz real

O provedor implementado é a [OpenAI Speech API](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create),
com o modelo `gpt-4o-mini-tts-2025-12-15`. O limite oficial é 4096 caracteres por
requisição; a aplicação divide o texto em segmentos menores. Vozes, velocidade
(0,25–4×), instruções expressivas, MP3 e WAV correspondem a parâmetros da API.
Uma chave de API própria pode gerar cobrança. Configure-a apenas no ambiente:

```powershell
$env:OPENAI_API_KEY = '<sua chave>'
.\START_FACTORY.ps1
```

A chave nunca é enviada ao navegador nem gravada no projeto. O texto de cada
produção ou amostra só é enviado depois de marcar a caixa de consentimento
correspondente. Sem chave, a criação e edição locais continuam disponíveis.
O provedor está isolado em `factory_tts.py` para futuras integrações.

## Fluxo

1. Crie uma obra com título, autor, idioma, descrição e capa opcional.
2. Importe TXT ou Markdown. O arquivo original fica em `originals/`; os capítulos
   editáveis ficam em `chapters/`. Markdown é dividido por títulos `#` e `##`.
3. Edite, salve e reorganize capítulos. O manifesto ATLAS define a ordem.
4. Selecione narrador e opções de voz. Gere uma prévia se desejar.
5. Selecione capítulos e confirme o envio ao provedor para iniciar a produção.
   Segmentos concluídos ficam em cache para repetição após falhas.
6. Revise e baixe cada capítulo na Audioteca; exporte todos em ZIP.
7. Compile TXT e EPUB pelo ATLAS na seção Exportação.

Dados locais ficam em `.factory-data/`, que está no `.gitignore`. Não coloque
credenciais, manuscritos privados nem áudios gerados sob controle de versão.

## Limites atuais

- DOCX, PDF e EPUB como **entrada** estão identificados como indisponíveis; a
  importação aceita TXT e Markdown.
- O compilador original gera PDF opcionalmente com WeasyPrint, mas a interface
  nova usa TXT e EPUB como saídas padrão.
- ZIP contém capítulos individuais. M4B e um arquivo único com marcadores de
  capítulos dependem de uma etapa futura de conversão de áudio.
- MP3 de vários segmentos usa concatenação de quadros; alguns players podem
  apresentar duração imprecisa. WAV é combinado com cabeçalho refeito.
- A aplicação é para uso local de um usuário; não exponha a porta na rede.

## Testes

```powershell
py -3 -m unittest discover -s tests -v
```

Futuro: importadores DOCX/EPUB/PDF, conversão M4B com capítulos e metadados,
direção editorial com IA (briefing, sumário, revisão e roteiro), mais provedores
de voz e controle refinado de versões dos manuscritos.

## SAMIX

O Estúdio de Música local está documentado em [README_SAMIX.md](README_SAMIX.md).

