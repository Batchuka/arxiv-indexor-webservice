import json
from typing import Callable, cast
import anthropic
from anthropic.types import TextBlock
from arxiv_indexor import get_settings
from arxiv_indexor.db import get_conn, get_unscored_articles, update_score, get_top_articles

INTEREST_PROFILE = """
Primary interest: quantum algorithms — people proposing using qubits to solve different problems.
Secondary interest: context compression and latent memory for LLMs.
""".strip()

MODEL = "claude-sonnet-4-20250514"


def _build_scoring_prompt(articles: list[dict]) -> str:
    items = []
    for a in articles:
        items.append(f"ID: {a['id']}\nTitle: {a['title']}\nAbstract: {a['abstract'][:500]}")
    articles_text = "\n---\n".join(items)

    return f"""You are an academic paper relevance classifier.

Research interest profile:
{INTEREST_PROFILE}

Rate each article from 0 to 10 based on relevance to the profile above.
- 8-10: directly about quantum algorithms or LLM context compression/latent memory
- 5-7: related topics (quantum computing, NLP, transformers architecture)
- 0-4: unrelated or only tangentially related

Return a JSON array with objects having "id" (string) and "score" (number).
Only return the JSON array, no other text.

Articles:
{articles_text}"""


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
    """Returns (classified_count, input_tokens, output_tokens).

    progress_cb(step, processed, total, input_tokens, output_tokens, cost_usd) is called
    after each batch so callers can track progress and cost in real time.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    conn = get_conn()
    articles = get_unscored_articles(conn)

    if not articles:
        conn.close()
        return 0, 0, 0

    classified = 0
    input_tokens = 0
    output_tokens = 0
    total = len(articles)

    # Score in batches of 20 — commit after each batch so a crash loses at most one batch
    for i in range(0, total, 20):
        batch = articles[i : i + 20]
        prompt = _build_scoring_prompt(batch)

        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        text = cast(TextBlock, response.content[0]).text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        for item in json.loads(text):
            update_score(conn, item["id"], item["score"])
            classified += 1

        conn.commit()  # incremental save after every batch

        if progress_cb:
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            progress_cb(step="scoring", processed=classified, total=total,
                        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

    # Generate summaries for top 5
    top = get_top_articles(conn, n=5)
    if top:
        if progress_cb:
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            progress_cb(step="summarizing", processed=classified, total=total,
                        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

        prompt = _build_summary_prompt(top)
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        text = cast(TextBlock, response.content[0]).text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        for item in json.loads(text):
            conn.execute("UPDATE articles SET summary = ? WHERE id = ?", (item["summary"], item["id"]))

        conn.commit()

    conn.close()
    return classified, input_tokens, output_tokens
