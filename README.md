# OpsDeck

Dashboard pessoal de devops: cards de atalhos (projetos GCP, ferramentas,
programas locais), consultas salvas ao Cloud Logging e feeds de atividade de
orgs do GitHub. FastAPI + JS vanilla, sem build step.

## Rodar localmente

```bash
uv run main.py            # http://127.0.0.1:8765 (MySQL local + gcloud CLI)
uv run pytest -q          # testes
```

Requisitos locais: MySQL em `127.0.0.1:13306` (root/1234; outra porta via `DASHBOARD_DB_PORT`) e o `gcloud` autenticado.
Detalhes de arquitetura e comandos em [CLAUDE.md](CLAUDE.md).

## Feeds do GitHub

A aba GitHub lê a API do GitHub com o token do usuário. Localmente basta o `gh` logado (`gh auth login`), porque o app usa o `gh auth token` quando `GITHUB_TOKEN` não está definido. Em container, como no k3s, o token entra como variável de ambiente vinda de um Secret. Use um PAT clássico com os escopos `repo` e `read:org`, porque o feed da org inteira usa o dashboard da org do próprio usuário, que inclui os repositórios privados.

```bash
kubectl create secret generic opsdeck-github --from-literal=GITHUB_TOKEN=ghp_...
```

```yaml
# no container do Deployment
envFrom:
  - secretRef:
      name: opsdeck-github
```

O indicador `github` na barra do topo fica verde quando há token. As requisições são condicionais por ETag, então atualizar um feed sem novidades não consome cota.

## Container (k3s)

A imagem sai do [`Dockerfile`](Dockerfile) (distroless, sem shell, usuário não-root) e escuta na porta 8080. Em container não há `gcloud` nem `gh`, então a configuração vem toda de variáveis de ambiente:

| Variável | Valor no container |
|---|---|
| `DASHBOARD_DB_HOST`, `DASHBOARD_DB_PORT`, `DASHBOARD_DB_USER`, `DASHBOARD_DB_PASSWORD`, `DASHBOARD_DB_NAME` | o serviço MySQL do cluster |
| `DASHBOARD_LOG_PROVIDER` | `api`, com `GOOGLE_APPLICATION_CREDENTIALS` apontando para um arquivo ADC montado |
| `DASHBOARD_COMMANDS` | `off`, porque os cards de comando rodariam dentro do container |
| `GITHUB_TOKEN` | o token da aba GitHub, vindo de um Secret |

As imagens enviadas para os cards ficam em `/app/data/images`, que deve ser um volume.
