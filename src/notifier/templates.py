"""Message templates shared by the Telegram and Discord notifiers.

Kept plain-text / light-markdown so both channels can reuse them with minor tweaks.
"""

from __future__ import annotations

from src.schemas.listing import ScoredListing

SOURCE_LABEL = {
    "leboncoin": "LeBonCoin",
    "seloger": "SeLoger",
    "pap": "PAP",
    "bienici": "Bien'ici",
    "logic_immo": "Logic-Immo",
    "superimmo": "Superimmo",
    "foncia": "Foncia",
}


def fmt_price(v: float | None) -> str:
    return f"{int(round(v))} € CC" if v else "— €"


def fmt_rent(rent_cc: float | None, charges: float | None) -> str:
    """'758 € charges comprises (dont 173 € de charges → 585 € hors charges)'."""
    if not rent_cc:
        return "— €"
    total = f"{int(round(rent_cc))} € charges comprises"
    if charges:
        hc = int(round(rent_cc - charges))
        return f"{total} (dont {int(round(charges))} € de charges → {hc} € hors charges)"
    return f"{total} (montant des charges non précisé)"


def fmt_surface(v: float | None) -> str:
    return f"{v:.0f} m²" if v else "— m²"


def fmt_arrondissement(postcode: str | None) -> str:
    """'75011' -> 'Paris 11e'; '92110' -> 'Clichy (92110)' using the YAML city names."""
    if not postcode or len(postcode) != 5:
        return "—"
    if postcode.startswith("75"):
        return f"Paris {int(postcode[2:])}e"
    from src.config import get_criteria

    for zone in get_criteria().zones.include:
        if zone.code == postcode and zone.city:
            return f"{zone.city} ({postcode})"
    return postcode


# Backward-compat aliases for callers that already import the underscored names.
_fmt_price = fmt_price
_fmt_surface = fmt_surface
_fmt_arrondissement = fmt_arrondissement


def build_header(listing: ScoredListing) -> str:
    emoji = "🔥" if listing.is_urgent else "✨"
    header = f"{emoji} Pépite {listing.score}/100"
    if listing.price_drop_pct and listing.price_drop_pct > 0:
        header = f"📉 Baisse de prix -{listing.price_drop_pct:.1f}% — {header}"
    if listing.is_urgent:
        header = "URGENT " + header
    return header


_FURNISHED_BADGE = {
    "meuble": "🛋️ MEUBLÉ",
    "vide": "🪑 vide",
    "unclear": "❓ statut meublé incertain",
}


def telegram_message(listing: ScoredListing) -> str:
    r = listing.raw
    source = SOURCE_LABEL.get(r.source, r.source)
    header = build_header(listing)

    # Badge meublé/vide en tête (juste sous le score) pour qu'on triage vite.
    badge_lines: list[str] = []
    fs = listing.furnished_status
    if fs in _FURNISHED_BADGE:
        badge_lines.append(_FURNISHED_BADGE[fs])

    lines = [
        f"*{header}*",
        f"*{r.title}*",
        "",
        *badge_lines,
        f"📍 {_fmt_arrondissement(r.postal_code)}" + (f" — {r.address}" if r.address else ""),
        f"💶 {fmt_rent(r.rent_cc, r.charges)}",
        f"📐 {_fmt_surface(r.surface_m2)}"
        + (f" · {r.rent_cc / r.surface_m2:.1f} €/m²" if r.rent_cc and r.surface_m2 else "")
        + (f"  ·  🧱 {r.property_type}" if r.property_type else ""),
    ]
    floor_bits = []
    if r.floor is not None:
        floor_bits.append(f"Étage {r.floor}")
    if r.has_elevator is not None:
        floor_bits.append("ascenseur OK" if r.has_elevator else "sans ascenseur")
    if floor_bits:
        lines.append("🏢 " + " · ".join(floor_bits))

    poster_label = {"agency": "🏢 Agence", "private": "👤 Particulier"}.get(r.poster_type or "")
    if poster_label:
        lines.append(poster_label)

    if listing.highlights:
        lines.append("")
        lines.append("✅ *Atouts*")
        for h in listing.highlights[:5]:
            lines.append(f"• {h}")

    if listing.red_flags:
        lines.append("")
        lines.append("⚠️ *Red flags*")
        for f in listing.red_flags[:3]:
            lines.append(f"• {f}")

    if listing.reasons:
        lines.append("")
        lines.append("🧠 " + listing.reasons[0])

    lines.append("")
    lines.append(f"🔗 [{source} — voir l'annonce]({r.url})")
    return "\n".join(lines)


def discord_embed(listing: ScoredListing) -> dict:
    r = listing.raw
    source = SOURCE_LABEL.get(r.source, r.source)
    color = 0xFF4D4D if listing.is_urgent else 0x3AB795
    if listing.price_drop_pct and listing.price_drop_pct > 0:
        color = 0x3498DB

    fields = [
        {
            "name": "Loyer",
            "value": fmt_rent(r.rent_cc, r.charges),
            "inline": True,
        },
        {
            "name": "Surface",
            "value": _fmt_surface(r.surface_m2),
            "inline": True,
        },
        {
            "name": "Zone",
            "value": _fmt_arrondissement(r.postal_code),
            "inline": True,
        },
    ]
    if r.floor is not None or r.has_elevator is not None:
        floor_bits = []
        if r.floor is not None:
            floor_bits.append(f"Étage {r.floor}")
        if r.has_elevator is not None:
            floor_bits.append("asc. ✅" if r.has_elevator else "asc. ❌")
        fields.append({"name": "Étage", "value": " · ".join(floor_bits), "inline": True})

    if listing.highlights:
        fields.append(
            {
                "name": "Atouts",
                "value": "\n".join(f"• {h}" for h in listing.highlights[:5]),
                "inline": False,
            }
        )
    if listing.red_flags:
        fields.append(
            {
                "name": "⚠️ Red flags",
                "value": "\n".join(f"• {f}" for f in listing.red_flags[:3]),
                "inline": False,
            }
        )

    embed: dict = {
        "title": f"{build_header(listing)} — {r.title}"[:250],
        "url": str(r.url),
        "color": color,
        "description": listing.reasons[0] if listing.reasons else "",
        "fields": fields,
        "footer": {"text": source},
    }
    if r.photo_urls:
        embed["image"] = {"url": r.photo_urls[0]}
    return embed
