import sys
from src.utils import setup_logging, save_json, get_madrid_now
from src.scraper import EventScraper
from src.normalizer import EventNormalizer
from src.filters import EventFilter
from src.deduplicator import EventDeduplicator
from src.ai_enricher import AIEnricher
from src.email_generator import export_plans_json

logger = setup_logging("main")


def main():
    logger.info("=" * 60)
    logger.info("INICIANDO AUTOMATIZACION MADRID PLANS")
    logger.info("=" * 60)

    try:
        logger.info("\n[1/7] SCRAPING")
        scraper = EventScraper()
        raw_events = scraper.scrape_all()
        if not raw_events:
            logger.warning("Sin eventos extraidos. Abortando.")
            return 1

        logger.info("\n[2/7] NORMALIZACION")
        normalizer = EventNormalizer()
        normalized = normalizer.normalize_events(raw_events)

        logger.info("\n[3/7] FILTROS DE KEYWORDS")
        event_filter = EventFilter()
        filtered = event_filter.filter_events(normalized)

        logger.info("\n[4/7] DEDUPLICACION")
        deduplicator = EventDeduplicator()
        deduplicated = deduplicator.deduplicate(filtered)

        logger.info("\n[5/7] ENRIQUECIMIENTO CON AI")
        enricher = AIEnricher()
        enriched = enricher.enrich_events(deduplicated)

        logger.info("\n[6/7] GUARDANDO")
        now = get_madrid_now()
        save_json(enriched, f"data/events_{now.strftime('%Y-%m')}.json")
        deduplicator.update_history(enriched)

        logger.info("\n[6b/7] EXPORTANDO JSON PARA WEEKLY-HUB")
        export_plans_json(enriched)

        logger.info("\n[7/7] JSON generado y empujado al hub")

        logger.info("\n" + "=" * 60)
        logger.info("COMPLETADO EXITOSAMENTE")
        logger.info("=" * 60)
        return 0

    except Exception as e:
        logger.error(f"ERROR CRITICO: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
