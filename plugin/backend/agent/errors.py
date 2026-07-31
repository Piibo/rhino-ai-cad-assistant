"""agent.errors - API-Fehler-Klassifikation/-Formatierung + WS-Emission."""
from __future__ import annotations

import logging
import re
from typing import Any

from .. import schemas
from ..config import config
from ..websocket_manager import manager

logger = logging.getLogger("FurniturePlugin.Agent")

# Haertung: API-Keys nie in Log/WS/Studiendaten durchsickern lassen. SDK-
# Exceptions koennen den Key in den Fehlertext packen; ausserdem laufen Fehler
# seit dem agent_error-Event in den Export. Darum jeden nutzer-/persistenz-
# seitigen Fehlertext durch _sanitize_secrets() schicken.
_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def _sanitize_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for secret in (
        getattr(config, "api_key", None),
        getattr(config, "local_api_key", None),
    ):
        if secret and len(secret) >= 6 and secret in out:
            out = out.replace(secret, "***")
    return _SK_KEY_RE.sub("sk-***", out)


def _format_api_error_message(e: Exception, prefix: str = "API-Fehler") -> str:
    text = _sanitize_secrets(str(e))
    lowered = text.lower()
    if "429" in text or "rate_limit_error" in lowered:
        return (
            f"{prefix}: Rate-Limit erreicht (429). Bitte 20-60 Sekunden warten "
            "oder eine neue Session mit kuerzerer Historie oeffnen und den "
            "letzten Prompt erneut senden."
        )
    if _is_overloaded_api_error(e):
        return (
            f"{prefix}: Der Modell-Dienst ist gerade ueberlastet. "
            "Bitte in wenigen Sekunden erneut versuchen. "
            "Das ist ein temporaerer Serverfehler und liegt nicht an deinem Prompt."
        )
    return f"{prefix}: {text}"


def _is_overloaded_api_error(e: Exception) -> bool:
    text = str(e)
    lowered = text.lower()
    return (
        "overloaded_error" in lowered
        or "'message': 'overloaded'" in lowered
        or '"message": "overloaded"' in lowered
        or "error code: 529" in lowered
        or " 529 " in lowered
    )


async def _emit_error(
    session_id: str, message: str, correlation_id: str | None
) -> None:
    # Defensiv entschaerfen, BEVOR die Meldung irgendwo landet — Broadcast UND
    # agent_error-Persistenz unten nutzen dieselbe message.
    message = _sanitize_secrets(message)
    # Erst in die Studiendaten persistieren, dann broadcasten: der WS-Broadcast
    # ist best-effort (erreicht nur einen lebenden Client) und der logger landet
    # NICHT im Export. Damit die Auswertung jeden Lauf-Fehler sieht, wird bei
    # aktiver Studien-Session ein "agent_error"-Marker in die events-Tabelle
    # geschrieben. Persistenz darf den Broadcast nie blockieren -> try/except.
    try:
        from ..session_store import get_store

        store = get_store()
        study_session = store.get_study_session_for_session(session_id)
        if study_session is not None:
            store.log_study_context_event(
                schemas.StudyContextEvent(
                    study_session_id=study_session.id,
                    event_type="agent_error",
                    payload={"message": message[:1000]},
                )
            )
    except Exception:
        logger.debug("persisting agent_error event failed", exc_info=True)
    await manager.broadcast(
        schemas.WsEvent(
            type="message.error",
            payload={"session_id": session_id, "message": message},
            correlation_id=correlation_id,
        )
    )
