import requests
import re
import os
import json
import html
import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

DAYS_AHEAD = 56
TODAY  = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
CUTOFF = TODAY + timedelta(days=DAYS_AHEAD)

MONTHS_ES = {
    'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
    'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
    'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,
    'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12,
    'jan':1,'aug':8,'apr':4,'dec':12,
}

_NOT_A_BAND = {
    'entradas agotadas','agotadas','sold out','pospuesto','cancelado',
    'anunciado','confirmado','próximamente','sin fecha','nueva fecha',
    'fecha por confirmar','entradas disponibles','entradas a la venta',
    'compra entradas','más información','info','aviso','nota',
}

_NON_MADRID_CITIES = {
    'barcelona','bilbao','sevilla','seville','valencia','zaragoza',
    'málaga','malaga','vigo','oviedo','asturias','salamanca','villava',
    'badalona','pamplona','iruña','murcia','alicante','granada','vitoria',
    'gasteiz','donostia','san sebastian','castellón','castellon','burgos',
    'valladolid','córdoba','cordoba','santander','girona','tarragona','lleida',
    'albacete','badajoz','huelva','jaén','jaen','almería','almeria','toledo',
    'cuenca','mérida','merida','cáceres','caceres','logroño','logrono',
    'palencia','ávila','avila','segovia','soria','teruel','huesca',
    'pontevedra','ferrol','lugo','ourense','orense','a coruña','la coruña',
    'coruña','vigo','santiago','cadiz','cádiz','elche','jerez',
    'porto','lisboa','lisbon','braga','coimbra','faro',
    'paris','lyon','bordeaux','toulouse','marseille','nantes','lille',
    'strasbourg','montpellier','rennes','nice','grenoble',
    'london','manchester','birmingham','glasgow','edinburgh','bristol',
    'leeds','liverpool','sheffield','newcastle','dublin','belfast',
    'berlin','munich','münchen','hamburg','cologne','köln','frankfurt',
    'dusseldorf','düsseldorf','stuttgart','dortmund','essen','leipzig',
    'amsterdam','brussels','bruxelles','antwerp','milan','milano',
    'rome','roma','turin','torino','naples','napoli',
    'vienna','wien','zurich','zürich','bern','geneva','genève',
    'oslo','stockholm','gothenburg','copenhagen','helsinki',
    'athens','warsaw','krakow','prague','bratislava','budapest',
    'bucharest','sofia','zagreb','belgrade','sarajevo','ljubljana',
    'riga','tallinn','vilnius','reykjavik',
}

def clean(t):
    if not t: return ""
    t = html.unescape(str(t))
    t = re.sub(r'<[^>]+>', '', t)
    return re.sub(r'\s+', ' ', t).strip()

def parse_date(text):
    if not text: return None
    t = clean(str(text)).lower().strip()
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', t)
    if m:
        try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except: pass
    m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', t)
    if m:
        try: return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except: pass
    m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})(?!\d)', t)
    if m:
        try:
            yr = 2000 + int(m.group(3))
            return datetime(yr, int(m.group(2)), int(m.group(1)))
        except: pass
    m = re.search(r'(\d{1,2})\s+(?:de\s+)?([a-záéíóúü]+)\s+(?:de\s+)?(\d{4})', t)
    if m:
        mon = MONTHS_ES.get(m.group(2))
        if mon:
            try: return datetime(int(m.group(3)), mon, int(m.group(1)))
            except: pass
    m = re.search(r'(\d{1,2})\s+(?:de\s+)?([a-záéíóúü]{3,})(?:\s*[-–,]|$)', t)
    if m:
        mon = MONTHS_ES.get(m.group(2))
        if mon:
            day, yr = int(m.group(1)), TODAY.year
            try:
                dt = datetime(yr, mon, day)
                if dt < TODAY: dt = datetime(yr+1, mon, day)
                return dt
            except: pass
    return None

def future(dt):
    return dt is not None and TODAY <= dt <= CUTOFF

def day_es(dt):
    return ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'][dt.weekday()]

def fmt(dt):
    return f"{dt.strftime('%d/%m/%Y')} · {day_es(dt)}" if dt else ''

def sim(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def is_real_band_name(text: str) -> bool:
    stripped = re.sub(r'[¡!¿?\s]', '', text).lower()
    for phrase in _NOT_A_BAND:
        if phrase.replace(' ', '') in stripped:
            return False
    if text.strip().startswith('¡') or text.strip().startswith('¿'):
        return False
    return True

_LOWER_WORDS = {
    'a','an','the','and','but','or','for','in','of','on','to','by','at','as','up','is',
    'y','de','en','con','el','la','los','las','del','al','sin','por','para','una','uno',
}
_KEEP_UPPER = {'DJ','LP','EP','AC','DC','UK','US','NY','LA','II','IV','VI','IX','XI'}

def to_title_case(text: str) -> str:
    if not text: return text
    words = text.split()
    result = []
    for i, word in enumerate(words):
        if word.upper() in _KEEP_UPPER:
            result.append(word.upper())
        elif re.match(r'^[A-Z0-9]{1,2}/[A-Z0-9]{1,2}$', word):
            result.append(word)
        elif word.lower() in _LOWER_WORDS and i > 0:
            result.append(word.lower())
        else:
            result.append(word.capitalize())
    return ' '.join(result)

def strip_cities(text: str) -> str:
    if not text: return text
    text = re.sub(
        r'^(madrid|barcelona|bilbao|sevilla|valencia|zaragoza|m[aá]laga|españa)\s*[-:]\s*',
        '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\s+(?:on|en)\s+20\d{2}\s+(?:in|en)\s+.*$', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(
        r'\s+en\s+(madrid|barcelona|bilbao|sevilla|valencia|zaragoza|m[aá]laga'
        r'|espa[nñ]a|villava|alcobendas|alcalá|leganés|getafe|móstoles'
        r'|[a-záéíóúü]+(?:\s*[,y]\s*[a-záéíóúü]+)*)[\s,]*$',
        '', text, flags=re.IGNORECASE).strip()
    text = re.sub(
        r'\s*[-–]\s*(madrid|barcelona|bilbao|sevilla|valencia)[\s,]*$',
        '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\s+20\d{2}\s*$', '', text).strip()
    return text

def clean_artist(text: str) -> str:
    return to_title_case(strip_cities(text))

def _norm(artist: str) -> str:
    a = strip_cities(artist).lower().strip()
    # Strip Spanish concert page title prefixes
    a = re.sub(r'^detalles\s+de\s+concierto\s+de\s+', '', a)
    a = re.sub(r'^concierto\s+de\s+', '', a)
    a = re.sub(r'^entradas\s+para\s+', '', a)
    a = re.sub(r'\s*([\-–]\s*)?(tour\b|gira\b|en\s+concierto|presenta(ci[oó]n)?|live\b|show\b).*$', '', a)
    a = re.sub(r'\s*\(.*?\)\s*$', '', a)
    a = re.sub(r'\s+en\s*$', '', a)
    a = re.split(r'\s*[+&]\s*|\s+con\s+|\s+with\s+|\s+y\s+', a)[0]
    return a.strip()

def _bands_match(nc: str, nu: str) -> bool:
    if nc == nu: return True
    if sim(nc, nu) > 0.85: return True
    short, long = (nc, nu) if len(nc) <= len(nu) else (nu, nc)
    if len(short) > 4 and short in long and len(short) / len(long) >= 0.70: return True
    return False

def _dates_close(d1, d2, tolerance: int = 3) -> bool:
    if d1 is None or d2 is None: return d1 is None and d2 is None
    return abs((d1.date() - d2.date()).days) <= tolerance

def _is_non_madrid(concert: dict) -> bool:
    venue = (concert.get('venue') or '').lower().strip()
    raw   = (concert.get('_artist_raw') or concert.get('artist') or '').lower()
    if any(re.search(r'\b' + re.escape(city) + r'\b', venue) for city in _NON_MADRID_CITIES):
        return True
    cities_in_raw = [c for c in _NON_MADRID_CITIES if re.search(r'\b' + re.escape(c) + r'\b', raw)]
    if cities_in_raw and not re.search(r'\bmadrid\b', raw): return True
    return False

def _is_better(c: dict, existing: dict) -> bool:
    c_venue = c.get('venue', '').lower()
    e_venue = existing.get('venue', '').lower()
    if c_venue not in ('madrid', '') and e_venue in ('madrid', ''): return True
    if c.get('buy_link') and not existing.get('buy_link'): return True
    return False

def dedup(lst):
    unique = []
    for c in lst:
        nc = _norm(c['artist'])
        found = False
        for j, u in enumerate(unique):
            if _bands_match(nc, _norm(u['artist'])) and _dates_close(c.get('date_obj'), u.get('date_obj'), 3):
                if _is_better(c, u): unique[j] = c
                found = True; break
        if not found: unique.append(c)
    return [c for c in unique if not _is_non_madrid(c)]

def clean_venue(v: str) -> str:
    """Strip date patterns that sometimes leak into venue strings."""
    if not v: return v
    v = re.sub(r'\s+\d{1,2}[/\-]\d{1,2}[/\-]\d{4}\s*$', '', v).strip()
    v = re.sub(r'\s+\d{4}-\d{2}-\d{2}\s*$', '', v).strip()
    return v

def mk(artist, date_obj, venue='Madrid', address='', price='Consultar',
       buy_link='', source='', lineup='', notes='', genre=''):
    return {
        'artist': artist, '_artist_raw': artist,
        'date_obj': date_obj, 'date_str': fmt(date_obj),
        'venue': clean_venue(venue), 'address': address, 'price': price,
        'buy_link': buy_link, 'source': source,
        'lineup': lineup or artist, 'notes': notes,
        'image_url': '', 'genre': genre,
    }

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')

def fetch(url, timeout=25):
    try:
        r = requests.get(url, headers={'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9'}, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  requests error: {e}"); return ""

def pw_fetch(url, wait_ms=4000):
    try:
        with sync_playwright() as p:
            br  = p.chromium.launch(headless=True)
            ctx = br.new_context(user_agent=UA, locale='es-ES')
            pg  = ctx.new_page()
            pg.goto(url, wait_until='domcontentloaded', timeout=45000)
            pg.wait_for_timeout(wait_ms)
            pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            pg.wait_for_timeout(2000)
            c = pg.content(); br.close(); return c
    except Exception as e:
        print(f"  Playwright error: {e}"); return ""

def _initials_svg(artist: str) -> str:
    words = [w for w in artist.split() if w and w[0].isalpha()]
    initials = (words[0][0] + words[1][0]).upper() if len(words) >= 2 \
               else (words[0][:2].upper() if words else '🤘')
    return (
        "data:image/svg+xml,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='65' height='65'>"
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0%25' stop-color='%23374151'/>"
        "<stop offset='100%25' stop-color='%231f2937'/>"
        "</linearGradient></defs>"
        "<rect width='65' height='65' rx='6' fill='url(%23g)'/>"
        "<rect width='65' height='65' rx='6' fill='none' stroke='%234b5563' stroke-width='1'/>"
        f"<text x='50%25' y='50%25' dominant-baseline='central' text-anchor='middle' "
        f"font-family='Arial,sans-serif' font-size='20' font-weight='700' fill='%239ca3af'>"
        f"{initials}</text></svg>"
    )

def fetch_event_poster(url: str) -> str:
    if not url: return ''
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=12)
        if r.status_code != 200: return ''
        for pat in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ]:
            m = re.search(pat, r.text, re.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith('http'): return img
    except Exception: pass
    return ''

def fetch_artist_image_deezer(artist: str) -> str:
    if not artist: return ''
    try:
        r = requests.get('https://api.deezer.com/search/artist', params={'q': artist, 'limit': 3}, timeout=10)
        if r.status_code == 200:
            for item in r.json().get('data', []):
                pic = item.get('picture_big') or item.get('picture_medium')
                if pic and pic.startswith('http') and 'default' not in pic and '/images/artist///' not in pic:
                    return pic
    except Exception: pass
    return ''

def fetch_artist_image_itunes(artist: str) -> str:
    if not artist: return ''
    for query in [artist, re.sub(r'^(the|los|las|el|la)\s+', '', artist, flags=re.IGNORECASE)]:
        try:
            r = requests.get('https://itunes.apple.com/search',
                params={'term': query, 'entity': 'musicArtist', 'limit': 5, 'media': 'music'}, timeout=10)
            if r.status_code == 200:
                for result in r.json().get('results', []):
                    art = result.get('artworkUrl100') or result.get('artworkUrl60')
                    if art: return art.replace('100x100bb', '300x300bb').replace('60x60bb', '300x300bb')
        except Exception: pass
    return ''

def fetch_artist_image_wikipedia(artist: str) -> str:
    if not artist: return ''
    for lang in ('en', 'es'):
        try:
            r = requests.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(artist)}",
                headers={'User-Agent': 'MadridMetalConcerts/1.0'}, timeout=10)
            if r.status_code == 200:
                img = r.json().get('thumbnail', {}).get('source', '')
                if img and img.startswith('http'): return re.sub(r'/\d+px-', '/300px-', img)
        except Exception: pass
    return ''

def fetch_artist_image_lastfm(artist: str, api_key: str) -> str:
    if not api_key or not artist: return ''
    try:
        r = requests.get('https://ws.audioscrobbler.com/2.0/',
            params={'method': 'artist.getinfo', 'artist': artist,
                    'api_key': api_key, 'format': 'json', 'autocorrect': 1}, timeout=10)
        if r.status_code == 200:
            for img in r.json().get('artist', {}).get('image', []):
                if img.get('size') in ('extralarge', 'large'):
                    url = img.get('#text', '')
                    if url.startswith('http') and '2a96cbd8b46e442fc41c2b86b821562f' not in url:
                        return url
    except Exception: pass
    return ''

def enrich_images(concerts: list, lastfm_key: str) -> list:
    print(f"  Fetching images ({len(concerts)} concerts)...")
    stats = {k: 0 for k in ('poster','deezer','itunes','wikipedia','lastfm','initials')}
    for i, c in enumerate(concerts, 1):
        artist = c.get('artist', '')
        img, src = '', 'initials'
        for fn, label in [
            (lambda: fetch_event_poster(c.get('buy_link', '')), 'poster'),
            (lambda: fetch_artist_image_deezer(artist),        'deezer'),
            (lambda: fetch_artist_image_itunes(artist),        'itunes'),
            (lambda: fetch_artist_image_wikipedia(artist),     'wikipedia'),
            (lambda: fetch_artist_image_lastfm(artist, lastfm_key), 'lastfm'),
        ]:
            img = fn()
            if img: src = label; break
        c['image_url'] = img if img else _initials_svg(artist)
        stats[src] += 1
        print(f"    [{i:2d}] {'✓' if img else '◌'} [{src:9s}] {artist[:40]}")
        if i % 8 == 0: time.sleep(0.5)
    print(f"  {stats}")
    return concerts

def scrape_todoheavymetal():
    print("  [TodoHeavyMetal]")
    results = []
    html_text = fetch("https://www.todoheavymetal.com/index.php/agenda")
    if not html_text: html_text = pw_fetch("https://www.todoheavymetal.com/index.php/agenda")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    content = soup.select_one('.entry-content,.post-content,article,main,#content')
    if not content: content = soup
    current_artist, current_lineup = '', ''
    for el in content.find_all(['strong','li','p']):
        text = clean(el.get_text())
        if not text: continue
        if el.name in ('strong','p') and not re.search(r'\d{4}', text) and len(text) > 1:
            looks_like_band = (re.match(r'^[A-ZÁÉÍÓÚÜÑ\s\+\-\/\(\)&\.\'0-9]+$', text) or text == text.upper())
            if looks_like_band and is_real_band_name(text):
                current_lineup = text
                current_artist = re.split(r'\s*\+\s*|\bcon\b', text, flags=re.IGNORECASE)[0].strip()
        elif el.name == 'li' and current_artist:
            notes = ''
            if re.search(r'entradas\s+agotadas|sold\s*out', text, re.IGNORECASE):
                notes = 'ENTRADAS AGOTADAS'
                text = re.sub(r'¡+\s*entradas\s+agotadas\s*!+', '', text, flags=re.IGNORECASE).strip()
            elif '¡¡' in text:
                nm = re.search(r'¡¡(.+?)!!', text)
                notes = nm.group(1).strip() if nm else ''
            elif 'pospuesto' in text.lower(): notes = 'POSPUESTO'
            if 'madrid' not in text.lower(): continue
            date_obj = parse_date(text)
            if not future(date_obj): continue
            venue_m = re.search(r'\d{4}\s+(.+?)(?:,\s*(?:madrid|leganés|alcalá)|$)', text, re.IGNORECASE)
            venue = venue_m.group(1).strip().rstrip(',') if venue_m else 'Madrid'
            a_tag = el.find('a', href=True)
            results.append(mk(artist=current_artist, date_obj=date_obj, venue=venue,
                               buy_link=a_tag['href'] if a_tag else '',
                               source='TodoHeavyMetal', lineup=current_lineup, notes=notes))
    print(f"  {len(results)} conciertos"); return results

def scrape_hellpress():
    print("  [HellPress]")
    results = []
    html_text = fetch("https://www.hellpress.com/agenda-conciertos/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    content = soup.select_one('.entry-content,article,.post-content,main')
    if not content: content = soup
    for h3 in content.find_all('h3'):
        h3_text = clean(h3.get_text())
        artist = re.sub(r'^(conciertos?\s+de\s+|gira\s+de\s+|concierto\s+de\s+)', '', h3_text, flags=re.IGNORECASE)
        artist_main = re.split(r'\s*\+\s*|\bcon\b', artist, flags=re.IGNORECASE)[0].strip()
        if not artist_main or len(artist_main) < 2: continue
        nxt = h3.find_next_sibling(); ul = None
        for _ in range(6):
            if nxt is None: break
            if nxt.name == 'ul': ul = nxt; break
            if nxt.name in ('h2','h3'): break
            inner = nxt.find('ul')
            if inner: ul = inner; break
            nxt = nxt.find_next_sibling()
        if not ul: continue
        for li in ul.find_all('li'):
            li_text = clean(li.get_text(' '))
            if 'madrid' not in li_text.lower(): continue
            date_obj = parse_date(li_text)
            if not future(date_obj): continue
            venue = ''
            pm = re.search(r'\(([^)]+)\)', li_text)
            if pm: venue = pm.group(1).strip()
            if not venue:
                sm = re.search(r'sala\s+([A-Za-záéíóúü\s]+)', li_text, re.IGNORECASE)
                if sm: venue = 'Sala ' + sm.group(1).strip()
            price = 'Consultar'
            prm = re.search(r'(\d[\d\.,]+)\s*euros?', li_text, re.IGNORECASE)
            if prm:
                price = prm.group(1) + '€'
                if 'gastos incluidos' in li_text.lower(): price += ' (gastos incl.)'
            buy_link = ''
            for a in li.find_all('a', href=True):
                if any(x in a['href'] for x in ['entradium','ticketmaster','taquilla','entradas','wegofan','quintessence','resurrectionfest','madnesslive']):
                    buy_link = a['href']; break
            if not buy_link:
                a = li.find('a', href=True)
                if a: buy_link = a['href']
            results.append(mk(artist=artist_main, date_obj=date_obj, venue=venue or 'Madrid',
                               price=price, buy_link=buy_link, source='HellPress', lineup=artist))
    print(f"  {len(results)} conciertos"); return results

def scrape_mariskalrock():
    print("  [MariskalRock]")
    results = []
    html_text = fetch("https://mariskalrock.com/guia-de-conciertos/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    content = soup.select_one('.entry-content,.post-content,article')
    if not content: content = soup
    current_artist, current_lineup = '', ''
    SKIP = ['guía','lista','facebook','instagram','twitter','busca','ctrl','recomendamos','actualizamos','periódicamente','te traemos','puedes ubicar']
    for el in content.find_all(['strong','b','p','br','li']):
        if el.name in ('strong','b'):
            text = clean(el.get_text())
            if not text or len(text) < 2: continue
            if re.search(r'\d{4}|\d{1,2}\s*[-–]\s*\w', text): continue
            if any(x in text.lower() for x in SKIP): continue
            if not is_real_band_name(text): continue
            candidate = re.split(r'\s*\+\s*|\bcon\b', text, flags=re.IGNORECASE)[0].strip()
            if candidate and len(candidate) > 1: current_artist = candidate; current_lineup = text
            continue
        if not current_artist: continue
        for line in el.get_text('\n').split('\n'):
            line = line.strip()
            if not line or len(line) < 5: continue
            if not re.search(r'\bmadrid\b|\bleganés\b|\bmóstoles\b|\balcalá\b|\bgetafe\b', line, re.IGNORECASE): continue
            date_obj = parse_date(line)
            if not future(date_obj): continue
            parts = [p.strip() for p in re.split(r'\s*[-–]\s*', line)]
            venue = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else 'Madrid')
            venue = re.split(r'\s*\+\s*', venue)[0].strip()
            results.append(mk(artist=current_artist, date_obj=date_obj, venue=venue, source='MariskalRock', lineup=current_lineup))
    print(f"  {len(results)} conciertos"); return results

def scrape_metalcry():
    print("  [MetalCry]")
    results = []
    html_text = fetch("https://metalcry.com/conciertos/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for h3 in soup.find_all('h3'):
        a_tag = h3.find('a')
        if not a_tag: continue
        artist = clean(a_tag.get_text())
        if not artist: continue
        nxt = h3.find_next_sibling(); table = None
        for _ in range(5):
            if nxt is None: break
            t = nxt if nxt.name == 'table' else nxt.find('table')
            if t: table = t; break
            nxt = nxt.find_next_sibling()
        if not table: continue
        for row in table.find_all('tr'):
            row_text = clean(row.get_text(' '))
            if 'madrid' not in row_text.lower(): continue
            date_obj = None
            for cell in row.find_all('td'):
                date_obj = parse_date(clean(cell.get_text()))
                if date_obj: break
            if not future(date_obj): continue
            venue = ''
            for a in row.find_all('a'):
                v = clean(a.get_text())
                if v and not any(x in v.lower() for x in ['añadir','google','ical','descargar']): venue = v; break
            address = ''
            am = re.search(r'Dirección:\s*([^|\.]+?)(?:\.|Teléfono|$)', row_text)
            if am: address = am.group(1).strip()
            buy_link = ''
            for a in row.find_all('a', href=True):
                if any(x in a['href'] for x in ['entradas','ticket','comprar','taquilla','madnesslive','quintessence','wegofan']):
                    buy_link = a['href']; break
            results.append(mk(artist=artist, date_obj=date_obj, venue=venue, address=address, buy_link=buy_link, source='MetalCry'))
    print(f"  {len(results)} conciertos"); return results

def scrape_metaltrip():
    print("  [MetalTrip]")
    results = []
    html_text = fetch("https://metaltrip.com/agenda/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for table in soup.find_all('table'):
        headers = [clean(th.get_text()).lower() for th in table.find_all('th')]
        if not any('evento' in h or 'fecha' in h for h in headers): continue
        idx = {}
        for i, h in enumerate(headers):
            if 'evento' in h or 'artista' in h: idx['artist'] = i
            elif 'fecha' in h: idx['date'] = i
            elif 'recinto' in h or 'sala' in h: idx['venue'] = i
            elif 'ciudad' in h: idx['city'] = i
            elif 'entrada' in h: idx['ticket'] = i
        if 'artist' not in idx or 'date' not in idx: continue
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) < 2: continue
            try:
                artist = clean(cells[idx['artist']].get_text())
                if not artist: continue
                date_obj = parse_date(clean(cells[idx['date']].get_text()))
                if not future(date_obj): continue
                city = clean(cells[idx['city']].get_text()).lower() if 'city' in idx else ''
                if city and 'madrid' not in city: continue
                venue = clean(cells[idx['venue']].get_text()) if 'venue' in idx else ''
                buy_link = ''
                if 'ticket' in idx:
                    a = cells[idx['ticket']].find('a', href=True)
                    if a: buy_link = a['href']
                if not buy_link:
                    a = cells[idx['artist']].find('a', href=True)
                    if a: buy_link = a['href']
                results.append(mk(artist=artist, date_obj=date_obj, venue=venue, buy_link=buy_link, source='MetalTrip'))
            except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_metalhammer():
    print("  [Metal Hammer ES]")
    results = []
    html_text = fetch("https://metalhammer.es/agenda/categoria/conciertos/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for article in soup.select('article,.entry,.post,.event'):
        try:
            text = clean(article.get_text(' '))
            if 'madrid' not in text.lower(): continue
            title_tag = article.select_one('h1,h2,h3,.entry-title,.post-title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist: continue
            date_obj = parse_date(text)
            if not future(date_obj): continue
            venue_tag = article.select_one('.venue,.sala,.place')
            venue = clean(venue_tag.get_text()) if venue_tag else 'Madrid'
            link_tag = article.select_one('a[href]')
            buy_link = link_tag['href'] if link_tag else ''
            results.append(mk(artist=artist, date_obj=date_obj, venue=venue, buy_link=buy_link, source='Metal Hammer ES'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_concerts_metal():
    print("  [Concerts-Metal]")
    results = []
    hdrs = {'User-Agent': UA, 'Accept': 'text/html,*/*;q=0.8', 'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br', 'Referer': 'https://es.concerts-metal.com/', 'Connection': 'keep-alive'}
    try:
        r = requests.get("https://es.concerts-metal.com/concerts_es_Spain_Madrid-2.html", headers=hdrs, timeout=25)
        html_text = r.text if r.status_code == 200 else ""
    except Exception as e:
        print(f"  requests error: {e}"); html_text = pw_fetch("https://es.concerts-metal.com/concerts_es_Spain_Madrid-2.html")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for row in soup.select('tr'):
        cells = row.find_all('td')
        if len(cells) < 3: continue
        try:
            date_obj = parse_date(clean(cells[0].get_text()))
            if not future(date_obj): continue
            a_tag = cells[1].find('a') or cells[1]
            artist = clean(a_tag.get_text())
            if not artist or len(artist) < 2: continue
            venue = clean(cells[2].get_text())
            buy_link = ''
            for a in row.find_all('a', href=True):
                href = a['href']
                if href.startswith('http') and 'concerts-metal' not in href: buy_link = href; break
            if not buy_link:
                a = cells[1].find('a', href=True)
                if a:
                    href = a['href']
                    buy_link = href if href.startswith('http') else 'https://es.concerts-metal.com' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue=venue, buy_link=buy_link, source='Concerts-Metal'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_tntradiorock():
    print("  [TNT Radio Rock]")
    results = []
    html_text = fetch("https://tntradiorock.com/agenda-de-conciertos/")
    if not html_text: html_text = pw_fetch("https://tntradiorock.com/agenda-de-conciertos/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    content = soup.select_one('.entry-content,.post-content,article,main')
    if not content: content = soup
    for heading in content.find_all(['h2','h3']):
        artist_full = clean(heading.get_text())
        if not artist_full or len(artist_full) < 2: continue
        if any(x in artist_full.lower() for x in ['agenda','listado','conciertos de rock','conciertos de metal','banda']): continue
        artist_main = re.split(r'\s*\+\s*|\s*-\s*', artist_full)[0].strip()
        nxt = heading.find_next_sibling()
        while nxt and nxt.name not in ('h2','h3'):
            for el in ([nxt] + nxt.find_all(['p','li'])):
                text = clean(el.get_text(' '))
                if not text or len(text) < 5: continue
                if 'madrid' not in text.lower(): break
                date_obj = parse_date(text)
                if not future(date_obj): break
                parts = [p.strip() for p in re.split(r'\s*[-–]\s*', text)]
                venue = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else 'Madrid')
                venue = re.sub(r'\bMadrid\b', '', venue, flags=re.IGNORECASE).strip().strip('-').strip()
                a_tag = el.find('a', href=True) if hasattr(el,'find') else None
                results.append(mk(artist=artist_main, date_obj=date_obj, venue=venue or 'Madrid',
                                   buy_link=a_tag['href'] if a_tag else '', source='TNT Radio Rock', lineup=artist_full))
                break
            if nxt: nxt = nxt.find_next_sibling()
    print(f"  {len(results)} conciertos"); return results

def scrape_conciertospormadrid():
    print("  [ConciertosPorMadrid]")
    results = []
    html_text = fetch("https://conciertospormadrid.com/conciertos-metal-madrid/")
    if not html_text: html_text = pw_fetch("https://conciertospormadrid.com/conciertos-metal-madrid/", wait_ms=5000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for h3 in soup.select('h3,h2'):
        a_tag = h3.find('a', href=True)
        if not a_tag: continue
        title = clean(a_tag.get('title','') or a_tag.get_text()); href = a_tag['href']
        date_obj = None
        ud = re.search(r'(\d{2})-(\d{2})-(\d{4})', href)
        if ud:
            try: date_obj = datetime(int(ud.group(3)), int(ud.group(2)), int(ud.group(1)))
            except: pass
        if not date_obj: date_obj = parse_date(title)
        if not future(date_obj): continue
        artist = re.sub(r'^Detalles\s+de\s+Concierto\s+de\s+', '', title, flags=re.IGNORECASE).strip()
        artist = re.sub(r'^Concierto\s+de\s+', '', artist, flags=re.IGNORECASE).strip()
        artist = re.sub(r'^Entradas\s+para\s+', '', artist, flags=re.IGNORECASE).strip()
        artist = re.sub(r'\s+en\s+concierto.*$', '', artist, flags=re.IGNORECASE).strip()
        artist = re.sub(r'\s+concierto.*$', '', artist, flags=re.IGNORECASE).strip()
        artist = re.sub(r'\s+madrid.*$', '', artist, flags=re.IGNORECASE).strip()
        artist_main = re.split(r'\s*\+\s*', artist)[0].strip()
        if not artist_main or len(artist_main) < 2: continue
        results.append(mk(artist=artist_main, date_obj=date_obj, venue='Madrid', buy_link=href, source='ConciertosPorMadrid', lineup=artist))
    print(f"  {len(results)} conciertos"); return results

def scrape_rafabasa():
    print("  [RafaBasa]")
    results = []
    html_text = fetch("https://www.rafabasa.com/agenda-de-conciertos-2/")
    if not html_text: html_text = pw_fetch("https://www.rafabasa.com/agenda-de-conciertos-2/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    content = soup.select_one('.entry-content,.post-content,article,main')
    if not content: content = soup
    lines = [l.strip() for l in content.get_text('\n').split('\n') if l.strip()]
    current_artist, current_lineup = '', ''
    for line in lines:
        if (not re.search(r'\d{4}', line) and not re.search(r'\d{1,2}\s+\w+\s*[-–]', line)
                and 2 < len(line) < 120
                and not any(x in line.lower() for x in ['agenda','conciertos','facebook','instagram','twitter','busca','actualiza','noticias','contacto'])
                and is_real_band_name(line)):
            candidate = re.split(r'\s*\+\s*|\bcon\b', line, flags=re.IGNORECASE)[0].strip()
            if candidate and len(candidate) > 1: current_artist = candidate; current_lineup = line
        elif current_artist and re.search(r'\bmadrid\b|\bleganés\b', line, re.IGNORECASE):
            date_obj = parse_date(line)
            if future(date_obj):
                parts = [p.strip() for p in re.split(r'\s*[-–]\s*', line)]
                venue = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else 'Madrid')
                results.append(mk(artist=current_artist, date_obj=date_obj, venue=venue, source='RafaBasa', lineup=current_lineup))
    print(f"  {len(results)} conciertos"); return results

def scrape_rockandblog():
    print("  [RockAndBlog]")
    results = []
    html_text = pw_fetch("https://rockandblog.net/agenda-conciertos-rock/", wait_ms=5000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    content = soup.select_one('.entry-content,.post-content,article,main,.wp-block-group')
    if not content: content = soup
    current_artist = ''
    for el in content.find_all(['strong','b','h3','h4','p','li']):
        text = clean(el.get_text())
        if not text: continue
        if el.name in ('strong','b','h3','h4') and not re.search(r'\d{4}', text) and len(text) > 1:
            if not is_real_band_name(text): continue
            candidate = re.split(r'\s*\+\s*|\bcon\b', text, flags=re.IGNORECASE)[0].strip()
            if candidate and not any(x in candidate.lower() for x in list(MONTHS_ES.keys()) + ['agenda','conciertos','rock','metal']):
                current_artist = candidate
        elif current_artist:
            if not re.search(r'\bmadrid\b|\bleganés\b', text, re.IGNORECASE): continue
            date_obj = parse_date(text)
            if future(date_obj):
                parts = [p.strip() for p in re.split(r'\s*[-–]\s*', text)]
                venue = parts[2] if len(parts) >= 3 else 'Madrid'
                a_tag = el.find('a', href=True) if hasattr(el,'find') else None
                results.append(mk(artist=current_artist, date_obj=date_obj, venue=venue,
                                   buy_link=a_tag['href'] if a_tag else '', source='RockAndBlog'))
    print(f"  {len(results)} conciertos"); return results

def scrape_directoriorock():
    print("  [Directorio Rock]")
    results = []
    html_text = fetch("https://www.directorio-rock.com/agenda/")
    if not html_text: html_text = pw_fetch("https://www.directorio-rock.com/agenda/")
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('a[href*="/agenda/"]'):
        try:
            if not card.find('h3') and not card.find('h2'): continue
            title_tag = card.select_one('h3,h2')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2: continue
            full_text = clean(card.get_text(' '))
            if 'madrid' not in full_text.lower(): continue
            date_obj = parse_date(full_text)
            if not future(date_obj): continue
            venue_m = re.search(r'([A-Za-záéíóúü\s]+),\s*madrid', full_text, re.IGNORECASE)
            venue = venue_m.group(1).strip() if venue_m else 'Madrid'
            buy_link = ''
            parent = card.parent
            if parent:
                for a in parent.find_all('a', href=True):
                    if 'taquilla.com' in a['href'] and 'entradas' in a['href']: buy_link = a['href']; break
            results.append(mk(artist=artist, date_obj=date_obj, venue=venue, buy_link=buy_link, source='Directorio Rock'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_madnesslive():
    print("  [MadnessLive]")
    results = []
    html_text = pw_fetch("https://www.madnesslive.es/es/", wait_ms=5000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for item in soup.select('article,.event,.event-item,.show,.product,.concierto'):
        try:
            text = clean(item.get_text(' '))
            if 'madrid' not in text.lower(): continue
            title_tag = item.select_one('h1,h2,h3,h4,.title,.name,.product-title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist: continue
            date_tag = item.select_one('time,.date,.fecha,.event-date')
            date_str_val = (date_tag.get('datetime','') or clean(date_tag.get_text())) if date_tag else ''
            date_obj = parse_date(date_str_val) or parse_date(text)
            if not future(date_obj): continue
            venue_tag = item.select_one('.venue,.sala,.place,.location')
            venue = clean(venue_tag.get_text()) if venue_tag else 'Madrid'
            price_tag = item.select_one('.price,.precio,.amount')
            price = clean(price_tag.get_text()) if price_tag else 'Consultar'
            link_tag = item.select_one('a[href]')
            buy_link = ''
            if link_tag:
                href = link_tag['href']
                buy_link = href if href.startswith('http') else 'https://www.madnesslive.es' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue=venue, price=price, buy_link=buy_link, source='MadnessLive'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_taquilla():
    print("  [Taquilla.com]")
    results = []
    html_text = pw_fetch("https://www.taquilla.com/conciertos/hard-rock/madrid", wait_ms=6000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('article,.event-card,.show-item,.concert-item,li.item,.product'):
        try:
            text = clean(card.get_text(' '))
            title_tag = card.select_one('h1,h2,h3,h4,.title,.name,.event-name')
            artist = clean(title_tag.get_text()) if title_tag else ''
            artist = re.sub(r'^Conciertos\s*', '', artist, flags=re.IGNORECASE).strip()
            if not artist or len(artist) < 2: continue
            date_obj = parse_date(text)
            if not future(date_obj): continue
            price_tag = card.select_one('.price,.precio,.amount,.desde')
            price = clean(price_tag.get_text()) if price_tag else 'Consultar'
            if price and not re.search(r'\d', price): price = 'Consultar'
            link_tag = card.select_one('a[href*="/entradas/"]')
            buy_link = ''
            if link_tag:
                href = link_tag['href']
                buy_link = href if href.startswith('http') else 'https://www.taquilla.com' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue='Madrid', price=price, buy_link=buy_link, source='Taquilla.com'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_lariviera():
    print("  [La Riviera]")
    results = []
    html_text = fetch("https://www.lariviera.net/agenda/")
    if not html_text: html_text = pw_fetch("https://www.lariviera.net/agenda/", wait_ms=4000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('article,.event,.show,.tribe-event,.tribe-events-calendar-list__event,.post,.entry'):
        try:
            title_tag = card.select_one('h1,h2,h3,h4,.entry-title,.tribe-event-title,.title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
            date_obj = None
            time_tag = card.select_one('time[datetime]')
            if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
            if not date_obj: date_obj = parse_date(clean(card.get_text(' ')))
            if not future(date_obj): continue
            buy_link = ''
            for a in card.find_all('a', href=True):
                if any(x in a['href'] for x in ['entradas','ticket','entradium','wegofan','comprar']): buy_link = a['href']; break
            if not buy_link:
                link_tag = card.select_one('a[href]')
                if link_tag:
                    href = link_tag['href']
                    buy_link = href if href.startswith('http') else 'https://www.lariviera.net' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue='La Riviera',
                               address='Paseo Bajo de la Virgen del Puerto, s/n, Madrid',
                               buy_link=buy_link, source='La Riviera'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_salacaracol():
    print("  [Sala Caracol]")
    results = []
    html_text = ''
    for url in ["https://salacaracol.com/agenda/", "https://salacaracol.com/"]:
        html_text = fetch(url)
        if html_text and len(html_text) > 2000: break
    if not html_text or len(html_text) < 2000: html_text = pw_fetch("https://salacaracol.com/", wait_ms=4000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('article,.event,.show,.post,.entry,.tribe-event'):
        try:
            title_tag = card.select_one('h1,h2,h3,h4,.entry-title,.title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
            date_obj = None
            time_tag = card.select_one('time[datetime]')
            if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
            if not date_obj: date_obj = parse_date(clean(card.get_text(' ')))
            if not future(date_obj): continue
            buy_link = ''
            for a in card.find_all('a', href=True):
                if any(x in a['href'] for x in ['entradas','ticket','entradium','wegofan','comprar']): buy_link = a['href']; break
            if not buy_link:
                link_tag = card.select_one('a[href]')
                if link_tag:
                    href = link_tag['href']
                    buy_link = href if href.startswith('http') else 'https://salacaracol.com' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue='Sala Caracol',
                               address='Calle de Bernardino Obregón, 18, Madrid',
                               buy_link=buy_link, source='Sala Caracol'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_warlitzer():
    print("  [Warlitzer]")
    results = []
    html_text = ''
    for url in ["https://www.warlitzer.es/agenda/", "https://www.warlitzer.es/conciertos/", "https://www.warlitzer.es/"]:
        html_text = fetch(url)
        if html_text and len(html_text) > 2000: break
    if not html_text or len(html_text) < 2000: html_text = pw_fetch("https://www.warlitzer.es/", wait_ms=4000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('article,.event,.show,.post,.entry,li.item'):
        try:
            title_tag = card.select_one('h1,h2,h3,h4,.title,.entry-title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
            date_obj = None
            time_tag = card.select_one('time[datetime]')
            if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
            if not date_obj: date_obj = parse_date(clean(card.get_text(' ')))
            if not future(date_obj): continue
            buy_link = ''
            link_tag = card.select_one('a[href]')
            if link_tag:
                href = link_tag['href']
                buy_link = href if href.startswith('http') else 'https://www.warlitzer.es' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue='Warlitzer',
                               address='Calle de Valverde, 33, Madrid',
                               buy_link=buy_link, source='Warlitzer'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_elsol():
    print("  [El Sol]")
    results = []
    html_text = ''
    for url in ["https://elsolmadrid.com/agenda/", "https://www.elsolmadrid.com/agenda/", "https://elsolmadrid.com/"]:
        html_text = fetch(url)
        if html_text and len(html_text) > 2000: break
    if not html_text or len(html_text) < 2000: html_text = pw_fetch("https://elsolmadrid.com/", wait_ms=4000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('article,.event,.show,.post,.entry,.tribe-event'):
        try:
            title_tag = card.select_one('h1,h2,h3,h4,.title,.entry-title,.tribe-event-title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
            date_obj = None
            time_tag = card.select_one('time[datetime]')
            if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
            if not date_obj: date_obj = parse_date(clean(card.get_text(' ')))
            if not future(date_obj): continue
            buy_link = ''
            for a in card.find_all('a', href=True):
                if any(x in a['href'] for x in ['entradas','ticket','entradium','wegofan','comprar']): buy_link = a['href']; break
            if not buy_link:
                link_tag = card.select_one('a[href]')
                if link_tag:
                    href = link_tag['href']
                    buy_link = href if href.startswith('http') else 'https://elsolmadrid.com' + href
            results.append(mk(artist=artist, date_obj=date_obj, venue='El Sol',
                               address='Calle de los Jardines, 3, Madrid',
                               buy_link=buy_link, source='El Sol'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_copernico():
    print("  [Copérnico]")
    results = []
    html_text = ''; base = 'https://www.copernico.com.es'
    for url in ["https://www.copernico.com.es/agenda/", "https://www.copernico.com.es/"]:
        html_text = fetch(url)
        if html_text and len(html_text) > 2000: break
    if not html_text or len(html_text) < 2000: html_text = pw_fetch("https://www.copernico.com.es/", wait_ms=4000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    for card in soup.select('article,.event,.show,.post,.entry'):
        try:
            title_tag = card.select_one('h1,h2,h3,h4,.title,.entry-title')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
            date_obj = None
            time_tag = card.select_one('time[datetime]')
            if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
            if not date_obj: date_obj = parse_date(clean(card.get_text(' ')))
            if not future(date_obj): continue
            buy_link = ''
            link_tag = card.select_one('a[href]')
            if link_tag:
                href = link_tag['href']
                buy_link = href if href.startswith('http') else base + href
            results.append(mk(artist=artist, date_obj=date_obj, venue='Copérnico',
                               address='Calle de Alberto Aguilera, 23, Madrid',
                               buy_link=buy_link, source='Copérnico'))
        except: pass
    print(f"  {len(results)} conciertos"); return results

def scrape_ticketmaster():
    print("  [Ticketmaster ES]")
    results = []
    for url in [
        "https://www.ticketmaster.es/es/entradas-conciertos/rock/madrid/",
        "https://www.ticketmaster.es/es/entradas-conciertos/metal/madrid/",
    ]:
        html_text = pw_fetch(url, wait_ms=6000)
        if not html_text: continue
        soup = BeautifulSoup(html_text, 'lxml')
        cards = soup.select('[data-testid="event-card"],[class*="EventCard"],[class*="event-tile"],[class*="EventTile"],article,li[class*="event"],li[class*="show"]')
        for card in cards:
            try:
                title_tag = card.select_one('[data-testid="event-name"],h3,h2,h4,[class*="title"],[class*="Title"],[class*="name"]')
                artist = clean(title_tag.get_text()) if title_tag else ''
                if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
                date_obj = None
                time_tag = card.select_one('time[datetime]')
                if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
                if not date_obj:
                    date_tag = card.select_one('[data-testid="event-date"],[class*="date"],[class*="Date"]')
                    if date_tag: date_obj = parse_date(clean(date_tag.get_text()))
                if not date_obj: date_obj = parse_date(clean(card.get_text(' ')))
                if not future(date_obj): continue
                venue_tag = card.select_one('[data-testid="event-venue"],[class*="venue"],[class*="Venue"]')
                venue = clean(venue_tag.get_text()) if venue_tag else 'Madrid'
                if not venue or len(venue) < 2: venue = 'Madrid'
                price_tag = card.select_one('[class*="price"],[class*="Price"]')
                price = clean(price_tag.get_text()) if price_tag else 'Consultar'
                if price and not re.search(r'\d', price): price = 'Consultar'
                buy_link = ''
                link_tag = card.select_one('a[href]')
                if link_tag:
                    href = link_tag['href']
                    buy_link = href if href.startswith('http') else 'https://www.ticketmaster.es' + href
                results.append(mk(artist=artist, date_obj=date_obj, venue=venue, price=price, buy_link=buy_link, source='Ticketmaster ES'))
            except: pass
    seen = set(); deduped = []
    for c in results:
        key = (c['artist'].lower(), c.get('date_obj'))
        if key not in seen: seen.add(key); deduped.append(c)
    print(f"  {len(deduped)} conciertos"); return deduped

def scrape_songkick():
    print("  [Songkick]")
    results = []
    html_text = pw_fetch("https://www.songkick.com/metro-areas/28754-spain-madrid", wait_ms=6000)
    if not html_text: return results
    soup = BeautifulSoup(html_text, 'lxml')
    events = soup.select('li[class*="event"],article[class*="event"],.event-listings li,ul.event-listings > li,.concerts-summary li,[itemtype*="Event"]')
    for ev in events:
        try:
            title_tag = ev.select_one('.summary strong,[itemprop="name"],h3,h4,.event-title,strong,[class*="title"]')
            artist = clean(title_tag.get_text()) if title_tag else ''
            if not artist or len(artist) < 2 or not is_real_band_name(artist): continue
            date_obj = None
            time_tag = ev.select_one('time[datetime]')
            if time_tag: date_obj = parse_date(time_tag.get('datetime',''))
            if not date_obj: date_obj = parse_date(clean(ev.get_text(' ')))
            if not future(date_obj): continue
            venue_tag = ev.select_one('[itemprop="location"] [itemprop="name"],.venue-name,.location strong')
            venue = clean(venue_tag.get_text()) if venue_tag else 'Madrid'
            if not venue or len(venue) < 2: venue = 'Madrid'
            buy_link = ''
            for a in ev.find_all('a', href=True):
                href = a['href']
                if 'songkick.com' in href and '/concerts/' in href:
                    buy_link = href if href.startswith('http') else 'https://www.songkick.com' + href; break
            results.append(mk(artist=artist, date_obj=date_obj, venue=venue, buy_link=buy_link, source='Songkick'))
        except: pass
    print(f"  {len(results)} conciertos"); return results


# ── GENRE LOOKUP ──────────────────────────────────────────────────────────────

_genre_cache = {}  # artist → genre, avoids duplicate lookups in same run

def _clean_genre_string(raw: str) -> str:
    """Normalise a raw genre string from any source."""
    if not raw: return ''
    # Strip wiki-style citations and parenthetical notes
    raw = re.sub(r'\[.*?\]', '', raw)
    raw = re.sub(r'\(.*?\)', '', raw)
    # Take only the first genre if comma/slash separated and too long
    parts = [p.strip() for p in re.split(r'[,;]', raw) if p.strip()]
    genre = parts[0] if parts else raw.strip()
    # Capitalise each word, cap length
    genre = ' '.join(w.capitalize() for w in genre.split())
    return genre[:60]

def fetch_genre_metal_archives(artist: str) -> str:
    """Search Metal Archives for the artist and return their genre."""
    try:
        url = (
            "https://www.metal-archives.com/search/ajax-band-search/"
            f"?field=name&query={requests.utils.quote(artist)}"
            "&sEcho=1&iColumns=3&iDisplayStart=0&iDisplayLength=5"
        )
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; MusicBot/1.0)',
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return ''
        data = r.json()
        entries = data.get('aaData', [])
        if not entries:
            return ''
        # Each entry is [band_link_html, country_html, genre_html]
        # Try to find the best match: prefer entry whose name matches closely
        artist_lower = artist.lower()
        best_genre = ''
        best_score = 0
        for entry in entries[:5]:
            # Extract band name from HTML anchor
            name_match = re.search(r'>([^<]+)</a>', entry[0])
            genre_match = re.search(r'>([^<]+)</a>', entry[2]) if len(entry) > 2 else None
            if not name_match: continue
            band_name = name_match.group(1).strip()
            genre_raw = entry[2] if len(entry) > 2 else ''
            # genre column is plain text (not a link) in MA ajax
            if isinstance(genre_raw, str) and '<' not in genre_raw:
                genre_raw = genre_raw.strip()
            elif genre_match:
                genre_raw = genre_match.group(1).strip()
            else:
                genre_raw = ''
            score = SequenceMatcher(None, artist_lower, band_name.lower()).ratio()
            if score > best_score and genre_raw:
                best_score = score
                best_genre = genre_raw
        if best_score >= 0.75 and best_genre:
            return _clean_genre_string(best_genre)
    except Exception as e:
        pass
    return ''

def fetch_genre_wikipedia(artist: str) -> str:
    """Search Wikipedia for the artist and extract genre from the infobox."""
    try:
        search_url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={requests.utils.quote(artist + ' band')}"
            "&format=json&utf8=1&srlimit=3"
        )
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; MusicBot/1.0)'}
        r = requests.get(search_url, headers=headers, timeout=8)
        if r.status_code != 200: return ''
        results = r.json().get('query', {}).get('search', [])
        if not results: return ''

        # Try the top 3 results to find a music article
        for result in results[:3]:
            page_title = result['title']
            parse_url = (
                "https://en.wikipedia.org/w/api.php"
                f"?action=parse&page={requests.utils.quote(page_title)}"
                "&prop=wikitext&format=json&utf8=1"
            )
            r2 = requests.get(parse_url, headers=headers, timeout=8)
            if r2.status_code != 200: continue
            wikitext = r2.json().get('parse', {}).get('wikitext', {}).get('*', '')
            # Extract genre from infobox
            genre_match = re.search(
                r'\|\s*genre\s*=\s*([^\n\|\}]+)',
                wikitext, re.IGNORECASE
            )
            if genre_match:
                raw = genre_match.group(1).strip()
                # Remove wiki markup like [[Death metal]] → Death metal
                raw = re.sub(r'\[\[([^|\]]+)(?:\|[^\]]+)?\]\]', r'\1', raw)
                raw = re.sub(r"{{.*?}}", '', raw)
                genre = _clean_genre_string(raw)
                if genre and len(genre) > 2:
                    return genre
        return ''
    except Exception:
        return ''

def fetch_genre_groq_fallback(artist: str, api_key: str) -> str:
    """Last resort: ask Groq for the genre. Less accurate but covers unknowns."""
    try:
        prompt = (
            f"What is the primary metal or rock subgenre of the band/artist \"{artist}\"?\n"
            f"Reply with ONLY the genre name, nothing else. Examples: Death Metal, Black Metal, "
            f"Thrash Metal, Power Metal, Doom Metal, Heavy Metal, Hard Rock, Progressive Metal, "
            f"Sludge Metal, Stoner Rock, Metalcore, Punk, Gothic Metal, Folk Metal, Symphonic Metal.\n"
            f"If completely unknown, reply: Rock"
        )
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 20, "temperature": 0.05},
            timeout=15
        )
        if r.status_code == 200:
            genre = r.json()['choices'][0]['message']['content'].strip()
            genre = re.sub(r'^["\']|["\']$', '', genre)
            return _clean_genre_string(genre) or 'Rock'
    except Exception:
        pass
    return 'Rock'

def lookup_genre(artist: str, groq_key: str = '') -> str:
    """
    Main genre lookup. Priority:
    1. Cache (avoid duplicate calls)
    2. Metal Archives (most accurate for metal)
    3. Wikipedia (broad coverage)
    4. Groq AI (fallback for unknowns)
    """
    if not artist: return 'Rock'
    key = artist.lower().strip()
    if key in _genre_cache:
        return _genre_cache[key]

    genre = ''

    # 1. Metal Archives
    genre = fetch_genre_metal_archives(artist)
    if genre:
        print(f"    MA: {artist} → {genre}")
        _genre_cache[key] = genre
        return genre
    time.sleep(0.5)  # polite delay between sources

    # 2. Wikipedia
    genre = fetch_genre_wikipedia(artist)
    if genre:
        print(f"    WP: {artist} → {genre}")
        _genre_cache[key] = genre
        return genre

    # 3. Groq fallback
    if groq_key:
        genre = fetch_genre_groq_fallback(artist, groq_key)
        print(f"    AI: {artist} → {genre}")
    else:
        genre = 'Rock'

    _genre_cache[key] = genre
    return genre

def groq_enrich(c):
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key: return c
    if c.get('address') and c.get('price','') not in ('','Consultar') and c.get('buy_link') and c.get('genre'): return c
    prompt = (
        f"Madrid metal concert — fill ONLY missing fields. DO NOT invent. Empty string if unknown.\n"
        f"Artist: {c.get('artist','')} | Date: {c.get('date_str','')} | Venue: {c.get('venue','')}\n"
        f'Respond ONLY with JSON (no markdown):\n'
        f'{{"address":"full street address in Madrid or empty","price":"e.g. 18€ + 2€ gastos or Consultar",'
        f'"buy_link":"direct ticket URL or empty","notes":"e.g. SOLD OUT or empty",'
        f'"genre":"REQUIRED — specific metal/rock subgenre, e.g. Death Metal, Power Metal, Doom Metal, '
        f'Black Metal, Heavy Metal, Thrash Metal, Hard Rock, Punk, Metalcore, Groove Metal, '
        f'Progressive Metal, Alternative Rock, Indie Rock, Nu-Metal, Stoner Rock, Sludge Metal — '
        f'never leave empty, always pick the most accurate one"}}'
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":prompt}],"max_tokens":200,"temperature":0.05},
            timeout=20)
        if r.status_code == 200:
            raw = r.json()['choices'][0]['message']['content'].strip()
            raw = re.sub(r'^```json\s*', '', raw); raw = re.sub(r'\s*```$', '', raw)
            d = json.loads(raw)
            if d.get('address')  and not c.get('address'):                  c['address']  = d['address']
            if d.get('price')    and c.get('price','') in ('','Consultar'): c['price']    = d['price']
            if d.get('buy_link') and not c.get('buy_link'):                 c['buy_link'] = d['buy_link']
            if d.get('notes')    and not c.get('notes'):                    c['notes']    = d['notes']
            # Genre: use real lookup instead of Groq's guess
            if not c.get('genre'):
                c['genre'] = lookup_genre(c.get('artist',''), api_key)
    except: pass
    return c

def fill_missing_genres(concerts: list) -> list:
    """
    For every concert without a genre, look it up in order:
    1. Metal Archives  2. Wikipedia  3. Groq AI fallback
    Uses a cache so each artist is only looked up once per run.
    """
    groq_key = os.getenv('GROQ_API_KEY', '')
    missing = [i for i, c in enumerate(concerts) if not c.get('genre')]
    if not missing:
        return concerts
    print(f"  Buscando géneros para {len(missing)} conciertos...")
    for i in missing:
        artist = concerts[i].get('artist', '')
        if not artist:
            concerts[i]['genre'] = 'Rock'
            continue
        concerts[i]['genre'] = lookup_genre(artist, groq_key)
        time.sleep(0.3)
    return concerts

def email_html(concerts):
    now = datetime.now(); total = len(concerts)
    if not concerts:
        rows = ('<tr><td colspan="4" style="padding:60px;text-align:center;color:#9ca3af;font-size:14px;">Sin conciertos encontrados.</td></tr>')
    else:
        rows = ""
        for i, c in enumerate(concerts, 1):
            artist   = html.escape(clean_artist(c.get('artist', '')))
            lineup_parts = [clean_artist(p.strip()) for p in re.split(r'\s*\+\s*', c.get('lineup', c.get('artist','')))]
            lineup   = html.escape(' + '.join(p for p in lineup_parts if p))
            date_str = c.get('date_str', '—')
            venue    = html.escape(c.get('venue', ''))
            address  = html.escape(c.get('address', ''))
            notes    = html.escape(c.get('notes', ''))
            genre    = html.escape(c.get('genre', ''))
            buy_link = c.get('buy_link', '')
            image_url = c.get('image_url', '') or _initials_svg(c.get('artist', ''))
            support_html = (f'<div style="font-size:11px;color:#94a3b8;margin-top:3px;">{lineup}</div>' if lineup and lineup != artist else '')
            notes_html   = (f'<div style="font-size:11px;color:#f59e0b;font-weight:600;margin-top:4px;">⚠ {notes}</div>') if notes else ''
            addr_html    = (f'<div style="font-size:11px;color:#6b7280;margin-top:2px;">{address}</div>' if address else '')
            genre_html   = (f'<div style="margin-top:5px;">'
                            f'<span style="display:inline-block;font-size:9px;font-weight:700;color:#a78bfa;'
                            f'background:#1e1030;border:1px solid #4c1d95;border-radius:3px;'
                            f'padding:2px 6px;letter-spacing:0.6px;text-transform:uppercase;">'
                            f'{genre}</span></div>') if genre else ''
            name_html = (f'<a href="{html.escape(buy_link)}" target="_blank" style="color:#e2e8f0;font-weight:700;font-size:13px;text-decoration:none;">{artist}</a>') if buy_link else f'<span style="color:#e2e8f0;font-weight:700;font-size:13px;">{artist}</span>'
            bg = '#1c2333' if i % 2 == 0 else '#161d2c'
            rows += (
                f'<tr style="background:{bg};border-bottom:1px solid #252f42;">'
                f'<td style="padding:8px 8px 8px 10px;width:81px;vertical-align:middle;">'
                f'<img src="{html.escape(image_url)}" width="65" height="65" style="border-radius:6px;display:block;object-fit:cover;border:1px solid #2d3a50;"></td>'
                f'<td style="padding:11px 12px;vertical-align:top;">{name_html}{support_html}{genre_html}{notes_html}</td>'
                f'<td style="padding:11px 10px;color:#f59e0b;font-size:12px;white-space:nowrap;width:165px;vertical-align:top;">{date_str}</td>'
                f'<td style="padding:11px 10px;color:#94a3b8;font-size:12px;vertical-align:top;">{venue}{addr_html}</td></tr>'
            )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Conciertos Metal &amp; Rock · Madrid</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:#0d1117; font-family:'Helvetica Neue',Arial,sans-serif; }}
    a {{ color:#e2e8f0; }} a:hover {{ color:#f87171; text-decoration:underline; }}
    .wrap {{ max-width:920px; margin:24px auto; border-radius:8px; overflow:hidden;
             box-shadow:0 4px 24px rgba(0,0,0,0.5); border:1px solid #21293a; }}
    .header {{ background:linear-gradient(160deg,#1a1f2e 0%,#232b3e 60%,#2c1810 100%);
               padding:32px 28px 26px; text-align:center; border-bottom:2px solid #c0392b; }}
    .header-icon {{ font-size:38px; line-height:1; margin-bottom:10px; }}
    .header-title {{ font-size:20px; font-weight:800; color:#e2e8f0; letter-spacing:3px; text-transform:uppercase; }}
    .header-subtitle {{ font-size:11px; color:#64748b; margin-top:5px; letter-spacing:1px; }}
    .stats-bar {{ background:#131924; padding:12px 20px; display:table; width:100%; border-bottom:1px solid #21293a; }}
    .stat-cell {{ display:table-cell; text-align:center; padding:0 12px; border-right:1px solid #21293a; }}
    .stat-cell:last-child {{ border-right:none; }}
    .stat-val {{ font-size:20px; font-weight:800; color:#e2e8f0; line-height:1; }}
    .stat-lbl {{ font-size:10px; color:#4b5563; text-transform:uppercase; letter-spacing:0.6px; margin-top:3px; }}
    .agenda-wrap {{ background:#10151f; padding:18px 18px 24px; }}
    .agenda-title {{ color:#c0392b; font-size:11px; font-weight:700; letter-spacing:2.5px;
                     text-transform:uppercase; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid #21293a; }}
    table {{ width:100%; border-collapse:collapse; }}
    th {{ background:#131924; padding:9px 12px; font-size:9px; font-weight:700;
          color:#374151; text-transform:uppercase; letter-spacing:1px; text-align:left; border-bottom:1px solid #c0392b; }}
    tr:hover td {{ background:#1e293b !important; }}
    .footer {{ background:#0d1117; padding:12px 20px; text-align:center; color:#1f2937; font-size:10px; border-top:1px solid #21293a; }}
  </style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="header-icon">🤘</div>
    <div class="header-title">Conciertos Metal &amp; Rock &middot; Madrid</div>
    <div class="header-subtitle">{now.strftime('%d/%m/%Y')} &mdash; {CUTOFF.strftime('%d/%m/%Y')} &nbsp;&middot;&nbsp; {DAYS_AHEAD} días</div>
  </div>
  <div class="stats-bar">
    <div class="stat-cell"><div class="stat-val">{total}</div><div class="stat-lbl">Conciertos</div></div>
    <div class="stat-cell"><div class="stat-val">21</div><div class="stat-lbl">Fuentes</div></div>
    <div class="stat-cell"><div class="stat-val">{DAYS_AHEAD}</div><div class="stat-lbl">Días vista</div></div>
  </div>
  <div class="agenda-wrap">
    <div class="agenda-title">Agenda</div>
    <table>
      <thead><tr><th style="width:81px;"></th><th>Artista / Cartel</th><th style="width:165px;">Fecha</th><th>Sala</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="footer">Madrid Metal &amp; Rock &nbsp;&middot;&nbsp; 21 fuentes &nbsp;&middot;&nbsp; Groq AI &nbsp;&middot;&nbsp; Verifica disponibilidad antes de comprar</div>
</div>
</body>
</html>"""


def export_json(concerts: list):
    """Write concerts.json for the weekly-hub GitHub Pages site."""
    os.makedirs('data', exist_ok=True)
    payload = {
        'generated': datetime.now().isoformat(),
        'from_date': TODAY.strftime('%Y-%m-%d'),
        'to_date':   CUTOFF.strftime('%Y-%m-%d'),
        'concerts': [
            {
                'artist':    c.get('artist', ''),
                'lineup':    c.get('lineup', ''),
                'date_str':  c.get('date_str', ''),
                'date_iso':  c['date_obj'].strftime('%Y-%m-%d') if c.get('date_obj') else '',
                'venue':     c.get('venue', ''),
                'address':   c.get('address', ''),
                'price':     c.get('price', ''),
                'buy_link':  c.get('buy_link', ''),
                'genre':     c.get('genre', ''),
                'notes':     c.get('notes', ''),
                'image_url': c.get('image_url', ''),
            }
            for c in concerts
        ]
    }
    with open('data/concerts.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  Escrito data/concerts.json ({len(concerts)} conciertos)")

def send_email(subject, html_content):
    s = os.getenv('GMAIL_EMAIL'); p = os.getenv('GMAIL_PASSWORD'); r = os.getenv('RECIPIENT_EMAIL')
    if not all([s, p, r]): print("ERROR: Faltan secrets de Gmail"); return False
    msg = MIMEMultipart('alternative')
    msg['From'] = s; msg['To'] = r; msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context()) as sv:
            sv.login(s, p); sv.send_message(msg)
        print("✅ Email enviado"); return True
    except Exception as e:
        print(f"❌ {e}"); return False

def main():
    print("=" * 65)
    print("🤘 MADRID METAL & ROCK CONCERTS")
    print(f"   {TODAY.strftime('%d/%m/%Y')} → {CUTOFF.strftime('%d/%m/%Y')}")
    print("=" * 65)
    print("\n📡 PASO 1: Scraping...")
    all_c = []
    for fn in [
        scrape_todoheavymetal, scrape_hellpress, scrape_mariskalrock,
        scrape_metalcry, scrape_metaltrip, scrape_metalhammer,
        scrape_concerts_metal, scrape_tntradiorock, scrape_conciertospormadrid,
        scrape_rafabasa, scrape_rockandblog, scrape_directoriorock,
        scrape_madnesslive, scrape_taquilla,
        scrape_lariviera, scrape_salacaracol, scrape_warlitzer,
        scrape_elsol, scrape_copernico, scrape_ticketmaster, scrape_songkick,
    ]:
        try: all_c.extend(fn())
        except Exception as e: print(f"  ✗ {fn.__name__}: {e}")
        time.sleep(1)
    print(f"\n  Total bruto: {len(all_c)}")
    valid  = [c for c in all_c if future(c.get('date_obj'))]
    unique = dedup(valid)
    print(f"  Tras dedup: {len(unique)} (eliminados {len(valid)-len(unique)})")
    unique.sort(key=lambda x: x.get('date_obj') or datetime.max)
    for c in unique:
        c['artist'] = clean_artist(c.get('artist', ''))
        c['lineup'] = clean_artist(c.get('lineup', ''))
    print(f"\n✨ PASO 2: Groq AI ({len(unique)} conciertos)...")
    enriched = []
    for i, c in enumerate(unique, 1):
        enriched.append(groq_enrich(c))
        if i % 10 == 0: time.sleep(1)
    print(f"\n🎸 PASO 3: Géneros faltantes...")
    enriched = fill_missing_genres(enriched)
    print(f"\n🖼  PASO 4: Imágenes...")
    enriched = enrich_images(enriched, os.getenv('LASTFM_API_KEY', ''))
    print(f"\n💾 PASO 5: JSON export para weekly-hub...")
    export_json(enriched)

    print(f"\n✅ COMPLETADO — {len(enriched)} conciertos")

if __name__ == '__main__':
    main()
