# arXiv Indexor

Indexador diário de artigos do [arXiv](https://arxiv.org/) com classificação automática por relevância via Claude (Anthropic) e interface web para navegação.

## O que faz

1. **Busca** artigos novos nas categorias `quant-ph`, `cs.CL` e `cs.LG` via RSS
2. **Classifica** cada artigo com nota de 0–10 segundo um perfil de interesse definido
3. **Resume** os top-5 artigos em português (2 frases cada) usando Claude
4. **Envia** um digest diário por e-mail (opcional)
5. **Expõe** uma interface web para navegar artigos, ver histórico e acompanhar execuções

O fluxo foi projetado para separar a **indexação** (sem custo de IA) da **classificação** (com custo de IA), permitindo avaliar o custo estimado antes de enviar qualquer requisição ao Claude.

---

## Funcionalidades

- Detecção de artigos novos vs. já indexados (sem duplicatas)
- Estimativa de custo de classificação exibida antes de executar
- Progresso em tempo real na interface web (polling a cada 1,5 s)
- Salvamento incremental: cada batch de 20 artigos é commitado imediatamente — interromper no meio não perde trabalho
- Rastreamento de tokens e custo real por execução
- Interface web responsiva com abas Hoje / Histórico / Status

---

## Requisitos

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- Chave de API da Anthropic (`ANTHROPIC_API_KEY`)
- Conta SMTP para envio de e-mail (opcional)

---

## Instalação

```bash
git clone <repo>
cd arxiv-indexor-webservice

# Instalar dependências
poetry install

# Configurar variáveis de ambiente
cp .env.example .env
# edite .env com suas credenciais
```

---

## Configuração

Copie `.env.example` para `.env` e preencha:

| Variável            | Obrigatória | Descrição                                      |
|---------------------|-------------|------------------------------------------------|
| `ANTHROPIC_API_KEY` | Sim         | Chave de API da Anthropic                      |
| `SMTP_HOST`         | Não         | Servidor SMTP (ex: `smtp.gmail.com`)           |
| `SMTP_PORT`         | Não         | Porta SMTP (padrão: `587`)                     |
| `SMTP_USER`         | Não         | Usuário SMTP                                   |
| `SMTP_PASS`         | Não         | Senha de app SMTP                              |
| `EMAIL_TO`          | Não         | Destinatário do digest diário                  |
| `WEB_HOST`          | Não         | Host do servidor web (padrão: `0.0.0.0`)       |
| `WEB_PORT`          | Não         | Porta do servidor web (padrão: `8000`)         |

> **Nota:** Se `SMTP_*` não estiver configurado, o envio de e-mail é silenciosamente ignorado.

---

## Uso

### Comandos CLI

```bash
# 1. Buscar artigos novos (sem IA, sem custo)
poetry run python -m arxiv_indexor index

# 2. Buscar + classificar + resumir + enviar e-mail
poetry run python -m arxiv_indexor fetch

# 3. Subir interface web
poetry run python -m arxiv_indexor serve
```

### Fluxo recomendado

```
index  →  abrir web  →  ver estimativa de custo  →  clicar "Classificar com IA"
```

1. Rode `index` para detectar artigos novos sem gastar créditos
2. Abra `http://localhost:8000` para ver o card de estimativa de custo
3. Decida se vale a pena e clique **Classificar com IA** na interface

---

## Interface Web

```
http://localhost:8000
```

| Aba         | Conteúdo                                                              |
|-------------|-----------------------------------------------------------------------|
| **Hoje**    | Artigos indexados hoje, ordenados por score. Card de estimativa de custo quando há artigos sem classificação. |
| **Histórico** | Últimos 500 artigos de todas as execuções                           |
| **Status**  | Última execução: artigos buscados/classificados, tokens usados, custo real. Perfil de interesse configurado. |

Botões disponíveis em todas as abas:
- **Buscar artigos** — dispara indexação RSS em background
- **Classificar com IA** — aparece quando há artigos sem score; mostra custo estimado antes

---

## Docker

```bash
# Build
docker build -t arxiv-indexor .

# Interface web (persistindo o banco)
docker run -p 8000:8000 -v $(pwd)/data:/app/data --env-file .env arxiv-indexor serve

# Execução única (cron)
docker run -v $(pwd)/data:/app/data --env-file .env arxiv-indexor fetch
```

---

## Estrutura do Projeto

```
arxiv_indexor/
├── __init__.py       # Settings (pydantic-settings)
├── __main__.py       # CLI: index | fetch | serve
├── feed.py           # Busca RSS do arXiv
├── classifier.py     # Classificação e resumo via Claude
├── db.py             # SQLite (artigos + execuções)
├── web.py            # FastAPI + progresso em tempo real
├── mailer.py         # Digest por e-mail
└── templates/
    └── index.html    # Interface web
```

Veja [docs/](docs/) para documentação detalhada de cada componente.

---

## Categorias Monitoradas

| Categoria | Descrição                              |
|-----------|----------------------------------------|
| `quant-ph` | Física Quântica                       |
| `cs.CL`   | Computation and Language (NLP/LLMs)   |
| `cs.LG`   | Machine Learning                      |

Para adicionar ou remover categorias, edite `CATEGORIES` em [`arxiv_indexor/feed.py`](arxiv_indexor/feed.py).

---

## Custo Estimado

O modelo usado é `claude-sonnet-4-20250514`. Preços de referência:

| Tipo    | Preço          |
|---------|----------------|
| Input   | $3,00 / MTok   |
| Output  | $15,00 / MTok  |

Uma execução típica (50–100 artigos) custa entre **$0,01 e $0,05**.
