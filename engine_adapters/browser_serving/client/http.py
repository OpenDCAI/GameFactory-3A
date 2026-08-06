"""Small standard-library transport for the Browser Serving Gateway."""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .exceptions import (
    BrowserServingConnectionError,
    BrowserServingHTTPError,
    BrowserServingResponseError,
)


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        ...


class HTTPTransport:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7870",
        *,
        timeout: float = 30.0,
        headers: Mapping[str, str] | None = None,
        opener=None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        if not self.base_url:
            raise ValueError("base_url is required")
        self.timeout = float(timeout)
        self.headers = {
            "Accept": "application/json",
            **dict(headers or {}),
        }
        self._opener = opener or urlopen

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        data = None
        headers = dict(self.headers)
        if json_body is not None:
            data = json.dumps(
                json_body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self._send(method, path, query, data, headers)

    def upload(
        self,
        path: str,
        file_path: str | os.PathLike[str],
        *,
        fields: Mapping[str, Any] | None = None,
        filename: str = "",
        media_type: str = "",
    ) -> Any:
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Upload file was not found: {source}")
        boundary = f"----A3GameBrowser{uuid4().hex}"
        name = filename or source.name
        content_type = (
            media_type
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        chunks: list[bytes] = []
        for key, value in (fields or {}).items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    (
                        "Content-Disposition: form-data; "
                        f'name="{key}"\r\n\r\n'
                    ).encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    "Content-Disposition: form-data; "
                    f'name="file"; filename="{name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                source.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        headers = {
            **self.headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        return self._send(
            "POST",
            path,
            None,
            b"".join(chunks),
            headers,
        )

    def _send(
        self,
        method: str,
        path: str,
        query: Mapping[str, Any] | None,
        data: bytes | None,
        headers: Mapping[str, str],
    ) -> Any:
        normalized_method = str(method or "GET").strip().upper()
        url = self._url(path, query)
        request = Request(
            url,
            data=data,
            headers=dict(headers),
            method=normalized_method,
        )
        try:
            with self._opener(
                request,
                timeout=self.timeout,
            ) as response:
                body = response.read()
        except HTTPError as exc:
            raw_body = exc.read()
            payload = self._decode_optional_json(raw_body)
            detail = self._error_detail(payload, raw_body)
            raise BrowserServingHTTPError(
                status_code=exc.code,
                method=normalized_method,
                url=url,
                detail=detail,
                payload=payload,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise BrowserServingConnectionError(
                normalized_method,
                url,
                str(reason),
            ) from exc
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            text = body.decode("utf-8", errors="replace")
            raise BrowserServingResponseError(
                method=normalized_method,
                url=url,
                message=str(exc),
                body=text,
            ) from exc

    def _url(
        self,
        path: str,
        query: Mapping[str, Any] | None,
    ) -> str:
        normalized_path = "/" + str(path or "").lstrip("/")
        url = f"{self.base_url}{normalized_path}"
        encoded = self._encode_query(query)
        return f"{url}?{encoded}" if encoded else url

    @classmethod
    def _encode_query(
        cls,
        query: Mapping[str, Any] | None,
    ) -> str:
        items: list[tuple[str, Any]] = []
        for key, value in (query or {}).items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                items.extend(
                    (str(key), cls._query_value(item))
                    for item in value
                )
            else:
                items.append((str(key), cls._query_value(value)))
        return urlencode(items, doseq=True)

    @staticmethod
    def _query_value(value: Any) -> Any:
        if isinstance(value, bool):
            return "true" if value else "false"
        return value

    @staticmethod
    def _decode_optional_json(body: bytes) -> Any:
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _error_detail(payload: Any, body: bytes) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail:
                return detail
            if detail is not None:
                return json.dumps(detail, ensure_ascii=False)
        text = body.decode("utf-8", errors="replace").strip()
        return text or "HTTP request failed"
