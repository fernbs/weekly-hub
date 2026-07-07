from rapidfuzz import fuzz
from src.utils import setup_logging, load_json, save_json

logger = setup_logging("deduplicator")

CATEGORY_PREFIXES = [
    'exposición:', 'museo:', 'evento:', 'teatro:', 'cine:', 'taller:',
    'festival:', 'experiencia:', 'gastronomía:', 'ruta:', 'mercado:', 'concierto:'
]


def _clean_title(title: str) -> str:
    t = title.lower()
    for prefix in CATEGORY_PREFIXES:
        t = t.replace(prefix, '')
    return t.strip()


def _completeness_score(event: dict) -> int:
    """Score an event by how much data it has — used to pick the best duplicate."""
    score = 0
    score += len(event.get('descripcion', ''))          # longer description = better
    if event.get('precio'):
        score += 50
    if event.get('fecha_inicio'):
        score += 100
    if event.get('fecha_fin'):
        score += 100
    if event.get('enlace'):
        score += 30
    return score


class EventDeduplicator:
    def __init__(self):
        self.history_file = "data/events.json"
        self.history = load_json(self.history_file)
        if isinstance(self.history, list):
            self.history = {e.get('id', str(i)): e for i, e in enumerate(self.history)}

    def deduplicate(self, events: list) -> list:
        logger.info(f"Deduplicando {len(events)} eventos...")
        deduplicated = []
        seen_ids = set()
        # Map clean_title -> index in deduplicated list (for replacing with better version)
        seen_titles = {}
        new_count = 0
        ongoing_count = 0
        duplicates_count = 0

        for event in events:
            event_id = event.get('id')

            # Skip exact ID duplicate
            if event_id in seen_ids:
                duplicates_count += 1
                continue

            titulo_clean = _clean_title(event.get('titulo', ''))

            # Check for fuzzy duplicate within this batch
            dup_key = None
            for seen_title in seen_titles:
                is_fuzzy = fuzz.ratio(titulo_clean, seen_title) >= 85
                is_substring = (
                    len(titulo_clean) > 8 and len(seen_title) > 8 and
                    (titulo_clean in seen_title or seen_title in titulo_clean)
                )
                if is_fuzzy or is_substring:
                    dup_key = seen_title
                    break

            if dup_key is not None:
                # Keep the most complete version
                existing_idx = seen_titles[dup_key]
                existing = deduplicated[existing_idx]
                if _completeness_score(event) > _completeness_score(existing):
                    # Replace with the better version, preserving status fields
                    event['_is_new'] = existing.get('_is_new', True)
                    event['_status'] = existing.get('_status', 'nuevo')
                    event['_first_seen'] = existing.get('_first_seen', existing.get('_extracted_at'))
                    deduplicated[existing_idx] = event
                duplicates_count += 1
                continue

            # Check against historical data
            is_new, match = self._is_new_event(event)
            if is_new:
                event['_is_new'] = True
                event['_status'] = 'nuevo'
                new_count += 1
            else:
                event['_is_new'] = False
                event['_status'] = 'en_curso'
                ongoing_count += 1
                if match:
                    event['_first_seen'] = match.get('_first_seen', match.get('_extracted_at'))
                    # Prefer current entry's dates/description if more complete
                    if not event.get('fecha_inicio') and match.get('fecha_inicio'):
                        event['fecha_inicio'] = match['fecha_inicio']
                    if not event.get('fecha_fin') and match.get('fecha_fin'):
                        event['fecha_fin'] = match['fecha_fin']

            if '_first_seen' not in event:
                event['_first_seen'] = event.get('_extracted_at')

            seen_titles[titulo_clean] = len(deduplicated)
            deduplicated.append(event)
            seen_ids.add(event_id)

        logger.info(
            f"✓ {new_count} nuevos, {ongoing_count} en curso, {duplicates_count} duplicados eliminados"
        )
        return deduplicated

    def _is_new_event(self, event: dict) -> tuple:
        # 1. By ID
        if event.get('id') in self.history:
            return False, self.history[event['id']]
        # 2. By exact URL
        url = event.get('enlace')
        if url:
            for h in self.history.values():
                if h.get('enlace') == url:
                    return False, h
        # 3. Fuzzy title match
        title_clean = _clean_title(event.get('titulo', ''))
        for h in self.history.values():
            h_clean = _clean_title(h.get('titulo', ''))
            if fuzz.ratio(title_clean, h_clean) >= 90:
                return False, h
            if len(title_clean) > 10 and len(h_clean) > 10:
                if title_clean in h_clean or h_clean in title_clean:
                    return False, h
        return True, None

    def update_history(self, events: list):
        for event in events:
            if event.get('id'):
                self.history[event['id']] = event
        self._cleanup_old_events()
        save_json(self.history, self.history_file)
        logger.info(f"Histórico actualizado: {len(self.history)} eventos")

    def _cleanup_old_events(self):
        from datetime import timedelta
        from src.utils import parse_date, get_madrid_now
        now = get_madrid_now()
        cutoff = now - timedelta(days=180)
        old = [
            eid for eid, e in self.history.items()
            if (lambda dt: dt and dt < cutoff)(parse_date(e.get('_extracted_at', '')))
        ]
        for eid in old:
            del self.history[eid]
        if old:
            logger.info(f"Limpiados {len(old)} eventos antiguos")
