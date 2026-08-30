"""Schema do dataset dourado de avaliação (RAG-060, seções 9/20 do
plano): casos de pergunta, resposta esperada e evidência esperada,
usados por RAG-061 (Recall@K, MRR) e RAG-062 (faithfulness, answer
relevancy) para medir a qualidade do RAG contra uma referência fixa e
versionada — sem essa referência, "melhorou" ou "piorou" não tem
como ser medido.

Mesma convenção de versionamento imutável de
`packages/generation/prompts.py` (RAG-040, seção 8 do plano:
"configurações de prompt, retrieval e modelo devem possuir versão"):
`datasets/golden/<id>.<version>.yaml`; uma versão publicada nunca é
editada — uma mudança de conteúdo sempre cria uma versão nova.
`EvaluationRun.dataset_version` (seção 9 do plano,
`packages/domain/entities/evaluation_run.py`) referencia essa mesma
versão, para toda execução de avaliação saber exatamente contra qual
dataset ela rodou.

Este módulo só define e carrega o schema — não executa nenhuma
avaliação (isso é RAG-061/062) e não decide qual corpus de documentos
vira uma base de conhecimento indexada de verdade para o retrieval
rodar contra ela (também RAG-061)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_DATASETS_DIR = Path(__file__).resolve().parent.parent.parent / "datasets" / "golden"

#: Critério de aceite da RAG-060 ("pelo menos 30 casos") e item da
#: Definition of Done da POC (seção 20 do plano) — verificado aqui
#: como invariante do schema, não só como uma contagem manual feita
#: uma vez ao escrever o dataset.
MINIMUM_CASE_COUNT = 30


class GoldenDatasetNotFoundError(LookupError):
    """Não existe `datasets/golden/<dataset_id>.<version>.yaml` para o
    par pedido."""

    def __init__(self, dataset_id: str, version: str) -> None:
        self.dataset_id = dataset_id
        self.version = version
        super().__init__(f"Dataset dourado '{dataset_id}' versão '{version}' não encontrado.")


class ExpectedEvidence(BaseModel):
    """Uma evidência esperada para um `GoldenCase`.

    Deliberadamente NÃO usa `chunk_id`: um `chunk_id` é um UUID gerado
    no momento da indexação (seção 9 do plano) — reindexar os mesmos
    documentos, ou rodar em outro ambiente, gera UUIDs diferentes. Um
    dataset versionado no repositório precisa de um identificador que
    sobreviva a isso, então a evidência esperada é descrita em termos
    do documento de origem e de um trecho de texto:

    - `document_id`: um identificador estável escolhido por quem cura
      o dataset (ex.: o nome/caminho lógico do documento de
      referência), nunca um ID de banco.
    - `content_contains`: um trecho que deve aparecer no conteúdo do
      chunk certo — é assim que RAG-061 casa um chunk recuperado de
      verdade (que só existe depois de indexado, com um `chunk_id`
      novo) com a expectativa deste caso, por conteúdo, não por ID.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(min_length=1)
    section: str | None = Field(default=None, min_length=1)
    content_contains: str = Field(min_length=1)


class GoldenCase(BaseModel):
    """Um caso do dataset dourado: uma pergunta e o que se espera dela.

    `expected_answer=None` marca uma "pergunta sem resposta" (critério
    de aceite da RAG-060, seção 12.1 do plano: "responder 'não há
    evidência suficiente' quando nenhum chunk ultrapassar o limiar") —
    o caso existe para verificar que o sistema recusa responder em vez
    de inventar uma resposta, não para medir a qualidade de uma
    resposta real. A validação abaixo torna a correspondência entre
    pergunta e evidência um invariante do schema, não uma convenção
    de quem escreve o caso: uma pergunta sem resposta não pode ter
    evidência esperada (evidência esperada implicaria que ELA TEM
    resposta), e uma pergunta com resposta esperada precisa de pelo
    menos uma evidência (uma afirmação sempre vem de algum lugar)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: str | None = Field(default=None, min_length=1)
    expected_evidence: tuple[ExpectedEvidence, ...] = Field(default_factory=tuple)
    notes: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check_answerability_matches_evidence(self) -> Self:
        if self.expected_answer is None and self.expected_evidence:
            raise ValueError(
                f"Caso '{self.id}': pergunta sem resposta (expected_answer=None) "
                "não pode ter expected_evidence."
            )
        if self.expected_answer is not None and not self.expected_evidence:
            raise ValueError(
                f"Caso '{self.id}': pergunta com expected_answer precisa de ao "
                "menos uma expected_evidence."
            )
        return self


class GoldenDataset(BaseModel):
    """O dataset dourado versionado inteiro — o que
    `load_golden_dataset`/`get_default_golden_dataset` devolvem."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cases: tuple[GoldenCase, ...]

    @model_validator(mode="after")
    def _check_dataset_invariants(self) -> Self:
        if len(self.cases) < MINIMUM_CASE_COUNT:
            raise ValueError(
                f"Dataset '{self.id}' versão '{self.version}' tem {len(self.cases)} "
                f"caso(s), mínimo exigido é {MINIMUM_CASE_COUNT} (seção 20 do plano)."
            )

        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            duplicated = {case_id for case_id in case_ids if case_ids.count(case_id) > 1}
            raise ValueError(
                f"Dataset '{self.id}' versão '{self.version}' tem ID(s) de caso "
                f"duplicado(s): {sorted(duplicated)}."
            )

        if not any(case.expected_answer is None for case in self.cases):
            raise ValueError(
                f"Dataset '{self.id}' versão '{self.version}' não tem nenhuma "
                "pergunta sem resposta (critério de aceite da RAG-060)."
            )

        return self


@lru_cache
def load_golden_dataset(dataset_id: str, version: str) -> GoldenDataset:
    """Carrega e valida `datasets/golden/<dataset_id>.<version>.yaml`.

    Cacheado por processo (mesmo par `(id, version)` sempre devolve a
    mesma instância) — mesmo raciocínio de
    `packages/generation/prompts.py::load_prompt`: o arquivo é lido do
    disco só uma vez, e como uma versão publicada é imutável por
    convenção, isso é seguro.
    """
    path = _DATASETS_DIR / f"{dataset_id}.{version}.yaml"
    if not path.is_file():
        raise GoldenDatasetNotFoundError(dataset_id, version)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    dataset = GoldenDataset.model_validate(raw)

    if dataset.id != dataset_id or dataset.version != version:
        raise ValueError(
            f"{path}: campos 'id'/'version' ({dataset.id!r}/{dataset.version!r}) "
            f"não correspondem ao nome do arquivo ({dataset_id!r}/{version!r})."
        )
    return dataset


def get_default_golden_dataset() -> GoldenDataset:
    """O dataset dourado atualmente usado pela avaliação (RAG-061/062).

    Único ponto que precisa mudar quando uma nova versão for adotada —
    RAG-061/062 devem chamar esta função, nunca
    `load_golden_dataset("golden", ...)` com uma versão hardcoded em
    outro lugar (mesmo padrão de
    `packages/generation/prompts.py::get_default_answer_prompt`).
    """
    return load_golden_dataset("golden", "v1")
