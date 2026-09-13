# 更新日志

## v1.2.4

### 🔧 修复：附魔书等级变 0、烟花火箭飞行时间变 0

**根因是"按字段名猜标签类型"这个做法本身不成立。**

重建 NBT 时的类型判断原本依赖两张硬编码的字段名表（`_NBT_BYTE_FIELDS` /
`_NBT_SHORT_FIELDS`）。**表里没收录的字段一律降级成 `IntTag`**：

| 字段 | 出现在 | 基岩版要求 | 原来重建为 | 表现 |
|---|---|---|---|---|
| `lvl` / `id` | 附魔书的 `ench` 列表 | `ShortTag` | `IntTag` | **附魔等级显示 0** |
| `Flight` | 烟花火箭 `Fireworks` | `ByteTag` | `IntTag` | **飞行时间显示 0** |

客户端按错误类型读取，拿到的是默认值。这类问题只能靠"漏一个字段就坏一种物品"的方式
一个个撞出来，不可持续。

**现在改为编码时把真实标签类型记录下来**，重建时不需要任何猜测：

```json
{"ench": [{"id": {"@s": 9}, "lvl": {"@s": 5}}],
 "Fireworks": {"Flight": {"@b": 3}}}
```

六种数值类型各有标记：`@b` Byte / `@s` Short / `@i` Int / `@l` Long / `@f` Float / `@d` Double。
字符串、列表、复合标签保持自然形态。

因为类型是从**真实标签**读出来的，这不再是"猜对表"的问题 —— 任何物品的任何字段都能正确往返，
不需要事先知道它是什么类型。

**旧格式（无标记的裸整数）继续可读**，已有数据无需迁移。

### ✅ 测试

`tests/test_nbt.py` 增至 43 项，新增：

- 附魔书 `ench[].id` / `lvl` 必须还原为 `ShortTag`（回归用例）
- 烟花 `Fireworks.Flight` 必须还原为 `ByteTag`（回归用例）
- 嵌套的 `Explosions[].Type` / `Colors` 类型正确
- 九种标签类型逐一断言
- 旧格式向后兼容

---

## v1.2.3

### 🔧 修复：部分物品的 NBT 会被静默丢弃

`CompoundTag.to_dict()` 是**有损**的，会让某些物品的 NBT 在序列化时被悄悄丢掉 ——
潜影盒存进保险箱，盒子还在，**里面的东西没了**，而且没有任何报错。

实测各标签类型的 `to_dict()` 结果：

| 标签类型 | `to_dict()` 结果 | 后果 |
|---|---|---|
| `ByteArrayTag` | `bytes` | **json 序列化失败** → 被 `except` 吞掉 → NBT 整份丢失 |
| `IntArrayTag` | `list` | 重建时变成 `ListTag` ✗ |
| `FloatTag` | `float` | 重建时变成 `DoubleTag` ✗ |
| `LongTag` | `int` | 重建时变成 `IntTag` ✗（大数值还可能丢精度） |

后三种属于**静默类型降级** —— 服务端读回看不出问题，但客户端可能渲染不出来，
正是这个插件一直在处理的那类问题。

**改为直接遍历标签树**，只对上面这四种类型加单键标记（`@B` / `@I` / `@f` / `@l`），
其余字段保持裸值，JSON 依然可读：

```json
{"version": 18168865, "raw": {"@B": "AQL/"}, "arr": {"@I": [10, 20, 30]}}
```

标记键以 `@` 开头，基岩版 NBT 字段名不会这么起，不会冲突。
**旧格式（无标记）继续能正确读取，已有数据无需迁移。**

### 🔧 修复：NBT 编码失败不再静默

`_serialize_item_nbt` 编码失败时会打一条 `error` 日志并带上物品类型，
不再悄无声息地返回 `None` —— 那正是上面这个问题难以发现的原因。

### 🔧 保险箱：存盘失败不再污染内存状态

原来 `deposit_item` 是「先进内存、再存盘」：

```python
items[slot_i] = entry      # 先进内存
self._save()               # 再存盘 → 抛异常
```

一旦某个物品存不下去，坏条目就留在内存里，**之后每一次 `_save()` 都会失败**
——包括取出的。表现是保险箱在重启前一直写不进去。

现在存盘失败会**回滚内存改动**并提示「存入失败（数据无法保存）」，
物品不会被扣，后续操作也不受影响。

### 🔧 保险箱：改为原子写盘

`_save()` 先序列化再写 `.tmp` 后 `os.replace()`。序列化失败时还没碰到磁盘文件，
中途失败也不会留下损坏的 `safes.json`。

### ✅ 测试

- NBT 编解码 13 项：`ByteArrayTag` 字节完整、四种类型均不再降级、嵌套位字段类型正确、
  编码确定性、**旧格式向后兼容**
- 保险箱回滚：故意塞入不可序列化的值后，坏条目不残留、后续存入正常、磁盘内容正确

---

## v1.2.2

### 🎉 新功能：完整的 NBT 物品存取

保险箱现在可以存取**潜影盒 / 收纳袋连同里面的东西**，以及任何带自定义 NBT 的物品。

- **潜影盒、收纳袋**：内容物原样保留，取出后打开还是那些东西
- **方块朝向**：盒子里的熔炉、活塞、箱子等朝向正确（`facing_direction` 等方块状态一并还原）
- **铁砧命名的物品**：名称存进 NBT，取出后名字不变
- **内容不同的容器不互相合并**：两个装着不同东西的潜影盒会各占独立槽位，不会混在一起
- 存取菜单中容器类物品会显示 `[含N件]`，一眼看出里面有多少东西

### 🔧 修复：一个 wheel 装两个插件导致保险箱插件加载不出来

**这是本次最重要的修复，如果你升级后 `/arcim` 命令消失，就是这个问题。**

仓库此前把 `arc_inventory`（背包 API）和 `arc_inventory_manager`（保险箱）打包进同一个
wheel，但 **Endstone 0.11.3 的 `PythonPluginLoader` 不支持一个 wheel 注册多个插件**：

```python
# endstone/plugin/plugin_loader.py
def load_plugin(self, file: str) -> Plugin | None:
    eps = distribution(dist_name).entry_points.select(group="endstone")
    for ep in eps:
        plugin = self._load_plugin_from_ep(ep)
        if plugin:
            return plugin          # ← 加载完第一个就返回，后面的永远不被尝试
```

```python
def _load_plugin_from_ep(self, ep: EntryPoint) -> Plugin | None:
    dist_name = "endstone-" + ep.name.replace("_", "-")
    if ep.dist.name.replace("_", "-") != dist_name:
        self.server.logger.error(... "Invalid name")
        return None                # ← 包名必须等于 endstone_<入口点名>
```

两个约束叠加的结果：名为 `endstone_arc_inventory` 的 wheel 只能加载出 `arc_inventory`，
保险箱插件（入口名 `arc_inventory_manager`）**既轮不到、命名也不符**，永远加载不了。

**现在改为构建两个独立 wheel**（`build_all.py` 从同一份 `src/` 产出）：

| 产物 | 入口 | 说明 |
|---|---|---|
| `endstone_arc_inventory_manager-1.2.2-*.whl` | `arc_inventory_manager` | 保险箱，提供 `/arcim` |
| `endstone_arc_inventory-1.2.2-*.whl` | `arc_inventory` | 背包读写 API（可选装） |

### 🔧 修复：NBT 序列化从未生效

`endstone 0.11.3` 的 `endstone.nbt` **只提供标签类，没有 `load()` / `dump()`**，
因此原有的二进制序列化代码：

```python
raw = nbt_compound.dump(byte_order="little")   # AttributeError，被 except 吞掉
```

```python
from endstone.nbt import load                   # ImportError，被 except 吞掉
```

两条路径都静默失败，`nbt_b64` 永远是 `None` —— 这才是当初要写
`if "shulker_box" in t: continue` 跳过潜影盒的真正原因。

现在改为 `CompoundTag.to_dict()` → 按字段名重建标签树。**重建时必须还原成正确的标签类型**：

| 字段 | 所需类型 |
|---|---|
| `Slot` / `Count` / `WasPickedUp` | `ByteTag` |
| 方块状态位字段（`open_bit` / `triggered_bit` / `waterlogged` …） | `ByteTag` |
| `Damage` / `Health` / `Age` | `ShortTag` |
| `facing_direction` / `version` / `direction` | `IntTag` |
| `Name` | `StringTag` |

> ⚠️ 如果全部写成 `IntTag`，**服务端读回完全正常，但客户端渲染不出来 —— 潜影盒取出来是空的**。
> 这一点已由 `tests/test_nbt.py` 逐项断言覆盖。

### 🔧 修复：带 NBT 的物品发放时会被拆成非法堆叠

`give_item_count()` 原本固定 `min(remaining, 64)` 分块。潜影盒、收纳袋的堆叠上限是 **1**，
按 64 构造 `ItemStack` 会产生非法堆叠数量导致发放失败。现在带 `nbt_b64` 时按 **1 个一组**发放。

### ✅ 新增测试

```bash
python tests/test_safe.py    # 35 项：存取逻辑、NBT 槽位隔离、旧数据兼容、并发守恒、权限评估
python tests/test_nbt.py     # 23 项：NBT 往返一致、标签类型、编码确定性、边界输入
```

其中 NBT 隔离与旧数据兼容是本次新增的用例：

- 内容不同的潜影盒必须各占独立槽位，不合并
- 内容相同的潜影盒正确叠放
- 取出时 `nbt_b64` / `nbt_items` 原样返回
- **1.2.1 时期的旧 `safes.json`（条目里没有 `nbt_b64` 字段）仍可正常叠放与取出**

### 📦 构建方式变更

```bash
python build_all.py              # 构建两个 wheel 并部署
python build_all.py --no-deploy  # 只构建，输出到 dist/
```

构建脚本会校验每个 wheel **恰好只有一个 entry point**，防止再出现「加载不出插件」的问题。

### ⚠️ 升级须知

1. **删除旧 wheel**：`plugins/` 里若还留着 `endstone_arc_inventory_manager-1.2.1-*.whl`，
   必须删掉，否则与 1.2.2 同时注册 `arc_inventory_manager` 入口，Endstone 会报
   `Ambiguous plugin name` 并加载失败。
2. **把 `dist/` 里两个新 wheel 都放进 `plugins/`**（只要保险箱就只放 manager 那个）。
3. **冷启动服务器**，不要用 `/reload` —— `.local` 的清理只在启动时执行。
4. 玩家数据 `safes.json` **向后兼容，无需迁移**。
