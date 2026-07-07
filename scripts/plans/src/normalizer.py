import hashlib
import re
from datetime import datetime
from src.utils import setup_logging, clean_text, MADRID_TZ, get_madrid_now

logger = setup_logging("normalizer")

_MONTHS_ES = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12',
}
_MONTHS_RE = ('(enero|febrero|marzo|abril|mayo|junio|julio|agosto'
              '|septiembre|octubre|noviembre|diciembre)')


def extract_end_date(text: str) -> str:
    """
    Pull an end date (DD/MM/YYYY) from free text when the scraper missed it.
    Looks for the closing side of a range: "hasta el X de MES [de YYYY]"
    or "... al X de MES [de YYYY]". Returns '' if none found.
    """
    if not text:
        return ''
    current_year = get_madrid_now().strftime('%Y')
    m = re.search(
        rf'\b(?:hasta\s+el?|al)\s+(\d{{1,2}})\s+de\s+{_MONTHS_RE}(?:\s+de\s+(\d{{4}}))?',
        text, re.IGNORECASE)
    if m:
        day, mon, yr = m.groups()
        yr = yr or current_year
        return f"{int(day):02d}/{_MONTHS_ES.get(mon.lower(), '01')}/{yr}"
    return ''


def _parse_display_date(date_str: str):
    """Parse DD/MM/YYYY (or ISO) to datetime. Returns None if invalid."""
    if not date_str:
        return None
    for fmt in ('%d/%m/%Y',):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            pass
    try:
        return datetime.strptime(date_str.strip()[:10], '%Y-%m-%d')
    except ValueError:
        return None

FUENTE_MAP = {
    'HoyMadrid': 'HoyMadrid',
    'EsMadrid Exposiciones': 'EsMadrid',
    'EsMadrid Eventos': 'EsMadrid',
    'EsMadrid Ferias': 'EsMadrid',
    'Madrid Secreto': 'Madrid Secreto',
    'TimeOut Semanal': 'TimeOut',
    'TimeOut Fin de Semana': 'TimeOut',
    'UnBuenDia Gastronomía': 'UnBuenDia',
    'UnBuenDia Culturales': 'UnBuenDia',
    'UnBuenDia Otros': 'UnBuenDia',
}

# Encoding replacement map for garbled UTF-8
ENCODING_FIXES = {
    'Ã¡': '\u00e1', 'Ã©': '\u00e9', 'Ã­': '\u00ed', 'Ã³': '\u00f3', 'Ãº': '\u00fa',
    'Ã ': '\u00e0', 'Ã¨': '\u00e8', 'Ã¬': '\u00ec', 'Ã²': '\u00f2', 'Ã¹': '\u00f9',
    'Ã±': '\u00f1', 'Ã\x81': '\u00c1', 'Ã\x89': '\u00c9', 'Ã\x93': '\u00d3',
    'Ã\x9a': '\u00da', 'Ã\x91': '\u00d1',
    'Â\xbf': '\u00bf', 'Â\xa1': '\u00a1',
    'Ã¼': '\u00fc', 'Ã¶': '\u00f6', 'Ã¤': '\u00e4',
    '\xe2\x80\x9c': '\u201c', '\xe2\x80\x9d': '\u201d',
    '\xe2\x80\x98': '\u2018', '\xe2\x80\x99': '\u2019',
    '\xe2\x80\x93': '\u2013', '\xe2\x80\x94': '\u2014',
    '\xe2\x80\xa6': '\u2026', 'Â\xbb': '\u00bb', 'Â\xab': '\u00ab',
}


def fix_encoding(text: str) -> str:
    """Fix garbled UTF-8 characters."""
    if not text:
        return text
    # Try decode as utf-8 from latin-1 encoding
    try:
        fixed = text.encode('latin-1').decode('utf-8')
        return fixed
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    # Manual replacements
    for bad, good in ENCODING_FIXES.items():
        text = text.replace(bad, good)
    return text


class EventNormalizer:
    def normalize_events(self, events: list) -> list:
        logger.info(f"Normalizando {len(events)} eventos...")
        normalized = []
        for event in events:
            try:
                n = self.normalize_event(event)
                if n:
                    normalized.append(n)
            except Exception as e:
                logger.warning(f"Error normalizando: {e}")
        logger.info(f"✓ {len(normalized)} eventos normalizados")
        return normalized

    def normalize_event(self, event: dict) -> dict:
        titulo = fix_encoding(clean_text(event.get('titulo', '')))
        if not titulo:
            return None

        # Strip any legacy "Categoria: " prefix that might still be in titulo
        categoria = fix_encoding(clean_text(event.get('categoria', 'Evento')))
        for prefix in ['Exposición: ', 'Museo: ', 'Teatro: ', 'Cine: ', 'Taller: ',
                        'Festival: ', 'Experiencia: ', 'Gastronomía: ', 'Ruta: ',
                        'Mercado: ', 'Concierto: ', 'Evento: ']:
            if titulo.startswith(prefix):
                categoria = prefix.rstrip(': ')
                titulo = titulo[len(prefix):]
                break

        precio = fix_encoding(clean_text(event.get('precio', '')))
        if precio and any(w in precio.lower() for w in ['gratis', 'free', 'gratuito']):
            precio = 'Gratis'

        fuente_raw = event.get('fuente', '')
        fuente = FUENTE_MAP.get(fuente_raw, fuente_raw)

        descripcion = fix_encoding(clean_text(event.get('descripcion', '')))
        fecha_inicio = fix_encoding(clean_text(event.get('fecha_inicio', '')))
        fecha_fin = fix_encoding(clean_text(event.get('fecha_fin', '')))

        # Recover a missing end date from the title/description text.
        if not fecha_fin:
            fecha_fin = extract_end_date(f"{titulo}. {descripcion}")

        # Drop events already finished at source (end date strictly in the past).
        end_dt = _parse_display_date(fecha_fin)
        if end_dt is not None and end_dt.date() < get_madrid_now().date():
            logger.debug(f"Descartado (ya terminado {fecha_fin}): {titulo}")
            return None

        normalized = {
            'titulo': titulo,
            'categoria': categoria,
            'descripcion': descripcion,
            'precio': precio,
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'enlace': event.get('enlace', ''),
            'fuente': fuente,
            'url_fuente': event.get('url_fuente', ''),
            '_extracted_at': datetime.now(MADRID_TZ).isoformat(),
        }
        normalized['id'] = self._generate_id(normalized)
        return normalized

    def _generate_id(self, event: dict) -> str:
        text = f"{event['titulo']}|{event.get('enlace', '')}".lower()
        return hashlib.md5(text.encode()).hexdigest()[:16]
