"""Tencent AGS (Agent Sandbox) Runtime for SWE-ReX.

This runtime connects to Tencent Cloud AGS SWE sandbox instances.
It extends RemoteRuntime with AGS-specific authentication (X-Access-Token header)
and automatic token refresh support.

Requirements:
    pip install aiohttp
"""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
import uuid
from typing import Any, Awaitable, Callable

import aiohttp
from pydantic import BaseModel
from typing_extensions import Self

from swerex.exceptions import EnvironmentExpiredError
from swerex.runtime.abstract import IsAliveResponse, _ExceptionTransfer
from swerex.runtime.remote import RemoteRuntime
from swerex.utils.log import get_logger

__all__ = ["AGSRuntime"]

# Type alias for the token refresher callback.
TokenRefresher = Callable[[], Awaitable[str]]


class AGSRuntime(RemoteRuntime):
    """Runtime for Tencent AGS (Agent Sandbox) SWE sandbox.

    Extends RemoteRuntime with:
    - X-Access-Token header for AGS gateway authentication
    - Automatic token refresh via a callback
    - SSL verification skip support for internal endpoints
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        token_refresher: TokenRefresher | None = None,
        **kwargs: Any,
    ):
        """Initialize AGS Runtime.

        Args:
            logger: Logger instance.
            token_refresher: Async callback that returns a fresh AGS token.
                Called automatically before each request when the token may be expired.
            **kwargs: Keyword arguments (see ``AGSRuntimeConfig`` for details).
        """
        from swerex.runtime.config import AGSRuntimeConfig

        self._config = AGSRuntimeConfig(**kwargs)
        self._token_refresher = token_refresher
        self.logger = logger or get_logger("rex-runtime")
        if not self._config.host.startswith("http"):
            self.logger.warning("Host %s does not start with http, adding https://", self._config.host)
            self._config.host = f"https://{self._config.host}"

    @classmethod
    def from_config(cls, config: Any) -> Self:
        return cls(**config.model_dump())

    @property
    def _headers(self) -> dict[str, str]:
        """Build request headers with AGS token authentication."""
        headers: dict[str, str] = {}
        if self._config.ags_token:
            headers["X-Access-Token"] = self._config.ags_token
        if self._config.auth_token:
            headers["X-API-Key"] = self._config.auth_token
        return headers

    @property
    def _ssl_param(self) -> ssl.SSLContext | None:
        """Get SSL context.

        Returns a permissive SSL context when verification is disabled,
        otherwise ``None`` for default behavior.
        """
        if self._config.skip_ssl_verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    async def _ensure_valid_token(self) -> None:
        """Refresh the AGS token via the callback if one is configured."""
        if self._token_refresher is not None:
            new_token = await self._token_refresher()
            self._config.ags_token = new_token

    def _classify_request_exception(self, exception: Exception, request_url: str) -> Exception:
        """Map AGS gateway errors to more actionable environment exceptions."""
        if isinstance(exception, EnvironmentExpiredError):
            return exception
        if isinstance(exception, aiohttp.ClientResponseError) and exception.status == 404:
            return EnvironmentExpiredError(
                f"AGS sandbox runtime endpoint disappeared: {request_url}. "
                "The sandbox instance likely expired or was stopped."
            )
        return exception

    # ------------------------------------------------------------------
    # Override is_alive to add token refresh + SSL skip
    # ------------------------------------------------------------------

    async def is_alive(self, *, timeout: float | None = None) -> IsAliveResponse:
        """Check if the runtime is alive, refreshing the token first."""
        await self._ensure_valid_token()
        url = f"{self._api_url}/is_alive"
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
                timeout_value = self._get_timeout(timeout)
                async with session.get(
                    url,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_value),
                    ssl=self._ssl_param,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return IsAliveResponse(**data)
                    elif response.status == 511:
                        data = await response.json()
                        exc_transfer = _ExceptionTransfer(**data["swerexception"])
                        self._handle_transfer_exception(exc_transfer)

                    try:
                        body = await response.text()
                    except Exception:
                        body = "<could not read response body>"
                    msg = f"GET {url} returned status {response.status}. Body (first 500 chars): {body[:500]}"
                    self.logger.debug(msg)
                    return IsAliveResponse(is_alive=False, message=msg)
        except aiohttp.ClientError as e:
            msg = f"Connection error to {url}: {type(e).__name__}: {e}"
            self.logger.debug(msg)
            return IsAliveResponse(is_alive=False, message=msg)
        except Exception as e:
            msg = f"Unexpected error connecting to {url}: {type(e).__name__}: {e}"
            self.logger.warning(msg)
            return IsAliveResponse(is_alive=False, message=msg)

    async def _handle_response_errors(self, response: aiohttp.ClientResponse) -> None:
        """Raise exceptions found in the request response."""
        if response.status == 511:
            data = await response.json()
            exc_transfer = _ExceptionTransfer(**data["swerexception"])
            self._handle_transfer_exception(exc_transfer)
        if response.status == 404:
            try:
                data = await response.json()
            except Exception:
                data = {}
            message = data.get("message") or data.get("error") or "The requested resource does not exist"
            raise EnvironmentExpiredError(
                f"AGS sandbox runtime endpoint returned 404: {message}. "
                "The sandbox instance likely expired or was stopped."
            )
        if response.status >= 400:
            data = await response.json()
            self.logger.critical("Received error response: %s", data)
            response.raise_for_status()

    # ------------------------------------------------------------------
    # Override _request to add token refresh + SSL skip
    # ------------------------------------------------------------------

    async def _request(self, endpoint: str, payload: BaseModel | None, output_class: Any, num_retries: int = 3):
        """Make a request with automatic token refresh and SSL skip support."""
        await self._ensure_valid_token()

        request_url = f"{self._api_url}/{endpoint}"
        request_id = str(uuid.uuid4())
        headers = self._headers.copy()
        headers["X-Request-ID"] = request_id

        retry_count = 0
        last_exception: Exception | None = None
        retry_delay = 0.5
        backoff_max = 10

        while retry_count <= num_retries:
            try:
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
                    async with session.post(
                        request_url,
                        json=payload.model_dump() if payload else None,
                        headers=headers,
                        ssl=self._ssl_param,
                    ) as resp:
                        await self._handle_response_errors(resp)
                        return output_class(**await resp.json())
            except Exception as e:
                last_exception = e
                retry_count += 1
                if retry_count <= num_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    retry_delay += random.uniform(0, 0.5)
                    retry_delay = min(retry_delay, backoff_max)
                    continue
                classified_exception = self._classify_request_exception(e, request_url)
                self.logger.error(
                    "Error making request %s after %d retries: %s", request_id, num_retries, classified_exception
                )
                raise classified_exception
        raise self._classify_request_exception(last_exception, request_url)  # type: ignore[arg-type]
