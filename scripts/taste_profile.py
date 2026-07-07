# -*- coding: utf-8 -*-
"""
taste_profile.py — Central taste profile for La Guía Ferlín.

Single source of truth for what Fernando cares about. Used to score and
filter news articles by relevance, and (later) to guide plans and concerts.

Named taste_profile (not profile) to avoid clashing with Python's stdlib
`profile` module.

Scoring model:
  - Each TOPIC has a weight and a keyword list.
  - An article's text (title counted twice + description) is matched against
    every topic. Each topic that matches adds its weight ONCE (keyword
    stuffing does not inflate the score).
  - NEGATIVE keywords subtract, so pure sports/gossip/politics noise sinks.
  - The primary topic is the highest-weight topic that matched.
"""

import re
import unicodedata

# ── Topics of genuine interest ──────────────────────────────────────────────
# weight: how much a match matters (5 = core interest, 2 = mild).
TOPICS = {
    "ai": {
        "weight": 5,
        "label": "IA & Agentes",
        "keywords": [
            "artificial intelligence", "inteligencia artificial", " ai ", "ia ",
            "llm", "gpt", "chatgpt", "claude", "gemini", "anthropic", "openai",
            "machine learning", "aprendizaje automatico", "deep learning",
            "red neuronal", "neural network", "ai agent", "agente de ia",
            "agentic", "agentica", "agentes de ia",
            "copilot", "modelo de lenguaje", "language model", "generative",
            "generativa", "generativo", "mistral", "llama", "hugging face",
            "prompt", "fine-tuning", "rag", "transformer",
        ],
    },
    "tech": {
        "weight": 4,
        "label": "Tecnología",
        "keywords": [
            "software", "hardware", "chip", "semiconductor", "nvidia", "cpu",
            "gpu", "quantum", "cuantic", "robot", "robotic", "robotica",
            "startup", "app ", "aplicacion", "gadget", "smartphone", "iphone",
            "android", "ciberseguridad", "cybersecurity", "hack", "malware",
            "programacion", "programming", "developer", "coding", "open source",
            "codigo abierto", "api", "cloud", "data center", "centro de datos",
            "drone", "dron", "wearable", "vr ", "ar ", "realidad virtual",
        ],
    },
    "science": {
        "weight": 4,
        "label": "Ciencia",
        "keywords": [
            "science", "ciencia", "cientific", "scientif", "physics", "fisica",
            "space", "espacio", "nasa", "esa ", "spacex", "astronom", "cosmos",
            "universo", "galaxy", "galaxia", "planet", "planeta", "biolog",
            "genetic", "genetica", "adn", "dna", "study finds", "estudio revela",
            "investigacion", "research", "researchers", "discovery",
            "descubrimiento", "descubren", "fossil", "fosil", "dinosaur",
            "dinosaurio", "climate", "clima", "quimica", "chemistry", "neuro",
            "medicin", "salud", "vacuna", "vaccine",
        ],
    },
    "geek": {
        "weight": 4,
        "label": "Cultura geek",
        "keywords": [
            "marvel", "dc comics", "comic", "comics", "videojuego", "videogame",
            "video game", "gaming", "playstation", "ps5", "xbox", "nintendo",
            "switch", "steam", "juego de mesa", "board game", "rol ", "rpg",
            "star wars", "star trek", "sci-fi", "ciencia ficcion", "anime",
            "manga", "lego", "retro", "80s", "90s", "arcade", "pixel",
            "fantasia", "fantasy", "dungeons", "warhammer",
        ],
    },
    "ops": {
        "weight": 3,
        "label": "Operaciones & negocio",
        "keywords": [
            "operations", "operaciones", "supply chain", "cadena de suministro",
            "logistic", "logistica", "productivity", "productividad",
            "management", "gestion", "consulting", "consultoria", "mckinsey",
            "bcg", "strategy", "estrategia", "workflow", "automatizacion",
            "process", "proceso", "transformation", "transformacion", "layoff",
            "despido", "hiring", "future of work", "futuro del trabajo",
        ],
    },
    "metal": {
        "weight": 3,
        "label": "Metal & Rock",
        "keywords": [
            "metal", "heavy", "thrash", "death metal", "black metal", "doom",
            "hardcore", "punk", "metalcore", "prog ", "rock band", "guitarist",
            "guitarra", "drummer", "new album", "nuevo disco", "nuevo album",
            "tour dates", "gira", "reunion tour", "riff", "headliner",
            "download festival", "hellfest", "resurrection fest",
        ],
    },
    "mystery": {
        "weight": 2,
        "label": "Misterio & curiosidades",
        "keywords": [
            "mystery", "misterio", "misterioso", "occult", "oculto", "unexplained",
            "inexplicable", "extrano", "extrana", "weird", "bizarre", "rare",
            "curios", "secret", "secreto", "enigma", "paranormal", "conspiracy",
            "leyenda", "legend", "ancient", "antiguo", "arqueolog",
        ],
    },
    "spain": {
        "weight": 2,
        "label": "España & Madrid",
        "keywords": [
            "madrid", "espana", "espanol", "spain", "spanish", "cataluna",
            "andalucia", "moncloa", "sanchez", "spaniard",
        ],
    },
}

# ── Noise: things that should sink to the bottom or be dropped ───────────────
# These subtract from the score. Tuned so a strong on-topic article survives
# an incidental match, but pure sport/gossip/partisan noise is filtered out.
NEGATIVE = {
    "sports": {
        "penalty": 6,
        "keywords": [
            "futbol", "football", "soccer", "fifa", "uefa", "laliga", "la liga",
            "champions league", "real madrid", "barcelona", "atletico",
            "baloncesto", "basketball", "nba", "tenis", "tennis", "golf",
            "motogp", "formula 1", "formula uno", "boxeo", "boxing", "ufc",
            "olimpic", "olympic", "mundial de", "world cup", "premier league",
            "goleador", "gol de", "penalti", "penalty kick",
        ],
    },
    "gossip": {
        "penalty": 6,
        "keywords": [
            "celebrity", "celebridad", "famoso", "famosa", "royal", "realeza",
            "kardashian", "influencer", "gossip", "cotilleo", "corazon",
            "reality show", "gran hermano", "operacion triunfo", "horoscopo",
            "horoscope", "zodiac", "signo del zodiaco", "loteria", "lottery",
            "boda de", "divorcio de", "romance", "novia de", "novio de",
        ],
    },
    "partisan": {
        "penalty": 3,
        "keywords": [
            "trump", "biden", "vox", "psoe ", "partido popular", "feijoo",
            "abascal", "campana electoral", "mitin", "encuesta electoral",
            "elecciones", "election poll",
        ],
    },
}

# How many articles to keep at most (fewer, better, cheaper on the AI).
MAX_ARTICLES = 22
# Minimum score to make the cut.
MIN_SCORE = 3

# ── Plans profile ────────────────────────────────────────────────────────────
# Same idea as TOPICS but tuned for Madrid leisure listings (Spanish event text,
# culture-heavy). Used to score/rank the weekly plans so they match Fernando's
# interests instead of leaking generic theatre. Keywords are matched
# accent-insensitively; short ones need a full word boundary (see _compile).
PLAN_TOPICS = {
    "geek": {
        "weight": 5,
        "label": "Cultura geek",
        "keywords": [
            "comic", "manga", "marvel", "superheroe", "videojuego", "gaming",
            "arcade", "retro", "vintage", "nostalgia", "80s", "90s", "juego de mesa",
            "juegos de mesa", "rol", "lego", "star wars", "anime", "friki",
            "cosplay", "fantasia", "ciencia ficcion", "pixel",
        ],
    },
    "ai_tech": {
        "weight": 5,
        "label": "IA & tecnología",
        "keywords": [
            "inteligencia artificial", "robot", "robotica", "tecnolog",
            "realidad virtual", "impresion 3d", "innovacion tecnologica",
            "digital",
        ],
    },
    "science": {
        "weight": 4,
        "label": "Ciencia",
        "keywords": [
            "ciencia", "cientific", "astronom", "espacio", "planetario",
            "dinosaurio", "fisica", "biolog", "quimica", "naturaleza",
            "universo", "cosmos", "geolog", "evolucion", "microscop",
        ],
    },
    "art": {
        "weight": 4,
        "label": "Arte & diseño",
        "keywords": [
            "arte", "exposicion", "museo", "pintura", "escultura", "fotografia",
            "ilustracion", "galeria", "diseno", "arquitectura", "instalacion",
            "grabado", "acuarela", "artistic",
        ],
    },
    "history": {
        "weight": 3,
        "label": "Historia",
        "keywords": [
            "historia", "historic", "arqueolog", "patrimonio", "medieval",
            "romano", "antiguo", "siglo", "imperio", "prehistor",
        ],
    },
    "mystery": {
        "weight": 3,
        "label": "Misterio & curiosidades",
        "keywords": [
            "misterio", "secreto", "oculto", "enigma", "curiosidad", "insolito",
            "inmersiv", "escape room", "paranormal", "leyenda", "crimen",
        ],
    },
    "cinema": {
        "weight": 3,
        "label": "Cine",
        "keywords": [
            "cine", "pelicula", "film", "documental", "proyeccion",
            "cortometraje", "filmoteca", "estreno",
        ],
    },
    "gastronomy": {
        "weight": 2,
        "label": "Gastronomía",
        "keywords": [
            "gastronom", "cocina", "gourmet", "tapas", "degustacion", "chef",
            "mercado gastronomico",
        ],
    },
}

# A plan needs to touch at least one interest to survive (gastronomy alone = 2).
PLAN_MIN_SCORE = 2

# What Fernando is about, in prose — injected into the AI system prompt so
# summaries are written for him, not for a generic reader.
READER_CONTEXT = (
    "The reader is an operations manager and AI builder. He cares about "
    "artificial intelligence and agents, technology, science, business and "
    "operations, geek culture (comics, video games, board games, sci-fi), "
    "metal and rock music, and Spain/Madrid. He values substance, hard data, "
    "and the practical takeaway. He dislikes fluff, hype and vague language."
)


def _norm(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace — for robust matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compile(keywords) -> "re.Pattern":
    """
    Build one word-boundary regex from a keyword list.
    - Long keywords match as a word prefix ("comic" -> "comics", "astronom"
      -> "astronomia"), so stems work.
    - Short keywords (<=3 chars, e.g. "ai", "ia", "gpt") require a full word
      boundary on both sides, so they don't match inside longer words
      ("ia" must not match "andalucia").
    """
    parts = []
    for kw in keywords:
        k = _norm(kw)
        if not k:
            continue
        esc = re.escape(k)
        if len(k) <= 3:
            parts.append(rf"\b{esc}\b")
        else:
            parts.append(rf"\b{esc}")
    return re.compile("|".join(parts)) if parts else re.compile(r"(?!x)x")


# Pre-compile every pattern once at import time.
for _cfg in TOPICS.values():
    _cfg["_re"] = _compile(_cfg["keywords"])
for _cfg in NEGATIVE.values():
    _cfg["_re"] = _compile(_cfg["keywords"])
for _cfg in PLAN_TOPICS.values():
    _cfg["_re"] = _compile(_cfg["keywords"])


def score_article(title: str, description: str = "") -> dict:
    """
    Return {score, topic, label, matched} for an article.
    topic/label are the primary (highest-weight) interest that matched.

    Scoring is title-dominant: the title (the real topical signal) plus a
    short lead of the description. Full-text RSS bodies are NOT scored, or a
    long political article would incidentally match half the topics.
    """
    lead = (description or "")[:200]
    haystack = " " + _norm(title) + " " + _norm(title) + " " + _norm(lead) + " "

    score = 0
    best_topic = None
    best_weight = -1
    matched = []

    for topic, cfg in TOPICS.items():
        if cfg["_re"].search(haystack):
            score += cfg["weight"]
            matched.append(topic)
            if cfg["weight"] > best_weight:
                best_weight = cfg["weight"]
                best_topic = topic

    for _, cfg in NEGATIVE.items():
        if cfg["_re"].search(haystack):
            score -= cfg["penalty"]

    label = TOPICS[best_topic]["label"] if best_topic else ""
    return {"score": score, "topic": best_topic or "", "label": label, "matched": matched}


def rank_and_filter(articles: list) -> list:
    """
    Attach relevance to each article, drop the noise, sort best-first,
    and cap at MAX_ARTICLES. Each article dict must have 'title' and may
    have 'description'. Adds 'score', 'topic', 'topic_label'.
    """
    scored = []
    for a in articles:
        r = score_article(a.get("title", ""), a.get("description", ""))
        a["score"] = r["score"]
        a["topic"] = r["topic"]
        a["topic_label"] = r["label"]
        if r["score"] >= MIN_SCORE:
            scored.append(a)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:MAX_ARTICLES]


# ── Plans scoring ────────────────────────────────────────────────────────────
def score_event(title: str, description: str = "", category: str = "") -> dict:
    """
    Relevance of a Madrid plan against PLAN_TOPICS. Same title-dominant model as
    score_article: the title counts twice, plus the category and a short lead of
    the description. Each interest topic that matches adds its weight once.
    Returns {score, topic, label}.
    """
    lead = (description or "")[:300]
    haystack = (" " + _norm(title) + " " + _norm(title) + " " +
                _norm(category) + " " + _norm(lead) + " ")

    score = 0
    best_topic = None
    best_weight = -1
    for topic, cfg in PLAN_TOPICS.items():
        if cfg["_re"].search(haystack):
            score += cfg["weight"]
            if cfg["weight"] > best_weight:
                best_weight = cfg["weight"]
                best_topic = topic

    label = PLAN_TOPICS[best_topic]["label"] if best_topic else ""
    return {"score": score, "topic": best_topic or "", "label": label}


def rank_plans(events: list) -> list:
    """
    Attach relevance to each plan, drop those below PLAN_MIN_SCORE, and sort
    best-first. Each event must have 'titulo'/'descripcion'/'categoria'. Adds
    '_relevance', '_topic', '_topic_label'. Mirrors rank_and_filter for news.
    """
    kept = []
    for e in events:
        r = score_event(e.get("titulo", ""), e.get("descripcion", ""),
                         e.get("categoria", ""))
        e["_relevance"]   = r["score"]
        e["_topic"]       = r["topic"]
        e["_topic_label"] = r["label"]
        if r["score"] >= PLAN_MIN_SCORE:
            kept.append(e)
    kept.sort(key=lambda x: x["_relevance"], reverse=True)
    return kept
