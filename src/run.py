"""Ponto de entrada. Uso:

    python run.py init                 # cria contracts.json a partir das pastas
    python run.py bootstrap            # gera contexts/<id>.md dos emails antigos
    python run.py classify             # dry-run: mostra o que faria
    python run.py classify --apply     # move de fato os emails
"""

import json
import os
import re
import sys

from graph_client import GraphClient, load_config
import classifier

CONTEXTS_DIR = "contexts"


def _slug(name: str) -> str:
    """Transforma o nome da pasta num id de arquivo seguro."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")


def _contract_folders(graph: GraphClient, config: dict) -> list[dict]:
    """Pastas que representam contratos (ignora as de sistema)."""
    ignore = set(config.get("ignore_folders", []))
    return [f for f in graph.list_folders() if f["displayName"] not in ignore]


def cmd_init(graph, config):
    """Cria contracts.json com um registro por pasta existente."""
    contracts = classifier.load_contracts()
    for f in _contract_folders(graph, config):
        cid = _slug(f["displayName"])
        contracts.setdefault(cid, {
            "folder_id": f["id"],
            "folder_name": f["displayName"],
            "cliente": "",
            "descricao": "",
        })
        # mantém o folder_id atualizado
        contracts[cid]["folder_id"] = f["id"]
        contracts[cid]["folder_name"] = f["displayName"]
    json.dump(contracts, open("contracts.json", "w"), ensure_ascii=False, indent=2)
    print(f"contracts.json criado/atualizado com {len(contracts)} contrato(s).")
    print("Dica: preencha 'cliente' e 'descricao' à mão quando puder.")


def cmd_bootstrap(graph, config):
    """Gera o contexto Markdown inicial de cada contrato."""
    os.makedirs(CONTEXTS_DIR, exist_ok=True)
    contracts = classifier.load_contracts()
    if not contracts:
        sys.exit("Rode 'python run.py init' antes.")
    for cid, meta in contracts.items():
        out = os.path.join(CONTEXTS_DIR, f"{cid}.md")
        if os.path.exists(out):
            print(f"  pulando {cid} (já tem contexto)")
            continue
        emails = graph.get_messages(meta["folder_id"], top=25)
        if not emails:
            print(f"  {cid}: sem emails, pulando")
            continue
        print(f"  resumindo {cid} ({len(emails)} emails)...")
        summary = classifier.summarize_folder(config, meta["folder_name"], emails)
        open(out, "w", encoding="utf-8").write(
            f"# {meta['folder_name']}\n\n{summary}\n"
        )
    print("Contextos gerados em ./contexts/")


def cmd_classify(graph, config, apply: bool):
    contracts = classifier.load_contracts()
    contexts = classifier.load_contexts()
    if not contracts:
        sys.exit("Rode 'python run.py init' antes.")

    # localiza a pasta Inbox
    inbox_name = config.get("inbox_folder", "Inbox")
    inbox = next(
        (f for f in graph.list_folders() if f["displayName"] == inbox_name), None
    )
    if not inbox:
        sys.exit(f"Pasta '{inbox_name}' não encontrada.")

    emails = graph.get_messages(inbox["id"], top=25)
    print(f"{len(emails)} email(s) na inbox.\n")

    # monta o system UMA vez: o bloco de contratos (cacheado) é o mesmo pra
    # todos os emails, então o cache é reaproveitado a cada iteração.
    system = classifier.build_system(contracts, contexts)
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    for e in emails:
        body = graph.get_message_body(e["id"])
        res = classifier.classify_email(config, e, body, contracts, contexts, system=system)
        cid = res["contract_id"]
        subj = e.get("subject", "(sem assunto)")[:60]
        u = res["usage"]
        for k in total:
            total[k] += u[k]

        # tokens deste email (cache = quanto do input veio barato do cache)
        tok = (f"tokens: in={u['input']} cache={u['cache_read']} "
               f"out={u['output']}")

        if not cid or res["confidence"] < 0.6:
            print(f"[  MANTÉM ] {subj}  ({res['reason']})  | {tok}")
            continue

        print(f"[-> {cid}] {subj}  (conf {res['confidence']:.2f}: {res['reason']})  | {tok}")
        if apply:
            graph.move_message(e["id"], contracts[cid]["folder_id"])

    print(
        f"\nTotal: entrada {total['input']} tok "
        f"(cache lido {total['cache_read']}, escrito {total['cache_write']}), "
        f"saída {total['output']} tok."
    )
    if not apply:
        print("(dry-run — nada foi movido. Use --apply para mover.)")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    config = load_config()
    graph = GraphClient(config)

    if cmd == "init":
        cmd_init(graph, config)
    elif cmd == "bootstrap":
        cmd_bootstrap(graph, config)
    elif cmd == "classify":
        cmd_classify(graph, config, apply="--apply" in sys.argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
