"""Erros de aplicação (RAG-013).

Categorias de problema independentes de transporte: representam o que
pode dar errado em um caso de uso, não como isso vira uma resposta HTTP
— essa tradução (Problem Details, RFC 7807) fica em `apps/api/errors.py`,
o adapter que sabe que existe um transporte HTTP.

Cada subclasse fixa um `title` (RFC 7807) padrão; `detail` é a mensagem
específica da ocorrência, sempre segura para o cliente ver (nunca deve
incluir dados sensíveis nem detalhes internos de implementação).
"""


class ApplicationError(Exception):
    """Base de todo erro de aplicação."""

    title: str = "Erro de aplicação"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(detail or self.title)


class InvalidRequestError(ApplicationError):
    """A requisição é malformada de um jeito não coberto pelas
    categorias mais específicas abaixo (400)."""

    title = "Requisição inválida"


class AuthenticationError(ApplicationError):
    """Credenciais ausentes ou inválidas (401)."""

    title = "Autenticação necessária"


class PermissionDeniedError(ApplicationError):
    """Autenticado, mas sem permissão para o recurso — inclui tentar
    acessar um recurso de outro tenant (403)."""

    title = "Acesso negado"


class NotFoundError(ApplicationError):
    """O recurso solicitado não existe (ou não é visível para o tenant
    atual — do ponto de vista do cliente, é o mesmo caso) (404)."""

    title = "Recurso não encontrado"


class ConflictError(ApplicationError):
    """A operação conflita com o estado atual do recurso — nome
    duplicado, versão desatualizada, etc. (409)."""

    title = "Conflito"


class UnprocessableEntityError(ApplicationError):
    """Os dados são bem-formados (passam na validação de schema) mas
    violam uma regra de negócio (422)."""

    title = "Entidade não processável"


class ServiceUnavailableError(ApplicationError):
    """Uma dependência externa (RAG-044: o gateway de geração LiteLLM)
    falhou depois de esgotar suas próprias tentativas de retry — uma
    indisponibilidade real de infraestrutura, nunca "o modelo respondeu
    algo sem evidência suficiente" (isso tem sua própria resposta segura,
    RAG-043, sem levantar exceção) (503)."""

    title = "Serviço indisponível"
