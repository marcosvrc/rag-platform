"""Dependências (FastAPI `Depends`) compartilhadas pelos routers da API."""

from packages.config.settings import Settings, get_settings


def get_settings_dependency() -> Settings:
    """Wrapper fino sobre `get_settings()`.

    Existe como função separada (em vez de usar `get_settings` diretamente
    em `Depends(...)`) para que os testes possam sobrescrevê-la via
    `app.dependency_overrides` sem mexer no cache de `get_settings()`.
    """
    return get_settings()
