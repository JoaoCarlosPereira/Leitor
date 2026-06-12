# Leitor

Plataforma de produção de audiolivros multi-voz a partir de PDFs, com LLM local (Qwen3.6-35B) e TTS local (Qwen3-TTS).

## Stack

- **Backend**: Python 3.11+ / FastAPI / Celery + Redis / SQLAlchemy 2.0
- **UI**: Jinja2 + HTMX (server-side rendering)
- **Banco**: PostgreSQL 16
- **IA local**: Qwen3.6-35B (LLM) em `http://192.168.2.112:8000/v1/`
- **TTS local**: Qwen3-TTS em `http://192.168.2.112:8881`

## Estrutura

```
app/             # FastAPI app, rotas, serviços, repositórios
  routes/        # Rotas HTML e REST
  services/      # PDF, LLM, TTS, personagens, catalogação
  repositories/  # SQLAlchemy 2.0 models e CRUD
  templates/     # Jinja2 + HTMX
tasks/           # Tarefas Celery
migrations/      # Alembic
sql/             # Schema base do banco
dataset/         # 500 vozes (wav + info.json)
tests/           # Testes unitários e integração
```

## Instalação

### Quick Install (Linux)

```bash
# 1. Clone o repositório
git clone <url> && cd leitor

# 2. Execute o instalador
chmod +x install.sh
./install.sh
```

O instalador automatiza: dependências do sistema, ambiente virtual, instalação de pacotes, configuração do `.env`, criação de diretórios, Docker Compose (opcional), migrations e geração de scripts de inicialização.

### Instalação Manual

```bash
# 1. Subir infraestrutura (PostgreSQL, Redis, Redis Commander)
docker compose up -d

# 2. Instalar dependências
python -m venv venv
source venv/bin/activate          # Linux/Mac
pip install -e "."

# 3. Aplicar migrations
alembic upgrade head

# 4. Configurar variáveis de ambiente
cp .env.example .env
# editar .env conforme necessário

# 5. Iniciar a aplicação
./start_all.sh                    # web + worker (produção)
# ou
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000  # desenvolvimento

# 6. Iniciar worker Celery (em outro terminal)
celery -A tasks.pipeline.celery_app worker --loglevel=info --concurrency=4
```

### Scripts de Inicialização

| Script | Uso |
|--------|-----|
| `./start.sh` | Apenas servidor web (produção) |
| `./start_worker.sh` | Apenas worker Celery |
| `./start_all.sh` | Web + worker juntos (produção) |

### Variáveis de Ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `DB_USER` | `leitor` | Usuário do PostgreSQL |
| `DB_PASSWORD` | `leitor` | Senha do PostgreSQL |
| `DB_NAME` | `leitor` | Nome do banco |
| `DB_HOST` | `localhost` | Host do PostgreSQL |
| `DB_PORT` | `5432` | Porta do PostgreSQL |
| `REDIS_HOST` | `localhost` | Host do Redis |
| `REDIS_PORT` | `6379` | Porta do Redis |
| `LLM_BASE_URL` | `http://192.168.2.112:8000/v1/` | Endpoint do LLM |
| `TTS_BASE_URL` | `http://192.168.2.112:8881` | Endpoint do TTS |
| `APP_PORT` | `8000` | Porta do servidor web |

## Endpoints

- `GET  /` — dashboard principal
- `GET  /livro/novo` — upload de PDF
- `GET  /livro/{id}/personagens` — revisão de personagens
- `GET  /livro/{id}/vozes` — configuração de vozes
- `GET  /livro/{id}/producao` — monitoramento de produção
- `GET  /livro/{id}/download` — download do audiolivro
- `GET  /fila` — fila de produção
- `GET  /health` — healthcheck
- API REST: ver `_techspec.md`

## Testes

```bash
pytest                      # executa todos os testes
pytest --cov=app --cov=tasks
pytest -m unit              # apenas unitários
pytest -m integration       # apenas integração
```

## Documentação do projeto

- PRD: `.docs/tasks/leitor/_prd.md`
- TechSpec: `.docs/tasks/leitor/_techspec.md`
- ADRs: `.docs/tasks/leitor/adrs/`
- Tasks: `.docs/tasks/leitor/task_*.md`
