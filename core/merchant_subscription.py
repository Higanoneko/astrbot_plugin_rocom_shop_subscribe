from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from typing import Any, Dict, Optional

from astrbot.api import logger


class MerchantSubscriptionStore:
    """Persistent merchant subscriptions keyed by group or private user."""

    def __init__(
        self, data_dir: str, filename: str = "rocom_merchant_subscriptions.json"
    ):
        self.path = os.path.join(data_dir, filename)
        self.lock = asyncio.Lock()
        os.makedirs(data_dir, exist_ok=True)
        self.data: Dict[str, Dict[str, Any]] = self._load()

    async def upsert(self, key: str, subscription: Dict[str, Any]):
        async with self.lock:
            payload = copy.deepcopy(subscription)
            payload["updated_at"] = int(time.time())
            self.data[str(key)] = payload
            await self._save()

    async def delete(self, key: str) -> bool:
        async with self.lock:
            key = str(key)
            if key not in self.data:
                return False
            del self.data[key]
            await self._save()
            return True

    async def all(self) -> Dict[str, Dict[str, Any]]:
        async with self.lock:
            return copy.deepcopy(self.data)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                loaded = json.load(file)
            return loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            logger.error(f"[Luoshou Merchant] 加载订阅失败: {exc}")
            return {}

    async def _save(self):
        try:
            temp_path = self.path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
        except Exception as exc:
            logger.error(f"[Luoshou Merchant] 保存订阅失败: {exc}")
