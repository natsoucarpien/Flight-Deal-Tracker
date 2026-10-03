"""Regenerate report.md: top-3 deals per region plus per-route cheapest
current price with a 7-day trend. Prix affiches en euros, avec un lien
Google Flights pour verifier chaque offre."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import db
import rank
import tracker
from alerts import format_deal

log = logging.getLogger("report")

OUTPUT_PATH = "report.md"


def google_flights_url(origin: str, dest: str, depart: str | None = None, back: str | None = None) -> str:
    """Lien Google Flights (recherche en langage naturel) pour verifier une offre."""
    query = f"Flights from {origin} to {dest}"
    if depart:
        query += f" on {depart}"
    if back:
        query += f" through {back}"
    return f"https://www.google.com/travel/flights?q={quote(query)}&curr=EUR&hl=fr"


def euro(text: str) -> str:
    """'$48' -> '48 €' dans un texte deja formate."""
    return re.sub(r"\$(\d+)", r"\1 €", text)


def _field(row, key):
    """Lit un champ d'une ligne de base de donnees sans jamais planter."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def route_trends(conn) -> list[dict]:
    """Per route: cheapest price in the last 24h vs cheapest 1-7 days ago."""
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    current: dict[tuple[str, str], float] = {}
    previous: dict[tuple[str, str], float] = {}
    cheapest_row: dict[tuple[str, str], object] = {}
    for row in db.offers_since(conn, 7):
        key = (row["origin"], row["dest"])
        if row["run_timestamp"] >= day_ago:
            bucket = current
        elif row["run_timestamp"] >= week_ago:
            bucket = previous
        else:
            continue
        if key not in bucket or row["price_usd"] < bucket[key]:
            bucket[key] = row["price_usd"]
            if bucket is current:
                cheapest_row[key] = row

    trends = []
    for (origin, dest), price in sorted(current.items()):
        prior = previous.get((origin, dest))
        if prior is None:
            trend = "–"
        elif price < prior:
            trend = f"↓ {prior - price:.0f} €"
        elif price > prior:
            trend = f"↑ {price - prior:.0f} €"
        else:
            trend = "→ flat"
        row = cheapest_row.get((origin, dest))
        trends.append({
            "origin": origin, "dest": dest, "price": price, "trend": trend,
            "depart": _field(row, "depart_date") if row is not None else None,
            "back": _field(row, "return_date") if row is not None else None,
        })
    return trends


def build_report(conn, config: dict) -> str:
    """Assemble the full markdown report."""
    tops = rank.rank_all(conn, config)
    lines = [
        "# Flight deal report",
        "",
        f"_Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "_Prix en euros, issus d'un cache : à vérifier sur Google Flights ou chez la compagnie avant de réserver._",
        "",
    ]
    for region in ("domestic", "international"):
        lines.append(f"## Top 3 {region}")
        lines.append("")
        deals = tops.get(region, [])
        if not deals:
            lines.append("_No qualifying offers in the latest run._")
        for i, deal in enumerate(deals, 1):
            text = euro(format_deal(deal))
            origin, dest = deal.get("origin"), deal.get("dest")
            if origin and dest:
                url = google_flights_url(origin, dest, deal.get("depart_date"), deal.get("return_date"))
                text += f" · [Vérifier]({url})"
            lines.append(f"{i}. {text}")
        lines.append("")

    lines.append("## Routes (cheapest current offer, 7-day trend)")
    lines.append("")
    trends = route_trends(conn)
    if trends:
        lines.append("| Route | Cheapest | 7d trend | Google Flights |")
        lines.append("|-------|---------:|----------|----------------|")
        for t in trends:
            url = google_flights_url(t["origin"], t["dest"], t["depart"], t["back"])
            lines.append(
                f"| {t['origin']}→{t['dest']} | {t['price']:.0f} € | {t['trend']} | [Vérifier]({url}) |"
            )
    else:
        lines.append("_No offers recorded in the last 24 hours._")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Write report.md from the database."""
    config = tracker.load_config()
    conn = db.connect()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(build_report(conn, config))
    log.info("Wrote %s", OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
