"""Pipeline gratuita: feed ufficiali → filtro trasparente → archivio → digest."""
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
SOURCE_STATUS = ROOT / "data" / "source_status.json"
AI_TERMS = re.compile(r"\b(ai act|artificial intelligence|intelligenza artificiale|machine learning|generative ai|general.purpose ai|foundation model|modelli? di ia|sistemi? di ia|algoritm\w*|biometri\w*|2024/1689|(?-i:AI)|(?-i:IA))\b", re.I)
DIGITAL_TERMS = re.compile(r"\b(gdpr|rgpd|privacy|data protection|protezione dei dati|data act|data governance act|digital services act|digital markets act|digital omnibus|digital fairness act|digital rulebook|eprivacy|nis\s?2|cybersecurity|cyber security|cybersicurezza|cibersicurezza|sicurezza informatica|cyber resilience act|data breach|violazione dei dati|regolamentazione digitale|normativa digitale|digital regulation|digital legislation|piattaforme digitali|mercati digitali|proposta legislativa.{0,35}digital|digital.{0,35}legislative proposal|2016/679|2023/2854|2022/2065|2022/1925|2022/2555)\b", re.I)
ACT_PATTERNS = {
    "AI Act": r"\b(ai act|artificial intelligence act|regolamento.{0,20}intelligenza artificiale|2024/1689)\b",
    "GDPR": r"\b(gdpr|rgpd|2016/679|general data protection regulation)\b",
    "Data Act": r"\b(data act|2023/2854)\b",
    "DSA": r"\b(digital services act|2022/2065|\bdsa\b)\b",
    "DMA": r"\b(digital markets act|2022/1925|\bdma\b)\b",
    "NIS2": r"\b(nis2|nis 2|2022/2555)\b",
    "Cyber Resilience Act": r"\b(cyber resilience act|2024/2847)\b",
    "Data Governance Act": r"\b(data governance act|2022/868)\b",
}
TOPIC_PATTERNS = {
    "Privacy": r"\b(gdpr|rgpd|privacy|data protection|protezione dei dati|dati personali|personal data|data breach|violazione dei dati|2016/679)\b",
    "Governance dei dati": r"\b(data act|data governance act|2023/2854|2022/868|data sharing|condivisione dei dati|data spaces)\b",
    "Piattaforme digitali": r"\b(digital services act|\bdsa\b|2022/2065|online platforms?|piattaforme (online|digitali)|servizi digitali)\b",
    "Mercati digitali": r"\b(digital markets act|\bdma\b|2022/1925|gatekeepers?|mercati digitali)\b",
    "Cybersecurity": r"\b(nis\s?2|2022/2555|cybersecurity|cyber security|sicurezza informatica|cyber resilience act|2024/2847|resilienza informatica)\b",
    "Novità legislative": r"\b(proposta legislativa|legislative proposal|proposta di regolamento|proposed regulation|commission proposal|commission proposes|digital omnibus|consultazione pubblica|public consultation)\b",
}
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


def fetch_sources(statuses: dict | None = None) -> list[dict]:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    gathered = []
    for source in sources:
        if not source.get("enabled") or not source.get("url"):
            continue
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            response = requests.get(source["url"], timeout=25, headers={"User-Agent": "AI-Regulatory-Radar/1.0 (portfolio project)"})
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if not feed.entries:
                raise ValueError("Feed vuoto o formato non riconosciuto")
            if statuses is not None:
                statuses[source["name"]] = {"status": "ok", "last_checked": checked_at,
                                              "last_success": checked_at}
            count = 0
            for entry in feed.entries[:75]:
                link = canonical_link(entry.get("link", ""))
                if not link:
                    continue
                title = plain(entry.get("title", ""), 350)
                summary = plain(entry.get("summary", ""))
                if not is_relevant(title + " " + summary):
                    continue
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                date = f"{published.tm_year:04d}-{published.tm_mon:02d}-{published.tm_mday:02d}" if published else ""
                gathered.append({"id": item_id(link), "source": source["name"], "title": title,
                                 "published": date, "summary": summary, "link": link})
                count += 1
            LOG.info("%s: %d voci pertinenti", source["name"], count)
        except Exception as exc:
            LOG.warning("Fonte non disponibile (%s): %s", source["name"], exc)
            if statuses is not None:
                previous = statuses.get(source["name"], {})
                statuses[source["name"]] = {"status": "error", "last_checked": checked_at,
                                              "last_success": previous.get("last_success")}
    return gathered


def identify_acts(entry: dict) -> list[str]:
    """Etichette indicative solo da corrispondenze testuali, non analisi legale."""
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    return [act for act, pattern in ACT_PATTERNS.items() if re.search(pattern, text, re.I)]


def is_relevant(text: str) -> bool:
    return bool(AI_TERMS.search(text) or DIGITAL_TERMS.search(text))


def identify_areas(entry: dict) -> list[str]:
    """Macroaree indicative: una notizia può essere pertinente a entrambe."""
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    areas = []
    if AI_TERMS.search(text):
        areas.append("Intelligenza artificiale")
    if DIGITAL_TERMS.search(text):
        areas.append("Altre normative digitali")
    return areas


def identify_topics(entry: dict) -> list[str]:
    """Sette ambiti descrittivi individuati per parole chiave, senza giudizi giuridici."""
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    topics = ["Intelligenza artificiale"] if AI_TERMS.search(text) else []
    topics.extend(name for name, pattern in TOPIC_PATTERNS.items() if re.search(pattern, text, re.I))
    return topics


def digest_text(items: list[dict]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"AI Regulatory Radar — Digest {today}", "", "Elenco di pubblicazioni ufficiali. Non è consulenza legale.", ""]
    for item in items:
        acts = ", ".join(item.get("acts", [])) or "Non identificato"
        lines.extend([f"• {item['title']}", f"  Fonte: {item['source']} | Tema indicativo: {acts}",
                      f"  Pubblicazione: {item.get('published') or 'Data non indicata'}", f"  Fonte ufficiale: {item['link']}", ""])
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


def run(dry_run: bool = False, skip_notify: bool = False) -> int:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    archive = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    known = {item["id"] for item in archive}
    statuses = json.loads(SOURCE_STATUS.read_text(encoding="utf-8")) if SOURCE_STATUS.exists() else {}
    fresh = [entry for entry in fetch_sources(statuses) if entry["id"] not in known]
    fresh = list({entry["id"]: entry for entry in fresh}.values())
    LOG.info("Nuove voci: %d", len(fresh))
    if dry_run:
        return len(fresh)
    SOURCE_STATUS.write_text(json.dumps(statuses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for entry in fresh:
        entry["acts"] = identify_acts(entry)
        entry["areas"] = identify_areas(entry)
        entry["topics"] = identify_topics(entry)
        entry["seen_at"] = datetime.now(timezone.utc).isoformat()
    # Migrazione automatica dei dati della versione precedente, senza rifare il fetch.
    for entry in archive:
        entry["acts"] = identify_acts(entry)
        entry["areas"] = identify_areas(entry)
        entry["topics"] = identify_topics(entry)
        entry.pop("classification", None)
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"Nuove voci: {run(args.dry_run, args.skip_notify)}")
