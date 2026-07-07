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
"""

PROMPT = """Eres un experto en ocio adulto en Madrid. Analiza este contenido scrapeado.

RECHAZA (is_real_event: false) si es cualquiera de estos casos — sé estricto:
- Artículo de lista genérico: "los mejores parques", "10 cosas que hacer", "qué hacer esta semana"
- Columna o serie de artículos de alguien: "artículos de X", "reportero de Y", "columna de"
- Contenido infantil o para niños: parques infantiles, ludotecas, talleres para niños, cuentacuentos
- Colección permanente de museo (no una exposición temporal). Ejemplo: "La colección Thyssen", "Obras del siglo XIX"
- Sección administrativa de web: visita libre, visita en grupo, datos de interés, información práctica, precios, horarios, cómo llegar
- Pregunta SEO sin evento: "¿cuánto cuesta?", "¿dónde está?", "¿cómo comprar entradas?"
- Eventos de baile, música, concierto, verbena, flashmob, festival de música
- Deporte, running, yoga, meditación, spa
- Religioso: iglesias, catedrales, misas, retiros espirituales
- Política, manifestaciones, actos reivindicativos
- Título es un slogan, tagline o descripción de marketing, no el nombre real del evento

ACEPTA solo eventos de ocio adulto concretos, temporales y asistibles.

{category_guide}

Título: {title}
Descripción scrapeada: {description}
Fuente: {source}

Responde SOLO con este JSON exacto (sin markdown, sin texto extra):
{{
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
}}"""


class AIEnricher:
    def __init__(self):
        self.api_key = os.getenv('GROQ_API_KEY')
        self.enabled = bool(self.api_key)
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

        logger.info(f"AI analizando {len(events)} eventos...")
        enriched = []
        rejected = 0

        for i, event in enumerate(events, 1):
            logger.info(f"  [{i}/{len(events)}] {event.get('titulo', '')[:60]}...")
            if i > 1:
                time.sleep(1)

            result = self._call_groq(event)

            if result is None:
                # API failed — keep with defaults so we don't lose events
                event['emoji']  = CATEGORY_EMOJIS.get(event.get('categoria', 'Evento'), '📌')
                event['points'] = []
                event['why_go'] = ''
                enriched.append(event)
                continue

            if not result.get('is_real_event', True):
                logger.info(f"    ✗ Rechazado: {result.get('reject_reason', '')}")
                rejected += 1
                continue

            # Apply enrichments
            if result.get('clean_title', '').strip():
                event['titulo'] = result['clean_title'].strip()
            cat = result.get('category', '').strip() or event.get('categoria', 'Evento')
            event['categoria'] = cat
            event['emoji']     = CATEGORY_EMOJIS.get(cat, '📌')

            # Ensure points is always a non-empty list of strings
            raw_points = result.get('points', [])
            if isinstance(raw_points, list) and raw_points:
                event['points'] = [str(p).strip() for p in raw_points if str(p).strip()]
            else:
                event['points'] = []

            event['why_go'] = result.get('why_go', '').strip()

            # Fill missing dates from AI extraction
            if not event.get('fecha_inicio') and result.get('fecha_inicio', '').strip():
                event['fecha_inicio'] = result['fecha_inicio'].strip()
            if not event.get('fecha_fin') and result.get('fecha_fin', '').strip():
                event['fecha_fin'] = result['fecha_fin'].strip()

            enriched.append(event)

        logger.info(f"✓ Resultado AI: {len(enriched)} válidos, {rejected} rechazados")
        return enriched

    def _call_groq(self, event: dict) -> dict | None:
        prompt = PROMPT.format(
            category_guide=CATEGORY_GUIDE,
            title=event.get('titulo', '')[:200],
            description=event.get('descripcion', '')[:800],
            source=event.get('fuente', ''),
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 450,
            "temperature": 0.1,
        }
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                text = resp.json()['choices'][0]['message']['content'].strip()
                # Strip markdown fences if present
                text = text.replace('```json', '').replace('```', '').strip()
                return json.loads(text)
            else:
                logger.warning(f"    Groq {resp.status_code}: {resp.text[:100]}")
                return None
        except json.JSONDecodeError as e:
            logger.warning(f"    JSON parse error: {e}")
            return None
        except Exception as e:
            logger.warning(f"    Error: {e}")
            return None
