"""Configuração da aplicação validada via Pydantic Settings (RAG-004).

Nenhum outro módulo deve ler `os.environ` diretamente para configuração
da aplicação — use `get_settings()`. Campos sem default (as senhas) são
obrigatórios: se ausentes, `get_settings()` levanta `ConfigurationError`
com uma mensagem que nomeia apenas os campos faltantes, nunca valores —
segredos usam `SecretStr`, cujo repr/str é sempre mascarado
(`SecretStr('**********')`), então mesmo um `print(settings)` ou um log
acidental não expõe credenciais.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Ambiente de execução da aplicação."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ConfigurationError(RuntimeError):
    """Configuração inválida ou incompleta.

    A mensagem nunca inclui o valor de uma variável — apenas o nome do(s)
    campo(s) problemático(s) — para que possa ser logada com segurança.
    """


class Settings(BaseSettings):
    """Configuração validada da aplicação, lida de variáveis de ambiente
    (e opcionalmente de um arquivo `.env`, ver `.env.example`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    # Identificação do ambiente de execução.
    environment: Environment = Field(default=Environment.LOCAL, alias="ENVIRONMENT")

    # PostgreSQL + pgvector (ver RAG-003/docker-compose.yml).
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="rag_platform", alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="rag_platform", alias="POSTGRES_DB")

    # Redis (fila/Celery).
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")

    # MinIO / object storage compatível com S3.
    minio_host: str = Field(default="localhost", alias="MINIO_HOST")
    minio_port: int = Field(default=9000, alias="MINIO_API_PORT")
    minio_root_user: str = Field(default="rag_platform", alias="MINIO_ROOT_USER")
    minio_root_password: SecretStr = Field(alias="MINIO_ROOT_PASSWORD")
    minio_use_ssl: bool = Field(default=False, alias="MINIO_USE_SSL")
    minio_bucket: str = Field(default="rag-platform", alias="MINIO_BUCKET")

    # Upload de documentos (RAG-021). Tamanho máximo configurável (seção
    # 13 do plano: "arquivos devem ser validados e ter tamanho máximo
    # configurável"); os tipos aceitos (PDF, Markdown, TXT, DOCX — seção
    # 2 e RAG-023) não são configuráveis por ambiente, são uma decisão
    # de produto, então ficam fixos em
    # `packages/application/commands/document.py`, não aqui.
    document_max_size_bytes: int = Field(default=52_428_800, alias="DOCUMENT_MAX_SIZE_BYTES")
    # Autenticação JWT (RAG-050). `jwt_issuer`/`jwt_audience` são
    # obrigatórios (sem default) mesmo em modo local: forçam configuração
    # explícita via `.env`, em vez de um valor "que sempre funciona" que
    # poderia vazar para produção sem ninguém notar. Em modo local, isso
    # é um provedor de identidade *simulado* — só um segredo compartilhado
    # (`JWT_SECRET`, algoritmo HS*) configurado localmente, sem OIDC real
    # nem rotação de chave (seção 13 do plano: "em modo local, provedor
    # de identidade simulado e explicitamente identificado como não
    # produtivo"). `jwt_public_key` só é usado com um algoritmo
    # assimétrico (RS*/ES*/PS*); os dois nunca são obrigatórios ao mesmo
    # tempo, e qual deles vale depende de `jwt_algorithm` (validado em
    # `adapters/token_verifier/pyjwt_verifier.py`, não aqui).
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_secret: SecretStr | None = Field(default=None, alias="JWT_SECRET")
    jwt_public_key: str | None = Field(default=None, alias="JWT_PUBLIC_KEY")
    jwt_issuer: str = Field(alias="JWT_ISSUER")
    jwt_audience: str = Field(alias="JWT_AUDIENCE")
    jwt_leeway_seconds: int = Field(default=10, alias="JWT_LEEWAY_SECONDS")

    @property
    def database_url(self) -> str:
        """DSN assíncrono do SQLAlchemy (driver `asyncpg`, ver RAG-006)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """URL de conexão do Redis (broker/backend do Celery, ver RAG-022)."""
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def minio_endpoint(self) -> str:
        """Endpoint `host:port` do MinIO (ver RAG-020)."""
        return f"{self.minio_host}:{self.minio_port}"

    @property
    def minio_endpoint_url(self) -> str:
        """Endpoint completo (`http(s)://host:port`) para o cliente S3
        (RAG-020) — o SDK exige o esquema, `minio_endpoint` não tem."""
        scheme = "https" if self.minio_use_ssl else "http"
        return f"{scheme}://{self.minio_endpoint}"


def load_settings(*, env_file: str | None = ".env") -> Settings:
    """Carrega e valida a configuração, convertendo falhas em
    `ConfigurationError` com mensagem segura (nunca inclui valores).

    `env_file=None` desativa a leitura de um arquivo `.env` (usado nos
    testes, para isolar o comportamento das variáveis de ambiente reais
    da máquina que executa os testes).
    """

    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = sorted(
            {
                ".".join(str(part) for part in error["loc"])
                for error in exc.errors()
                if error["type"] == "missing"
            }
        )
        if missing:
            message = (
                "Configuração obrigatória ausente: "
                + ", ".join(missing)
                + ". Defina essas variáveis de ambiente ou copie .env.example para .env."
            )
        else:
            message = (
                f"Configuração inválida ({len(exc.errors())} erro(s) de validação). "
                "Verifique as variáveis de ambiente definidas; nenhum valor é "
                "exibido aqui por segurança."
            )
        raise ConfigurationError(message) from None


@lru_cache
def get_settings() -> Settings:
    """Retorna a configuração validada, cacheada para o processo."""
    return load_settings()
