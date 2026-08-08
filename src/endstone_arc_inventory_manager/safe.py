"""ARC 保险箱数据层（保留 SLE 旧存储格式）。

存储结构 safes.json：
{
  "players": { "<xuid>": {"safes": [ {type, slots, name, items} ]} },
  "guilds":  {
    "<guild_id>": {
      "managers": {"<xuid>": true},   // 仓库管理权限（全公会统一，由会长授予管理员）
      "safes": [
        {
          "type": "small|normal|large", "slots": 2|4|6, "name": "",
          "items": [ null | item, ...],
          "contribution_limit": 0,                       // 对普通成员生效的存取最低个人贡献点，0=无限制
          "permissions": {"<xuid>": {"store": bool, "withdraw": bool}}  // 逐人覆盖（可存可取/仅存/仅取/双向禁）
        }
      ]
    }
  }
}

所有变更操作使用同一把 RLock 串行化，避免并发存取时 read-modify-write 竞态导致刷物品/丢物品。
"""

import json
import threading
from pathlib import Path
from typing import Optional

from endstone_arc_inventory_manager.item_cn import _ITEM_CN

MAX_SAFES = 6
MAX_STACK = 2147483647

SAFE_TYPES: dict[str, dict] = {
    "small": {"name": "小型保险箱", "slots": 2, "price": 3000},
    "normal": {"name": "普通保险箱", "slots": 4, "price": 5000},
    "large": {"name": "大型保险箱", "slots": 6, "price": 10000},
}

# 逐人存取权限模式
MODE_BOTH = "both"              # 可存可取（默认，删除覆盖）
MODE_STORE_ONLY = "store_only"  # 仅存（禁取）
MODE_WITHDRAW_ONLY = "withdraw_only"  # 仅取（禁存）
MODE_NONE = "none"              # 双向禁止

_MODE_LABEL = {
    MODE_BOTH: "可存可取",
    MODE_STORE_ONLY: "仅存(禁取)",
    MODE_WITHDRAW_ONLY: "仅取(禁存)",
    MODE_NONE: "双向禁止",
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


def permission_mode_label(mode: str) -> str:
    return _MODE_LABEL.get(mode, "可存可取")


def mode_from_override(store: bool, withdraw: bool) -> str:
    """根据 (store, withdraw) 得到权限模式名。"""
    if store and withdraw:
        return MODE_BOTH
    if store:
        return MODE_STORE_ONLY
    if withdraw:
        return MODE_WITHDRAW_ONLY
    return MODE_NONE


class SafeManager:
    def __init__(self, data_folder: Path):
        self.data_folder = data_folder
        self.data_file = data_folder / "safes.json"
        self.players: dict[str, dict] = {}
        self.guilds: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._load()

    @property
    def lock(self) -> threading.RLock:
        """供 UI 层持有以包裹复合存取操作（可重入）。"""
        return self._lock

    def _load(self):
        with self._lock:
            if self.data_file.exists():
                try:
                    data = json.loads(self.data_file.read_text("utf-8"))
                    self.players = data.get("players", {}) or {}
                    self.guilds = data.get("guilds", {}) or {}
                except (json.JSONDecodeError, OSError):
                    self.players = {}
                    self.guilds = {}
            else:
                self.players = {}
                self.guilds = {}

    def _save(self):
        with self._lock:
            self.data_folder.mkdir(parents=True, exist_ok=True)
            self.data_file.write_text(
                json.dumps({"players": self.players, "guilds": self.guilds},
                           ensure_ascii=False, indent=2), "utf-8"
            )

    # ---------- scope 解析 ----------

    @staticmethod
    def p_scope(xuid: str) -> tuple:
        return ("p", str(xuid))

    @staticmethod
    def g_scope(guild_id) -> tuple:
        return ("g", str(guild_id))

    def _resolve(self, scope: tuple):
        """返回 (kind, key, safes_list)，自动创建条目。调用方需持锁或处于主线程。"""
        kind, key = scope
        if kind == "g":
            rec = self.guilds.setdefault(key, {"managers": {}, "safes": []})
            return kind, key, rec["safes"]
        self.players.setdefault(key, {"safes": []})
        return kind, key, self.players[key]["safes"]

    def _guild_rec(self, guild_id):
        return self.guilds.setdefault(str(guild_id), {"managers": {}, "safes": []})

    # ---------- 统一接口（个人与公会共用） ----------

    def get_safe_count(self, scope: tuple) -> int:
        with self._lock:
            _, _, safes = self._resolve(scope)
            return len(safes)

    def can_buy_safe(self, scope: tuple) -> bool:
        return self.get_safe_count(scope) < MAX_SAFES

    def get_safe_price(self, safe_type: str) -> int:
        return SAFE_TYPES.get(safe_type, {}).get("price", 5000)

    def get_safe_refund(self, safe_type: str) -> int:
        return int(self.get_safe_price(safe_type) * 0.6)

    def buy_safe(self, scope: tuple, safe_type: str = "normal") -> bool:
        with self._lock:
            if not self.can_buy_safe(scope):
                return False
            cfg = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"])
            _, _, safes = self._resolve(scope)
            safes.append({
                "type": safe_type,
                "slots": cfg["slots"],
                "items": [None] * cfg["slots"],
            })
            self._save()
            return True

    def delete_safe(self, scope: tuple, safe_index: int) -> Optional[str]:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return None
            safe_type = safes[safe_index].get("type", "normal")
            safes.pop(safe_index)
            self._save()
            return safe_type

    def get_safe_info(self, scope: tuple, safe_index: int) -> Optional[dict]:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return None
            s = safes[safe_index]
            return {"type": s.get("type", "normal"), "slots": s.get("slots", 4),
                    "name": s.get("name", "")}

    def rename_safe(self, scope: tuple, safe_index: int, new_name: str) -> bool:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return False
            safes[safe_index]["name"] = new_name
            self._save()
            return True

    def clear_safe_slot(self, scope: tuple, safe_index: int, slot: int) -> bool:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return False
            items = safes[safe_index]["items"]
            if slot < 0 or slot >= len(items):
                return False
            items[slot] = None
            self._save()
            return True

    def get_safe_items(self, scope: tuple, safe_index: int) -> list:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return []
            return safes[safe_index]["items"]

    def _make_item_key(self, item_type: str, enchantments: list, display_name: str,
                        container_items: list = None, data: int = 0) -> tuple:
        ench_tuple = tuple(sorted((e["id"], e["level"]) for e in (enchantments or [])))
        cont_tuple = tuple(sorted((c["item"], c["count"]) for c in (container_items or [])))
        return (item_type, display_name, ench_tuple, cont_tuple, data)

    def _deposit_into(self, safes: list, safe_index: int, item_type: str,
                      amount: int, enchantments: list = None, display_name: str = "",
                      container_items: list = None, data: int = 0) -> tuple:
        """核心入仓：先全仓找相同物品叠放，再在指定箱空槽放入。返回 (stored, actual_index, actual_slot)。"""
        target_key = self._make_item_key(item_type, enchantments or [], display_name,
                                         container_items, data)
        # 1. 搜索所有保险箱中相同的、未叠满的槽位
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
        # 2. 无匹配槽位，用指定保险箱的空槽
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

    def deposit_item(self, scope: tuple, safe_index: int, item_type: str,
                     amount: int, enchantments: list = None, display_name: str = "",
                     container_items: list = None, data: int = 0) -> tuple:
        """入仓（个人或公会仓库共用）。"""
        with self._lock:
            _, _, safes = self._resolve(scope)
            return self._deposit_into(safes, safe_index, item_type, amount,
                                      enchantments, display_name, container_items, data)

    def _withdraw_from(self, safes: list, safe_index: int, slot: int,
                       amount: int) -> Optional[dict]:
        if safe_index < 0 or safe_index >= len(safes):
            return None
        items = safes[safe_index]["items"]
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

    def withdraw_item(self, scope: tuple, safe_index: int, slot: int,
                      amount: int) -> Optional[dict]:
        """出仓（个人或公会仓库共用）。"""
        with self._lock:
            _, _, safes = self._resolve(scope)
            return self._withdraw_from(safes, safe_index, slot, amount)

    # ---------- 公会专属：仓库管理授权 ----------

    def get_guild_managers(self, guild_id) -> set:
        with self._lock:
            rec = self._guild_rec(guild_id)
            return set((rec.get("managers") or {}).keys())

    def set_guild_manager(self, guild_id, xuid: str, on: bool) -> bool:
        with self._lock:
            rec = self._guild_rec(guild_id)
            managers = rec.setdefault("managers", {})
            if on:
                managers[str(xuid)] = True
            else:
                managers.pop(str(xuid), None)
            self._save()
            return True

    def is_guild_manager(self, guild_id, xuid: str) -> bool:
        with self._lock:
            rec = self._guild_rec(guild_id)
            return str(xuid) in (rec.get("managers") or {})

    # ---------- 公会专属：逐人存取权限 / 贡献点限制 ----------

    def get_safe_permission(self, scope: tuple, safe_index: int, xuid: str) -> Optional[dict]:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return None
            perms = safes[safe_index].get("permissions", {}) or {}
            p = perms.get(str(xuid))
            return dict(p) if p else None

    def set_safe_permission(self, scope: tuple, safe_index: int, xuid: str, mode: str) -> bool:
        """mode: both / store_only / withdraw_only / none。both 表示删除覆盖（回到职级默认）。"""
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return False
            rec = safes[safe_index]
            perms = rec.setdefault("permissions", {})
            if mode == MODE_BOTH:
                perms.pop(str(xuid), None)
            elif mode == MODE_STORE_ONLY:
                perms[str(xuid)] = {"store": True, "withdraw": False}
            elif mode == MODE_WITHDRAW_ONLY:
                perms[str(xuid)] = {"store": False, "withdraw": True}
            else:
                perms[str(xuid)] = {"store": False, "withdraw": False}
            self._save()
            return True

    def get_contribution_limit(self, scope: tuple, safe_index: int) -> int:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return 0
            try:
                return int(safes[safe_index].get("contribution_limit") or 0)
            except (TypeError, ValueError):
                return 0

    def set_contribution_limit(self, scope: tuple, safe_index: int, limit: int) -> bool:
        with self._lock:
            _, _, safes = self._resolve(scope)
            if safe_index < 0 or safe_index >= len(safes):
                return False
            try:
                v = int(limit)
            except (TypeError, ValueError):
                v = 0
            safes[safe_index]["contribution_limit"] = max(0, v)
            self._save()
            return True

    def get_safe_contribution_limit(self, scope: tuple, safe_index: int) -> int:
        return self.get_contribution_limit(scope, safe_index)

    # ---------- 存取权限评估 ----------

    def evaluate_access(self, scope: tuple, safe_index: int, xuid: str,
                        role: str, personal_contribution: int = 0) -> tuple:
        """
        评估某成员对某保险箱的存取权限。
        :return: (store_ok, withdraw_ok, reason)  reason 为 None 或错误码（CONTRIB_LOW / SAFE_MISSING）
        规则：
        - 会长：始终可存可取，无视贡献点限制
        - 管理员：受逐人覆盖限制，但无视贡献点限制
        - 成员：受逐人覆盖 + 贡献点限制（个人贡献点 < limit 则存取皆禁）
        - 无逐人覆盖时按职级默认（可存可取）
        """
        with self._lock:
            kind, _, safes = self._resolve(scope)
            if kind != "g" or role == "owner":
                return True, True, None
            if safe_index < 0 or safe_index >= len(safes):
                return False, False, "SAFE_MISSING"
            rec = safes[safe_index]
            perms = rec.get("permissions", {}) or {}
            p = perms.get(str(xuid))
            if p:
                store = bool(p.get("store", True))
                withdraw = bool(p.get("withdraw", True))
            else:
                store = withdraw = True
            if role == "member":
                try:
                    limit = int(rec.get("contribution_limit") or 0)
                except (TypeError, ValueError):
                    limit = 0
                if limit > 0 and int(personal_contribution or 0) < limit:
                    return False, False, "CONTRIB_LOW"
            return store, withdraw, None

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
