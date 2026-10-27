# ATLAS — instalação e uso da interface gráfica

## Requisitos
- Python 3.9 ou mais novo, **com tkinter** (vem no instalador do python.org; mantenha marcado *tcl/tk and IDLE*).
- Nada mais: só biblioteca padrão, funciona offline. (PDF é opcional e exige WeasyPrint; sem ele a saída `pdf` é pulada com aviso.)

## Windows 10/11
1. Copie a pasta do projeto (com `atlas.py`, `atlas_gui.py` e `ATLAS.bat`) para qualquer lugar.
2. Dê dois cliques em **ATLAS.bat**. Ele procura o Python nesta ordem: `py -3`, depois
   `%LOCALAPPDATA%\Programs\Python\Python3*`, depois `python` (só se funcionar de verdade;
   os atalhos da Microsoft Store são ignorados). A janela abre sem console preto.
3. Se aparecer "nao encontrei um Python", instale o Python 3 e repita.

## Linux
```
python3 atlas_gui.py            # Debian/Ubuntu: sudo apt install python3-tk
```

## Usar
1. **Escolher pasta** da obra (ou digite o caminho e tecle Enter).
2. Pasta nova? **Iniciar obra** cria `manifest.json` e `chapters/00_abertura.md` (não sobrescreve).
3. Edite id, título, autor, volume, idioma, a ordem dos capítulos (Subir/Descer) e as saídas,
   e clique **Salvar manifesto**. O autor não pode ficar vazio.
4. **Validar** confere o manifesto e os capítulos; **Compilar** gera tudo em `<pasta>/build/`.
5. **Abrir pasta build** abre o resultado. O log mostra erros (vermelho) e avisos (laranja).

A pasta `chapters/` nunca é alterada pela interface; só `manifest.json` é gravado, ao salvar.

## Testes
```
python3 -m unittest discover -s tests -v
```
Os testes da janela rodam se houver tkinter e display (no Linux sem tela: `xvfb-run -a python3 -m unittest discover -s tests`); senão são pulados.
