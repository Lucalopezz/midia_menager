# Servidor de mídia local

Servidor local com Jellyfin em Docker e uma interface web para o downloader
sequencial de vídeos baseado em `yt-dlp`. Todo o estado fica dentro deste diretório.

## Início rápido

```bash
mkdir -p downloader/state downloader/logs library/youtube
touch downloader/downloaded.txt
docker compose up -d --build
```

- Downloads: [http://localhost:8080](http://localhost:8080)
- Jellyfin: [http://localhost:8096](http://localhost:8096)

Na página de downloads, cole uma URL por linha, escolha uma categoria opcional
e clique em **Adicionar à fila**. Vídeos e playlists são processados em ordem.
A página mostra a porcentagem da faixa em download, velocidade, tempo restante
e a etapa de finalização. Quando vídeo e áudio vêm separados, cada faixa tem
sua própria porcentagem; em playlists, a página também mostra o item atual.
Quando o servidor não informa o tamanho, a barra indica atividade sem inventar
uma porcentagem. A atualização acontece a cada segundo.

Você pode fechar a página: o trabalho continua no servidor. A fila e o histórico
ficam em `downloader/state/queue.sqlite3`; downloads interrompidos voltam à fila
depois de reiniciar o container. O archive e os logs são os mesmos do script.
Falhas não interrompem a fila e podem ser tentadas novamente pela interface.
A página exibe todos os pendentes e os 100 últimos downloads finalizados.

O container já inclui Python, `yt-dlp`, ffmpeg e Deno. Os arquivos são gravados
em `library/youtube/<categoria>/`, compartilhado com o Jellyfin. Para que novos
vídeos apareçam, habilite o monitoramento da biblioteca no Jellyfin ou execute
uma atualização da biblioteca.

O downloader escuta apenas em `127.0.0.1:8080` por padrão. Para acessá-lo de
outros dispositivos da sua rede, configure `DOWNLOADER_BIND=0.0.0.0` em um
arquivo `.env` ao lado do Compose. A interface é destinada à rede local e não
tem login. `DOWNLOADER_PORT` permite trocar a porta.

O Compose usa UID/GID `1000:1000` para os arquivos. Caso seu usuário tenha outros
IDs, configure `LOCAL_UID` e `LOCAL_GID` no `.env`, consultando `id -u` e `id -g`.
As pastas e o archive devem existir e ser graváveis por esse usuário antes de subir.

### Cookies do YouTube no Docker

O script no terminal mantém o uso dos cookies do Firefox. Dentro do container,
a interface funciona sem cookies por padrão. Se o YouTube exigir autenticação,
coloque um arquivo de cookies no formato Netscape em
`downloader/state/cookies.txt`. Ele será usado nos próximos downloads,
sem reconstruir a imagem. O arquivo não é versionado.

Você pode gerar esse arquivo no host com o Firefox já autenticado:

```bash
source .venv/bin/activate
python -m yt_dlp --cookies-from-browser firefox --cookies downloader/state/cookies.txt
chmod 600 downloader/state/cookies.txt
```

O arquivo contém sua sessão; mantenha-o privado. A exportação pelo navegador é
descrita na [documentação do yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).

Para atualizar a interface e as dependências do container:

```bash
docker compose build --pull --no-cache downloader
docker compose up -d downloader
docker compose logs -f downloader
```

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
    ├── Dockerfile
    ├── download.py
    ├── web.py
    ├── static/         # interface HTML, CSS e JavaScript
    ├── state/          # fila persistente e cookies opcionais
    ├── downloaded.txt  # criado após o primeiro download
    └── logs/
```

Configurações, cache, mídias, fila, cookies, logs e o arquivo `downloaded.txt` são dados de
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

Acesse [http://localhost:8096](http://localhost:8096). Na configuração atual,
o Jellyfin também está disponível para outros computadores da rede na porta 8096.

No assistente inicial, crie as bibliotecas usando estes caminhos internos do
container:

- YouTube: `/media/youtube`
- Filmes: `/media/filmes`
- Séries: `/media/series`

O Jellyfin enxerga `library/` como somente leitura. O container do downloader
grava os arquivos nessa mesma biblioteca, e o script também pode ser usado no host.

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

Baixe uma ou várias URLs de vídeos ou playlists, processadas sequencialmente:

```bash
python downloader/download.py URL1
python downloader/download.py URL1 URL2 URL3
python downloader/download.py --category akita URL1 URL2 URL3
python downloader/download.py --category musica "https://www.youtube.com/playlist?list=ID_DA_PLAYLIST"
```

Consulte todas as opções:

```bash
python downloader/download.py --help
```

Sem `--category`, o destino é `library/youtube/`. Com, por exemplo,
`--category akita`, o destino passa a ser `library/youtube/akita/`. A pasta é
criada automaticamente. Categorias aninhadas como `cursos/python` também são
aceitas, desde que permaneçam dentro de `library/youtube/`.

Quando uma URL aponta para uma playlist, todos os vídeos disponíveis nela são
baixados na categoria escolhida. O `yt-dlp` processa os itens da playlist em
ordem e adiciona a posição ao começo de cada nome (`1. Nome do vídeo`, `2. Nome
do vídeo`, etc.), facilitando a ordenação no Jellyfin. Downloads avulsos
continuam sem esse prefixo. O processamento continua com os próximos itens caso
algum esteja indisponível. O mesmo comando também pode misturar URLs de vídeos
e playlists. Coloque a URL entre aspas para que caracteres como `&` não sejam
interpretados pelo shell.

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

## Desenvolvimento da interface

Depois de instalar `downloader/requirements.txt` no ambiente virtual:

```bash
python -m uvicorn downloader.web:app --host 127.0.0.1 --port 8080
```

Execute apenas uma instância do backend (um worker) por pasta de estado.
Use uma porta diferente se o container já estiver rodando. Para os testes da API
e da fila, que usam pastas temporárias e downloads simulados:

```bash
pip install httpx
python -m unittest downloader.test_web -v
```
