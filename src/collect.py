#!/usr/bin/env python3
"""Collect authorized public RSS/Atom feeds and build the static site's data."""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone, tzinfo
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    PARIS = ZoneInfo("Europe/Paris")
except (ImportError, ZoneInfoNotFoundError):
    class ParisTimezone(tzinfo):
        @staticmethod
        def _last_sunday(year: int, month: int) -> date:
            last_day = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
            return last_day - timedelta(days=(last_day.weekday() + 1) % 7)

        def utcoffset(self, value: datetime | None) -> timedelta:
            return timedelta(hours=1) + self.dst(value)

        def dst(self, value: datetime | None) -> timedelta:
            if value is None:
                return timedelta(0)
            local = value.replace(tzinfo=None)
            start = datetime.combine(self._last_sunday(local.year, 3), datetime.min.time()).replace(hour=2)
            end = datetime.combine(self._last_sunday(local.year, 10), datetime.min.time()).replace(hour=3)
            return timedelta(hours=1) if start <= local < end else timedelta(0)

        def tzname(self, value: datetime | None) -> str:
            return "CEST" if self.dst(value) else "CET"

        def fromutc(self, value: datetime) -> datetime:
            utc_value = value.replace(tzinfo=None)
            start = datetime.combine(self._last_sunday(utc_value.year, 3), datetime.min.time()).replace(hour=1)
            end = datetime.combine(self._last_sunday(utc_value.year, 10), datetime.min.time()).replace(hour=1)
            offset = 2 if start <= utc_value < end else 1
            return (utc_value + timedelta(hours=offset)).replace(tzinfo=self)

    PARIS = ParisTimezone()

LOG = logging.getLogger("veille66")
MAX_FEED_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_ENTRIES_PER_FEED = 200
MAX_SUMMARY_LENGTH = 420
CATEGORIES = {"marketing", "evenementiel", "institutionnel", "emploi", "autre"}

KEYWORDS = {
    "marketing": (
        "communication terrain", "street marketing", "marketing de proximite",
        "communication", "campagne de communication", "publicite",
        "promotion commerciale", "commerce de proximite", "commercant",
        "animation commerciale", "echantillonnage", "distribution de prospectus",
        "promotion commerciale", "publicite sur lieu de vente", "plv", "affichage",
        "promotion des ventes", "force de vente",
    ),
    "evenementiel": (
        "evenementiel", "festival", "salon", "concert", "inauguration",
        "animation", "evenement", "manifestation", "foire",
    ),
    "institutionnel": (
        "pyrenees orientales", "perpignan", "collectivite", "commune", "mairie",
        "conseil departemental", "service public", "mediation", "tourisme",
    ),
    "emploi": (
        "recrutement", "emploi", "appel d offres", "marche public", "offre d emploi",
        "animateur", "distributeur", "entreprise", "professionnel", "salarie",
        "commerçant", "commercant", "auto entrepreneur", "micro entrepreneur",
        "activite partielle", "aide aux entreprises", "aide exceptionnelle",
    ),
}
LOCAL_PLACES = (
    "Pyrénées-Orientales", "Perpignan", "Canet-en-Roussillon", "Saint-Cyprien",
    "Argelès-sur-Mer", "Céret", "Prades", "Collioure", "Thuir", "Rivesaltes",
    "Elne", "Banyuls-sur-Mer", "Le Barcarès", "Font-Romeu",
)


class TextExtractor(HTMLParser):
    """Extract readable text from short HTML feed descriptions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.hidden_depth += 1
        elif tag.lower() in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag.lower() in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def clean_text(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    parser = TextExtractor()
    parser.feed(value)
    parser.close()
    text = " ".join(" ".join(parser.parts).split())
    text = html.unescape(text).replace("\x00", "").strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def first_text(element: ET.Element, *names: str) -> str:
    expected = {name.lower() for name in names}
    for child in element.iter():
        if child is not element and local_name(child.tag) in expected:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_feed(content: bytes) -> list[dict[str, str]]:
    if b"<!doctype" in content.lower() or b"<!entity" in content.lower():
        raise ValueError("Les déclarations DTD/ENTITY ne sont pas acceptées dans les flux.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Flux XML invalide : {exc}") from exc

    items = [element for element in root.iter() if local_name(element.tag) in {"item", "entry"}]
    output: list[dict[str, str]] = []
    for item in items[:MAX_ENTRIES_PER_FEED]:
        title = clean_text(first_text(item, "title"), 300)
        link = ""
        for element in item.iter():
            if local_name(element.tag) != "link":
                continue
            href = element.attrib.get("href")
            rel = element.attrib.get("rel", "alternate")
            candidate = href if href and rel == "alternate" else "".join(element.itertext()).strip()
            if candidate:
                link = candidate
                break
        link = html.unescape(link).strip()
        summary = clean_text(first_text(item, "description", "summary", "content", "encoded"), MAX_SUMMARY_LENGTH)
        published = parse_date(first_text(item, "published", "pubdate", "date", "updated", "issued"))
        guid = clean_text(first_text(item, "guid", "id"), 300)
        categories = [
            clean_text("".join(element.itertext()), 60)
            for element in item.iter() if local_name(element.tag) == "category"
        ]
        parsed_link = urllib.parse.urlparse(link)
        if title and link and parsed_link.scheme in {"http", "https"} and parsed_link.hostname and not parsed_link.username and not parsed_link.password:
            output.append({
                "title": title,
                "url": link,
                "summary": summary,
                "publishedAt": published or "",
                "guid": guid,
                "feedCategories": ", ".join(filter(None, categories)),
            })
    return output


def normalized(value: str) -> str:
    import unicodedata

    text = "".join(
        character for character in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def score_entry(item: dict[str, str], source: dict[str, Any], now: datetime) -> tuple[int, str, list[str], str]:
    text = normalized(" ".join((item.get("title", ""), item.get("summary", ""), item.get("feedCategories", ""))))
    matches: dict[str, int] = {}
    tags: list[str] = []
    for category, keywords in KEYWORDS.items():
        found = [keyword for keyword in keywords if normalized(keyword) in text]
        if found:
            matches[category] = len(found)
            tags.extend(found[:2])

    configured_category = source.get("category", "autre")
    category = max(matches, key=matches.get) if matches else configured_category
    location = next(
        (place for place in LOCAL_PLACES if normalized(place) in text),
        str(source.get("location", "")),
    )
    if not matches:
        return 0, category, [], location
    score = 8 if matches else 0
    score += min(45, sum(min(count, 4) * 7 for count in matches.values()))
    source_location = normalized(str(source.get("location", "")))
    if "66" in source_location or "pyrenees orientales" in source_location:
        score += 18
    elif any(normalized(place) in source_location for place in LOCAL_PLACES[1:]):
        score += 14
    if any(normalized(place) in text for place in LOCAL_PLACES):
        score += 22
    published = item.get("publishedAt")
    if published:
        age_days = max(0, (now - datetime.fromisoformat(published.replace("Z", "+00:00"))).days)
        score += 15 if age_days <= 7 else 9 if age_days <= 30 else 4 if age_days <= 90 else 0
    else:
        score += 3
    return min(score, 100), category, list(dict.fromkeys(tags)), location


def validate_source(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError("Chaque source doit être un objet JSON.")
    required = (
        "id", "name", "author", "site_name", "apa_title", "url",
        "category", "terms_url", "terms_reviewed_at",
    )
    missing = [key for key in required if not str(source.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Champs manquants : {', '.join(missing)}.")
    if source.get("active", True) is not True:
        raise ValueError("La source est désactivée.")
    if source["category"] not in CATEGORIES:
        raise ValueError(f"Catégorie non prise en charge : {source['category']}.")
    if source.get("terms_authorized") is not True:
        raise ValueError("terms_authorized doit être true après vérification des conditions du flux.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(source["id"])):
        raise ValueError("id doit contenir 1 à 64 caractères minuscules, chiffres, tirets ou underscores.")
    try:
        date.fromisoformat(str(source["terms_reviewed_at"]))
    except ValueError as exc:
        raise ValueError("terms_reviewed_at doit être une date ISO (AAAA-MM-JJ).") from exc
    for key in ("url", "terms_url"):
        parsed = urllib.parse.urlparse(str(source[key]))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"{key} doit être une URL HTTPS publique.")
        if parsed.username or parsed.password:
            raise ValueError(f"{key} ne doit pas contenir d’identifiants.")
        if parsed.port not in (None, 443):
            raise ValueError(f"{key} doit utiliser le port HTTPS public standard 443.")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "metadata.google.internal"} or hostname.endswith((".localhost", ".local", ".internal")):
            raise ValueError(f"{key} ne peut pas cibler un hôte local.")
        try:
            address = ipaddress.ip_address(hostname)
            if not address.is_global:
                raise ValueError(f"{key} ne peut pas cibler une adresse IP privée.")
        except ValueError as exc:
            if "ne peut pas cibler" in str(exc):
                raise
    return source


def validate_public_target(value: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Les flux et redirections doivent rester des URL HTTPS publiques.")
    host = parsed.hostname.lower().rstrip(".")
    if parsed.port not in (None, 443) or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Cible de collecte non publique refusée.")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0].split("%", 1)[0])
                for result in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ValueError(f"Impossible de vérifier l’hôte public {host}: {exc}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError(f"Cible de collecte privée ou réservée refusée : {host}.")


class PublicHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: urllib.request.Request, response: Any, code: int, message: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        validate_public_target(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def fetch_feed(source: dict[str, Any], user_agent: str) -> bytes:
    validate_public_target(source["url"])
    request = urllib.request.Request(
        source["url"],
        headers={
            "User-Agent": user_agent,
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9",
        },
    )
    try:
        opener = urllib.request.build_opener(PublicHttpsRedirectHandler())
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"HTTP {response.status}")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FEED_BYTES:
                raise ValueError(f"Flux trop volumineux (limite {MAX_FEED_BYTES} octets).")
            content = response.read(MAX_FEED_BYTES + 1)
            if len(content) > MAX_FEED_BYTES:
                raise ValueError(f"Flux trop volumineux (limite {MAX_FEED_BYTES} octets).")
            return content
    except urllib.error.URLError as exc:
        raise ValueError(f"Impossible de lire le flux : {exc.reason}") from exc


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as file:
        file.write(content)
        temporary = Path(file.name)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def make_entry(item: dict[str, str], source: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    score, category, tags, location = score_entry(item, source, now)
    canonical_url = canonical_article_url(item["url"])
    entry_id = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:20]
    return {
        "id": entry_id,
        "title": item["title"],
        "summary": item["summary"],
        "url": canonical_url,
        "publishedAt": item.get("publishedAt") or None,
        "collectedAt": now.isoformat(timespec="seconds"),
        "sourceId": source["id"],
        "sourceName": source["name"],
        "sourceAuthor": source.get("author", source["name"]),
        "sourceSiteName": source.get("site_name", ""),
        "category": category,
        "location": location,
        "score": score,
        "tags": tags,
    }


def canonical_article_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    tracking_parameters = {"fbclid", "gclid", "mc_cid", "mc_eid", "xtor"}
    query = [
        (key, item) for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in tracking_parameters and not key.lower().startswith("utm_")
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path,
        urllib.parse.urlencode(sorted(query)),
        "",
    ))


def snapshot(entries: list[dict[str, Any]], source_count: int, today: date) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for entry in entries:
        category = entry.get("category", "autre")
        counts[category] = counts.get(category, 0) + 1
    return {
        "date": today.isoformat(),
        "total": len(entries),
        "sourceCount": source_count,
        "categories": counts,
    }


def update_history(root: Path, daily: dict[str, Any]) -> None:
    path = root / "data" / "history.json"
    history = read_json(path, {"schemaVersion": 1, "snapshots": []})
    snapshots = [item for item in history.get("snapshots", []) if item.get("date") != daily["date"]]
    snapshots.append(daily)
    snapshots.sort(key=lambda item: item.get("date", ""))
    write_json(path, {"schemaVersion": 1, "snapshots": snapshots[-730:]})


def markdown_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def apa_date(value: str | None) -> str:
    if not value:
        return "s. d."
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(PARIS)
    except ValueError:
        return "s. d."
    month = (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    )[published.month - 1]
    return f"{published.year}, {published.day} {month}"


def apa_reference(item: dict[str, Any]) -> str:
    author = markdown_escape(str(item.get("sourceAuthor") or item.get("sourceName") or "Source publique"))
    title = markdown_escape(str(item.get("title") or "Publication sans titre"))
    site_name = markdown_escape(str(item.get("sourceSiteName") or ""))
    url = str(item.get("url") or "")
    site = f" {site_name}." if site_name and site_name.casefold() != author.casefold() else ""
    link = f" [{url}]({url})" if url else ""
    title_end = "" if title.rstrip().endswith((".", "?", "!")) else "."
    return f"{author}. ({apa_date(item.get('publishedAt'))}). *{title}*{title_end}{site}{link}"


def build_weekly_note(latest: dict[str, Any], now: datetime) -> str:
    start = now.date() - timedelta(days=now.weekday())
    end = start + timedelta(days=6)
    entries = [
        item for item in latest.get("entries", [])
        if item.get("publishedAt")
        and start <= datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00")).astimezone(PARIS).date() <= end
    ]
    entries.sort(key=lambda item: (-int(item.get("score", 0)), item.get("publishedAt") or ""))
    lines = [
        f"# Veille terrain 66 — semaine du {start.strftime('%d/%m/%Y')}",
        "",
        f"> Synthèse générée le {now.strftime('%d/%m/%Y à %H:%M')} (Europe/Paris) depuis les flux publics configurés.",
        "",
        f"## Bilan de la semaine",
        "",
        f"{len(entries)} signal(aux) collecté(s) et publié(s) pendant la semaine du {start.strftime('%d/%m')} au {end.strftime('%d/%m/%Y')}.",
        "",
    ]
    if not entries:
        lines.extend([
            "Aucun signal correspondant aux critères n’a été collecté pour cette semaine. Cela ne signifie pas qu’aucune activité n’a eu lieu : seuls les flux et critères actuellement configurés sont couverts.",
            "",
        ])
    else:
        cited_entries = entries[:12]
        lines.extend(["## Signaux à examiner", ""])
        for item in cited_entries:
            title = markdown_escape(str(item.get("title", "Publication sans titre")))
            url = str(item.get("url", ""))
            summary = markdown_escape(str(item.get("summary", "")))
            score = int(item.get("score", 0))
            lines.append(f"- [{title}]({url}) — pertinence indicative {score}/100.")
            if summary:
                lines.append(f"  - {summary}")
        lines.append("")
        lines.extend(["## Références (APA 7)", ""])
        lines.extend(f"- {apa_reference(item)}" for item in cited_entries)
        lines.append("")
    lines.extend([
        "## Méthode et limites",
        "",
        "Les références suivent le format APA 7 adapté en français : auteur institutionnel, date de publication, titre de la publication, nom du site et URL. « s. d. » indique qu’aucune date n’est fournie. Cette synthèse couvre uniquement les flux RSS/Atom publics explicitement configurés et les éléments qui dépassent le seuil de pertinence. Le score est un tri automatisé indicatif, pas une validation éditoriale. Consultez la publication source avant toute décision ou prise de contact. L’absence de résultat ne prouve pas l’absence d’activité.",
        "",
    ])
    return "\n".join(lines)


def publish_weekly(root: Path, now: datetime, require_fresh: bool = False) -> bool:
    latest = read_json(root / "data" / "latest.json", {"status": "unconfigured", "entries": []})
    if latest.get("status") not in {"live", "empty"}:
        LOG.info("Aucune collecte réussie : synthèse hebdomadaire non générée.")
        return False
    local_now = now.astimezone(PARIS)
    if require_fresh:
        collection_status = read_json(root / "data" / "collection-status.json", {})
        generated_at = latest.get("generatedAt")
        try:
            generated_date = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00")).astimezone(PARIS).date()
        except ValueError:
            generated_date = None
        if collection_status.get("status") not in {"live", "empty", "partial"} or generated_date != local_now.date():
            LOG.warning("Publication automatique ignorée : aucune collecte réussie le même jour.")
            return False
    week = local_now.strftime("%G-W%V")
    content = build_weekly_note(latest, local_now)
    weekly_dir = root / "notes" / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    archive_path = weekly_dir / f"{week}.md"
    if not archive_path.exists():
        try:
            with archive_path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
        except FileExistsError:
            pass
    atomic_write(root / "notes" / "latest.md", content)
    archives = []
    for path in sorted(weekly_dir.glob("????-W??.md"), reverse=True):
        archives.append({
            "week": path.stem,
            "label": f"Semaine {path.stem.split('-W')[-1]} · {path.stem[:4]}",
            "url": f"notes/weekly/{path.name}",
        })
    write_json(root / "notes" / "weekly" / "index.json", {"archives": archives})
    LOG.info("Note hebdomadaire publiée : %s", archive_path.relative_to(root))
    return True


def paris_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(PARIS)


def run_collection(root: Path, config_path: Path, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    checked_at = now.astimezone(PARIS).isoformat(timespec="seconds")
    status_path = root / "data" / "collection-status.json"
    try:
        config = read_json(config_path, {})
        if not isinstance(config, dict) or not isinstance(config.get("sources", []), list):
            raise ValueError("La configuration doit être un objet JSON contenant une liste sources.")
        active_sources: list[dict[str, Any]] = []
        for source in config.get("sources", []):
            if isinstance(source, dict) and source.get("active", True) is False:
                continue
            active_sources.append(validate_source(source))
        source_ids = [source["id"] for source in active_sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Chaque source active doit avoir un id unique.")
        minimum_score = int(config.get("minimum_score", 18))
        lookback_days = int(config.get("lookback_days", 90))
        if not 0 <= minimum_score <= 100 or lookback_days < 1:
            raise ValueError("minimum_score doit être entre 0 et 100 et lookback_days doit être positif.")
        if not active_sources:
            write_json(status_path, {
                "status": "unconfigured",
                "checkedAt": checked_at,
                "message": "Aucun flux public n’est configuré.",
                "sourcesChecked": 0,
                "sourcesSucceeded": 0,
                "sourcesFailed": 0,
            })
            LOG.info("Aucun flux configuré; aucune donnée ni archive de démonstration n’a été créée.")
            return 0
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        write_json(status_path, {
            "status": "error",
            "checkedAt": checked_at,
            "message": f"Configuration invalide : {exc}",
            "sourcesChecked": 0,
            "sourcesSucceeded": 0,
            "sourcesFailed": 0,
        })
        LOG.error("Configuration de collecte invalide : %s", exc)
        return 1

    user_agent = str(config.get("user_agent", "VeilleTerrain66/1.0"))
    latest_entries: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    succeeded = 0
    for source in active_sources:
        try:
            feed = fetch_feed(source, user_agent)
            items = parse_feed(feed)
            succeeded += 1
            for item in items:
                if item.get("publishedAt"):
                    published = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
                    if published < now - timedelta(days=lookback_days):
                        continue
                entry = make_entry(item, source, now)
                if entry["score"] >= minimum_score:
                    latest_entries.setdefault(entry["id"], entry)
            LOG.info("Flux lu : %s (%d publication(s) analysée(s))", source["name"], len(items))
        except (OSError, ValueError, urllib.error.URLError, ET.ParseError) as exc:
            message = str(exc)[:240]
            LOG.warning("Échec du flux %s : %s", source["name"], message)
            errors.append({"sourceId": source["id"], "sourceName": source["name"], "message": message})

    if succeeded == 0:
        status = {
            "status": "error",
            "checkedAt": checked_at,
            "message": "Tous les flux configurés ont échoué. Les dernières données réussies sont conservées sans être remplacées.",
            "sourcesChecked": len(active_sources),
            "sourcesSucceeded": 0,
            "sourcesFailed": len(errors),
            "errors": errors,
        }
        write_json(status_path, status)
        LOG.error("%s", status["message"])
        return 1

    entries = sorted(latest_entries.values(), key=lambda entry: (-entry["score"], entry.get("publishedAt") or "", entry["title"]))
    data_status = "partial" if errors else "live" if entries else "empty"
    latest = {
        "schemaVersion": 1,
        "status": "live" if entries else "empty",
        "generatedAt": checked_at,
        "entries": entries,
        "sources": [{"id": source["id"], "name": source["name"]} for source in active_sources if source["id"] not in {error["sourceId"] for error in errors}],
        "errors": errors,
    }
    write_json(root / "data" / "latest.json", latest)
    update_history(root, snapshot(entries, succeeded, now.astimezone(PARIS).date()))
    message = (
        f"{len(entries)} signal(aux) pertinent(s) trouvé(s) sur {succeeded} flux."
        if entries else f"Aucun signal n’a dépassé le seuil de pertinence sur {succeeded} flux."
    )
    if errors:
        message += f" {len(errors)} flux en erreur."
    write_json(status_path, {
        "status": data_status,
        "checkedAt": checked_at,
        "message": message,
        "sourcesChecked": len(active_sources),
        "sourcesSucceeded": succeeded,
        "sourcesFailed": len(errors),
        "errors": errors,
    })
    LOG.info("%s", message)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Racine du site statique.")
    parser.add_argument("--config", type=Path, help="Fichier JSON de configuration des flux.")
    parser.add_argument("--weekly", action="store_true", help="Publier immédiatement la note hebdomadaire depuis la dernière collecte réussie.")
    parser.add_argument("--weekly-if-due", action="store_true", help="Publier seulement si l’heure locale est lundi 09:00 Europe/Paris.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = args.root.resolve()
    config_path = (args.config or root / "config" / "sources.json").resolve()
    if args.weekly or args.weekly_if_due:
        now = paris_now()
        if args.weekly_if_due and (now.weekday() != 0 or now.hour != 9):
            LOG.info("Publication différée : il est %s, pas lundi 09:00 Europe/Paris.", now.strftime("%A %H:%M"))
            return 0
        return 0 if publish_weekly(root, now, require_fresh=args.weekly_if_due) else 0
    return run_collection(root, config_path)


if __name__ == "__main__":
    sys.exit(main())
