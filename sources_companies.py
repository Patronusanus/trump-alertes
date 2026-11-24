"""Source n°7 : déclarations SEC des sociétés liées à la famille Trump.

Ce que font les SOCIÉTÉS (achats de bitcoin, rachats, accords...) apparaît
d'abord dans leurs déclarations à la SEC, publiées en quelques secondes sur
EDGAR. Le bot les vérifie toutes les 60 s et envoie :

  - 8-K   : annonce officielle (achat/vente d'actifs, accord, rachat...) ;
  - 144   : un dirigeant ANNONCE qu'il va vendre des actions (déposé avant
            ou le jour de la vente : le signal légal le plus précoce) ;
  - 4     : achat/vente en bourse d'un dirigeant de la société.

Sociétés (numéros CIK vérifiés le 24/11/2026 sur sec.gov) : voir COMPANIES.
Seuil des ventes annoncées (144) : CO_MIN_USD (défaut 100 000 $).
Ajouter : SEC_COMPANIES="1234567:Nom,7654321:Nom2".
"""
from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET

import delay
import sources_sec

COMPANIES = {
    "1849635": "Trump Media (DJT)",
    "1755953": "American Bitcoin (ABTC)",
    "2051380": "GrabAGun (PEW)",
    "12239": "Dominari (DOMH)",
    "1847064": "PSQ Holdings (PSQH)",
    # Trouvées le 24/11/2026 par recherche plein texte EDGAR (noms des fils Trump
    # ou de World Liberty Financial dans leurs déclarations) :
    "862861": "ALT5 Sigma (ALTS, trésorerie WLFI)",
    "2043140": "World Liberty Financial",
    "2074878": "New America Acquisition I (NWAX)",
    "1853825": "Thumzup (TZUP)",
    "2091024": "Colombier Acquisition III",
}
for item in filter(None, (x.strip() for x in os.environ.get("SEC_COMPANIES", "").split(","))):
    cik, _, label = item.partition(":")
    COMPANIES[cik.lstrip("0")] = label or cik

ITEMS = {
    "1.01": "accord important",
    "1.02": "fin d'un accord",
    "2.01": "achat ou vente d'actifs",
    "2.03": "nouvelle dette",
    "3.02": "vente d'actions",
    "5.02": "changement de dirigeant",
    "7.01": "annonce",
    "8.01": "annonce",
}

_TAG = re.compile(r"<[^>]+>")


def _filings(cik: str) -> list[dict]:
    j = sources_sec._get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
    r = j["filings"]["recent"]
    out = []
    for i in range(min(len(r["form"]), 30)):
        acc, doc = r["accessionNumber"][i], r["primaryDocument"][i]
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
        raw = doc.split("/", 1)[1] if doc.startswith("xsl") else doc
        out.append(
            {
                "id": acc,
                "form": r["form"][i],
                "items": r["items"][i] or "",
                "accepted": r["acceptanceDateTime"][i],
                "url": f"{base}/{doc}",
                "raw_url": f"{base}/{raw}",
            }
        )
    return out


def _summary_8k(url: str) -> str:
    """Première phrase utile du 8-K (« On ..., the Company announced ... »)."""
    try:
        t = _TAG.sub(" ", sources_sec._get(url).text)
    except Exception:
        return ""
    t = html.unescape(re.sub(r"\s+", " ", t))
    m = re.search(r"Item\s+[1-8]\.\d\d[^.]{0,80}\.\s+(On .{20,600}?\.)(?=\s+[A-Z][a-z]|\s*$)", t)
    if not m:
        return ""
    s = m.group(1)
    return s if len(s) <= 300 else s[:300] + "…"


MIN_144 = float(os.environ.get("CO_MIN_USD", "100000"))


def _msg_144(label: str, f: dict) -> str | None:
    root = ET.fromstring(sources_sec._get(f["raw_url"]).text)
    get = lambda name: next((el.text or "" for el in root.iter() if el.tag.endswith(name)), "")
    who = get("nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold")
    n = float(get("noOfUnitsSold") or 0)
    usd = float(get("aggregateMarketValue") or 0)
    when = get("approxSaleDate")
    if usd < MIN_144:
        return None  # petites ventes (salaires en actions) : ignorées
    e = html.escape
    return (
        f"🔴 VENTE ANNONCÉE <b>{e(label)}</b> · <b>{sources_sec._fmt_n(usd)} $</b>\n"
        f"👤 {e(who)} · {sources_sec._fmt_n(n)} actions · vente prévue le {e(when)}\n"
        f'⏱️ déposé il y a <b>{delay.since_timestamp(f["accepted"])}</b> · <a href="{e(f["url"])}">📎 SEC 144</a>'
    )


def _msg_8k(label: str, f: dict) -> str:
    e = html.escape
    kinds = sorted({ITEMS[i] for i in f["items"].split(",") if i in ITEMS})
    what = e(" · ".join(kinds)) if kinds else "déclaration"
    summary = _summary_8k(f["raw_url"])
    line = f"\n📝 <i>{e(summary)}</i>" if summary else ""
    return (
        f"🏢 <b>{e(label)}</b> · 8-K : {what}{line}\n"
        f'⏱️ déposé il y a <b>{delay.since_timestamp(f["accepted"])}</b> · <a href="{e(f["url"])}">📎 SEC</a>'
    )


def _msg_4(label: str, f: dict) -> str | None:
    xml = sources_sec._get(f["raw_url"]).text
    parsed = sources_sec.parse_form4(xml)
    root = ET.fromstring(xml)
    person = sources_sec._txt(root, "reportingOwner/reportingOwnerId/rptOwnerName") or label
    return sources_sec.build_message(person, f, parsed)


def check(state: dict, log=print) -> list[str]:
    seen = set(state.get("co_seen", []))
    family = set(state.get("sec_seen", []))  # déjà signalé via la famille Trump
    ready = set(state.get("co_ready", []))
    msgs = []
    for cik, label in COMPANIES.items():
        try:
            filings = _filings(cik)
        except Exception as ex:
            log(f"SEC société {label} : erreur {ex!r}")
            continue
        silent = cik not in ready
        ready.add(cik)
        for f in reversed(filings):
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            if silent or (f["id"] in family and f["form"].startswith("4")):
                continue
            try:
                if f["form"] in ("8-K", "8-K/A"):
                    msg = _msg_8k(label, f)
                elif f["form"] in ("144", "144/A"):
                    msg = _msg_144(label, f)
                elif f["form"] in ("4", "4/A"):
                    msg = _msg_4(label, f)
                else:
                    msg = None
            except Exception as ex:
                log(f"SEC société {label} {f['form']} : lecture impossible {ex!r}")
                msg = None
            if msg:
                log("SOCIÉTÉ NOUVEAU :", label, f["form"], f["id"])
                msgs.append(msg)
    state["co_seen"] = sorted(seen)[-3000:]
    state["co_ready"] = sorted(ready)
    return msgs
