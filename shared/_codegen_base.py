"""Shared helper for the code_templates submodules."""
from __future__ import annotations


def inject_params(**params) -> str:
    """Build a leading block of ``_name = value`` assignments."""
    lines: list[str] = []
    for key, value in params.items():
        if isinstance(value, str):
            # Escape newlines/CR too (after the backslash escape, so the
            # inserted backslash isn't doubled) — a multi-line string value
            # would otherwise break out of the single-quoted literal and
            # crash the WHOLE generated code block with a SyntaxError.
            # Byte-identical to before for the common newline-free case.
            escaped = (
                value.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )
            lines.append(f"_{key} = '{escaped}'")
        else:
            lines.append(f"_{key} = {value!r}")
    return "\n".join(lines) + "\n"
