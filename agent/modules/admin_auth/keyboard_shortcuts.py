"""Account-scoped keyboard shortcut defaults and overrides."""

from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent.modules.admin_auth.models import AdminKeyboardShortcuts
from agent.shared.infrastructure.db.session import get_async_session_maker

ShortcutAction = Literal[
    "new-session",
    "back",
    "forward",
    "copy-link",
    "command-palette",
    "show-keyboard-shortcuts",
    "search-sessions",
    "toggle-sidebar",
    "focus-message-input",
]
ShortcutMap = dict[ShortcutAction, list[str]]

DEFAULT_SHORTCUTS: ShortcutMap = {
    "new-session": ["Mod+Alt+KeyO"],
    "back": ["Mod+Shift+ArrowLeft"],
    "forward": ["Mod+Shift+ArrowRight"],
    "copy-link": ["Alt+Shift+KeyL"],
    "command-palette": ["Mod+KeyK", "Mod+Shift+Space"],
    "show-keyboard-shortcuts": ["Shift+Slash"],
    "search-sessions": ["Mod+Alt+KeyA"],
    "toggle-sidebar": ["Mod+Alt+KeyS"],
    "focus-message-input": ["Slash", "Mod+Shift+Period"],
}

MODIFIERS = ("Mod", "Ctrl", "Alt", "Shift", "Meta")
KEY_CODE = re.compile(
    r"Key[A-Z]|Digit[0-9]|F(?:[1-9]|1[0-9]|2[0-4])"
    r"|Numpad(?:[0-9]|Add|Subtract|Multiply|Divide|Decimal|Enter|Equal|Comma)"
    r"|Arrow(?:Left|Right|Up|Down)|Space|Enter|Backspace|Delete|Insert|Home|End|Page(?:Up|Down)"
    r"|Backquote|Minus|Equal|Bracket(?:Left|Right)|Backslash|Semicolon|Quote|Comma|Period|Slash"
    r"|Intl(?:Backslash|Ro|Yen)"
)


def _normalize_binding(action: ShortcutAction, binding: str) -> str:
    if action == "focus-message-input" and binding == "Slash":
        return binding

    *modifiers, code = binding.split("+")
    unique = set(modifiers)
    if (
        not modifiers
        or len(unique) != len(modifiers)
        or not unique.issubset(MODIFIERS)
        or ("Mod" in unique and unique.intersection(("Ctrl", "Meta")))
        or not KEY_CODE.fullmatch(code)
    ):
        raise ValueError(f"Invalid key combination: {binding}")
    return "+".join([*(modifier for modifier in MODIFIERS if modifier in unique), code])


def validate_overrides(overrides: ShortcutMap) -> ShortcutMap:
    normalized = {
        action: [_normalize_binding(action, binding) for binding in bindings]
        for action, bindings in overrides.items()
    }
    effective = DEFAULT_SHORTCUTS | normalized
    for primary in ("Ctrl", "Meta"):
        assigned = {}
        for action, bindings in effective.items():
            for binding in bindings:
                *modifiers, code = binding.split("+")
                chord = (code, frozenset(primary if key == "Mod" else key for key in modifiers))
                if chord in assigned:
                    raise ValueError(f"{binding} is already assigned to {assigned[chord]}.")
                assigned[chord] = action
    return normalized


async def read_keyboard_shortcuts(admin_id: int) -> tuple[ShortcutMap, int]:
    async with get_async_session_maker()() as session:
        row = await session.get(AdminKeyboardShortcuts, admin_id)
        return (row.overrides, row.revision) if row else ({}, 0)


async def save_keyboard_shortcuts(
    admin_id: int, overrides: ShortcutMap, revision: int
) -> int | None:
    async with get_async_session_maker()() as session:
        if revision == 0:
            insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            statement = insert(AdminKeyboardShortcuts).values(
                admin_id=admin_id, overrides=overrides, revision=1
            ).on_conflict_do_nothing(index_elements=[AdminKeyboardShortcuts.admin_id])
        else:
            statement = update(AdminKeyboardShortcuts).where(
                AdminKeyboardShortcuts.admin_id == admin_id,
                AdminKeyboardShortcuts.revision == revision,
            ).values(overrides=overrides, revision=revision + 1)
        new_revision = await session.scalar(statement.returning(AdminKeyboardShortcuts.revision))
        await session.commit()
        return new_revision
