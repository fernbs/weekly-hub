import os
import json
import logging
from datetime import datetime
from pathlib import Path
import pytz
from colorama import Fore, Style, init

init(autoreset=True)
MADRID_TZ = pytz.timezone('Europe/Madrid')

def setup_logging(name: str = "madrid-plans") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

def load_config(path: str) -> dict:
    import yaml
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def load_json(path: str) -> dict:
    if not Path(path).exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(data: dict, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_madrid_now() -> datetime:
    return datetime.now(MADRID_TZ)

def parse_date(date_str: str) -> datetime:
    from dateutil import parser
    if not date_str:
        return None
    try:
        dt = parser.parse(date_str, fuzzy=True, dayfirst=True)
        if dt.tzinfo is None:
            dt = MADRID_TZ.localize(dt)
        return dt
    except:
        return None

def clean_text(text: str) -> str:
    if not text:
        return ""
    import re
    return re.sub(r'\s+', ' ', text).strip()

def normalize_url(url: str, base: str = "") -> str:
    from urllib.parse import urljoin
    if base and not url.startswith('http'):
        return urljoin(base, url)
    return url

def get_week_number() -> int:
    return get_madrid_now().isocalendar()[1]

def get_env_var(name: str, required: bool = False) -> str:
    value = os.getenv(name)
    if required and not value:
        raise ValueError(f"{name} is required")
    return value
