---
name: DocToMarkdown
description: Converte qualquer arquivo (PDF, DOCX, PPTX, XLSX, imagens, HTML, EPUB, áudio) ou URL de vídeo (YouTube, Vimeo, etc.) em Markdown com contexto completo. Suporta OCR em PDFs escaneados, transcrição local via Whisper, extração de texto na tela via OCR de frames, e descrição visual scene-by-scene via LLM. Use quando o usuário quiser transformar arquivo/vídeo em texto/markdown, extrair conteúdo, processar PDF escaneado, transcrever/analisar um vídeo do YouTube com contexto visual, ou preparar material para alimentar um LLM. Triggers em português — "converter pra markdown", "extrair texto do PDF", "PDF escaneado", "transcrever vídeo", "analisar esse YouTube", "markdown desse vídeo", "resumir esse vídeo", "extrair contexto do vídeo". Triggers em inglês — "convert to markdown", "extract text", "OCR this scan", "transcribe this video", "analyze this YouTube", "get context from video".
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

## Preferência de canal — MCP primeiro

Se o Claude tem acesso ao **MCP server `doctomarkdown`** (ferramentas `convert_file`, `process_video`, `preview_video`, `list_supported_formats`, `get_provider_status`, `set_api_key`, `delete_api_key`), **use essas tools nativas** em vez de subprocessos. Elas são mais confiáveis e não dependem da UI web estar rodando.

Se as tools MCP não estiverem disponíveis (checar o tool palette antes), caia pra HTTP na porta 5555 (se a UI estiver rodando) ou pra subprocess `markitdown`/`ocrmypdf` diretamente.

## Passo a passo obrigatório (fallback sem MCP)

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

## URLs de vídeo (YouTube, Vimeo, etc.)

Quando o usuário passar uma URL de vídeo em vez de arquivo, use a interface web do DocToMarkdown (se rodando) ou a CLI equivalente. A ferramenta processa vídeo em 5 fases:

1. **yt-dlp** baixa metadata + MP3 do áudio + (opcional) MP4 + legendas nativas
2. **Transcript**: legenda manual se existe (mais fiel); auto-gerada como fallback; Whisper local se nada disso serve
3. **Frames**: PySceneDetect extrai um keyframe por corte de cena
4. **OCR**: Tesseract em cada frame → captura texto na tela (slides, chyrons, whiteboard)
5. **Vision LLM** (opcional, BYOK): descreve o que se vê em cada cena

Output: Markdown com timeline unificada — `🎤 fala + 🖥 texto na tela + 👁 descrição visual`.

### Como acionar

Se o app DocToMarkdown está rodando (`curl http://127.0.0.1:5555/health` retorna 200), abra o navegador na tab **Vídeo/URL** e mostre ao usuário como usar.

Se não está rodando, ou o usuário prefere linha de comando, use `curl` na API:

```bash
# 1. Kick off
JOB=$(curl -s -X POST http://127.0.0.1:5555/video/process \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtube.com/watch?v=XXX","transcript_mode":"auto","vision_provider":null}' \
  | jq -r .job_id)

# 2. Wait for done (polling)
until curl -sf "http://127.0.0.1:5555/video/result/$JOB" >/dev/null; do sleep 2; done

# 3. Get result
curl -s "http://127.0.0.1:5555/video/result/$JOB" | jq -r .markdown > video.md
```

### Vision LLM (BYOK)

Se o usuário quer descrição visual completa das cenas, ele precisa de uma chave de API:

- `ANTHROPIC_API_KEY` → Claude Sonnet 4.5 vision
- `OPENAI_API_KEY` → GPT-4o mini
- `GEMINI_API_KEY` → Gemini 2.0 Flash

Passe no body do POST: `"vision_provider": "anthropic"` (ou `openai`/`gemini`). O env var deve estar setado no processo que roda o app.

### Cuidados

- Vídeos com legenda manual são MUITO mais rápidos que Whisper (5s vs 5min pra 30min de conteúdo)
- Whisper baixa modelo na 1ª execução (~150MB pro `base`)
- Vídeos >30min com vision LLM podem custar US$0.10-0.50 dependendo do provider — avise o usuário

## Alternativa: interface web

Se o usuário instalou o app DocToMarkdown (veja o repo), ele pode preferir a interface web em `http://127.0.0.1:5555` — drag-and-drop, tabs Arquivo/Vídeo, download, cópia. Mencione essa opção quando útil, especialmente se o usuário está processando múltiplos arquivos manualmente.

## Recursos

- Repo do projeto: <https://github.com/diegombraga/DocToMarkdown>
- Docs do markitdown: <https://github.com/microsoft/markitdown>
- Docs do ocrmypdf: <https://ocrmypdf.readthedocs.io>
- Códigos de idiomas do Tesseract: <https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html>
