# -*- coding: utf-8 -*-
"""
背包管理类：统一负责玩家背包的读取、匹配、移除与发放。
复用附魔/洛尔等 Endstone API 的转换与比较逻辑，便于维护与扩展。
Endstone ItemMeta.enchants 返回 dict[Enchantment, int]，键不可哈希会报错，
故通过 get_enchant_level(id: str) 逐个查询已知附魔 id 获取等级。
"""
import base64
import json
import traceback
from typing import Any, Dict, List, Optional

# Endstone 已知附魔 id 列表（minecraft:xxx），用于 get_enchant_level 逐个查询，避免访问 .enchants
_ENCHANT_IDS: List[str] = []

# ---------------------------------------------------------------------------
# NBT 序列化 / 还原
#
# endstone 0.11.3 的 endstone.nbt **只提供标签类**，没有 load() / dump()，
# 因此无法用二进制往返，只能走 CompoundTag.to_dict() -> 重建。
# 重建时必须按字段名还原成正确的标签类型：全部写成 IntTag 的话，服务端读回无误，
# 但客户端渲染不出来（潜影盒取出来是空的）。
# 下面的字段表来自基岩版真实 NBT 数据实测。
# ---------------------------------------------------------------------------

# 标签类型为 Byte 的字段（含 Block.states 里的方块状态位字段）
_NBT_BYTE_FIELDS = {
    "Slot", "Count", "WasPickedUp", "inverted", "Findable",
    "KeepPacked", "OnGround", "Fire", "SpawnEgg",
    "open_bit", "triggered_bit", "powered_bit", "toggle_bit",
    "occupied_bit", "in_wall_bit", "button_pressed_bit",
    "top_slot_bit", "conditional_bit", "update_bit",
    "waterlogged", "stripped_bit", "extinguished",
    "drag_down", "paused", "attached_bit", "disarmed_bit",
    "door_hinge_bit", "upper_block_bit", "upside_down_bit",
    "infiniburn_bit", "allow_underwater_bit",
    "brewing_stand_slot_a_bit", "brewing_stand_slot_b_bit",
    "brewing_stand_slot_c_bit", "covered_bit",
}

# 标签类型为 Short 的字段
_NBT_SHORT_FIELDS = {"Damage", "Health", "Age"}


def _tag_to_jsonable(tag):
    """把 NBT 标签树转成可 JSON 序列化的结构，**每个数值都带上真实标签类型**。

    两条都不能省：

    1) 不能用 CompoundTag.to_dict() —— 它是有损的：
       ByteArrayTag -> bytes（json.dumps 抛 TypeError，被 except 吞掉后返回 None，
       结果是**静默丢失整份 NBT** —— 潜影盒存进去但内容物没了且无任何报错）、
       IntArrayTag -> list、FloatTag -> float、LongTag -> int。

    2) **不能靠字段名猜类型**。曾经用 _NBT_BYTE_FIELDS / _NBT_SHORT_FIELDS 两张
       表，表里没收录的字段一律降级成 IntTag。结果附魔书的 `lvl`/`id`（应为 Short）
       和烟花火箭的 `Flight`（应为 Byte）都被写成了 IntTag，客户端按错误类型读，
       表现为**附魔等级变 0、烟花飞行时间变 0**。这种"漏一个字段就坏一种物品"的
       做法不可持续，所以现在改为编码时把真实类型记下来，重建时不需要任何猜测。

    表示法：数值 -> {"@b"/"@s"/"@i"/"@l"/"@f"/"@d": 值}
            字符串、列表、复合标签保持自然形态。
    标记键以 @ 开头 —— 基岩版 NBT 字段名不会以 @ 开头，不会冲突。
    """
    if tag is None:
        return None
    cls = type(tag).__name__
    if cls == "CompoundTag":
        out = {}
        for k, v in tag.items():
            out[str(k)] = _tag_to_jsonable(v)
        return out
    if cls == "ListTag":
        return [_tag_to_jsonable(v) for v in tag]
    if cls == "ByteArrayTag":
        return {"@B": base64.b64encode(bytes(tag)).decode("ascii")}
    if cls == "IntArrayTag":
        return {"@I": [int(x) for x in tag]}
    if cls == "ByteTag":
        return {"@b": int(tag.value)}
    if cls == "ShortTag":
        return {"@s": int(tag.value)}
    if cls == "IntTag":
        return {"@i": int(tag.value)}
    if cls == "LongTag":
        return {"@l": int(tag.value)}
    if cls == "FloatTag":
        return {"@f": float(tag.value)}
    if cls == "DoubleTag":
        return {"@d": float(tag.value)}
    if cls == "StringTag":
        return str(tag.value)
    # 未知类型：退回 to_dict()，至少不抛异常
    try:
        return tag.to_dict()
    except Exception:
        return None


def _build_nbt(value, field_name: str = ""):
    """把普通 Python 值按基岩版正确的标签类型重建为 NBT 标签树。

    数值类型优先看 @ 标记（由 _tag_to_jsonable 写入，是标签的真实类型）。
    只有**旧格式**的裸整数才回退到按字段名猜 —— 那是本次改动之前存的数据，
    新写入的数据一律带标记，不再依赖字段名表。
    """
    from endstone.nbt import (CompoundTag, ListTag, StringTag, IntTag, LongTag,
                              ByteTag, ShortTag, DoubleTag, FloatTag,
                              ByteArrayTag, IntArrayTag)
    if isinstance(value, dict):
        # 单键 @ 标记 → 显式类型的标签（见 _tag_to_jsonable）
        if len(value) == 1:
            mk, mv = next(iter(value.items()))
            if mk == "@b":
                return ByteTag(int(mv))
            if mk == "@s":
                return ShortTag(int(mv))
            if mk == "@i":
                return IntTag(int(mv))
            if mk == "@l":
                return LongTag(int(mv))
            if mk == "@f":
                return FloatTag(float(mv))
            if mk == "@d":
                return DoubleTag(float(mv))
            if mk == "@B":
                return ByteArrayTag(base64.b64decode(mv))
            if mk == "@I":
                return IntArrayTag([int(x) for x in mv])
        tag = CompoundTag()
        for k, v in value.items():
            tag[str(k)] = _build_nbt(v, str(k))
        return tag
    if isinstance(value, (list, tuple)):
        lst = ListTag()
        for elem in value:
            lst.append(_build_nbt(elem, field_name))
        return lst
    if isinstance(value, bool):
        return ByteTag(1 if value else 0)
    if isinstance(value, int):
        # 旧格式兼容：没有 @ 标记的裸整数只能按字段名猜
        if field_name in _NBT_BYTE_FIELDS:
            return ByteTag(value)
        if field_name in _NBT_SHORT_FIELDS:
            return ShortTag(value)
        return IntTag(value)
    if isinstance(value, float):
        return DoubleTag(value)
    return StringTag(str(value))


def _encode_nbt_b64(nbt_dict) -> Optional[str]:
    """dict -> base64(JSON)。sort_keys 保证同一份 NBT 每次编码结果一致，便于分组与匹配。"""
    if not nbt_dict:
        return None
    try:
        raw = json.dumps(nbt_dict, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def _decode_nbt_b64(nbt_b64: str):
    """base64(JSON) -> NBT 标签树。失败返回 None。"""
    if not nbt_b64:
        return None
    try:
        payload = json.loads(base64.b64decode(nbt_b64).decode("utf-8"))
        if not payload:
            return None
        return _build_nbt(payload)
    except Exception:
        return None


def _normalize_enchant_id(eid: str) -> str:
    """统一为 minecraft:xxx 格式，兼容旧数据中的短 id。"""
    if not eid:
        return eid
    if eid.startswith("minecraft:"):
        return eid
    return "minecraft:" + eid.replace(" ", "_").lower()


def _build_enchant_ids() -> List[str]:
    """从 endstone.enchantments.Enchantment 收集所有附魔字符串 id（仅执行一次）。"""
    global _ENCHANT_IDS
    if _ENCHANT_IDS:
        return _ENCHANT_IDS
    try:
        from endstone.enchantments import Enchantment
        for name in dir(Enchantment):
            if name.isupper():
                val = getattr(Enchantment, name, None)
                if isinstance(val, str) and val.startswith("minecraft:"):
                    _ENCHANT_IDS.append(val)
    except Exception:
        pass
    if not _ENCHANT_IDS:
        _ENCHANT_IDS = [
            "minecraft:aqua_affinity", "minecraft:bane_of_arthropods",
            "minecraft:blast_protection", "minecraft:breach", "minecraft:channeling",
            "minecraft:binding", "minecraft:vanishing", "minecraft:density",
            "minecraft:depth_strider", "minecraft:efficiency", "minecraft:feather_falling",
            "minecraft:fire_aspect", "minecraft:fire_protection", "minecraft:flame",
            "minecraft:frost_walker", "minecraft:impaling", "minecraft:infinity",
            "minecraft:knockback", "minecraft:looting", "minecraft:loyalty",
            "minecraft:luck_of_the_sea", "minecraft:lure", "minecraft:mending",
            "minecraft:multishot", "minecraft:piercing", "minecraft:power",
            "minecraft:projectile_protection", "minecraft:protection", "minecraft:punch",
            "minecraft:quick_charge", "minecraft:respiration", "minecraft:riptide",
            "minecraft:sharpness", "minecraft:silk_touch", "minecraft:smite",
            "minecraft:soul_speed", "minecraft:swift_sneak", "minecraft:thorns",
            "minecraft:unbreaking", "minecraft:wind_burst",
        ]
    return _ENCHANT_IDS


class InventoryManager:
    """
    专门负责玩家背包物品管理的类。
    依赖插件实例以使用 _safe_log 与 server（如语言翻译）。
    """

    def __init__(self, plugin: Any):
        """
        :param plugin: 插件实例，需提供 _safe_log(level, message) 与 server
        """
        self._plugin = plugin
        self._server = getattr(plugin, "server", None)

    def _log(self, level: str, message: str) -> None:
        if hasattr(self._plugin, "_safe_log") and self._plugin._safe_log:
            self._plugin._safe_log(level, message)
        else:
            print(f"[{level.upper()}] {message}")

    def _serialize_item_nbt(self, item_stack: Any) -> Optional[str]:
        """
        将物品完整 NBT 序列化为 base64(JSON)，用于完整还原潜影盒/收纳袋内容、
        附魔书、铁砧命名等 ItemMeta 无法表达的标签。

        注意：endstone 0.11.3 的 CompoundTag 没有 dump()，无法做二进制序列化，
        因此走标签树遍历 + _build_nbt() 重建的路子。

        这里不能直接用 CompoundTag.to_dict()：它会把 ByteArrayTag 变成 bytes
        导致 JSON 序列化失败，进而**静默丢掉整个 NBT**（潜影盒存进去但内容物没了）。
        """
        def _type_id():
            t = getattr(item_stack, "type", None)
            return str(getattr(t, "id", t) or "?")

        try:
            if not item_stack:
                return None
            nbt_compound = getattr(item_stack, "nbt", None)
            if nbt_compound is None:
                return None
            data = _tag_to_jsonable(nbt_compound)
            if not data:
                return None
            encoded = _encode_nbt_b64(data)
            if encoded is None:
                # 编码失败绝不能静默：那会变成"物品存进去了但内容没了"，
                # 而且调用方完全看不出来。这里明确报出来。
                self._log(
                    "error",
                    f"[ARCInventory] NBT 编码失败，该物品的内容将被丢弃: type={_type_id()}",
                )
            return encoded
        except Exception as e:
            self._log(
                "error",
                f"[ARCInventory] NBT 序列化异常，该物品的内容将被丢弃: "
                f"type={_type_id()} err={e}",
            )
            return None

    def _get_item_enchants(self, item_stack: Any) -> Dict[str, int]:
        """
        从 ItemStack 安全读取附魔信息（str->int）。
        不访问 ItemMeta.enchants（会触发 unhashable），改用 get_enchant_level(id) 逐个查询。
        """
        if not item_stack or not getattr(item_stack, "item_meta", None):
            return {}
        meta = item_stack.item_meta
        if not getattr(meta, "has_enchants", False):
            return {}
        result: Dict[str, int] = {}
        get_level = getattr(meta, "get_enchant_level", None)
        if not callable(get_level):
            return {}
        try:
            for enchant_id in _build_enchant_ids():
                try:
                    level = get_level(enchant_id)
                    if level and int(level) > 0:
                        result[enchant_id] = int(level)
                except Exception:
                    continue
        except Exception as enc_e:
            self._log(
                "warning",
                f"[ARCInventory] Get enchants (get_enchant_level) failed: {enc_e}\n{traceback.format_exc()}",
            )
        return result

    def _get_item_lore(self, item_stack: Any) -> List[str]:
        """从 ItemStack 安全读取 Lore。"""
        if not item_stack or not getattr(item_stack, "item_meta", None):
            return []
        if not getattr(item_stack.item_meta, "has_lore", False):
            return []
        try:
            lore = item_stack.item_meta.lore
            return list(lore) if isinstance(lore, list) else []
        except Exception:
            return []

    def _item_stack_matches_info(
        self,
        item_stack: Any,
        required_type: str,
        required_data: int,
        required_enchants: Dict[str, int],
        required_lore: List[str],
        required_nbt_b64: Optional[str] = None,
    ) -> bool:
        """判断单个 ItemStack 是否与 item_info 要求一致（类型、data；若有 nbt_b64 则比对完整 NBT，否则比对附魔与 Lore）。"""
        if not item_stack or not item_stack.type:
            return False
        if item_stack.type.id != required_type or item_stack.data != required_data:
            return False
        if required_nbt_b64:
            serialized = self._serialize_item_nbt(item_stack)
            return serialized is not None and serialized == required_nbt_b64
        item_enchants = self._get_item_enchants(item_stack)
        item_lore = self._get_item_lore(item_stack)
        if required_enchants:
            for eid, level in required_enchants.items():
                key = eid if eid in item_enchants else _normalize_enchant_id(eid)
                if item_enchants.get(key) != level:
                    return False
        if required_lore:
            if len(required_lore) != len(item_lore):
                return False
            for i, line in enumerate(required_lore):
                if i >= len(item_lore) or item_lore[i] != line:
                    return False
        return True

    def get_inventory_items(self, player: Any) -> List[Dict[str, Any]]:
        """
        获取玩家背包中所有有效物品的列表。
        每项为 dict：type, type_translation_key, name, count, data, enchants, lore, slot_index；
        若物品含完整用户 NBT（如附魔书），另含 nbt_b64（Base64 二进制 NBT）。
        """
        items: List[Dict[str, Any]] = []
        try:
            inventory = player.inventory
            for slot_index in range(inventory.size):
                try:
                    item_stack = inventory.get_item(slot_index)
                except Exception as slot_e:
                    self._log(
                        "warning",
                        f"[ARCInventory] get_item(slot={slot_index}) failed: {slot_e}",
                    )
                    continue
                if not item_stack or not item_stack.type or item_stack.amount <= 0:
                    continue
                try:
                    item_type_id = item_stack.type.id
                    item_type_translation_key = item_stack.type.translation_key
                    display_name = item_type_id
                    if self._server and hasattr(self._server, "language"):
                        try:
                            display_name = self._server.language.translate(
                                item_type_translation_key,
                                None,
                                getattr(player, "locale", None),
                            )
                        except Exception:
                            pass
                    if item_stack.item_meta and getattr(
                        item_stack.item_meta, "has_display_name", False
                    ):
                        display_name = item_stack.item_meta.display_name
                    enchants = self._get_item_enchants(item_stack)
                    lore = self._get_item_lore(item_stack)
                    nbt_b64 = self._serialize_item_nbt(item_stack)
                    entry: Dict[str, Any] = {
                        "type": item_type_id,
                        "type_translation_key": item_type_translation_key,
                        "name": display_name,
                        "count": item_stack.amount,
                        "data": item_stack.data,
                        "enchants": enchants,
                        "lore": lore,
                        "slot_index": slot_index,
                    }
                    if nbt_b64:
                        entry["nbt_b64"] = nbt_b64
                    items.append(entry)
                except Exception as item_e:
                    self._log(
                        "warning",
                        f"[ARCInventory] Slot {slot_index} item build failed: "
                        f"{item_e}\n{traceback.format_exc()}",
                    )
            return items
        except Exception as e:
            self._log(
                "error",
                f"[ARCInventory] Get player inventory error: {str(e)}\n{traceback.format_exc()}",
            )
            return []

    def has_item(self, player: Any, item_info: Dict[str, Any]) -> bool:
        """检查玩家背包是否拥有至少 item_info 要求数量、类型、data、附魔、Lore 一致的物品。"""
        try:
            inventory = player.inventory
            required_type = item_info["type"]
            required_count = item_info["count"]
            required_data = item_info.get("data", 0)
            required_enchants = item_info.get("enchants", {})
            required_lore = item_info.get("lore", [])
            required_nbt_b64 = item_info.get("nbt_b64")
            total_count = 0
            for slot_index in range(inventory.size):
                item_stack = inventory.get_item(slot_index)
                if not self._item_stack_matches_info(
                    item_stack,
                    required_type,
                    required_data,
                    required_enchants,
                    required_lore,
                    required_nbt_b64,
                ):
                    continue
                total_count += item_stack.amount
                if total_count >= required_count:
                    return True
            return False
        except Exception as e:
            self._log("error", f"[ARCInventory] Player has item check error: {str(e)}")
            return False

    def remove_item(self, player: Any, item_info: Dict[str, Any]) -> bool:
        """从玩家背包移除与 item_info 匹配的物品（数量、类型、data、附魔、Lore）。"""
        try:
            inventory = player.inventory
            required_type = item_info["type"]
            required_count = item_info["count"]
            required_data = item_info.get("data", 0)
            required_enchants = item_info.get("enchants", {})
            required_lore = item_info.get("lore", [])
            required_nbt_b64 = item_info.get("nbt_b64")
            if not self.has_item(player, item_info):
                return False
            remaining_to_remove = required_count
            slots_to_modify: List[tuple] = []
            for slot_index in range(inventory.size):
                if remaining_to_remove <= 0:
                    break
                item_stack = inventory.get_item(slot_index)
                if not self._item_stack_matches_info(
                    item_stack,
                    required_type,
                    required_data,
                    required_enchants,
                    required_lore,
                    required_nbt_b64,
                ):
                    continue
                remove_from_slot = min(remaining_to_remove, item_stack.amount)
                slots_to_modify.append((slot_index, item_stack, remove_from_slot))
                remaining_to_remove -= remove_from_slot
            for slot_index, original_stack, remove_count in slots_to_modify:
                new_amount = original_stack.amount - remove_count
                if new_amount <= 0:
                    inventory.set_item(slot_index, None)
                else:
                    original_stack.amount = new_amount
                    inventory.set_item(slot_index, original_stack)
            return True
        except Exception as e:
            self._log(
                "error", f"[ARCInventory] Remove item from player error: {str(e)}"
            )
            return False

    def give_item(self, player: Any, item_info: Dict[str, Any]) -> bool:
        """向玩家背包发放物品（类型、数量、data；附魔/Lore 若 API 支持则应用）。"""
        given = self.give_item_count(player, item_info)
        return given >= int(item_info.get("count", 0) or 0)

    def give_item_count(self, player: Any, item_info: Dict[str, Any]) -> int:
        """
        尝试向玩家背包发放物品，返回**实际成功发放的数量**（可能为部分）。
        注意：当背包不足时不会强行回滚已发放部分；调用方需要基于返回值决定扣款/回滚策略。
        """
        try:
            from endstone.inventory import ItemStack

            inventory = player.inventory
            item_type_id = item_info["type"]
            total_amount = item_info["count"]
            item_data = item_info.get("data", 0)
            if total_amount <= 0:
                self._log("warning", f"[ARCInventory] Invalid item amount: {total_amount}")
                return 0
            # 带 NBT 的物品（潜影盒 / 收纳袋）堆叠上限为 1，必须逐个构造 ItemStack；
            # 按 64 分块会生成非法堆叠数量，导致 add_item 失败或物品被截断。
            nbt_b64 = item_info.get("nbt_b64")
            max_chunk = 1 if nbt_b64 else 64
            remaining_to_give = total_amount
            given_total = 0
            while remaining_to_give > 0:
                current_amount = min(remaining_to_give, max_chunk)
                if current_amount <= 0:
                    break
                item_stack = ItemStack(
                    type=item_type_id,
                    amount=current_amount,
                    data=item_data,
                )
                if nbt_b64:
                    try:
                        tag = _decode_nbt_b64(nbt_b64)
                        if tag is not None and hasattr(item_stack, "nbt"):
                            item_stack.nbt = tag
                    except Exception as e:
                        self._log(
                            "error",
                            f"[ARCInventory] Restore item NBT failed: {e}",
                        )
                elif item_info.get("enchants") or item_info.get("lore"):
                    try:
                        meta = item_stack.item_meta
                        if meta and item_info.get("enchants"):
                            for enchant_id, level in item_info["enchants"].items():
                                try:
                                    if hasattr(meta, "add_enchant"):
                                        meta.add_enchant(str(enchant_id), int(level))
                                except Exception as e:
                                    self._log(
                                        "warning",
                                        f"[ARCInventory] Failed to apply enchant {enchant_id}: {e}",
                                    )
                        if meta and item_info.get("lore") and hasattr(meta, "lore"):
                            try:
                                meta.lore = list(item_info["lore"])
                            except Exception as e:
                                self._log(
                                    "warning",
                                    f"[ARCInventory] Failed to apply lore: {e}",
                                )
                        if meta and hasattr(item_stack, "set_item_meta"):
                            item_stack.set_item_meta(meta)
                    except Exception as e:
                        self._log("warning", f"[ARCInventory] Apply item meta: {e}")
                remaining_items = inventory.add_item(item_stack)
                if remaining_items:
                    try:
                        if hasattr(remaining_items, "get"):
                            first_remaining = remaining_items.get(0)
                        elif isinstance(remaining_items, dict):
                            first_remaining = next(iter(remaining_items.values()), None)
                        else:
                            first_remaining = (
                                remaining_items[0]
                                if isinstance(remaining_items, list)
                                and len(remaining_items) > 0
                                else None
                            )
                        remaining_amount = (
                            getattr(first_remaining, "amount", 0)
                            if first_remaining is not None
                            else 0
                        )
                        added_amount = item_stack.amount - remaining_amount
                        if added_amount > 0:
                            given_total += added_amount
                        remaining_to_give -= added_amount
                        if added_amount == 0:
                            self._log(
                                "warning",
                                f"[ARCInventory] Player {player.name} inventory full",
                            )
                            break
                    except Exception as e:
                        self._log(
                            "warning",
                            f"[ARCInventory] Error calculating remaining: {e}",
                        )
                        # 计算失败时，宁可保守：认为本次未成功添加，直接停止，避免错误累计
                        break
                else:
                    remaining_to_give -= item_stack.amount
                    given_total += item_stack.amount
            return int(given_total)
        except Exception as e:
            self._log(
                "error", f"[ARCInventory] Give item to player error: {str(e)}"
            )
            return 0
