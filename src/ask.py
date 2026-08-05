"""Responde perguntas sobre os contratos, roteando em duas vias:

  • Filtro estruturado  → responde a partir do contracts.json (exato, barato)
  • Conteúdo/contexto    → carrega o(s) contexts/<id>.md relevante(s)

Otimizações: system separado, índice de contratos cacheado no roteador,
resposta do roteador em JSON via prefill, e uso de tokens ao final.

Uso:
    python ask.py "quais contratos são do cliente Acme?"
    python ask.py "qual a pendência aberta no contrato 4021?"
"""

import json
import sys

from graph_client import load_config
import classifier

ROUTER_ROLE = (
    "Você consulta uma carteira de contratos. Decide se uma pergunta pode ser "
    "respondida só com os dados estruturados (filtros, listagens) ou se precisa "
    "do conteúdo detalhado (Markdown) de contratos específicos."
)
ANSWER_ROLE = (
    "Você responde perguntas sobre contratos usando apenas o contexto fornecido. "
    "Se a informação não estiver no contexto, diga que não encontrou. Cite o "
    "número do contrato quando fizer sentido."
)


def _index_block(contracts: dict) -> str:
    linhas = []
    for cid, meta in contracts.items():
        campos = {k: v for k, v in meta.items() if k != "folder_id" and v}
        linhas.append(f"- {cid}: {json.dumps(campos, ensure_ascii=False)}")
    return "\n".join(linhas)


def _acc(total, usage):
    total["input"] += getattr(usage, "input_tokens", 0)
    total["output"] += getattr(usage, "output_tokens", 0)
    total["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    total["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0


def _route(config, question, contracts, available_ctx, total):
    """1ª chamada: decide a via e já responde se for filtro estruturado.

    O índice de contratos vai no system com cache_control — assim, se você
    fizer várias perguntas seguidas, o prefixo é reaproveitado."""
    system = [
        {"type": "text", "text": ROUTER_ROLE},
        {
            "type": "text",
            "text": ("<dados_estruturados>\n" + _index_block(contracts) +
                     "\n</dados_estruturados>\n"
                     f"Contratos com contexto detalhado: {list(available_ctx.keys())}"),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    user = (
        f"<pergunta>{question}</pergunta>\n\n"
        "Se der pra responder só com os dados estruturados, responda direto. "
        "Se precisar do conteúdo de contratos, peça-os. Responda SOMENTE com JSON:\n"
        '{"mode": "structured"|"context", "answer": "<resposta se structured, '
        'senão vazio>", "needed_contracts": ["<ids necessários; vazio se structured>"]}'
    )
    msg = classifier._client(config).messages.create(
        model=config.get("model", "claude-sonnet-5"),
        max_tokens=800,
        system=system,
        messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": "{"},  # prefill: força JSON
        ],
    )
    _acc(total, msg.usage)
    try:
        return json.loads("{" + msg.content[0].text)
    except json.JSONDecodeError:
        return {"mode": "context", "answer": "", "needed_contracts": []}


def _answer_with_context(config, question, contracts, contexts, needed, total):
    """2ª chamada: responde usando o Markdown dos contratos relevantes."""
    if not needed:  # roteador não soube apontar → usa todos (caso mais caro)
        needed = list(contexts.keys())

    blocos = []
    for cid in needed:
        meta = contracts.get(cid, {})
        cabec = f"### Contrato {cid}" + (f" — {meta.get('cliente')}" if meta.get("cliente") else "")
        blocos.append(f"{cabec}\n{contexts.get(cid, '(sem contexto)')[:4000]}")
    contexto = "\n\n".join(blocos)

    system = [
        {"type": "text", "text": ANSWER_ROLE},
        {"type": "text", "text": "<contexto>\n" + contexto + "\n</contexto>",
         "cache_control": {"type": "ephemeral"}},
    ]
    msg = classifier._client(config).messages.create(
        model=config.get("model", "claude-sonnet-5"),
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": f"<pergunta>{question}</pergunta>"}],
    )
    _acc(total, msg.usage)
    return msg.content[0].text.strip()


def ask(config, question: str):
    contracts = classifier.load_contracts()
    contexts = classifier.load_contexts()
    if not contracts:
        return "Nenhum contrato cadastrado. Rode 'python run.py init' antes.", None

    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    route = _route(config, question, contracts, contexts, total)

    if route.get("mode") == "structured" and route.get("answer"):
        return route["answer"], total

    resposta = _answer_with_context(
        config, question, contracts, contexts, route.get("needed_contracts", []), total
    )
    return resposta, total


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    question = " ".join(sys.argv[1:])
    resposta, total = ask(load_config(), question)
    print(resposta)
    if total:
        print(
            f"\n[tokens: entrada {total['input']} (cache {total['cache_read']}), "
            f"saída {total['output']}]"
        )


if __name__ == "__main__":
    main()
