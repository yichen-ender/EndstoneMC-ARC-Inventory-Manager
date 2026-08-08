# -*- coding: utf-8 -*-
"""ARC Inventory Manager - 保险箱插件。

- 保险箱存储保留 SLE 旧格式（type/amount/enchantments/display_name/container_items/data）
- 背包交互使用 ARC InventoryManager（get_enchant_level 读附魔、remove_item 精确扣除、give_item_count 发放）
- 经济系统接入 ARC Core（server.get_plugin('arc_core')）
- 命令: /arcim 打开保险箱界面；/arcimreload 热重载数据（管理员）
  命令名均大小写不敏感（/ARCIM、/ArcIm、/ARCIMRELOAD 均可）。
"""

from endstone import ColorFormat, Player
from endstone.command import Command, CommandSender
from endstone.plugin import Plugin

from endstone_arc_inventory_manager.inventory import InventoryManager
from endstone_arc_inventory_manager.safe import SafeManager
from endstone_arc_inventory_manager.ui import UIManager


class ARCInventoryManagerPlugin(Plugin):
    api_version = "0.10"
    prefix = "ARC-IM"

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
        self._safe_log("info", "ARC Inventory Manager loading...")

    def on_enable(self) -> None:
        self.inventory = InventoryManager(self)
        self.safe = SafeManager(self.data_folder)
        self.ui = UIManager(self, self.safe, self.inventory)
        self._safe_log("info", f"ARC Inventory Manager enabled. Data: {self.data_folder}")

    def on_disable(self) -> None:
        self._safe_log("info", "ARC Inventory Manager disabled.")

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
