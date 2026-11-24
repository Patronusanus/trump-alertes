"""Source n°4 : posts Truth Social de Donald Trump qui parlent de marchés.

Trump annonce parfois sur Truth Social des choses qui font bouger les cours
(« THIS IS A GREAT TIME TO BUY!!! », crypto, droits de douane...). On relaie
les posts qui contiennent un mot-clé financier.

Attention : Truth Social bloque souvent les serveurs (dont GitHub Actions).
Si c'est le cas, le bot l'ignore simplement ; cette source marche en revanche
très bien si tu lances le bot sur ton PC (python bot.py --loop 60).
"""
from __future__ import annotations

import html
import os
import re

import requests

import delay

ACCOUNT_ID = "107780257626128497"  # @realDonaldTrump
URL = f"https://truthsocial.com/api/v1/accounts/{ACCOUNT_ID}/statuses"
KEYWORDS = os.environ.get(
    "TRUTH_KEYWORDS",
    "buy,bought,sell,sold,stock,stocks,share,shares,market,dow,nasdaq,s&p,invest,crypto,bitcoin,"
    "btc,ethereum,$trump,wlfi,world liberty,usd1,tariff,tariffs,djt,truth social,interest rate,fed",
)
KW = [k.strip().lower() for k in KEYWORDS.split(",") if k.strip()]
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})


def _text(content: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>", "\n", content)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def _match(text: str) -> list[str]:
    low = text.lower()
    return [k for k in KW if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", low)]


def check(state: dict, log=print) -> list[str]:
    if os.environ.get("TRUTH_ENABLED", "1") == "0":
        return []
    try:
        r = _s.get(URL, params={"limit": 20, "exclude_replies": "true"}, timeout=20)
        r.raise_for_status()
        posts = r.json()
    except Exception as ex:
        log(f"Truth Social inaccessible ({type(ex).__name__}) : ignoré")
        return []
    first = "truth_last_id" not in state
    last = int(state.get("truth_last_id", 0))
    msgs = []
    for p in sorted(posts, key=lambda p: int(p["id"])):
        pid = int(p["id"])
        if pid <= last:
            continue
        last = max(last, pid)
        if first:
            continue
        text = _text(p.get("content") or "")
        hits = _match(text)
        if not text or not hits:
            continue
        excerpt = text if len(text) < 280 else text[:280] + "…"
        msgs.append(
            f"📣 <b>Trump sur Truth Social</b> · ⏱️ il y a <b>{delay.since_timestamp(p.get('created_at', ''))}</b>\n"
            f"« {html.escape(excerpt)} »\n"
            f'<a href="{html.escape(p.get("url") or "")}">📎 Post</a>'
        )
    state["truth_last_id"] = str(last)
    return msgs
