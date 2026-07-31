"""Consent-text loader and gate logic (Studienartefakt-Spec §2.2).

The consent text lives in ``consent_text.md`` next to this file so the
wording can be edited (with Gerhard) without touching code. The frontend
fetches the rendered text plus its SHA-256, the participant ticks all
checkboxes, and the submission round-trips the hash so we can verify
the wording the participant saw matches what we expect.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("FurniturePlugin.Consent")

# Four checkboxes: study purpose / recording + pseudonymisation / external
# AI interface (Anthropic, possibly outside the EU) / voluntariness. Spec §2.2
# lists three; the external-interface acknowledgement is the additional one, so
# the transfer to Anthropic is confirmed explicitly rather than implied. Anhang
# E.3 of the thesis documents both these four in-app boxes and the six of the
# printed consent form. Stable IDs so the frontend can map them back without
# copying the German labels around.
CONSENT_CHECKBOX_LABELS: list[str] = [
    "Ich habe die Informationen zum Studienziel gelesen und verstanden.",
    "Ich bin damit einverstanden, dass meine Chat-Eingaben, Tool-Aktionen "
    "und Viewport-Aufnahmen pseudonymisiert aufgezeichnet und ausgewertet "
    "werden.",
    "Mir ist bewusst, dass für die Bearbeitung der Aufgabe Eingaben "
    "(Chattexte, Aufgabeninfos, Modellkontext, Bildanhänge) an eine externe "
    "KI-Schnittstelle (Anthropic) übertragen werden, ggf. auf Server "
    "außerhalb der EU.",
    "Mir ist bewusst, dass die Teilnahme freiwillig ist und ich die "
    "Sitzung jederzeit ohne Angabe von Gründen abbrechen kann.",
]


@dataclass(frozen=True)
class ConsentTextBundle:
    """Container for the consent body + its hash."""

    text: str
    text_hash: str


_TEXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consent_text.md")


def load_consent_text() -> ConsentTextBundle:
    """Read the Markdown body from disk and compute its SHA-256.

    Recomputed on every call so on-disk edits show up without a server
    restart. Hash is over the raw bytes (UTF-8) so a single whitespace
    change moves the hash and is detectable in old `consent` rows.
    """
    try:
        with open(_TEXT_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        # Missing OR unreadable (locked under Windows during an edit, perms)
        # must not block the consent gate — serve the placeholder + warn.
        logger.warning(
            "consent_text.md not readable at %s: %s - serving placeholder",
            _TEXT_PATH,
            e,
        )
        raw = (
            "# Einwilligung\n\n"
            "*Der Einwilligungstext fehlt. Bitte vor dem Pilot "
            "`consent_text.md` befüllen.*\n"
        )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ConsentTextBundle(text=raw, text_hash=digest)
