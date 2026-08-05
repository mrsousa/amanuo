"""Ponto de entrada. Uso:

    python run.py init                 # cria contracts.json a partir das pastas
    python run.py bootstrap            # gera contexts/<id>.md dos emails antigos
    python run.py classify             # dry-run: mostra o que faria
    python run.py classify --apply     # move de fato os emails
"""

import os
import sys

from graph_client import GraphClient, load_config
import classifier


def _contract_folders(graph: GraphClient, config: dict) -> list[dict]:
    """Percorre a árvore de pastas do Outlook recursivamente e retorna as que
    representam contratos — reconhecidas pelo nome bater com o padrão de
    código (AA00-000 ou AA00-C000, ex. DF21-100, DF22-C038), em qualquer
    profundidade. Não desce em pastas de sistema (ignore_folders) nem
    continua descendo depois de encontrar uma pasta de contrato.

    Cada pasta retornada ganha uma chave extra "_caminho" com o caminho
    completo (separado por " > ") desde o nível superior, para auditoria."""
    ignore = set(config.get("ignore_folders", []))
    encontradas = []
    fila = [(f, f["displayName"]) for f in graph.list_folders()
            if f["displayName"] not in ignore]
    while fila:
        f, caminho = fila.pop(0)
        if classifier.CODE_RE.match(f["displayName"]):
            f["_caminho"] = caminho
            encontradas.append(f)
            continue
        if f.get("childFolderCount", 0) > 0:
            fila.extend(
                (filho, f"{caminho} > {filho['displayName']}")
                for filho in graph.list_child_folders(f["id"])
            )
    return encontradas


def cmd_init(graph, config):
    """Cria contracts.json com um registro por pasta de contrato encontrada.

    Um contrato é marcado "ativo": true quando o caminho da pasta passa
    exatamente pela pasta configurada em "pasta_ativos" (config.json,
    padrão "02 - Projetos") — as demais (ex.: "...Finalizados e Outros",
    anos anteriores) entram como "ativo": false."""
    pasta_ativos = config.get("pasta_ativos", "02 - Projetos")
    contracts = classifier.load_contracts()
    for f in _contract_folders(graph, config):
        cid = classifier.extract_cid(f["displayName"])
        caminho = f["_caminho"]
        ativo = pasta_ativos in caminho.split(" > ")
        contracts.setdefault(cid, {
            "folder_id": f["id"],
            "folder_name": f["displayName"],
            "folder_path": caminho,
            "ativo": ativo,
            "cliente": "",
            "descricao": "",
        })
        # mantém os metadados de pasta atualizados
        contracts[cid]["folder_id"] = f["id"]
        contracts[cid]["folder_name"] = f["displayName"]
        contracts[cid]["folder_path"] = caminho
        contracts[cid]["ativo"] = ativo
    classifier.save_contracts(contracts)
    print(f"contracts.json criado/atualizado com {len(contracts)} contrato(s).")
    print("Dica: preencha 'cliente' e 'descricao' à mão quando puder.")


def cmd_bootstrap(graph, config):
    """Gera o contexto Markdown inicial de cada contrato."""
    os.makedirs(classifier.CONTEXTS_DIR, exist_ok=True)
    contracts = classifier.load_contracts()
    if not contracts:
        sys.exit("Rode 'python run.py init' antes.")
    for cid, meta in contracts.items():
        out = os.path.join(classifier.CONTEXTS_DIR, f"{cid}.md")
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
    print(f"Contextos gerados em ./{classifier.CONTEXTS_DIR}/")


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
