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
- 当前轮次查询缓存写入 AstrBot `data/temp`，订阅和快捷指令写入 `data/plugin_data`。
- 普通查询优先读取缓存，缓存缺失时自动请求接口并写入缓存。
- 手动 `刷新` / `强制刷新` 会强制请求最新商城状态并覆盖缓存。
- 定时刷新沿用原版时间点，默认每日 `08:01 / 12:01 / 16:01 / 20:01` 附近强制刷新缓存。
- 订阅支持原版指定商品模式，也支持 `全部`、`*`、`.*` 通配全部商品。
- 支持按群聊、私聊或控制台配置快捷查询指令，快捷词等效普通 `/洛手远行商人`。

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
| `merchant_shortcut_mappings` | `[]` | 控制台快捷查询指令映射，格式为 `通道=快捷词` |

`merchant_shortcut_mappings` 示例：

```text
*=远商
group:123456789=商人
private:987654321=远行
aiocqhttp:GroupMessage:123456789=洛手商人
```

通道支持：

- `*`：所有通道的全局快捷词。
- `group:群号`：指定群聊。
- `private:用户ID`：指定私聊用户。
- 完整 UMO：可通过 AstrBot `/sid` 查看，例如 `aiocqhttp:GroupMessage:123456789`。

同一个通道可以配置多个快捷词，用逗号分隔，例如 `*=远商,商人`。

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
| `/设置洛手远行商人快捷指令 <快捷词>` | 为当前群或当前私聊设置快捷查询词，命中后等效 `/洛手远行商人` |
| `/查看洛手远行商人快捷指令` | 查看当前群或当前私聊的快捷查询词 |
| `/取消洛手远行商人快捷指令` | 删除当前群或当前私聊的快捷查询词 |

群聊中订阅和取消订阅需要群主、群管理员或 bot 管理员权限；私聊订阅由 `merchant_private_subscription_enabled` 控制。

群聊中设置和取消快捷查询指令同样需要群主、群管理员或 bot 管理员权限。快捷词可作为查询命令头使用，例如设置为 `远商` 后，直接发送 `远商` 或 `/远商` 会触发普通查询，发送 `远商 刷新` 或 `/远商 强制刷新` 会强制刷新缓存。

聊天内设置和控制台设置会同时生效；控制台中的 `*` 全局快捷词会对所有通道生效。

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

如果普通查询时当前轮次还没有缓存，插件会请求接口并写入缓存；这类普通请求会携带 `refresh=false`。手动刷新和定时自动刷新会携带 `refresh=true`，用于强制刷新上游商城状态。之后同轮次普通查询会直接使用缓存。手动刷新请求失败时会直接返回查询失败，不会回退使用旧缓存。

运行时文件会按用途拆分保存：

```text
AstrBot/data/temp/astrbot_plugin_rocom_shop_subscribe/
├── rocom_shop_cache.json          # 当前轮次商城 raw_data 缓存
└── render_cache/                  # 查询图片和临时渲染 HTML

AstrBot/data/plugin_data/astrbot_plugin_rocom_shop_subscribe/
├── rocom_merchant_subscriptions.json
└── rocom_merchant_shortcuts.json
```

如果 AstrBot 版本较旧，插件无法获取标准 `data` 根目录时，会回退到 `StarTools.get_data_dir()`；此时订阅和快捷指令仍保存在旧版插件数据目录，商城缓存会保存在该目录下的 `temp/` 子目录。

商城缓存会保存上游返回的完整 `raw_data`，同时保存由官方解析链路得到的 `activity`、`products` 和 `history_groups` 快照。图片生成和订阅匹配时，仍会从缓存中的 `raw_data` 重新走官方解析链路，而不是使用额外拼装的商品列表。商品主列表只来自 `merchantActivities`，`random_goods` 只按商品名补充价格和限购信息；如果 `merchantActivities` 为空，行为与官方一致，当前商品列表为空。旧版本已写入的解析后缓存仍会兼容读取。

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
/设置洛手远行商人快捷指令 远商
/取消洛手远行商人快捷指令
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
│   ├── merchant_shortcut.py   # 快捷查询指令持久化
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
