"""Lean-Fragebogen loader (fragebogen-spec.md v1.2.7, Lean-Variante).

Item-Wortlaute leben in ``fragebogen_items.json`` neben diesem Modul,
damit sie (mit Gerhard) ohne Code-Änderung angepasst werden können —
gleiches Prinzip wie ``agency.py``/``agency_items.json``. Die vier
Agency-Items bleiben bewusst in ``agency_items.json`` (Spec §3.3: der
Plugin-Stand ist für den deutschen Wortlaut führend); dieses Modul
merged sie in den Per-Bedingungs-Block.

``fragebogen_version_hash()`` implementiert Spec §10.3: SHA-256 über
den vollständigen serialisierten Item-Wortlaut (inkl. Agency-Items),
gekürzt auf 16 Hex-Stellen. Jede Wortlaut-Änderung erzeugt ein neues
Hash; Antworten bleiben mit dem Hash gespeichert, mit dem sie erhoben
wurden.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from .agency import load_agency_items

logger = logging.getLogger("FurniturePlugin.Fragebogen")

_ITEMS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fragebogen_items.json"
)

_EMPTY_DEFAULT: dict[str, Any] = {
    "_note": "fragebogen_items.json fehlt — bitte Datei wiederherstellen.",
    "version": "missing",
    "scale_likert7": {
        "min": 1,
        "max": 7,
        "anchor_min": "stimme gar nicht zu",
        "anchor_max": "stimme voll zu",
    },
    "per_condition": {
        "intro": "",
        "csi_items": [],
        "competence_items": [],
        "tool_not_used_label": "habe ich nicht genutzt",
        "tool_items": [],
        "feature_flags": {},
        "unused_question": {"werkzeug_label": "", "basis_label": ""},
        "reflection": {"id": "reflection", "label": "", "max_chars": 500},
    },
    "final": {
        "intro": "",
        "preference": {
            "label": "",
            "options": [],
            "why_label": "",
            "why_max_chars": 300,
        },
        "v2": {
            "label": "",
            "scale": {
                "min": -2,
                "max": 2,
                "anchor_min": "",
                "anchor_mid": "",
                "anchor_max": "",
            },
            "dimensions": [],
        },
        "ranking": {"label": "", "max_rank": 3},
        "hybrid": {"id": "hybrid_mode", "label": "", "max_chars": 500},
        "open_questions": [],
    },
    "demographics": {"intro": "", "fields": []},
}


def load_fragebogen() -> dict[str, Any]:
    """Read the instrument definition from disk on every call.

    Same rationale as ``agency.load_agency_items``: edits show up
    without a server restart, and a broken file must not crash the
    survey endpoints mid-study — fall back to the empty default and
    log loudly.
    """
    try:
        with open(_ITEMS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("fragebogen_items.json not usable at %s: %s", _ITEMS_PATH, e)
        return dict(_EMPTY_DEFAULT)


def fragebogen_version_hash() -> str:
    """SHA-256 (first 16 hex chars) over the complete item wording.

    Includes the agency items so a Gerhard edit in either JSON file
    bumps the version. Canonical serialization (sorted keys) keeps the
    hash stable across dict ordering.
    """
    bundle = load_fragebogen()
    agency = load_agency_items()
    payload = {
        "fragebogen": bundle,
        "agency": {
            "scale": {
                "min": agency.scale.min,
                "max": agency.scale.max,
                "anchor_min": agency.scale.anchor_min,
                "anchor_max": agency.scale.anchor_max,
            },
            "items": [
                {"id": i.id, "dimension": i.dimension, "label": i.label}
                for i in agency.items
            ],
        },
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
