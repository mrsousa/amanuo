"""Gera contexto (.md) e dados fixos (contracts.json) a partir de DOCUMENTOS.

Fonte mais rica que os emails: proposta comercial, atas, contrato. Você
organiza os arquivos por contrato e o Claude destila cada pasta em duas
saídas de uma vez: campos estruturados + contexto em Markdown.

A pasta raiz onde ficam esses documentos é configurável (chave "fontes_dir"
em config.json, padrão "fontes") para não colidir com a pasta docs/ do
próprio repositório. Estrutura esperada dentro dela:

    fontes/
      4021/
        proposta.pdf
        ata_2024-03.docx
      4055/
        contrato.pdf

O nome da pasta (4021, 4055) é o ID do contrato — o mesmo usado em
contracts.json e contexts/<id>.md.

Uso:
    python ingest.py            # processa todos os contratos em fontes_dir
    python ingest.py 4021       # processa só um contrato
"""

import json
import os
import sys
import glob

import classifier
from graph_client import load_config

CONTEXTS_DIR = "contexts"
MAX_CHARS = 20000  # teto de texto por contrato enviado ao modelo

INGEST_ROLE = (
    "Você lê documentos de um contrato (proposta, ata, contrato) e produz: "
    "(1) os dados fixos estruturados e (2) um contexto em Markdown que servirá "
    "para uma IA decidir, no futuro, se novos emails pertencem a este contrato. "
    "Extraia apenas o que estiver nos documentos; não invente."
)


# ---------- extração de texto ----------
def extract_pdf(path: str) -> str:
    import pdfplumber
    partes = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            partes.append(page.extract_text() or "")
    return "\n".join(partes)


def extract_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    linhas = [p.text for p in doc.paragraphs]
    # também pega texto de tabelas (comum em propostas)
    for tbl in doc.tables:
        for row in tbl.rows:
            linhas.append(" | ".join(c.text for c in row.cells))
    return "\n".join(linhas)


def extract_folder_text(folder: str) -> str:
    """Concatena o texto de todos os PDF/DOCX de uma pasta, marcando a fonte."""
    blocos = []
    arquivos = sorted(glob.glob(os.path.join(folder, "*")))
    for path in arquivos:
        ext = os.path.splitext(path)[1].lower()
        nome = os.path.basename(path)
        try:
            if ext == ".pdf":
                texto = extract_pdf(path)
            elif ext in (".docx", ".doc"):
                texto = extract_docx(path)
            else:
                continue
        except Exception as e:  # não deixa um arquivo ruim derrubar tudo
            print(f"    ! falha lendo {nome}: {e}")
            continue
        blocos.append(f"<documento nome=\"{nome}\">\n{texto.strip()}\n</documento>")
    return "\n\n".join(blocos)


# ---------- destilação via Claude ----------
def distill(config, cid: str, texto: str) -> tuple[dict, str, dict]:
    """Retorna (campos_fixos, contexto_md, usage)."""
    user = (
        f"Documentos do contrato \"{cid}\":\n\n{texto[:MAX_CHARS]}\n\n"
        "Produza SOMENTE um JSON com duas chaves:\n"
        '{"fields": {"cliente": "", "numero_contrato": "", "objeto": "", '
        '"valor": "", "data_inicio": "", "prazo": "", "responsavel": ""}, '
        '"context_md": "<contexto em Markdown: partes, pessoas-chave, escopo, '
        'marcos, pendências e vocabulário típico; ~250 palavras>"}\n'
        "Deixe em branco os campos que não encontrar. Não invente dados."
    )
    msg = classifier._client(config).messages.create(
        model=config.get("model", "claude-sonnet-5"),
        max_tokens=1500,
        system=[{"type": "text", "text": INGEST_ROLE}],
        messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": "{"},  # prefill: força JSON
        ],
    )
    usage = classifier._usage_dict(msg.usage)
    try:
        data = json.loads("{" + msg.content[0].text)
    except json.JSONDecodeError:
        return {}, "", usage
    fields = {k: v for k, v in (data.get("fields") or {}).items() if v}
    return fields, data.get("context_md", ""), usage


# ---------- orquestração ----------
def process_contract(config, cid: str, contracts: dict, fontes_dir: str) -> dict | None:
    folder = os.path.join(fontes_dir, cid)
    texto = extract_folder_text(folder)
    if not texto.strip():
        print(f"  {cid}: nenhum PDF/DOCX legível em {folder}, pulando")
        return None

    print(f"  {cid}: destilando {len(texto)} chars...")
    fields, context_md, usage = distill(config, cid, texto)

    # --- dados fixos → contracts.json (preserva metadados de pasta do init) ---
    rec = contracts.get(cid, {})
    preservar = {k: rec[k] for k in ("folder_id", "folder_name") if k in rec}
    rec.update(fields)
    rec.update(preservar)
    contracts[cid] = rec

    # --- contexto → contexts/<id>.md ---
    if context_md:
        os.makedirs(CONTEXTS_DIR, exist_ok=True)
        titulo = rec.get("folder_name") or rec.get("cliente") or cid
        with open(os.path.join(CONTEXTS_DIR, f"{cid}.md"), "w", encoding="utf-8") as f:
            f.write(f"# {titulo} ({cid})\n\n{context_md}\n")
    return usage


def main():
    config = load_config()
    contracts = classifier.load_contracts()
    fontes_dir = config.get("fontes_dir", "fontes")

    if len(sys.argv) > 1:
        alvos = [sys.argv[1]]
    else:
        if not os.path.isdir(fontes_dir):
            sys.exit(f"Pasta '{fontes_dir}/' não existe. Crie {fontes_dir}/<id>/ com os documentos.")
        alvos = [d for d in os.listdir(fontes_dir)
                 if os.path.isdir(os.path.join(fontes_dir, d))]
    if not alvos:
        sys.exit(f"Nenhum contrato encontrado em {fontes_dir}/.")

    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for cid in sorted(alvos):
        u = process_contract(config, cid, contracts, fontes_dir)
        if u:
            for k in total:
                total[k] += u[k]

    json.dump(contracts, open("contracts.json", "w"), ensure_ascii=False, indent=2)
    print(f"\ncontracts.json e contexts/ atualizados.")
    print(f"[tokens: entrada {total['input']}, saída {total['output']}]")


if __name__ == "__main__":
    main()
