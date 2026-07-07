import os
import json
import time
import requests
from src.utils import setup_logging

logger = setup_logging("ai-enricher")

CATEGORY_EMOJIS = {
    'Exposición':  '🎨',
    'Museo':       '🏛️',
    'Teatro':      '🎭',
    'Cine':        '🎬',
    'Taller':      '🛠️',
    'Festival':    '🎪',
    'Experiencia': '✨',
    'Gastronomía': '🍽️',
    'Ruta':        '🗺️',
    'Mercado':     '🛍️',
    'Concierto':   '🎵',
    'Evento':      '📌',
}

# How many events to score in a single Groq call. Batching cuts the number of
# requests ~Nx, which both speeds the run up and stays under Groq's 30 req/min
# free-tier rate limit that used to leave most events un-enriched.
BATCH_SIZE = 6

# Categories with their defining characteristics — fed to the model as examples
CATEGORY_GUIDE = """
Exposición: muestra de arte, fotografía, ilustración, diseño o ciencia en un museo o galería. Temporal.
Museo: apertura, evento especial o actividad organizada POR un museo (no su colección permanente).
Teatro: obra de teatro, monólogo de comedia, performance escénica, dramaturgia. SOLO si la descripción menciona actores, escenario, obra de teatro.
Cine: proyección de película, ciclo de cine, estreno, documental.
Taller: clase práctica, workshop, curso presencial con participación activa del asistente.
Festival: multievento durante varios días, feria temática, congreso. NO música ni baile.
Experiencia: escape room, visita inmersiva, tour interactivo, instalación participativa.
Gastronomía: cena maridaje, clase de cocina, mercado gourmet, cata de alimentos (NO alcohol).
Ruta: ruta temática guiada por espacios de Madrid, paseo histórico o cultural organizado.
Mercado: mercado de segunda mano, artesanía, antigüedades, libros.
Evento: cualquier otro evento de ocio concreto que no encaje arriba.

IMPORTANTE: Si el evento es en un museo pero es una exposición temporal → usa Exposición, no Museo.
IMPORTANTE: Un artículo sobre "qué ver en un teatro" no es Teatro. Solo es Teatro si el evento ES una obra.
IMPORTANTE: Un concierto, recital, ópera o zarzuela NO es Teatro → is_real_event: false.
"""

# Shared rejection rules, used by both the single and the batch prompt.
REJECT_RULES = """RECHAZA (is_real_event: false) si es cualquiera de estos casos — sé estricto:
- Artículo de lista genérico: "los mejores parques", "10 cosas que hacer", "qué hacer esta semana"
- Columna o serie de artículos de alguien: "artículos de X", "reportero de Y", "columna de"
- Contenido infantil o para niños: parques infantiles, ludotecas, talleres para niños, cuentacuentos, campamentos, aula de verano, guardería
- Colección permanente de museo (no una exposición temporal). Ejemplo: "La colección Thyssen", "Obras del siglo XIX"
- Sección administrativa de web: visita libre, visita en grupo, datos de interés, información práctica, precios, horarios, cómo llegar
- Pregunta SEO sin evento: "¿cuánto cuesta?", "¿dónde está?", "¿cómo comprar entradas?"
- Eventos de baile, música, concierto, recital, verbena, flashmob, festival de música, ópera, zarzuela, musical
- Corrida de toros, tauromaquia, apuestas, hipódromo, carreras
- Deporte, running, yoga, meditación, spa
- Religioso: iglesias, catedrales, misas, retiros espirituales
- Política, manifestaciones, actos reivindicativos
- Título es un slogan, tagline o descripción de marketing, no el nombre real del evento

ACEPTA solo eventos de ocio adulto concretos, temporales y asistibles."""

# Exact JSON shape we want back for one event.
_JSON_SHAPE = """{
  "is_real_event": true,
  "reject_reason": "",
  "category": "exactamente una de las categorías de la guía",
  "clean_title": "nombre real y concreto del evento, sin taglines ni frases de marketing, máximo 8 palabras",
  "points": [
    "Qué es exactamente (1 frase específica)",
    "Por qué es interesante o qué lo hace especial (1 frase)",
    "Detalle práctico clave: lugar, precio, formato o dato concreto"
  ],
  "why_go": "razón concreta y específica para ir, máximo 12 palabras",
  "fecha_inicio": "DD de MES de YYYY si aparece claramente en el texto, si no cadena vacía",
  "fecha_fin": "DD de MES de YYYY si aparece claramente en el texto, si no cadena vacía"
}"""

# Note: _JSON_SHAPE has literal { } braces, so it must NOT go through
# str.format(). It's appended after formatting in _call_groq().
PROMPT = """Eres un experto en ocio adulto en Madrid. Analiza este contenido scrapeado.

{reject_rules}

{category_guide}

Título: {title}
Descripción scrapeada: {description}
Fuente: {source}

Responde SOLO con este JSON exacto (sin markdown, sin texto extra):
"""

BATCH_PROMPT = """Eres un experto en ocio adulto en Madrid. Analiza estos {n} eventos scrapeados.

{reject_rules}

{category_guide}

EVENTOS:
{events_block}

Responde SOLO con un array JSON de EXACTAMENTE {n} objetos, en el MISMO orden que
los eventos de arriba (el objeto i corresponde al Evento i). Sin markdown, sin
texto extra. Cada objeto tiene esta forma exacta, añadiendo el campo "index":
{{"index": <número de evento>, "is_real_event": true, "reject_reason": "", "category": "...", "clean_title": "...", "points": ["...", "...", "..."], "why_go": "...", "fecha_inicio": "", "fecha_fin": ""}}"""


class AIEnricher:
    # If a 429 asks us to wait longer than this, treat it as the daily budget
    # being spent: stop calling and keep events with defaults (graceful).
    LONG_LIMIT_SECONDS = 65

    def __init__(self):
        self.api_key = os.getenv('GROQ_API_KEY')
        self.enabled = bool(self.api_key)
        self._budget_exhausted = False
        if not self.enabled:
            logger.warning("GROQ_API_KEY no configurada — AI enrichment desactivado")

    def enrich_events(self, events: list) -> list:
        if not self.enabled:
            for e in events:
                cat = e.get('categoria', 'Evento')
                e['emoji']  = CATEGORY_EMOJIS.get(cat, '📌')
                e['points'] = []
                e['why_go'] = ''
            return events

        logger.info(f"AI analizando {len(events)} eventos en lotes de {BATCH_SIZE}...")
        enriched = []
        rejected = 0

        batches = [events[i:i + BATCH_SIZE] for i in range(0, len(events), BATCH_SIZE)]
        for bi, batch in enumerate(batches, 1):
            if self._budget_exhausted:
                # Groq budget spent — keep the rest with defaults, no more calls.
                for event in batch:
                    self._apply_result(event, None)
                    enriched.append(event)
                continue

            logger.info(f"  Lote {bi}/{len(batches)} ({len(batch)} eventos)")
            results = self._call_groq_batch(batch)

            # Fall back to per-event calls if the batch failed or came back
            # with the wrong number of results.
            if results is None or len(results) != len(batch):
                if self._budget_exhausted:
                    results = [None] * len(batch)
                else:
                    if results is not None:
                        logger.warning(
                            f"    Lote devolvió {len(results)}/{len(batch)} — "
                            f"fallback por evento")
                    results = [self._call_groq(e) for e in batch]

            for event, result in zip(batch, results):
                kept, was_rejected = self._apply_result(event, result)
                if was_rejected:
                    rejected += 1
                elif kept:
                    enriched.append(event)

            if bi < len(batches):
                time.sleep(2)  # stay comfortably under 30 req/min

        if self._budget_exhausted:
            logger.warning("Presupuesto de Groq agotado durante el enriquecimiento; "
                           "eventos restantes conservados sin why_go/categoría AI")

        logger.info(f"✓ Resultado AI: {len(enriched)} válidos, {rejected} rechazados")
        return enriched

    def _apply_result(self, event: dict, result) -> tuple:
        """
        Apply one AI result to an event in place.
        Returns (kept, was_rejected):
          - (True, False): enriched, keep it
          - (False, True): AI said not a real event, drop it
          - (True, False) with defaults: API failed, keep with defaults
        """
        if result is None:
            # API failed — keep with defaults so we don't lose events.
            event['emoji']  = CATEGORY_EMOJIS.get(event.get('categoria', 'Evento'), '📌')
            event.setdefault('points', [])
            event.setdefault('why_go', '')
            return True, False

        if not result.get('is_real_event', True):
            logger.info(f"    ✗ Rechazado: {event.get('titulo','')[:50]} "
                        f"({result.get('reject_reason', '')})")
            return False, True

        if result.get('clean_title', '').strip():
            event['titulo'] = result['clean_title'].strip()
        cat = result.get('category', '').strip() or event.get('categoria', 'Evento')
        event['categoria'] = cat
        event['emoji']     = CATEGORY_EMOJIS.get(cat, '📌')

        raw_points = result.get('points', [])
        if isinstance(raw_points, list) and raw_points:
            event['points'] = [str(p).strip() for p in raw_points if str(p).strip()]
        else:
            event['points'] = []

        event['why_go'] = str(result.get('why_go', '')).strip()

        if not event.get('fecha_inicio') and str(result.get('fecha_inicio', '')).strip():
            event['fecha_inicio'] = result['fecha_inicio'].strip()
        if not event.get('fecha_fin') and str(result.get('fecha_fin', '')).strip():
            event['fecha_fin'] = result['fecha_fin'].strip()

        return True, False

    def _call_groq_batch(self, events: list):
        """Score a batch of events in one call. Returns a list of result dicts
        aligned to `events` (index-matched), or None on failure."""
        blocks = []
        for i, e in enumerate(events, 1):
            blocks.append(
                f"### Evento {i}\n"
                f"Título: {e.get('titulo', '')[:200]}\n"
                f"Descripción: {e.get('descripcion', '')[:600]}\n"
                f"Fuente: {e.get('fuente', '')}"
            )
        prompt = BATCH_PROMPT.format(
            n=len(events),
            reject_rules=REJECT_RULES,
            category_guide=CATEGORY_GUIDE,
            events_block="\n\n".join(blocks),
        )
        data = self._post({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": min(2200, 300 * len(events)),
            "temperature": 0.1,
        })
        if data is None:
            return None
        parsed = self._extract_json(data)
        if not isinstance(parsed, list):
            # Some responses wrap the array in a key — try to recover it.
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break
        if not isinstance(parsed, list):
            return None

        # Align by "index" when present, else by order.
        by_index = {}
        for obj in parsed:
            if isinstance(obj, dict) and isinstance(obj.get('index'), int):
                by_index[obj['index']] = obj
        if len(by_index) == len(events):
            return [by_index.get(i) for i in range(1, len(events) + 1)]
        return parsed

    def _call_groq(self, event: dict):
        """Single-event scoring — used as a fallback when a batch fails."""
        prompt = PROMPT.format(
            reject_rules=REJECT_RULES,
            category_guide=CATEGORY_GUIDE,
            title=event.get('titulo', '')[:200],
            description=event.get('descripcion', '')[:800],
            source=event.get('fuente', ''),
        ) + _JSON_SHAPE
        data = self._post({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 450,
            "temperature": 0.1,
        })
        if data is None:
            return None
        parsed = self._extract_json(data)
        return parsed if isinstance(parsed, dict) else None

    def _post(self, payload: dict):
        """POST to Groq with 429 backoff. Returns the message content string
        on success, or None on failure."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = "https://api.groq.com/openai/v1/chat/completions"
        max_tries = 4
        for attempt in range(1, max_tries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=45)
            except Exception as e:
                logger.warning(f"    Error de red: {e}")
                return None

            if resp.status_code == 200:
                try:
                    return resp.json()['choices'][0]['message']['content'].strip()
                except Exception as e:
                    logger.warning(f"    Respuesta inesperada: {e}")
                    return None

            if resp.status_code == 429:
                wait = self._retry_after(resp, attempt)
                # A long wait means the daily budget is spent (not a per-minute
                # blip). Don't burn the run retrying — degrade gracefully.
                if wait > self.LONG_LIMIT_SECONDS:
                    logger.warning(f"    Groq 429 con espera larga ({wait:.0f}s) — "
                                   f"presupuesto agotado, se degrada")
                    self._budget_exhausted = True
                    return None
                if attempt < max_tries:
                    logger.warning(f"    Groq 429 — reintento {attempt}/{max_tries - 1} "
                                   f"en {wait:.1f}s")
                    time.sleep(wait)
                    continue

            logger.warning(f"    Groq {resp.status_code}: {resp.text[:200]}")
            return None
        return None

    @staticmethod
    def _retry_after(resp, attempt: int) -> float:
        """Seconds to wait after a 429 — honour Retry-After, else exp. backoff."""
        ra = resp.headers.get('retry-after') or resp.headers.get('Retry-After')
        if ra:
            try:
                return float(ra) + 0.5
            except ValueError:
                pass
        return min(2.0 * (2 ** (attempt - 1)), 20.0)

    @staticmethod
    def _extract_json(text: str):
        """Strip markdown fences and parse JSON. Returns obj or None."""
        if not text:
            return None
        text = text.replace('```json', '').replace('```', '').strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Best effort: grab the first [...] array or {...} object.
            import re
            for pattern in (r'\[.*\]', r'\{.*\}'):
                m = re.search(pattern, text, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except json.JSONDecodeError:
                        continue
        return None
