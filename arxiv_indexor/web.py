from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from arxiv_indexor.classifier import INTEREST_PROFILE, classify_articles
from arxiv_indexor.db import get_conn, init_db, get_today_articles, get_all_articles, get_last_run, get_unscored_articles
from arxiv_indexor.feed import fetch_articles

# Pricing for claude-sonnet-4 ($/million tokens)
_PRICE_INPUT = 3.0
_PRICE_OUTPUT = 15.0


def _estimate_classification_cost(articles: list[dict]) -> dict:
    """Estimate Claude API cost to classify a list of unscored articles."""
    n = len(articles)
    if n == 0:
        return {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    # Scoring prompt: fixed overhead per batch + text per article (abstract capped at 500 chars)
    batches = (n + 19) // 20
    scoring_chars = sum(
        len(a.get("id", "")) + len(a.get("title", "")) + len((a.get("abstract") or "")[:500])
        for a in articles
    )
    scoring_input_tokens = batches * 220 + scoring_chars // 4
    scoring_output_tokens = n * 25  # {"id": "...", "score": N} per article

    # Summary prompt: top 5 articles, abstract capped at 800 chars
    top5 = articles[:5]
    summary_chars = sum(
        len(a.get("id", "")) + len(a.get("title", "")) + len((a.get("abstract") or "")[:800])
        for a in top5
    )
    summary_input_tokens = 160 + summary_chars // 4
    summary_output_tokens = 5 * 80  # ~2 sentences each

    total_input = scoring_input_tokens + summary_input_tokens
    total_output = scoring_output_tokens + summary_output_tokens
    cost = (total_input * _PRICE_INPUT + total_output * _PRICE_OUTPUT) / 1_000_000

    return {"count": n, "input_tokens": total_input, "output_tokens": total_output, "cost_usd": cost}

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="arXiv Indexor")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Shared task state (single background task at a time)
_state: dict = {"running": False, "operation": "", "step": "", "processed": 0, "total": 0,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None, "done": False}


def _ctx(request: Request, tab: str, **kwargs):
    return {"request": request, "tab": tab, "interest_profile": INTEREST_PROFILE, **kwargs}


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
    try:
        init_db()
        conn = get_conn()
        total = len(get_unscored_articles(conn))
        conn.close()
        _state["total"] = total

        def on_progress(**kwargs):
            step = kwargs.pop("step", "scoring")
            label = "Gerando resumos..." if step == "summarizing" else f"Classificando artigos..."
            _state.update({"step": label, **kwargs})

        classify_articles(progress_cb=on_progress)
        cost = _state["cost_usd"]
        _state.update({"running": False, "done": True,
                        "step": f"Concluido — ${cost:.4f} gastos"})
    except Exception as e:
        _state.update({"running": False, "error": str(e), "step": "Erro"})


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


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = get_conn()
    today = get_today_articles(conn)
    last_run = get_last_run(conn)
    unscored = get_unscored_articles(conn)
    conn.close()
    estimate = _estimate_classification_cost(unscored)
    return templates.TemplateResponse("index.html", _ctx(
        request, "today", articles=today, last_run=last_run, estimate=estimate,
    ))


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    conn = get_conn()
    articles = get_all_articles(conn, limit=500)
    last_run = get_last_run(conn)
    conn.close()
    return templates.TemplateResponse("index.html", _ctx(
        request, "history", articles=articles, last_run=last_run,
    ))


@app.get("/status", response_class=HTMLResponse)
def status(request: Request):
    conn = get_conn()
    last_run = get_last_run(conn)
    conn.close()
    return templates.TemplateResponse("index.html", _ctx(
        request, "status", articles=[], last_run=last_run,
    ))
