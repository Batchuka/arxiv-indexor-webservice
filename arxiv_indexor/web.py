from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from arxiv_indexor.db import get_conn, get_today_articles, get_all_articles, get_last_run

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="arXiv Indexor")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = get_conn()
    today = get_today_articles(conn)
    last_run = get_last_run(conn)
    conn.close()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tab": "today",
        "articles": today,
        "last_run": last_run,
    })


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    conn = get_conn()
    articles = get_all_articles(conn, limit=500)
    last_run = get_last_run(conn)
    conn.close()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tab": "history",
        "articles": articles,
        "last_run": last_run,
    })


@app.get("/status", response_class=HTMLResponse)
def status(request: Request):
    conn = get_conn()
    last_run = get_last_run(conn)
    conn.close()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tab": "status",
        "articles": [],
        "last_run": last_run,
    })
