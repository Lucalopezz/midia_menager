# Servidor de mídia local

MVP local com Jellyfin em Docker e um downloader sequencial de vídeos baseado em
`yt-dlp`. Todo o estado fica dentro deste diretório.

## Estrutura

```text
.
├── compose.yml
├── jellyfin/
│   ├── config/
│   └── cache/
├── library/
│   ├── youtube/
│   ├── filmes/
│   └── series/
└── downloader/
    ├── download.py
    ├── downloaded.txt  # criado após o primeiro download
    └── logs/
```

Configurações, cache, mídias, logs e o arquivo `downloaded.txt` são dados de
runtime e não são versionados.

## Jellyfin

É necessário ter Docker com o plugin Docker Compose instalado.

Suba o Jellyfin:

```bash
docker compose up -d
```

Comandos úteis:

```bash
docker compose ps
docker compose logs -f jellyfin
docker compose down
```

Acesse [http://localhost:8096](http://localhost:8096). A porta está vinculada
apenas a `127.0.0.1`, portanto o serviço não fica exposto para outros
computadores da rede.

No assistente inicial, crie as bibliotecas usando estes caminhos internos do
container:

- YouTube: `/media/youtube`
- Filmes: `/media/filmes`
- Séries: `/media/series`

O Jellyfin enxerga `library/` como somente leitura. Os downloads devem ser
feitos pelo script no sistema hospedeiro.

## Downloader

O `yt-dlp` atual requer Python 3.10 ou mais recente. Crie um ambiente virtual na
raiz do projeto e instale a dependência:

```bash
python --version
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r downloader/requirements.txt
```

Baixe uma ou várias URLs, processadas sequencialmente:

```bash
python downloader/download.py URL1
python downloader/download.py URL1 URL2 URL3
python downloader/download.py --category akita URL1 URL2 URL3
```

Consulte todas as opções:

```bash
python downloader/download.py --help
```

Sem `--category`, o destino é `library/youtube/`. Com, por exemplo,
`--category akita`, o destino passa a ser `library/youtube/akita/`. A pasta é
criada automaticamente. Categorias aninhadas como `cursos/python` também são
aceitas, desde que permaneçam dentro de `library/youtube/`.

O arquivo `downloader/downloaded.txt` é o download archive do `yt-dlp`: vídeos
já registrados nele não são baixados novamente. O histórico de cada URL é
acrescentado a `downloader/logs/download.log`; ele não é apagado entre
execuções. Se houver falhas, as demais URLs ainda serão tentadas e o programa
terminará com código de saída `1` depois de exibir o resumo.

```text
YouTube URL
    ↓
download.py
    ↓
yt-dlp
    ↓
library/youtube/<categoria>/
    ↓
Jellyfin
    ↓
Browser
```

### Dependências externas

O formato escolhido prioriza vídeo H.264 e áudio AAC, com saída MP4 e limite de
1080p. O YouTube normalmente oferece as melhores faixas de vídeo e áudio em
arquivos separados, e o `yt-dlp` precisa dos binários `ffmpeg` e `ffprobe` para
uni-las. Verifique a instalação com:

```bash
ffmpeg -version
ffprobe -version
```

Não instale o pacote Python chamado `ffmpeg`; são necessários os binários do
sistema operacional.

Para suporte completo ao YouTube, versões atuais do `yt-dlp` também recomendam
um runtime JavaScript. O Deno é a opção recomendada e é detectado
automaticamente quando está no `PATH`:

```bash
deno --version
```

O arquivo `requirements.txt` usa `yt-dlp[default]`, que inclui o componente EJS
necessário, mas não instala Deno nem ffmpeg. O script não tenta instalar pacotes
do sistema automaticamente.

Use o downloader somente para conteúdo que você tenha autorização para baixar.
