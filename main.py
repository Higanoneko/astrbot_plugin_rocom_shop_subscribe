from __future__ import annotations

import asyncio
import base64
import copy
import mimetypes
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig

from .core.client import RocomMerchantClient
from .core.merchant_cache import MerchantCache
from .core.merchant_parser import merchant_products_from_response
from .core.merchant_round import cn_tz, current_merchant_round
from .core.merchant_subscription import MerchantSubscriptionStore
from .core.render import Renderer


ALL_SUBSCRIPTION_MARKERS = {"全部", "*", ".*"}


@register(
    "astrbot_plugin_rocom_shop_subscribe",
    "pianc & Codex",
    "洛手远行商人查询与订阅",
    "0.1.0",
    "https://github.com/Entropy-Increase-Team/astrbot_plugin_rocom",
)
class LuoshouMerchantPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.client = RocomMerchantClient(
            base_url=self.config.get("api_base_url", "https://wegame.shallow.ink"),
            wegame_api_key=self.config.get("wegame_api_key", ""),
        )
        self.res_path = os.path.abspath(os.path.dirname(__file__))
        data_dir = str(StarTools.get_data_dir())
        self.cache = MerchantCache(data_dir)
        self.subscriptions = MerchantSubscriptionStore(data_dir)
        self.renderer = Renderer(
            res_path=self.res_path,
            render_timeout=int(self.config.get("render_timeout", 30000) or 30000),
        )

        self.default_subscription_items = self.config.get(
            "merchant_subscription_items", ["国王球", "棱镜球", "炫彩精灵蛋"]
        )
        self.private_subscription_enabled = self.config.get(
            "merchant_private_subscription_enabled", True
        )
        self.auto_refresh_enabled = self.config.get("merchant_auto_refresh_enabled", True)
        self.subscription_enabled = self.config.get("merchant_subscription_enabled", True)
        self.refresh_times = self.config.get(
            "merchant_refresh_times", ["08:01", "12:01", "16:01", "20:01"]
        )
        self._merchant_refresh_task: Optional[asyncio.Task] = None
        self._merchant_retry_delay_seconds = 240
        self._merchant_retry_times = 3
        self._merchant_jitter_seconds = 30

        if self.auto_refresh_enabled:
            self._merchant_refresh_task = asyncio.create_task(self._merchant_refresh_loop())

    async def terminate(self):
        if self._merchant_refresh_task and not self._merchant_refresh_task.done():
            self._merchant_refresh_task.cancel()
            try:
                await self._merchant_refresh_task
            except asyncio.CancelledError:
                pass
        await self.client.close()
        await self.renderer.close()

    @filter.command("洛手远行商人")
    async def query_merchant(self, event: AstrMessageEvent, args: str = ""):
        args_text = self._command_args(event, "洛手远行商人", args)
        force_refresh = self._is_refresh_request(args_text)
        if force_refresh:
            data, from_cache, error = await self._refresh_current_merchant_data(
                allow_cache_fallback=True
            )
        else:
            data, from_cache, error = await self._get_cached_or_refresh_current_merchant_data()
        if not data:
            yield event.plain_result(
                f"远行商人查询失败：{error or self.client.get_last_error()}"
            )
            return

        if from_cache and force_refresh and error:
            yield event.plain_result(
                f"最新商店请求失败，以下为当前轮次缓存：{error or self.client.get_last_error()}"
            )

        img_url = await self._render_merchant_image(data)
        if img_url:
            yield event.image_result(img_url)
            return

        products = data.get("products") or []
        if not products:
            yield event.plain_result("当前远行商人暂无商品。")
            return
        names = "、".join([str(product.get("name") or "未知商品") for product in products])
        round_info = data.get("round_info") or {}
        source = "缓存" if from_cache else "最新"
        yield event.plain_result(
            f"远行商人当前商品：{names}\n"
            f"当前轮次：第{round_info.get('current') or '未开放'}轮\n"
            f"剩余：{round_info.get('countdown') or '--'}\n"
            f"数据来源：{source}"
        )

    @filter.command("订阅洛手远行商人")
    async def subscribe_merchant(self, event: AstrMessageEvent, args: str = ""):
        if event.is_private_chat() and not self.private_subscription_enabled:
            yield event.plain_result("个人私聊订阅功能已被禁用，请联系机器人管理员。")
            return
        if not event.is_private_chat() and not await self._is_group_admin(event):
            yield event.plain_result("仅当前群管理员可以配置远行商人订阅。")
            return

        args_text = self._command_args(event, "订阅洛手远行商人", args)
        mention_all, mode, items = self._parse_subscription_args(args_text)
        key, subscription_type, target_name = self._subscription_identity(event)
        await self.subscriptions.upsert(
            key,
            {
                "key": key,
                "type": subscription_type,
                "umo": event.unified_msg_origin,
                "mention_all": mention_all,
                "mode": mode,
                "items": items,
                "last_push_round": "",
                "last_matched_items": [],
                "updated_by": str(event.get_sender_id()),
            },
        )

        mention_hint = f"命中后{'会' if mention_all else '不会'}@全体" if not event.is_private_chat() else ""
        if mode == "all":
            yield event.plain_result(
                f"已订阅{target_name}洛手远行商人全部商品；{mention_hint}\n"
                "本轮只要出现任意商品就会推送。"
            )
            return

        yield event.plain_result(
            f"已订阅{target_name}洛手远行商人，监听商品：{'、'.join(items)}；{mention_hint}\n"
            "使用 /订阅洛手远行商人 1 全部 可订阅所有商品，全部/*/.* 等价。"
        )

    @filter.command("取消订阅洛手远行商人")
    async def unsubscribe_merchant(self, event: AstrMessageEvent):
        if not event.is_private_chat() and not await self._is_group_admin(event):
            yield event.plain_result("仅当前群管理员可以取消远行商人订阅。")
            return

        key, _, target_name = self._subscription_identity(event)
        deleted = await self.subscriptions.delete(key)
        if deleted:
            yield event.plain_result(f"已取消{target_name}洛手远行商人订阅。")
            return
        yield event.plain_result(f"{target_name}当前没有洛手远行商人订阅。")

    async def _merchant_refresh_loop(self):
        logger.info("[Luoshou Merchant] 远行商人定时刷新任务已启动")
        while True:
            try:
                now = datetime.now(cn_tz())
                next_check = self._next_refresh_time(now)
                jitter = random.uniform(-self._merchant_jitter_seconds, self._merchant_jitter_seconds)
                target_check = next_check + timedelta(seconds=jitter)
                sleep_seconds = max(1, (target_check - now).total_seconds())
                logger.info(
                    "[Luoshou Merchant] 下次定时刷新时间："
                    f"{target_check.strftime('%Y-%m-%d %H:%M:%S CST')}"
                )
                await asyncio.sleep(sleep_seconds)
                await self._run_merchant_refresh_window()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"[Luoshou Merchant] 定时刷新循环异常: {exc}")
                await asyncio.sleep(60)

    async def _run_merchant_refresh_window(self):
        for retry_index in range(self._merchant_retry_times + 1):
            if retry_index > 0:
                delay = max(
                    1,
                    self._merchant_retry_delay_seconds
                    + random.uniform(-self._merchant_jitter_seconds, self._merchant_jitter_seconds),
                )
                logger.warning(
                    f"[Luoshou Merchant] 远行商人返回为空，{delay:.1f} 秒后重试"
                )
                await asyncio.sleep(delay)
            status = await self._refresh_cache_and_push_subscriptions()
            if status != "empty":
                return
        logger.warning("[Luoshou Merchant] 远行商人定时刷新连续为空，已暂停本轮重试")

    async def _refresh_cache_and_push_subscriptions(self) -> str:
        # Scheduled checks always refresh and cache the current shop state.
        # Subscription delivery is an extra step after the cache is updated.
        data, _, error = await self._refresh_current_merchant_data(allow_cache_fallback=False)
        if error or not data:
            return "empty"

        round_info = data.get("round_info") or current_merchant_round()
        if not round_info.get("is_open"):
            return "closed"

        products = data.get("products") or []
        if not products:
            return "empty"

        if not self.subscription_enabled:
            return "refreshed"

        all_subs = await self.subscriptions.all()
        if not all_subs:
            return "no_subscriptions"

        pending_pushes = self._pending_subscription_pushes(all_subs, products, round_info)
        if not pending_pushes:
            return "done"

        img_url = await self._render_merchant_image(data)
        for key, sub, matched in pending_pushes:
            if await self._send_subscription_push(sub, matched, round_info, img_url):
                sub["last_push_round"] = round_info["round_id"]
                sub["last_matched_items"] = matched
                await self.subscriptions.upsert(key, sub)
                await asyncio.sleep(5)
        return "done"

    def _pending_subscription_pushes(
        self,
        all_subs: Dict[str, Dict[str, Any]],
        products: List[Dict[str, Any]],
        round_info: Dict[str, Any],
    ) -> List[Tuple[str, Dict[str, Any], List[str]]]:
        product_names = [str(product.get("name") or "") for product in products]
        product_name_set = set(product_names)
        pending = []
        for key, sub in all_subs.items():
            if sub.get("last_push_round") == round_info.get("round_id"):
                continue
            if sub.get("mode") == "all":
                matched = [name for name in product_names if name]
            else:
                items = sub.get("items") or self.default_subscription_items
                matched = [name for name in items if name in product_name_set]
            if matched:
                pending.append((key, sub, matched))
        return pending

    async def _send_subscription_push(
        self,
        sub: Dict[str, Any],
        matched: List[str],
        round_info: Dict[str, Any],
        img_url: Optional[str],
    ) -> bool:
        mode = sub.get("mode") or "items"
        title = "远行商人本轮出现商品" if mode == "all" else "远行商人本轮命中订阅商品"
        text_chain = MessageChain()
        if sub.get("mention_all"):
            text_chain.at_all()
        text_chain.message(
            f"{title}：{'、'.join(matched)}\n"
            f"轮次：第{round_info.get('current')}轮\n"
            f"剩余：{round_info.get('countdown')}"
        )
        try:
            await self.context.send_message(sub["umo"], text_chain)
        except Exception as exc:
            logger.warning(f"[Luoshou Merchant] 订阅文本推送失败: {exc}")
            return False

        if not img_url:
            return True
        try:
            await self.context.send_message(sub["umo"], MessageChain().file_image(img_url))
        except Exception as exc:
            logger.warning(f"[Luoshou Merchant] 订阅图片推送失败: {exc}")
        return True

    async def _get_cached_or_refresh_current_merchant_data(
        self,
    ) -> Tuple[Optional[Dict[str, Any]], bool, str]:
        round_info = current_merchant_round()
        cached = await self.cache.get(round_info["round_id"])
        if cached:
            return self._with_live_round_info(cached, round_info), True, ""
        return await self._refresh_current_merchant_data(allow_cache_fallback=False)

    async def _refresh_current_merchant_data(
        self,
        allow_cache_fallback: bool,
    ) -> Tuple[Optional[Dict[str, Any]], bool, str]:
        round_info = current_merchant_round()
        cached = await self.cache.get(round_info["round_id"])
        response = await self.client.get_merchant_info(refresh=True)
        if response is None:
            if allow_cache_fallback and cached:
                return self._with_live_round_info(cached, round_info), True, self.client.get_last_error()
            return None, False, self.client.get_last_error()

        activity, products, history_groups = merchant_products_from_response(response)
        data = {
            "activity": activity,
            "products": products,
            "history_groups": history_groups,
            "round_info": self._serializable_round_info(round_info),
            "fetched_at": int(time.time()),
        }
        await self.cache.set(round_info["round_id"], data)
        return data, False, ""

    def _next_refresh_time(self, now: datetime) -> datetime:
        current = now if now.tzinfo else now.replace(tzinfo=cn_tz())
        check_times = self._refresh_check_times(current)
        for check_time in check_times:
            if check_time > current:
                return check_time
        return self._refresh_check_times(current + timedelta(days=1))[0]

    def _refresh_check_times(self, base: datetime) -> List[datetime]:
        current = base if base.tzinfo else base.replace(tzinfo=cn_tz())
        parsed_times = []
        for item in self.refresh_times:
            try:
                hour_text, minute_text = str(item).strip().split(":", 1)
                hour = int(hour_text)
                minute = int(minute_text)
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    parsed_times.append(
                        current.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    )
            except Exception:
                continue
        if not parsed_times:
            parsed_times = [
                current.replace(hour=8, minute=1, second=0, microsecond=0),
                current.replace(hour=12, minute=1, second=0, microsecond=0),
                current.replace(hour=16, minute=1, second=0, microsecond=0),
                current.replace(hour=20, minute=1, second=0, microsecond=0),
            ]
        return sorted(parsed_times)

    def _with_live_round_info(
        self, cached: Dict[str, Any], round_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        data = copy.deepcopy(cached)
        data["round_info"] = self._serializable_round_info(round_info)
        return data

    async def _render_merchant_image(self, data: Dict[str, Any]) -> Optional[str]:
        render_data = {
            "background": self._asset_data_uri("img/bg.C8CUoi7I.jpg"),
            "titleIcon": True,
            "title": (data.get("activity") or {}).get("name", "远行商人"),
            "subtitle": (data.get("activity") or {}).get(
                "start_date", "每日 08:00 / 12:00 / 16:00 / 20:00 刷新"
            ),
            "product_count": len(data.get("products") or []),
            "round_info": data.get("round_info") or current_merchant_round(),
            "products": self._products_with_fallback_images(data.get("products") or []),
            "history_groups": self._history_with_fallback_images(data.get("history_groups") or []),
        }
        return await self.renderer.render_html(
            "render/yuanxing-shangren/index.html",
            render_data,
            {
                "device_scale_factor": 2,
                "viewport_width": 1200,
                "viewport_height": 1000,
            },
        )

    def _products_with_fallback_images(
        self, products: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        fallback = self._asset_data_uri("img/yuanxingshangren.png")
        normalized = []
        for product in products:
            item = dict(product)
            item["image"] = item.get("image") or fallback
            normalized.append(item)
        return normalized

    def _history_with_fallback_images(
        self, history_groups: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        normalized = []
        for group in history_groups:
            item = dict(group)
            item["products"] = self._products_with_fallback_images(group.get("products") or [])
            normalized.append(item)
        return normalized

    def _asset_data_uri(self, relative_path: str) -> str:
        path = os.path.join(self.res_path, relative_path)
        if not os.path.exists(path):
            return ""
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as file:
            encoded = base64.b64encode(file.read()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _parse_subscription_args(self, raw_text: str) -> Tuple[bool, str, List[str]]:
        text = str(raw_text or "").strip()
        mention_all = False
        items_text = text
        tokens = text.split(maxsplit=1)
        if tokens and tokens[0] in {"0", "1"}:
            mention_all = tokens[0] == "1"
            items_text = tokens[1] if len(tokens) > 1 else ""

        items = self._split_subscription_items(items_text)
        if any(item in ALL_SUBSCRIPTION_MARKERS for item in items):
            return mention_all, "all", []
        if items:
            return mention_all, "items", items
        return mention_all, "items", list(self.default_subscription_items)

    def _split_subscription_items(self, raw_text: str) -> List[str]:
        parts = re.split(r"[\s,，、/|；;]+", raw_text.strip())
        items = []
        seen = set()
        for part in parts:
            name = str(part or "").strip()
            if not name or name in seen:
                continue
            items.append(name)
            seen.add(name)
        return items

    def _command_args(self, event: AstrMessageEvent, command: str, args: str = "") -> str:
        full_command = str(getattr(event, "message_str", "") or "")
        if command in full_command:
            return full_command.split(command, 1)[1].strip()
        return str(args or "").strip()

    def _is_refresh_request(self, args_text: str) -> bool:
        tokens = self._split_subscription_items(args_text)
        return any(token in {"刷新", "强制刷新", "refresh", "force"} for token in tokens)

    def _subscription_identity(
        self, event: AstrMessageEvent
    ) -> Tuple[str, str, str]:
        if event.is_private_chat():
            return f"private_{event.get_sender_id()}", "个人订阅", "你的个人"
        return str(event.get_group_id()), "群订阅", "本群"

    def _serializable_round_info(self, round_info: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = dict(round_info)
        cleaned.pop("start_time", None)
        cleaned.pop("end_time", None)
        return cleaned

    async def _is_group_admin(self, event: AstrMessageEvent) -> bool:
        if event.is_private_chat():
            return False
        sender_id = str(event.get_sender_id())
        role = str(getattr(event, "role", "") or "").lower()
        try:
            group = await event.get_group()
            if not group:
                return role in {"admin", "owner"}

            owner_candidates = [
                getattr(group, "group_owner", None),
                getattr(group, "owner_id", None),
                getattr(group, "group_owner_id", None),
            ]
            if any(str(owner) == sender_id for owner in owner_candidates if owner is not None):
                return True

            admins = [str(item) for item in getattr(group, "group_admins", [])]
            if sender_id in admins:
                return True
            return role in {"admin", "owner"}
        except Exception:
            return role in {"admin", "owner"}
