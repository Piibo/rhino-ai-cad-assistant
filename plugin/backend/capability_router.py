"""Session-aware capability routing for structure, GH, and slider priority."""

from __future__ import annotations

from typing import Iterable

from . import schemas
from .session_store import get_store


def _parameter_actions(param: schemas.ExposedParameter) -> list[schemas.ParameterAction]:
    if param.actions:
        return list(param.actions)
    if param.action is not None:
        return [param.action]
    return []


def _names_for_source(
    parameters: Iterable[schemas.ExposedParameter],
    source: str,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for param in parameters:
        if param.source != source:
            continue
        key = param.name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(param.name)
    return names


def _join_or_none(names: list[str]) -> str:
    return ", ".join(names) if names else "keine"


def _structure_label(context: schemas.EditableStructureContext) -> str:
    bits = [context.title]
    if context.object_name:
        bits.append(context.object_name)
    elif context.object_id:
        bits.append(context.object_id)
    return " | ".join(bit for bit in bits if bit)


def build_capability_router_prompt(session_id: str) -> str:
    store = get_store()
    # The capability router steers parameter / slider / expose_parameters usage
    # — all werkzeug-only affordances. In the basis condition the model has none
    # of those tools, so this guidance is noise that would also make the basis
    # system prompt reference affordances it cannot use (a condition asymmetry
    # the study must avoid — only the visible interaction layer may differ).
    # Return nothing for basis. This block is dynamic and NOT part of the frozen
    # agent.SYSTEM_PROMPT, so gating it is hash-neutral.
    session = store.get_session(session_id)
    if session is not None and session.condition == "basis":
        return ""
    context = store.get_active_structure_context(session_id)
    parameters = store.get_exposed_parameters(session_id)

    structure_names = _names_for_source(parameters, "editable_structure")
    gh_names = _names_for_source(parameters, "gh_slider")
    free_names = _names_for_source(parameters, "agent_exposed")

    lines = [
        "SESSION-CAPABILITY-ROUTER (bindend fuer diese Sitzung):",
        "Prioritaet: 1. aktiver Strukturkontext + automatisch abgeleitete Strukturparameter, 2. GH-Slider-Mirroring, 3. bereits sichtbare freie Agent-Parameter, 4. erst danach neue freie Slider oder freier Workflow.",
    ]

    if context is None:
        lines.append("Aktiver Strukturkontext: keiner")
    else:
        lines.append(
            "Aktiver Strukturkontext: {0} (typ={1}, key={2}, quelle={3})".format(
                _structure_label(context),
                context.structure_type,
                context.structure_key,
                context.source,
            )
        )
        if context.editable_operations:
            ops = ", ".join(context.editable_operations[:6])
            lines.append(f"Erlaubte Struktur-Operationen: {ops}")

    lines.append("Aktive Strukturparameter: " + _join_or_none(structure_names))
    lines.append("Aktive GH-Slider: " + _join_or_none(gh_names))
    lines.append("Aktive freie Agent-Parameter: " + _join_or_none(free_names))

    if context is not None and context.structure_type == "primitive":
        lines.append(
            "Konsequenz: Fuer dieses Objekt zuerst Strukturparameter oder primitive-spezifische Tools nutzen. expose_parameters ist hier kein Ersatz fuer bestehende Strukturkontrolle."
        )
    elif context is not None and context.structure_type == "grasshopper":
        lines.append(
            "Konsequenz: Fuer diesen GH-Kontext zuerst bestehende GH-Slider nutzen. expose_parameters darf hier nur weitere gh_slider-Spiegelungen hinzufuegen, keine freien Rhino-Slider als Ersatz."
        )
        if gh_names:
            lines.append(
                "Hinweis: Existieren auf dem Active-Layer noch zuvor in Rhino gebaute Vorgaenger-Objekte derselben Bauteile, verschiebe sie auf den Archive-Layer, sobald der Designer die GH-Variante bestaetigt oder gebacken hat."
            )
    elif free_names:
        lines.append(
            "Konsequenz: Bereits sichtbare freie Agent-Parameter zuerst wiederverwenden, bevor neue freie Slider exponiert werden."
        )
    else:
        lines.append(
            "Konsequenz: Wenn keine passende Struktur und keine priorisierten Slider aktiv sind, ist der normale freie Rhino-Workflow unveraendert erlaubt."
        )

    return "\n".join(lines)


def guard_expose_parameters(
    session_id: str,
    parameters: list[schemas.ExposedParameter],
) -> str | None:
    store = get_store()
    context = store.get_active_structure_context(session_id)
    if context is None:
        return None

    existing = store.get_exposed_parameters(session_id)
    structure_names = _names_for_source(existing, "editable_structure")
    gh_names = _names_for_source(existing, "gh_slider")

    if context.structure_type == "primitive" and context.object_id and structure_names:
        conflicting: list[str] = []
        active_object_id = context.object_id
        for param in parameters:
            if param.source == "gh_slider":
                continue
            if param.structure_id == context.id or param.structure_key == context.structure_key:
                conflicting.append(param.name)
                continue
            for action in _parameter_actions(param):
                if active_object_id in (action.target_object_ids or []):
                    conflicting.append(param.name)
                    break
        if conflicting:
            return (
                "Router: Fuer den aktiven Strukturkontext '{0}' sind bereits "
                "priorisierte Strukturparameter aktiv ({1}). Nutze diese "
                "Strukturkontrolle zuerst; expose_parameters fuer {2} ist hier "
                "kein zulaessiger Ersatz."
            ).format(
                _structure_label(context),
                ", ".join(structure_names),
                ", ".join(conflicting),
            )

    if context.structure_type == "grasshopper" and gh_names:
        non_gh = [param.name for param in parameters if param.source != "gh_slider"]
        if non_gh:
            return (
                "Router: Fuer den aktiven GH-Kontext sind bereits gespiegelte "
                "GH-Slider aktiv ({0}). expose_parameters darf hier nur weitere "
                "gh_slider-Spiegelungen hinzufuegen, nicht die freien Parameter {1}."
            ).format(
                ", ".join(gh_names),
                ", ".join(non_gh),
            )

    return None


__all__ = [
    "build_capability_router_prompt",
    "guard_expose_parameters",
]
