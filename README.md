<div align="center">

# astrbot_plugin_rocom_shop_subscribe

### 洛手远行商人查询与订阅

基于 WeGame 洛克王国数据接口的 AstrBot 插件，专注于远行商人当前轮次商品查询、按轮次缓存、定时刷新缓存和商品订阅推送。

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-FFc65f?style=for-the-badge&logo=python)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue?style=for-the-badge)](./LICENSE)

</div>

---

## 特性

- 只保留远行商人相关能力，适合和原版 `astrbot_plugin_rocom` 同时安装。
- 使用 `洛手` 命令前缀，避免和原版 `/远行商人`、`/订阅远行商人` 冲突。
- 当前轮次查询缓存会持久化到 AstrBot 插件数据目录。
- 普通查询优先读取缓存，缓存缺失时自动请求接口并写入缓存。
- 手动 `刷新` / `强制刷新` 会强制请求最新商城状态并覆盖缓存。
- 定时刷新沿用原版时间点，默认每日 `08:01 / 12:01 / 16:01 / 20:01` 附近强制刷新缓存。
- 订阅支持原版指定商品模式，也支持 `全部`、`*`、`.*` 通配全部商品。

---

## 安装

在 AstrBot 插件目录中克隆本仓库：

```bash
cd AstrBot/data/plugins
git clone https://github.com/Higanoneko/astrbot_plugin_rocom_shop_subscribe.git
```

安装依赖：

```bash
pip install -r requirements.txt
playwright install chromium
```

需要在插件配置中填写 `wegame_api_key`。API Key 可在 [rocom.shallow.ink](https://rocom.shallow.ink/) 申请。

---

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `wegame_api_key` | `""` | WeGame API Key，建议填写 |
| `api_base_url` | `https://wegame.shallow.ink` | API 服务地址 |
| `render_timeout` | `30000` | 图片渲染超时时间，单位毫秒 |
| `merchant_auto_refresh_enabled` | `true` | 是否启用定时刷新缓存 |
| `merchant_refresh_times` | `["08:01", "12:01", "16:01", "20:01"]` | 每天强制刷新缓存的时间点 |
| `merchant_subscription_enabled` | `true` | 是否在定时刷新后处理订阅推送 |
| `merchant_subscription_items` | `["国王球", "棱镜球", "炫彩精灵蛋"]` | 未填写商品且未使用通配符时的默认订阅商品 |
| `merchant_private_subscription_enabled` | `true` | 是否允许私聊订阅 |

---

## 指令

> 以下指令默认使用 AstrBot 的 `/` 前缀；如果你的 AstrBot 配置了其他前缀，请按实际前缀发送。

| 指令 | 说明 |
| --- | --- |
| `/洛手远行商人` | 查询当前轮次远行商人商品，优先读取缓存；缓存缺失时请求接口并写入缓存 |
| `/洛手远行商人 刷新` | 强制请求最新商城状态并覆盖当前轮次缓存 |
| `/洛手远行商人 强制刷新` | 与 `刷新` 等价 |
| `/订阅洛手远行商人 [1/0] [商品...]` | 订阅指定商品；`1` 表示群聊命中后 `@全体`，`0` 或不填表示普通提醒 |
| `/订阅洛手远行商人 [1/0] 全部` | 订阅全部商品，只要当前轮次存在任意商品就推送 |
| `/订阅洛手远行商人 [1/0] *` | 与 `全部` 等价 |
| `/订阅洛手远行商人 [1/0] .*` | 与 `全部` 等价 |
| `/取消订阅洛手远行商人` | 取消当前群或当前私聊的远行商人订阅 |

群聊中订阅和取消订阅需要群主、群管理员或 bot 管理员权限；私聊订阅由 `merchant_private_subscription_enabled` 控制。

---

## 缓存工作流

插件会按当前中国时区远行商人轮次生成缓存 key，例如 `2026-06-01-1`、`2026-06-01-2`。

典型流程：

```text
08:01 定时任务触发 -> 强制刷新商城状态 -> 覆盖当前轮次缓存 -> 检查订阅推送
09:00 用户发送 /洛手远行商人 -> 命中 08:01 缓存 -> 直接返回缓存内容
12:01 定时任务触发 -> 强制刷新商城状态 -> 覆盖当前轮次缓存 -> 检查订阅推送
13:00 用户发送 /洛手远行商人 强制刷新 -> 请求最新商城状态 -> 覆盖当前轮次缓存
```

如果普通查询时当前轮次还没有缓存，插件会请求接口并写入缓存；之后同轮次普通查询会直接使用缓存。

缓存文件默认保存在 AstrBot 插件数据目录下，文件名为 `rocom_shop_cache.json`。订阅文件名为 `rocom_merchant_subscriptions.json`。

---

## 订阅规则

订阅有两种模式：

- `items`：指定商品模式。只有当前轮次商品名命中订阅列表时才推送。
- `all`：全部商品模式。由 `全部`、`*`、`.*` 触发，只要当前轮次存在任意商品就推送。

同一个订阅在同一轮次最多推送一次，插件会记录 `last_push_round` 防止重复提醒。

示例：

```text
/订阅洛手远行商人 1 国王球 棱镜球
/订阅洛手远行商人 0 全部
/订阅洛手远行商人 *
/取消订阅洛手远行商人
```

---

## 项目结构

```text
astrbot_plugin_rocom_shop_subscribe/
├── main.py                    # 插件入口、命令注册、定时刷新和订阅推送
├── metadata.yaml              # AstrBot 插件元数据
├── _conf_schema.json          # AstrBot WebUI 配置 schema
├── core/
│   ├── client.py              # 远行商人 API 客户端
│   ├── merchant_cache.py      # 当前轮次商城缓存
│   ├── merchant_parser.py     # API 响应解析
│   ├── merchant_round.py      # 中国时区轮次与刷新时间计算
│   ├── merchant_subscription.py # 订阅持久化
│   └── render.py              # HTML 到图片渲染器
├── render/yuanxing-shangren/  # 远行商人渲染模板
├── img/                       # 渲染资源
└── ttf/                       # 渲染字体
```

---

## 来源与许可

本插件参考并复用 [Entropy-Increase-Team/astrbot_plugin_rocom](https://github.com/Entropy-Increase-Team/astrbot_plugin_rocom) 的远行商人查询思路、渲染模板和部分资源。

原项目与本项目均按 AGPL-3.0 许可证发布，详见 [LICENSE](./LICENSE) 和 [NOTICE.md](./NOTICE.md)。

美术素材、游戏数据与相关权益归腾讯科技、WeGame 与《洛克王国》项目组所有。
