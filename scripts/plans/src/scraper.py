import time
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from src.utils import setup_logging, load_config, normalize_url, clean_text

logger = setup_logging("scraper")

MONTHS_ES = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
}

# Titles that are single forbidden words (web sections, not events)
SINGLE_WORD_FORBIDDEN = {
    'entradas', 'horario', 'dirección', 'teléfono', 'tienda', 'ubicación',
    'contacto', 'acceso', 'precios', 'tarifas', 'información', 'reservas',
    'newsletter', 'cafetería', 'restaurante', 'librería', 'shop', 'horarios',
    'taquilla', 'aparcamiento', 'parking', 'mapa', 'plano', 'inicio',
    'cartel', 'fechas', 'fecha', 'descripción', 'descripcion', 'programa',
    'programación', 'contenido', 'resumen', 'patrocinadores',
}

# Patterns indicating permanent museum content (not a temporary event)
PERMANENT_COLLECTION_PATTERNS = [
    r'^la\s+colección$',
    r'^colección\s+permanente',
    r'^colección\s+del\s+museo',
    r'^la\s+colección\s+del',
    r'^obras\s+de\s+la\s+colección',
    r'^colección\s+histórica',
]

# Patterns indicating web sections, not events
SECTION_TITLE_PATTERNS = [
    r'^\¿',
    r'^(cuánto|cuándo|dónde|cómo|qué|quién)',
    r'^\d{1,2}\s+de\s+\w+\s+de\s+\d{4}$',
    r'^(lunes|martes|miércoles|jueves|viernes|sábado|domingo)\s+\d',
    r'^(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)',
    r'descárgate\s+la\s+app',
    r'síguenos\s+en',
    r'newsletter',
    r'^mdr\s+entrada',
    r'^(más\s+información|info|aviso)',
    # Bare years, listicles and English/legal chrome that leak from listings
    r'^\d{4}$',
    r'^cartel(\s+\d{4})?$',
    r'^fechas?(\s+\d{4})?$',
    r'^(los|las)\s+mejores\b',
    r'\bque\s+puedes\s+ver\b',
    r'^planes?\s+para\b',
    r'privacy\s+notice',
    r'cookie',
    r'^discover\b',
    r'coolest\s+cities',
    r'time\s+out\s+market',
]


class EventScraper:
    def __init__(self):
        self.config = load_config('config/sources.yml')
        self.sources = self.config.get('sources', [])
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                          ' (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.all_events = []
        self.failed_sources = []
        self.visited_urls = set()

    def scrape_all(self) -> list:
        logger.info(f"Scraping {len(self.sources)} fuentes...")
        for source in self.sources:
            try:
                events = self.scrape_source(source)
                for event in events:
                    event['fuente'] = source['name']
                    event['url_fuente'] = source['url']
                self.all_events.extend(events)
                logger.info(f"✓ {source['name']}: {len(events)} eventos")
            except Exception as e:
                logger.error(f"✗ {source['name']}: {e}")
                self.failed_sources.append({
                    'name': source['name'],
                    'url': source['url'],
                    'error': str(e)
                })
            time.sleep(2)
        logger.info(f"Total extraído: {len(self.all_events)} eventos")
        return self.all_events

    def scrape_source(self, source: dict) -> list:
        url = source['url']
        source_name = source['name']
        selectors = source.get('selectors', {})

        if source.get('type') == 'dynamic':
            html = self._fetch_dynamic(url)
        else:
            html = self._fetch_static(url)

        if not html:
            return []

        soup = BeautifulSoup(html, 'lxml')
        all_events = []

        # ── Link collection ───────────────────────────────────────────────────
        # Two strategies depending on sources.yml config:
        #
        # 1. link_selector: for sites where event cards ARE <a> elements
        #    (e.g. HoyMadrid, EsMadrid). Collects hrefs directly from the
        #    listing page without looking for container wrappers.
        #
        # 2. container-based: classic approach for WordPress-style sites where
        #    events are wrapped in article/.post elements containing <a> links.

        link_selector = source.get('link_selector')
        max_links = source.get('max_links', 100)

        if link_selector:
            event_links = []
            seen_links = set()
            for link_el in soup.select(link_selector):
                href = normalize_url(link_el.get('href', ''), url)
                if (href and href != url
                        and href not in self.visited_urls
                        and href not in seen_links):
                    seen_links.add(href)
                    event_links.append(href)
            logger.info(f"  Direct links encontrados: {len(event_links)} (link_selector)")
        else:
            container_selector = selectors.get('container', 'article')
            containers = []
            for selector in container_selector.split(','):
                containers.extend(soup.select(selector.strip()))
            logger.info(f"  Encontrados {len(containers)} contenedores")

            event_links = []
            seen_links = set()
            for container in containers:
                for link in container.find_all('a', href=True):
                    href = normalize_url(link['href'], url)
                    if (href and href != url
                            and href not in self.visited_urls
                            and href not in seen_links):
                        seen_links.add(href)
                        event_links.append(href)

        logger.info(f"  Enlaces únicos a visitar: {len(event_links)}")

        for i, link in enumerate(event_links[:max_links], 1):
            if link in self.visited_urls:
                continue
            self.visited_urls.add(link)
            logger.info(f"  [{i}/{min(max_links, len(event_links))}] {link[:80]}")
            try:
                page_events = self._extract_from_page(link, source_name)
                all_events.extend(page_events)
                time.sleep(1.5)
            except Exception as e:
                logger.debug(f"  Error: {e}")

        return all_events

    def _extract_from_page(self, url: str, source_name: str) -> list:
        html = self._fetch_static(url)
        if not html:
            return []
        soup = BeautifulSoup(html, 'lxml')

        structured_start, structured_end = self._extract_dates_from_soup(soup)

        events = self._extract_multiple_events(soup, url, source_name)
        if events and len(events) > 1:
            for e in events:
                if not e.get('fecha_inicio') and structured_start:
                    e['fecha_inicio'] = structured_start
                if not e.get('fecha_fin') and structured_end:
                    e['fecha_fin'] = structured_end
            logger.info(f"  Extraídos {len(events)} eventos del artículo")
            return events

        event = self._extract_single_event(soup, url, source_name)
        if event and event.get('titulo'):
            if not event.get('fecha_inicio') and structured_start:
                event['fecha_inicio'] = structured_start
            if not event.get('fecha_fin') and structured_end:
                event['fecha_fin'] = structured_end
            return [event]

        return []

    def _extract_multiple_events(self, soup, url: str, source_name: str) -> list:
        events = []
        headings = soup.find_all(['h2', 'h3', 'h4'])
        skip_keywords = [
            'resumen', 'índice', 'contenido', 'compartir', 'también',
            'relacionado', 'más información', 'sobre', 'contacto',
            'cómo llegar', 'cómo comprar', 'horarios', 'precios',
            'síguenos', 'redes sociales', 'newsletter',
            'términos', 'condiciones', 'privacidad', 'cookies',
            'cuenta', 'contraseña', 'servicios adicionales'
        ]
        generic_patterns = [
            r'museo.*y.*exposiciones', r'qué\s+(ver|hacer)',
            r'guía\s+de', r'todo\s+sobre', r'conoce\s+el',
            r'^\d+\.', r'^\d+\.\d+'
        ]

        for heading in headings:
            titulo = clean_text(heading.get_text())
            if not titulo or len(titulo) < 5:
                continue
            if any(kw in titulo.lower() for kw in skip_keywords):
                continue
            if any(re.search(p, titulo.lower()) for p in generic_patterns):
                continue
            if not self._is_valid_title(titulo):
                continue

            desc_parts = []
            next_elem = heading.find_next_sibling()
            while next_elem and next_elem.name not in ['h2', 'h3', 'h4']:
                if next_elem.name == 'p':
                    text = clean_text(next_elem.get_text())
                    text = self._fix_encoding(text)
                    if text and len(text) > 20 and not self._is_admin_text(text):
                        desc_parts.append(text)
                    if len(' '.join(desc_parts)) > 300:
                        break
                next_elem = next_elem.find_next_sibling()

            descripcion = self._clean_description_aggressive(' '.join(desc_parts)[:600])
            if descripcion and len(descripcion) >= 50:
                context_text = heading.parent.get_text() if heading.parent else ''
                categoria, titulo_limpio = self._categorize_title(titulo, context_text)
                fecha_inicio, fecha_fin = self._extract_dates(context_text)
                event = {
                    'titulo': titulo_limpio,
                    'categoria': categoria,
                    'descripcion': descripcion,
                    'precio': self._extract_price(context_text),
                    'fecha_inicio': fecha_inicio or '',
                    'fecha_fin': fecha_fin or '',
                    'enlace': url,
                    'fuente': source_name
                }
                if not self._is_legal_content(event['titulo'], event['descripcion']):
                    events.append(event)

        return events

    def _extract_single_event(self, soup, url: str, source_name: str) -> dict:
        title_tag = soup.find('h1') or soup.find(['h2', 'h3'])
        if not title_tag:
            return None

        raw_title = clean_text(title_tag.get_text())
        raw_title = self._fix_encoding(raw_title)

        if not self._is_valid_title(raw_title):
            return None

        forbidden_in_title = [
            'cuánto cuesta', 'precio de entrada', 'horario', 'cómo llegar',
            'cómo comprar', 'dónde comprar', 'comprar entradas', 'cuánto se tarda',
            'cuánto tiempo', 'entradas', 'tienda', 'restaurante del museo',
            'cafetería', 'librería', 'shop', 'cuenta de instagram', 'síguenos',
            'visita nuestra', 'información práctica', 'planifica tu visita',
            'antes de tu visita', 'qué ver en', 'qué hacer en', 'mejores',
            'top ', 'guía de', 'guía del', 'todo sobre', 'historia del museo',
            'el edificio', 'arquitectura del', 'colección permanente',
            'obras maestras', 'obras más famosas',
            'términos', 'condiciones', 'política', 'privacidad', 'cookies',
            'aviso legal', 'propiedad intelectual', 'copyright', 'derechos reservados',
            'acuerdo', 'contrato', 'vinculante', 'parte contratante',
            'limitación de responsabilidad', 'servicios adicionales', 'cupones',
            'vales', 'contraseña', 'seguridad', 'inicio de sesión', 'registro',
            'suscripción', 'newsletter', 'dirección', 'dónde está', 'ubicación',
            'contacto', 'sobre nosotros', 'quiénes somos', 'equipo',
            'descárgate', 'descarga la app', 'descarga gratis',
        ]
        for forbidden in forbidden_in_title:
            if forbidden in raw_title.lower():
                return None

        forbidden_patterns = [
            r'^.*museo.*\s+sus?\s+exposiciones', r'^.*museo.*\s+obras?\s+famosas',
            r'^.*colección.*completa', r'^qué\s+(ver|hacer|visitar)\s+en',
            r'^guía\s+(de|del)', r'^\d+\s+cosas', r'^mejores?\s+', r'^top\s+\d+',
            r'cómo\s+(llegar|comprar|conseguir)', r'dónde\s+comprar',
            r'horarios?\s+(de|del|y)', r'precio\s+(de|del|y)',
            r'^\¿', r'^entradas$', r'^tienda$', r'^cafetería$', r'^restaurante$',
            r'^\d+\.\s', r'^\d+\.\d+\s',
            r'^servicios?\s+', r'^cupones?\s+', r'^política\s+de',
            r'^términos\s+', r'^condiciones\s+', r'^aviso\s+legal',
            r'^(su|tu)\s+uso\s+de', r'^limitación\s+de', r'^acuerdo\s+vinculante',
            r'^parte\s+contratante', r'^dirección$', r'^dónde\s+(está|estamos)',
            r'^ubicación$', r'^contacto$', r'^sobre\s+nosotros', r'^quiénes?\s+somos',
            r'^teléfono$', r'^horario$', r'^mdr\s+entrada',
            r'descárgate', r'descarga\s+(la\s+)?app',
            r'^\w+$',
        ]
        for pattern in forbidden_patterns:
            if re.search(pattern, raw_title.lower()):
                if pattern == r'^\w+$':
                    if raw_title.lower() in SINGLE_WORD_FORBIDDEN:
                        return None
                else:
                    return None

        for pattern in PERMANENT_COLLECTION_PATTERNS:
            if re.search(pattern, raw_title.lower()):
                return None

        full_text = soup.get_text()
        full_text = self._fix_encoding(full_text)
        categoria, titulo_limpio = self._categorize_title(raw_title, full_text)
        fecha_inicio, fecha_fin = self._extract_dates(full_text[:2000])

        # Extended selector list covers WordPress, Drupal, custom CMS structures.
        desc_containers = soup.select(
            'article, .content, .entry-content, .post-content, main, '
            '.field--type-text-with-summary, .field-items, .field-body, '
            '.node-body, .region-content, .view-content, .block-content, '
            '.event-description, .descripcion, .description, section.content'
        )
        desc_parts = []
        for container in desc_containers[:2]:
            for p in container.find_all('p', limit=15):
                text = clean_text(p.get_text())
                text = self._fix_encoding(text)
                if text and len(text) > 30 and not self._is_admin_text(text):
                    desc_parts.append(text)
                if len(' '.join(desc_parts)) > 800:
                    break
            if desc_parts:
                break

        # Fallback: scan all <p> tags in the page when specific containers yield nothing.
        if not desc_parts:
            for p in soup.find_all('p', limit=25):
                text = clean_text(p.get_text())
                text = self._fix_encoding(text)
                if text and len(text) > 60 and not self._is_admin_text(text):
                    desc_parts.append(text)
                if len(' '.join(desc_parts)) > 800:
                    break

        # Last resort: og:description meta tag.
        if not desc_parts:
            og = soup.find('meta', property='og:description') or soup.find('meta', {'name': 'description'})
            if og and og.get('content') and len(og['content']) > 30:
                desc_parts.append(og['content'])

        descripcion = self._clean_description_aggressive(' '.join(desc_parts)[:1200])
        if not descripcion or len(descripcion) < 30:
            return None

        event = {
            'titulo': titulo_limpio,
            'categoria': categoria,
            'descripcion': descripcion,
            'precio': self._extract_price(full_text),
            'fecha_inicio': fecha_inicio or '',
            'fecha_fin': fecha_fin or '',
            'enlace': url,
            'fuente': source_name
        }
        if self._is_legal_content(event['titulo'], event['descripcion']):
            return None
        return event

    def _is_valid_title(self, title: str) -> bool:
        tl = title.lower().strip()
        if tl in SINGLE_WORD_FORBIDDEN:
            return False
        for pattern in SECTION_TITLE_PATTERNS:
            if re.search(pattern, tl, re.IGNORECASE):
                return False
        for pattern in PERMANENT_COLLECTION_PATTERNS:
            if re.search(pattern, tl, re.IGNORECASE):
                return False
        if len(title) < 4:
            return False
        if re.match(r'^\d{1,2}\s+de\s+\w+', tl):
            return False
        return True

    def _fix_encoding(self, text: str) -> str:
        if not text:
            return text
        try:
            return text.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        replacements = {
            'Ã¡': '\u00e1', 'Ã©': '\u00e9', 'Ã­': '\u00ed', 'Ã³': '\u00f3', 'Ãº': '\u00fa',
            'Ã ': '\u00e0', 'Ã¨': '\u00e8', 'Ã¬': '\u00ec', 'Ã²': '\u00f2', 'Ã¹': '\u00f9',
            'Ã±': '\u00f1', 'Ã\x81': '\u00c1', 'Ã\x89': '\u00c9', 'Ã\x8d': '\u00cd',
            'Ã\x93': '\u00d3', 'Ã\x9a': '\u00da', 'Ã\x91': '\u00d1',
            'Â\xbf': '\u00bf', 'Â\xa1': '\u00a1',
            'Ã¼': '\u00fc', 'Ã¶': '\u00f6', 'Ã¤': '\u00e4',
            '\xe2\x80\x9c': '\u201c', '\xe2\x80\x9d': '\u201d',
            '\xe2\x80\x98': '\u2018', '\xe2\x80\x99': '\u2019',
            '\xe2\x80\x93': '\u2013', '\xe2\x80\x94': '\u2014',
            '\xe2\x80\xa6': '\u2026',
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        return text

    def _extract_dates_from_soup(self, soup) -> tuple:
        import json as _json
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = _json.loads(script.string or '')
                if isinstance(data, list):
                    data = data[0]
                start = data.get('startDate') or data.get('datePublished') or data.get('dateCreated')
                end = data.get('endDate')
                if start or end:
                    return self._iso_to_display(start), self._iso_to_display(end)
            except Exception:
                pass

        meta_start = (soup.find('meta', {'property': 'event:start_time'}) or
                      soup.find('meta', {'itemprop': 'startDate'}) or
                      soup.find('meta', {'name': 'startDate'}))
        meta_end   = (soup.find('meta', {'property': 'event:end_time'}) or
                      soup.find('meta', {'itemprop': 'endDate'}) or
                      soup.find('meta', {'name': 'endDate'}))
        if meta_start or meta_end:
            s = meta_start.get('content', '') if meta_start else ''
            e = meta_end.get('content', '') if meta_end else ''
            return self._iso_to_display(s) or None, self._iso_to_display(e) or None

        time_tags = soup.find_all('time', datetime=True)
        if len(time_tags) >= 2:
            return self._iso_to_display(time_tags[0]['datetime']), self._iso_to_display(time_tags[-1]['datetime'])
        elif len(time_tags) == 1:
            return self._iso_to_display(time_tags[0]['datetime']), None

        return None, None

    def _iso_to_display(self, iso: str) -> str:
        if not iso:
            return None
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', iso)
        if m:
            return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
        return None

    def _extract_dates(self, text: str) -> tuple:
        if not text:
            return None, None
        current_year = str(time.strftime('%Y'))
        MONTHS = r'(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)'

        def fmt(d, mon, yr):
            return f"{int(d):02d}/{MONTHS_ES.get(mon.lower(), '01')}/{yr}"

        # "del X de MES [de YEAR] al Y de MES [de YEAR]"
        m = re.search(
            rf'del?\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?\s+al?\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?',
            text, re.IGNORECASE)
        if m:
            d1, m1, y1, d2, m2, y2 = m.groups()
            y1 = y1 or current_year
            y2 = y2 or y1
            return fmt(d1, m1, y1), fmt(d2, m2, y2)

        # "desde el X de MES ... hasta el Y de MES"
        m = re.search(
            rf'desde\s+el?\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?'
            rf'\s+hasta\s+el?\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?',
            text, re.IGNORECASE)
        if m:
            d1, m1, y1, d2, m2, y2 = m.groups()
            y1 = y1 or current_year
            y2 = y2 or y1
            return fmt(d1, m1, y1), fmt(d2, m2, y2)

        # "X de MES al Y de MES"
        m = re.search(
            rf'(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?\s+al?\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?',
            text, re.IGNORECASE)
        if m:
            d1, m1, y1, d2, m2, y2 = m.groups()
            y1 = y1 or current_year
            y2 = y2 or y1
            return fmt(d1, m1, y1), fmt(d2, m2, y2)

        # "hasta el X de MES"
        m = re.search(rf'hasta\s+el?\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?', text, re.IGNORECASE)
        if m:
            d, mon, yr = m.groups()
            yr = yr or current_year
            return None, fmt(d, mon, yr)

        # "a partir del / desde el X de MES"
        m = re.search(
            rf'(?:a\s+partir\s+del?|desde\s+el?)\s+(\d{{1,2}})\s+de\s+{MONTHS}(?:\s+de\s+(\d{{4}}))?',
            text, re.IGNORECASE)
        if m:
            d, mon, yr = m.groups()
            yr = yr or current_year
            return fmt(d, mon, yr), None

        # Standalone "X de MES de YEAR"
        m = re.search(rf'(\d{{1,2}})\s+de\s+{MONTHS}\s+de\s+(\d{{4}})', text, re.IGNORECASE)
        if m:
            d, mon, yr = m.groups()
            return fmt(d, mon, yr), None

        return None, None

    def _clean_description_aggressive(self, description: str) -> str:
        date_patterns = [
            r'^(del?\s+\d{1,2}\s+de\s+\w+\s+al?\s+\d{1,2}\s+de\s+\w+(\s+de\s+\d{4})?[,.\s]*)+',
            r'^(desde\s+el?\s+\d{1,2}\s+de\s+\w+\s+hasta\s+el?\s+\d{1,2}\s+de\s+\w+[,.\s]*)+',
            r'^((\w+,\s+\d{1,2}\s+de\s+\w+\s*)+)',
            r'^((\d{1,2}\s+de\s+\w+\s*[,\s]*)+)',
            r'^(hasta\s+el?\s+\d{1,2}\s+de\s+\w+[,.\s]*)+',
            r'^(desde\s+el?\s+\d{1,2}\s+de\s+\w+[,.\s]*)+',
            r'^(\d{1,2}/\d{1,2}/\d{4}\s*[-]\s*\d{1,2}/\d{1,2}/\d{4}[,.\s]*)+',
            r'^(\d{1,2}\s+\w+\s+\d{4}[,.\s]*)+',
            r'^((lunes|martes|miércoles|jueves|viernes|sábado|domingo)[,\s]+)+',
            r'^(de\s+(lunes|martes|miércoles|jueves|viernes|sábado|domingo)\s+a\s+\w+[,.\s]*)+',
            r'^(de\s+\d{1,2}:\d{2}\s+a\s+\d{1,2}:\d{2}[,.\s]*)+',
            r'^(\d{1,2}:\d{2}\s*h[,.\s]*)+',
            r'^(\.\.\.\s*y\s+más\s+fechas\s*)',
        ]
        cleaned = description
        for _ in range(5):
            original = cleaned
            for pattern in date_patterns:
                cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.strip()
            if cleaned == original:
                break
        for phrase in [r'^(consulta (aquí|el)\s+)', r'^(más información\s+)', r'^(para más información\s+)']:
            cleaned = re.sub(phrase, '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _categorize_title(self, title: str, context: str) -> tuple:
        tl = title.lower()
        cl = context.lower()

        for prefix in ['exposición: ', 'museo: ', 'teatro: ', 'cine: ', 'taller: ',
                        'festival: ', 'experiencia: ', 'gastronomía: ', 'ruta: ',
                        'mercado: ', 'concierto: ', 'evento: ']:
            if tl.startswith(prefix):
                return prefix.rstrip(': ').capitalize(), title[len(prefix):]

        if any(w in tl for w in ['exposición', 'expo', 'muestra', 'retrospectiva', 'instalación artística']):
            return 'Exposición', title
        elif any(w in tl for w in ['museo', 'sala ']):
            return 'Museo', title
        elif any(w in tl for w in ['teatro', 'obra de teatro', 'función', 'representación', 'dramaturgia']):
            return 'Teatro', title
        elif any(w in tl for w in ['comedia', 'monólogo', 'humor', 'stand-up', 'stand up']):
            return 'Teatro', title
        elif any(w in tl for w in ['concierto', 'actuación musical', 'recital']):
            return 'Concierto', title
        elif any(w in tl for w in ['festival', 'feria', 'congreso']):
            return 'Festival', title
        elif any(w in tl for w in ['taller', 'workshop', 'curso', 'masterclass']):
            return 'Taller', title
        elif any(w in tl for w in ['cine', 'película', 'film', 'proyección', 'ciclo de cine']):
            return 'Cine', title
        elif any(w in tl for w in ['mercado', 'mercadillo', 'rastro']):
            return 'Mercado', title
        elif any(w in tl for w in ['ruta', 'paseo ', 'tour ']):
            return 'Ruta', title
        elif any(w in tl for w in ['gastronomía', 'gastronómico', 'food', 'chef']):
            return 'Gastronomía', title
        elif any(w in cl for w in ['inmersiv', 'experiencia interactiva', 'escape room']):
            return 'Experiencia', title
        elif any(w in cl for w in ['teatro', 'obra', 'escena', 'actores']):
            return 'Teatro', title
        elif any(w in cl for w in ['exposición', 'galería', 'pintura', 'escultura', 'arte']):
            return 'Exposición', title
        else:
            return 'Evento', title

    def _is_admin_text(self, text: str) -> bool:
        keywords = [
            'cómo llegar', 'cómo comprar', 'dónde comprar', 'horario de',
            'precio de entrada', 'más información', 'visita nuestra web',
            'síguenos en', 'instagram', 'facebook', 'redes sociales',
            'suscríbete', 'newsletter', 'términos y condiciones',
            'política de privacidad', 'uso de cookies', 'derechos reservados',
            'propiedad intelectual', 'iniciar sesión', 'registrarse',
            'descárgate la app', 'descarga gratis', 'disponible en app store'
        ]
        return any(k in text.lower() for k in keywords)

    def _is_legal_content(self, title: str, description: str) -> bool:
        combined = (title + " " + description).lower()
        indicators = [
            'términos y condiciones', 'política de privacidad', 'aviso legal',
            'derechos reservados', 'propiedad intelectual', 'limitación de responsabilidad',
            'parte contratante', 'acuerdo vinculante', 'condiciones generales',
            'términos de uso', 'servicios adicionales', 'cupones y vales',
            'cuenta, contraseña', 'base legal', 'consentimiento', 'protección de datos',
        ]
        return sum(1 for i in indicators if i in combined) >= 2

    def _extract_price(self, text: str) -> str:
        tl = text.lower()
        if any(w in tl for w in ['gratis', 'gratuito', 'free', 'entrada libre', 'sin coste']):
            return 'Gratis'
        m = re.search(r'(\d+[,.]?\d*)\s*€', text)
        if m:
            return m.group(0)
        if 'precio' in tl or 'entrada' in tl:
            return 'Consultar'
        return ''

    def _fetch_static(self, url: str) -> str:
        try:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            content = r.text
            if r.encoding and r.encoding.lower() in ('iso-8859-1', 'latin-1', 'windows-1252'):
                try:
                    content = r.content.decode('utf-8')
                except Exception:
                    pass
            return content
        except Exception:
            return None

    def _fetch_dynamic(self, url: str) -> str:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                page = ctx.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(3000)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            logger.error(f"  Playwright error: {e}")
            return self._fetch_static(url)

    def get_failed_sources(self) -> list:
        return self.failed_sources
