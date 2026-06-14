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
from .core.merchant_shortcut import MerchantShortcutStore
from .core.merchant_subscription import MerchantSubscriptionStore
from .core.render import Renderer


ALL_SUBSCRIPTION_MARKERS = {"全部", "*", ".*"}
PLUGIN_NAME = "astrbot_plugin_rocom_shop_subscribe"
RESERVED_SHORTCUT_COMMANDS = {
    "洛手远行商人",
    "订阅洛手远行商人",
    "取消订阅洛手远行商人",
    "设置洛手远行商人快捷指令",
    "查看洛手远行商人快捷指令",
    "取消洛手远行商人快捷指令",
}


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
        self.persistent_data_dir, self.temp_cache_dir = self._build_storage_dirs()
        self.cache = MerchantCache(self.temp_cache_dir)
        self.subscriptions = MerchantSubscriptionStore(self.persistent_data_dir)
        self.shortcuts = MerchantShortcutStore(self.persistent_data_dir)
        self.renderer = Renderer(
            res_path=self.res_path,
            render_timeout=int(self.config.get("render_timeout", 30000) or 30000),
            output_dir=os.path.join(self.temp_cache_dir, "render_cache"),
        )
        logger.info(
            "[Luoshou Merchant] 存储目录初始化完成: "
            f"persistent={self.persistent_data_dir}, temp={self.temp_cache_dir}"
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
        self.configured_shortcuts = self._parse_configured_shortcuts(
            self.config.get("merchant_shortcut_mappings", [])
        )
        self._merchant_refresh_task: Optional[asyncio.Task] = None
        self._merchant_retry_delay_seconds = 240
        self._merchant_retry_times = 3
        self._merchant_jitter_seconds = 30

        if self.auto_refresh_enabled:
            self._merchant_refresh_task = asyncio.create_task(self._merchant_refresh_loop())

    def _build_storage_dirs(self) -> Tuple[str, str]:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            data_root = os.path.abspath(str(get_astrbot_data_path()))
            persistent_dir = os.path.join(data_root, "plugin_data", PLUGIN_NAME)
            temp_dir = os.path.join(data_root, "temp", PLUGIN_NAME)
        except Exception as exc:
            legacy_dir = os.path.abspath(str(StarTools.get_data_dir()))
            persistent_dir = legacy_dir
            temp_dir = os.path.join(legacy_dir, "temp")
            logger.warning(
                "[Luoshou Merchant] 无法获取 AstrBot data 根目录，"
                f"已回退到旧版插件数据目录: {exc}"
            )

        os.makedirs(persistent_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)
        return persistent_dir, temp_dir

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
        async for result in self._yield_merchant_query(event, args_text):
            yield result

    @filter.command("设置洛手远行商人快捷指令")
    async def set_merchant_shortcut(self, event: AstrMessageEvent, args: str = ""):
        args_text = self._command_args(event, "设置洛手远行商人快捷指令", args)
        shortcut = self._normalize_shortcut_text(args_text)
        if not shortcut:
            yield event.plain_result("请提供快捷指令，例如：/设置洛手远行商人快捷指令 远商")
            return
        if not self._is_valid_shortcut(shortcut):
            yield event.plain_result("这个快捷指令和已有命令冲突，请换一个更短、更独特的词。")
            return
        if not event.is_private_chat() and not await self._is_group_admin(event):
            yield event.plain_result("仅当前群管理员可以设置远行商人快捷指令。")
            return

        key, target_name = self._shortcut_identity(event)
        await self.shortcuts.upsert(
            key,
            {
                "key": key,
                "shortcut": shortcut,
                "umo": event.unified_msg_origin,
                "updated_by": str(event.get_sender_id()),
            },
        )
        yield event.plain_result(f"已将{target_name}远行商人快捷查询指令设置为：{shortcut}")

    @filter.command("查看洛手远行商人快捷指令")
    async def show_merchant_shortcut(self, event: AstrMessageEvent):
        key, target_name = self._shortcut_identity(event)
        shortcut = await self.shortcuts.get(key)
        configured_shortcuts = self._configured_shortcuts_for_event(event)
        if not shortcut and not configured_shortcuts:
            yield event.plain_result(f"{target_name}还没有设置远行商人快捷查询指令。")
            return
        parts = []
        if shortcut:
            parts.append(f"聊天内快捷：{shortcut.get('shortcut')}")
        if configured_shortcuts:
            parts.append(f"控制台快捷：{'、'.join(configured_shortcuts)}")
        yield event.plain_result(f"{target_name}当前远行商人快捷查询指令：\n" + "\n".join(parts))

    @filter.command("取消洛手远行商人快捷指令")
    async def delete_merchant_shortcut(self, event: AstrMessageEvent):
        if not event.is_private_chat() and not await self._is_group_admin(event):
            yield event.plain_result("仅当前群管理员可以取消远行商人快捷指令。")
            return

        key, target_name = self._shortcut_identity(event)
        deleted = await self.shortcuts.delete(key)
        if deleted:
            yield event.plain_result(f"已取消{target_name}远行商人快捷查询指令。")
            return
        yield event.plain_result(f"{target_name}当前没有远行商人快捷查询指令。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def query_merchant_by_shortcut(self, event: AstrMessageEvent):
        message = self._normalize_shortcut_text(getattr(event, "message_str", "") or "")
        if not message:
            return

        key, _ = self._shortcut_identity(event)
        shortcut = await self.shortcuts.get(key)
        shortcuts = self._event_shortcuts(event, shortcut)
        shortcut_args = self._shortcut_args(message, shortcuts)
        if shortcut_args is None:
            return

        handled = False
        async for result in self._yield_merchant_query(event, shortcut_args):
            handled = True
            yield result
        if handled:
            self._stop_event(event)

    async def _yield_merchant_query(self, event: AstrMessageEvent, args_text: str = ""):
        force_refresh = self._is_refresh_request(args_text)
        if force_refresh:
            data, from_cache, error = await self._refresh_current_merchant_data(
                allow_cache_fallback=False
            )
        else:
            data, from_cache, error = await self._get_cached_or_refresh_current_merchant_data()
        if not data:
            yield event.plain_result(
                f"远行商人查询失败：{error or self.client.get_last_error()}"
            )
            return

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
            data = self._merchant_data_from_cache(cached, round_info)
            return data, True, ""
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
                return self._merchant_data_from_cache(cached, round_info), True, self.client.get_last_error()
            return None, False, self.client.get_last_error()
        data = self._merchant_data_from_response(response, round_info)

        cache_entry = self._merchant_cache_entry_from_data(data)
        await self.cache.set(round_info["round_id"], cache_entry)
        products = data.get("products") or []
        product_names = "、".join([str(product.get("name") or "未知商品") for product in products])
        logger.info(
            f"[Luoshou Merchant] 已刷新并写入远行商人缓存："
            f"round_id={round_info['round_id']} products={product_names or '空'}"
        )
        return data, False, ""

    def _merchant_data_from_response(
        self, response: Dict[str, Any], round_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        activity, products, history_groups = merchant_products_from_response(response)
        return {
            "raw_data": copy.deepcopy(response),
            "activity": activity,
            "products": products,
            "history_groups": history_groups,
            "round_info": self._serializable_round_info(round_info),
        }

    def _merchant_cache_entry_from_data(
        self, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "raw_data": copy.deepcopy(data.get("raw_data") or {}),
            "activity": copy.deepcopy(data.get("activity") or {}),
            "products": copy.deepcopy(data.get("products") or []),
            "history_groups": copy.deepcopy(data.get("history_groups") or []),
            "round_info": copy.deepcopy(data.get("round_info") or {}),
            "fetched_at": int(time.time()),
            "source_refresh": True,
        }

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

    def _merchant_data_from_cache(
        self, cached: Dict[str, Any], round_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        raw_data = cached.get("raw_data")
        if isinstance(raw_data, dict):
            data = self._merchant_data_from_response(raw_data, round_info)
            data["fetched_at"] = cached.get("fetched_at") or cached.get("cached_at")
            data["cached_at"] = cached.get("cached_at")
            data["source_refresh"] = cached.get("source_refresh")
            return data

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
        fallback = self._asset_data_uri("img/logo.cVSpb3sL.png")
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

    def _normalize_shortcut_text(self, value: str) -> str:
        text = str(value or "").strip()
        while text.startswith("/"):
            text = text[1:].strip()
        return text

    def _event_shortcuts(
        self, event: AstrMessageEvent, stored_shortcut: Optional[Dict[str, Any]]
    ) -> set[str]:
        shortcuts = set(self._configured_shortcuts_for_event(event))
        if stored_shortcut:
            shortcut = self._normalize_shortcut_text(stored_shortcut.get("shortcut") or "")
            if shortcut:
                shortcuts.add(shortcut)
        return {shortcut for shortcut in shortcuts if self._is_valid_shortcut(shortcut)}

    def _shortcut_args(self, message: str, shortcuts: set[str]) -> Optional[str]:
        for shortcut in sorted(shortcuts, key=len, reverse=True):
            if message == shortcut:
                return ""
            if not message.startswith(shortcut):
                continue
            remainder = message[len(shortcut):]
            if remainder and remainder[0].isspace():
                return remainder.strip()
        return None

    def _configured_shortcuts_for_event(self, event: AstrMessageEvent) -> List[str]:
        if not self.configured_shortcuts:
            return []
        matched = []
        seen = set()
        for channel in [*self._shortcut_channel_candidates(event), "*"]:
            for shortcut in self.configured_shortcuts.get(channel, []):
                if shortcut in seen:
                    continue
                matched.append(shortcut)
                seen.add(shortcut)
        return matched

    def _shortcut_channel_candidates(self, event: AstrMessageEvent) -> List[str]:
        candidates = []
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if umo:
            candidates.append(umo)

        key, _ = self._shortcut_identity(event)
        if key:
            candidates.append(key)

        if event.is_private_chat():
            sender_id = self._safe_event_value(event, "get_sender_id")
            if sender_id:
                candidates.append(f"private:{sender_id}")
        else:
            group_id = self._safe_event_value(event, "get_group_id")
            if group_id:
                candidates.append(f"group:{group_id}")
        return candidates

    def _parse_configured_shortcuts(self, raw_value: Any) -> Dict[str, List[str]]:
        mappings: Dict[str, List[str]] = {}
        if not isinstance(raw_value, list):
            return mappings

        for item in raw_value:
            channel, shortcuts = self._parse_configured_shortcut_item(item)
            if not channel or not shortcuts:
                continue
            bucket = mappings.setdefault(channel, [])
            for shortcut in shortcuts:
                if shortcut not in bucket:
                    bucket.append(shortcut)
        return mappings

    def _parse_configured_shortcut_item(self, item: Any) -> Tuple[str, List[str]]:
        if isinstance(item, dict):
            channel = str(item.get("channel") or item.get("key") or "").strip()
            shortcut_value = item.get("shortcut") or item.get("command") or ""
            return channel, self._split_configured_shortcuts(shortcut_value)

        text = str(item or "").strip()
        if "=" not in text:
            return "", []
        channel, shortcut_value = text.split("=", 1)
        return channel.strip(), self._split_configured_shortcuts(shortcut_value)

    def _split_configured_shortcuts(self, value: Any) -> List[str]:
        if isinstance(value, list):
            raw_parts = value
        else:
            raw_parts = re.split(r"[,，、|；;]+", str(value or ""))
        shortcuts = []
        seen = set()
        for part in raw_parts:
            shortcut = self._normalize_shortcut_text(str(part or ""))
            if not shortcut or shortcut in seen or not self._is_valid_shortcut(shortcut):
                continue
            shortcuts.append(shortcut)
            seen.add(shortcut)
        return shortcuts

    def _is_valid_shortcut(self, shortcut: str) -> bool:
        if not shortcut:
            return False
        if any(char.isspace() for char in shortcut):
            return False
        normalized_reserved = {self._normalize_shortcut_text(item) for item in RESERVED_SHORTCUT_COMMANDS}
        return shortcut not in normalized_reserved

    def _stop_event(self, event: AstrMessageEvent):
        stop_event = getattr(event, "stop_event", None)
        if callable(stop_event):
            stop_event()

    def _safe_event_value(self, event: AstrMessageEvent, method_name: str) -> str:
        method = getattr(event, method_name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "").strip()
        except Exception:
            return ""

    def _subscription_identity(
        self, event: AstrMessageEvent
    ) -> Tuple[str, str, str]:
        if event.is_private_chat():
            return f"private_{event.get_sender_id()}", "个人订阅", "你的个人"
        return str(event.get_group_id()), "群订阅", "本群"

    def _shortcut_identity(self, event: AstrMessageEvent) -> Tuple[str, str]:
        if event.is_private_chat():
            return f"private_{event.get_sender_id()}", "你的个人"
        return str(event.get_group_id()), "本群"

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
