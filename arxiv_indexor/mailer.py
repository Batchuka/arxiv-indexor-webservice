import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from arxiv_indexor import get_settings


def send_daily_email(articles: list[dict]):
    settings = get_settings()
    host = settings.smtp_host
    port = settings.smtp_port
    user = settings.smtp_user
    password = settings.smtp_pass
    to_addr = settings.email_to

    if not user or not password:
        print("[mailer] SMTP_USER/SMTP_PASS not configured, skipping email.")
        return

    html = _build_html(articles)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"arXiv Daily Digest — Top {len(articles)} artigos"
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, to_addr, msg.as_string())

    print(f"[mailer] Email sent to {to_addr}")


def _build_html(articles: list[dict]) -> str:
    rows = ""
    for i, a in enumerate(articles, 1):
        score = a.get("score", "?")
        summary = a.get("summary") or "Sem resumo"
        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee;vertical-align:top;color:#666;">{i}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">
                <a href="{a['link']}" style="color:#1a0dab;font-weight:bold;">{a['title']}</a>
                <br><span style="color:#666;font-size:0.9em;">{a.get('authors', '')}</span>
                <br><span style="color:#888;font-size:0.85em;">Score: {score}/10 | {a.get('category', '')}</span>
                <br><span style="font-size:0.9em;margin-top:4px;display:inline-block;">{summary}</span>
            </td>
        </tr>"""

    return f"""
    <html>
    <body style="font-family:system-ui,sans-serif;max-width:700px;margin:0 auto;">
        <h2 style="color:#333;">arXiv Daily Digest</h2>
        <table style="width:100%;border-collapse:collapse;">
            {rows}
        </table>
        <p style="color:#999;font-size:0.8em;margin-top:20px;">
            Gerado automaticamente pelo arxiv-indexor
        </p>
    </body>
    </html>"""
