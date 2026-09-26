"""`CharacterCog._persist_avatar_upload` (`panem_bot.cogs.characters`) --
relays an uploaded `/character avatar` attachment's bytes to `panem_api`'s
avatar-upload endpoint so the resulting URL doesn't expire the way
Discord's own CDN URL does. Mirrors `test_character_approval.py`'s
`FakeBot` pattern: mocked Discord-facing/HTTP calls, no real network.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from panem_bot.cogs.characters import CharacterCog


class FakeSettings:
    def resolved_api_internal_url(self) -> str:
        return "http://api:8000"


class FakeBot:
    def __init__(self) -> None:
        self.settings = FakeSettings()

    @asynccontextmanager
    async def db(self):
        raise AssertionError("not needed for these tests")
        yield  # pragma: no cover


@pytest.fixture
def cog() -> CharacterCog:
    return CharacterCog(FakeBot())  # type: ignore[arg-type]


def _fake_response(status_code: int, json_body: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        status_code, json=json_body, request=httpx.Request("POST", "http://api:8000/x")
    )


class TestPersistAvatarUpload:
    async def test_returns_the_persisted_url_on_success(self, cog: CharacterCog):
        response = _fake_response(200, {"avatar_url": "https://example.com/uploads/avatars/x.png"})
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
            url = await cog._persist_avatar_upload(
                character_id=1,
                discord_id=42,
                data=b"fake-bytes",
                content_type="image/png",
                filename="pic.png",
            )
        assert url == "https://example.com/uploads/avatars/x.png"

    async def test_returns_none_when_the_api_is_unreachable(self, cog: CharacterCog):
        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("refused"))
        ):
            url = await cog._persist_avatar_upload(
                character_id=1,
                discord_id=42,
                data=b"fake-bytes",
                content_type="image/png",
                filename="pic.png",
            )
        assert url is None

    async def test_returns_none_when_the_api_refuses_the_upload(self, cog: CharacterCog):
        response = _fake_response(400, {"detail": "invalid_avatar_image"})
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
            url = await cog._persist_avatar_upload(
                character_id=1,
                discord_id=42,
                data=b"not-an-image",
                content_type="image/png",
                filename="pic.png",
            )
        assert url is None
