"""Parse literature search results for PSUR Section J.

Input format (see data/templates/literature_template.csv):
    article_id, title, authors, journal, publication_date, database,
    search_terms, relevance, findings_summary, safety_signal

The parser is fully deterministic — no LLM calls.
"""
import csv
from pathlib import Path
from typing import Any, Dict, List


_TRUTHY = {"yes", "y", "true", "1", "relevant", "included"}


def parse_literature(filepath: Path) -> Dict[str, Any]:
    """Parse a literature search results CSV into a structured dict.

    Returns:
        {
          "total_articles": int,
          "relevant_articles": int,
          "databases_searched": [str],
          "search_terms": [str],
          "articles": [ {column: value} ],
          "safety_signals": [ {article_id, title, findings_summary} ],
        }
    """
    filepath = Path(filepath)
    articles: List[Dict[str, str]] = []
    with open(filepath, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cleaned = {
                (k or "").strip(): (v or "").strip()
                for k, v in row.items()
                if k is not None
            }
            if any(cleaned.values()):
                articles.append(cleaned)

    databases: List[str] = []
    search_terms: List[str] = []
    relevant = 0
    safety_signals: List[Dict[str, str]] = []

    for art in articles:
        db = art.get("database", "")
        if db and db not in databases:
            databases.append(db)
        for term in (art.get("search_terms", "") or "").split(";"):
            term = term.strip()
            if term and term not in search_terms:
                search_terms.append(term)
        if (art.get("relevance", "") or "").strip().lower() in _TRUTHY:
            relevant += 1
        if (art.get("safety_signal", "") or "").strip().lower() in _TRUTHY:
            safety_signals.append({
                "article_id": art.get("article_id", ""),
                "title": art.get("title", ""),
                "findings_summary": art.get("findings_summary", ""),
            })

    return {
        "total_articles": len(articles),
        "relevant_articles": relevant,
        "databases_searched": databases,
        "search_terms": search_terms,
        "articles": articles,
        "safety_signals": safety_signals,
        "source_file": filepath.name,
    }
