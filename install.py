#!/usr/bin/env python3
"""Instalador cross-platform do projeto Leitor (Linux e Windows)."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "scripts"
VENV_DIR = ROOT / "venv"
PYTHON_MIN = (3, 11)
LLM_PORT = 8000
TTS_PORT = 8881
DEFAULT_APP_PORT = 8000

sys.path.insert(0, str(SCRIPTS_DIR))
from port_utils import find_available_port  # noqa: E402

IS_WINDOWS = platform.system() == "Windows"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
VENV_PIP = VENV_DIR / ("Scripts/pip.exe" if IS_WINDOWS else "bin/pip")
VENV_ACTIVATE = VENV_DIR / ("Scripts/activate.bat" if IS_WINDOWS else "bin/activate")


class Installer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.non_interactive = args.yes
        self.use_docker = False
        self.python_cmd = self._resolve_system_python()
        self.config = {
            "db_user": os.environ.get("DB_USER", "leitor"),
            "db_password": os.environ.get("DB_PASSWORD", "leitor"),
            "db_name": os.environ.get("DB_NAME", "leitor"),
            "db_host": os.environ.get("DB_HOST", "localhost"),
            "db_port": int(os.environ.get("DB_PORT", "5432")),
            "redis_host": os.environ.get("REDIS_HOST", "localhost"),
            "redis_port": int(os.environ.get("REDIS_PORT", "6379")),
            "llm_base_url": os.environ.get("LLM_BASE_URL", ""),
            "tts_base_url": os.environ.get("TTS_BASE_URL", ""),
            "app_host": os.environ.get("APP_HOST", "0.0.0.0"),
            "app_port": int(os.environ.get("APP_PORT", str(DEFAULT_APP_PORT))),
        }

    # ------------------------------------------------------------------ logging
    def header(self, title: str) -> None:
        line = "=" * 60
        print(f"\n{line}\n{title}\n{line}")

    def info(self, msg: str) -> None:
        print(f"[INFO] {msg}")

    def ok(self, msg: str) -> None:
        print(f"[OK] {msg}")

    def warn(self, msg: str) -> None:
        print(f"[WARN] {msg}")

    def error(self, msg: str) -> None:
        print(f"[ERR] {msg}", file=sys.stderr)

    def ask_yes_no(self, prompt: str, default: bool = False) -> bool:
        if self.non_interactive:
            return default
        suffix = " [S/n]" if default else " [s/N]"
        reply = input(f"{prompt}{suffix}: ").strip().lower()
        if not reply:
            return default
        return reply in {"s", "sim", "y", "yes"}

    def ask_text(self, prompt: str, default: str = "") -> str:
        if self.non_interactive:
            return default
        suffix = f" [{default}]" if default else ""
        reply = input(f"{prompt}{suffix}: ").strip()
        return reply or default

    # ------------------------------------------------------------------ helpers
    def _resolve_system_python(self) -> str:
        for candidate in ("python3", "python", "py"):
            path = shutil.which(candidate)
            if path:
                return path
        return sys.executable

    def _run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        result = subprocess.run(
            cmd,
            cwd=cwd or ROOT,
            env=merged_env,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.error(result.stderr.strip() or result.stdout.strip())
            raise RuntimeError(f"Comando falhou: {' '.join(cmd)}")
        return result

    def _venv_cmd(self, *parts: str) -> list[str]:
        return [str(VENV_PYTHON), *parts]

    def _http_probe(self, url: str, timeout: float = 2.0) -> bool:
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status < 500
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _http_json_probe(self, url: str, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                body = response.read(4096).decode("utf-8", errors="ignore").lower()
                return response.status < 500 and (
                    "models" in body or "openai" in body or "data" in body
                )
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _is_leitor_health(self, host: str, port: int) -> bool:
        url = f"http://{host}:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                body = response.read(4096).decode("utf-8", errors="ignore")
                return '"service"' in body and "leitor" in body.lower()
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _is_llm_on_host(self, host: str, port: int = LLM_PORT) -> bool:
        if self._is_leitor_health(host, port):
            return False
        candidates = (
            f"http://{host}:{port}/v1/models",
            f"http://{host}:{port}/models",
            f"http://{host}:{port}/v1/chat/completions",
        )
        return any(self._http_json_probe(url) or self._http_probe(url) for url in candidates)

    def _is_tts_on_host(self, host: str, port: int = TTS_PORT) -> bool:
        candidates = (
            f"http://{host}:{port}/docs",
            f"http://{host}:{port}/openapi.json",
            f"http://{host}:{port}/",
        )
        return any(self._http_probe(url) for url in candidates)

    def _normalize_llm_url(self, host: str, port: int = LLM_PORT) -> str:
        return f"http://{host}:{port}/v1/"

    def _normalize_tts_url(self, host: str, port: int = TTS_PORT) -> str:
        return f"http://{host}:{port}"

    def _valid_ip_or_host(self, value: str) -> bool:
        if not value:
            return False
        if re.fullmatch(r"[\w.\-]+", value):
            return True
        return False

    # ------------------------------------------------------------------ steps
    def check_environment(self) -> None:
        self.header("Verificando Ambiente")
        os.chdir(ROOT)

        if not (ROOT / "pyproject.toml").exists():
            raise RuntimeError("Execute o instalador na raiz do projeto Leitor.")

        probe = self._run(
            [
                self.python_cmd,
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ]
        )
        major, minor = map(int, probe.stdout.strip().split("."))
        if (major, minor) < PYTHON_MIN:
            raise RuntimeError(
                f"Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ necessário (encontrado {major}.{minor})."
            )

        version_line = self._run([self.python_cmd, "--version"]).stdout.strip()
        self.ok(f"Plataforma: {platform.system()} {platform.release()}")
        self.ok(f"Python: {version_line}")
        self.ok(f"Diretório: {ROOT}")

    def install_system_deps_linux(self) -> None:
        if IS_WINDOWS:
            self.info("Windows detectado — instale manualmente Python 3.11+, Git e Docker Desktop (opcional).")
            return

        self.header("Dependências do Sistema (Linux)")
        if shutil.which("apt-get"):
            self.info("Debian/Ubuntu: instale python3-venv, libpq-dev e build-essential se necessário.")
        elif shutil.which("dnf") or shutil.which("yum"):
            self.info("RHEL/Fedora: instale python3-devel, postgresql-libs e gcc se necessário.")
        else:
            self.warn("Gerenciador de pacotes não detectado — verifique dependências manualmente.")

    def setup_venv(self) -> None:
        self.header("Ambiente Virtual")
        if VENV_DIR.exists() and not self.args.recreate_venv:
            if self.ask_yes_no("Ambiente virtual já existe. Recriar?", default=False):
                shutil.rmtree(VENV_DIR)
            else:
                self.ok(f"Usando {VENV_DIR}")
                return

        if VENV_DIR.exists():
            shutil.rmtree(VENV_DIR)

        self.info(f"Criando venv com {self.python_cmd}...")
        self._run([self.python_cmd, "-m", "venv", str(VENV_DIR)])
        self.ok(f"Ambiente virtual criado em {VENV_DIR}")

    def install_python_deps(self) -> None:
        self.header("Dependências Python")
        self._run(self._venv_cmd("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"))
        self._run(self._venv_cmd("-m", "pip", "install", "-e", "."))

        critical = (
            "fastapi",
            "uvicorn",
            "celery",
            "redis",
            "sqlalchemy",
            "psycopg2",
            "openai",
            "httpx",
            "pdfplumber",
            "jinja2",
            "pydantic_settings",
        )
        failed = []
        for module in critical:
            result = self._run(self._venv_cmd("-c", f"import {module}"), check=False)
            if result.returncode != 0:
                failed.append(module)

        if failed:
            raise RuntimeError(f"Imports críticos falharam: {', '.join(failed)}")
        self.ok("Dependências Python instaladas.")

    def setup_docker(self) -> None:
        self.header("Docker Compose (Opcional)")
        if not shutil.which("docker"):
            self.warn("Docker não encontrado — configure PostgreSQL e Redis manualmente.")
            return

        default_docker = self.non_interactive and os.environ.get("USE_DOCKER", "").lower() in {
            "1",
            "true",
            "yes",
        }
        if not self.ask_yes_no(
            "Iniciar PostgreSQL + Redis via Docker Compose?",
            default=default_docker,
        ):
            self.info("Docker Compose ignorado.")
            return

        compose = ["docker", "compose"]
        probe = self._run(compose + ["version"], check=False)
        if probe.returncode != 0 and shutil.which("docker-compose"):
            compose = ["docker-compose"]

        self.info("Subindo containers...")
        self._run(compose + ["up", "-d"])
        self.use_docker = True
        self.config["db_host"] = "localhost"
        self.config["redis_host"] = "localhost"
        self.ok("Docker Compose iniciado (PostgreSQL:5432, Redis:6379).")
        self._wait_for_tcp("localhost", self.config["db_port"], timeout=60)
        self._wait_for_tcp("localhost", self.config["redis_port"], timeout=30)

    def _wait_for_tcp(self, host: str, port: int, timeout: int) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=2):
                    return
            except OSError:
                time.sleep(2)
        self.warn(f"Timeout aguardando {host}:{port}")

    def detect_api_endpoints(self) -> None:
        self.header("Detecção de APIs (LLM e TTS)")

        localhost_hosts = ("127.0.0.1", "localhost")

        if self.config["llm_base_url"]:
            self.ok(f"LLM_BASE_URL definido: {self.config['llm_base_url']}")
        else:
            llm_host = ""
            for host in localhost_hosts:
                if self._is_llm_on_host(host, LLM_PORT):
                    llm_host = host
                    self.ok(f"LLM detectado em {self._normalize_llm_url(host, LLM_PORT)}")
                    break

            if not llm_host:
                self.warn(f"LLM não encontrado em localhost:{LLM_PORT}.")
                default_ip = os.environ.get("LLM_HOST", "192.168.2.112")
                ip = self.ask_text("Informe o IP do servidor LLM", default=default_ip)
                if not self._valid_ip_or_host(ip):
                    raise RuntimeError("IP do LLM inválido.")
                llm_host = ip
                if not self._is_llm_on_host(llm_host, LLM_PORT):
                    self.warn(
                        f"LLM não respondeu em {ip}:{LLM_PORT} — URL será configurada mesmo assim."
                    )

            self.config["llm_base_url"] = self._normalize_llm_url(llm_host, LLM_PORT)

        if self.config["tts_base_url"]:
            self.ok(f"TTS_BASE_URL definido: {self.config['tts_base_url']}")
        else:
            tts_host = ""
            for host in localhost_hosts:
                if self._is_tts_on_host(host, TTS_PORT):
                    tts_host = host
                    self.ok(f"TTS detectado em {self._normalize_tts_url(host, TTS_PORT)}")
                    break

            if not tts_host:
                self.warn(f"TTS não encontrado em localhost:{TTS_PORT}.")
                default_ip = os.environ.get("TTS_HOST", "192.168.2.112")
                ip = self.ask_text("Informe o IP do servidor TTS", default=default_ip)
                if not self._valid_ip_or_host(ip):
                    raise RuntimeError("IP do TTS inválido.")
                tts_host = ip
                if not self._is_tts_on_host(tts_host, TTS_PORT):
                    self.warn(
                        f"TTS não respondeu em {ip}:{TTS_PORT} — URL será configurada mesmo assim."
                    )

            self.config["tts_base_url"] = self._normalize_tts_url(tts_host, TTS_PORT)

    def resolve_app_port(self) -> None:
        self.header("Porta do Leitor")
        preferred = self.config["app_port"]
        host = self.config["app_host"]

        resolved = find_available_port(preferred, host)
        if resolved != preferred:
            self.warn(f"Porta {preferred} em uso — Leitor usará {resolved}.")
        else:
            self.ok(f"Porta {resolved} disponível.")

        self.config["app_port"] = resolved

    def setup_env_file(self) -> None:
        self.header("Arquivo .env")
        example = ROOT / ".env.example"
        env_path = ROOT / ".env"
        if not example.exists():
            raise RuntimeError(".env.example não encontrado.")

        if env_path.exists():
            backup = ROOT / ".env.backup"
            shutil.copy2(env_path, backup)
            self.warn(f".env existente salvo em {backup}")

        shutil.copy2(example, env_path)
        values = {
            "DATABASE_URL": (
                f"postgresql+psycopg2://{self.config['db_user']}:{self.config['db_password']}"
                f"@{self.config['db_host']}:{self.config['db_port']}/{self.config['db_name']}"
            ),
            "DB_USER": self.config["db_user"],
            "DB_PASSWORD": self.config["db_password"],
            "DB_NAME": self.config["db_name"],
            "DB_HOST": self.config["db_host"],
            "DB_PORT": str(self.config["db_port"]),
            "REDIS_URL": f"redis://{self.config['redis_host']}:{self.config['redis_port']}/0",
            "CELERY_BROKER_URL": f"redis://{self.config['redis_host']}:{self.config['redis_port']}/0",
            "CELERY_RESULT_BACKEND": (
                f"db+postgresql://{self.config['db_user']}:{self.config['db_password']}"
                f"@{self.config['db_host']}:{self.config['db_port']}/{self.config['db_name']}"
            ),
            "LLM_BASE_URL": self.config["llm_base_url"],
            "TTS_BASE_URL": self.config["tts_base_url"],
            "APP_HOST": self.config["app_host"],
            "APP_PORT": str(self.config["app_port"]),
        }
        self._write_env(env_path, values)
        self.ok(".env configurado.")
        for key in ("DB_HOST", "DB_NAME", "APP_PORT", "LLM_BASE_URL", "TTS_BASE_URL"):
            self.info(f"  {key}={values[key]}")

    def _write_env(self, path: Path, values: dict[str, str]) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        updated: list[str] = []
        seen: set[str] = set()
        for line in lines:
            if not line or line.strip().startswith("#") or "=" not in line:
                updated.append(line)
                continue
            key, _ = line.split("=", 1)
            if key in values:
                updated.append(f"{key}={values[key]}")
                seen.add(key)
            else:
                updated.append(line)
        for key, value in values.items():
            if key not in seen:
                updated.append(f"{key}={value}")
        path.write_text("\n".join(updated) + "\n", encoding="utf-8")

    def create_directories(self) -> None:
        self.header("Diretórios")
        for name in ("dataset", "storage", "uploads", "output"):
            path = ROOT / name
            path.mkdir(parents=True, exist_ok=True)
            self.ok(f"{name}/")

    def ensure_database(self) -> None:
        self.header("Banco de Dados PostgreSQL")
        script = r"""
import os
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

host = os.environ["LEITOR_DB_HOST"]
port = int(os.environ["LEITOR_DB_PORT"])
user = os.environ["LEITOR_DB_USER"]
password = os.environ["LEITOR_DB_PASSWORD"]
dbname = os.environ["LEITOR_DB_NAME"]

def connect(db, login_user=user, login_password=password):
    return psycopg2.connect(
        host=host, port=port, user=login_user, password=login_password, dbname=db
    )

try:
    connect(dbname).close()
    print("EXISTS")
    raise SystemExit(0)
except psycopg2.OperationalError:
    pass

conn = None
for login_user, login_password in ((user, password), ("postgres", password), ("postgres", "postgres")):
    try:
        conn = connect("postgres", login_user, login_password)
        break
    except psycopg2.OperationalError:
        continue

if conn is None:
    print("NO_ADMIN", file=__import__("sys").stderr)
    raise SystemExit(2)

conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
if not cur.fetchone():
    cur.execute(
        sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(user)),
        (password,),
    )
    print("USER_CREATED")

cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
if not cur.fetchone():
    cur.execute(
        sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(dbname), sql.Identifier(user)
        )
    )
    print("DB_CREATED")
else:
    print("DB_EXISTS")

cur.close()
conn.close()
"""
        db_env = {
            "LEITOR_DB_HOST": str(self.config["db_host"]),
            "LEITOR_DB_PORT": str(self.config["db_port"]),
            "LEITOR_DB_USER": self.config["db_user"],
            "LEITOR_DB_PASSWORD": self.config["db_password"],
            "LEITOR_DB_NAME": self.config["db_name"],
        }
        result = self._run(
            self._venv_cmd("-c", script),
            check=False,
            env=db_env,
        )
        output = (result.stdout or "").strip()
        if result.returncode == 0:
            if "EXISTS" in output:
                self.ok(f"Banco '{self.config['db_name']}' já existe.")
            elif "DB_CREATED" in output:
                self.ok(f"Banco '{self.config['db_name']}' criado.")
            elif "DB_EXISTS" in output:
                self.ok(f"Banco '{self.config['db_name']}' já existia.")
            return

        self.warn("Não foi possível criar/verificar o banco automaticamente.")
        self.warn(result.stderr.strip() or "Verifique credenciais e se o PostgreSQL está ativo.")
        if not self.ask_yes_no("Continuar sem garantir criação do banco?", default=False):
            raise RuntimeError("Instalação interrompida na etapa do banco.")

    def run_migrations(self) -> None:
        self.header("Migrations Alembic")
        if not self.ask_yes_no("Executar alembic upgrade head?", default=True):
            self.warn("Migrations ignoradas — execute manualmente depois.")
            return
        self._run(self._venv_cmd("-m", "alembic", "upgrade", "head"))
        self.ok("Migrations aplicadas.")

    def run_tests(self) -> None:
        if self.args.skip_tests:
            return
        self.header("Testes (Opcional)")
        if not self.ask_yes_no("Executar suite de testes?", default=False):
            return
        self._run(
            self._venv_cmd(
                "-m",
                "pytest",
                "tests/",
                "-q",
                "--tb=short",
                "--cov=app",
                "--cov=tasks",
                "--cov-fail-under=70",
            )
        )
        self.ok("Testes passaram.")

    def generate_startup_scripts(self) -> None:
        self.header("Scripts de Inicialização")

        if IS_WINDOWS:
            self._write_file(
                "start.ps1",
                """$ErrorActionPreference = "Stop"
Write-Host "Leitor - Servidor Web (Producao)"
& ".\\venv\\Scripts\\python.exe" scripts/start_web.py
""",
            )
            self._write_file(
                "start_worker.ps1",
                """$ErrorActionPreference = "Stop"
Write-Host "Leitor - Celery Worker (Producao)"
& ".\\venv\\Scripts\\python.exe" -m celery -A tasks.pipeline.celery_app worker --loglevel=info --concurrency=4 --prefetch-multiplier=1
""",
            )
            self._write_file(
                "start_all.ps1",
                """$ErrorActionPreference = "Stop"
Write-Host "Leitor - Web + Worker"
$worker = Start-Process -FilePath ".\\venv\\Scripts\\python.exe" -ArgumentList "-m celery -A tasks.pipeline.celery_app worker --loglevel=info --concurrency=4 --prefetch-multiplier=1" -PassThru -NoNewWindow
try {
  & ".\\venv\\Scripts\\python.exe" scripts/start_web.py
} finally {
  if ($worker -and -not $worker.HasExited) { Stop-Process -Id $worker.Id -Force }
}
""",
            )
            self.ok("start.ps1, start_worker.ps1, start_all.ps1")
        else:
            self._write_file(
                "start.sh",
                """#!/usr/bin/env bash
set -euo pipefail
source venv/bin/activate
python scripts/start_web.py
""",
                executable=True,
            )
            self._write_file(
                "start_worker.sh",
                """#!/usr/bin/env bash
set -euo pipefail
source venv/bin/activate
celery -A tasks.pipeline.celery_app worker --loglevel=info --concurrency=4 --prefetch-multiplier=1
""",
                executable=True,
            )
            self._write_file(
                "start_all.sh",
                """#!/usr/bin/env bash
set -euo pipefail
source venv/bin/activate
celery -A tasks.pipeline.celery_app worker --loglevel=info --concurrency=4 --prefetch-multiplier=1 > celery.log 2>&1 &
CELERY_PID=$!
trap 'kill $CELERY_PID 2>/dev/null || true' EXIT INT TERM
sleep 2
python scripts/start_web.py
""",
                executable=True,
            )
            self.ok("start.sh, start_worker.sh, start_all.sh")

    def _write_file(self, name: str, content: str, *, executable: bool = False) -> None:
        path = ROOT / name
        path.write_text(content, encoding="utf-8", newline="\n")
        if executable and not IS_WINDOWS:
            path.chmod(path.stat().st_mode | 0o111)

    def print_summary(self) -> None:
        self.header("Instalação Concluída")
        if IS_WINDOWS:
            start_cmd = ".\\start_all.ps1"
        else:
            start_cmd = "./start_all.sh"
        print(
            f"""
Próximos passos:
  1. Revise o arquivo .env
  2. Inicie a aplicação: {start_cmd}
  3. Health check: http://localhost:{self.config['app_port']}/health

APIs configuradas:
  LLM: {self.config['llm_base_url']}
  TTS: {self.config['tts_base_url']}
"""
        )

    def run(self) -> None:
        self.check_environment()
        self.install_system_deps_linux()
        self.setup_venv()
        self.install_python_deps()
        self.setup_docker()
        self.detect_api_endpoints()
        self.resolve_app_port()
        self.setup_env_file()
        self.create_directories()
        self.ensure_database()
        self.run_migrations()
        self.run_tests()
        self.generate_startup_scripts()
        self.print_summary()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Instalador cross-platform do Leitor")
    parser.add_argument("-y", "--yes", action="store_true", help="Modo não interativo")
    parser.add_argument(
        "--recreate-venv",
        action="store_true",
        help="Recria o ambiente virtual sem perguntar",
    )
    parser.add_argument("--skip-tests", action="store_true", help="Não executa testes")
    return parser.parse_args()


def main() -> int:
    try:
        installer = Installer(parse_args())
        installer.run()
    except KeyboardInterrupt:
        print("\nInstalação cancelada.")
        return 130
    except RuntimeError as exc:
        print(f"\n[ERR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
