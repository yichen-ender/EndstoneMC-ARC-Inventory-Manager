# EndStone ARC Inventory / 弧光背包管理器

[![版本](https://img.shields.io/badge/版本-0.2.0-blue.svg)](https://github.com/yichen-ender/EndstoneMC-ARC-Inventory-Manager)
[![EndStone](https://img.shields.io/badge/EndStone-0.10+-green.svg)](https://github.com/EndstoneMC/endstone)

本仓库为 [ARC Inventory Manager](https://github.com/ARC-Minecraft/EndstoneMC-ARC-Inventory-Manager) 的分支，包含**两个独立插件**：

| 插件 | 包名 | Plugin id | 职责 |
|---|---|---|---|
| 背包管理器 | `endstone_arc_inventory` | `arc_inventory` | 纯背包读写 API（供其它插件复用） |
| 保险箱 | `endstone_arc_inventory_manager` | `arc_inventory_manager` | 玩家保险箱（独立业务插件） |

两者相互独立，各自注册 entry point；构建后一个 wheel 同时包含两个插件。

---

## 一、背包管理器（arc_inventory）

统一处理玩家背包的读取、匹配、扣除与发放（含附魔、Lore、Bedrock NBT），供按钮商店、成就等插件复用，避免各插件各写一套易踩坑的逻辑。

### 功能特性

- **列出背包**：槽位、类型 ID、显示名、数量、data、附魔、Lore；复杂物品可带 `nbt_b64`
- **匹配检查**：`has_item` — 类型 / 数量 / data；有 `nbt_b64` 时按完整 NBT 比对，否则比附魔与 Lore
- **扣除物品**：`remove_item` — 数量不足则失败且不改动背包
- **发放物品**：`give_item` / `give_item_count` — 支持附魔、Lore、NBT 还原；背包满时返回实际入包数量
- **附魔读取**：不直接遍历 `ItemMeta.enchants`（避免 unhashable），改用 `get_enchant_level` 按已知附魔 ID 查询

### 其它插件如何调用

```python
inv = self.server.plugin_manager.get_plugin("arc_inventory")
if inv is None:
    self.logger.error("需要安装弧光背包管理器 (arc_inventory)")
    return

items = inv.api_get_inventory_items(player)
item_info = {"type": "minecraft:diamond", "count": 3, "data": 0}
if inv.api_has_item(player, item_info):
    inv.api_remove_item(player, item_info)
ok = inv.api_give_item(player, item_info)
given = inv.api_give_item_count(player, {"type": "minecraft:apple", "count": 64})
```

### API 一览

| 方法 | 返回 | 说明 |
|---|---|---|
| `api_get_inventory_items(player)` | `list[dict]` | 背包物品列表 |
| `api_has_item(player, item_info)` | `bool` | 是否够量匹配 |
| `api_remove_item(player, item_info)` | `bool` | 扣除 |
| `api_give_item(player, item_info)` | `bool` | 是否足额发放 |
| `api_give_item_count(player, item_info)` | `int` | 实际发放数量 |
| `api_get_inventory_manager()` | `InventoryManager \| None` | 底层管理器 |

---

## 二、保险箱（arc_inventory_manager）

独立保险箱插件，通过 `get_plugin("arc_inventory")` 调用背包管理器 API 存取物品。

### 功能特性

- 小型（2 格）/ 普通（4 格）/ 大型（6 格），最多 6 个
- 精确存取物品（含附魔、自定义显示名、药水 data 值），不误扣同类型物品
- 药水/药箭/不详之瓶按 data 正确取出，分块放入空槽，失败回退 `/give`
- 保险箱重命名、清除指定插槽、删除（管理员无退款 / 普通玩家返还 60%）
- 保险箱类型选择：个人 / 公会（公会占位，暂未开放）
- XUID 存储键：玩家改名不丢数据

### 命令

| 命令 | 说明 |
|---|---|
| `/arcim` | 打开保险箱界面（大小写不限） |
| `/arcimreload` | 热重载保险箱数据（仅管理员，无需重启） |

> 保险箱经济依赖 **ARC Core**（`server.get_plugin("arc_core")` 的 ARC 币）。ARC Core 未装时普通玩家购买提示余额不足；管理员（OP）始终可免费购买。

---

## 安装

1. 构建 wheel 后放入服务器 `plugins/`：
   ```bash
   pip install build
   python -m build
   # dist/endstone_arc_inventory-0.2.0-py2.py3-none-any.whl
   ```
2. 重启服务器
3. 其它插件通过 `server.get_plugin("arc_inventory")` 调用背包 API；玩家用 `/arcim` 打开保险箱

## 数据

- 保险箱数据：`plugins/arc_inventory_manager/safes.json`

## 许可证

见 [LICENSE](LICENSE)。
