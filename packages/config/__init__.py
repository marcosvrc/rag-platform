"""Configuração de execução da aplicação (variáveis de ambiente).

Este pacote não é um dos módulos de negócio descritos na seção 7 do
plano (domain/application/contracts/...): ele existe para centralizar a
leitura e validação de variáveis de ambiente via Pydantic Settings
(RAG-004), evitando que apps/ e adapters/ leiam `os.environ` diretamente
em pontos espalhados do código. Decisão registrada no PR de RAG-004.
"""
