from datetime import datetime, timedelta
from src.utils import setup_logging, load_config, get_madrid_now

logger = setup_logging("filters")


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


def _is_within_window(fecha_inicio: str, fecha_fin: str, now: datetime, horizon: datetime) -> bool:
    """
    Return True if the event is relevant within [now, horizon].
    - No dates at all: include (can't tell)
    - Starts before horizon AND (no end OR ends after now): include
    - Ends within window: include
    """
    start = _parse_display_date(fecha_inicio)
    end   = _parse_display_date(fecha_fin)

    if start is None and end is None:
        return True

    if start is None and end is not None:
        return end >= now

    if start is not None and end is None:
        return start <= horizon

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
        horizon = now + timedelta(weeks=4)

        filtered = []
        excluded_date = 0
        excluded_kw   = 0

        for event in events:
            score = self._calculate_score(event)
            event['_score'] = score
            event['_priority'] = score >= 15

            if not self._should_include_keywords(event, score):
                excluded_kw += 1
                continue

            if not _is_within_window(
                event.get('fecha_inicio', ''),
                event.get('fecha_fin', ''),
                now, horizon
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
            f"(excluidos: {excluded_kw} por keywords, {excluded_date} fuera de ventana)"
        )
        return filtered

    def _calculate_score(self, event: dict) -> float:
        text = f"{event.get('titulo', '')} {event.get('descripcion', '')} {event.get('categoria', '')}".lower()
        score = 0
        score += sum(10 for kw in self.include_high if kw in text)
        score += sum(5 for kw in self.include_medium if kw in text)
        if event.get('precio', '').lower() in ['gratis', 'free']:
            score += 3
        if event.get('_is_new'):
            score += 8
        return score

    def _should_include_keywords(self, event: dict, score: float) -> bool:
        text = f"{event.get('titulo', '')} {event.get('descripcion', '')}".lower()
        for kw in self.exclude_strict:
            if kw in text:
                return False
        return score >= 3 and len(event.get('descripcion', '')) >= 20
