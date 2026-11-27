"""Bot d'alertes : transactions de Donald Trump et de ses fils.

Sources (de la plus rapide à la plus lente) :
  - sources_crypto.py : portefeuille crypto public de Trump (temps réel)
  - sources_truth.py  : posts Truth Social parlant de marchés (temps réel)
  - sources_sec.py    : formulaires 4 de la SEC (Trump, Don Jr., Eric ; ≤ 2 jours ouvrés)
  - ce fichier        : déclarations 278-T de l'OGE (30 à 45 jours, fourchettes)

Source : API publique de l'Office of Government Ethics (OGE), la même que celle
qu'utilise la page officielle
https://www.oge.gov/web/oge.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm

Fonctionnement d'un passage OGE :
  1. on demande à l'OGE la liste des déclarations dont le nom contient « Trump » ;
  2. on compare avec celles déjà vues (fichier state.json) ;
  3. pour chaque nouvelle déclaration de transactions (278-T), on télécharge le
     PDF, on lit chaque opération (achat / vente, montant, date) ;
  4. on envoie le tout sur Telegram.

Usage :
  python bot.py            # un passage (c'est ce que lance GitHub Actions)
  python bot.py --loop 60  # tourne en continu, vérifie toutes les 60 s
  python bot.py --demo     # renvoie la dernière déclaration (pour tester)
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

import analyse_ia
import delay
import sources_btc
import sources_companies
import sources_crypto
import sources_hyperliquid
import sources_sec
import sources_truth
from parse_278t import Transaction, fmt_usd, parse_pdf

OGE_BASE = "https://extapps2.oge.gov"
OGE_API = OGE_BASE + "/201/Presiden.nsf/API.xsp/v3/rest"
STATE_FILE = Path(os.environ.get("STATE_FILE", Path(__file__).with_name("state.json")))
FILER_FILTER = os.environ.get("FILER_FILTER", "Trump")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MAX_LINES = int(os.environ.get("MAX_LINES", "5"))
SEND_CSV = os.environ.get("SEND_CSV", "0") == "1"
# OGE (portefeuille boursier perso de Trump) : 30–45 jours de retard. Coupé par défaut.
OGE_ENABLED = os.environ.get("OGE_ENABLED", "0") == "1"
HEADERS = {"User-Agent": "Mozilla/5.0 (disclosure-alert-bot)"}
COLUMNS = ["docDate", "title", "type", "name", "agency", "level"]

session = requests.Session()
session.headers.update(HEADERS)


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ OGE


def fetch_filings(name_filter: str = FILER_FILTER, length: int = 100) -> list[dict]:
    """Liste des déclarations (les plus récentes d'abord) pour un nom donné."""
    params = {
        "draw": "1",
        "start": "0",
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "desc",
    }
    for i, col in enumerate(COLUMNS):
        params[f"columns[{i}][data]"] = col
        params[f"columns[{i}][name]"] = ""
        params[f"columns[{i}][searchable]"] = "true"
        params[f"columns[{i}][orderable]"] = "true"
        params[f"columns[{i}][search][value]"] = name_filter if col == "name" else ""
        params[f"columns[{i}][search][regex]"] = "false"
    r = _get(OGE_API, params=params, timeout=90)
    data = r.json().get("data", [])
    filings = []
    for d in data:
        raw_type = d.get("type", "")
        m = re.search(r"href='([^']+)'", raw_type)
        url = urljoin(OGE_BASE, m.group(1)) if m else ""
        filings.append(
            {
                "id": url or f"{d.get('name')}|{d.get('docDate')}|{raw_type}",
                "name": d.get("name", ""),
                "title": d.get("title", ""),
                "agency": d.get("agency", ""),
                "type": re.sub(r"<[^>]+>", "", raw_type).strip(),
                "date": d.get("docDate", ""),
                "url": url,
                "has_pdf": url.lower().endswith(".pdf") or "$file" in url.lower(),
            }
        )
    return filings


def _get(url, retries=4, **kw):
    for attempt in range(retries):
        try:
            r = session.get(url, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            log(f"Erreur réseau ({e}), nouvel essai dans {wait}s")
            time.sleep(wait)


def download_pdf(url: str) -> bytes:
    # Le serveur de l'OGE est lent (parfois ~100 Ko/s) : délai généreux.
    return _get(url, timeout=600).content


# ------------------------------------------------------------- Telegram


def tg(method: str, **payload):
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant (voir README).")
    r = session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}", json=payload, timeout=30)
    if not r.ok:
        log("Telegram:", r.status_code, r.text[:300])
    return r.json()


def resolve_chat_ids(state: dict) -> list[str]:
    """Destinataires : TELEGRAM_CHAT_ID, sinon toute personne ayant écrit au bot."""
    ids = [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]
    if ids:
        return ids
    known = set(state.get("chat_ids", []))
    try:
        updates = tg("getUpdates", offset=state.get("tg_offset", 0), timeout=0).get("result", [])
    except Exception as e:  # pragma: no cover
        log("getUpdates impossible:", e)
        updates = []
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id") is not None and str(chat["id"]) not in known:
            known.add(str(chat["id"]))
            state.setdefault("pending_welcome", []).append(str(chat["id"]))
    state["chat_ids"] = sorted(known)
    return state["chat_ids"]


def send(chat_ids: list[str], text: str, csv_file: tuple[str, bytes] | None = None, silent: bool = False):
    for chunk in split_message(text):
        for cid in chat_ids:
            tg(
                "sendMessage",
                chat_id=cid,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=silent,
            )
            time.sleep(0.4)
    if csv_file:
        name, content = csv_file
        for cid in chat_ids:
            r = session.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                data={"chat_id": cid, "caption": "Liste complète des opérations"},
                files={"document": (name, content, "text/csv")},
                timeout=60,
            )
            if not r.ok:
                log("Telegram sendDocument:", r.status_code, r.text[:300])


def split_message(text: str, limit: int = 3900) -> list[str]:
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


# ------------------------------------------------------------ messages

KIND_ICON = {"achat": "🟢 ACHAT", "vente": "🔴 VENTE", "vente partielle": "🟠 VENTE PARTIELLE", "échange": "🔁 ÉCHANGE"}


def fr_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "")).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def fmt_short(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:,.1f} M$".replace(",", " ").replace(".", ",")
    if v >= 1_000:
        return f"{v / 1_000:,.0f} k$".replace(",", " ")
    return f"{v:.0f} $"


def totals(txs: list[Transaction]) -> tuple[int, int, bool]:
    lo = sum(t.amount_min or 0 for t in txs)
    hi = sum((t.amount_max or t.amount_min or 0) for t in txs)
    open_ended = any(t.amount_min and t.amount_max is None for t in txs)
    return lo, hi, open_ended


def short_range(t: Transaction) -> str:
    """Fourchette compacte : « 1–5 M$ », « 250–500 k$ », « > 50 M$ »."""
    if t.amount_min is None:
        return "montant ?"

    def f(v: int) -> str:
        if v >= 1_000_000:
            return f"{v / 1_000_000:g}".replace(".", ",")
        return f"{v / 1_000:g}".replace(".", ",")

    lo = round(t.amount_min, -3) if t.amount_min > 1000 else 1000
    if t.amount_max is None:
        return f"> {fmt_short(t.amount_min - 1)}"
    unit = "M$" if t.amount_max >= 1_000_000 and lo >= 1_000_000 else "k$"
    if unit == "k$" and t.amount_max >= 1_000_000:
        return f"{f(lo)} k$–{f(t.amount_max)} M$"
    return f"{f(lo)}–{f(t.amount_max)} {unit}"


def build_message(filing: dict, txs: list[Transaction] | None, error: str | None = None) -> str:
    """Message court : les plus gros ordres, une ligne chacun."""
    e = html.escape
    link = f'<a href="{e(filing["url"])}">📎 PDF officiel</a>'
    head = "🇺🇸 <b>TRUMP · déclaration officielle (OGE)</b>"
    if not txs:
        why = f" ({e(error)})" if error else ""
        return f"{head}\n⚠️ Lecture automatique impossible{why}.\n{link}"

    buys = [t for t in txs if t.kind == "achat"]
    sells = [t for t in txs if t.kind in ("vente", "vente partielle")]
    days = [delay.days_since(delay.parse_day(t.date)) for t in txs]
    days = [d for d in days if d is not None]
    lines = [head]
    if days:
        lo, hi = min(days), max(days)
        span = f"{lo} jours" if lo == hi else f"{lo} à {hi} jours"
        lines.append(f"⏱️ Ordres passés il y a <b>{span}</b>")
    lines.append(f"🟢 {len(buys)} achats · 🔴 {len(sells)} ventes")
    lines.append("")
    ranked = sorted(txs, key=lambda t: -(t.amount_max or (t.amount_min or 0) * 2))
    if len(ranked) > MAX_LINES:
        lines.append(f"<b>Top {MAX_LINES} :</b>")
    for t in ranked[:MAX_LINES]:
        tag = "🟢 ACHAT" if t.kind == "achat" else "🔴 VENTE" if t.kind.startswith("vente") else "🔁"
        d = delay.days_since(delay.parse_day(t.date))
        ago = f" · il y a {d} j" if d is not None else ""
        name = e((t.description or "actif illisible")[:40])
        lines.append(f"{tag} <b>{name}</b> · {short_range(t)}{ago}")
    if len(ranked) > MAX_LINES:
        lines.append(f"<i>+ {len(ranked) - MAX_LINES} autres ordres plus petits</i>")
    lines.append(link)
    return "\n".join(lines)


# ------------------------------------------------------------- logique


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    state["seen"] = sorted(set(state.get("seen", [])))
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def to_csv(txs: list[Transaction]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["n°", "actif", "opération", "date", "montant min ($)", "montant max ($)", "déclaré en retard"])
    for t in txs:
        w.writerow([t.number, t.description, t.kind, t.date, t.amount_min or "", t.amount_max or "", "oui" if t.late else ""])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM : s'ouvre bien dans Excel


def process(filing: dict) -> tuple[str, tuple[str, bytes] | None]:
    """Renvoie (message, fichier CSV éventuel)."""
    if not filing["has_pdf"]:
        return build_message(filing, None), None
    if "transaction" not in filing["type"].lower():
        # Rapport annuel, de fin de mandat... : on prévient sans détailler.
        return build_message(filing, None), None
    try:
        log("Téléchargement", filing["url"])
        pdf = download_pdf(filing["url"])
        log(f"PDF {len(pdf) // 1024} Ko, lecture...")
        txs = parse_pdf(pdf)
        log(f"{len(txs)} opérations trouvées")
        csv_file = None
        if SEND_CSV and len(txs) > MAX_LINES:
            csv_file = (f"trump-{filing['date'][:10]}.csv", to_csv(txs))
        return build_message(filing, txs), csv_file
    except Exception as ex:
        log("Erreur de lecture:", repr(ex))
        return build_message(filing, None, error=type(ex).__name__), None


FAST_SOURCES = [
    ("SEC", sources_sec),
    ("SEC sociétés", sources_companies),
    ("Crypto", sources_crypto),
    ("Hyperliquid", sources_hyperliquid),
    ("Bitcoin", sources_btc),
    ("Truth Social", sources_truth),
]

WELCOME = (
    "✅ <b>Bot activé.</b> Un message court par ordre :\n"
    "🟢 ACHAT / 🔴 VENTE · action · montant · ⏱️ délai depuis l'ordre.\n"
    "Sources : crypto (secondes), SEC (≤ 2 jours), OGE (30–45 jours).\n"
    "Exemple avec la dernière déclaration OGE :"
)


ALIVE_EVERY = 600  # « signe de vie » enregistré dans state.json toutes les 10 min (lu par watchdog.py)
WATCHDOG_FILE = STATE_FILE.with_name("watchdog.json")


def _paris_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.now(timezone.utc)


def update_status(state: dict, chat_ids: list[str], status: dict, move: bool = False):
    """Un seul message « 🟢 BOT ACTIF », modifié en silence à chaque passage.

    Après une alerte (move=True), il est supprimé puis renvoyé sans son ni
    notification pour rester tout en bas de la conversation.
    """
    now = datetime.now(timezone.utc)
    last = state.get("alive_at")
    if not last or (now - datetime.fromisoformat(last)).total_seconds() >= ALIVE_EVERY:
        state["alive_at"] = now.isoformat(timespec="seconds")
    ko = [n for n, v in status.items() if not v]
    text = f"🟢 <b>BOT ACTIF</b> · vérifié à {_paris_now():%H:%M}"
    if ko:
        text += "\n⚠️ Hors service : " + ", ".join(ko)
    ids = state.setdefault("status_msg", {})
    for cid in chat_ids:
        mid = ids.get(cid)
        if mid and move:
            if not tg("deleteMessage", chat_id=cid, message_id=mid).get("ok"):
                # trop ancien pour être supprimé (> 48 h) : on le marque périmé
                tg("editMessageText", chat_id=cid, message_id=mid, text="⚪ ancien statut (voir plus bas)")
            mid = None
        if mid:
            r = tg("editMessageText", chat_id=cid, message_id=mid, text=text, parse_mode="HTML")
            if r.get("ok") or "not modified" in r.get("description", ""):
                continue
        r = tg("sendMessage", chat_id=cid, text=text, parse_mode="HTML", disable_notification=True)
        if r.get("ok"):
            ids[cid] = r["result"]["message_id"]


def announce_restart(state: dict):
    """Au démarrage : si la surveillance avait signalé « INACTIF », on prévient du retour."""
    try:
        wd = json.loads(WATCHDOG_FILE.read_text())
    except Exception:
        return
    if not wd.get("alerted") or state.get("back_announced") == wd.get("down_since"):
        return
    chat_ids = resolve_chat_ids(state)
    send(chat_ids, "🟢 <b>BOT DE NOUVEAU ACTIF</b> · il a redémarré, la surveillance reprend.")
    state["back_announced"] = wd.get("down_since")
    state["status_move"] = True
    save_state(state)


IA_MAX_PER_DAY = int(os.environ.get("IA_MAX_PER_DAY") or "300")  # analyses IA max par jour


def deliver(state: dict, chat_ids: list[str], source: str, alert: str) -> bool:
    """Filtre par l'IA : seules les alertes à impact FORT sont envoyées (avec son).

    Si l'IA est indisponible (pas de clé, quota, panne), l'alerte part quand même
    mais SANS son, pour ne rien perdre.
    """
    a, why = None, ""
    day = _paris_now().strftime("%Y-%m-%d")
    if state.get("ia_day") != day:
        state["ia_day"], state["ia_count"] = day, 0
    if not analyse_ia.enabled():
        why = "IA non configurée"
    elif state["ia_count"] >= IA_MAX_PER_DAY:
        why = "quota IA du jour atteint"
    else:
        state["ia_count"] += 1
        kind = "post" if source == "Truth Social" else "operation"
        try:
            a = analyse_ia.analyse(html.unescape(re.sub(r"<[^>]+>", "", alert)), [], kind=kind)
        except Exception as ex:
            log(f"Analyse IA indisponible ({type(ex).__name__})")
        if not a:
            why = "analyse IA indisponible"
    if a is None:
        send(chat_ids, alert + f"\n<i>({why} : envoyé sans son)</i>", silent=True)
        return True
    if a.get("impact") != "fort":
        log(f"{source} : impact {a.get('impact')} → pas d'alerte")
        return False
    send(chat_ids, analyse_ia.high_alert(alert, a))
    return True


def run_once(demo: bool = False, oge: bool = True) -> bool:
    state = load_state()
    # Premier lancement = jamais initialisé ET aucune déclaration encore vue.
    first_run = not state.get("initialized") and not state.get("seen")
    chat_ids = resolve_chat_ids(state)
    if not chat_ids:
        log("Aucun destinataire : envoie un message à ton bot sur Telegram (voir README).")
        save_state(state)
        return False

    # 1) Sources rapides d'abord (quelques secondes chacune).
    sent_any = False
    status = {}
    for name, mod in FAST_SOURCES:
        errors = []

        def slog(*a, _e=errors):
            txt = " ".join(map(str, a)).lower()
            if any(w in txt for w in ("erreur", "inaccessible", "impossible", "illisible")):
                _e.append(txt)
            log(*a)

        try:
            for msg in mod.check(state, log=slog):
                if deliver(state, chat_ids, name, msg):
                    sent_any = True
            save_state(state)
        except Exception as ex:
            log(f"{name} : erreur {ex!r}")
            errors.append(repr(ex))
        if errors and name == "Truth Social":
            continue  # bloqué depuis GitHub Actions : normal, on ne l'affiche pas
        status[name] = not errors
    update_status(state, chat_ids, status, move=sent_any or state.pop("status_move", False))

    # 2) Déclarations OGE (peut prendre quelques minutes à cause de l'OCR).
    if (not oge or not OGE_ENABLED) and state.get("initialized"):
        save_state(state)
        return sent_any
    try:
        filings = fetch_filings()
    except Exception as ex:
        log(f"OGE inaccessible : {ex!r}")
        save_state(state)
        return sent_any
    log(f"{len(filings)} déclarations OGE trouvées pour « {FILER_FILTER} »")
    seen = set(state.get("seen", []))
    new = [f for f in filings if f["id"] not in seen]

    welcome = state.pop("pending_welcome", [])
    if first_run or demo or welcome:
        targets = chat_ids if (first_run or demo) else welcome
        send(targets, WELCOME)
        latest = next((f for f in filings if "transaction" in f["type"].lower()), None)
        if latest:
            send(targets, *process(latest))
    if first_run:
        new = []  # on ne renvoie pas tout l'historique au premier lancement

    for filing in reversed(new):  # du plus ancien au plus récent
        log("NOUVEAU :", filing["name"], filing["type"], filing["date"])
        if "transaction" in filing["type"].lower():  # rapports annuels etc. : ignorés
            send(chat_ids, *process(filing))
        seen.add(filing["id"])
        state["seen"] = list(seen)
        save_state(state)  # on sauve à chaque fois : pas de doublon en cas de crash

    state["seen"] = list(seen | {f["id"] for f in filings})
    state["initialized"] = True
    # Change une fois par semaine : garde le dépôt « actif » pour GitHub Actions.
    state["keepalive_week"] = datetime.now(timezone.utc).strftime("%G-W%V")
    save_state(state)
    return sent_any or bool(new)


def git_save():
    """Enregistre state.json dans le dépôt (mode GitHub Actions en continu)."""
    import subprocess

    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True)

    if not git("status", "--porcelain", str(STATE_FILE)).stdout.strip():
        return
    git("config", "user.name", "trump-bot")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", str(STATE_FILE))
    git("commit", "-q", "-m", "Mise à jour de l'état du bot")
    for _ in range(3):
        if git("pull", "-q", "--rebase", "-X", "theirs").returncode == 0 and git("push", "-q").returncode == 0:
            return
        time.sleep(5)
    log("git push impossible (on réessaiera)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="vérifie en continu toutes les N secondes")
    ap.add_argument("--oge-every", type=int, default=300, help="en continu : OGE toutes les N secondes")
    ap.add_argument("--max-minutes", type=float, default=0, help="en continu : s'arrête après N minutes")
    ap.add_argument("--git-commit", action="store_true", help="enregistre state.json dans git à chaque changement")
    ap.add_argument("--demo", action="store_true", help="envoie la dernière déclaration pour tester")
    args = ap.parse_args()
    state = load_state()
    state["status_move"] = True  # nouveau lancement : statut renvoyé tout en bas
    save_state(state)
    announce_restart(state)
    if not args.loop:
        run_once(demo=args.demo)
        if args.git_commit:
            git_save()
        return
    start = time.monotonic()
    last_oge = -1e9
    demo = args.demo
    while True:
        t0 = time.monotonic()
        do_oge = t0 - last_oge >= args.oge_every
        try:
            run_once(demo=demo, oge=do_oge)
            demo = False
            if do_oge:
                last_oge = t0
        except Exception as e:
            log("Erreur:", repr(e))
        if args.git_commit:
            git_save()
        if args.max_minutes and time.monotonic() - start > args.max_minutes * 60:
            log("Durée maximale atteinte, arrêt (le prochain lancement prend le relais).")
            return
        time.sleep(max(1, args.loop - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main())
