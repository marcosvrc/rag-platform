"""Base declarativa do SQLAlchemy (RAG-006).

`Base.metadata` é usado pelo Alembic (ver migrations/env.py) para
autogeração de migrations futuras. O schema em si — as tabelas do modelo
mínimo descrito na seção 9 do plano — é criado em RAG-011; aqui só existe
a infraestrutura compartilhada.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Classe base declarativa compartilhada por todos os modelos ORM."""
