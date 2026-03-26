# Classificação com IA

## Como Funciona

A classificação usa o Claude (`claude-sonnet-4-20250514`) em duas etapas sequenciais.

### Etapa 1 — Scoring

Para cada artigo sem score, o sistema envia ao Claude:
- O **título** do artigo
- Os primeiros **500 caracteres do abstract**

Artigos são agrupados em **batches de 20** para reduzir o número de chamadas à API.

O prompt instrui o Claude a retornar um JSON:
```json
[
  {"id": "http://arxiv.org/abs/...", "score": 8.5},
  {"id": "http://arxiv.org/abs/...", "score": 2.0},
  ...
]
```

**Escala de pontuação:**

| Score | Critério                                                                                 |
| ----- | ---------------------------------------------------------------------------------------- |
| 8–10  | Diretamente sobre algoritmos quânticos OU compressão de contexto/memória latente em LLMs |
| 5–7   | Tópicos relacionados (computação quântica, NLP, arquitetura de transformers)             |
| 0–4   | Não relacionado ou apenas tangencialmente relacionado                                    |

### Etapa 2 — Resumo

Após o scoring, os **top 5 artigos do dia** recebem um resumo gerado pelo Claude:
- Abstract enviado com até **800 caracteres**
- Resposta: exatamente **2 frases em português (pt-BR)**
- Armazenado no campo `summary` da tabela `articles`
- Exibido em destaque (caixa verde) na interface web

---

## Perfil de Interesse

O critério de classificação é definido pela constante `INTEREST_PROFILE` em `classifier.py`:

```python
INTEREST_PROFILE = """
Primary interest: quantum algorithms — people proposing using qubits to solve different problems.
Secondary interest: context compression and latent memory for LLMs.
""".strip()
```

Para alterar o perfil, edite essa string. O perfil atual também é visível na aba **Status** da interface web.

---

## Custo e Tokens

### Rastreamento

Cada chamada à API retorna `response.usage.input_tokens` e `response.usage.output_tokens`. Esses valores são acumulados durante toda a execução e salvos na tabela `runs`.

### Estimativa Prévia

Antes de classificar, a interface web exibe uma estimativa baseada nos artigos sem score:

```
input_tokens ≈ (overhead por batch × nº batches) + (chars totais dos abstracts / 4)
output_tokens ≈ (nº artigos × 25) + (5 × 80)  # scoring + resumos
custo ≈ (input × $3 + output × $15) / 1_000_000
```

A estimativa é conservadora — pode variar ±20% dependendo do tamanho real dos abstracts.

### Preços de Referência

| Modelo                     | Input        | Output        |
| -------------------------- | ------------ | ------------- |
| `claude-sonnet-4-20250514` | $3,00 / MTok | $15,00 / MTok |

> Os preços são atualizados em `_PRICE_INPUT` e `_PRICE_OUTPUT` em `web.py` e na fórmula de custo em `__main__.py`.

---

## Salvamento Incremental

A classificação **não espera terminar tudo** para salvar:

```python
for batch in batches:
    # classifica 20 artigos
    conn.commit()  # salva imediatamente
    progress_cb(...)  # atualiza UI
```

Se a execução for interrompida (Ctrl+C, timeout, queda de rede):
- Artigos já classificados têm score salvo
- A próxima execução retoma do ponto onde parou (filtra por `score IS NULL`)
- Nenhum crédito é desperdiçado reclassificando artigos já processados

---

## Retomada Automática

`get_unscored_articles()` sempre busca **apenas artigos sem score**, independente de quando foram indexados:

```sql
SELECT * FROM articles WHERE score IS NULL ORDER BY fetched_at DESC
```

Isso significa que:
- Artigos de dias anteriores que não foram classificados continuam na fila
- Uma interrupção no meio não reprocessa artigos já avaliados
- Rodar `fetch` ou "Classificar com IA" várias vezes é idempotente (do ponto de vista de custo)
