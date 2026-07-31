"""agent.loop - run_agent + die zwei Treiber-Loops (Top des DAG)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from .. import schemas
from ..capability_router import build_capability_router_prompt
from ..config import config
from ..rhino_exec import decode_run_code_result
from ..session_store import get_store
from ..tool_registry import build_tool_list, DIALOG_TOOL_NAMES, INTERACTION_TOOL_NAMES
from ..viewport_bridge import fit_ortho_viewports_if_changed
from ..websocket_manager import manager
from .prompts import SYSTEM_PROMPT
from .history import _history_to_api, _filter_for_role, _block_dict, _summarise
from .errors import _emit_error, _format_api_error_message, _is_overloaded_api_error
from .dispatch import _execute_tool, _persist_tool_call_and_maybe_snapshot, _result_signals_error
from .local_adapter import _tools_anthropic_to_openai, _messages_anthropic_to_openai

logger = logging.getLogger("FurniturePlugin.Agent")

# Runtime tool-surface audit (Pilot 01.07.2026). The export manifest
# re-derives the tools hash from the *current* code at export time, so a
# runtime leak (a basis run served the full interaction surface by a stale
# build) stays invisible in the bundle. We log the ACTUAL tool list handed
# to the model — once per (session, surface) — so any mismatch with the
# gated basis surface is visible in the study data itself.
_LOGGED_TOOL_SURFACES: set[tuple[str, str]] = set()


def _log_tool_surface(
    session_id: str, condition: str, tool_list: list[dict[str, Any]]
) -> None:
    try:
        names = sorted(t.get("name", "") for t in tool_list)
        sha = hashlib.sha256(
            json.dumps(tool_list, sort_keys=True).encode("utf-8")
        ).hexdigest()
        key = (session_id, sha)
        if key in _LOGGED_TOOL_SURFACES:
            return
        store = get_store()
        study_session = store.get_study_session_for_session(session_id)
        if study_session is None:
            return
        _LOGGED_TOOL_SURFACES.add(key)
        leaked = sorted(n for n in names if n in INTERACTION_TOOL_NAMES)
        store.log_study_context_event(
            schemas.StudyContextEvent(
                study_session_id=study_session.id,
                event_type="tool_surface",
                payload={
                    "condition": condition,
                    "tool_count": len(tool_list),
                    "tools_sha256": sha,
                    # Must be [] in basis. Anything else is a live leak.
                    "interaction_tools_present": leaked,
                },
            )
        )
        if condition == "basis" and leaked:
            logger.error(
                "CONDITION LEAK: basis session %s exposed interaction tools %s",
                session_id,
                leaked,
            )
    except Exception:
        # Warning statt debug: ein still scheiterndes tool_surface-Event hat
        # den Literal-Luecken-Bug 5 Tage verdeckt (01.-06.07.) — der Leak-
        # Beleg fehlte in allen Bundles, ohne dass es jemand sehen konnte.
        logger.warning("tool_surface logging failed", exc_info=True)


def _with_history_cache_breakpoint(
    api_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy of ``api_messages`` with a cache breakpoint on the last block.

    Token-Fix (Pilot 01.07.2026): System+Tools were already cached, but the
    conversation history — by far the largest and fastest-growing part of the
    request — was re-billed at full input price on EVERY tool round. A long
    session (~110 API calls, ~67k-token history) burned ~5M uncached input
    tokens ≈ $15; with this breakpoint the previously-seen history is a cache
    read (0.1x) and only each turn's delta is written (analysis:
    ~75-82% cheaper). Shallow copies only — the persisted history is never
    mutated and breakpoints don't accumulate across turns (max 4 allowed;
    we use 1 here + 1 on the system block). 1h TTL matches the system
    block's rationale: designer think-pauses in study sessions routinely
    exceed the 5-minute default TTL.
    """
    if not api_messages:
        return api_messages
    msgs = list(api_messages)
    last = dict(msgs[-1])
    content = last.get("content")
    if isinstance(content, list) and content:
        blocks = list(content)
        last_block = blocks[-1]
        if isinstance(last_block, dict) and last_block.get("type") in (
            "text",
            "image",
            "tool_use",
            "tool_result",
        ):
            last_block = dict(last_block)
            last_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            blocks[-1] = last_block
            last["content"] = blocks
            msgs[-1] = last
    return msgs


async def _emit_max_turns_pause(
    session_id: str, correlation_id: str | None, max_turns: int, model: str
) -> None:
    """Graceful stop when the tool-round cap is hit (Pilot 01.07.2026).

    A red error toast read to participants as "it broke". Instead surface a
    normal, resumable assistant message + message.complete, and log a study
    event so the analysis still sees the cap was reached."""
    text = (
        f"Ich habe für diese Runde die maximale Schrittzahl ({max_turns}) "
        "erreicht und pausiere hier, damit du die Kontrolle behältst. "
        "Sag „weiter“, wenn ich am nächsten Schritt weitermachen soll."
    )
    try:
        store = get_store()
        study_session = store.get_study_session_for_session(session_id)
        if study_session is not None:
            store.log_study_context_event(
                schemas.StudyContextEvent(
                    study_session_id=study_session.id,
                    event_type="max_iterations_reached",
                    payload={"max_turns": max_turns},
                )
            )
        assistant_msg = schemas.Message(
            session_id=session_id,
            role="assistant",
            content=[{"type": "text", "text": text}],
            model=model,
            stop_reason="end_turn",
        )
        store.add_message(assistant_msg)
    except Exception:
        logger.debug("persisting max-turns pause failed", exc_info=True)
        assistant_msg = schemas.Message(
            session_id=session_id,
            role="assistant",
            content=[{"type": "text", "text": text}],
            model=model,
            stop_reason="end_turn",
        )
    await manager.broadcast(
        schemas.WsEvent(
            type="message.new",
            payload=assistant_msg.model_dump(mode="json"),
            correlation_id=correlation_id,
        )
    )
    await manager.broadcast(
        schemas.WsEvent(
            type="message.complete",
            payload={"session_id": session_id, "turn": max_turns},
            correlation_id=correlation_id,
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_agent(session_id: str, correlation_id: str | None = None) -> None:
    """Run one Claude turn loop for the given session. Fire-and-forget.

    Wrapped in a blanket try/except so that any unexpected exception (SDK
    init error, httpx timeout, whatever) surfaces as ``message.error`` to
    the UI instead of silently killing the task.
    """
    # Zeitstempel VOR der Operation: damit der Created-Flash nach dem Run
    # GENAU die in diesem Run erzeugten Objekte aus der Action-History
    # herausfiltern kann (Aktionen mit started_at >= run_start_ts).
    run_start_ts = time.time()
    # Neue Operation = frische Gate-Entscheidung: die run-weite accept/revert-
    # Entscheidung des vorigen Runs darf nicht durchsickern, sonst zeigt der
    # erste destruktive Schritt dieser Instruktion keine Vorschaukarte mehr.
    try:
        from .gate import gate_registry

        gate_registry.clear_run_decision(session_id, correlation_id)
    except Exception:
        logger.debug("gate run-decision reset at start failed", exc_info=True)
    try:
        await _run_agent_inner(session_id, correlation_id)
        # Nach einem erfolgreichen Turn die Ortho-Ansichten (Top/Front/Right) auf
        # die — ggf. neue/groessere — Geometrie einpassen, damit ein frisch
        # erzeugtes Objekt nicht zu nah/zu fern sitzt. Perspektive bleibt
        # unberuehrt; No-Op, wenn sich die Geometrie nicht geaendert hat. Laeuft
        # NUR auf dem normalen Pfad (nicht bei Cancel/Crash, die die Zeile
        # ueberspringen). Voll defensiv: ein Fehler hier darf den Turn nie
        # nachtraeglich kippen.
        try:
            await asyncio.to_thread(fit_ortho_viewports_if_changed)
        except Exception:
            logger.debug("ortho auto-fit after turn failed", exc_info=True)
        # Created-Flash: die in DIESEM Run erzeugten Objekte kurz blau
        # aufleuchten lassen ("wurde erstellt"-Feedback). Sichtbare Feedback-/
        # Interaktionsschicht -> wie die anderen Viewport-Affordanzen NUR in
        # der werkzeug-Bedingung (Basis bleibt chat-only, saubere Conditions).
        # Nur auf dem normalen Pfad (nicht bei Cancel/Crash). Voll defensiv:
        # ein Fehler hier darf den fertigen Turn nie nachtraeglich kippen.
        try:
            row = get_store().get_session(session_id)
            # Fail SAFE: a missing row must NOT flash in basis (the flash is a
            # werkzeug-only affordance). Default to basis when the row is absent.
            if (row.condition if row else "basis") == "werkzeug":
                from ..viewport_bridge import flash_run_created

                flash_run_created(run_start_ts)
        except Exception:
            logger.debug("created-object flash after turn failed", exc_info=True)
    except asyncio.CancelledError:
        # Run cancelled (chat.cancel cancelled the task). Any gate await in
        # flight already saw the CancelledError and tidied its own card; this
        # is the belt-and-suspenders sweep so no gate of this session lingers
        # if the cancel landed between tool calls.
        try:
            from .gate import gate_registry

            gate_registry.cancel_session(session_id)
        except Exception:
            logger.debug("gate sweep on cancel failed", exc_info=True)
        raise
    except Exception as e:
        logger.exception("run_agent crashed: %s", e)
        try:
            await _emit_error(
                session_id, f"Agent abgestürzt: {e}", correlation_id
            )
        except Exception:
            logger.exception("failed to emit crash error")
    finally:
        # On any normal end / error path, make sure no gate of this session
        # is left pending (would otherwise hold a future + card forever).
        try:
            from .gate import gate_registry

            if gate_registry.has_pending(session_id):
                gate_registry.cancel_session(session_id)
            # Run-weite Entscheidung am Ende verwerfen (der naechste Run setzt
            # sie ohnehin am Start zurueck; das hier ist belt-and-suspenders).
            gate_registry.clear_run_decision(session_id, correlation_id)
            # Provenance-Eintraege dieser Session fegen: ein per chat.cancel
            # abgebrochener cached-accept-Schritt persistiert nicht (CancelledError
            # umgeht das pop) -> sonst verwaister Eintrag. Dieses finally laeuft
            # auch bei Cancel, daher hier sicher.
            gate_registry.clear_session_provenance(session_id)
        except Exception:
            logger.debug("gate sweep at run end failed", exc_info=True)


async def _run_agent_inner(session_id: str, correlation_id: str | None) -> None:
    # Provider switch — keep the Anthropic hot path untouched. The local
    # branch lives in its own function and routes through litellm so the
    # study-mode flow on Opus/Sonnet keeps the original SDK semantics
    # (streaming aggregation, cache_control, exact usage accounting).
    if config.is_local_model:
        await _run_agent_inner_local(session_id, correlation_id)
        return

    logger.info("agent start — session=%s model=%s", session_id, config.model)
    if not config.api_key:
        await _emit_error(
            session_id,
            "Kein API-Key hinterlegt. Bitte in den Einstellungen setzen.",
            correlation_id,
        )
        return

    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:
        await _emit_error(
            session_id,
            f"anthropic SDK nicht verfügbar: {e}",
            correlation_id,
        )
        return

    try:
        # Explicit timeout + capped retries so a stalled connection surfaces
        # as an error inside a minute instead of leaving the UI in pendingSend
        # forever. SDK default is multi-minute with silent retries.
        client = AsyncAnthropic(
            api_key=config.api_key,
            timeout=60.0,
            max_retries=1,
        )
    except Exception as e:
        logger.exception("AsyncAnthropic init failed: %s", e)
        await _emit_error(session_id, f"SDK-Init fehlgeschlagen: {e}", correlation_id)
        return

    store = get_store()

    # Build API message history from persisted content blocks.
    try:
        api_messages = _history_to_api(store.list_messages(session_id))
    except Exception as e:
        logger.exception("failed to build message history: %s", e)
        await _emit_error(session_id, f"Historie ungültig: {e}", correlation_id)
        return

    # Resolve the study condition once per agent run. The session row is
    # immutable for ``condition`` after creation (Studienartefakt-Spec §1.2),
    # so we don't need to re-read it per turn. Fail SAFE: a missing session
    # defaults to ``basis`` (the smaller, interaction-free toolset) rather than
    # handing the model the full werkzeug surface in what may be a basis run.
    session_row = store.get_session(session_id)
    session_condition = session_row.condition if session_row else "basis"
    tool_list = build_tool_list(session_condition)
    logger.info(
        "agent → %d message(s) in history, condition=%s, tools=%d",
        len(api_messages),
        session_condition,
        len(tool_list),
    )
    _log_tool_surface(session_id, session_condition, tool_list)

    turn = 0
    # Bound on tool-use rounds; configurable via Settings → max_iterations.
    # Multi-step modeling flows (Grasshopper builds especially) chew
    # through rounds quickly; default 25 is comfortable, the user can
    # raise it for more autonomous work.
    max_turns = max(1, int(getattr(config, "max_iterations", 25) or 25))
    while turn < max_turns:
        turn += 1
        dynamic_router_prompt = build_capability_router_prompt(session_id)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "turn %d → sending %d messages: %s",
                turn,
                len(api_messages),
                [_summarise(m) for m in api_messages],
            )
        response = None
        last_api_error: Exception | None = None
        for api_attempt in range(3):
            try:
                # Stream instead of create(): prevents timeouts on long responses,
                # and get_final_message() aggregates events into the same Message
                # shape create() would have returned.
                # System block uses cache_control so the ~600-char German prompt +
                # tool definitions (tools render before system) are cached across
                # turns — prefix match, so keep them byte-stable.
                system_blocks = [
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        # 1h TTL (GA, kein Beta-Header): haelt das ~26k grosse
                        # System+Tools-Praefix ueber die >5min-Denkpausen warm,
                        # die im interaktiven Entwurf typisch sind, statt es bei
                        # jeder Pause neu zu schreiben (1.25x). Der 1h-Write
                        # kostet 2x, amortisiert sich aber ueber die vielen
                        # Reads innerhalb der Stunde.
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ]
                if dynamic_router_prompt:
                    system_blocks.append(
                        {
                            "type": "text",
                            "text": dynamic_router_prompt,
                        }
                    )
                # Locked-objects addendum (Studienartefakt-Spec §1.1, P5).
                # Appended after cache_control so adding/removing a lock
                # never invalidates the cached SYSTEM_PROMPT prefix.
                lock_addendum = _locked_objects_system_addendum(session_id)
                if lock_addendum:
                    system_blocks.append({"type": "text", "text": lock_addendum})
                async with client.messages.stream(
                    model=config.model,
                    max_tokens=int(config.max_tokens),
                    system=system_blocks,
                    tools=tool_list,
                    # History-Cache-Breakpoint: Praefix (Tools+System+bisherige
                    # Historie) wird als Cache-Read (0.1x) abgerechnet statt
                    # jede Runde voll — der groesste Kostenhebel im Agent-Loop.
                    messages=_with_history_cache_breakpoint(api_messages),
                    # Adaptives Denken abschalten: Sonnet 5 (u.a.) hat es sonst
                    # per Default an und liefert `thinking`-Bloecke zurueck, die
                    # das Message-Schema (text/image/tool_*/pick/sketch) nicht
                    # kennt -> Validierungs-Absturz. Fuers Studien-Setting zudem
                    # erwuenscht: schnellere, vorhersehbarere Antworten.
                    # {"type": "disabled"} ist auf allen erlaubten Claude-Modellen
                    # gueltig (nur Fable 5 wuerde es ablehnen, ist aber nicht in
                    # _KNOWN_MODELS).
                    thinking={"type": "disabled"},
                ) as stream:
                    response = await stream.get_final_message()
                break
            except Exception as e:
                last_api_error = e
                if _is_overloaded_api_error(e) and api_attempt < 2:
                    delay_s = 2 * (api_attempt + 1)
                    logger.warning(
                        "Anthropic overloaded on attempt %d/3 - retrying in %ss: %s",
                        api_attempt + 1,
                        delay_s,
                        e,
                    )
                    await asyncio.sleep(delay_s)
                    continue
                logger.exception("Anthropic call failed: %s", e)
                logger.warning(
                    "api_messages on failure: %s",
                    [_summarise(m) for m in api_messages],
                )
                await _emit_error(
                    session_id, _format_api_error_message(e), correlation_id
                )
                return
        if response is None:
            await _emit_error(
                session_id,
                _format_api_error_message(
                    last_api_error or RuntimeError("Unbekannter API-Fehler")
                ),
                correlation_id,
            )
            return

        # Visibility into caching: first turn populates cache_creation_input_tokens,
        # subsequent turns should read cache_read_input_tokens. If cache_read stays
        # zero a silent invalidator is at work (check tool definitions / system text).
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "turn %d usage — in=%s out=%s cache_create=%s cache_read=%s",
                turn,
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_creation_input_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
            )

        assistant_blocks = _filter_for_role(
            "assistant", [_block_dict(b) for b in response.content]
        )

        # Guard (spiegelt den lokalen LLM-Pfad): bleibt nach dem Rollen-Filter
        # nichts uebrig — z. B. weil die API nur einen nicht-gelisteten Block wie
        # 'refusal' lieferte —, sonst wuerde eine content=[]-Nachricht persistiert
        # (stiller Datenverlust im Studien-Audit). Platzhalter statt Leere.
        if not assistant_blocks:
            placeholder = (
                "(Anfrage vom Modell abgelehnt)"
                if response.stop_reason == "refusal"
                else "(leere Antwort)"
            )
            logger.warning(
                "Leere Assistent-Antwort nach Rollen-Filter (stop_reason=%s) — "
                "Platzhalter gespeichert statt content=[]",
                response.stop_reason,
            )
            assistant_blocks = [{"type": "text", "text": placeholder}]

        assistant_msg = schemas.Message(
            session_id=session_id,
            role="assistant",
            content=assistant_blocks,  # Pydantic validates into ContentBlocks
            model=response.model,
            stop_reason=response.stop_reason,
        )
        store.add_message(assistant_msg)
        await manager.broadcast(
            schemas.WsEvent(
                type="message.new",
                payload=assistant_msg.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        )

        if response.stop_reason != "tool_use":
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.complete",
                    payload={"session_id": session_id, "turn": turn},
                    correlation_id=correlation_id,
                )
            )
            return

        # Tool loop: execute every tool_use, build a single tool_result user msg.
        # Each call also writes a ``tool_calls`` row (§2.4 auto-logging) and,
        # when study mode applies and the tool is modifying, a fire-and-forget
        # ``model_states`` snapshot.
        api_messages.append({"role": "assistant", "content": assistant_blocks})
        tool_result_blocks: list[dict[str, Any]] = []
        study_session = store.get_study_session_for_session(session_id)
        has_study_session = (
            study_session is not None and study_session.status == "active"
        )
        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            name = block["name"]
            tu_id = block["id"]
            tool_input = block.get("input", {})
            started_at = datetime.now(timezone.utc)
            started_perf = time.perf_counter()
            try:
                result_content = await _execute_tool(
                    name,
                    tool_input,
                    session_id=session_id,
                    correlation_id=correlation_id,
                    tu_id=tu_id,
                    condition=session_condition,
                )
                # Mirror the failure signal back as is_error=True so the
                # model gets a strong cue instead of just text starting
                # with "Fehler:". Without this, the same broken tool gets
                # retried by the model before it parses the error string.
                tool_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": result_content,
                }
                if _result_signals_error(result_content):
                    tool_block["is_error"] = True
                tool_result_blocks.append(tool_block)
            except Exception as e:
                logger.exception("tool %s raised: %s", name, e)
                result_content = f"Tool-Fehler: {e}"
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": result_content,
                        "is_error": True,
                    }
                )
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            await _persist_tool_call_and_maybe_snapshot(
                session_id=session_id,
                message_id=assistant_msg.id,
                tool_name=name,
                tool_input=tool_input,
                result_content=result_content,
                started_at=started_at,
                duration_ms=duration_ms,
                has_study_session=has_study_session,
                tu_id=tu_id,
            )

        tr_msg = schemas.Message(
            session_id=session_id,
            role="user",
            content=tool_result_blocks,
        )
        store.add_message(tr_msg)
        await manager.broadcast(
            schemas.WsEvent(
                type="message.new",
                payload=tr_msg.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        )
        # Dialog cards pause the turn to await the participant — but only in
        # werkzeug, where the card actually renders. In basis a dialog
        # tool_use must NOT stall the turn on a card nobody sees (Pilot
        # 01.07.2026, Bug B: post-fix this shouldn't occur, but guard anyway
        # so the loop keeps working through the dispatch "unavailable" result).
        if session_condition == "werkzeug" and _contains_dialog_request(
            assistant_blocks
        ):
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.complete",
                    payload={"session_id": session_id, "turn": turn},
                    correlation_id=correlation_id,
                )
            )
            return
        short_circuit_text = _maybe_short_circuit_after_tools(
            assistant_blocks, tool_result_blocks
        )
        if short_circuit_text:
            assistant_msg = schemas.Message(
                session_id=session_id,
                role="assistant",
                content=[{"type": "text", "text": short_circuit_text}],
                model=config.model,
                stop_reason="end_turn",
            )
            store.add_message(assistant_msg)
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.new",
                    payload=assistant_msg.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.complete",
                    payload={"session_id": session_id, "turn": turn},
                    correlation_id=correlation_id,
                )
            )
            return
        api_messages.append({"role": "user", "content": tool_result_blocks})

    await _emit_max_turns_pause(
        session_id, correlation_id, max_turns, config.model
    )


# ---------------------------------------------------------------------------
# Local-LLM path (LM Studio / Ollama via litellm, OpenAI-compatible)
# ---------------------------------------------------------------------------


async def _run_agent_inner_local(
    session_id: str, correlation_id: str | None
) -> None:
    """Drive a chat turn against an OpenAI-compatible endpoint (local or cloud).

    Mirrors the Anthropic flow above but converts the persisted Anthropic
    block format to OpenAI chat-completions on the way out and back. The
    rest of the plugin (UI, SQLite, tool dispatch) keeps speaking Anthropic
    so nothing else has to know the local path exists. Local models can be
    slow on first token, so the timeout is bumped to 180 s.
    """
    logger.info(
        "agent (local) start — session=%s base_url=%s model=%s",
        session_id,
        config.local_base_url,
        config.local_model,
    )

    try:
        import litellm
    except ImportError as e:
        await _emit_error(
            session_id,
            (
                "litellm nicht verfügbar — bitte im Plugin-Backend "
                f"`pip install litellm` ausführen ({e})."
            ),
            correlation_id,
        )
        return

    store = get_store()
    try:
        anthropic_messages = _history_to_api(store.list_messages(session_id))
    except Exception as e:
        logger.exception("failed to build message history: %s", e)
        await _emit_error(session_id, f"Historie ungültig: {e}", correlation_id)
        return

    # Same per-session condition lookup as the Anthropic path
    # (Studienartefakt-Spec §1.2). Local LLMs see the same gated tool set.
    # Fail SAFE to basis on a missing row (mirrors the Anthropic path).
    session_row = store.get_session(session_id)
    session_condition = session_row.condition if session_row else "basis"
    tool_list = build_tool_list(session_condition)
    openai_tools = _tools_anthropic_to_openai(tool_list)
    logger.info(
        "agent (local) → condition=%s, tools=%d",
        session_condition,
        len(tool_list),
    )
    _log_tool_surface(session_id, session_condition, tool_list)

    turn = 0
    max_turns = max(1, int(getattr(config, "max_iterations", 25) or 25))
    while turn < max_turns:
        turn += 1
        dynamic_router_prompt = build_capability_router_prompt(session_id)
        lock_addendum = _locked_objects_system_addendum(session_id)
        parts = [SYSTEM_PROMPT]
        if dynamic_router_prompt:
            parts.append(dynamic_router_prompt)
        if lock_addendum:
            parts.append(lock_addendum)
        system_prompt = "\n\n".join(parts)
        openai_messages = _messages_anthropic_to_openai(
            anthropic_messages, system=system_prompt
        )
        try:
            response = await litellm.acompletion(
                # ``openai/<model>`` + ``api_base`` is the generic shape for
                # any OpenAI-compatible server (LM Studio, llama.cpp, vLLM).
                # Ollama users can swap to ``ollama/<model>`` if they prefer.
                model=f"openai/{config.local_model}",
                api_base=config.local_base_url,
                # Real key for cloud providers (OpenAI / OpenRouter); falls
                # back to a dummy for LM Studio / Ollama which ignore auth.
                api_key=config.local_api_key or "local-dummy",
                max_tokens=int(config.max_tokens),
                messages=openai_messages,
                tools=openai_tools or None,
                tool_choice="auto" if openai_tools else None,
                timeout=180.0,
            )
        except Exception as e:
            logger.exception("litellm call failed: %s", e)
            await _emit_error(
                session_id, _format_api_error_message(e, prefix="Local-LLM-Fehler"), correlation_id
            )
            return

        try:
            choice = response.choices[0]
            message = choice.message
            finish_reason = choice.finish_reason
        except (AttributeError, IndexError) as e:
            logger.exception("malformed litellm response: %s", e)
            await _emit_error(
                session_id,
                f"Local-LLM lieferte unerwartete Antwort: {e}",
                correlation_id,
            )
            return

        assistant_blocks: list[dict[str, Any]] = []
        text = getattr(message, "content", None)
        if text:
            assistant_blocks.append({"type": "text", "text": text})
        tool_calls = getattr(message, "tool_calls", None) or []
        for tc in tool_calls:
            try:
                raw_args = tc.function.arguments or "{}"
                tool_input = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                # Local models sometimes emit non-JSON arg blobs — keep the
                # raw string so the user can see what happened.
                tool_input = {"_raw": tc.function.arguments}
            assistant_blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": tool_input,
                }
            )

        if not assistant_blocks:
            assistant_blocks = [{"type": "text", "text": "(leere Antwort)"}]

        stop_reason = (
            "tool_use" if finish_reason == "tool_calls" else "end_turn"
        )

        assistant_msg = schemas.Message(
            session_id=session_id,
            role="assistant",
            content=assistant_blocks,
            model=getattr(response, "model", config.local_model),
            stop_reason=stop_reason,
        )
        store.add_message(assistant_msg)
        await manager.broadcast(
            schemas.WsEvent(
                type="message.new",
                payload=assistant_msg.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        )

        if stop_reason != "tool_use":
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.complete",
                    payload={"session_id": session_id, "turn": turn},
                    correlation_id=correlation_id,
                )
            )
            return

        anthropic_messages.append(
            {"role": "assistant", "content": assistant_blocks}
        )
        # Same auto-logging hook as the Anthropic path (Studienartefakt-
        # Spec §2.4 writers).
        tool_result_blocks: list[dict[str, Any]] = []
        study_session = store.get_study_session_for_session(session_id)
        has_study_session = (
            study_session is not None and study_session.status == "active"
        )
        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_name = block["name"]
            tool_input = block.get("input", {}) or {}
            started_at = datetime.now(timezone.utc)
            started_perf = time.perf_counter()
            try:
                result_content = await _execute_tool(
                    tool_name,
                    tool_input,
                    session_id=session_id,
                    correlation_id=correlation_id,
                    tu_id=block.get("id"),
                    condition=session_condition,
                )
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": result_content,
                    }
                )
            except Exception as e:
                logger.exception("tool %s raised: %s", tool_name, e)
                result_content = f"Tool-Fehler: {e}"
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": result_content,
                        "is_error": True,
                    }
                )
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            await _persist_tool_call_and_maybe_snapshot(
                session_id=session_id,
                message_id=assistant_msg.id,
                tool_name=tool_name,
                tool_input=tool_input if isinstance(tool_input, dict) else {"_raw": str(tool_input)},
                result_content=result_content,
                started_at=started_at,
                duration_ms=duration_ms,
                has_study_session=has_study_session,
                tu_id=block.get("id"),
            )

        tr_msg = schemas.Message(
            session_id=session_id,
            role="user",
            content=tool_result_blocks,
        )
        store.add_message(tr_msg)
        await manager.broadcast(
            schemas.WsEvent(
                type="message.new",
                payload=tr_msg.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        )
        # Dialog cards pause the turn to await the participant — but only in
        # werkzeug, where the card actually renders. In basis a dialog
        # tool_use must NOT stall the turn on a card nobody sees (Pilot
        # 01.07.2026, Bug B: post-fix this shouldn't occur, but guard anyway
        # so the loop keeps working through the dispatch "unavailable" result).
        if session_condition == "werkzeug" and _contains_dialog_request(
            assistant_blocks
        ):
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.complete",
                    payload={"session_id": session_id, "turn": turn},
                    correlation_id=correlation_id,
                )
            )
            return
        short_circuit_text = _maybe_short_circuit_after_tools(
            assistant_blocks, tool_result_blocks
        )
        if short_circuit_text:
            assistant_msg = schemas.Message(
                session_id=session_id,
                role="assistant",
                content=[{"type": "text", "text": short_circuit_text}],
                model=getattr(response, "model", config.local_model),
                stop_reason="end_turn",
            )
            store.add_message(assistant_msg)
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.new",
                    payload=assistant_msg.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.complete",
                    payload={"session_id": session_id, "turn": turn},
                    correlation_id=correlation_id,
                )
            )
            return
        anthropic_messages.append(
            {"role": "user", "content": tool_result_blocks}
        )

    await _emit_max_turns_pause(
        session_id, correlation_id, max_turns, config.local_model
    )


def _maybe_short_circuit_after_tools(
    assistant_blocks: list[dict[str, Any]],
    tool_result_blocks: list[dict[str, Any]],
) -> str | None:
    # GH-not-connected: a deterministic situation that needs no LLM
    # round-trip. If any GH tool came back with the "Grasshopper not
    # connected" sentinel, end the turn right here with a fixed,
    # actionable note instead of paraphrasing it through the model.
    # Checked before the single-tool restriction below because the LLM
    # may have fired several GH tools, all of which fail the same way.
    from ..grasshopper_bridge import GH_NOT_CONNECTED_MESSAGE

    for block in tool_result_blocks:
        content = block.get("content")
        if isinstance(content, str) and GH_NOT_CONNECTED_MESSAGE in content:
            # By the time this sentinel surfaces, the bridge already tried to
            # launch + poll Grasshopper itself (see grasshopper_bridge
            # auto-connect) and the retry still failed — so the message is no
            # longer "you forgot to connect" but "the automatic start didn't
            # work, please do it manually".
            return (
                "Automatischer Grasshopper-Start fehlgeschlagen — klick "
                "den GH-Punkt oben im Plugin und versuch es erneut."
            )

    tool_names: dict[str, str] = {
        block["id"]: block["name"]
        for block in assistant_blocks
        if block.get("type") == "tool_use"
        and isinstance(block.get("id"), str)
        and isinstance(block.get("name"), str)
    }
    if len(tool_names) != 1:
        return None

    tool_use_id, tool_name = next(iter(tool_names.items()))
    if tool_name != "get_selected":
        return None

    result_block = next(
        (
            block
            for block in tool_result_blocks
            if block.get("tool_use_id") == tool_use_id
        ),
        None,
    )
    if not result_block:
        return None

    content = result_block.get("content")
    if not isinstance(content, str):
        return None

    # ``content`` is the repr-gewrappte run_code-Ausgabe von get_selected
    # (result = json.dumps({"count": ...})). Ein direktes json.loads scheiterte
    # bislang immer an der repr-Huelle (Single-Quotes) -> dieser Kurzschluss war
    # toter Code. decode_run_code_result zieht erst die Huelle ab, dann JSON.
    payload = decode_run_code_result(content)
    if isinstance(payload, dict) and payload.get("count") == 0:
        return (
            "Es ist aktuell nichts ausgewaehlt. Bitte waehle zuerst das "
            "gemeinte Objekt in Rhino aus und sende den Prompt dann in "
            "dieser oder einer neuen Session erneut."
        )
    return None


def _contains_dialog_request(assistant_blocks: list[dict[str, Any]]) -> bool:
    return any(
        block.get("type") == "tool_use" and block.get("name") in DIALOG_TOOL_NAMES
        for block in assistant_blocks
    )


def _locked_objects_system_addendum(session_id: str) -> str:
    """Return the system-prompt snippet that warns the model off any
    locked objects in this session. Returns "" when nothing is locked
    so the system prompt stays cache-stable for unrestricted runs."""
    try:
        locks = get_store().list_locked_objects(session_id)
    except Exception as e:
        logger.warning("locked-objects lookup failed: %s", e)
        return ""
    if not locks:
        return ""
    lines = ["", "GESPERRTE OBJEKTE (Designer hat gesperrt — NICHT verändern):"]
    for lk in locks:
        label = lk.object_id
        if lk.object_name:
            label += f" ({lk.object_name})"
        if lk.note:
            label += f" — {lk.note}"
        lines.append(f"- {label}")
    lines.append(
        "Modifying tools wie move/scale/rotate/delete/boolean dürfen "
        "diese GUIDs nicht als Ziel haben. Wenn der Designer das doch "
        "verlangt, weise höflich auf die Sperre hin statt sie zu "
        "umgehen."
    )
    return "\n".join(lines)
