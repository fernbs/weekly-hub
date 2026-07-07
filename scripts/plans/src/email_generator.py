from jinja2 import Template
from datetime import datetime, timedelta
from src.utils import setup_logging, get_week_number, get_madrid_now, MADRID_TZ
import json
import os
import re
import requests

logger = setup_logging("email-generator")

MONTH_NAMES_ES = {
    '01': 'enero', '02': 'febrero', '03': 'marzo', '04': 'abril',
    '05': 'mayo', '06': 'junio', '07': 'julio', '08': 'agosto',
    '09': 'septiembre', '10': 'octubre', '11': 'noviembre', '12': 'diciembre'
}

MONTH_ABBR_ES = {
    '01': 'ene', '02': 'feb', '03': 'mar', '04': 'abr',
    '05': 'may', '06': 'jun', '07': 'jul', '08': 'ago',
    '09': 'sep', '10': 'oct', '11': 'nov', '12': 'dic'
}


def format_date_es(date_str: str) -> str:
    import re
    if not date_str:
        return ''
    if ' de ' in date_str:
        m = re.match(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', date_str.strip(), re.IGNORECASE)
        if m:
            day, month_name, year = m.groups()
            abbr = {v: MONTH_ABBR_ES[k] for k, v in MONTH_NAMES_ES.items()}.get(month_name.lower(), month_name[:3])
            return f"{int(day)} {abbr} {year}"
        return date_str
    m = re.match(r'(\d{1,2})/(\d{2})/(\d{4})', date_str.strip())
    if m:
        day, month, year = m.groups()
        return f"{int(day)} {MONTH_ABBR_ES.get(month, month)} {year}"
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str.strip())
    if m:
        year, month, day = m.groups()
        return f"{int(day)} {MONTH_ABBR_ES.get(month, month)} {year}"
    return date_str


def format_date_es_long(date_str: str) -> str:
    import re
    if not date_str:
        return ''
    if ' de ' in date_str:
        return date_str
    m = re.match(r'(\d{1,2})/(\d{2})/(\d{4})', date_str.strip())
    if m:
        day, month, year = m.groups()
        return f"{int(day)} de {MONTH_NAMES_ES.get(month, month)} de {year}"
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str.strip())
    if m:
        year, month, day = m.groups()
        return f"{int(day)} de {MONTH_NAMES_ES.get(month, month)} de {year}"
    return date_str


def _parse_event_date(date_str: str) -> datetime | None:
    import re
    if not date_str:
        return None
    m = re.match(r'(\d{1,2})/(\d{2})/(\d{4})', date_str.strip())
    if m:
        day, month, year = m.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except Exception:
            pass
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str.strip())
    if m:
        year, month, day = m.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except Exception:
            pass
    months_rev = {v: k for k, v in MONTH_NAMES_ES.items()}
    m = re.match(r'(\d{1,2})\s+(?:de\s+)?(\w+)\s+(?:de\s+)?(\d{4})', date_str.strip(), re.IGNORECASE)
    if m:
        day, month_name, year = m.groups()
        month_num = months_rev.get(month_name.lower())
        if month_num:
            try:
                return datetime(int(year), int(month_num), int(day))
            except Exception:
                pass
    return None


def _parse_iso(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _classify_event(event: dict, now: datetime) -> str:
    end_dt = _parse_event_date(event.get('fecha_fin', ''))
    if end_dt is not None:
        if now.date() <= end_dt.date() <= (now + timedelta(days=7)).date():
            return 'ending_soon'

    first_seen = _parse_iso(event.get('_first_seen') or event.get('_extracted_at', ''))
    seen_recently = first_seen is not None and (now - first_seen).days <= 8

    start_dt = _parse_event_date(event.get('fecha_inicio', ''))
    start_is_recent = (start_dt is None) or (start_dt >= now - timedelta(weeks=3))

    if seen_recently and start_is_recent:
        return 'new'

    return 'ongoing'


def _apply_display_dates(event: dict) -> dict:
    e = dict(event)
    e['fecha_inicio_es'] = format_date_es(e.get('fecha_inicio', ''))
    e['fecha_fin_es']    = format_date_es(e.get('fecha_fin', ''))
    return e


class EmailGenerator:
    def __init__(self):
        with open('templates/email_template.html', 'r', encoding='utf-8') as f:
            self.template = Template(f.read())

    def generate(self, events: list, stats: dict, failed_sources: list = None) -> dict:
        logger.info(f"Generando email con {len(events)} eventos...")
        now = get_madrid_now().replace(tzinfo=None)

        ending_soon    = []
        new_events     = []
        ongoing_events = []

        for e in events:
            e = _apply_display_dates(e)
            bucket = _classify_event(e, now)
            if bucket == 'ending_soon':
                ending_soon.append(e)
            elif bucket == 'new':
                new_events.append(e)
            else:
                ongoing_events.append(e)

        new_events     = self._sort_by_start(new_events)[:30]
        ending_soon    = self._sort_by_end(ending_soon)[:15]
        ongoing_events = self._sort_by_end(ongoing_events)[:25]

        week_num   = get_week_number()
        now_madrid = get_madrid_now()

        html = self.template.render(
            week_number=week_num,
            current_date=now_madrid.strftime('%d/%m/%Y'),
            current_date_es=format_date_es_long(now_madrid.strftime('%d/%m/%Y')),
            stats={
                'sources_processed': stats.get('sources_processed', 0),
                'new_events':     len(new_events),
                'ending_soon':    len(ending_soon),
                'ongoing_events': len(ongoing_events),
                'total_events':   len(events),
            },
            new_events=new_events,
            ending_soon=ending_soon,
            ongoing_events=ongoing_events,
            failed_sources=failed_sources or [],
        )

        subject = (
            f"🎭 Planes Madrid — semana {week_num} "
            f"({len(new_events)} nuevos · {len(ending_soon)} acaban pronto · "
            f"{len(ongoing_events)} activos)"
        )
        logger.info("✓ Email generado")

        # Inject hub button near top + link in footer if HUB_URL is configured
        hub_url = os.getenv('HUB_URL', '')
        if hub_url:
            hub_btn = (
                f'<div style="background:#2d1660;padding:12px 24px;text-align:center;'
                f'border-bottom:1px solid #4c1d95;">'
                f'<a href="{hub_url}" target="_blank" '
                f'style="display:inline-block;background:#7c3aed;color:#ffffff;text-decoration:none;'
                f'font-size:12px;font-weight:800;letter-spacing:1px;text-transform:uppercase;'
                f'padding:9px 22px;border-radius:4px;">🌐 Ver todos los planes en la web →</a>'
                f'</div>'
            )
            # Insert right after the opening <body> tag
            html = html.replace('<body', hub_btn + '<body', 1)

        return {'subject': subject, 'html': html}

    def _safe_dt(self, fecha: str) -> datetime:
        dt = _parse_event_date(fecha)
        return dt if dt is not None else datetime.max

    def _sort_by_start(self, events: list) -> list:
        return sorted(events, key=lambda e: self._safe_dt(e.get('fecha_inicio', '')))

    def _sort_by_end(self, events: list) -> list:
        return sorted(events, key=lambda e: self._safe_dt(e.get('fecha_fin') or e.get('fecha_inicio', '')))


# ============================================================
# JSON EXPORT — for weekly-hub
# ============================================================
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')


def _og_image_from_url(url: str) -> str:
    """Extract og:image from a page URL. Returns '' on failure."""
    if not url:
        return ''
    try:
        r = requests.get(url, headers={'User-Agent': _UA}, timeout=10)
        if r.status_code != 200:
            return ''
        for pat in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ]:
            m = re.search(pat, r.text, re.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith('http'):
                    return img
    except Exception:
        pass
    return ''


def _wikipedia_image(title: str) -> str:
    """Search Wikipedia (ES then EN) for an image matching the title."""
    if not title:
        return ''
    for lang in ('es', 'en'):
        try:
            # First: try direct page summary
            r = requests.get(
                f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}',
                headers={'User-Agent': 'LaGuiaFerlin/1.0'},
                timeout=8)
            if r.status_code == 200:
                img = r.json().get('thumbnail', {}).get('source', '')
                if img and img.startswith('http'):
                    return re.sub(r'/\d+px-', '/400px-', img)
            # Second: search API
            r = requests.get(
                f'https://{lang}.wikipedia.org/w/api.php',
                params={'action': 'query', 'list': 'search', 'srsearch': title,
                        'srlimit': 1, 'format': 'json'},
                headers={'User-Agent': 'LaGuiaFerlin/1.0'},
                timeout=8)
            if r.status_code == 200:
                results = r.json().get('query', {}).get('search', [])
                if results:
                    page_title = results[0]['title']
                    r2 = requests.get(
                        f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(page_title)}',
                        headers={'User-Agent': 'LaGuiaFerlin/1.0'},
                        timeout=8)
                    if r2.status_code == 200:
                        img = r2.json().get('thumbnail', {}).get('source', '')
                        if img and img.startswith('http'):
                            return re.sub(r'/\d+px-', '/400px-', img)
        except Exception:
            pass
    return ''


def _wikimedia_commons_image(title: str) -> str:
    """Search Wikimedia Commons for an image matching the title."""
    if not title:
        return ''
    try:
        r = requests.get(
            'https://commons.wikimedia.org/w/api.php',
            params={'action': 'query', 'generator': 'search', 'gsrsearch': f'file:{title}',
                    'gsrlimit': 1, 'prop': 'imageinfo', 'iiprop': 'url',
                    'iiurlwidth': 400, 'format': 'json'},
            headers={'User-Agent': 'LaGuiaFerlin/1.0'},
            timeout=8)
        if r.status_code == 200:
            pages = r.json().get('query', {}).get('pages', {})
            for page in pages.values():
                info = page.get('imageinfo', [{}])[0]
                img = info.get('thumburl') or info.get('url', '')
                if img and img.startswith('http'):
                    return img
    except Exception:
        pass
    return ''


def _fetch_event_image(event_url: str, source_url: str, title: str) -> str:
    """
    Try multiple sources to find an image for a plans event.
    Priority: event page og:image → source listing page → Wikipedia → Wikimedia Commons
    """
    # 1. Event page og:image
    img = _og_image_from_url(event_url)
    if img:
        return img

    # 2. Source listing page og:image (different URL, may not be blocked)
    if source_url and source_url != event_url:
        img = _og_image_from_url(source_url)
        if img:
            return img

    # 3. Wikipedia search by title
    img = _wikipedia_image(title)
    if img:
        return img

    # 4. Wikimedia Commons
    img = _wikimedia_commons_image(title)
    if img:
        return img

    return ''


def export_plans_json(events: list):
    """
    Write data/plans.json for the weekly-hub GitHub Pages site.
    Re-uses the same _classify_event() logic as EmailGenerator
    to split events into the three sections the web hub expects.
    """
    os.makedirs('data', exist_ok=True)
    now = datetime.now()

    new_events  = []
    ending_soon = []
    ongoing     = []

    for e in events:
        e = _apply_display_dates(e)
        bucket = _classify_event(e, now)
        serialized = _serialize_event(e, bucket)
        if bucket == 'new':
            new_events.append(serialized)
        elif bucket == 'ending_soon':
            ending_soon.append(serialized)
        else:
            ongoing.append(serialized)

    # Fetch images for events that have a URL but no image yet
    all_serialized = new_events + ending_soon + ongoing
    missing_img = [s for s in all_serialized if not s.get('image_url') and s.get('url')]
    if missing_img:
        logger.info(f"Fetching images for {len(missing_img)} events...")
        for i, s in enumerate(missing_img, 1):
            img = _fetch_event_image(
                event_url=s.get('url', ''),
                source_url=s.get('_url_fuente', ''),
                title=s.get('title', ''),
            )
            if img:
                s['image_url'] = img
            if i % 10 == 0:
                import time; time.sleep(0.5)
        found = sum(1 for s in missing_img if s.get('image_url'))
        logger.info(f"  Images found: {found}/{len(missing_img)}")

    payload = {
        'generated':   now.isoformat(),
        'new':         new_events,
        'ending_soon': ending_soon,
        'ongoing':     ongoing,
    }

    with open('data/plans.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(
        f"Escrito data/plans.json — "
        f"{len(new_events)} nuevos / {len(ending_soon)} acaban pronto / {len(ongoing)} activos"
    )


def _serialize_event(e: dict, section: str) -> dict:
    import re as _re
    # Build bullets: use points first, fall back to descripcion sentences, then why_go
    points = e.get('points') or []
    if not points and e.get('descripcion'):
        sentences = [s.strip() for s in _re.split(r'[.·•\n]+', e['descripcion']) if len(s.strip()) > 20]
        points = sentences[:3]
    if not points and e.get('why_go'):
        points = [e['why_go']]

    # Raw ISO dates for progress bar calculation
    date_start_raw = e.get('fecha_inicio', '')
    date_end_raw   = e.get('fecha_fin', '')

    return {
        'title':           e.get('titulo', ''),
        'category':        e.get('categoria', ''),
        'date_start':      e.get('fecha_inicio_es') or date_start_raw,
        'date_end':        e.get('fecha_fin_es')    or date_end_raw,
        'date_start_raw':  date_start_raw,
        'date_end_raw':    date_end_raw,
        'venue':           '',
        'url':             e.get('enlace') or e.get('url', ''),
        'image_url':       '',
        'bullets':         points,
        'price':           e.get('precio', ''),
        'why_go':          e.get('why_go', ''),
        '_url_fuente':     e.get('url_fuente', ''),
    }
