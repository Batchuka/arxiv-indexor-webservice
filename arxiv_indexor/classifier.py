import json
import re
from typing import Callable, cast
import anthropic
from anthropic.types import TextBlock
from arxiv_indexor import get_settings
from arxiv_indexor.db import get_conn, get_setting, get_subscore_eligible, get_unscored_articles, update_score, get_top_unsummarized

DEFAULT_INTEREST_PROFILE = """
TOPIC 1 — Quantum Algorithms
  Primary: papers proposing quantum algorithms that use qubits to solve computational problems — new algorithms, speedup proofs, circuit constructions, quantum query/gate complexity.
  Secondary: quantum complexity theory, error correction schemes that directly enable better algorithms, quantum-classical hybrid algorithms, benchmarking frameworks comparing quantum vs. classical performance.

TOPIC 2 — LLM Context & Memory
  Primary: context compression methods for LLMs, latent/persistent memory across long sequences, memory-augmented transformers, efficient token representation.
  Secondary: efficient attention mechanisms (linear, sparse, sliding-window), KV cache compression, in-context learning mechanics, long-context architecture design.
""".strip()


def get_interest_profile() -> str:
    conn = get_conn()
    profile = get_setting(conn, "interest_profile", DEFAULT_INTEREST_PROFILE)
    conn.close()
    return profile

MODEL = "claude-sonnet-4-20250514"


def _extract_text_payload(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return clean


def _load_json_array(text: str) -> list[dict]:
    clean = _extract_text_payload(text)
    candidates = [clean]

    start = clean.find("[")
    end = clean.rfind("]")
    if start >= 0 and end > start:
        candidates.append(clean[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except json.JSONDecodeError:
            sanitized = re.sub(r",(\s*[}\]])", r"\1", candidate)
            try:
                data = json.loads(sanitized)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
            except json.JSONDecodeError:
                continue

    raise ValueError("Could not parse model response as JSON array")


def _build_scoring_prompt(articles: list[dict], profile: str) -> str:
    items = []
    for a in articles:
        items.append(f"ID: {a['id']}\nTitle: {a['title']}\nAbstract: {a['abstract'][:500]}")
    articles_text = "\n---\n".join(items)

    return f"""You are an academic paper relevance classifier. The two topics below are equally important — score each independently on how well it matches either topic's PRIMARY or SECONDARY scope.

Research interest profile:
{profile}

Scoring scale (both topics treated symmetrically):
- 10: landmark paper squarely in the PRIMARY scope of Topic 1 OR Topic 2 — clear algorithmic/methodological novelty, rigorous contribution.
- 9: solid paper in the PRIMARY scope of Topic 1 OR Topic 2.
- 7-8: paper in the SECONDARY scope of Topic 1 OR Topic 2 (adjacent, within the broader topic area).
- 5-6: related to one of the topic areas broadly but outside both primary and secondary scopes.
- 0-4: unrelated or only tangentially related.

Return a JSON array with objects having "id" (string) and "score" (integer 0-10).
Only return the JSON array, no other text.

Articles:
{articles_text}"""


def _build_subscore_prompt(articles: list[dict], profile: str) -> str:
    items = []
    for a in articles:
        items.append(f"ID: {a['id']}\nTitle: {a['title']}\nAbstract: {a['abstract'][:600]}")
    body = "\n---\n".join(items)

    return (
        "These papers scored 9/10 for relevance to this research profile:\n"
        f"{profile}\n\n"
        "Assign a fine-grained sub-score from 9.0 to 9.9 (one decimal place).\n\n"
        "Criteria (PRIMARY scope = Topic 1 or 2 primary; SECONDARY scope = Topic 1 or 2 secondary):\n"
        "- 9.9  Landmark PRIMARY result — proves a new quantum speedup / solves an open problem, or breakthrough LLM context compression with strong theoretical grounding.\n"
        "- 9.7-9.8  Strong original PRIMARY contribution with clear novelty and rigorous analysis.\n"
        "- 9.5-9.6  Solid PRIMARY contribution — meaningfully improves SOTA or applies the core technique creatively.\n"
        "- 9.3-9.4  Good SECONDARY contribution — clearly within the topic's extended scope, real contribution but not the core focus.\n"
        "- 9.0-9.2  High relevance but thin on PRIMARY novelty — hardware-focused, simulation without algorithmic insight, empirical-only, or mostly SECONDARY scope.\n\n"
        "Return a JSON array with objects having \"id\" (string) and \"score\" (number between 9.0 and 9.9, one decimal).\n"
        "Only return the JSON array, no other text.\n\n"
        f"Articles:\n{body}"
    )


def _build_summary_prompt(articles: list[dict]) -> str:
    body = "\n---\n".join(
        f"ID: {a['id']}\nTitle: {a['title']}\nAbstract: {a['abstract'][:800]}"
        for a in articles
    )
    return (
        "Summarize each of the following articles in exactly 2 sentences in Portuguese (pt-BR).\n"
        "Return a JSON array with objects having \"id\" (string) and \"summary\" (string).\n"
        "Only return the JSON array, no other text.\n\n"
        "Articles IDs and content:\n"
    ) + body


def classify_articles(progress_cb: Callable[..., None] | None = None) -> tuple[int, int, int]:
    """Score unscored articles. Returns (classified_count, input_tokens, output_tokens).

    Articles scoring exactly 9 get a second sub-score pass (9.0–9.9).
    progress_cb(step, processed, total, input_tokens, output_tokens, cost_usd) is called after each batch.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    conn = get_conn()
    profile = get_setting(conn, "interest_profile", DEFAULT_INTEREST_PROFILE)
    articles = get_unscored_articles(conn)

    if not articles:
        conn.close()
        return 0, 0, 0

    classified = 0
    input_tokens = 0
    output_tokens = 0
    total = len(articles)

    # --- Pass 1: integer scoring (0–10) in batches of 20 ---
    for i in range(0, total, 20):
        batch = articles[i : i + 20]
        batch_ids = {a["id"] for a in batch}
        prompt = _build_scoring_prompt(batch, profile)

        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        text = cast(TextBlock, response.content[0]).text
        try:
            parsed = _load_json_array(text)
            for item in parsed:
                article_id = str(item.get("id", "")).strip()
                if article_id not in batch_ids:
                    continue
                try:
                    score = float(item.get("score") or 0)
                except (TypeError, ValueError):
                    continue
                if score == 0:
                    continue
                score = max(0.0, min(10.0, score))
                update_score(conn, article_id, score)
                classified += 1
            conn.commit()
        except Exception:
            conn.rollback()

        if progress_cb:
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            progress_cb(step="scoring", processed=classified, total=total,
                        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

    conn.close()
    return classified, input_tokens, output_tokens


def subscore_articles(progress_cb: Callable[..., None] | None = None) -> tuple[int, int, int]:
    """Refine articles with integer score 9 → 9.0–9.9.

    Returns (subscored_count, input_tokens, output_tokens).
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    conn = get_conn()
    profile = get_setting(conn, "interest_profile", DEFAULT_INTEREST_PROFILE)
    eligible = get_subscore_eligible(conn)

    if not eligible:
        conn.close()
        return 0, 0, 0

    total = len(eligible)
    subscored = 0
    input_tokens = 0
    output_tokens = 0

    # Batch of 10 — each response is ~50 tokens/article, safe under 1024 tokens
    for i in range(0, total, 10):
        batch = eligible[i : i + 10]
        batch_ids = {a["id"] for a in batch}

        if progress_cb:
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            progress_cb(step="subscoring", processed=subscored, total=total,
                        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

        prompt = _build_subscore_prompt(batch, profile)
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        text = cast(TextBlock, response.content[0]).text
        try:
            parsed = _load_json_array(text)
            for item in parsed:
                article_id = str(item.get("id", "")).strip()
                if article_id not in batch_ids:
                    continue
                try:
                    score = float(item.get("score") or 0)
                except (TypeError, ValueError):
                    continue
                if score == 0:
                    continue
                score = max(9.0, min(9.9, score))
                conn.execute("UPDATE articles SET score = ? WHERE id = ?", (score, article_id))
                subscored += 1
            conn.commit()
        except Exception:
            conn.rollback()

    if progress_cb:
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        progress_cb(step="subscoring", processed=subscored, total=total,
                    input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

    conn.close()
    return subscored, input_tokens, output_tokens


def summarize_top_articles(n: int = 5, progress_cb: Callable[..., None] | None = None) -> tuple[int, int, int]:
    """Generate pt-BR summaries for the top-N unsummarized articles (score >= 9).

    Returns (summarized_count, input_tokens, output_tokens).
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    conn = get_conn()
    top = get_top_unsummarized(conn, n=n)

    if not top:
        conn.close()
        return 0, 0, 0

    if progress_cb:
        progress_cb(step="summarizing", processed=0, total=len(top),
                    input_tokens=0, output_tokens=0, cost_usd=0.0)

    prompt = _build_summary_prompt(top)
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    summarized = 0
    text = cast(TextBlock, response.content[0]).text
    try:
        parsed = _load_json_array(text)
        top_ids = {a["id"] for a in top}
        for item in parsed:
            article_id = str(item.get("id", "")).strip()
            if article_id not in top_ids:
                continue
            summary = str(item.get("summary", "")).strip()
            if not summary:
                continue
            conn.execute("UPDATE articles SET summary = ? WHERE id = ?", (summary, article_id))
            summarized += 1
        conn.commit()
    except Exception:
        conn.rollback()

    conn.close()
    return summarized, input_tokens, output_tokens
