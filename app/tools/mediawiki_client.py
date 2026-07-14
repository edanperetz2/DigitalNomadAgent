"""Small shared client for MediaWiki APIs used by evidence tools."""

from __future__ import annotations

from typing import Any

from app.tools.http_client import JsonHttpClient

WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"


class MediaWikiClient:
    def __init__(self, api_url: str, http: JsonHttpClient | None = None):
        self._api_url = api_url
        self._http = http or JsonHttpClient()

    async def request(self, **params: Any) -> dict[str, Any]:
        request_params = {"format": "json", "formatversion": 2, **params}
        payload = await self._http.get_json(self._api_url, params=request_params)
        if not isinstance(payload, dict):
            raise ValueError("MediaWiki returned a non-object JSON response")
        return payload
