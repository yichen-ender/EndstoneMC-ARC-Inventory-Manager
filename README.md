# EndStone ARC Inventory / 弧光背包管理器

[![版本](https://img.shields.io/badge/版本-1.2.2-blue.svg)](https://github.com/yichen-ender/EndstoneMC-ARC-Inventory-Manager)
[![EndStone](https://img.shields.io/badge/EndStone-0.11-green.svg)](https://github.com/EndstoneMC/endstone)

本仓库为 [ARC Inventory Manager](https://github.com/ARC-Minecraft/EndstoneMC-ARC-Inventory-Manager) 的分支，包含**两个独立插件**：

| 插件 | 包名 | Plugin id | 职责 |
|---|---|---|---|
| 背包管理器 | `endstone_arc_inventory` | `arc_inventory` | 纯背包读写 API（供其它插件复用） |
| 保险箱 | `endstone_arc_inventory_manager` | `arc_inventory_manager` | 玩家保险箱 + 公会共享仓库（独立业务插件） |

两者相互独立，**各自构建成一个 wheel**（由 `build_all.py` 从同一份 `src/` 产出）。

> ⚠️ **不能把两个插件塞进同一个 wheel。** Endstone 0.11.3 的 `PythonPluginLoader` 有两条硬性约束：
> 1. `load_plugin()` 是 `for ep in eps: if plugin: return plugin` —— 加载完第一个 entry point 就返回，后面的永远不被尝试；
> 2. `_load_plugin_from_ep()` 强制 `dist_name == "endstone-" + ep.name` —— 包名必须等于 `endstone_<入口点名>`，否则以 `Invalid name` 拒绝。
>
> 因此「一个 wheel 两个插件」在 Endstone 上只能加载出其中一个（且通常是第一个）。

> ⚠️ **v1.2.1 起，公会共享仓库功能需要前置插件 ARC Core（arc_core）。**

---

## ⚠️ 前置依赖（必读）

公会共享仓库需要 **ARC Core**（`arc_core`）作为前置。ARC Core 是 EndStone 基岩版服务器核心插件，提供公会系统与经济系统。

- **ARC Core 仓库**：https://github.com/ARC-Minecraft/EndstoneMC-ARC-Core-Plugin
- **重要**：ARC Core 的 wheel **不包含配置文件**。请把 release assets 里的 `core_setting.yml` 和 `ZH-CN.txt` 复制到服务器的 `plugins/ARCCore/` 目录，否则 ARC Core 启动时会因缺少 `DATABASE_PATH` 报错而无法加载：

```
plugins/
└── ARCCore/
    ├── core_setting.yml   ← 必须复制到这里
    └── ZH-CN.txt          ← 必须复制到这里
```

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

保险箱插件**自包含背包读写逻辑（InventoryManager）**，运行时无需额外安装背包管理器插件；外部插件也可单独使用仓库里的 `arc_inventory` 背包 API 插件。

### 功能特性（个人保险箱）

- 小型（2 格）/ 普通（4 格）/ 大型（6 格），最多 6 个
- 精确存取物品（含附魔、自定义显示名、药水 data 值），不误扣同类型物品
- **完整 NBT 存取：潜影盒 / 收纳袋连同里面的东西一起存，取出后内容物、方块朝向原样保留**
- 药水/药箭/不详之瓶按 data 正确取出，分块放入空槽，失败回退 `/give`
- 保险箱重命名、清除指定插槽、删除（管理员无退款 / 普通玩家返还 60%）
- XUID 存储键：玩家改名不丢数据

> **NBT 实现说明**：`endstone 0.11.3` 的 `endstone.nbt` 只提供标签类，**没有 `load()` / `dump()`**，
> 无法做二进制往返。因此走 `CompoundTag.to_dict()` → 按字段名重建标签树的方式。
> 重建时必须还原成正确的标签类型（`Slot`/`Count`/方块状态位字段 = `ByteTag`、
> `Damage` = `ShortTag`、`facing_direction` = `IntTag`）——全部写成 `IntTag` 的话服务端读回无误，
> 但**客户端渲染不出来，潜影盒取出后是空的**。

### 公会共享仓库（v1.2.1 新增，需前置 arc_core）

- 同公会成员共用一套仓库保险箱（上限 6，小型 / 普通 / 大型），会员通过「公会仓库」进入
- 存取权限：职级默认（可存可取）+ 逐人覆盖（可存可取 / 仅存禁取 / 仅取禁存 / 双向禁止），**每个保险箱可单独设置**
- 贡献点限制：可为每个保险箱设置普通成员存取所需的最低个人公会贡献点（0=无限制，不足则禁存取）
- 管理员**无视贡献点限制**，但仍受存取权限限制；**会长无视一切限制**
- 购买保险箱消耗**公会公共贡献点**（仅会长可购买），删除返还 60% 贡献点
- 仓库管理权限由**会长**授权给管理员：改名 / 删除 / 清除插槽 / 设置贡献点限制（全公会统一生效）
- **防刷物品**：线程锁 + 原子化存取（入仓/出仓与背包扣除/发放整体加锁），防止多人同时存取时刷物品/丢物品
- 修复：跨插件获取 arc_core 使用 `server.plugin_manager.get_plugin("arc_core")`（Endstone 的 `Server` 类没有 `get_plugin` 方法）

### 命令

| 命令 | 说明 |
|---|---|
| `/arcim` | 打开保险箱界面（大小写不限） |
| `/arcimreload` | 热重载保险箱数据（仅管理员，无需重启） |

> 保险箱经济依赖 **ARC Core**（`server.plugin_manager.get_plugin("arc_core")` 的 ARC 币）；公会仓库扣/退公会公共贡献点。

---

## 安装

1. **安装 ARC Core（前置）**：将 `endstone_arc_core-0.8.2-*.whl` 放入服务器 `plugins/`，并把 release assets 里的 `core_setting.yml`、`ZH-CN.txt` 复制到 `plugins/ARCCore/`。
2. **安装插件**：把 `dist/` 里**两个 wheel 都**放进 `plugins/`
   ```
   endstone_arc_inventory_manager-1.2.2-*.whl   ← 保险箱（/arcim）
   endstone_arc_inventory-1.2.2-*.whl           ← 背包 API（可选，供其它插件调用）
   ```
   如果只需要保险箱，只装前者即可。
3. **重启服务器**（建议冷启动，不要用 `/reload`）。
4. 玩家执行 `/arcim` 打开保险箱；公会成员进入「公会仓库」使用共享仓库。

### 自行构建

```bash
python build_all.py              # 构建两个 wheel 并部署
python build_all.py --no-deploy  # 只构建，输出到 dist/
```

> ⚠️ **升级注意**：1.2.2 修复了「一个 wheel 两个插件」的加载问题。
> 如果你的 `plugins/` 里还留着 1.2.1 的 `endstone_arc_inventory_manager-1.2.1-*.whl`，
> **必须先删掉**，否则与新的 1.2.2 同时注册 `arc_inventory_manager` 入口，
> Endstone 会报 `Ambiguous plugin name` 并加载失败。玩家 `safes.json` 向后兼容，无需迁移。

## 数据

- 保险箱数据：`plugins/arc_inventory_manager/safes.json`

## 许可

见 [LICENSE](LICENSE)。
