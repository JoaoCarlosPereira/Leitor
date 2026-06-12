#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Leitor — Instalador de Instalação Rápida (Linux)
# Versão: 0.1.0
# =============================================================================

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Variáveis do projeto
PROJECT_NAME="leitor"
PROJECT_DIR=""
VENV_DIR="venv"
PYTHON_MIN="3.11"
DB_USER="${DB_USER:-leitor}"
DB_PASSWORD="${DB_PASSWORD:-leitor}"
DB_NAME="${DB_NAME:-leitor}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
LLM_BASE_URL="${LLM_BASE_URL:-http://192.168.2.112:8000/v1/}"
TTS_BASE_URL="${TTS_BASE_URL:-http://192.168.2.112:8881}"
APP_PORT="${APP_PORT:-8000}"
APP_HOST="${APP_HOST:-0.0.0.0}"
USE_DOCKER="no"

# =============================================================================
# Funções de logging
# =============================================================================
info()    { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
success() { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
error()   { printf "${RED}[ERR]${NC} %s\n" "$*" >&2; }
header()  { printf "\n${CYAN}═%.0s" $(seq 1 60); echo; printf "${CYAN}%s${NC}\n" "$*"; printf "${CYAN}═%.0s" $(seq 1 60); echo; }

# =============================================================================
# Funções utilitárias
# =============================================================================

# Substitui um valor no .env (usado por setup_env e setup_docker)
set_env_var() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" .env
    fi
}

# =============================================================================
# Verifica se está rodando como root
# =============================================================================
check_root() {
    if [ "$(id -u)" -eq 0 ]; then
        error "Este instalador não deve ser rodado como root."
        error "Use um usuário normal com permissão de sudo (se necessário)."
        exit 1
    fi
}

# =============================================================================
# Verifica se o diretório é um repositório git válido
# =============================================================================
check_repo() {
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        error "O diretório atual não parece ser um repositório git do Leitor."
        error "Clone o repositório primeiro: git clone <url> && cd leitor"
        exit 1
    fi
    success "Repositório git detectado."
}

# =============================================================================
# Verifica e instala dependências do sistema
# =============================================================================
install_system_deps() {
    header "Verificando Dependências do Sistema"

    # Detecta o gerenciador de pacotes
    if command -v apt-get &>/dev/null; then
        PKG_MGR="apt"
        PKG_INSTALL="sudo apt-get install -y"
        PKG_UPDATE="sudo apt-get update"
    elif command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
        PKG_INSTALL="sudo dnf install -y"
        PKG_UPDATE="sudo dnf update -y"
    elif command -v yum &>/dev/null; then
        PKG_MGR="yum"
        PKG_INSTALL="sudo yum install -y"
        PKG_UPDATE="sudo yum update -y"
    elif command -v pacman &>/dev/null; then
        PKG_MGR="pacman"
        PKG_INSTALL="sudo pacman -Sy --noconfirm"
        PKG_UPDATE=""
    elif command -v zypper &>/dev/null; then
        PKG_MGR="zypper"
        PKG_INSTALL="sudo zypper install -y"
        PKG_UPDATE="sudo zypper refresh"
    else
        warn "Gerenciador de pacotes não detectado. Instale manualmente:"
        warn "  Python 3.11+, pip, git, e (opcional) docker-compose"
        return 0
    fi

    # Atualiza pacotes (exceto pacman)
    if [ -n "$PKG_UPDATE" ]; then
        info "Atualizando lista de pacotes..."
        sudo $PKG_UPDATE
    fi

    # Pacotes necessários
    local pkgs=("python3" "python3-pip" "git")

    case "$PKG_MGR" in
        apt)
            pkgs+=("python3-venv" "build-essential" "libpq-dev" "libffi-dev" "libssl-dev")
            ;;
        dnf|yum)
            pkgs+=("python3-devel" "postgresql-libs" "gcc" "gcc-c++" "make")
            ;;
        pacman)
            pkgs+=("python" "python-pip" "git" "base-devel" "postgresql-libs" "libffi")
            ;;
        zypper)
            pkgs+=("python3-devel" "postgresql-devel" "gcc" "gcc-c++" "make")
            ;;
    esac

    local missing=()
    for pkg in "${pkgs[@]}"; do
        if ! command -v "$pkg" &>/dev/null; then
            missing+=("$pkg")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        info "Instalando pacotes ausentes: ${missing[*]}"
        for pkg in "${missing[@]}"; do
            sudo $PKG_INSTALL "$pkg" 2>/dev/null || warn "Falha ao instalar $pkg (tente manualmente)"
        done
    else
        success "Todos os pacotes do sistema estão presentes."
    fi
}

# =============================================================================
# Verifica versão do Python
# =============================================================================
check_python_version() {
    header "Verificando Python"

    local python_cmd="python3"
    if ! command -v python3 &>/dev/null; then
        if command -v python &>/dev/null; then
            python_cmd="python"
        else
            error "Python não encontrado. Instale Python $PYTHON_MIN ou superior."
            exit 1
        fi
    fi

    local py_version
    py_version=$("$python_cmd" --version 2>&1 | awk '{print $2}')
    local py_major py_minor
    py_major=$(echo "$py_version" | cut -d. -f1)
    py_minor=$(echo "$py_version" | cut -d. -f2)

    info "Python detectado: $py_version"

    if [ "$py_major" -lt 3 ] || ([ "$py_major" -eq 3 ] && [ "$py_minor" -lt 11 ]); then
        error "Python $PYTHON_MIN ou superior é necessário (encontrado: $py_version)."
        error "Atualize o Python ou use: pyenv install $PYTHONMIN"
        exit 1
    fi

    PYTHON="$python_cmd"
    success "Python $py_version atendido (mínimo: $PYTHON_MIN)."
}

# =============================================================================
# Configura ambiente virtual
# =============================================================================
setup_venv() {
    header "Configurando Ambiente Virtual"

    if [ -d "$VENV_DIR" ]; then
        warn "Ambiente virtual já existe em $VENV_DIR."
        printf "${YELLOW}Deseja recriá-lo? [s/N]: ${NC}"
        read -r reply
        if echo "$reply" | grep -iq '^s'; then
            rm -rf "$VENV_DIR"
        else
            success "Usando ambiente virtual existente."
            return 0
        fi
    fi

    info "Criando ambiente virtual com $PYTHON..."
    $PYTHON -m venv "$VENV_DIR"
    success "Ambiente virtual criado em $VENV_DIR."
}

# =============================================================================
# Ativa ambiente virtual e instala dependências
# =============================================================================
activate_venv() {
    header "Ativando Ambiente Virtual"
    source "$VENV_DIR/bin/activate"
    success "Ambiente virtual ativado."
    info "Python: $(python --version 2>&1)"
    info "pip: $(pip --version 2>&1)"
}

install_python_deps() {
    header "Instalando Dependências Python"

    if [ ! -f "pyproject.toml" ]; then
        error "pyproject.toml não encontrado. Verifique se está na raiz do projeto."
        exit 1
    fi

    info "Instalando pacotes do pyproject.toml..."
    pip install --upgrade pip setuptools wheel

    info "Instalando dependências do projeto (modo editável)..."
    pip install -e "."

    success "Dependências do projeto instaladas."

    # Verifica imports críticos
    info "Verificando imports críticos..."
    local critical_imports=("fastapi" "uvicorn" "celery" "redis" "sqlalchemy" "openai" "httpx" "pdfplumber" "jinja2" "pydantic_settings")
    local failed=()

    for mod in "${critical_imports[@]}"; do
        if ! $PYTHON -c "import $mod" 2>/dev/null; then
            failed+=("$mod")
        fi
    done

    if [ ${#failed[@]} -gt 0 ]; then
        error "Falha ao importar: ${failed[*]}"
        error "Tente: pip install -e '.[dev]'"
        exit 1
    else
        success "Todos os imports críticos verificados."
    fi
}

# =============================================================================
# Configura variáveis de ambiente
# =============================================================================
setup_env() {
    header "Configurando Variáveis de Ambiente"

    if [ -f ".env" ]; then
        warn ".env já existe. Será renomeado para .env.backup."
        mv .env .env.backup
        success ".env.backup criado."
    fi

    if [ -f ".env.example" ]; then
        cp .env.example .env
        success ".env criado a partir de .env.example."
    else
        error ".env.example não encontrado."
        exit 1
    fi

    # Atualiza .env com valores personalizados se fornecidos
    info "Atualizando .env com configurações..."

    set_env_var "DB_USER" "$DB_USER"
    set_env_var "DB_PASSWORD" "$DB_PASSWORD"
    set_env_var "DB_NAME" "$DB_NAME"
    set_env_var "DB_HOST" "$DB_HOST"
    set_env_var "DB_PORT" "$DB_PORT"
    set_env_var "REDIS_URL" "redis://${REDIS_HOST}:${REDIS_PORT}/0"
    set_env_var "CELERY_BROKER_URL" "redis://${REDIS_HOST}:${REDIS_PORT}/0"
    set_env_var "CELERY_RESULT_BACKEND" "db+postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
    set_env_var "LLM_BASE_URL" "$LLM_BASE_URL"
    set_env_var "TTS_BASE_URL" "$TTS_BASE_URL"
    set_env_var "APP_PORT" "$APP_PORT"
    set_env_var "APP_HOST" "$APP_HOST"

    success ".env configurado."
    info "Variáveis principais:"
    grep -E '^(DB_|REDIS|LLM_|TTS_|APP_)' .env | sed 's/^/  /'
}

# =============================================================================
# Cria diretórios necessários
# =============================================================================
create_directories() {
    header "Criando Diretórios"

    local dirs=("dataset" "storage" "uploads" "output" ".pytest_cache")

    for dir in "${dirs[@]}"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
            success "Diretório criado: $dir/"
        else
            info "Diretório já existe: $dir/"
        fi
    done
}

# =============================================================================
# Configura e roda migrations do banco
# =============================================================================
setup_database() {
    header "Configuração do Banco de Dados"

    # Verifica se o PostgreSQL está acessível
    info "Verificando conexão com PostgreSQL em ${DB_HOST}:${DB_PORT}..."

    if command -v pg_isready &>/dev/null; then
        if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" &>/dev/null; then
            success "PostgreSQL acessível em ${DB_HOST}:${DB_PORT}/${DB_NAME}."
        else
            warn "PostgreSQL não respondendo em ${DB_HOST}:${DB_PORT}."
            printf "${YELLOW}Deseja tentar rodar migrations mesmo assim? [s/N]: ${NC}"
            read -r reply
            if ! echo "$reply" | grep -iq '^s'; then
                info "Pulando migrations. Execute manualmente depois:"
                info "  alembic upgrade head"
                return 0
            fi
        fi
    else
        warn "pg_isready não encontrado. Pulando verificação de conexão."
    fi

    # Roda migrations
    info "Executando migrations Alembic (alembic upgrade head)..."
    alembic upgrade head

    success "Migrations aplicadas com sucesso."
    info "Tabelas criadas: TB_LIVROCABECALHO, TB_LIVROPAGINA, TB_LIVROPERSONAGENS,"
    info "  TB_LIVROFALAS, TB_LIVROAPIS (+ extensões de pipeline)."
}

# =============================================================================
# Opção Docker Compose (alternativa)
# =============================================================================
setup_docker() {
    header "Serviços com Docker Compose (Opcional)"

    if ! command -v docker &>/dev/null; then
        warn "Docker não encontrado. Serviços rodarão em containers Docker."
        return 0
    fi

    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
        warn "Docker Compose não encontrado. Serviços de banco e Redis devem estar disponíveis externamente."
        return 0
    fi

    printf "${YELLOW}Deseja iniciar serviços com Docker Compose? (PostgreSQL + Redis + Redis Commander) [s/N]: ${NC}"
    read -r reply

    if echo "$reply" | grep -iq '^s'; then
        USE_DOCKER="yes"
        info "Iniciando Docker Compose..."
        if docker compose version &>/dev/null; then
            docker compose up -d
        else
            docker-compose up -d
        fi
        success "Serviços Docker iniciados."
        info "  PostgreSQL: localhost:5432"
        info "  Redis:      localhost:6379"
        info "  Redis Cmd:  http://localhost:8081"
        info "Reconfigurando .env para nomes de serviço Docker..."
        # Quando usa Docker, os serviços são acessados pelos nomes dos containers
        set_env_var "DB_HOST" "postgres"
        set_env_var "REDIS_HOST" "redis"
        set_env_var "REDIS_URL" "redis://redis:6379/0"
        set_env_var "CELERY_BROKER_URL" "redis://redis:6379/0"
        set_env_var "CELERY_RESULT_BACKEND" "db+postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}"
        info ".env reconfigurado para comunicação interna Docker."
    else
        info "Pulando Docker Compose. Configure serviços externos manualmente."
    fi
}

# =============================================================================
# Executa testes rápidos
# =============================================================================
run_smoke_tests() {
    header "Executando Testes de Fumaça"

    info "Rodando subset de testes críticos..."
    if python -m pytest tests/ -v --tb=short -x -q --co 2>/dev/null | head -20; then
        success "Testes coletados com sucesso."
    else
        warn "Não foi possível listar testes. Pulo etapa."
        return 0
    fi

    printf "${YELLOW}Deseja rodar TODOS os testes? (357 testes, ~30s) [s/N]: ${NC}"
    read -r reply

    if echo "$reply" | grep -iq '^s'; then
        python -m pytest tests/ -v --tb=short --cov=app --cov=tasks --cov-report=term-missing --cov-fail-under=70 -q
        success "Todos os testes passaram."
    else
        info "Pulando execução completa de testes."
    fi
}

# =============================================================================
# Gera script de inicialização
# =============================================================================
generate_startup_scripts() {
    header "Scripts de Inicialização"

    # Script para iniciar o servidor web (modo produção)
    cat > start.sh << 'STARTUP'
#!/usr/bin/env bash
set -euo pipefail

echo "============================================="
echo "  Leitor — Servidor Web (Produção)"
echo "============================================="
echo "Host: ${APP_HOST:-0.0.0.0}"
echo "Port: ${APP_PORT:-8000}"
echo ""
echo "Pressione Ctrl+C para parar."

source venv/bin/activate
uvicorn app.main:app \
    --host "${APP_HOST:-0.0.0.0}" \
    --port "${APP_PORT:-8000}" \
    --workers 1 \
    --access-log
STARTUP
    chmod +x start.sh
    success "start.sh criado (servidor web — produção)."

    # Script para iniciar o worker Celery (modo produção)
    cat > start_worker.sh << 'STARTUP'
#!/usr/bin/env bash
set -euo pipefail

echo "============================================="
echo "  Leitor — Celery Worker (Produção)"
echo "============================================="
echo "Concorrência: 4"
echo ""
echo "Pressione Ctrl+C para parar."

source venv/bin/activate
celery -A tasks.pipeline.celery_app \
    worker \
    --loglevel=info \
    --concurrency=4 \
    --prefetch-multiplier=1
STARTUP
    chmod +x start_worker.sh
    success "start_worker.sh criado (worker Celery — produção)."

    # Script para iniciar tudo (web + worker)
    cat > start_all.sh << 'STARTUP'
#!/usr/bin/env bash
set -euo pipefail

echo "============================================="
echo "  Leitor — Iniciando todos os serviços"
echo "============================================="
echo ""

# Worker Celery em background
echo "[1/2] Iniciando Celery Worker..."
source venv/bin/activate
celery -A tasks.pipeline.celery_app \
    worker \
    --loglevel=info \
    --concurrency=4 \
    --prefetch-multiplier=1 \
    > celery.log 2>&1 &
CELERY_PID=$!
echo "  Worker PID: $CELERY_PID"

# Small delay to ensure worker starts cleanly
sleep 2

# Server principal
echo ""
echo "[2/2] Iniciando servidor web..."
echo "  Acesse: http://${APP_HOST:-0.0.0.0}:${APP_PORT:-8000}"
echo ""

# Cleanup handler
cleanup() {
    echo ""
    echo "Parando Celery Worker (PID $CELERY_PID)..."
    kill $CELERY_PID 2>/dev/null || true
    wait $CELERY_PID 2>/dev/null || true
    echo "Pronto."
    exit 0
}
trap cleanup SIGINT SIGTERM

uvicorn app.main:app \
    --host "${APP_HOST:-0.0.0.0}" \
    --port "${APP_PORT:-8000}" \
    --workers 1 \
    --access-log
STARTUP
    chmod +x start_all.sh
    success "start_all.sh criado (web + worker — produção)."
}

# =============================================================================
# Exibe resumo final
# =============================================================================
print_summary() {
    header "Instalação Concluída"

    cat << SUMMARY
${GREEN}✓${NC} Ambiente virtual:    $VENV_DIR/
${GREEN}✓${NC} Dependências:        Instaladas
${GREEN}✓${NC} Configuração:        .env (ajustado)
${GREEN}✓${NC} Banco:               Migrations aplicadas
${GREEN}✓${NC} Diretórios:          Criados
${GREEN}✓${NC} Scripts:             start.sh, start_worker.sh, start_all.sh

${CYAN}Próximos passos:${NC}
  1. Verifique o .env e ajuste se necessário
  2. Inicie os serviços:
     - Apenas web:      ./start.sh
     - Apenas worker:   ./start_worker.sh
     - Tudo:            ./start_all.sh
  3. Acesse: http://$(hostname -f 2>/dev/null || echo localhost):$APP_PORT
  4. Health check: curl http://localhost:$APP_PORT/health
  5. Redis Commander: http://localhost:8081 (se Docker usado)

${YELLOW}Nota:${NC} LLM ($LLM_BASE_URL) e TTS ($TTS_BASE_URL) devem estar
      disponíveis na rede antes da produção de audiolivros.

SUMMARY
}

# =============================================================================
# Main
# =============================================================================
main() {
    header "Leitor — Instalador de Instalação Rápida v0.1.0"

    check_root
    check_repo
    install_system_deps
    check_python_version
    setup_venv
    activate_venv
    install_python_deps
    setup_docker
    setup_env
    create_directories
    setup_database
    run_smoke_tests
    generate_startup_scripts
    print_summary

    success "Projeto Leitor instalado com sucesso!"
}

# Executa main
main "$@"
