# Referência de Comandos

## Uso Geral

```bash
poetry run python -m arxiv_indexor <comando>
```

---

## `index` — Indexar artigos novos (sem IA)

```bash
poetry run python -m arxiv_indexor index
```

**O que faz:**
- Busca os feeds RSS de `quant-ph`, `cs.CL` e `cs.LG`
- Insere apenas artigos ainda não presentes no banco (`INSERT OR IGNORE`)
- Exibe no terminal a lista de novos artigos (título, categoria, link)
- **Não chama a API do Claude — custo zero**

**Saída esperada:**
```
[index] Fetching arXiv RSS feeds (no AI)...
[index] 47 new articles:

  [quant-ph] Quantum Algorithm for Linear Systems of Equations
             http://arxiv.org/abs/...

  [cs.LG] Efficient Context Compression for Long-Context LLMs
          http://arxiv.org/abs/...
  ...

[index] Done. 47 new articles indexed.
```

**Quando usar:** sempre que quiser verificar o que chegou de novo antes de decidir gastar créditos com classificação.

---

## `fetch` — Pipeline completo (com IA)

```bash
poetry run python -m arxiv_indexor fetch
```

**O que faz:**
1. Indexa artigos novos (RSS)
2. Classifica **todos** os artigos sem score com Claude (batches de 20)
3. Gera resumo em português para os top-5
4. Envia digest por e-mail (se SMTP configurado)
5. Registra a execução na tabela `runs`

**Saída esperada:**
```
[fetch] Fetching RSS feeds...
[fetch] 12 new articles fetched
[classify] Classifying articles with Claude...
[classify] 59 articles classified
[classify] tokens: 18432 in / 1240 out — ~$0.0740 (estimado)
[mail] Sending digest with 5 top articles...
[done] Fetch complete.
```

**Nota:** classifica **todos** os artigos sem score, não apenas os do dia atual. Se `index` foi rodado em dias anteriores sem classificar, esses artigos acumulados também serão classificados.

---

## `serve` — Interface web

```bash
poetry run python -m arxiv_indexor serve
```

**O que faz:**
- Sobe o servidor FastAPI com uvicorn em `http://localhost:8000`
- Hot-reload ativado (detecta mudanças nos arquivos automaticamente)

**Saída esperada:**
```
[serve] Starting web interface on http://localhost:8000
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**Porta e host** podem ser alterados via `.env`:
```env
WEB_HOST=127.0.0.1
WEB_PORT=9000
```

---

## Uso com Cron

Para execução automática diária:

```bash
# Crontab: rodar às 7h todos os dias
0 7 * * * cd /caminho/do/projeto && poetry run python -m arxiv_indexor fetch >> /var/log/arxiv-indexor.log 2>&1
```

Ou usando o Docker:

```bash
# docker-compose.yml
services:
  indexor:
    image: arxiv-indexor
    command: fetch
    env_file: .env
    volumes:
      - ./data:/app/data
```

---

## Ordem de Execução Recomendada

```bash
# Terminal 1 (deixar rodando)
poetry run python -m arxiv_indexor serve

# Terminal 2 (quando quiser atualizar)
poetry run python -m arxiv_indexor index
# → abre http://localhost:8000, vê estimativa, clica "Classificar com IA"
```
