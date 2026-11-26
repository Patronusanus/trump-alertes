"""Surveillance du bot, lancée à part par GitHub Actions (watchdog.yml).

Un bot arrêté ne peut pas annoncer lui-même qu'il est arrêté : ce script le
vérifie de l'extérieur. Le bot enregistre un « signe de vie » (alive_at) dans
state.json toutes les 10 minutes. S'il n'y en a plus depuis DOWN_AFTER minutes :
  1. le message de statut passe à « 🔴 BOT INACTIF » ;
  2. un message avec notification part sur Telegram (ton téléphone sonne) ;
  3. le bot est relancé automatiquement.
Quand le bot repart, il remet lui-même « 🟢 BOT ACTIF » et le signale.

Bibliothèque standard uniquement (pas d'installation nécessaire).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
STATE = HERE / "state.json"
WATCHDOG = HERE / "watchdog.json"
DOWN_AFTER = float(os.environ.get("DOWN_AFTER_MINUTES") or "30")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")


def _call(url: str, payload: dict | None = None, headers: dict | None = None, method: str | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {"ok": True}
    except Exception as ex:
        print("Erreur", url.split("/bot")[0], type(ex).__name__, getattr(ex, "code", ""))
        return {}


def tg(method: str, **payload) -> dict:
    return _call(f"https://api.telegram.org/bot{TOKEN}/{method}", payload)


def gh(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    return _call(
        f"https://api.github.com/repos/{REPO}/{path}",
        payload,
        {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
        method,
    )


def paris(t: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        return t.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m %H:%M")
    except Exception:
        return t.strftime("%d/%m %H:%M UTC")


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    wd = json.loads(WATCHDOG.read_text()) if WATCHDOG.exists() else {}
    alive = state.get("alive_at")
    if not alive:
        print("Pas encore de signe de vie enregistré : rien à faire.")
        return
    last = datetime.fromisoformat(alive)
    age = (datetime.now(timezone.utc) - last).total_seconds() / 60
    print(f"Dernier signe de vie : {alive} (il y a {age:.0f} min)")

    if age < DOWN_AFTER:
        if wd.get("alerted"):
            wd["alerted"] = False  # le bot est reparti (il l'a lui-même annoncé)
            WATCHDOG.write_text(json.dumps(wd, indent=1) + "\n")
            save()
        return

    runs = gh("actions/workflows/bot.yml/runs?per_page=5").get("workflow_runs", [])
    running = [r for r in runs if r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested")]

    if not wd.get("alerted"):
        red = f"🔴 <b>BOT INACTIF</b> · dernier signe de vie {paris(last)}"
        for cid, mid in state.get("status_msg", {}).items():
            tg("editMessageText", chat_id=cid, message_id=mid, text=red, parse_mode="HTML")
        for cid in state.get("chat_ids", []) or list(state.get("status_msg", {})):
            tg(
                "sendMessage",
                chat_id=cid,
                parse_mode="HTML",
                text=f"🔴 <b>BOT INACTIF</b> depuis {paris(last)} (heure de Paris).\nRelance automatique en cours…",
            )
        wd = {"alerted": True, "down_since": alive}
        WATCHDOG.write_text(json.dumps(wd, indent=1) + "\n")
        save()  # publié AVANT la relance : le bot le lit au démarrage

    # Relance, sauf si un lancement vient de démarrer (installation ≈ 2 min).
    # Un lancement plus ancien sans signe de vie est bloqué : le lancement
    # manuel le remplace (cancel-in-progress dans bot.yml).
    now = datetime.now(timezone.utc)
    starting = [
        r for r in running
        if (now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).total_seconds() < 15 * 60
    ]
    if not starting:
        print("Relance du bot")
        gh("actions/workflows/bot.yml/dispatches", {"ref": "main"}, method="POST")


def save():
    import subprocess

    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True)

    if not git("status", "--porcelain", str(WATCHDOG)).stdout.strip():
        return
    git("config", "user.name", "trump-bot")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", str(WATCHDOG))
    git("commit", "-q", "-m", "Surveillance : état du bot")
    for _ in range(3):
        if git("pull", "-q", "--rebase", "-X", "theirs").returncode == 0 and git("push", "-q").returncode == 0:
            return


if __name__ == "__main__":
    main()
