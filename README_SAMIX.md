# SAMIX — Estúdio de Música local

SAMIX é a camada de intenção, organização e revisão musical do Dream Fabrics.
O ACE-Step 1.5 é o motor de síntese, executado **separadamente** na mesma
máquina. O aplicativo Dream Fabrics fala apenas com `127.0.0.1`; não usa um
serviço de geração hospedado, não inicia o motor automaticamente e não baixa
pesos por conta própria.

## Arquitetura e fluxo

1. Escolha um projeto existente no Dream Fabrics e abra **SAMIX · Estúdio de
   Música**. Escreva título, intenção, mood, estrutura, letra opcional, duração,
   idioma vocal, BPM, tonalidade, compasso, semente e passos.
2. **Salvar direção** grava um rascunho em
   `.factory-data/<id-do-projeto>/music/tracks.json`. Nenhuma geração começa.
   Em **Referência sonora**, selecione e ouça um MP3 ou WAV de até 25 MB.
   **Salvar referência no projeto** grava o arquivo na pasta `music/` com nome
   interno gerado pelo SAMIX e registra seus metadados em `tracks.json`.
   O nome original nunca define o caminho. A seleção e o salvamento não iniciam
   geração. Ao reabrir a faixa, a referência fica disponível para reprodução.
3. Com a API local do ACE-Step ativa, marque a confirmação e clique em
   **Gerar faixa local**. O SAMIX chama `POST /release_task` em `127.0.0.1:8001`.
   A semente é fixa, `sample_mode=false`, `use_format=false` e
   `use_random_seed=false`; mood e estrutura são incorporados ao prompt.
   Quando há referência, o pedido usa `multipart/form-data` com
   `reference_audio` e `task_type=text2music`, conforme a
   [API oficial](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md).
   Sem referência, continua usando JSON. O navegador só envia ao servidor
   local Dream Fabrics; apenas a confirmação de geração envia ao ACE-Step.
4. O aplicativo consulta `POST /query_result`. Quando a tarefa termina, obtém
   o arquivo por `GET /v1/audio?path=...` da **mesma origem local** e salva WAV
   na pasta `music/` do projeto. A Audioteca passa a listar a faixa.

O identificador de tarefa e o estado são persistidos. Ao reabrir, a interface
pode continuar consultando uma tarefa pendente, desde que o mesmo ACE-Step
local ainda a reconheça. Faixas produzidas permanecem no projeto mesmo se o
motor estiver desligado. Uma direção pode ser associada a um capítulo; a
mixagem automática com narração fica para a próxima etapa, quando houver
processamento de áudio compatível. O campo `style_profile` no catálogo está
reservado para LoRA, sem fingir que essa função já está pronta.

## Preparar o motor

Consulte a [instalação oficial do ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INSTALL.md)
e sua [API REST](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md).
O projeto oficial recomenda Python 3.11–3.12 e CUDA, embora documente caminhos
para CPU e outros aceleradores. A primeira instalação/execução pode precisar
de internet para dependências e pesos; depois de tudo em cache, a geração pode
ocorrer offline. Mantenha a API em `127.0.0.1` e inicie-a separadamente:

```powershell
cd <pasta-do-ACE-Step-1.5>
uv run acestep-api
```

O porto padrão é 8001. Se alterá-lo, defina `DREAM_FABRICS_ACE_PORT` no ambiente
do Dream Fabrics antes de iniciar `START_FACTORY.ps1`. Se a API tiver uma chave
local, defina também `ACESTEP_API_KEY` no ambiente; o navegador não recebe essa
chave. Não coloque o repositório/pesos do ACE-Step dentro do Dream Fabrics.

O computador inspecionado nesta etapa mostrou **Intel UHD Graphics**, sem GPU
dedicada identificada. A integração foi testada com uma API local simulada;
não instalamos pesos nem executamos inferência real. O desempenho em CPU/Intel
UHD permanece por medir antes de prometer duração ou qualidade de geração.

## Licença e uso

O [código do ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5/blob/main/LICENSE)
e o [modelo principal publicado no Hugging Face](https://huggingface.co/ACE-Step/Ace-Step1.5)
estão marcados como **MIT**. A licença permite uso, modificação e distribuição,
inclusive comercial, com preservação do aviso de licença nas cópias. Verifique
a licença de cada peso, LoRA ou componente adicional que instalar. A licença
MIT não substitui a revisão da originalidade da música produzida e de direitos
de terceiros em letras ou materiais de referência.

## Princípios de orientação lógica

- Os dados ficam em `.factory-data/` até a decisão de gerar ou exportar.
- O formulário separa intenção, mood, estrutura, letras e parâmetros medíveis.
- A geração usa os valores salvos, semente fixa e confirmação explícita.
- O motor é um adaptador isolado em `factory_music.py`; a futura mixagem,
  LoRA, texturas, frequências e bowls podem entrar como novos processadores,
  mantendo o catálogo e a Audioteca.

## Limites da referência sonora

O SAMIX verifica os bytes do arquivo: estrutura RIFF/WAV e quadros MPEG Layer III
consecutivos para MP3. Essa verificação rejeita arquivos evidentemente falsos ou
truncados, mas não substitui uma decodificação completa; FFprobe pode ser
integrado numa etapa posterior. A referência pode orientar características
acústicas, mas o SAMIX não garante que o modelo evitará semelhanças melódicas.
Use somente materiais com direitos adequados. Não há conversão automática nem
dependência obrigatória de FFmpeg nesta versão.

## Estado desta etapa

O módulo, armazenamento, contrato REST local, consulta de tarefa, salvamento
de WAV e Audioteca estão implementados. **Não há síntese comprovada com pesos
reais neste computador.** Mixagem de narração com música e treinamento LoRA
estão marcados como futuros.

SAMIX 1.1 acrescenta referência sonora e envio multipart. Os testes cobrem
upload WAV, persistência, validação, envio com e sem referência e saída na
Audioteca usando um ACE-Step simulado. A geração real e um MP3 real ainda
precisam de validação na máquina com ACE-Step/arquivo disponíveis.
