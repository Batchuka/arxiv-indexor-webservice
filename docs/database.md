# Banco de Dados

O projeto usa SQLite (`arxiv.db`) com WAL mode habilitado.

> O arquivo `arxiv.db` está no `.gitignore` — não é versionado.

---

## Tabela `articles`

Armazena todos os artigos indexados.

| Coluna       | Tipo    | Descrição                                               |
| ------------ | ------- | ------------------------------------------------------- |
| `id`         | TEXT PK | ID do arXiv (ex: `http://arxiv.org/abs/2501.12345v1`)   |
| `title`      | TEXT    | Título do artigo                                        |
| `authors`    | TEXT    | Autores (string concatenada)                            |
| `abstract`   | TEXT    | Abstract completo                                       |
| `category`   | TEXT    | Categoria (`quant-ph`, `cs.CL`, `cs.LG`)                |
| `published`  | TEXT    | Data de publicação (string do RSS)                      |
| `link`       | TEXT    | URL do artigo no arXiv                                  |
| `score`      | REAL    | Nota 0–10 atribuída pelo Claude (`NULL` = não avaliado) |
| `summary`    | TEXT    | Resumo em pt-BR gerado pelo Claude (top-5 apenas)       |
| `read`       | INTEGER | Flag de lido (`0`/`1`), não usado na UI ainda           |
| `fetched_at` | TEXT    | Timestamp de inserção (`datetime('now')`)               |

**Deduplicação:** `INSERT OR IGNORE` garante que o mesmo artigo nunca é inserido duas vezes (deduplicação por `id`).

**Retomada:** artigos com `score IS NULL` são os candidatos à próxima classificação. Se uma execução for interrompida, os artigos já classificados (com score) não são reprocessados.

---

## Tabela `runs`

Rastreia cada execução do pipeline.

| Coluna                | Tipo    | Descrição                                      |
| --------------------- | ------- | ---------------------------------------------- |
| `id`                  | INTEGER | Chave primária auto-incremento                 |
| `started_at`          | TEXT    | Timestamp de início                            |
| `status`              | TEXT    | `running` → `success` ou `error`               |
| `articles_fetched`    | INTEGER | Artigos novos encontrados no RSS               |
| `articles_classified` | INTEGER | Artigos classificados com score nessa execução |
| `error`               | TEXT    | Mensagem de erro (se `status = error`)         |
| `input_tokens`        | INTEGER | Tokens de entrada consumidos na execução       |
| `output_tokens`       | INTEGER | Tokens de saída consumidos na execução         |

**Migration automática:** as colunas `input_tokens` e `output_tokens` foram adicionadas depois da criação original da tabela. O `init_db()` executa `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` para garantir compatibilidade com bancos existentes.

---

## Queries Principais

```sql
-- Artigos de hoje (ordenados por relevância)
SELECT * FROM articles
WHERE date(fetched_at) = date('now')
ORDER BY score DESC;

-- Artigos aguardando classificação
SELECT * FROM articles
WHERE score IS NULL
ORDER BY fetched_at DESC;

-- Top 5 do dia (para resumo e digest)
SELECT * FROM articles
WHERE date(fetched_at) = date('now')
  AND score IS NOT NULL
ORDER BY score DESC
LIMIT 5;

-- Última execução
SELECT * FROM runs ORDER BY id DESC LIMIT 1;
```

---

## Inicialização

```python
from arxiv_indexor.db import init_db
init_db()  # cria tabelas se não existirem + migrations
```

Seguro chamar múltiplas vezes (`CREATE TABLE IF NOT EXISTS`).
