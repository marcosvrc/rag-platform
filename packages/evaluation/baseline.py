"""Baseline versionada da POC e verificação de regressão (RAG-063,
seção 21 do plano: "Requisitos de desempenho iniciais").

## Por que este módulo não importa nada de RAG-061/RAG-062

O plano lista "Dependências: RAG-061, RAG-062" para esta atividade,
mas essas duas atividades vivem em branches irmãs ainda não mescladas
(`feat/rag-061-retrieval-evaluation`, ramificada de `master`, e
`feat/rag-062-generation-evaluation`, empilhada sobre a pilha de
RAG-043/044) — nenhuma das duas contém a outra, então um módulo desta
atividade que importasse `packages.evaluation.retrieval_evaluation`
ou `packages.evaluation.generation_evaluation` diretamente exigiria
mesclar as três branches juntas antes de qualquer uma poder ser
revisada ou publicada isoladamente.

Em vez disso, este módulo opera sobre `dict[str, float]` genérico —
o mesmo formato que `RetrievalEvaluationReport`/`GenerationEvaluationReport`
já produzem via `report_to_dict()` (`recall_at_k`, `mrr`,
`faithfulness`, `answer_relevancy` como chaves de nível superior). Uma
métrica corrente vinda de qualquer um dos dois relatórios (ou de um
JSON já gravado em disco por `scripts/run_retrieval_evaluation.py`/
`scripts/run_generation_evaluation.py`, carregado com `json.load`, sem
nenhum import Python das branches de RAG-061/062) pode ser comparada
contra esta baseline sem acoplar o código das três atividades. Isso
mantém as três branches independentemente mesclável, ao mesmo tempo em
que a baseline representa de fato as métricas que RAG-061/062 medem.

## Limitação documentada (critério de aceite "limitações documentadas")

Os valores em `config/evaluation/poc.v1.yaml` são as METAS da
seção 21 do plano ("Recall@5 inicial igual ou superior a 0,80" / "MRR
inicial igual ou superior a 0,70" / "Faithfulness inicial igual ou
superior a 0,85"), não uma medição real: escrever esta atividade não
teve acesso a um gateway LiteLLM alcançável (mesma limitação já
documentada em `scripts/run_retrieval_evaluation.py`/
`scripts/run_generation_evaluation.py` — seção 15 do plano, "chamadas
reais ficam em workflow manual ou agendado"). O campo `measured: false`
deixa isso explícito no próprio arquivo, não só em um comentário. A
primeira execução real dos dois scripts contra um ambiente com gateway
ativo deve avaliar se as metas foram atingidas e, em caso positivo,
publicar `baseline.v2.yaml` com os valores MEDIDOS de fato e
`measured: true` — nunca editar `poc.v1.yaml` (mesma convenção de
versão imutável de `packages/generation/prompts.py`,
`packages/evaluation/golden_dataset.py`, `packages/config/models.py`).
Answer relevancy não tem meta própria na seção 21 do plano — reusa a
meta de faithfulness (0,85), mesma decisão já tomada em
`scripts/run_generation_evaluation.py`.

`check_regression` em si funciona hoje contra qualquer baseline,
medida ou não — só o CONTEÚDO de v1 é provisório."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_BASELINES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "evaluation"


class BaselineNotFoundError(LookupError):
    """Não existe `config/evaluation/<baseline_id>.<version>.yaml` para
    o par pedido."""

    def __init__(self, baseline_id: str, version: str) -> None:
        self.baseline_id = baseline_id
        self.version = version
        super().__init__(f"Baseline '{baseline_id}' versão '{version}' não encontrada.")


class Baseline(BaseModel):
    """Uma baseline versionada de métricas de avaliação (RAG-063).

    `max_regression_pct` é a fração máxima de queda tolerada em
    relação a cada valor de `metrics` (seção 21 do plano: "Regressão
    máxima permitida de 5% contra a baseline aprovada" → `0.05`).
    `measured=False` marca uma baseline com valores-alvo do plano,
    ainda não confirmados por uma execução real (ver docstring do
    módulo) — `check_regression` funciona igual nos dois casos; o
    campo é só para tornar essa limitação visível a quem consome o
    arquivo, nunca lido pela lógica de comparação em si.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    measured: bool
    max_regression_pct: float = Field(gt=0.0, le=1.0)
    metrics: dict[str, float]
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_metrics_are_not_empty(self) -> Self:
        if not self.metrics:
            raise ValueError(f"Baseline '{self.id}' versão '{self.version}' não tem métricas.")
        return self

    def minimum_acceptable(self, metric: str) -> float:
        """O menor valor de `metric` ainda aceitável antes de
        configurar regressão: `baseline * (1 - max_regression_pct)`.

        Levanta `KeyError` se `metric` não existir nesta baseline —
        mesmo comportamento de acessar `self.metrics[metric]`
        diretamente, então quem chama já precisa ter checado a
        presença da métrica (é o que `check_regression` faz).
        """
        return self.metrics[metric] * (1 - self.max_regression_pct)


@lru_cache
def load_baseline(baseline_id: str, version: str) -> Baseline:
    """Carrega e valida `config/evaluation/<baseline_id>.<version>.yaml`.

    Cacheado por processo — mesma convenção de
    `packages/evaluation/golden_dataset.py::load_golden_dataset`: uma
    versão publicada é imutável por convenção, então ler o arquivo uma
    única vez por processo é seguro.
    """
    path = _BASELINES_DIR / f"{baseline_id}.{version}.yaml"
    if not path.is_file():
        raise BaselineNotFoundError(baseline_id, version)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    baseline = Baseline.model_validate(raw)

    if baseline.id != baseline_id or baseline.version != version:
        raise ValueError(
            f"{path}: campos 'id'/'version' ({baseline.id!r}/{baseline.version!r}) "
            f"não correspondem ao nome do arquivo ({baseline_id!r}/{version!r})."
        )
    return baseline


def get_current_baseline() -> Baseline:
    """A baseline atualmente aprovada para verificação de regressão.

    Único ponto que precisa mudar quando uma nova versão for adotada
    (ex.: `baseline.v2.yaml` com valores medidos de verdade) — quem
    verifica regressão deve chamar esta função, nunca
    `load_baseline("poc", ...)` com uma versão hardcoded em outro
    lugar (mesmo padrão de
    `packages/generation/prompts.py::get_default_answer_prompt`).
    """
    return load_baseline("poc", "v1")


@dataclass(frozen=True, slots=True)
class RegressionCheck:
    """Resultado de `check_regression`: `passed=False` sempre vem
    acompanhado de pelo menos uma mensagem em `violations`."""

    passed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)


def check_regression(current_metrics: dict[str, float], *, baseline: Baseline) -> RegressionCheck:
    """Compara `current_metrics` (o `dict` de métricas de uma execução
    nova — por exemplo `report_to_dict(report)["recall_at_k"]` etc.,
    de RAG-061/062, ou o mesmo `dict` recarregado de um JSON já
    gravado em disco) contra `baseline`.

    Para cada métrica conhecida por `baseline.metrics`: se
    `current_metrics` também tiver essa chave, falha quando o valor
    atual caiu mais que `baseline.max_regression_pct` em relação ao
    valor da baseline. Uma métrica que `baseline.metrics` não conhece
    é ignorada (nunca um erro) e uma métrica de `baseline.metrics`
    ausente em `current_metrics` também é ignorada (permite comparar
    contra um relatório parcial, por exemplo só de retrieval ou só de
    geração, sem falhar por métricas que aquele relatório nunca
    teve)."""
    violations = []
    for metric, baseline_value in baseline.metrics.items():
        if metric not in current_metrics:
            continue
        current_value = current_metrics[metric]
        minimum = baseline.minimum_acceptable(metric)
        if current_value < minimum:
            drop_pct = (baseline_value - current_value) / baseline_value if baseline_value else 1.0
            violations.append(
                f"{metric}: valor atual {current_value:.4f} caiu {drop_pct:.1%} abaixo da "
                f"baseline {baseline_value:.4f} (regressão máxima permitida: "
                f"{baseline.max_regression_pct:.1%})."
            )
    return RegressionCheck(passed=not violations, violations=tuple(violations))
