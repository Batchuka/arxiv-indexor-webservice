import sys
from arxiv_indexor.db import init_db, get_conn, get_top_articles, insert_run, finish_run


def cmd_index():
    from arxiv_indexor.feed import fetch_articles

    init_db()
    print("[index] Fetching arXiv RSS feeds (no AI)...")
    count, new_articles = fetch_articles()

    if not new_articles:
        print("[index] No new articles found.")
    else:
        print(f"[index] {count} new articles:\n")
        for a in new_articles:
            print(f"  [{a['category']}] {a['title']}")
            print(f"           {a['link']}")
            print()

    print(f"[index] Done. {count} new articles indexed.")


def cmd_fetch():
    from arxiv_indexor.feed import fetch_articles
    from arxiv_indexor.classifier import classify_articles
    from arxiv_indexor.mailer import send_daily_email

    init_db()
    conn = get_conn()
    run_id = insert_run(conn)

    try:
        print("[fetch] Fetching RSS feeds...")
        fetched, _ = fetch_articles()
        print(f"[fetch] {fetched} new articles fetched")

        print("[classify] Classifying articles with Claude...")
        classified, input_tokens, output_tokens = classify_articles()
        print(f"[classify] {classified} articles classified")
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        print(f"[classify] tokens: {input_tokens} in / {output_tokens} out — ~${cost:.4f} (estimado)")

        top = get_top_articles(conn, n=5)
        if top:
            print(f"[mail] Sending digest with {len(top)} top articles...")
            send_daily_email(top)
        else:
            print("[mail] No top articles to send")

        finish_run(conn, run_id, "success", fetched, classified, input_tokens=input_tokens, output_tokens=output_tokens)
        print("[done] Fetch complete.")

    except Exception as e:
        finish_run(conn, run_id, "error", 0, 0, str(e))
        print(f"[error] {e}")
        raise
    finally:
        conn.close()


def cmd_serve():
    import uvicorn
    from arxiv_indexor import get_settings
    settings = get_settings()
    init_db()
    print(f"[serve] Starting web interface on http://localhost:{settings.web_port}")
    uvicorn.run("arxiv_indexor.web:app", host=settings.web_host, port=settings.web_port, reload=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m arxiv_indexor <command>")
        print("Commands:")
        print("  index   — Fetch new articles only (no AI, no email)")
        print("  fetch   — Fetch, classify and email daily digest")
        print("  serve   — Start web interface")
        sys.exit(1)

    command = sys.argv[1]
    if command == "index":
        cmd_index()
    elif command == "fetch":
        cmd_fetch()
    elif command == "serve":
        cmd_serve()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


main()
