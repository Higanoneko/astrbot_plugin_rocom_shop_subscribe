from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .merchant_round import cn_tz


def merchant_products_from_response(
    response: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    payload = merchant_payload(response)
    activities = payload.get("merchantActivities")
    if activities is None:
        activities = payload.get("merchant_activities")
    activities = activities or []
    activity = activities[0] if activities else {}
    if not isinstance(activity, dict):
        activity = {}

    random_goods = payload.get("random_goods")
    goods_meta_by_name = _goods_meta_by_name(random_goods if isinstance(random_goods, list) else [])
    now_ms = int(datetime.now(cn_tz()).timestamp() * 1000)
    active_products: List[Dict[str, Any]] = []
    all_products: List[Dict[str, Any]] = []

    buckets = [
        ("道具", activity.get("get_props") or []),
        ("额外道具", activity.get("get_extra_props") or []),
        ("精灵", activity.get("get_pets") or []),
    ]
    for category, items in buckets:
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            goods_meta = goods_meta_by_name.get(str(item.get("name", "") or "").strip(), {})
            product = _merchant_product_from_item(item, activity, category, now_ms, goods_meta)
            all_products.append(product)
            if product.get("is_active"):
                active_products.append(product)

    random_goods_products = _products_from_random_goods(
        random_goods if isinstance(random_goods, list) else [],
        all_products,
        activity,
        now_ms,
    )
    if random_goods_products:
        active_products = random_goods_products

    return activity, active_products, _merchant_history_groups(all_products, now_ms)


def merchant_payload(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = response or {}
    if isinstance(payload.get("data"), dict):
        payload = payload.get("data") or {}
    return payload if isinstance(payload, dict) else {}


def _goods_meta_by_name(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("goods_name", "") or item.get("name", "")).strip(): item
        for item in items
        if isinstance(item, dict)
        and str(item.get("goods_name", "") or item.get("name", "")).strip()
    }


def _products_from_random_goods(
    random_goods: List[Dict[str, Any]],
    scheduled_products: List[Dict[str, Any]],
    activity: Dict[str, Any],
    now_ms: int,
) -> List[Dict[str, Any]]:
    if not random_goods:
        return []

    scheduled_by_name = {
        str(product.get("name", "") or "").strip(): product
        for product in scheduled_products
        if str(product.get("name", "") or "").strip()
    }
    products = []
    seen = set()
    for item in random_goods:
        if not isinstance(item, dict):
            continue
        name = str(item.get("goods_name", "") or item.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        scheduled = scheduled_by_name.get(name, {})
        products.append(_product_from_random_goods_item(item, scheduled, activity, now_ms))
    return products


def _product_from_random_goods_item(
    item: Dict[str, Any],
    scheduled: Dict[str, Any],
    activity: Dict[str, Any],
    now_ms: int,
) -> Dict[str, Any]:
    name = str(item.get("goods_name", "") or item.get("name", "")).strip() or "未知商品"
    start_ms = _merchant_timestamp_ms(item.get("start_time"))
    end_ms = _merchant_timestamp_ms(item.get("end_time"))
    if start_ms is None:
        start_ms = _merchant_timestamp_ms(scheduled.get("start_ms"))
    if end_ms is None:
        end_ms = _merchant_timestamp_ms(scheduled.get("end_ms"))
    if start_ms is None:
        start_ms = _merchant_timestamp_ms(activity.get("start_time"))
    if end_ms is None:
        end_ms = _merchant_timestamp_ms(activity.get("end_time"))

    return {
        "name": name,
        "image": (
            item.get("icon_url")
            or item.get("iconUrl")
            or item.get("goods_icon")
            or item.get("goodsIcon")
            or scheduled.get("image")
            or ""
        ),
        "time_label": _format_merchant_window(start_ms, end_ms),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "is_active": True,
        "status_label": "当前轮次",
        "category": scheduled.get("category") or item.get("category") or "商品",
        "price": item.get("price") if item.get("price") not in (None, "") else scheduled.get("price"),
        "buy_limit_num": (
            item.get("buy_limit_num")
            if item.get("buy_limit_num") not in (None, "")
            else scheduled.get("buy_limit_num")
        ),
    }


def _merchant_product_from_item(
    item: Dict[str, Any],
    activity: Dict[str, Any],
    category: str,
    now_ms: int,
    goods_meta: Dict[str, Any],
) -> Dict[str, Any]:
    start_ms = _merchant_timestamp_ms(item.get("start_time"))
    end_ms = _merchant_timestamp_ms(item.get("end_time"))
    if start_ms is None:
        start_ms = _merchant_timestamp_ms(activity.get("start_time"))
    if end_ms is None:
        end_ms = _merchant_timestamp_ms(activity.get("end_time"))

    is_active = True
    if start_ms is not None and end_ms is not None:
        is_active = start_ms <= now_ms < end_ms

    status_label = "当前轮次"
    if start_ms is not None and now_ms < start_ms:
        status_label = "未开始"
    elif end_ms is not None and now_ms >= end_ms:
        status_label = "已结束"

    return {
        "name": item.get("name", "未知商品"),
        "image": item.get("icon_url") or item.get("iconUrl") or "",
        "time_label": _format_merchant_window(start_ms, end_ms),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "is_active": is_active,
        "status_label": status_label,
        "category": category,
        "price": item.get("price") if item.get("price") not in (None, "") else goods_meta.get("price"),
        "buy_limit_num": (
            item.get("buy_limit_num")
            if item.get("buy_limit_num") not in (None, "")
            else goods_meta.get("buy_limit_num")
        ),
    }


def _merchant_history_groups(
    products: List[Dict[str, Any]], now_ms: int
) -> List[Dict[str, Any]]:
    today = datetime.fromtimestamp(now_ms / 1000, tz=cn_tz()).strftime("%Y-%m-%d")
    grouped: Dict[str, Dict[str, Any]] = {}
    for product in products:
        if product.get("is_active"):
            continue
        start_ms = _merchant_timestamp_ms(product.get("start_ms"))
        if start_ms is None:
            continue
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=cn_tz())
        if start_dt.strftime("%Y-%m-%d") != today:
            continue
        key = f"{start_ms}-{product.get('end_ms') or ''}"
        group = grouped.setdefault(
            key,
            {
                "time_label": product.get("time_label") or "--",
                "status_label": product.get("status_label") or "其他时段",
                "sort": start_ms,
                "products": [],
            },
        )
        names = {item.get("name") for item in group["products"]}
        if product.get("name") not in names and len(group["products"]) < 5:
            group["products"].append(product)

    return [
        {key: value for key, value in group.items() if key != "sort"}
        for group in sorted(grouped.values(), key=lambda item: item["sort"])
        if group.get("products")
    ]


def _merchant_timestamp_ms(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_merchant_window(start_ms: Optional[int], end_ms: Optional[int]) -> str:
    if start_ms is None or end_ms is None:
        return "当前轮次"
    start_label = _format_merchant_time(start_ms)
    end_label = _format_merchant_time(end_ms)
    if start_label == "--" or end_label == "--":
        return "当前轮次"
    if start_label[:5] == end_label[:5]:
        return f"{start_label} - {end_label[6:]}"
    return f"{start_label} - {end_label}"


def _format_merchant_time(timestamp_ms: Any) -> str:
    try:
        dt = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=cn_tz())
        return dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "--"
