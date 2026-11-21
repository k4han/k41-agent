from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.modules.admin_auth import get_current_admin
from agent.modules.admin_auth.keyboard_shortcuts import (
    DEFAULT_SHORTCUTS,
    ShortcutMap,
    read_keyboard_shortcuts,
    save_keyboard_shortcuts,
    validate_overrides,
)

router = APIRouter()
SHORTCUTS_PATH = "/dashboard-api/keyboard-shortcuts"


class KeyboardShortcutsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: ShortcutMap
    revision: int = Field(ge=0, le=2_147_483_646, strict=True)

    @field_validator("overrides")
    @classmethod
    def check_bindings(cls, overrides: ShortcutMap) -> ShortcutMap:
        return validate_overrides(overrides)


class KeyboardShortcutsPayload(BaseModel):
    defaults: ShortcutMap
    overrides: ShortcutMap
    revision: int


@router.get(SHORTCUTS_PATH)
async def get_keyboard_shortcuts(
    response: Response, admin: str = Depends(get_current_admin)
) -> KeyboardShortcutsPayload:
    overrides, revision = await read_keyboard_shortcuts(int(admin))
    response.headers["Cache-Control"] = "no-store"
    return KeyboardShortcutsPayload(defaults=DEFAULT_SHORTCUTS, overrides=overrides, revision=revision)


@router.put(SHORTCUTS_PATH)
async def put_keyboard_shortcuts(
    body: KeyboardShortcutsUpdate, response: Response, admin: str = Depends(get_current_admin)
) -> KeyboardShortcutsPayload:
    revision = await save_keyboard_shortcuts(int(admin), body.overrides, body.revision)
    if revision is None:
        raise HTTPException(409, "Keyboard shortcuts changed in another tab or device. Try your edit again.")
    response.headers["Cache-Control"] = "no-store"
    return KeyboardShortcutsPayload(defaults=DEFAULT_SHORTCUTS, overrides=body.overrides, revision=revision)
