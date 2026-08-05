"""Classificação e resumo usando o Claude (API da Anthropic).

Otimizações aplicadas:
- system prompt separado do conteúdo (papel vs tarefa);
- bloco de contratos marcado com cache_control (prefixo estável reaproveitado
  a cada email da rodada → ~90% mais barato do 2º email em diante);
- resposta em JSON via prefill ('{'), eliminando preâmbulo e parse frágil;
- uso de tokens (incl. cache) devolvido junto de cada classificação.
"""

import json
import os
import re
import glob
import anthropic

# Tudo que é gerado/persistido pelo Amanuo (dados de contrato, token de
# acesso) fica agrupado em dados/, fora da raiz do repositório.
DADOS_DIR = "dados"
CONTEXTS_DIR = os.path.join(DADOS_DIR, "contexts")
CONTRACTS_FILE = os.path.join(DADOS_DIR, "contracts.json")

# Padrão de código de contrato: antigo AA00-000 (ex. DF21-100), novo AA00-C000
# (ex. DF22-C038). As duas letras identificam a unidade de negócio; o "C" no
# padrão novo indica contrato. Usado para reconhecer contratos tanto nas
# pastas de documentos (ingest.py) quanto nas pastas do Outlook (run.py).
CODE_RE = re.compile(r"^([A-Za-z]{2}\d{2}-C?\d+)")


def extract_cid(folder_name: str) -> str:
    """Extrai o código do contrato do início do nome da pasta.

    Ex.: 'DF22-C038 - Vale - ATO Maciço Final' -> 'DF22-C038'.
    Sem match, usa o nome inteiro (fallback)."""
    m = CODE_RE.match(folder_name.strip())
    return m.group(1) if m else folder_name

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["contract_id", "confidence", "reason"],
    "additionalProperties": False,
}

SYSTEM_ROLE = (
    "Você é um assistente que organiza emails corporativos por contrato. "
    "Recebe o cadastro dos contratos (dados fixos + contexto) e um email, e "
    "decide a qual contrato o email pertence, com objetividade e cautela: "
    "na dúvida, prefira não classificar a errar o destino."
)


def load_contracts() -> dict:
    if not os.path.exists(CONTRACTS_FILE):
        return {}
    return json.load(open(CONTRACTS_FILE))


def save_contracts(contracts: dict) -> None:
    os.makedirs(DADOS_DIR, exist_ok=True)
    json.dump(contracts, open(CONTRACTS_FILE, "w"), ensure_ascii=False, indent=2)


def load_contexts() -> dict[str, str]:
    ctx = {}
    for path in glob.glob(os.path.join(CONTEXTS_DIR, "*.md")):
        cid = os.path.splitext(os.path.basename(path))[0]
        ctx[cid] = open(path, encoding="utf-8").read()
    return ctx


def _client(config: dict) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=config["anthropic_api_key"])


def _text(msg) -> str:
    """Extrai o texto da resposta, pulando blocos de thinking (o modelo pensa
    por padrão, então content[0] nem sempre é o bloco de texto)."""
    return next((b.text for b in msg.content if b.type == "text"), "")


def _contexts_block(contracts: dict, contexts: dict[str, str]) -> str:
    parts = []
    for cid, meta in contracts.items():
        header = f"### Contrato {cid}"
        if meta.get("cliente"):
            header += f" — {meta['cliente']}"
        summary = contexts.get(cid, "(sem contexto ainda)")
        parts.append(f"{header}\n{summary[:1500]}")
    return "\n\n".join(parts)


def _usage_dict(usage) -> dict:
    """Normaliza o objeto usage da API (com campos de cache) em dict simples."""
    return {
        "input": getattr(usage, "input_tokens", 0),
        "output": getattr(usage, "output_tokens", 0),
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def build_system(contracts: dict, contexts: dict[str, str]) -> list[dict]:
    """System em dois blocos: papel (curto) + contratos (grande, cacheado).

    O cache_control fica no bloco dos contratos, que é idêntico a cada email
    da rodada — só o email (na mensagem do usuário) muda."""
    return [
        {"type": "text", "text": SYSTEM_ROLE},
        {
            "type": "text",
            "text": "<contratos>\n" + _contexts_block(contracts, contexts) + "\n</contratos>",
            "cache_control": {"type": "ephemeral"},
        },
    ]


def classify_email(config, email, body, contracts, contexts, system=None) -> dict:
    """Decide o contrato do email.

    Retorna {contract_id, confidence, reason, usage}. Passe `system` pré-montado
    (via build_system) para reutilizar o mesmo prefixo cacheado na rodada toda.
    """
    if system is None:
        system = build_system(contracts, contexts)
    valid_ids = list(contracts.keys())

    remetente = email.get("from", {}).get("emailAddress", {}).get("address", "")
    user = (
        "<email>\n"
        f"Assunto: {email.get('subject', '')}\n"
        f"De: {remetente}\n"
        f"Corpo:\n{body[:4000]}\n"
        "</email>\n\n"
        f"IDs válidos de contrato: {valid_ids}\n"
        "Decida a qual contrato este email pertence. Se não houver contrato "
        "claro, use contract_id null. confidence vai de 0 a 1."
    )

    msg = _client(config).messages.create(
        model=config.get("model", "claude-sonnet-5"),
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
    )

    if not msg.content:  # recusa de segurança ou similar
        return {"contract_id": None, "confidence": 0.0,
                "reason": "sem resposta do modelo", "usage": _usage_dict(msg.usage)}
    try:
        parsed = json.loads(_text(msg))
    except json.JSONDecodeError:
        return {"contract_id": None, "confidence": 0.0,
                "reason": "parse falhou", "usage": _usage_dict(msg.usage)}

    cid = parsed.get("contract_id")
    if cid not in contracts:  # protege contra id inventado
        cid = None
    return {
        "contract_id": cid,
        "confidence": float(parsed.get("confidence", 0)),
        "reason": parsed.get("reason", ""),
        "usage": _usage_dict(msg.usage),
    }


def summarize_folder(config, folder_name, emails) -> str:
    joined = "\n\n".join(
        f"- {e.get('receivedDateTime','')[:10]} | {e.get('subject','')}\n"
        f"  {e.get('bodyPreview','')[:300]}"
        for e in emails
    )
    user = (
        f"Estes são emails já arquivados na pasta do contrato \"{folder_name}\".\n"
        "Escreva um resumo em Markdown que sirva de CONTEXTO para decidir, no "
        "futuro, se novos emails pertencem a este contrato. Inclua: partes "
        "envolvidas, pessoas-chave, assuntos recorrentes, pendências e "
        "vocabulário típico. Seja conciso (máx ~250 palavras).\n\n"
        f"<emails>\n{joined[:6000]}\n</emails>"
    )
    msg = _client(config).messages.create(
        model=config.get("model", "claude-sonnet-5"),
        max_tokens=600,
        system=[{"type": "text", "text": SYSTEM_ROLE}],
        messages=[{"role": "user", "content": user}],
    )
    return _text(msg).strip()
