import os
import sys
import re
import unicodedata
from datetime import datetime, timedelta
from src.utils import setup_logging, load_config, get_madrid_now

logger = setup_logging("filters")

# taste_profile.py is the central interest model (scripts/), one level above the
# plans package. Add it to the path so plans scores relevance the same way news
# does. If it can't be imported we fall back to the filters.yml keyword scoring.
_TASTE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _TASTE_DIR not in sys.path:
    sys.path.insert(0, _TASTE_DIR)
try:
    import taste_profile as tp
except Exception as _e:  # pragma: no cover
    tp = None
    logger.warning(f"No se pudo importar taste_profile ({_e}); usando keywords de filters.yml")


def _strip_accents(text: str) -> str:
    """Lowercase + strip accents, so junk matching is accent-insensitive."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip()


# Titles that are scraper garbage, not events: web sections, listicles,
# legal/cookie notices, brand chrome, bare years, etc. Matched on an
# accent-stripped, lowercased title.
_JUNK_EXACT = {
    'cartel', 'fechas', 'fecha', 'descripcion', 'precios', 'precio',
    'horario', 'horarios', 'programa', 'programacion', 'contenido',
    'resumen', 'indice', 'contacto', 'ubicacion', 'direccion', 'telefono',
    'entradas', 'mapa', 'plano', 'newsletter', 'patrocinadores',
    'time out market', 'online privacy notice', 'privacy notice',
    'cookie policy', 'terms of service',
}

_JUNK_PATTERNS = [re.compile(p) for p in [
    r'^cartel(\s+\d{4})?$',
    r'^fechas?(\s+\d{4})?$',
    r'^\d{4}$',                                    # bare year, e.g. "2026"
    r'^(precios?|horarios?|descripcion|programaci[o]n|programa|contenido'
    r'|resumen|indice|contacto|ubicacion|direccion|telefono)$',
    r'privacy notice|cookie policy|cookies|terms of|aviso legal'
    r'|politica de privacidad',
    r'^discover\b',
    r'coolest cities',
    r'time out market',
    # Listicles / SEO round-ups, not a single attendable event
    r'^(los|las)\s+mejores\b',
    r'^las?\s+exposiciones\b',
    r'^los?\s+mercadillos\b',
    r'\bque\s+puedes\s+ver\b',
    r'^planes?\s+para\b',
    r'^que\s+(ver|hacer|visitar)\b',
    r'^\d+\s+(planes|cosas|lugares|sitios|razones|motivos)\b',
    r'^todo\s+(lo\s+que|sobre)\b',
    r'^arte\s+en\s+madrid$',
    r'^guia\s+(de|del|para)\b',
]]


def is_junk_title(title: str) -> bool:
    """True if the title is scraper noise rather than a real event."""
    t = _strip_accents(title)
    if not t or len(t) < 4:
        return True
    if t in _JUNK_EXACT:
        return True
    return any(p.search(t) for p in _JUNK_PATTERNS)


def _parse_display_date(date_str: str):
    """Parse DD/MM/YYYY string to datetime. Returns None if invalid."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), '%d/%m/%Y')
    except ValueError:
        pass
    try:
        return datetime.strptime(date_str.strip()[:10], '%Y-%m-%d')
    except ValueError:
        return None


def _is_within_window(fecha_inicio: str, fecha_fin: str, now: datetime,
                      horizon: datetime, open_ended: bool = False) -> bool:
    """
    Return True if the event is relevant within [now, horizon].
    - No dates at all: include (can't tell)
    - Start only, no end: include if it starts before the horizon. If the start
      is already in the past, keep it if it started within 8 weeks (likely
      still running) or the text signals an open run ("a partir de", "permanente").
    - Ends within window: include
    """
    start = _parse_display_date(fecha_inicio)
    end   = _parse_display_date(fecha_fin)

    if start is None and end is None:
        return True

    if start is None and end is not None:
        return end >= now

    if start is not None and end is None:
        if start > horizon:
            return False
        if start.date() < now.date():
            # Past start with no end: keep if started within 8 weeks or open-ended phrasing.
            return open_ended or (now - start).days <= 56
        return True

    return start <= horizon and end >= now


class EventFilter:
    def __init__(self):
        self.config = load_config('config/filters.yml')
        self.include_high = set(
            k.lower() for k in self.config.get('include_keywords', {}).get('high_priority', [])
        )
        self.include_medium = set(
            k.lower() for k in self.config.get('include_keywords', {}).get('medium_priority', [])
        )
        self.exclude_strict = set(
            k.lower() for k in self.config.get('exclude_keywords', {}).get('strict', [])
        )

    def filter_events(self, events: list) -> list:
        logger.info(f"Filtrando {len(events)} eventos...")

        now     = get_madrid_now().replace(tzinfo=None)
        horizon = now + timedelta(weeks=8)

        filtered = []
        excluded_date = 0
        excluded_kw   = 0
        excluded_junk = 0

        for event in events:
            if is_junk_title(event.get('titulo', '')):
                excluded_junk += 1
                logger.debug(f"  Basura descartada: {event.get('titulo','')}")
                continue

            score = self._calculate_score(event)
            event['_score'] = score
            event['_priority'] = score >= 15

            if not self._should_include_keywords(event, score):
                excluded_kw += 1
                continue

            open_ended = bool(re.search(
                r'a\s+partir\s+de|desde\s+el|permanente|todo\s+el\s+(año|verano)'
                r'|hasta\s+nuevo\s+aviso',
                f"{event.get('titulo','')} {event.get('descripcion','')}".lower()
            ))
            if not _is_within_window(
                event.get('fecha_inicio', ''),
                event.get('fecha_fin', ''),
                now, horizon, open_ended
            ):
                excluded_date += 1
                logger.debug(
                    f"  Fuera de ventana: {event.get('titulo','')} "
                    f"({event.get('fecha_inicio','')} - {event.get('fecha_fin','')})"
                )
                continue

            filtered.append(event)

        filtered.sort(key=lambda x: x['_score'], reverse=True)
        logger.info(
            f"\u2713 {len(filtered)} eventos en las pr\u00f3ximas 4 semanas "
            f"(excluidos: {excluded_junk} basura, {excluded_kw} por keywords, "
            f"{excluded_date} fuera de ventana)"
        )
        return filtered

    def _calculate_score(self, event: dict) -> float:
        """
        Relevance score against the taste profile (falls back to filters.yml
        keywords if taste_profile is unavailable). Stores '_relevance' on the
        event; the returned score adds small free/new tie-breakers for ordering.
        """
        if tp is not None:
            relevance = tp.score_event(
                event.get('titulo', ''),
                event.get('descripcion', ''),
                event.get('categoria', ''),
            )['score']
        else:
            text = (f"{event.get('titulo', '')} {event.get('descripcion', '')} "
                    f"{event.get('categoria', '')}").lower()
            relevance = (sum(4 for kw in self.include_high if kw in text)
                         + sum(2 for kw in self.include_medium if kw in text))

        event['_relevance'] = relevance

        score = relevance
        if event.get('precio', '').lower() in ['gratis', 'free']:
            score += 1
        if event.get('_is_new'):
            score += 2
        return score

    def _min_relevance(self) -> float:
        return tp.PLAN_MIN_SCORE if tp is not None else 3

    def _should_include_keywords(self, event: dict, score: float) -> bool:
        titulo = event.get('titulo', '').lower()
        # Only check the first 300 chars of description: real content, not price
        # tables or logistics sections where "infantil", "musical" etc. can appear
        # incidentally and wrongly exclude legitimate events.
        desc_lead = event.get('descripcion', '')[:300].lower()
        text = titulo + ' ' + desc_lead
        for kw in self.exclude_strict:
            if kw in text:
                return False
        return event.get('_relevance', 0) >= self._min_relevance()
