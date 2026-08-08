# -*- coding: utf-8 -*-
"""弧光背包管理器：为弧光系列插件提供统一的背包读写 API。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from endstone import Player
from endstone.plugin import Plugin

from endstone_arc_inventory.InventoryManager import InventoryManager


class ARCInventoryPlugin(Plugin):
    """
    Plugin id: arc_inventory
    其他插件：server.get_plugin("arc_inventory") 后调用 api_*。
    """

    api_version = "0.10"
    prefix = "ARCInventory"

    def __init__(self):
        super().__init__()
        self.inventory_manager: Optional[InventoryManager] = None

    def _safe_log(self, level: str, message: str) -> None:
        if hasattr(self, "logger") and self.logger is not None:
            fn = getattr(self.logger, level.lower(), None)
            if callable(fn):
                fn(message)
                return
            self.logger.info(message)
        else:
            print(f"[{level.upper()}] {message}")

    def on_load(self) -> None:
        self._safe_log("info", "[ARCInventory] on_load")

    def on_enable(self) -> None:
        self.inventory_manager = InventoryManager(self)
        self._safe_log("info", "[ARCInventory] 已启用，可供其它插件通过 api_* 操作玩家背包。")

    def on_disable(self) -> None:
        self._safe_log("info", "[ARCInventory] on_disable")

    def _mgr(self) -> Optional[InventoryManager]:
        return self.inventory_manager

    # ---------- 对外 API ----------

    def api_get_inventory_items(self, player: Player) -> List[Dict[str, Any]]:
        """
        列出玩家背包有效物品。
        每项含 type / name / count / data / enchants / lore / slot_index，可能含 nbt_b64。
        """
        mgr = self._mgr()
        if mgr is None or player is None:
            return []
        return mgr.get_inventory_items(player)

    def api_has_item(self, player: Player, item_info: Dict[str, Any]) -> bool:
        """是否拥有与 item_info 匹配（类型/数量/data/附魔/Lore/NBT）的物品。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return False
        return bool(mgr.has_item(player, item_info))

    def api_remove_item(self, player: Player, item_info: Dict[str, Any]) -> bool:
        """按 item_info 从背包移除匹配物品；不足则失败且不改动。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return False
        return bool(mgr.remove_item(player, item_info))

    def api_give_item(self, player: Player, item_info: Dict[str, Any]) -> bool:
        """发放物品；足额成功返回 True，否则 False（可能已部分入包）。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return False
        return bool(mgr.give_item(player, item_info))

    def api_give_item_count(self, player: Player, item_info: Dict[str, Any]) -> int:
        """尝试发放，返回实际入包数量（背包满时可能小于请求量）。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return 0
        try:
            return int(mgr.give_item_count(player, item_info) or 0)
        except Exception:
            return 0

    def api_get_inventory_manager(self) -> Optional[InventoryManager]:
        """高级用法：直接拿到 InventoryManager 实例（与按钮商店原先用法一致）。"""
        return self._mgr()
