# EndStone ARC Inventory / 弧光背包管理器

[![版本](https://img.shields.io/badge/版本-0.2.0-blue.svg)](https://github.com/yichen-ender/EndstoneMC-ARC-Inventory-Manager)
[![EndStone](https://img.shields.io/badge/EndStone-0.10+-green.svg)](https://github.com/EndstoneMC/endstone)

弧光系列共享背包工具插件。统一处理玩家背包的读取、匹配、扣除与发放（含附魔、Lore、Bedrock NBT），供按钮商店、成就等插件复用，避免各插件各写一套易踩坑的逻辑。并内置**保险箱系统**（个人保险箱 / 经验保管箱）。

> 本仓库为 ARC Inventory Manager 的分支，在原有背包 API 基础上集成了保险箱功能。

## 命名约定

| 项 | 值 |
|---|---|
| 包名 | `endstone_arc_inventory` |
| Plugin id | `arc_inventory` |
| 显示名 | 弧光背包管理器 |

## 功能特性

**背包 API**
- **列出背包**：槽位、类型 ID、显示名、数量、data、附魔、Lore；复杂物品可带 `nbt_b64`
- **匹配检查**：`has_item` — 类型 / 数量 / data；有 `nbt_b64` 时按完整 NBT 比对，否则比附魔与 Lore
- **扣除物品**：`remove_item` — 数量不足则失败且不改动背包
- **发放物品**：`give_item` / `give_item_count` — 支持附魔、Lore、NBT 还原；背包满时返回实际入包数量
- **附魔读取**：不直接遍历 `ItemMeta.enchants`（避免 unhashable），改用 `get_enchant_level` 按已知附魔 ID 查询

**保险箱**
- 小型（2 格）/ 普通（4 格）/ 大型（6 格），最多 6 个
- 精确存取物品（含附魔、自定义显示名、药水 data 值），不误扣同类型物品
- 药水/药箭/不详之瓶按 data 正确取出，分块放入空槽，失败回退 `/give`
- 经验保管箱：购买后可存取经验（等级）
- 保险箱重命名、清除指定插槽、删除（管理员无退款 / 普通玩家返还 60%）
- 保险箱类型选择：个人 / 公会（公会占位，暂未开放）
- XUID 存储键：玩家改名不丢数据

## 安装

1. 将 `endstone_arc_inventory-*.whl` 放入服务器 `plugins/`
2. 重启服务器
3. 其它插件通过 `server.get_plugin("arc_inventory")` 调用 API

依赖本插件的系列插件（如弧光按钮商店）应同时安装本 wheel。

> 保险箱经济依赖 **ARC Core**（`server.get_plugin("arc_core")` 的 ARC 币）。ARC Core 未装时普通玩家购买提示余额不足；管理员（OP）始终可免费购买。

### 本地构建

```bash
pip install build
python -m build
# dist/endstone_arc_inventory-<version>-py2.py3-none-any.whl
```

## 命令

| 命令 | 说明 |
|---|---|
| `/arcim` | 打开保险箱界面（大小写不限） |
| `/arcimreload` | 热重载保险箱数据（仅管理员，无需重启） |

## 其它插件如何调用

```python
inv = self.server.plugin_manager.get_plugin("arc_inventory")
if inv is None:
    self.logger.error("需要安装弧光背包管理器 (arc_inventory)")
    return

# 列出背包
items = inv.api_get_inventory_items(player)
# items[i] ≈ {
#   "type": "minecraft:diamond_sword",
#   "name": "...",
#   "count": 1,
#   "data": 0,
#   "enchants": {"minecraft:sharpness": 5},
#   "lore": ["..."],
#   "slot_index": 0,
#   # 可选 "nbt_b64": "..."
# }

item_info = {"type": "minecraft:diamond", "count": 3, "data": 0}

if inv.api_has_item(player, item_info):
    inv.api_remove_item(player, item_info)

# 发放：足额与否
ok = inv.api_give_item(player, item_info)

# 或拿实际数量（背包满时可能小于 count）
given = inv.api_give_item_count(player, {"type": "minecraft:apple", "count": 64})

# 高级：直接拿 InventoryManager（与按钮商店旧用法一致）
mgr = inv.api_get_inventory_manager()
```

### `item_info` 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 物品类型 ID，如 `minecraft:diamond` |
| `count` | 是 | 数量 |
| `data` | 否 | 默认 `0` |
| `enchants` | 否 | `{附魔ID: 等级}` |
| `lore` | 否 | 字符串列表 |
| `nbt_b64` | 否 | 完整用户 NBT 的 Base64；有则匹配/发放以此为准 |

## API 一览

| 方法 | 返回 | 说明 |
|------|------|------|
| `api_get_inventory_items(player)` | `list[dict]` | 背包物品列表 |
| `api_has_item(player, item_info)` | `bool` | 是否够量匹配 |
| `api_remove_item(player, item_info)` | `bool` | 扣除 |
| `api_give_item(player, item_info)` | `bool` | 是否足额发放 |
| `api_give_item_count(player, item_info)` | `int` | 实际发放数量 |
| `api_get_inventory_manager()` | `InventoryManager \| None` | 底层管理器 |
| `api_get_safe_manager()` | `SafeManager \| None` | 保险箱数据管理器 |

## 数据

- 保险箱数据：`plugins/arc_inventory/safes.json`（存储格式：`type/amount/enchantments/display_name/container_items/data`）
- 背包 API 本身为纯服务，无独立配置

## 与弧光系列

| 插件 | 关系 |
|------|------|
| 弧光按钮商店 | 交易扣物/发物应使用本插件 |
| 弧光核心 / 成就等 | 需要精确背包操作时可同样依赖本插件 |

## 许可证

见 [LICENSE](LICENSE)。
