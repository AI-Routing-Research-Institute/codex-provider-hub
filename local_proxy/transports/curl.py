from __future__ import annotations

import asyncio
import codecs
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from curl_cffi.const import CurlOpt


@dataclass(frozen=True)
class CurlRequest:
    method: str
    url: str
    params: Sequence[tuple[str, str]] = ()
    headers: Mapping[str, str] = field(default_factory=dict)
    content: bytes = b""
    disable_internal_idle_timeout: bool = False


class CurlResponse:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.status_code = int(response.status_code)
        self.reason_phrase = str(getattr(response, "reason", ""))
        encoding = str(getattr(response, "encoding", "utf-8") or "utf-8")
        try:
            codecs.lookup(encoding)
        except LookupError:
            encoding = "utf-8"
        self.encoding = encoding
        self.headers = httpx.Headers(
            (key, value)
            for key, value in response.headers.items()
            if key.casefold() not in {"content-encoding", "content-length"}
        )
        self.content = b""
        self.is_stream_consumed = False
        self._closed = False

    async def aiter_raw(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_content():
                if chunk:
                    yield bytes(chunk)
        except RequestException as exc:
            raise httpx.ReadError(str(exc)) from exc
        finally:
            self.is_stream_consumed = True

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        async for chunk in self.aiter_raw():
            yield chunk

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        quit_now = getattr(self._response, "quit_now", None)
        if quit_now is not None:
            quit_now.set()
        stream_task = getattr(self._response, "astream_task", None)
        if stream_task is not None and not stream_task.done():
            stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await stream_task
            return
        await self._response.aclose()


class CurlClient:
    def __init__(
        self,
        *,
        session: Any | None = None,
        connect_timeout_seconds: float = 30.0,
        idle_timeout_seconds: float = 300.0,
    ) -> None:
        self._session = session or AsyncSession(
            max_clients=50,
            allow_redirects=False,
        )
        self._timeout = (connect_timeout_seconds, idle_timeout_seconds)

    def build_request(
        self,
        method: str,
        url: str,
        *,
        params: Sequence[tuple[str, str]] = (),
        headers: Mapping[str, str] | None = None,
        content: bytes = b"",
    ) -> CurlRequest:
        return CurlRequest(
            method=method,
            url=url,
            params=tuple(params),
            headers=dict(headers or {}),
            content=content,
        )

    def configure_upstream_request(
        self,
        request: CurlRequest,
        *,
        response_headers_timeout_seconds: float | None,
        stream_idle_timeout_seconds: float | None,
    ) -> CurlRequest:
        del response_headers_timeout_seconds, stream_idle_timeout_seconds
        # The proxy core applies the two guards independently. curl's combined
        # low-speed timer would otherwise impose a shorter hidden timeout.
        return replace(request, disable_internal_idle_timeout=True)

    async def send(
        self,
        request: CurlRequest,
        *,
        stream: bool = True,
    ) -> CurlResponse:
        headers = dict(request.headers)
        for key in tuple(headers):
            if key.casefold() == "accept-encoding":
                headers.pop(key)
        headers["accept-encoding"] = "identity"
        timeout = self._timeout
        curl_options = None
        if request.disable_internal_idle_timeout:
            timeout = None
            curl_options = {
                CurlOpt.CONNECTTIMEOUT_MS: int(self._timeout[0] * 1000),
                CurlOpt.TIMEOUT_MS: 0,
                CurlOpt.LOW_SPEED_LIMIT: 0,
                CurlOpt.LOW_SPEED_TIME: 0,
            }
        try:
            response = await self._session.request(
                method=request.method,
                url=request.url,
                params=list(request.params),
                headers=headers,
                data=request.content,
                stream=stream,
                timeout=timeout,
                allow_redirects=False,
                accept_encoding="identity",
                curl_options=curl_options,
            )
        except RequestException as exc:
            raise httpx.TransportError(str(exc)) from exc
        return CurlResponse(response)

    async def aclose(self) -> None:
        await self._session.close()
