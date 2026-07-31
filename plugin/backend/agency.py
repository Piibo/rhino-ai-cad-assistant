"""Agency-survey loader (Studienartefakt-Spec §2.5).

Items live in ``agency_items.json`` next to this module so the wording
can be edited (with Gerhard) without touching code. Items are versioned
by their position; a Bestätigung is bound to a study_session +
condition + raw responses, not to an item-text hash, because we want
the survey rows to remain interpretable if the wording is fixed up
between pilot and main study without dropping pilot data.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("FurniturePlugin.Agency")

_ITEMS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "agency_items.json"
)


@dataclass(frozen=True)
class AgencyScale:
    min: int
    max: int
    anchor_min: str
    anchor_max: str


@dataclass(frozen=True)
class AgencyItem:
    id: str
    dimension: str
    label: str


@dataclass(frozen=True)
class AgencyItems:
    scale: AgencyScale
    items: list[AgencyItem]
    note: str


def load_agency_items() -> AgencyItems:
    """Read the survey item set from disk on every call.

    Same rationale as ``consent.load_consent_text``: edits to the JSON
    show up without a server restart and surveys handed out late in
    the study can stay in lockstep with the file on disk.
    """
    try:
        with open(_ITEMS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # Missing, unreadable (locked under Windows during an edit), or
        # malformed (a stray comma after a Gerhard edit) must NOT crash the
        # agency-survey endpoint mid-study — fall back to the empty default
        # and log loudly so it gets noticed.
        logger.error("agency_items.json not usable at %s: %s", _ITEMS_PATH, e)
        raw = {
            "_note": "Default items missing — bitte agency_items.json befüllen.",
            "scale": {"min": 1, "max": 7, "anchor_min": "", "anchor_max": ""},
            "items": [],
        }
    scale_dict = raw.get("scale", {})
    scale = AgencyScale(
        min=int(scale_dict.get("min", 1)),
        max=int(scale_dict.get("max", 7)),
        anchor_min=str(scale_dict.get("anchor_min", "")),
        anchor_max=str(scale_dict.get("anchor_max", "")),
    )
    items = [
        AgencyItem(
            id=str(it["id"]),
            dimension=str(it["dimension"]),
            label=str(it["label"]),
        )
        for it in raw.get("items", [])
        if isinstance(it, dict) and "id" in it and "label" in it
    ]
    note = str(raw.get("_note", ""))
    return AgencyItems(scale=scale, items=items, note=note)
