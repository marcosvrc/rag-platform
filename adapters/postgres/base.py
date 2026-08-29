"""Base declarativa do SQLAlchemy (RAG-006; convenção de nomes em RAG-011).

`Base.metadata` é usado pelo Alembic (ver migrations/env.py) para
autogeração de migrations futuras. O schema em si — as tabelas do modelo
mínimo descrito na seção 9 do plano — é criado em RAG-011 (ver
`adapters/postgres/models/`).

A `naming_convention` abaixo é a recomendação padrão do SQLAlchemy: sem
ela, o nome de constraints (em especial `CHECK` e `UNIQUE`) fica a cargo
do driver/banco e pode variar, dificultando `alembic revision
--autogenerate` e migrations de downgrade previsíveis.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Classe base declarativa compartilhada por todos os modelos ORM."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
