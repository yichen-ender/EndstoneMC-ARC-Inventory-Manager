# -*- coding: utf-8 -*-
"""弧光背包管理器：为弧光系列插件提供统一的背包读写 API，并内置保险箱功能。

Plugin id: arc_inventory
命令:
  /arcim        打开保险箱界面
  /arcimreload  热重载保险箱数据（管理员）
其他插件：server.get_plugin("arc_inventory") 后调用 api_*。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from endstone import ColorFormat, Player
from endstone.command import Command, CommandSender
from endstone.plugin import Plugin

from endstone_arc_inventory.InventoryManager import InventoryManager
from endstone_arc_inventory.safe import SafeManager
from endstone_arc_inventory.ui import UIManager


class ARCInventoryPlugin(Plugin):
    """
    Plugin id: arc_inventory
    其他插件：server.get_plugin("arc_inventory") 后调用 api_*。
    """

    api_version = "0.10"
    prefix = "ARCInventory"

    commands = {
        "arcim": {
            "description": "打开 ARC 保险箱界面",
            "usages": ["/arcim"],
        },
        "arcimreload": {
            "description": "热重载 ARC 保险箱数据（管理员）",
            "usages": ["/arcimreload"],
        },
    }

    permissions = {
        "arcim.command.use": {
            "description": "允许使用 /arcim 命令",
            "default": True,
        },
        "arcimreload.command.use": {
            "description": "允许使用 /arcimreload 命令",
            "default": True,
        },
    }

    def __init__(self):
        super().__init__()
        self.inventory_manager: Optional[InventoryManager] = None
        self.safe: Optional[SafeManager] = None
        self.ui: Optional[UIManager] = None

    def _safe_log(self, level: str, message: str) -> None:
        if hasattr(self, "logger") and self.logger is not None:
            fn = getattr(self.logger, level.lower(), None)
            if callable(fn):
                fn(message)
                return
            self.logger.info(message)
        else:
            print(f"[{level.upper()}] {message}")

    # ---------- 经济（接入 ARC Core） ----------

    def get_economy(self):
        """获取 ARC Core 插件实例（经济系统提供方）。"""
        try:
            return self.server.get_plugin("arc_core")
        except Exception:
            return None

    def get_money(self, player_name: str) -> float:
        """获取玩家余额（ARC 币）。ARC Core 未装时返回 0。"""
        core = self.get_economy()
        if core and hasattr(core, "api_get_player_money"):
            try:
                return float(core.api_get_player_money(player_name) or 0)
            except Exception as e:
                self._safe_log("warning", f"get_money failed: {e}")
        return 0.0

    def change_money(self, player_name: str, amount: float) -> bool:
        """改变玩家余额（正增负减）。返回是否成功。"""
        core = self.get_economy()
        if core and hasattr(core, "api_change_player_money"):
            try:
                return bool(core.api_change_player_money(player_name, amount))
            except Exception as e:
                self._safe_log("warning", f"change_money failed: {e}")
        return False

    def on_load(self) -> None:
        self._safe_log("info", "[ARCInventory] on_load")

    def on_enable(self) -> None:
        self.inventory_manager = InventoryManager(self)
        self.safe = SafeManager(self.data_folder)
        self.ui = UIManager(self, self.safe, self.inventory_manager)
        self._safe_log("info", "[ARCInventory] 已启用（背包 API + 保险箱）。")

    def on_disable(self) -> None:
        self._safe_log("info", "[ARCInventory] on_disable")

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if not isinstance(sender, Player):
            sender.send_message(f"{ColorFormat.RED}[ARC-IM] 此命令只能由玩家执行。{ColorFormat.RESET}")
            return True
        cmd = command.name.lower()
        if cmd == "arcimreload":
            if not sender.is_op:
                sender.send_message(f"{ColorFormat.RED}[ARC-IM] 只有管理员可以重载。{ColorFormat.RESET}")
                return True
            try:
                self.safe._load()
            except Exception as e:
                self._safe_log("error", f"Reload failed: {e}")
                sender.send_message(f"{ColorFormat.RED}[ARC-IM] 重载失败: {e}{ColorFormat.RESET}")
                return True
            self._safe_log("info", f"Data reloaded by {sender.name}")
            sender.send_message(f"{ColorFormat.GREEN}[ARC-IM] 所有数据已热重载。{ColorFormat.RESET}")
            return True
        if cmd == "arcim":
            try:
                self.ui.send_main_menu(sender)
            except Exception as e:
                import traceback
                self._safe_log("error", f"打开界面失败: {e}\n{traceback.format_exc()}")
                sender.send_message(f"{ColorFormat.RED}[ARC-IM] 打开界面失败: {e}{ColorFormat.RESET}")
        return True

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

    def api_get_safe_manager(self) -> Optional[SafeManager]:
        """拿到保险箱数据管理器（可读玩家保险箱数据）。"""
        return self.safe
