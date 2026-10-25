from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SourceForgeApiError(RuntimeError):
    pass


def sourceforge_json(path: str) -> object:
    request = Request(
        f"https://sourceforge.net/rest/p/{path.lstrip('/')}",
        headers={"Accept": "application/json", "User-Agent": "repo-agent-context"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SourceForgeApiError(f"SourceForge API request failed for {path}: {exc}") from exc
