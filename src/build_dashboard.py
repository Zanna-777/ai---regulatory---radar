"""Genera un sito statico per GitHub Pages, senza backend né chiavi esposte."""
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from src.pipeline import identify_topics

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "data/items.json"
MILESTONES = ROOT / "config/milestones.json"
SOURCES = ROOT / "config/sources.json"
SOURCE_STATUS = ROOT / "data/source_status.json"
DEST = ROOT / "docs/index.html"


def build() -> None:
    items = json.loads(ITEMS.read_text(encoding="utf-8")) if ITEMS.exists() else []
    # Preserve the existing archive: label older entries in memory until the next pipeline run.
    for item in items:
        item["topics"] = identify_topics(item)
    milestones = json.loads(MILESTONES.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    statuses = json.loads(SOURCE_STATUS.read_text(encoding="utf-8")) if SOURCE_STATUS.exists() else {}
    # Escape '<' in serialized JSON to prevent script tag breakout.
    payload = json.dumps({"items": items, "milestones": milestones, "sources": sources,
                          "source_status": statuses}, ensure_ascii=False).replace("<", "\\u003c")
    template = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    template = template.replace("__DATA_JSON__", payload)
    template = template.replace("__GENERATED_AT__", html.escape(datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")))
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(template, encoding="utf-8")
    print(f"Dashboard creata: {DEST} ({len(items)} notizie)")


if __name__ == "__main__":
    build()
