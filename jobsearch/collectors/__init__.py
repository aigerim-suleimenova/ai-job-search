"""Сборщики вакансий. Каждый возвращает список словарей:
{title, company, location, url, description, source, is_direct}
"""
from . import aggregators, ats, crawler  # noqa: F401
