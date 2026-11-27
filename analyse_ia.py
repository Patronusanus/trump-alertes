"""Analyse IA d'un post de Trump : impact probable sur les marchés.

Deux moteurs possibles (le premier dont la clé existe est utilisé) :
  - GEMINI_API_KEY : Google Gemini, GRATUIT (clé sur https://aistudio.google.com,
    sans carte bancaire). Modèles essayés dans l'ordre : Gemini Flash (le
    meilleur, ~20 analyses/jour gratuites), puis Flash-Lite (~500/jour) quand
    le quota est atteint.
  - ANTHROPIC_API_KEY : Claude (payant).
Sans clé, le bot garde la simple liste « Marchés concernés » par mots-clés.

C'est une ESTIMATION générale (comme un commentaire de marché), jamais un
conseil d'achat ou de vente.
"""
from __future__ import annotations

import html
import json
import os
import re
import time

import requests

MODEL = os.environ.get("ANALYSE_MODEL") or "claude-opus-5-5"
GEMINI_MODELS = [
    m.strip()
    for m in (os.environ.get("GEMINI_MODELS") or "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",")
    if m.strip()
]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

SYSTEM = (
    "Tu es analyste de marchés financiers. On te donne un post Truth Social de Donald Trump. "
    "Estime de façon factuelle et prudente l'impact probable de ce post sur les marchés, à court terme "
    "(heures à quelques jours) : quels actifs, indices, secteurs, devises ou cryptos pourraient bouger, "
    "dans quel sens, et avec quelle probabilité. Appuie-toi sur les réactions passées des marchés à des "
    "annonces comparables. Si le post n'a probablement aucun effet, dis-le (impact « nul »). "
    "N'invente pas de chiffres précis. Ne donne aucune recommandation d'achat ou de vente : décris "
    "seulement les mouvements les plus probables. Pour chaque actif, donne son symbole négociable. "
    "Réserve l'impact « fort » aux cas où un mouvement net et rapide est vraiment probable. "
    "Réponds en français, phrases très courtes."
)

SYSTEM_OP = (
    "Tu es analyste de marchés financiers. On te donne une opération financière qui vient d'être "
    "rendue publique : achat ou vente d'actions, de crypto ou de produits par Donald Trump, ses fils, "
    "leurs sociétés ou des portefeuilles qui leur sont liés, ou une annonce d'une de ces sociétés "
    "(déclarations SEC, blockchain...). Explique en une phrase ce que cette opération signifie "
    "probablement (signal de confiance, prise de bénéfices, besoin de liquidités, opération de "
    "routine...), puis estime de façon factuelle et prudente son impact probable sur les marchés à "
    "court terme : quels actifs pourraient bouger, dans quel sens, avec quelle probabilité. Beaucoup "
    "d'opérations n'ont aucun effet : dis-le (impact « nul »). N'invente pas de chiffres. Ne donne "
    "aucune recommandation d'achat ou de vente. Pour chaque actif, donne son symbole négociable. "
    "Réserve l'impact « fort » aux cas où un mouvement net et rapide est vraiment probable. "
    "Réponds en français, phrases très courtes."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "impact": {
            "type": "string",
            "enum": ["nul", "faible", "moyen", "fort"],
            "description": "« fort » seulement si un mouvement net et rapide d'au moins un actif est probable.",
        },
        "resume": {"type": "string", "description": "Une phrase : ce que le marché retiendra de ce post."},
        "mouvements": {
            "type": "array",
            "description": "Les 1 à 4 mouvements les plus probables, du plus probable au moins probable.",
            "items": {
                "type": "object",
                "properties": {
                    "actif": {"type": "string", "description": "ex. S&P 500, Nasdaq, yuan, Bitcoin, Tesla (TSLA), pétrole"},
                    "ticker": {
                        "type": "string",
                        "description": "Symbole négociable : action US (TSLA, DJT), crypto (BTC, ETH), "
                        "ETF pour un indice ou une matière (SPY, QQQ, GLD, USO), paire de devises (EURUSD). Vide si aucun.",
                    },
                    "categorie": {"type": "string", "enum": ["action", "crypto", "etf", "devise", "autre"]},
                    "sens": {"type": "string", "enum": ["hausse", "baisse"]},
                    "probabilite": {"type": "string", "enum": ["faible", "moyenne", "élevée"]},
                    "pourquoi": {"type": "string", "description": "Raison en moins de 12 mots."},
                },
                "required": ["actif", "ticker", "categorie", "sens", "probabilite", "pourquoi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["impact", "resume", "mouvements"],
    "additionalProperties": False,
}

IMPACT = {"nul": "⚪ NUL", "faible": "🟡 FAIBLE", "moyen": "🟠 MOYEN", "fort": "🔴 FORT"}
SENS = {"hausse": "📈", "baisse": "📉", "volatilité": "↕️"}

# Pages où passer l'ordre (modifiables : secrets TRADE_URL_ACTION / TRADE_URL_CRYPTO).
TRADE_URL_ACTION = os.environ.get("TRADE_URL_ACTION") or "https://www.etoro.com/markets/{t}"
TRADE_URL_CRYPTO = os.environ.get("TRADE_URL_CRYPTO") or "https://www.binance.com/fr/trade/{T}_USDT"


def trade_link(m: dict) -> str | None:
    t = re.sub(r"[^A-Za-z0-9.]", "", m.get("ticker") or "")
    if not t:
        return None
    tpl = TRADE_URL_CRYPTO if m.get("categorie") == "crypto" else TRADE_URL_ACTION
    return tpl.format(t=t.lower(), T=t.upper())

_client = None


def _gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def enabled() -> bool:
    return bool(_gemini_key() or os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _prompt(text: str, markets_hint: list[str], kind: str = "post") -> str:
    if kind == "operation":
        return f"Opération signalée :\n<operation>\n{text}\n</operation>"
    return (
        f"Post de Trump :\n<post>\n{text}\n</post>\n"
        f"Marchés repérés par mots-clés (indicatif) : {', '.join(markets_hint) or 'aucun'}"
    )


def _parse_json(out: str) -> dict | None:
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out.strip())
    try:
        data = json.loads(out)
    except ValueError:
        m = re.search(r"\{.*\}", out, re.S)
        data = json.loads(m.group(0)) if m else None
    return data if isinstance(data, dict) and "impact" in data else None


def _gemini(text: str, markets_hint: list[str], kind: str = "post") -> dict | None:
    """Gemini (gratuit). Passe au modèle suivant si quota atteint ou modèle absent."""
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_OP if kind == "operation" else SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": _prompt(text, markets_hint, kind)}]}],
    }
    configs = [
        {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA},
        # si le schéma est refusé : JSON simple, format décrit dans la consigne
        {"responseMimeType": "application/json"},
    ]
    hint = "\nRéponds uniquement en JSON conforme à ce schéma : " + json.dumps(SCHEMA, ensure_ascii=False)
    last = None
    for model in GEMINI_MODELS:
        for i, cfg in enumerate(configs):
            payload = dict(body, generationConfig=cfg)
            if i:
                payload["contents"] = [{"role": "user", "parts": [{"text": _prompt(text, markets_hint, kind) + hint}]}]
            r = None
            for attempt in range(2):  # 503 = modèle surchargé, souvent quelques secondes
                try:
                    r = requests.post(
                        GEMINI_URL.format(model),
                        headers={"x-goog-api-key": _gemini_key(), "Content-Type": "application/json"},
                        json=payload,
                        timeout=45,
                    )
                except requests.RequestException as ex:  # délai dépassé, coupure réseau
                    r, last = None, f"{model} {type(ex).__name__}"
                    break
                if r.status_code not in (500, 503):
                    break
                time.sleep(4 * (attempt + 1))
            if r is None:
                break  # modèle trop lent : modèle suivant
            last = f"{model} HTTP {r.status_code}"
            if r.status_code == 400 and i == 0:
                continue  # schéma refusé : on réessaie sans
            if r.status_code in (403, 404, 429, 500, 503) or not r.ok:
                break  # quota / modèle indisponible : modèle suivant
            parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            data = _parse_json("".join(p.get("text", "") for p in parts if not p.get("thought")))
            if data:
                return data
            break
    raise RuntimeError(last or "Gemini indisponible")


def analyse(text: str, markets_hint: list[str], kind: str = "post") -> dict | None:
    """Renvoie l'analyse (dict) ou None si indisponible / refusée."""
    global _client
    if not enabled():
        return None
    if _gemini_key():
        return _gemini(text, markets_hint, kind)
    import anthropic

    if _client is None:
        _client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    response = _client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM_OP if kind == "operation" else SYSTEM,
        messages=[{
            "role": "user",
            "content": _prompt(text, markets_hint, kind),
        }],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    out = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(out) if out else None


def format_block(a: dict) -> str:
    """Bloc Telegram (HTML) ajouté sous le post."""
    lines = [f"🧠 Impact marché estimé : <b>{IMPACT.get(a.get('impact'), a.get('impact', '?'))}</b>"]
    for m in a.get("mouvements", [])[:4]:
        lines.append(
            f"{SENS.get(m.get('sens'), '•')} <b>{html.escape(m.get('actif', ''))}</b> · "
            f"{html.escape(m.get('sens', ''))} (proba {html.escape(m.get('probabilite', ''))})"
            f" · {html.escape(m.get('pourquoi', ''))}"
        )
    if a.get("resume"):
        lines.append(f"💬 {html.escape(a['resume'])}")
    return "\n".join(lines)


def high_alert(original: str, a: dict) -> str:
    """Alerte complète pour un impact FORT : tendance, actif, liens pour trader."""
    moves = a.get("mouvements", [])[:3]
    top = moves[0] if moves else {}
    sens = top.get("sens", "")
    head = (
        f"🚨 <b>IMPACT FORT</b> · {SENS.get(sens, '')} <b>{html.escape(sens.upper())} probable</b> : "
        f"<b>{html.escape(top.get('actif', '?'))}</b>"
        + (f" ({html.escape(top['ticker'].upper())})" if top.get("ticker") else "")
    )
    links = []
    for m in moves:
        url = trade_link(m)
        if url and url not in [u for u, _ in links]:
            label = f"{SENS.get(m.get('sens'), '')} {m.get('ticker', '').upper()}"
            links.append((url, label))
    lines = [head, "", original, "", format_block(a)]
    if links:
        lines.append("🔗 Trader : " + " · ".join(f'<a href="{html.escape(u)}">{html.escape(l)}</a>' for u, l in links))
    lines.append("⚠️ <i>Tendance estimée par IA, pas un conseil d'investissement.</i>")
    return "\n".join(lines)
