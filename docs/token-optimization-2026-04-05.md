# Token Optimization — 2026-04-05

## Context

The classification pipeline was spending more tokens than necessary. Three targeted changes were made to reduce cost without altering the web interface, breaking backward compatibility, or discarding any stored classification data.

---

## Changes Applied

### 1. Haiku for Initial Scoring Pass (`classifier.py`)

**Before**: All three pipeline stages (scoring, sub-scoring, summarization) used `claude-sonnet-4-20250514` ($3.00/$15.00 per MTok in/out).

**After**: The initial scoring pass (0–10 integer score) now uses `claude-haiku-4-5-20251001` ($0.80/$4.00 per MTok). Sub-scoring (9.0–9.9) and summarization continue to use Sonnet.

**Why this is safe**: The scoring pass is a binary relevance filter. It does not require nuanced reasoning — it only needs to decide whether a paper is about quantum algorithms or LLM context/memory. Haiku handles this reliably. The Sonnet model is still used for the precision work (sub-scoring) and for generating Portuguese summaries.

**Estimated savings**: ~85–90% reduction in cost for the scoring pass, which accounts for the majority of total token spend.

**Constants introduced**:
```python
MODEL = "claude-sonnet-4-20250514"          # subscore + summarize
SCORING_MODEL = "claude-haiku-4-5-20251001"  # initial scoring pass
```

---

### 2. Aggressive Abstract Truncation (`classifier.py`, `web.py`)

**Before**:
- Scoring pass: `abstract[:500]`
- Sub-scoring pass: `abstract[:600]`
- Summarization pass: `abstract[:800]` (unchanged)

**After**:
- Scoring pass: `abstract[:200]`
- Sub-scoring pass: `abstract[:350]`
- Summarization pass: `abstract[:800]` (unchanged — quality matters here)

**Why this is safe**: Scientific abstracts follow a predictable structure. The first 1–2 sentences state the problem and the main contribution. For relevance classification, 200 characters is enough to determine topic alignment. Sub-scoring requires slightly more detail to distinguish 9.3 from 9.7, hence the less aggressive cut of 350. Summarization sends the full 800 chars because the output quality matters to the reader.

**Estimated savings**: ~55–60% reduction in abstract token volume for the scoring pass; ~40% for sub-scoring.

---

### 3. Larger Batch Size for Scoring Pass (`classifier.py`, `web.py`)

**Before**: Scoring processed articles in batches of 20, making `ceil(N/20)` API calls. Each call pays the fixed overhead of the interest profile (~100 tokens) and the prompt scaffolding (~120 tokens).

**After**: Scoring processes articles in batches of 40, halving the number of API calls. `max_tokens` was raised from 2048 to 3500 to accommodate up to 40 JSON responses per response.

**Why this is safe**: The JSON output per article is small (~10 tokens for `{"id": "...", "score": 8}`). 40 articles × 10 tokens = 400 output tokens, well within the 3500 limit. The model reliably handles large batches of this kind at temperature 0.

**Estimated savings**: ~50% reduction in fixed overhead per article (interest profile paid once per 40 articles instead of per 20).

---

## Pricing Constants (updated in `web.py`)

| Constant               | Value       | Used for                                            |
| ---------------------- | ----------- | --------------------------------------------------- |
| `_PRICE_INPUT_HAIKU`   | $0.80/MTok  | Scoring pass estimate + cost display                |
| `_PRICE_OUTPUT_HAIKU`  | $4.00/MTok  | Scoring pass estimate + cost display                |
| `_PRICE_INPUT_SONNET`  | $3.00/MTok  | Sub-scoring + summarization estimate + cost display |
| `_PRICE_OUTPUT_SONNET` | $15.00/MTok | Sub-scoring + summarization estimate + cost display |

---

## Combined Effect

For a typical daily run of ~100 articles:

| Stage                       | Before      | After       | Reduction |
| --------------------------- | ----------- | ----------- | --------- |
| Scoring (100 articles)      | ~$0.040     | ~$0.003     | ~93%      |
| Sub-scoring (5–10 articles) | ~$0.005     | ~$0.003     | ~30%      |
| Summarization (5 articles)  | ~$0.004     | ~$0.004     | —         |
| **Total**                   | **~$0.049** | **~$0.010** | **~80%**  |

---

## Compatibility

All changes are fully backward-compatible:
- Scores stored in the database are identical in format (0–10 integers, 9.0–9.9 floats).
- No database schema changes.
- The web interface (buttons, pipeline panel, progress bar) is unchanged.
- Existing classifications are not affected.
- The interest profile stored in `settings` is unchanged.
