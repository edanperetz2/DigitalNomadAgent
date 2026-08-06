"""Thin entrypoint. Run with: uvicorn main:app --reload

Wraps the FastAPI app so it survives Vercel's routing. `vercel.json` rewrites
every request to `/main.py`, and the Python runtime hands the ASGI app that
*rewritten* path rather than the original one -- so FastAPI matched no route and
the deployment answered `{"detail":"Not Found"}` to everything, including
`/openapi.json`, while importing and running perfectly well (D50).

Reproducible without deploying: `client.get("/main.py")` against the app returns
exactly the 404 body the live URL was serving.

The prefix is stripped here rather than in `app/main.py` because it is a
deployment concern, not an application one, and stripping is a no-op whenever
the path is already correct -- local `uvicorn`, the test client and Docker all
pass straight through.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from app.main import app as _app

# Whichever entrypoint a Vercel rewrite names. Both spellings are covered so
# moving the function later does not silently take the site down again.
_FUNCTION_PREFIXES = ("/main.py", "/api/index.py", "/api/index")


class _StripFunctionPrefix:
    """Restore the caller's path when the platform substitutes the function's."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        # Only HTTP/WebSocket carry a path. Lifespan must pass through untouched
        # or startup never runs and the database connection is never opened.
        if scope.get("type") in {"http", "websocket"}:
            path = scope.get("path", "")
            for prefix in _FUNCTION_PREFIXES:
                if path == prefix or path.startswith(f"{prefix}/"):
                    restored = path[len(prefix) :] or "/"
                    scope = {**scope, "path": restored, "raw_path": restored.encode()}
                    break
        await self._inner(scope, receive, send)


app = _StripFunctionPrefix(_app)

__all__ = ["app"]
