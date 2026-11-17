"""Genera un sito statico per GitHub Pages, senza backend né chiavi esposte."""
import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "data/items.json"
MILESTONES = ROOT / "config/milestones.json"
DEST = ROOT / "docs/index.html"


def build() -> None:
    items = json.loads(ITEMS.read_text(encoding="utf-8")) if ITEMS.exists() else []
    milestones = json.loads(MILESTONES.read_text(encoding="utf-8"))
    # Escape '<' in serialized JSON to prevent script tag breakout.
    payload = json.dumps({"items": items, "milestones": milestones}, ensure_ascii=False).replace("<", "\\u003c")
    template = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    template = template.replace("__DATA_JSON__", payload)
    template = template.replace("__GENERATED_AT__", html.escape(datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")))
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(template, encoding="utf-8")
    print(f"Dashboard creata: {DEST} ({len(items)} notizie)")


if __name__ == "__main__":
    build()
