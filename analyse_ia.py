"""Analyse IA d'un post de Trump : impact probable sur les marchés.

Activée seulement si le secret ANTHROPIC_API_KEY est renseigné (clé payante,
créée par toi sur https://console.anthropic.com). Sans clé, le bot garde la
simple liste « Marchés concernés » par mots-clés.

C'est une ESTIMATION générale (comme un commentaire de marché), jamais un
conseil d'achat ou de vente.
"""
from __future__ import annotations

import html
import json
import os

MODEL = os.environ.get("ANALYSE_MODEL") or "claude-opus-5-5"

SYSTEM = (
    "Tu es analyste de marchés financiers. On te donne un post Truth Social de Donald Trump. "
    "Estime de façon factuelle et prudente l'impact probable de ce post sur les marchés, à court terme "
    "(heures à quelques jours) : quels actifs, indices, secteurs, devises ou cryptos pourraient bouger, "
    "dans quel sens, et avec quelle probabilité. Appuie-toi sur les réactions passées des marchés à des "
    "annonces comparables. Si le post n'a probablement aucun effet, dis-le (impact « nul »). "
    "N'invente pas de chiffres précis. Ne donne aucune recommandation d'achat ou de vente : décris "
    "seulement les mouvements les plus probables. Réponds en français, phrases très courtes."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "impact": {"type": "string", "enum": ["nul", "faible", "moyen", "fort"]},
        "resume": {"type": "string", "description": "Une phrase : ce que le marché retiendra de ce post."},
        "mouvements": {
            "type": "array",
            "description": "Les 1 à 4 mouvements les plus probables, du plus probable au moins probable.",
            "items": {
                "type": "object",
                "properties": {
                    "actif": {"type": "string", "description": "ex. S&P 500, Nasdaq, yuan, Bitcoin, Tesla (TSLA), pétrole"},
                    "sens": {"type": "string", "enum": ["hausse", "baisse", "volatilité"]},
                    "probabilite": {"type": "string", "enum": ["faible", "moyenne", "élevée"]},
                    "pourquoi": {"type": "string", "description": "Raison en moins de 12 mots."},
                },
                "required": ["actif", "sens", "probabilite", "pourquoi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["impact", "resume", "mouvements"],
    "additionalProperties": False,
}

IMPACT = {"nul": "⚪ NUL", "faible": "🟡 FAIBLE", "moyen": "🟠 MOYEN", "fort": "🔴 FORT"}
SENS = {"hausse": "📈", "baisse": "📉", "volatilité": "↕️"}

_client = None


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def analyse(text: str, markets_hint: list[str]) -> dict | None:
    """Renvoie l'analyse (dict) ou None si indisponible / refusée."""
    global _client
    if not enabled():
        return None
    import anthropic

    if _client is None:
        _client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    response = _client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Post de Trump :\n<post>\n{text}\n</post>\n"
            f"Marchés repérés par mots-clés (indicatif) : {', '.join(markets_hint) or 'aucun'}",
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
