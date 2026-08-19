# -*- coding: utf-8 -*-
"""
news_aggregator.py — La Guía Ferlín daily news.

Pipeline:
  1. Read RSS feeds (clean source names, correct encoding).
  2. Deduplicate by title.
  3. Drop pure noise (sports/gossip) and cap volume — full coverage of the
     SOURCES list otherwise, no personal-interest topic filtering.
  4. Fetch full article text (BeautifulSoup, not regex) + og:image.
  5. Summarise with Groq (Gemini as fallback) in JSON mode: three
     non-overlapping sections (Qué pasó / Datos / Conclusión) with real
     figures and a takeaway.
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
    # Metal / music
    {"url": "https://www.theprp.com/feed",                                        "name": "The PRP",                    "lang": "en"},
    # General news (English)
    {"url": "https://www.nytimes.com/services/xml/rss/nyt/HomePage.xml",          "name": "New York Times",             "lang": "en"},
    {"url": "https://www.nytimes.com/services/xml/rss/nyt/Science.xml",           "name": "NYT Science",                "lang": "en"},
    {"url": "https://www.nytimes.com/services/xml/rss/nyt/Technology.xml",        "name": "NYT Tech",                   "lang": "en"},
    # General news (Spanish)
    {"url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",   "name": "El País",                    "lang": "es"},
    {"url": "https://feeds.bbci.co.uk/mundo/rss.xml",                             "name": "BBC Mundo",                  "lang": "es"},
    {"url": "https://www.eldiario.es/rss/",                                       "name": "elDiario.es",                "lang": "es"},
    {"url": "https://maldita.es/feed/",                                           "name": "Maldita.es",                 "lang": "es"},
    {"url": "https://www.europapress.es/rss/rss.aspx?ch=66",                      "name": "Europa Press Ciencia",       "lang": "es"},
    {"url": "https://www.europapress.es/rss/rss.aspx?ch=69",                      "name": "Europa Press Tecnología",    "lang": "es"},
    # AI / tech Spanish
    {"url": "https://www.robotitus.com/feed/",                                    "name": "Robotitus",                  "lang": "es"},
    {"url": "https://www.xataka.com/index.xml",                                   "name": "Xataka",                     "lang": "es"},
    {"url": "https://hipertextual.com/feed",                                      "name": "Hipertextual",               "lang": "es"},
    # AI / tech English
    {"url": "https://www.theguardian.com/technology/artificialintelligenceai/rss","name": "The Guardian AI",            "lang": "en"},
    {"url": "https://www.technologyreview.com/feed/",                             "name": "MIT Tech Review",            "lang": "en"},
    {"url": "https://arstechnica.com/feed/",                                      "name": "Ars Technica",               "lang": "en"},
    {"url": "https://www.theverge.com/rss/index.xml",                             "name": "The Verge",                  "lang": "en"},
    {"url": "https://www.wired.com/feed/rss",                                     "name": "Wired",                      "lang": "en"},
    # Science
    {"url": "https://www.iflscience.com/rss/ifls-latest-rss.xml",                 "name": "IFLScience",                 "lang": "en"},
    {"url": "https://futurism.com/feed",                                          "name": "Futurism",                   "lang": "en"},
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
GROQ_MODEL = "openai/gpt-oss-120b"

# If a 429 asks us to wait longer than this, it's the daily budget talking,
# not a per-minute blip. Stop calling and let remaining articles fail
# gracefully instead of burning the rest of the run on doomed retries.
GROQ_LONG_WAIT_SECONDS = 65
_groq_budget_exhausted = False


def _groq_retry_after(resp, attempt):
    ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    if ra:
        try:
            return float(ra) + 0.5
        except ValueError:
            pass
    return min(2.0 * (2 ** (attempt - 1)), 20.0)


def _post_groq(payload, api_key):
    """POST to Groq with 429 backoff honouring Retry-After. Returns the
    response on success, or None (and may set _groq_budget_exhausted)."""
    global _groq_budget_exhausted
    if _groq_budget_exhausted:
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_tries = 4
    for attempt in range(1, max_tries + 1):
        try:
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                  headers=headers, json=payload, timeout=40)
        except Exception as e:
            print(f"  Groq network error: {e}")
            return None

        if resp.status_code == 200:
            return resp

        if resp.status_code == 429:
            wait = _groq_retry_after(resp, attempt)
            if wait > GROQ_LONG_WAIT_SECONDS:
                print(f"  Groq 429 with long wait ({wait:.0f}s) — daily budget exhausted, degrading")
                _groq_budget_exhausted = True
                return None
            if attempt < max_tries:
                print(f"  Groq 429 — retry {attempt}/{max_tries - 1} in {wait:.1f}s")
                time.sleep(wait)
                continue

        print(f"  Groq error {resp.status_code}: {resp.text[:160]}")
        return None
    return None


# ============================================================
# GEMINI FALLBACK — used only when Groq fails (mainly: daily budget spent)
# ============================================================
GEMINI_MODEL = "gemini-3.7-flash"


def _call_gemini(prompt):
    """Ask Gemini for the same JSON summary Groq would have produced.
    Returns the raw JSON text, or None if no key is set or the call fails.
    Gemini's free tier has a much higher daily quota than Groq's, so this
    only needs to cover the days Groq's budget runs out."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={api_key}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "maxOutputTokens": 900,
            "responseMimeType": "application/json",
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=40)
        if resp.status_code != 200:
            print(f"  Gemini error {resp.status_code}: {resp.text[:160]}")
            return None
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"  Gemini error: {e}")
        return None


def summarize_with_groq(article):
    api_key = os.getenv("GROQ_API_KEY", "").strip()  # strip stray spaces/newlines
    if not api_key:
        print("  GROQ_API_KEY not set")
    # Note: no early return here even without a Groq key or with the budget
    # exhausted -- we still want to fetch content and try the Gemini
    # fallback below. _post_groq() itself is a cheap no-op once the budget
    # flag is set, so this doesn't waste time hammering Groq.

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

    try:
        resp = _post_groq(payload, api_key) if api_key else None
        if resp is not None:
            raw = resp.json()["choices"][0]["message"]["content"].strip()
        else:
            print("  Groq unavailable, trying Gemini fallback...")
            raw = _call_gemini(prompt)
            if raw is None:
                return None, image_url
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
        print(f"  Summary/JSON error: {e}")
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
            "que_paso": s["que_paso"],
            "datos": s["datos"],
            "conclusion": s["conclusion"],
        })
    payload = {"generated": datetime.now().isoformat(), "count": len(articles), "articles": articles}
    with open("data/news.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote data/news.json ({len(articles)} articles)")


# ============================================================
# EMAIL — dark design mirroring index.html
# ============================================================
_C = {
    "bg":    "#0e1116",
    "surf":  "#161a21",
    "surf2": "#1d232c",
    "line":  "#2a313d",
    "txt":   "#e8ebf0",
    "dim":   "#9aa5b4",
    "mute":  "#5c6672",
    "sky":   "#38bdf8",
    "red":   "#f87171",
    "amber": "#fbbf24",
    "green": "#34d399",
}


def _summary_html(s):
    datos = "".join(
        f'<li style="margin:0 0 6px 0;line-height:1.55;color:{_C["dim"]};font-size:14px;'
        f'padding-left:16px;position:relative;list-style:none;">'
        f'<span style="position:absolute;left:0;color:{_C["sky"]};font-weight:700;">•</span>'
        f'{html.escape(d)}</li>'
        for d in s["datos"]
    )
    blocks = []
    if s["que_paso"]:
        blocks.append(
            f'<div style="margin-bottom:13px;">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
            f'color:{_C["sky"]};margin-bottom:4px;font-family:Arial,Helvetica,sans-serif;">Qué pasó</div>'
            f'<p style="margin:0;line-height:1.6;color:{_C["dim"]};font-size:14px;">{html.escape(s["que_paso"])}</p></div>'
        )
    if datos:
        blocks.append(
            f'<div style="margin-bottom:13px;">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
            f'color:{_C["mute"]};margin-bottom:5px;font-family:Arial,Helvetica,sans-serif;">Datos</div>'
            f'<ul style="margin:0;padding:0;">{datos}</ul></div>'
        )
    if s["conclusion"]:
        blocks.append(
            f'<div style="background:{_C["surf2"]};border-left:3px solid {_C["amber"]};'
            f'padding:9px 13px;border-radius:0 6px 6px 0;">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
            f'color:{_C["amber"]};margin-bottom:4px;font-family:Arial,Helvetica,sans-serif;">Conclusión</div>'
            f'<p style="margin:0;line-height:1.55;color:{_C["txt"]};font-size:13px;">{html.escape(s["conclusion"])}</p></div>'
        )
    return "".join(blocks)


def generate_email_html(summaries, stats):
    now = datetime.now()
    hub_url = os.getenv("HUB_URL", "")
    date_es = now.strftime("%d/%m/%Y")
    c = _C

    hub_btn = (
        f'<tr><td style="background:{c["surf2"]};padding:12px 32px;text-align:center;border-bottom:1px solid {c["line"]};">'
        f'<a href="{hub_url}" target="_blank" style="display:inline-block;background:{c["sky"]};'
        f'color:{c["bg"]};text-decoration:none;font-size:12px;font-weight:800;letter-spacing:1px;'
        f'text-transform:uppercase;padding:9px 22px;border-radius:6px;font-family:Arial,Helvetica,sans-serif;">'
        f'Ver todo en la web &#8594;</a></td></tr>'
    ) if hub_url else ""

    cards = ""
    for i, item in enumerate(summaries, 1):
        lang = item.get("language", "es")
        lang_bg = c["red"] if lang == "es" else c["sky"]
        lang_label = "ES" if lang == "es" else "EN"
        img = ""
        if item.get("image_url"):
            img = (f'<div style="margin-bottom:14px;border-radius:8px;overflow:hidden;">'
                   f'<img src="{html.escape(item["image_url"])}" alt="" '
                   f'style="width:100%;max-height:220px;object-fit:cover;display:block;" '
                   f'onerror="this.style.display=\'none\'"></div>')
        cards += (
            f'<div style="background:{c["surf"]};border:1px solid {c["line"]};border-radius:10px;'
            f'padding:20px 22px;margin-bottom:14px;">'
            f'<div style="margin-bottom:10px;">'
            f'<span style="font-size:10px;font-weight:700;color:{c["bg"]};background:{lang_bg};'
            f'padding:2px 7px;border-radius:4px;margin-right:6px;">{lang_label}</span>'
            f'<span style="font-size:11px;font-weight:600;color:{c["mute"]};text-transform:uppercase;'
            f'letter-spacing:.8px;margin-left:4px;font-family:Arial,Helvetica,sans-serif;">{html.escape(item["source"])}</span>'
            f'<span style="font-size:11px;color:{c["mute"]};margin-left:6px;font-family:Arial,Helvetica,sans-serif;">#{i}</span>'
            f'</div>'
            f'<h2 style="margin:0 0 13px;font-size:17px;font-weight:700;line-height:1.4;'
            f'color:{c["txt"]};font-family:Georgia,serif;">{html.escape(item["title"])}</h2>'
            f'{img}'
            f'<div style="background:{c["surf2"]};border-radius:8px;padding:14px 16px;margin-bottom:14px;">'
            f'{_summary_html(item["summary"])}</div>'
            f'<a href="{html.escape(item["url"])}" style="display:inline-block;background:{c["surf2"]};'
            f'color:{c["sky"]};text-decoration:none;font-size:12px;font-weight:600;padding:8px 16px;'
            f'border-radius:6px;border:1px solid {c["line"]};text-transform:uppercase;'
            f'letter-spacing:.5px;font-family:Arial,Helvetica,sans-serif;">Leer artículo &#8594;</a>'
            f'</div>'
        )

    no_news = f'<p style="color:{c["mute"]};text-align:center;padding:40px;font-family:Arial,Helvetica,sans-serif;">Sin noticias relevantes hoy.</p>'

    return (
        f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>La Guía Ferlín</title></head>'
        f'<body style="margin:0;padding:0;background:{c["bg"]};font-family:Arial,Helvetica,sans-serif;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{c["bg"]};padding:20px 0;">'
        f'<tr><td align="center">'
        f'<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">'
        # Header
        f'<tr><td style="background:{c["surf"]};padding:28px 32px 22px;border-radius:10px 10px 0 0;border-bottom:1px solid {c["line"]};">'
        f'<div style="border-bottom:1px solid {c["line"]};padding-bottom:14px;margin-bottom:14px;">'
        f'<div style="font-size:11px;font-weight:700;color:{c["sky"]};letter-spacing:2px;text-transform:uppercase;'
        f'margin-bottom:8px;font-family:Arial,Helvetica,sans-serif;">La Guía Ferlín · Noticias</div>'
        f'<div style="font-size:26px;font-weight:800;color:{c["txt"]};font-family:Georgia,serif;">El resumen del día</div>'
        f'</div>'
        f'<div>'
        f'<span style="font-size:13px;color:{c["dim"]};font-family:Arial,Helvetica,sans-serif;">{date_es}</span>'
        f'<span style="font-size:12px;color:{c["sky"]};font-weight:700;float:right;font-family:Arial,Helvetica,sans-serif;">'
        f'{stats["summarized"]} noticias</span>'
        f'</div>'
        f'</td></tr>'
        # Hub button
        f'{hub_btn}'
        # Section label
        f'<tr><td style="background:{c["surf2"]};padding:10px 32px;border-top:1px solid {c["line"]};border-bottom:1px solid {c["line"]};">'
        f'<span style="font-size:11px;font-weight:700;color:{c["sky"]};text-transform:uppercase;'
        f'letter-spacing:1.5px;font-family:Arial,Helvetica,sans-serif;">Lo relevante de hoy</span></td></tr>'
        # Cards
        f'<tr><td style="background:{c["bg"]};padding:16px 16px 8px;">'
        f'{cards if cards else no_news}'
        f'</td></tr>'
        # Footer
        f'<tr><td style="background:{c["surf"]};padding:18px 32px;border-radius:0 0 10px 10px;'
        f'border-top:1px solid {c["line"]};text-align:center;">'
        f'<p style="margin:0;font-size:11px;color:{c["mute"]};font-family:Arial,Helvetica,sans-serif;">'
        f'Generado automáticamente · Groq AI · {now.strftime("%d/%m/%Y %H:%M")}</p>'
        f'</td></tr>'
        f'</table></td></tr></table></body></html>'
    )


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

    print("\n[2] Filtering noise (sports/gossip)...")
    articles = tp.filter_news(articles)
    print(f"Kept {len(articles)} articles")

    print("\n[3] Summarising with Groq...")
    summaries, failed = [], 0
    for i, art in enumerate(articles, 1):
        print(f"\n[{i}/{len(articles)}] {art['title'][:65]}")
        if i > 1:
            time.sleep(1)
        summary, image_url = summarize_with_groq(art)
        if summary:
            summaries.append({
                "title": art["title"], "url": art["url"], "source": art["source"],
                "language": art.get("language", "es"),
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
