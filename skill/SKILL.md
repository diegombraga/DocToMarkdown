---
name: DocToMarkdown
description: Converte qualquer arquivo (PDF, DOCX, PPTX, XLSX, imagens, HTML, EPUB, áudio, YouTube) em Markdown limpo — com OCR automático em PDFs escaneados. Use quando o usuário quiser transformar um arquivo em texto/markdown, extrair conteúdo legível de um documento, processar um PDF escaneado, preparar material para alimentar um LLM, ou dar contexto documental ao Claude. Triggers em português — "converter pra markdown", "extrair texto do PDF", "ler esse arquivo", "PDF escaneado", "OCR nesse documento", "transformar em md", "markdown desse arquivo", "esse PDF não abre / não copia texto". Triggers em inglês — "convert to markdown", "extract text from PDF", "OCR this scan", "read this document", "turn this into md".
---

# DocToMarkdown

Uma pipeline local e gratuita pra transformar qualquer arquivo em Markdown legível. Combina duas ferramentas open source:

- **[markitdown](https://github.com/microsoft/markitdown)** (Microsoft, MIT) — extrai conteúdo estruturado de PDF, DOCX, PPTX, XLSX, imagens, HTML, EPUB, áudio, YouTube, ZIP, notebooks, CSV, JSON, XML.
- **[ocrmypdf](https://github.com/ocrmypdf/OCRmyPDF)** (MPL-2.0) — adiciona camada de texto invisível em PDFs escaneados via Tesseract OCR.

## Quando invocar esta skill

Sempre que o usuário quiser **texto/markdown a partir de um arquivo**, mesmo que ele não use as palavras exatas. Exemplos:

- "Lê esse PDF pra mim" (arquivo referenciado) → converter e devolver o conteúdo
- "Faz um resumo desse contrato" (arquivo PDF/DOCX referenciado) → converter primeiro, depois resumir
- "Preciso do conteúdo desse arquivo em texto"
- "Esse PDF está com scan, dá pra extrair o texto?"
- "Transforma essa apresentação em markdown"
- "Extrai a tabela desse xlsx pra mim"

## Passo a passo obrigatório

### 1. Verificar dependências

Rode `which markitdown` (Unix) ou `where markitdown` (Windows). Se falhar:
- **macOS**: sugira `curl -fsSL https://raw.githubusercontent.com/diegombraga/DocToMarkdown/main/install.sh | bash`
- **Linux**: mesmo comando acima
- **Windows**: `iwr -useb https://raw.githubusercontent.com/diegombraga/DocToMarkdown/main/install.ps1 | iex`

Não continue sem as ferramentas.

### 2. Detectar o tipo do arquivo

Pela extensão. Formatos suportados diretamente por markitdown (sem OCR necessário):

`.docx` `.pptx` `.xlsx` `.xls` `.html` `.htm` `.epub` `.mp3` `.wav` `.m4a` `.mp4` `.zip` `.ipynb` `.csv` `.tsv` `.json` `.xml` `.png` `.jpg` `.jpeg`

PDF é um caso especial (ver passo 3).

### 3. PDF: detectar necessidade de OCR

Para `.pdf`, teste se tem camada de texto:

```bash
# Extrai texto da primeira página; se vier vazio, precisa de OCR.
pdftotext -l 1 "$arquivo" - 2>/dev/null | head -c 200
```

Se a saída for vazia ou conter menos de ~30 caracteres úteis, é um PDF escaneado — rode OCR primeiro:

```bash
# Detectar idioma dominante se der; senão default pra por+eng.
ocrmypdf -l por+eng --skip-text "$arquivo" "$arquivo_ocr"
```

Flags úteis do ocrmypdf:
- `--skip-text` — pula páginas que já têm texto (padrão seguro)
- `--force-ocr` — refaz OCR mesmo em páginas com texto (use se o texto embutido estiver corrompido/lixo)
- `-l por+eng` — múltiplos idiomas juntos, separados por `+`. Códigos ISO 639-2: `por` (português), `eng` (inglês), `spa` (espanhol), `fra` (francês), `deu` (alemão), `ita` (italiano), `jpn` (japonês), etc.

### 4. Converter para Markdown

```bash
markitdown "$arquivo" > "$arquivo.md"
```

Ou pipe direto se o resultado for pequeno:

```bash
markitdown "$arquivo" | head -c 5000
```

### 5. Salvar e reportar

Salve o `.md` no **mesmo diretório do arquivo original** (não em /tmp). Reporte ao usuário:
- Caminho absoluto do `.md` gerado
- Tamanho em caracteres
- Se foi feito OCR (e em qual idioma)
- Preview curto (200-500 chars) para confirmar qualidade

## Lote

Se o usuário passar múltiplos arquivos ou uma pasta:

- Até **3 arquivos**: processe sequencialmente
- **4 ou mais**: pergunte antes se deve rodar em paralelo (`xargs -P 4` no Unix ou `ForEach-Object -Parallel` no PowerShell)

Não processe silenciosamente uma pasta inteira sem confirmação — arquivos grandes com OCR podem levar minutos cada.

## Casos limite

- **Áudio/vídeo grande** (>50MB): avisa que a transcrição via markitdown vai levar tempo e usar CPU (usa SpeechRecognition local + ffmpeg). Sugere transcrever só se essencial.
- **PDF misto** (algumas páginas com texto, outras escaneadas): use `--skip-text` — ocrmypdf pula as que já têm texto.
- **PDF criptografado**: sugerir remover senha primeiro (`qpdf --decrypt --password=X in.pdf out.pdf`).
- **YouTube URL** ao invés de arquivo: markitdown suporta URLs de YouTube diretamente. Passe a URL como argumento.
- **Arquivos muito grandes** (>100MB): oferecer processar por partes; markitdown funciona mas o output pode ser gigantesco.

## Alternativa: interface web

Se o usuário instalou o app DocToMarkdown (veja o repo), ele pode preferir a interface web em `http://127.0.0.1:5555` — drag-and-drop, toggles, download, cópia. Mencione essa opção quando útil, especialmente se o usuário está processando múltiplos arquivos manualmente.

## Recursos

- Repo do projeto: <https://github.com/diegombraga/DocToMarkdown>
- Docs do markitdown: <https://github.com/microsoft/markitdown>
- Docs do ocrmypdf: <https://ocrmypdf.readthedocs.io>
- Códigos de idiomas do Tesseract: <https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html>
