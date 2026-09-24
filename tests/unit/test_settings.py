from __future__ import annotations

from panem_shared.settings import Settings


class TestResolvedApiInternalUrl:
    def test_falls_back_to_localhost_with_api_port_when_unset(self):
        settings = Settings(api_internal_url="", api_port=8000)
        assert settings.resolved_api_internal_url() == "http://localhost:8000"

    def test_uses_the_configured_override_when_set(self):
        settings = Settings(api_internal_url="http://api:8000", api_port=9000)
        assert settings.resolved_api_internal_url() == "http://api:8000"
