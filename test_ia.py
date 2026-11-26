"""Test réel de l'analyse IA (lancé à la main : Actions → « Test analyse IA »).

Prend le dernier post de Trump qui touche un marché, l'analyse avec l'IA
configurée (Gemini gratuit ou Claude) et envoie le résultat sur Telegram,
précédé de « 🧪 TEST ». N'a aucun effet sur le bot (state.json n'est pas modifié).
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

import requests

import analyse_ia
import sources_truth

_post = requests.post


def _logged_post(url, *a, **kw):
    r = _post(url, *a, **kw)
    if "generativelanguage" in url:
        model = url.split("/models/")[1].split(":")[0]
        detail = "" if r.ok else " · " + r.text[:200].replace("\n", " ")
        print(f"Gemini {model} → HTTP {r.status_code}{detail}")
    return r


analyse_ia.requests.post = _logged_post


def main():
    print("Clé Gemini présente :", bool(os.environ.get("GEMINI_API_KEY", "").strip()))
    try:
        posts = sources_truth._from_api()
    except Exception:
        posts = sources_truth._from_rss()
    posts.sort(key=lambda p: p["id"], reverse=True)
    post = next((p for p in posts if p["text"] and sources_truth.markets(p["text"])), None) or next(p for p in posts if p["text"])
    hits = sources_truth.markets(post["text"])
    print("Post testé :", post["text"][:200])
    a = analyse_ia.analyse(post["text"], hits)
    print("Analyse :", json.dumps(a, ensure_ascii=False))
    if not a:
        raise SystemExit("Pas d'analyse : clé absente ou réponse vide")
    excerpt = post["text"] if len(post["text"]) < 280 else post["text"][:280] + "…"
    msg = (
        "🧪 <b>TEST de l'analyse IA</b> (dernier post de Trump sur un marché)\n"
        f"« {html.escape(excerpt)} »\n"
        f"{analyse_ia.format_block(a)}\n"
        "⚠️ <i>Estimation automatique par IA, pas un conseil d'investissement.</i>"
    )
    state = json.loads(Path("state.json").read_text()) if Path("state.json").exists() else {}
    ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()] or state.get("chat_ids") or list(state.get("status_msg", {}))
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    for cid in ids:
        r = _post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        print("Telegram :", r.status_code)


if __name__ == "__main__":
    main()
