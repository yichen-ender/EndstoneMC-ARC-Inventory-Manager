"""ARC 保险箱数据层（保留 SLE 旧存储格式）."""

import json
from pathlib import Path
from typing import Optional

from endstone_arc_inventory_manager.item_cn import _ITEM_CN

MAX_SAFES = 6
MAX_STACK = 2147483647
EXP_VAULT_PRICE = 20000
EXP_VAULT_MAX = 2147483647

SAFE_TYPES: dict[str, dict] = {
    "small": {"name": "小型保险箱", "slots": 2, "price": 3000},
    "normal": {"name": "普通保险箱", "slots": 4, "price": 5000},
    "large": {"name": "大型保险箱", "slots": 6, "price": 10000},
}


def _cn_name(item_type: str) -> str:
    key = item_type.split(":")[-1]
    return _ITEM_CN.get(key, key)


# Potion data ID -> Chinese name (普通=Ⅰ级, 延长=Ⅱ级, 加强=Ⅲ级)
_POTION_NAMES: dict[int, str] = {
    0: "水瓶", 1: "平凡的药水 Ⅰ", 2: "平凡的药水 Ⅱ", 3: "浓稠的药水 Ⅰ", 4: "粗制的药水 Ⅰ",
    5: "夜视药水 Ⅰ", 6: "夜视药水 Ⅱ", 7: "隐身药水 Ⅰ", 8: "隐身药水 Ⅱ",
    9: "跳跃药水 Ⅰ", 10: "跳跃药水 Ⅱ", 11: "跳跃药水 Ⅲ",
    12: "抗火药水 Ⅰ", 13: "抗火药水 Ⅱ", 14: "迅捷药水 Ⅰ", 15: "迅捷药水 Ⅱ", 16: "迅捷药水 Ⅲ",
    17: "迟缓药水 Ⅰ", 18: "迟缓药水 Ⅱ", 19: "水肺药水 Ⅰ", 20: "水肺药水 Ⅱ",
    21: "治疗药水 Ⅰ", 22: "治疗药水 Ⅲ", 23: "伤害药水 Ⅰ", 24: "伤害药水 Ⅲ",
    25: "剧毒药水 Ⅰ", 26: "剧毒药水 Ⅱ", 27: "剧毒药水 Ⅲ",
    28: "再生药水 Ⅰ", 29: "再生药水 Ⅱ", 30: "再生药水 Ⅲ",
    31: "力量药水 Ⅰ", 32: "力量药水 Ⅱ", 33: "力量药水 Ⅲ",
    34: "虚弱药水 Ⅰ", 35: "虚弱药水 Ⅱ", 36: "衰变药水 Ⅰ",
    37: "神龟药水 Ⅰ", 38: "神龟药水 Ⅱ", 39: "神龟药水 Ⅲ",
    40: "缓降药水 Ⅰ", 41: "缓降药水 Ⅱ", 42: "迟缓药水 Ⅲ",
    43: "蓄风药水 Ⅰ", 44: "盘丝药水 Ⅰ", 45: "渗浆药水 Ⅰ", 46: "虫蚀药水 Ⅰ",
}

# Tipped arrow data ID -> Chinese name (Ⅰ/Ⅱ/Ⅲ)
_ARROW_NAMES: dict[int, str] = {
    0: "箭", 1: "喷溅之箭", 2: "药箭(平凡) Ⅰ", 3: "药箭(平凡) Ⅱ",
    4: "药箭(浓稠) Ⅰ", 5: "药箭(粗制) Ⅰ",
    6: "夜视之箭 Ⅰ", 7: "夜视之箭 Ⅱ", 8: "隐身之箭 Ⅰ", 9: "隐身之箭 Ⅱ",
    10: "跳跃之箭 Ⅰ", 11: "跳跃之箭 Ⅱ", 12: "跳跃之箭 Ⅲ",
    13: "抗火之箭 Ⅰ", 14: "抗火之箭 Ⅱ", 15: "迅捷之箭 Ⅰ", 16: "迅捷之箭 Ⅱ", 17: "迅捷之箭 Ⅲ",
    20: "水肺之箭 Ⅰ", 21: "水肺之箭 Ⅱ", 22: "治疗之箭 Ⅰ", 23: "治疗之箭 Ⅲ",
    24: "伤害之箭 Ⅰ", 25: "伤害之箭 Ⅲ",
    29: "再生之箭 Ⅰ", 30: "再生之箭 Ⅱ", 31: "再生之箭 Ⅲ",
    32: "力量之箭 Ⅰ", 33: "力量之箭 Ⅱ", 34: "力量之箭 Ⅲ",
    35: "虚弱之箭 Ⅰ", 36: "虚弱之箭 Ⅱ", 37: "衰变之箭 Ⅰ",
    38: "神龟之箭 Ⅰ", 39: "神龟之箭 Ⅱ", 40: "神龟之箭 Ⅲ",
    41: "缓降之箭 Ⅰ", 42: "缓降之箭 Ⅱ",
    44: "蓄风之箭 Ⅰ", 45: "盘丝之箭 Ⅰ", 46: "渗浆之箭 Ⅰ", 47: "虫蚀之箭 Ⅰ",
}


def _item_display_name(item_data: dict) -> str:
    """Get display name considering potion/ominous data values."""
    if item_data.get("display_name"):
        return item_data["display_name"]
    item_type = item_data.get("type", "")
    data = item_data.get("data", 0)
    if item_type in ("minecraft:potion", "minecraft:lingering_potion", "minecraft:splash_potion"):
        base = {"minecraft:potion": "药水", "minecraft:lingering_potion": "滞留药水",
                "minecraft:splash_potion": "喷溅药水"}.get(item_type, "药水")
        potion_name = _POTION_NAMES.get(data, f"未知({data})")
        return f"{potion_name}"
    if item_type == "minecraft:arrow" and data:
        return _ARROW_NAMES.get(data, f"药箭({data})")
    if item_type == "minecraft:ominous_bottle" and data:
        roman = {1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", 4: "Ⅳ", 5: "Ⅴ"}.get(data, str(data))
        return f"不详之瓶 {roman}"
    return _cn_name(item_type)


class SafeManager:
    def __init__(self, data_folder: Path):
        self.data_folder = data_folder
        self.data_file = data_folder / "safes.json"
        self.players: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.data_file.exists():
            try:
                data = json.loads(self.data_file.read_text("utf-8"))
                self.players = data.get("players", {})
            except (json.JSONDecodeError, OSError):
                self.players = {}
        else:
            self.players = {}

    def _save(self):
        self.data_folder.mkdir(parents=True, exist_ok=True)
        self.data_file.write_text(
            json.dumps({"players": self.players}, ensure_ascii=False, indent=2), "utf-8"
        )

    def _ensure_player(self, player_name: str):
        if player_name not in self.players:
            self.players[player_name] = {"safes": []}

    def get_safe_count(self, player_name: str) -> int:
        self._ensure_player(player_name)
        return len(self.players[player_name]["safes"])

    def can_buy_safe(self, player_name: str) -> bool:
        return self.get_safe_count(player_name) < MAX_SAFES

    def get_safe_price(self, safe_type: str) -> int:
        return SAFE_TYPES.get(safe_type, {}).get("price", 5000)

    def get_safe_refund(self, safe_type: str) -> int:
        return int(self.get_safe_price(safe_type) * 0.6)

    def buy_safe(self, player_name: str, safe_type: str = "normal") -> bool:
        if not self.can_buy_safe(player_name):
            return False
        cfg = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"])
        slots = cfg["slots"]
        self._ensure_player(player_name)
        self.players[player_name]["safes"].append({
            "type": safe_type,
            "slots": slots,
            "items": [None] * slots,
        })
        self._save()
        return True

    def delete_safe(self, player_name: str, safe_index: int) -> Optional[str]:
        self._ensure_player(player_name)
        safes = self.players[player_name]["safes"]
        if safe_index < 0 or safe_index >= len(safes):
            return None
        safe_type = safes[safe_index].get("type", "normal")
        safes.pop(safe_index)
        self._save()
        return safe_type

    def get_safe_info(self, player_name: str, safe_index: int) -> Optional[dict]:
        self._ensure_player(player_name)
        safes = self.players[player_name]["safes"]
        if safe_index < 0 or safe_index >= len(safes):
            return None
        s = safes[safe_index]
        return {"type": s.get("type", "normal"), "slots": s.get("slots", 4),
                "name": s.get("name", "")}

    def rename_safe(self, player_name: str, safe_index: int, new_name: str) -> bool:
        self._ensure_player(player_name)
        safes = self.players[player_name]["safes"]
        if safe_index < 0 or safe_index >= len(safes):
            return False
        safes[safe_index]["name"] = new_name
        self._save()
        return True

    def clear_safe_slot(self, player_name: str, safe_index: int, slot: int) -> bool:
        """Permanently clear an item slot in a safe."""
        self._ensure_player(player_name)
        safes = self.players[player_name]["safes"]
        if safe_index < 0 or safe_index >= len(safes):
            return False
        items = safes[safe_index]["items"]
        if slot < 0 or slot >= len(items):
            return False
        items[slot] = None
        self._save()
        return True

    def migrate_name_to_xuid(self, player_name: str, xuid: str):
        """Migrate a player's data from name-keyed to xuid-keyed."""
        if not xuid or player_name == xuid:
            return
        if player_name in self.players and xuid not in self.players:
            self.players[xuid] = self.players[player_name]
            del self.players[player_name]
            self._save()

    def get_safe_items(self, player_name: str, safe_index: int) -> list:
        self._ensure_player(player_name)
        safes = self.players[player_name]["safes"]
        if safe_index < 0 or safe_index >= len(safes):
            return []
        return safes[safe_index]["items"]

    def _make_item_key(self, item_type: str, enchantments: list, display_name: str,
                        container_items: list = None, data: int = 0) -> tuple:
        ench_tuple = tuple(sorted((e["id"], e["level"]) for e in (enchantments or [])))
        cont_tuple = tuple(sorted((c["item"], c["count"]) for c in (container_items or [])))
        return (item_type, display_name, ench_tuple, cont_tuple, data)

    def can_store_item(self, player_name: str, safe_index: int, item_type: str,
                       enchantments: list = None, display_name: str = "",
                       container_items: list = None, data: int = 0) -> Optional[int]:
        target_key = self._make_item_key(item_type, enchantments or [], display_name,
                                         container_items, data)
        items = self.get_safe_items(player_name, safe_index)
        for i, item in enumerate(items):
            if item is not None:
                item_key = self._make_item_key(
                    item["type"], item.get("enchantments", []),
                    item.get("display_name", ""), item.get("container_items"),
                    item.get("data", 0))
                if item_key == target_key and item["amount"] < MAX_STACK:
                    return i
        for i, item in enumerate(items):
            if item is None:
                return i
        return None

    def deposit_item(self, player_name: str, safe_index: int, item_type: str,
                     amount: int, enchantments: list = None, display_name: str = "",
                     container_items: list = None, data: int = 0) -> tuple:
        """Store item, searching ALL safes for a matching identical slot first.
        Returns (stored_amount, actual_safe_index, actual_slot)."""
        target_key = self._make_item_key(item_type, enchantments or [], display_name,
                                         container_items, data)
        self._ensure_player(player_name)
        safes = self.players[player_name]["safes"]

        # 1. Search all safes for an identical item slot with space
        for si, safe in enumerate(safes):
            items = safe["items"]
            for slot_i, item in enumerate(items):
                if item is not None:
                    item_key = self._make_item_key(
                        item["type"], item.get("enchantments", []),
                        item.get("display_name", ""), item.get("container_items"),
                        item.get("data", 0))
                    if item_key == target_key and item["amount"] < MAX_STACK:
                        space = MAX_STACK - item["amount"]
                        to_store = min(amount, space)
                        item["amount"] += to_store
                        self._save()
                        return (to_store, si, slot_i)

        # 2. No matching slot - use an empty slot in the preferred safe
        if 0 <= safe_index < len(safes):
            items = safes[safe_index]["items"]
            for slot_i, item in enumerate(items):
                if item is None:
                    to_store = min(amount, MAX_STACK)
                    items[slot_i] = {"type": item_type, "amount": to_store,
                                     "enchantments": enchantments or [],
                                     "display_name": display_name,
                                     "container_items": container_items or [],
                                     "data": data}
                    self._save()
                    return (to_store, safe_index, slot_i)

        return (0, safe_index, -1)

    def withdraw_item(self, player_name: str, safe_index: int, slot: int,
                      amount: int) -> Optional[dict]:
        items = self.get_safe_items(player_name, safe_index)
        if slot < 0 or slot >= len(items) or items[slot] is None:
            return None
        item = items[slot]
        to_withdraw = min(amount, item["amount"])
        result = {
            "type": item["type"],
            "amount": to_withdraw,
            "enchantments": item.get("enchantments", []),
            "display_name": item.get("display_name", ""),
            "container_items": item.get("container_items", []),
            "data": item.get("data", 0),
        }
        item["amount"] -= to_withdraw
        if item["amount"] <= 0:
            items[slot] = None
        self._save()
        return result

    # ---- Experience Vault ----
    def has_exp_vault(self, player_name: str) -> bool:
        self._ensure_player(player_name)
        return "exp_vault" in self.players[player_name]

    def buy_exp_vault(self, player_name: str) -> bool:
        self._ensure_player(player_name)
        if "exp_vault" in self.players[player_name]:
            return False
        self.players[player_name]["exp_vault"] = 0
        self._save()
        return True

    def get_exp_vault_level(self, player_name: str) -> int:
        self._ensure_player(player_name)
        return self.players[player_name].get("exp_vault", 0)

    def deposit_exp(self, player_name: str, amount: int) -> int:
        self._ensure_player(player_name)
        current = self.players[player_name].get("exp_vault", 0)
        space = MAX_STACK - current
        to_store = min(amount, space)
        self.players[player_name]["exp_vault"] = current + to_store
        self._save()
        return to_store

    def withdraw_exp(self, player_name: str, amount: int) -> int:
        self._ensure_player(player_name)
        current = self.players[player_name].get("exp_vault", 0)
        to_withdraw = min(amount, current)
        self.players[player_name]["exp_vault"] = current - to_withdraw
        self._save()
        return to_withdraw

    def format_slot_display(self, item_data: dict) -> str:
        if item_data is None:
            return "空"
        name = _item_display_name(item_data)
        ench_str = ""
        if item_data.get("enchantments"):
            ench_names = [f"{e['id']} Lv.{e['level']}" for e in item_data["enchantments"]]
            ench_str = f" [{', '.join(ench_names)}]"
        cont = item_data.get("container_items")
        if cont:
            total = sum(c["count"] for c in cont)
            ench_str += f" [内含{total}件]"
        return f"{name} x{item_data['amount']}{ench_str}"
