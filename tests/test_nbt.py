# -*- coding: utf-8 -*-
"""NBT 序列化/还原单测。

验证重点：
1. 往返一致性 —— to_dict() -> encode -> decode -> build 后内容完全相同
2. **标签类型正确** —— 全部写成 IntTag 的话客户端渲染不出来（潜影盒取出是空的）
3. 编码确定性 —— 同一份 NBT 每次编码结果必须一致，否则分组与精确匹配会失效

需要 endstone 的标签类（endstone.nbt），不需要启动服务器。
运行： python tests/test_nbt.py
"""
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
sys.path.insert(0, SRC)

from endstone_arc_inventory_manager.inventory import (
    _encode_nbt_b64, _decode_nbt_b64, _build_nbt,
)

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))


# 真实潜影盒结构（装 barrel + dispenser，含方块状态）
SHULKER = {
    "Items": [
        {"Block": {"name": "minecraft:barrel",
                   "states": {"facing_direction": 0, "open_bit": 0},
                   "version": 18168865},
         "Count": 64, "Damage": 0, "Name": "minecraft:barrel", "Slot": 0, "WasPickedUp": 0},
        {"Block": {"name": "minecraft:dispenser",
                   "states": {"facing_direction": 3, "triggered_bit": 0},
                   "version": 18168865},
         "Count": 64, "Damage": 0, "Name": "minecraft:dispenser", "Slot": 1, "WasPickedUp": 0},
    ]
}

# ---- 1. 往返一致 ----
b64 = _encode_nbt_b64(SHULKER)
check("非空 NBT 能编码出结果", bool(b64))
tag = _decode_nbt_b64(b64)
check("能解码回标签树", tag is not None)
check("往返后内容完全一致", tag.to_dict() == SHULKER)

# ---- 2. 标签类型必须正确（这是客户端能否渲染的关键）----
items = tag["Items"]
check("Items 是 ListTag 且长度正确", items.size() == 2)
it0 = items[0]
check("Slot 必须是 ByteTag", type(it0["Slot"]).__name__ == "ByteTag")
check("Count 必须是 ByteTag", type(it0["Count"]).__name__ == "ByteTag")
check("Damage 必须是 ShortTag", type(it0["Damage"]).__name__ == "ShortTag")
check("Name 必须是 StringTag", type(it0["Name"]).__name__ == "StringTag")
check("WasPickedUp 必须是 ByteTag", type(it0["WasPickedUp"]).__name__ == "ByteTag")

blk = it0["Block"]
check("Block 必须是 CompoundTag", type(blk).__name__ == "CompoundTag")
check("Block.version 必须是 IntTag", type(blk["version"]).__name__ == "IntTag")
states = blk["states"]
check("states 必须是 CompoundTag（嵌套下钻）", type(states).__name__ == "CompoundTag")
check("facing_direction 必须是 IntTag", type(states["facing_direction"]).__name__ == "IntTag")
check("方块位字段 open_bit 必须是 ByteTag", type(states["open_bit"]).__name__ == "ByteTag")
check("方块位字段 triggered_bit 必须是 ByteTag",
      type(items[1]["Block"]["states"]["triggered_bit"]).__name__ == "ByteTag")

# ---- 3. 编码确定性（分组与精确匹配的前提）----
check("同一份 NBT 编码结果稳定", _encode_nbt_b64(SHULKER) == _encode_nbt_b64(SHULKER))
# 键序不同的等价 dict 也必须编出同一个结果
reordered = {"Items": [dict(reversed(list(e.items()))) for e in SHULKER["Items"]]}
check("键序不同但内容相同 → 编码一致", _encode_nbt_b64(reordered) == b64)

# ---- 4. 不同内容必须区分开 ----
OTHER = {"Items": [{"Name": "minecraft:stone", "Count": 1, "Slot": 0}]}
check("内容不同 → 编码不同", _encode_nbt_b64(OTHER) != b64)

# ---- 5. 边界输入 ----
check("空 dict 编码为 None", _encode_nbt_b64({}) is None)
check("None 编码为 None", _encode_nbt_b64(None) is None)
check("空串解码为 None", _decode_nbt_b64("") is None)
check("垃圾输入解码为 None（不抛异常）", _decode_nbt_b64("!!!not-base64!!!") is None)

# ---- 6. 类型映射表覆盖常见方块状态 ----
BOOL_LIKE = {"open_bit": 1, "triggered_bit": 0, "powered_bit": 1, "waterlogged": 0,
             "door_hinge_bit": 1, "upside_down_bit": 0}
t = _build_nbt(BOOL_LIKE)
check("常见方块位字段全部还原为 ByteTag",
      all(type(t[k]).__name__ == "ByteTag" for k in BOOL_LIKE))

# ---- 结果输出 ----
fails = [n for n, ok in RESULTS if not ok]
print("PASS: %d / %d" % (len(RESULTS) - len(fails), len(RESULTS)))
for n, ok in RESULTS:
    print(("[OK]   " if ok else "[FAIL] ") + n)
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
