"""Pipeline giornaliera: feed ufficiali → classificazione → archivio → digest."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "items.json"
SOURCES = ROOT / "config" / "sources.json"
PROMPT = ROOT / "prompts" / "classification_it.md"
KEYWORDS = re.compile(r"\b(ai act|artificial intelligence|intelligenza artificiale|\bai\b|gdpr|privacy|data protection|protezione dei dati|data act|digital services act|digital markets act|nis2|algorithm|algoritm|machine learning|automated decision|decisioni automatizzate|biometr|general.purpose|modelli di ia)\b", re.I)
ALLOWED_ACTS = {"AI Act", "GDPR", "Data Act", "DSA", "DMA", "NIS2", "Altro"}
ALLOWED_URGENCY = {"alta", "media", "bassa", "da_verificare"}
LOG = logging.getLogger("radar")


def canonical_link(link: str) -> str:
    parts = urlsplit(link.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def item_id(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:20]


def plain(text: str, limit: int = 1600) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text or "")).strip()[:limit]


def fetch_sources() -> list[dict]:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    gathered = []
    for source in sources:
        if not source.get("enabled") or not source.get("url"):
            continue
        try:
            response = requests.get(source["url"], timeout=25, headers={"User-Agent": "AI-Regulatory-Radar/1.0 (portfolio project)"})
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if not feed.entries:
                raise ValueError("Feed vuoto o formato non riconosciuto")
            count = 0
            for entry in feed.entries[:75]:
                link = canonical_link(entry.get("link", ""))
                if not link:
                    continue
                title = plain(entry.get("title", ""), 350)
                summary = plain(entry.get("summary", ""))
                if not KEYWORDS.search(title + " " + summary):
                    continue
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                date = f"{published.tm_year:04d}-{published.tm_mon:02d}-{published.tm_mday:02d}" if published else ""
                gathered.append({"id": item_id(link), "source": source["name"], "title": title,
                                 "published": date, "summary": summary, "link": link})
                count += 1
            LOG.info("%s: %d voci pertinenti", source["name"], count)
        except (requests.RequestException, ValueError, Exception) as exc:
            LOG.warning("Fonte non disponibile (%s): %s", source["name"], exc)
    return gathered


def pending_classification() -> dict:
    return {"relevant": True, "act": "Altro", "type": "altro",
            "summary_it": "Sintesi non disponibile: in attesa di classificazione e verifica.",
            "what_changes": "Da verificare sulla fonte ufficiale",
            "applies_to": "Da verificare sulla fonte ufficiale",
            "urgency": "da_verificare", "urgency_reason": "Classificazione non ancora verificata",
            "legal_effect_confirmed": False, "review_required": True}


def classify(entry: dict, client=None) -> dict:
    if client is None:
        return pending_classification()
    instructions = PROMPT.read_text(encoding="utf-8")
    try:
        answer = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": instructions},
                      {"role": "user", "content": json.dumps(entry, ensure_ascii=False)}],
        )
        result = json.loads(answer.choices[0].message.content)
        required = set(pending_classification())
        if not required.issubset(result) or result["act"] not in ALLOWED_ACTS or result["urgency"] not in ALLOWED_URGENCY:
            raise ValueError("Risposta JSON incompleta o con valori non consentiti")
        for key in ("summary_it", "what_changes", "applies_to", "urgency_reason", "type"):
            if not isinstance(result[key], str):
                raise ValueError(f"Campo non valido: {key}")
            result[key] = plain(result[key], 900)
        result["review_required"] = True  # La sintesi non è verificata da un giurista.
        return result
    except Exception as exc:
        LOG.warning("Classificazione fallita per %s: %s", entry["id"], exc)
        return pending_classification()


def digest_text(items: list[dict]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"AI Regulatory Radar — Digest {today}", "", "Sintesi automatizzate da verificare sulle fonti ufficiali. Non è consulenza legale.", ""]
    for item in items:
        c = item["classification"]
        lines.extend([f"• {item['title']}", f"  Fonte: {item['source']} | Atto: {c['act']} | Urgenza: {c['urgency']}",
                      f"  {c['summary_it']}", f"  Fonte ufficiale: {item['link']}", ""])
    return "\n".join(lines)


def notify(items: list[dict]) -> None:
    if not items:
        return
    digest = digest_text(items)
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        # Telegram limita la lunghezza dei messaggi; invio in blocchi per voce.
        for item in items:
            message = digest_text([item])[:3900]
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True}, timeout=20)
            resp.raise_for_status()
    if all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")):
        msg = EmailMessage()
        msg["Subject"] = "AI Regulatory Radar — nuovi aggiornamenti"
        msg["From"] = os.environ["EMAIL_FROM"]
        msg["To"] = os.environ["EMAIL_TO"]
        msg.set_content(digest)
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=25) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(msg)


def run(dry_run: bool = False, skip_notify: bool = False, reclassify_pending: bool = False) -> int:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    archive = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    known = {item["id"] for item in archive}
    fresh = [entry for entry in fetch_sources() if entry["id"] not in known]
    fresh = list({entry["id"]: entry for entry in fresh}.values())
    LOG.info("Nuove voci: %d", len(fresh))
    if dry_run:
        return len(fresh)
    client = None
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=35, max_retries=2)
    for entry in fresh:
        entry["classification"] = classify(entry, client)
        entry["seen_at"] = datetime.now(timezone.utc).isoformat()
    if reclassify_pending and client is not None:
        for entry in archive:
            if entry.get("classification", {}).get("urgency") == "da_verificare":
                entry["classification"] = classify(entry, client)
    archive = sorted(fresh + archive, key=lambda x: (x.get("published") or "", x.get("seen_at") or ""), reverse=True)
    DATA.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not skip_notify:
        try:
            notify(fresh)
        except Exception as exc:
            LOG.error("Notifica fallita (archivio salvato): %s", exc)
    return len(fresh)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-notify", action="store_true")
    parser.add_argument("--reclassify-pending", action="store_true", help="Riprova la classificazione delle voci in attesa usando la API AI")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"Nuove voci: {run(args.dry_run, args.skip_notify, args.reclassify_pending)}")
