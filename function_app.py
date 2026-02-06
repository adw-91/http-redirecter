from urllib.parse import urlparse
import logging
import os
import time

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableClient
from azure.identity import DefaultAzureCredential

app = func.FunctionApp()

# Cache: {hostname: (redirect_url, timestamp)}
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))

_table_client: TableClient | None = None

def _get_table_client() -> TableClient:
    global _table_client
    if _table_client is None:
        endpoint = os.environ["AzureWebJobsStorage__tableServiceUri"]
        _table_client = TableClient(
            endpoint=endpoint,
            table_name=os.getenv("REDIRECT_TABLE_NAME", "redirects"),
            credential=DefaultAzureCredential(),
        )
    return _table_client


def _get_redirect_url(hostname: str) -> str | None:
    now = time.time()
    cached = _cache.get(hostname)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        entity = _get_table_client().get_entity(
            partition_key=hostname,
            row_key="default",
        )
        redirect_url = entity.get("RedirectUrl")
    except ResourceNotFoundError:
        logging.warning(f"No redirect entry for host {hostname}")
        redirect_url = None
    except Exception:
        logging.exception(f"Failed to look up redirect for {hostname}")
        return None  # Don't cache transient errors

    # Cache both hits and misses
    _cache[hostname] = (redirect_url, now)
    return redirect_url


@app.route(
    route="{*route}",
    methods=[
        "GET", "POST", "PUT",
        "DELETE", "PATCH", "HEAD"
    ],
    auth_level=func.AuthLevel.ANONYMOUS
)
def redirect_handler(req: func.HttpRequest) -> func.HttpResponse:
    hostname: str = urlparse(req.url).netloc.lower().split(":")[0]

    redirect_url = _get_redirect_url(hostname)
    if not redirect_url:
        return func.HttpResponse(
            "Configuration error: redirect url not found.",
            status_code=500
        )

    # Ensure redirect target is a valid URL
    parsed = urlparse(redirect_url)
    if not parsed.scheme:
        redirect_url = f"https://{redirect_url}"
        parsed = urlparse(redirect_url)
    if not parsed.netloc:
        logging.error(f"Invalid redirect URL for host {hostname}: {redirect_url}")
        return func.HttpResponse(
            "Configuration error: invalid redirect target.",
            status_code=500
        )

    # Preserve path and query string
    path = req.route_params.get('route', '')
    query = req.url.split('?', 1)[1] if '?' in req.url else ''

    # Build new URL, avoiding double slash on root path
    new_url = redirect_url.rstrip('/')
    if path:
        new_url += f"/{path}"
    if query:
        new_url += f"?{query}"

    # Log redirect for troubleshooting
    user_agent = req.headers.get('User-Agent', 'Unknown')
    logging.info(f'Redirecting {req.method} {req.url} -> {new_url} (UA: {user_agent})')

    return func.HttpResponse(
        status_code=307,
        headers={'Location': new_url}
    )
