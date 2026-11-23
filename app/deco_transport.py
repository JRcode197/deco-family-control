"""Keep a browser-initiated SDK session on the selected LAN address."""
from http.client import HTTPException
import json
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tplink_deco_api import TransportError
from tplink_deco_api.auth.transport import HttpTransport


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DirectDecoTransport(HttpTransport):
    """Use the pinned SDK's TLS settings and session cookies with direct HTTPS."""
    def __init__(self):
        super().__init__()
        self._opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=self._ssl_ctx))

    def _post(self, url: str, data: bytes) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        request = Request(url, data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                self._capture_cookie(response)
                payload = json.load(response)
        except HTTPError as exc:
            exc.close()
            raise TransportError("Deco HTTP request failed", status_code=exc.code) from None
        except (HTTPException, OSError):
            raise TransportError("Deco connection failed") from None
        if not isinstance(payload, dict):
            raise ValueError("Invalid Deco response")
        return payload
