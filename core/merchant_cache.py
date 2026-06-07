from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from typing import Any, Dict, Optional

from astrbot.api import logger


class MerchantCache:
    """Temporary per-round merchant query cache."""

    def __init__(self, data_dir: str, filename: str = "rocom_shop_cache.json"):
        self.path = os.path.join(data_dir, filename)
        self.lock = asyncio.Lock()
        os.makedirs(data_dir, exist_ok=True)
        self.data: Dict[str, Dict[str, Any]] = self._load()

    async def get(self, round_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            item = self.data.get(round_id)
            return copy.deepcopy(item) if item else None

    async def set(self, round_id: str, payload: Dict[str, Any]):
        async with self.lock:
            self.data[round_id] = copy.deepcopy(
                {
                    **payload,
                    "round_id": round_id,
                    "cached_at": int(time.time()),
                }
            )
            self._prune_locked()
            await self._save()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                loaded = json.load(file)
            return loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            logger.error(f"[Luoshou Merchant] 加载缓存失败: {exc}")
            return {}

    async def _save(self):
        try:
            temp_path = self.path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
        except Exception as exc:
            logger.error(f"[Luoshou Merchant] 保存缓存失败: {exc}")

    def _prune_locked(self):
        if len(self.data) <= 12:
            return
        ordered = sorted(
            self.data.items(),
            key=lambda item: int(item[1].get("cached_at") or 0),
            reverse=True,
        )
        self.data = dict(ordered[:12])
