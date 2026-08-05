# Amanuo

> **O assistente do coordenador de contrato.**
> Não é o protagonista — é o braço direito. Ele lê, entende, guarda a memória e
> ajuda a coordenar, sem roubar a cena de quem toca o projeto.

Amanuo conecta na sua caixa do Outlook (via Microsoft Graph), entende de qual
contrato cada email trata usando o Claude, e move a mensagem para a pasta certa.
Cada contrato tem **dados fixos** (JSON) e um **contexto vivo** em Markdown, que
alimentam tanto a classificação quanto as respostas sob demanda.

O nome nasce de *amanuense* — o secretário que escrevia a serviço de outro. Essa
é a alma da ferramenta: quem serve e viabiliza. O coordenador é o regente; o
Amanuo é o assistente sempre ao lado.

---

## Status

Este repositório contém o **primeiro degrau** de uma visão maior: triagem de
emails por contrato e uma base de conhecimento por contrato. É a fundação sobre
a qual as camadas seguintes (pessoas, tarefas, plataforma, proatividade) vão
assentar.

**Implementado hoje:**

- [x] `init` — cria a base de contratos a partir das pastas do Outlook
- [x] `ingest` — destila documentos (PDF/DOCX) em dados fixos + contexto
- [x] `bootstrap` — gera contexto inicial a partir de emails já arquivados
- [x] `classify` — lê a inbox, decide o contrato e move o email
- [x] `ask` — responde perguntas sobre a carteira (roteamento JSON/contexto)

**Roadmap** (ver `docs/mapa-de-camadas-amanuo.md` para a visão completa):

- [ ] Novas fontes de contexto: atas e bots de reunião, documentos recebidos,
      mensagens, planilhas, prazos e marcos
- [ ] Camada de pessoas: conhecer a equipe, delegar e cobrar tarefas
- [ ] Plataforma: participantes entram e veem suas próprias obrigações
- [ ] Proatividade: avisar prazos, pendências e riscos sem esperar ordem
- [ ] Interface web e execução agendada
- [ ] Índice vetorial de busca (quando a carteira passar de ~100–150 contratos)

---

## Como funciona

```
                 ┌─────────────────────────────┐
   documentos ──▶│ ingest   (PDF/DOCX → destila)│──┐
   (proposta,    └─────────────────────────────┘  │
    ata, etc.)    fontes/<id>/                     ▼
                                        contracts.json  (dados fixos)
   emails já      ┌─────────────────────────┐      +
   arquivados ──▶ │ bootstrap (emails → .md) │────▶ contexts/<id>.md (contexto vivo)
                  └─────────────────────────┘      ▲
                                                    │ lê
   caixa de       ┌───────────────────────────────┐│
   entrada ─────▶ │ classify (Claude decide pasta) │┘──▶ move o email p/ a pasta
                  └───────────────────────────────┘
                  ┌───────────────────────────────┐
   pergunta ────▶ │ ask (roteia: JSON ou Markdown) │────▶ resposta
                  └───────────────────────────────┘
```

**O modelo de dados é o coração:**

- **`contracts.json`** — a lista mestra. Um registro por contrato com os dados
  fixos (cliente, objeto, valor, prazo, `folder_id` da pasta no Outlook).
  É o *índice* — barato de ler e filtrar.
- **`contexts/<id>.md`** — um Markdown por contrato com o contexto vivo:
  pessoas-chave, escopo, marcos, pendências, vocabulário típico. É o que a IA
  lê para decidir emails "difíceis" (sem número explícito) e para responder
  perguntas de conteúdo.

Essa separação (dado estruturado vs texto livre) é o que mantém o sistema
rápido e barato — e é a base do roteamento de duas vias do `ask`. Todo o resto
da visão (novas fontes, pessoas, tarefas) alimenta essa mesma memória por
contrato.

---

## O pipeline

Ordem natural de uso: **`init` → `ingest` → `bootstrap` → `classify` → `ask`**

| Comando | O que faz | Fonte |
|---|---|---|
| `init` | Cria `contracts.json` com um registro por pasta existente no Outlook | pastas |
| `ingest` | Lê documentos (PDF/DOCX) e destila em dados fixos + contexto | `fontes/<id>/` |
| `bootstrap` | Gera o contexto inicial a partir dos emails já arquivados | emails |
| `classify` | Lê a caixa de entrada, decide o contrato e move o email | inbox |
| `ask` | Responde perguntas sobre a carteira | JSON + Markdown |

`ingest` e `bootstrap` são fontes complementares de contexto: documentos são
mais ricos; emails cobrem o histórico de conversa. Pode usar os dois.

---

## Estrutura do repositório

```
amanuo/
├── src/                        # código
│   ├── run.py                  #   entrada: init, bootstrap, classify
│   ├── ingest.py                #   documentos (PDF/DOCX) → contracts.json + contexts/
│   ├── ask.py                  #   perguntas → roteamento estruturado/contexto
│   ├── classifier.py           #   lógica de IA (classificação e resumo via Claude)
│   └── graph_client.py         #   conexão com o Microsoft Graph (auth, ler, mover)
├── docs/                       # documentação do projeto
│   └── mapa-de-camadas-amanuo.md   #   a visão completa do produto
├── fontes/                     # documentos de origem, uma pasta por contrato (você popula)
│   └── <id>/                   #   ex.: fontes/4021/proposta.pdf, fontes/4021/ata.docx
│                                #   caminho configurável em "fontes_dir" (config.json)
├── contracts.json              # base de dados fixos (gerada)
├── contexts/                   # contexto vivo por contrato, .md (gerado)
├── config.json                 # suas chaves e IDs (NÃO versionar)
├── config.example.json
└── requirements.txt
```

> `docs/` guarda a documentação do próprio projeto; `fontes/` guarda os
> documentos de origem de cada contrato que alimentam o `ingest`. São pastas
> diferentes de propósito — não confundir.

---

## Instalação

Requer Python 3.10+.

```bash
pip install -r requirements.txt
cp config.example.json config.json   # e preencha os valores
```

Dependências: `msal`, `requests`, `anthropic`, `pdfplumber`, `python-docx`.

---

## Configuração

### 1. Registrar o app no Azure (acesso ao Graph)

O Azure é a nuvem da Microsoft. Para um programa acessar seus emails, a
Microsoft exige registrar o programa lá antes. Isso gera um identificador que o
script usa para pedir seu login.

1. Acesse **portal.azure.com** → login com a conta corporativa.
2. Busque **Microsoft Entra ID** (antigo Azure Active Directory).
3. **App registrations** → **New registration**.
4. Nome: `Amanuo`. **Account types**: "this organizational directory only".
   **Redirect URI**: em branco (usamos device code flow). **Register**.
5. Copie **Application (client) ID** e **Directory (tenant) ID** → `config.json`.
6. **API permissions** → **Add** → **Microsoft Graph** → **Delegated
   permissions** → **Mail.ReadWrite**.
7. **Authentication** → ligue **Allow public client flows** = **Yes**. Salve.

> **Por que "Delegated" e não "Application"?** Delegated = o script age *como
> você*, só na *sua* caixa, e só depois de você fazer login. É o modo seguro.
> "Application" daria acesso a *todas* as caixas da empresa — não é o que
> queremos, e é o que preocupa o TI.

### 2. Consentimento do administrador (se necessário)

Dependendo da política da empresa, `Mail.ReadWrite` delegado exige o admin
clicar em "Grant admin consent". Texto pronto para enviar ao TI:

> Assunto: Consentimento — app pessoal de produtividade (Graph)
>
> Registrei um app no nosso Entra ID para uma ferramenta pessoal que organiza
> meus próprios emails por contrato.
> - App: **Amanuo** — App (client) ID: **<cole aqui>**
> - Permissão: **Microsoft Graph → Mail.ReadWrite (Delegated)**
>
> É permissão **delegada**: só funciona *depois que eu faço login* e acessa
> **exclusivamente a minha caixa** — não lê email de mais ninguém. Não há
> permissões de aplicação nem acesso a outras caixas ou dados da organização.
> Preciso do "Grant admin consent" para essa permissão. Obrigado!

### 3. Chave da Anthropic (a IA que classifica)

1. Crie uma chave em **console.anthropic.com** (API Keys).
2. Cole em `config.json` no campo `anthropic_api_key`.

> Isto é a **API**, cobrada por uso — **separada** da assinatura do chat do
> Claude. Não dá para reaproveitar a assinatura num script. Para classificar
> emails, o custo é de centavos por dia em volumes normais.

### config.json

```json
{
  "tenant_id": "...",
  "client_id": "...",
  "anthropic_api_key": "...",
  "model": "claude-sonnet-5",
  "inbox_folder": "Inbox",
  "ignore_folders": ["Inbox", "Sent Items", "Drafts", "Deleted Items", "Junk Email"],
  "fontes_dir": "fontes"
}
```

`fontes_dir` é a pasta onde o `ingest` procura os documentos de origem por
contrato (`fontes_dir/<id>/proposta.pdf`). O padrão é `fontes`; mude se
preferir outro nome ou caminho.

Na primeira execução, o script mostra um código e um link (device code flow):
abra no navegador, faça login e autorize. O token fica em cache local
(`token_cache.bin`) para não precisar logar toda vez.

---

## Uso

```bash
# 1) Esqueleto de contracts.json a partir das pastas do Outlook
python src/run.py init

# 2) Destila documentos em dados fixos + contexto
#    (organize antes: fontes/<id>/proposta.pdf, fontes/<id>/ata.docx)
python src/ingest.py            # todos os contratos
python src/ingest.py 4021       # só um

# 3) Contexto a partir dos emails já arquivados (complementar ao ingest)
python src/run.py bootstrap

# 4) Classifica a inbox
python src/run.py classify          # dry-run: mostra as decisões, não move nada
python src/run.py classify --apply  # move de fato

# 5) Pergunte à carteira
python src/ask.py "quais contratos são do cliente Acme?"       # via JSON
python src/ask.py "qual a pendência aberta no contrato 4021?"  # via Markdown
```

Todos os comandos assumem que você está na raiz do repositório (é lá que
`config.json`, `contracts.json`, `contexts/` e `fontes/` vivem).

Comece sempre o `classify` **sem** `--apply` para conferir antes de mover.
A classificação só move quando a confiança é alta (≥ 0.6); abaixo disso, deixa
o email na inbox para você decidir.

### O roteamento do `ask` (duas vias)

- **Filtro estruturado** (listar, filtrar por cliente/status/valor) → responde
  só com o `contracts.json`. Exato e barato, sem tocar os Markdowns.
- **Conteúdo/contexto** (pendências, o que foi combinado, histórico) → carrega
  apenas o(s) `contexts/<id>.md` relevante(s) e responde citando o contrato.

---

## Notas técnicas (otimização de prompts)

Os scripts aplicam boas práticas da API do Claude:

- **System separado**: o "papel" vai no `system`; a tarefa (email, pergunta)
  na mensagem do usuário.
- **Prompt caching** (`cache_control`): o bloco pesado e estável — todos os
  contratos no `classify`, o índice no `ask` — é marcado para cache. Numa
  rodada de `classify`, esse bloco é idêntico a cada email, então do 2º email
  em diante os tokens vêm do cache (~90% mais baratos). A *escrita* do cache
  tem um adicional (~25%), que acontece no 1º email — por isso o caching
  compensa quando a rodada tem vários emails.
- **Prefill de JSON**: as chamadas que precisam de JSON prefixam a resposta com
  `{`, eliminando preâmbulo e tornando o parse confiável.
- **Relatório de tokens**: cada `classify`/`ask` imprime tokens de entrada,
  saída e quanto veio do cache — métrica real de custo para acompanhar
  conforme a carteira cresce.

---

## Segurança

**Nunca versione segredos.** O `.gitignore` já cobre:

```
config.json
token_cache.bin
contexts/
contracts.json
fontes/
__pycache__/
*.pyc
```

`config.json` tem suas chaves; `token_cache.bin` guarda seu token de acesso;
`contexts/`, `contracts.json` e `fontes/` contêm dados dos contratos. Versione
o código (`src/`), a documentação (`docs/`) e o `config.example.json` — não os
dados nem as credenciais.

---

## Posicionamento

O Amanuo senta num vão pouco atacado do mercado, entre dois mundos:

- **Jurídico / ciclo do documento** (SAP Ariba, Juro, Spellbook): focam no
  documento e no processo de compra. Pesados, corporativos, caros.
- **Gestão de projeto genérica** (Artia, eKyte, ClickUp, Asana): ótimos para
  tarefas, mas o contexto é digitado na mão, não nasce do contrato.

O Amanuo é o assistente do **coordenador de contrato** no dia a dia: puxa
contexto de muitas fontes, entende o contrato e ajuda a coordenar pessoas —
leve e pessoal. Detalhes completos em `docs/mapa-de-camadas-amanuo.md`.

---

## Limitações conhecidas

- Execução manual por enquanto (você roda os comandos). Agendamento é roadmap.
- O `classify` e o `bootstrap` olham as N mensagens mais recentes de cada pasta
  (padrão 25); ajuste conforme necessário.
- Os nomes dos campos fixos extraídos pelo `ingest` são um ponto de partida
  razoável; calibre-os para o vocabulário real dos seus contratos.
