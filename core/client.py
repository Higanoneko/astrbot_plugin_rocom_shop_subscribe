from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from astrbot.api import logger


class RocomMerchantClient:
    """Minimal WeGame API client for RoCom merchant data."""

    def __init__(
        self,
        base_url: str = "https://wegame.shallow.ink",
        wegame_api_key: str = "",
        timeout: float = 15.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.wegame_api_key = wegame_api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._last_error = ""

    def get_last_error(self, default: str = "接口异常") -> str:
        return self._last_error or default

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def get_merchant_info(self, refresh: bool = False) -> Optional[Dict[str, Any]]:
        params = {"refresh": "true" if refresh else "false"}
        return await self._get("/api/v1/games/rocom/merchant/info", params=params)

    async def _get(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            self._last_error = ""
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}{path}",
                headers=self._headers(),
                params=params,
            )
            return self._parse_response(path, response)
        except httpx.TimeoutException:
            self._last_error = "请求超时"
            logger.error(f"[Luoshou Merchant] GET {path} 请求超时")
            return None
        except httpx.RequestError as exc:
            self._last_error = f"请求失败: {exc}"
            logger.error(f"[Luoshou Merchant] GET {path} 请求失败: {exc}")
            return None
        except Exception as exc:
            self._last_error = f"异常: {exc}"
            logger.error(f"[Luoshou Merchant] GET {path} 异常: {exc}")
            return None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _headers(self) -> Dict[str, str]:
        if not self.wegame_api_key:
            return {}
        return {"X-API-Key": self.wegame_api_key}

    def _parse_response(
        self, path: str, response: httpx.Response
    ) -> Optional[Dict[str, Any]]:
        if response.status_code != 200:
            hint = self._response_error_hint(response)
            self._last_error = f"HTTP {response.status_code}: {hint}".strip(": ")
            logger.warning(
                f"[Luoshou Merchant] {path} HTTP 错误: {response.status_code} {hint}"
            )
            return None

        if not response.text or not response.text.strip():
            self._last_error = "响应为空"
            logger.warning(f"[Luoshou Merchant] {path} 响应为空")
            return None

        try:
            payload = response.json()
        except Exception as exc:
            self._last_error = "JSON 解析失败"
            logger.warning(f"[Luoshou Merchant] {path} JSON 解析失败: {exc}")
            return None

        if payload.get("code") != 0:
            self._last_error = str(payload.get("message") or "未知错误")
            logger.warning(f"[Luoshou Merchant] {path} 错误: {self._last_error}")
            return None

        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}

    def _response_error_hint(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
            return str(payload.get("message") or response.text[:300] or "")
        except Exception:
            return response.text[:300] if response.text else ""
