# -*- coding: utf-8 -*-
"""
news_aggregator.py — La Guía Ferlín daily news.

Pipeline:
  1. Read RSS feeds (clean source names, correct encoding).
  2. Deduplicate by title.
  3. Score every article against taste_profile and keep only the relevant
     top ones (fewer, better, cheaper on the AI).
  4. Fetch full article text (BeautifulSoup, not regex) + og:image.
  5. Summarise with Groq in JSON mode: three non-overlapping sections
     (Qué pasó / Datos / Conclusión) with real figures and a takeaway.
  6. Export data/news.json for the hub.
  7. Send the daily email.
"""

import feedparser
import requests
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import os
import time
import re
import json
import html
from difflib import SequenceMatcher

import pytz
from bs4 import BeautifulSoup

import taste_profile as tp

# ============================================================
# FEEDS — clean display name + language per source.
# Fixes the old mojibake source names (feed titles were garbled).
# ============================================================
SOURCES = [
    {"url": "https://www.theprp.com/feed",                                   "name": "The PRP",         "lang": "en"},
    {"url": "https://www.nytimes.com/services/xml/rss/nyt/HomePage.xml",     "name": "New York Times",  "lang": "en"},
    {"url": "https://www.robotitus.com/feed/",                               "name": "Robotitus",       "lang": "es"},
    {"url": "https://www.theguardian.com/technology/artificialintelligenceai/rss", "name": "The Guardian AI", "lang": "en"},
    {"url": "https://www.eldiario.es/rss/",                                  "name": "elDiario.es",     "lang": "es"},
    {"url": "https://www.iflscience.com/rss/ifls-latest-rss.xml",            "name": "IFLScience",      "lang": "en"},
    {"url": "https://futurism.com/feed",                                     "name": "Futurism",        "lang": "en"},
    {"url": "https://maldita.es/feed/",                                      "name": "Maldita.es",      "lang": "es"},
    {"url": "https://www.europapress.es/rss/rss.aspx?ch=66",                 "name": "Europa Press Ciencia",    "lang": "es"},
    {"url": "https://www.europapress.es/rss/rss.aspx?ch=69",                 "name": "Europa Press Tecnología", "lang": "es"},
    # Extra tech/science signal aligned with the taste profile:
    {"url": "https://www.technologyreview.com/feed/",                        "name": "MIT Tech Review", "lang": "en"},
    {"url": "https://arstechnica.com/feed/",                                 "name": "Ars Technica",    "lang": "en"},
    {"url": "https://www.xataka.com/index.xml",                              "name": "Xataka",          "lang": "es"},
]

HOURS_BACK = 30  # a little over a day, to absorb feed/publish delays

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


# ============================================================
# UTILITIES
# ============================================================
def clean_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def remove_duplicates(articles):
    unique = []
    for article in articles:
        if not any(similar(article["title"], u["title"]) > 0.72 for u in unique):
            unique.append(article)
    return unique


def _decode(resp):
    """Decode a response body with the right charset (fixes mojibake)."""
    enc = resp.encoding
    if not enc or enc.lower() == "iso-8859-1":
        enc = resp.apparent_encoding or "utf-8"
    try:
        return resp.content.decode(enc, errors="replace")
    except (LookupError, TypeError):
        return resp.content.decode("utf-8", errors="replace")


# ============================================================
# RSS
# ============================================================
def fetch_articles():
    all_articles = []
    cutoff = datetime.utcnow() - timedelta(hours=HOURS_BACK)
    headers = {"User-Agent": UA,
               "Accept": "application/rss+xml, application/xml, text/xml, */*"}

    for src in SOURCES:
        feed_url, source_name, lang = src["url"], src["name"], src["lang"]
        print(f"Fetching: {source_name}")
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                pub_date = None
                if getattr(entry, "published_parsed", None):
                    pub_date = datetime(*entry.published_parsed[:6])
                elif getattr(entry, "updated_parsed", None):
                    pub_date = datetime(*entry.updated_parsed[:6])
                if pub_date and pub_date < cutoff:
                    continue

                description = ""
                if hasattr(entry, "summary"):
                    description = clean_html(entry.summary)
                elif hasattr(entry, "description"):
                    description = clean_html(entry.description)
                elif getattr(entry, "content", None):
                    description = clean_html(entry.content[0].get("value", ""))

                rss_image = ""
                if getattr(entry, "media_thumbnail", None):
                    rss_image = entry.media_thumbnail[0].get("url", "")
                elif getattr(entry, "media_content", None):
                    rss_image = entry.media_content[0].get("url", "")
                elif getattr(entry, "enclosures", None):
                    for enc in entry.enclosures:
                        if enc.get("type", "").startswith("image/"):
                            rss_image = enc.get("href", enc.get("url", ""))
                            break

                title = clean_html(entry.get("title", ""))
                url = entry.get("link", "")
                if title and url:
                    all_articles.append({
                        "title": title,
                        "url": url,
                        "description": description,
                        "source": source_name,
                        "language": lang,
                        "date": pub_date,
                        "rss_image": rss_image,
                    })
        except Exception as e:
            print(f"  Error fetching {source_name}: {e}")

    all_articles.sort(key=lambda x: x["date"] or datetime.min, reverse=True)
    unique = remove_duplicates(all_articles)
    print(f"\nTotal: {len(all_articles)} -> unique: {len(unique)}")
    return unique, len(all_articles)


# ============================================================
# ARTICLE CONTENT + IMAGE
# ============================================================
def fetch_article_content_and_image(url):
    if not url:
        return "", ""
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        resp.raise_for_status()
        text = _decode(resp)
        soup = BeautifulSoup(text, "html.parser")

        image_url = ""
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            image_url = og["content"].strip()

        # Drop boilerplate before pulling paragraphs.
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()

        root = soup.find("article") or soup.find("main") or soup.body or soup
        parts = []
        for p in root.find_all("p"):
            t = clean_html(p.get_text(" ", strip=True))
            # Skip short/boilerplate lines (cookie notices, share prompts...).
            if len(t) > 70 and not re.search(r"(cookie|newsletter|suscr|subscribe|©|\bshare\b)", t, re.I):
                parts.append(t)

        return " ".join(parts[:25])[:4000], image_url
    except Exception as e:
        print(f"  Could not fetch content: {e}")
        return "", ""


def search_fallback(title):
    try:
        q = requests.utils.quote(title + " news")
        resp = requests.get(f"https://html.duckduckgo.com/html/?q={q}",
                            headers={"User-Agent": UA}, timeout=10)
        links = re.findall(r'href="(https?://[^"]+)"', resp.text)
        external = [l for l in links if "duckduckgo" not in l and "duck.co" not in l]
        if external:
            alt, _ = fetch_article_content_and_image(external[0])
            if len(alt) > 200:
                return alt
    except Exception as e:
        print(f"  Fallback failed: {e}")
    return ""


# ============================================================
# GROQ SUMMARY (JSON mode, no fragile label parsing)
# ============================================================
GROQ_MODEL = "llama-3.3-70b-versatile"


def summarize_with_groq(article):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("  GROQ_API_KEY not set")
        return None, ""

    title = article["title"]
    lang = article.get("language", "es")
    print("  Fetching full content...")
    content, image_url = fetch_article_content_and_image(article["url"])
    if not image_url and article.get("rss_image"):
        image_url = article["rss_image"]

    if len(content) < 220:
        print("  Trying fallback search...")
        content = search_fallback(title)

    if len(content) > 220:
        source_text = content
    elif len(article.get("description", "")) > 120:
        source_text = article["description"]
    else:
        print("  Not enough content")
        return None, image_url

    lang_name = "Spanish" if lang == "es" else "English"
    prompt = f"""{tp.READER_CONTEXT}

You are his news analyst. Read the article and produce a briefing as a JSON object with EXACTLY these keys:

{{
  "que_paso": "2-3 sentences: what happened, who is involved, and the context the headline does not give. Never restate the headline.",
  "datos": ["4 to 6 hard facts, one per string: figures, %, dates, names, study results, official statements, before/after numbers, quotes. ONLY evidence. Do NOT narrate or repeat anything from que_paso."],
  "conclusion": "1-2 sentences: why it matters, the implication, or the practical takeaway/learning for the reader. No new facts, no repetition."
}}

Hard rules:
- Write everything in {lang_name} (same language as the article).
- The three sections must NOT overlap. que_paso = the story; datos = only evidence; conclusion = only the meaning.
- If the article genuinely lacks hard numbers, put the most specific concrete details available in "datos" (names, places, dates, amounts). Never leave datos empty.
- Be concrete and factual. No hype, no filler, no vague phrases.
- Finish every sentence. Return ONLY the JSON object.

Article title: {title}

Article content:
{source_text[:3200]}"""

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 900,
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
                            headers=headers, json=payload, timeout=40)
        if resp.status_code != 200:
            print(f"  Groq error {resp.status_code}: {resp.text[:160]}")
            return None, image_url
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        data = json.loads(raw)
        summary = {
            "que_paso": html.unescape(str(data.get("que_paso", "")).strip()),
            "datos": [html.unescape(str(d).strip()) for d in (data.get("datos") or []) if str(d).strip()],
            "conclusion": html.unescape(str(data.get("conclusion", "")).strip()),
        }
        if not summary["que_paso"] and not summary["datos"]:
            print("  Empty summary")
            return None, image_url
        print(f"  OK ({len(summary['datos'])} data points)")
        return summary, image_url
    except Exception as e:
        print(f"  Groq/JSON error: {e}")
        return None, image_url


# ============================================================
# JSON EXPORT
# ============================================================
def export_json(summaries):
    os.makedirs("data", exist_ok=True)
    articles = []
    for item in summaries:
        s = item["summary"]
        articles.append({
            "title": item["title"],
            "url": item["url"],
            "image": item.get("image_url", ""),
            "source": item["source"],
            "language": item.get("language", "es"),
            "topic": item.get("topic", ""),
            "topic_label": item.get("topic_label", ""),
            "que_paso": s["que_paso"],
            "datos": s["datos"],
            "conclusion": s["conclusion"],
        })
    payload = {"generated": datetime.now().isoformat(), "count": len(articles), "articles": articles}
    with open("data/news.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote data/news.json ({len(articles)} articles)")


# ============================================================
# EMAIL
# ============================================================
def _summary_html(s):
    datos = "".join(
        f'<li style="margin:0 0 5px 0;line-height:1.5;color:#333;font-size:14px;">{html.escape(d)}</li>'
        for d in s["datos"]
    )
    blocks = []
    if s["que_paso"]:
        blocks.append(
            '<div style="margin-bottom:12px;">'
            '<span style="font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#c0392b;">Qué pasó</span>'
            f'<p style="margin:3px 0 0;line-height:1.6;color:#333;font-size:14px;">{html.escape(s["que_paso"])}</p></div>'
        )
    if datos:
        blocks.append(
            '<div style="margin-bottom:12px;">'
            '<span style="font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#1a1a2e;">Datos</span>'
            f'<ul style="margin:5px 0 0;padding-left:18px;">{datos}</ul></div>'
        )
    if s["conclusion"]:
        blocks.append(
            '<div style="background:#fdf3f2;border-left:3px solid #c0392b;padding:8px 12px;border-radius:0 3px 3px 0;">'
            '<span style="font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#c0392b;">Conclusión</span>'
            f'<p style="margin:3px 0 0;line-height:1.55;color:#444;font-size:13px;">{html.escape(s["conclusion"])}</p></div>'
        )
    return "".join(blocks)


def generate_email_html(summaries, stats):
    now = datetime.now()
    hub_url = os.getenv("HUB_URL", "")
    date_es = now.strftime("%d/%m/%Y")

    hub_btn = (
        '<tr><td style="background:#0d1020;padding:12px 32px;text-align:center;">'
        f'<a href="{hub_url}" target="_blank" style="display:inline-block;background:#c0392b;'
        'color:#fff;text-decoration:none;font-size:12px;font-weight:800;letter-spacing:1px;'
        'text-transform:uppercase;padding:9px 22px;border-radius:4px;">'
        'Ver todo en la web &#8594;</a></td></tr>'
    ) if hub_url else ""

    cards = ""
    for i, item in enumerate(summaries, 1):
        lang = item.get("language", "es")
        lang_color = "#c0392b" if lang == "es" else "#1a6eb5"
        lang_label = "ES" if lang == "es" else "EN"
        topic = item.get("topic_label", "")
        topic_badge = (f'<span style="font-size:10px;font-weight:700;color:#6d28d9;'
                       f'background:#f3f0ff;padding:2px 7px;border-radius:3px;">{html.escape(topic)}</span>') if topic else ""
        img = ""
        if item.get("image_url"):
            img = (f'<div style="margin-bottom:14px;border-radius:4px;overflow:hidden;">'
                   f'<img src="{html.escape(item["image_url"])}" alt="" '
                   f'style="width:100%;max-height:220px;object-fit:cover;display:block;border-radius:4px;" '
                   f'onerror="this.style.display=\'none\'"></div>')
        cards += f'''
        <div style="background:#fff;border:1px solid #e0e0e0;border-left:4px solid #c0392b;border-radius:4px;padding:22px 24px;margin-bottom:16px;">
            <div style="margin-bottom:10px;">
                <span style="font-size:11px;font-weight:700;color:#fff;background:{lang_color};padding:2px 7px;border-radius:3px;margin-right:6px;">{lang_label}</span>
                {topic_badge}
                <span style="font-size:12px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-left:4px;">{html.escape(item["source"])}</span>
            </div>
            <h2 style="margin:0 0 14px;font-size:17px;font-weight:700;line-height:1.4;color:#1a1a2e;font-family:Georgia,serif;">{html.escape(item["title"])}</h2>
            {img}
            <div style="background:#f9f9f9;border-radius:3px;padding:14px 16px;margin-bottom:14px;">{_summary_html(item["summary"])}</div>
            <a href="{html.escape(item["url"])}" style="display:inline-block;background:#1a1a2e;color:#fff;text-decoration:none;font-size:12px;font-weight:600;padding:8px 16px;border-radius:3px;">Leer artículo &rarr;</a>
        </div>'''

    return f'''<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"><title>La Guía Ferlín</title></head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f0;padding:20px 0;"><tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
  <tr><td style="background:#1a1a2e;padding:34px 32px 26px;border-radius:4px 4px 0 0;">
    <div style="border-bottom:2px solid #c0392b;padding-bottom:14px;margin-bottom:14px;">
      <div style="font-size:11px;font-weight:700;color:#c0392b;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;">La Guía Ferlín · Noticias</div>
      <div style="font-size:27px;font-weight:800;color:#fff;font-family:Georgia,serif;">El resumen del día</div>
    </div>
    <div><span style="font-size:13px;color:#aab2c0;">{date_es}</span>
    <span style="font-size:12px;color:#c0392b;font-weight:600;float:right;">{stats["summarized"]} noticias para ti</span></div>
  </td></tr>
  {hub_btn}
  <tr><td style="background:#c0392b;padding:10px 32px;">
    <span style="font-size:11px;font-weight:700;color:#fff;text-transform:uppercase;letter-spacing:1.5px;">Lo relevante de hoy</span></td></tr>
  <tr><td style="background:#f0f0f0;padding:16px 16px 8px;">
    {cards if cards else '<p style="color:#888;text-align:center;padding:40px;">Sin noticias relevantes hoy.</p>'}
  </td></tr>
  <tr><td style="background:#1a1a2e;padding:20px 32px;border-radius:0 0 4px 4px;text-align:center;">
    <p style="margin:0;font-size:11px;color:#6677aa;">Generado automáticamente · Groq AI · {now.strftime("%d/%m/%Y %H:%M")}</p></td></tr>
</table></td></tr></table></body></html>'''


def send_email(subject, html_content):
    sender = os.getenv("GMAIL_EMAIL")
    password = os.getenv("GMAIL_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL")
    if not all([sender, password, recipient]):
        print("Missing Gmail credentials — skipping email")
        return False
    msg = MIMEMultipart("alternative")
    msg["From"], msg["To"], msg["Subject"] = sender, recipient, subject
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender, password)
            server.send_message(msg)
        print("Email sent")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("LA GUÍA FERLÍN — NEWS")
    print("=" * 60)

    print("\n[1] Reading feeds...")
    articles, total_raw = fetch_articles()
    if not articles:
        print("No articles. Aborting.")
        return

    print("\n[2] Ranking by relevance to profile...")
    articles = tp.rank_and_filter(articles)
    print(f"Kept {len(articles)} relevant articles")

    print("\n[3] Summarising with Groq...")
    summaries, failed = [], 0
    for i, art in enumerate(articles, 1):
        print(f"\n[{i}/{len(articles)}] ({art['score']}) {art['title'][:65]}")
        if i > 1:
            time.sleep(1)
        summary, image_url = summarize_with_groq(art)
        if summary:
            summaries.append({
                "title": art["title"], "url": art["url"], "source": art["source"],
                "language": art.get("language", "es"), "topic": art.get("topic", ""),
                "topic_label": art.get("topic_label", ""),
                "summary": summary, "image_url": image_url,
            })
        else:
            failed += 1

    print(f"\nSummarised {len(summaries)}/{len(articles)} ({failed} failed)")
    if not summaries:
        print("Nothing to publish.")
        return

    stats = {"feeds": len(SOURCES), "total": total_raw, "summarized": len(summaries)}

    print("\n[4] Export JSON...")
    export_json(summaries)

    print("\n[5] Email...")
    subject = f"La Guía Ferlín · {datetime.now().strftime('%d %b %Y')}"
    send_email(subject, generate_email_html(summaries, stats))

    print("\nDONE")


if __name__ == "__main__":
    main()
