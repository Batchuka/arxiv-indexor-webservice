from pathlib import Path
from urllib.parse import urlencode
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from arxiv_indexor.classifier import DEFAULT_INTEREST_PROFILE, classify_articles, subscore_articles, summarize_top_articles
from arxiv_indexor.db import (
    clear_all_scores,
    finish_run,
    get_conn,
    get_last_run,
    get_last_run_by_op,
    get_setting,
    get_subscore_eligible,
    get_today_fetch_stats,
    get_today_new_articles_page,
    get_top_unsummarized,
    get_unscored_articles,
    init_db,
    insert_run,
    search_ranked_articles_page,
    set_setting,
)
from arxiv_indexor.feed import fetch_articles

# Pricing ($/million tokens)
_PRICE_INPUT_SONNET = 3.0    # subscore + summarize
_PRICE_OUTPUT_SONNET = 15.0
_PRICE_INPUT_HAIKU = 0.80    # initial scoring pass
_PRICE_OUTPUT_HAIKU = 4.0


def _estimate_classification_cost(articles: list[dict]) -> dict:
    n = len(articles)
    if n == 0:
        return {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    batches = (n + 39) // 40  # batch size 40
    scoring_chars = sum(
        len(a.get("id", "")) + len(a.get("title", "")) + len((a.get("abstract") or "")[:200])
        for a in articles
    )
    input_tokens = batches * 220 + scoring_chars // 4
    output_tokens = n * 25
    cost = (input_tokens * _PRICE_INPUT_HAIKU + output_tokens * _PRICE_OUTPUT_HAIKU) / 1_000_000
    return {"count": n, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}


def _estimate_summary_cost(articles: list[dict]) -> dict:
    n = len(articles)
    if n == 0:
        return {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    chars = sum(
        len(a.get("id", "")) + len(a.get("title", "")) + len((a.get("abstract") or "")[:800])
        for a in articles
    )
    input_tokens = 160 + chars // 4
    output_tokens = n * 80
    cost = (input_tokens * _PRICE_INPUT_SONNET + output_tokens * _PRICE_OUTPUT_SONNET) / 1_000_000
    return {"count": n, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}


def _estimate_subscore_cost(articles: list[dict]) -> dict:
    n = len(articles)
    if n == 0:
        return {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    chars = sum(
        len(a.get("id", "")) + len(a.get("title", "")) + len((a.get("abstract") or "")[:350])
        for a in articles
    )
    input_tokens = 300 + chars // 4
    output_tokens = n * 20
    cost = (input_tokens * _PRICE_INPUT_SONNET + output_tokens * _PRICE_OUTPUT_SONNET) / 1_000_000
    return {"count": n, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}


def _run_cost_usd(run: dict | None) -> float:
    """Compute real cost of a stored run using the correct model pricing."""
    if not run or not run.get("input_tokens"):
        return 0.0
    i, o = run["input_tokens"], run["output_tokens"]
    if run.get("operation") == "classify":
        return (i * _PRICE_INPUT_HAIKU + o * _PRICE_OUTPUT_HAIKU) / 1_000_000
    return (i * _PRICE_INPUT_SONNET + o * _PRICE_OUTPUT_SONNET) / 1_000_000


def _pipeline_ctx(conn) -> dict:
    """Collect all pipeline state needed to render the pipeline panel."""
    fetch_stats = get_today_fetch_stats(conn)
    unscored = get_unscored_articles(conn)
    eligible = get_subscore_eligible(conn)
    unsummarized = get_top_unsummarized(conn, n=5)
    last_classify = get_last_run_by_op(conn, "classify")
    last_subscore = get_last_run_by_op(conn, "subscore")
    last_summarize = get_last_run_by_op(conn, "summarize")
    interest_profile = get_setting(conn, "interest_profile", DEFAULT_INTEREST_PROFILE)
    return {
        "fetch_stats": fetch_stats,
        "classify_estimate": _estimate_classification_cost(unscored),
        "subscore_estimate": _estimate_subscore_cost(eligible),
        "summary_estimate": _estimate_summary_cost(unsummarized),
        "last_classify": last_classify,
        "last_subscore": last_subscore,
        "last_summarize": last_summarize,
        "interest_profile": interest_profile,
    }


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="arXiv Indexor")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Shared task state (single background task at a time)
_state: dict = {"running": False, "operation": "", "step": "", "processed": 0, "total": 0,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None, "done": False}


def _ctx(request: Request, tab: str, **kwargs):
    return {"request": request, "tab": tab, **kwargs}


def _page_urls(path: str, page: int, total_pages: int, **params) -> dict:
    base_params = {k: v for k, v in params.items() if v not in (None, "")}

    def make_url(target_page: int) -> str:
        query = urlencode({**base_params, "page": target_page})
        return f"{path}?{query}" if query else path

    return {
        "page": page,
        "total_pages": total_pages,
        "prev_url": make_url(page - 1) if page > 1 else None,
        "next_url": make_url(page + 1) if page < total_pages else None,
    }


def _run_index():
    _state.update({"running": True, "operation": "fetch", "step": "Buscando feeds RSS...",
                   "processed": 0, "total": 0, "error": None, "done": False})
    try:
        init_db()
        count, _ = fetch_articles()
        _state.update({"running": False, "done": True, "step": f"{count} novos artigos indexados"})
    except Exception as e:
        _state.update({"running": False, "error": str(e), "step": "Erro"})


def _run_classify():
    _state.update({"running": True, "operation": "classify", "step": "Iniciando classificacao...",
                   "processed": 0, "total": 0, "input_tokens": 0, "output_tokens": 0,
                   "cost_usd": 0.0, "error": None, "done": False})
    conn = get_conn()
    run_id = insert_run(conn, operation="classify")
    conn.close()
    try:
        init_db()
        conn2 = get_conn()
        total = len(get_unscored_articles(conn2))
        conn2.close()
        _state["total"] = total

        def on_progress(**kwargs):
            step = kwargs.pop("step", "scoring")
            labels = {"scoring": "Classificando artigos...", "subscoring": "Refinando scores (9.x)..."}
            _state.update({"step": labels.get(step, step), **kwargs})

        classified, input_tokens, output_tokens = classify_articles(progress_cb=on_progress)
        cost = (input_tokens * _PRICE_INPUT_HAIKU + output_tokens * _PRICE_OUTPUT_HAIKU) / 1_000_000
        _state.update({"running": False, "done": True, "step": f"Concluido — ${cost:.4f} gastos"})

        conn3 = get_conn()
        finish_run(conn3, run_id, "success", 0, classified, input_tokens=input_tokens, output_tokens=output_tokens)
        conn3.close()
    except Exception as e:
        _state.update({"running": False, "error": str(e), "step": "Erro"})
        conn4 = get_conn()
        finish_run(conn4, run_id, "error", 0, 0, str(e))
        conn4.close()


def _run_subscore():
    _state.update({"running": True, "operation": "subscore", "step": "Sub-classificando (9.0–9.9)...",
                   "processed": 0, "total": 0, "input_tokens": 0, "output_tokens": 0,
                   "cost_usd": 0.0, "error": None, "done": False})
    conn = get_conn()
    run_id = insert_run(conn, operation="subscore")
    conn.close()
    try:
        def on_progress(**kwargs):
            _state.update({"step": "Sub-classificando (9.0–9.9)...", **kwargs})

        subscored, input_tokens, output_tokens = subscore_articles(progress_cb=on_progress)
        cost = (input_tokens * _PRICE_INPUT_SONNET + output_tokens * _PRICE_OUTPUT_SONNET) / 1_000_000
        _state.update({"running": False, "done": True,
                        "step": f"{subscored} artigos sub-classificados — ${cost:.4f} gastos"})

        conn2 = get_conn()
        finish_run(conn2, run_id, "success", 0, subscored, input_tokens=input_tokens, output_tokens=output_tokens)
        conn2.close()
    except Exception as e:
        _state.update({"running": False, "error": str(e), "step": "Erro"})
        conn3 = get_conn()
        finish_run(conn3, run_id, "error", 0, 0, str(e))
        conn3.close()


def _run_summarize():
    _state.update({"running": True, "operation": "summarize", "step": "Gerando resumos...",
                   "processed": 0, "total": 0, "input_tokens": 0, "output_tokens": 0,
                   "cost_usd": 0.0, "error": None, "done": False})
    conn = get_conn()
    run_id = insert_run(conn, operation="summarize")
    conn.close()
    try:
        def on_progress(**kwargs):
            _state.update({"step": "Gerando resumos...", **kwargs})

        summarized, input_tokens, output_tokens = summarize_top_articles(n=5, progress_cb=on_progress)
        cost = (input_tokens * _PRICE_INPUT_SONNET + output_tokens * _PRICE_OUTPUT_SONNET) / 1_000_000
        _state.update({"running": False, "done": True,
                        "step": f"{summarized} resumos gerados — ${cost:.4f} gastos"})

        conn2 = get_conn()
        finish_run(conn2, run_id, "success", 0, summarized, input_tokens=input_tokens, output_tokens=output_tokens)
        conn2.close()
    except Exception as e:
        _state.update({"running": False, "error": str(e), "step": "Erro"})
        conn3 = get_conn()
        finish_run(conn3, run_id, "error", 0, 0, str(e))
        conn3.close()


@app.get("/progress")
def get_progress():
    return _state.copy()


@app.post("/fetch")
def trigger_fetch(background_tasks: BackgroundTasks):
    if not _state["running"]:
        background_tasks.add_task(_run_index)
    return RedirectResponse("/", status_code=303)


@app.post("/classify")
def trigger_classify(background_tasks: BackgroundTasks):
    if not _state["running"]:
        background_tasks.add_task(_run_classify)
    return RedirectResponse("/", status_code=303)


@app.post("/subscore")
def trigger_subscore(background_tasks: BackgroundTasks):
    if not _state["running"]:
        background_tasks.add_task(_run_subscore)
    return RedirectResponse("/history", status_code=303)


@app.post("/summarize")
def trigger_summarize(background_tasks: BackgroundTasks):
    if not _state["running"]:
        background_tasks.add_task(_run_summarize)
    return RedirectResponse("/history", status_code=303)


@app.post("/config/profile")
async def save_profile(request: Request):
    form = await request.form()
    profile = (form.get("profile") or "").strip() # type: ignore
    if profile:
        conn = get_conn()
        set_setting(conn, "interest_profile", profile)
        clear_all_scores(conn)
        conn.close()
    return RedirectResponse("/status", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, page: int = Query(default=1, ge=1)):
    per_page = 20
    conn = get_conn()
    today, total_pages = get_today_new_articles_page(conn, page=page, per_page=per_page)
    current_page = min(page, total_pages)
    last_run = get_last_run(conn)
    pipeline = _pipeline_ctx(conn)
    conn.close()
    return templates.TemplateResponse("index.html", _ctx(
        request, "today",
        articles=today,
        last_run=last_run,
        last_run_cost=_run_cost_usd(last_run),
        pagination=_page_urls("/", page=current_page, total_pages=total_pages),
        **pipeline,
    ))


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, page: int = Query(default=1, ge=1), q: str = Query(default="")):
    per_page = 20
    conn = get_conn()
    articles, total_pages = search_ranked_articles_page(conn, query=q, page=page, per_page=per_page)
    current_page = min(page, total_pages)
    last_run = get_last_run(conn)
    pipeline = _pipeline_ctx(conn)
    conn.close()
    return templates.TemplateResponse("index.html", _ctx(
        request, "history",
        articles=articles,
        last_run=last_run,
        last_run_cost=_run_cost_usd(last_run),
        q=q,
        pagination=_page_urls("/history", page=current_page, total_pages=total_pages, q=q),
        **pipeline,
    ))


@app.get("/status", response_class=HTMLResponse)
def status(request: Request):
    conn = get_conn()
    last_run = get_last_run(conn)
    pipeline = _pipeline_ctx(conn)
    conn.close()
    return templates.TemplateResponse("index.html", _ctx(
        request, "status", articles=[], last_run=last_run, last_run_cost=_run_cost_usd(last_run), **pipeline,
    ))
