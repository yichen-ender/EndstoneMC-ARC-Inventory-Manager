# -*- coding: utf-8 -*-
"""ARC 保险箱 UI - 表单界面与存取流程。

相对 SLE 原版保险箱的改动：
1. 附魔读取使用 ARC InventoryManager.get_inventory_items（get_enchant_level，避免 meta.enchants unhashable）
2. 存入用 ARC InventoryManager.remove_item 精确匹配扣除（不再用 clear 命令，避免误扣同类型物品）
3. 取出用 ARC InventoryManager.give_item_count 发放（处理背包满/64 拆分），并补还原自定义显示名
4. 经济接入 ARC Core（plugin.get_money / change_money，货币 ARC币）
5. 保险箱存储格式保留 SLE 旧格式（type/amount/enchantments/display_name/container_items/data）
6. 公会仓库（v1.2.0）：同公会成员共享仓库；按职级默认+逐人覆盖的存取权限；
   每保险箱可设贡献点限制（普通成员个人贡献点不足则禁存取，管理员无视）；仓库管理权限由会长授予管理员。

所有个人/公会存取流程共用 scope（("p", xuid) / ("g", guild_id)），
复合操作（入仓+背包扣除+回滚、出仓+发放+回退）在 UI 层用 self.safe.lock 包裹，防止并发刷物品。
"""

import json as _json

from endstone import ColorFormat, Player
from endstone.form import ActionForm, ModalForm, Slider, TextInput, MessageForm
from endstone.inventory import ItemStack

from endstone_arc_inventory_manager.inventory import InventoryManager, _normalize_enchant_id
from endstone_arc_inventory_manager.safe import (
    SafeManager, MAX_SAFES, SAFE_TYPES, _item_display_name,
    MODE_BOTH, MODE_STORE_ONLY, MODE_WITHDRAW_ONLY, MODE_NONE,
    permission_mode_label, mode_from_override,
)
from endstone_arc_inventory_manager.item_cn import _ITEM_CN


C = ColorFormat
PFX = f"{C.GOLD}[ARC-IM]{C.RESET} "
MONEY_NAME = "ARC币"
CONTRIB_NAME = "公会贡献点"

_ENCHANT_CN = {
    "minecraft:sharpness": "锋利", "minecraft:smite": "亡灵杀手",
    "minecraft:bane_of_arthropods": "节肢杀手", "minecraft:protection": "保护",
    "minecraft:fire_protection": "火焰保护", "minecraft:blast_protection": "爆炸保护",
    "minecraft:projectile_protection": "弹射物保护", "minecraft:feather_falling": "摔落保护",
    "minecraft:respiration": "水下呼吸", "minecraft:aqua_affinity": "水下速掘",
    "minecraft:thorns": "荆棘", "minecraft:depth_strider": "深海探索者",
    "minecraft:frost_walker": "冰霜行者", "minecraft:soul_speed": "灵魂疾行",
    "minecraft:swift_sneak": "迅捷潜行", "minecraft:unbreaking": "耐久",
    "minecraft:mending": "经验修补", "minecraft:efficiency": "效率",
    "minecraft:silk_touch": "精准采集", "minecraft:fortune": "时运",
    "minecraft:luck_of_the_sea": "海之眷顾", "minecraft:lure": "饵钓",
    "minecraft:looting": "抢夺", "minecraft:fire_aspect": "火焰附加",
    "minecraft:knockback": "击退", "minecraft:punch": "冲击",
    "minecraft:power": "力量", "minecraft:flame": "火矢",
    "minecraft:infinity": "无限", "minecraft:multishot": "多重射击",
    "minecraft:quick_charge": "快速装填", "minecraft:piercing": "穿透",
    "minecraft:riptide": "激流", "minecraft:channeling": "引雷",
    "minecraft:impaling": "穿刺", "minecraft:loyalty": "忠诚",
    "minecraft:wind_burst": "风击",
}


def _item_cn_name(item_type: str) -> str:
    key = item_type.split(":")[-1]
    return _ITEM_CN.get(key, key)


def _enchant_cn(enchant_id: str) -> str:
    eid = _normalize_enchant_id(enchant_id)
    return _ENCHANT_CN.get(eid, eid)


def _send_form(player: Player, form, on_close_cb=None):
    if on_close_cb is not None:
        form.on_close = on_close_cb
    player.send_form(form)


def _fmt_enchant_text(enchants: dict) -> str:
    if not enchants:
        return ""
    names = [f"{_enchant_cn(e)} Lv.{v}" for e, v in enchants.items()]
    return f" {C.LIGHT_PURPLE}[{', '.join(names)}]{C.RESET}"


_ROLE_LABEL = {"owner": "会长", "manager": "管理员", "member": "成员"}
_MODE_LIST = [MODE_BOTH, MODE_STORE_ONLY, MODE_WITHDRAW_ONLY, MODE_NONE]


class UIManager:
    def __init__(self, plugin, safe: SafeManager, inventory: InventoryManager):
        self.plugin = plugin
        self.safe = safe
        self.inventory = inventory

    def _k(self, player: Player) -> str:
        """存储键：优先 XUID，避免改名丢数据。"""
        try:
            xuid = str(player.xuid)
            if xuid:
                return xuid
        except Exception:
            pass
        return player.name

    # ---------- scope ----------

    def _p_scope(self, player: Player) -> tuple:
        return ("p", self._k(player))

    def _g_scope(self, guild_id) -> tuple:
        return ("g", str(guild_id))

    # ---------- arc_core 桥 ----------

    def _core(self):
        try:
            return self.plugin.server.plugin_manager.get_plugin("arc_core")
        except Exception:
            return None

    def _guild_info(self, player: Player) -> dict:
        core = self._core()
        if core is None or not hasattr(core, "api_get_player_guild_info"):
            return {}
        try:
            return core.api_get_player_guild_info(player.name) or {}
        except Exception:
            return {}

    def _guild_system(self):
        core = self._core()
        if core is None:
            return None
        try:
            return getattr(core, "guild_system", None)
        except Exception:
            return None

    def _member_name(self, xuid: str) -> str:
        core = self._core()
        if core is not None and hasattr(core, "get_player_name_by_xuid"):
            try:
                n = core.get_player_name_by_xuid(xuid, False)
                if n:
                    return str(n).strip()
            except Exception:
                pass
        return str(xuid)

    def _personal_contrib(self, player: Player) -> int:
        core = self._core()
        if core is not None and hasattr(core, "api_get_player_guild_contribution"):
            try:
                return int(core.api_get_player_guild_contribution(player.name) or 0)
            except Exception:
                pass
        return 0

    def _guild_total_contrib(self, guild_id) -> int:
        gs = self._guild_system()
        if gs is not None and hasattr(gs, "get_guild_total_contribution"):
            try:
                return int(gs.get_guild_total_contribution(int(guild_id)) or 0)
            except Exception:
                pass
        return 0

    def _consume_guild_contrib(self, guild_id, points: int):
        gs = self._guild_system()
        if gs is None or not hasattr(gs, "consume_guild_contribution"):
            return False, "GUILD_UNAVAILABLE", 0
        try:
            ok, err, new_total = gs.consume_guild_contribution(int(guild_id), int(points))
            return bool(ok), err, int(new_total or 0)
        except Exception:
            return False, "GUILD_DB_ERROR", 0

    def _refund_guild_contrib(self, guild_id, points: int) -> bool:
        gs = self._guild_system()
        if gs is None or not hasattr(gs, "refund_guild_contribution_pool"):
            return False
        try:
            return bool(gs.refund_guild_contribution_pool(int(guild_id), int(points)))
        except Exception:
            return False

    def _list_guild_members(self, guild_id) -> list:
        gs = self._guild_system()
        if gs is None or not hasattr(gs, "list_members"):
            return []
        try:
            rows = gs.list_members(int(guild_id)) or []
        except Exception:
            return []
        out = []
        for r in rows:
            xuid = str(r.get("xuid") or "")
            if not xuid:
                continue
            out.append({
                "xuid": xuid,
                "name": self._member_name(xuid),
                "role": str(r.get("role") or "member"),
                "contribution": int(r.get("contribution") or 0),
            })
        return out

    def _guild_ctx(self, player: Player):
        """返回当前玩家公会上下文 dict，无公会/arc_core 缺失时返回 None。"""
        info = self._guild_info(player)
        if not info or not info.get("guild_id"):
            return None
        return {
            "gid": int(info.get("guild_id")),
            "role": str(info.get("role") or "member"),
            "xuid": str(getattr(player, "xuid", "") or ""),
            "name": str(info.get("name") or ""),
            "total": int(info.get("total_contribution") or 0),
            "owner_xuid": str(info.get("owner_xuid") or ""),
        }

    def _can_manage_safe(self, player: Player, gctx) -> bool:
        """是否拥有仓库敏感操作权限（改名/删除/清除插槽/设置贡献点限制）。"""
        if gctx is None:
            return False
        if gctx["role"] == "owner":
            return True
        if gctx["role"] == "manager":
            return self.safe.is_guild_manager(gctx["gid"], gctx["xuid"])
        return False

    def _guild_access(self, player: Player, guild_id, safe_index):
        """返回 (store_ok, withdraw_ok, reason)。贡献点实时刷新。"""
        gctx = self._guild_ctx(player)
        if gctx is None or int(gctx.get("gid") or 0) != int(guild_id):
            return False, False, "NOT_IN_GUILD"
        return self.safe.evaluate_access(
            self._g_scope(guild_id), safe_index, gctx["xuid"], gctx["role"],
            self._personal_contrib(player))

    def _access_msg(self, reason: str) -> str:
        return {
            "CONTRIB_LOW": f"{PFX}{C.RED}个人公会贡献点不足，无法对此保险箱存取。{C.RESET}",
            "NOT_IN_GUILD": f"{PFX}{C.RED}你当前不在该公会中。{C.RESET}",
            "SAFE_MISSING": f"{PFX}{C.RED}保险箱不存在。{C.RESET}",
        }.get(reason, f"{PFX}{C.RED}你没有权限执行此操作。{C.RESET}")

    def _detail_back(self, player: Player, scope: tuple, safe_index: int, gid=None):
        if gid is not None:
            self.send_guild_safe_detail(player, gid, safe_index)
        else:
            self.send_safe_detail(player, scope, safe_index)

    def _list_back(self, player: Player, gid=None):
        if gid is not None:
            self.send_guild_safe_menu(player)
        else:
            self.send_safe_menu(player)

    # ---------- 主菜单 ----------

    def send_main_menu(self, player: Player):
        self.send_safe_type_menu(player)

    def send_safe_type_menu(self, player: Player):
        """保险箱类型选择：个人 / 公会。"""
        form = ActionForm(title="保险箱管理", content="选择保险箱类型")
        form.add_button(f"{C.LIGHT_PURPLE}个人保险箱{C.RESET}",
                        on_click=lambda p: self.send_safe_menu(p))
        form.add_button(f"{C.AQUA}公会仓库{C.RESET}",
                        on_click=lambda p: self.send_guild_safe_menu(p))
        _send_form(player, form)

    # ---------- 个人保险箱 ----------

    def send_safe_menu(self, player: Player):
        scope = self._p_scope(player)
        safe_count = self.safe.get_safe_count(scope)
        balance = self.plugin.get_money(player.name)
        form = ActionForm(
            title="ARC 保险箱",
            content=f"保险箱: {safe_count}/{MAX_SAFES} | 余额: {balance:.2f} {MONEY_NAME}",
        )
        if self.safe.can_buy_safe(scope):
            form.add_button(f"{C.GREEN}购买保险箱{C.RESET}",
                            on_click=lambda p: self.send_buy_safe_menu(p))
        for i in range(safe_count):
            info = self.safe.get_safe_info(scope, i)
            s_type = info["type"] if info else "normal"
            s_slots = info["slots"] if info else 4
            type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
            safe_name = info["name"] if info and info["name"] else f"#{i + 1}"
            items = self.safe.get_safe_items(scope, i)
            used = sum(1 for it in items if it is not None)
            form.add_button(
                f"{C.LIGHT_PURPLE}{type_name} {safe_name}{C.RESET} ({used}/{s_slots})",
                on_click=lambda p, idx=i: self.send_safe_detail(p, scope, idx))
        form.add_button(f"{C.GRAY}返回保险箱类型{C.RESET}", on_click=lambda p: self.send_safe_type_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_type_menu(p))

    def send_buy_safe_menu(self, player: Player):
        balance = self.plugin.get_money(player.name)
        form = ActionForm(
            title="购买保险箱",
            content=f"余额: {balance:.2f} {MONEY_NAME}\n选择保险箱类型:",
        )
        for stype, cfg in SAFE_TYPES.items():
            form.add_button(
                f"{cfg['name']} - {cfg['slots']}格 - {cfg['price']} {MONEY_NAME}",
                on_click=lambda p, t=stype: self._buy_safe(p, t))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_menu(p))

    def _buy_safe(self, player: Player, safe_type: str = "normal"):
        scope = self._p_scope(player)
        price = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"]).get("price", 5000)
        cfg = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"])
        is_op = bool(getattr(player, "is_op", False))
        if not is_op:
            if not self.plugin.change_money(player.name, -price):
                player.send_message(f"{PFX}{C.RED}{MONEY_NAME}不足！需要 {price}{C.RESET}")
                return
        if self.safe.buy_safe(scope, safe_type):
            count = self.safe.get_safe_count(scope)
            if is_op:
                player.send_message(f"{PFX}{C.GREEN}管理员免费购买 {cfg['name']} 成功！({count}/{MAX_SAFES}){C.RESET}")
            else:
                player.send_message(f"{PFX}{C.GREEN}购买 {cfg['name']} 成功！({count}/{MAX_SAFES}){C.RESET}")
        else:
            if not is_op:
                self.plugin.change_money(player.name, price)
            player.send_message(f"{PFX}{C.RED}已达最大数量{C.RESET}")
        self.send_safe_menu(player)

    def send_safe_detail(self, player: Player, scope: tuple, safe_index: int):
        items = self.safe.get_safe_items(scope, safe_index)
        info = self.safe.get_safe_info(scope, safe_index)
        slots = info["slots"] if info else 4
        s_type = info["type"] if info else "normal"
        type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
        safe_name = info["name"] if info and info["name"] else f"#{safe_index + 1}"
        refund = self.safe.get_safe_refund(s_type)
        form = ActionForm(
            title=f"{type_name} {safe_name}",
            content=f"{type_name} - {slots}格 | 删除返还: {refund} {MONEY_NAME}",
        )
        for i in range(slots):
            item = items[i] if i < len(items) else None
            if item is not None:
                display = self.safe.format_slot_display(item)
                form.add_button(f"槽位{i + 1}: {C.WHITE}{display}{C.RESET}",
                                on_click=lambda p, s=i: self.send_safe_item_action(p, scope, safe_index, s))
            else:
                form.add_button(f"槽位{i + 1}: {C.GRAY}空{C.RESET}",
                                on_click=lambda p, s=i: self.send_safe_item_action(p, scope, safe_index, s))
        form.add_button(f"{C.GREEN}存入物品{C.RESET}",
                        on_click=lambda p: self.send_deposit_select(p, scope, safe_index))
        form.add_button(f"{C.AQUA}更改保险箱名字{C.RESET}",
                        on_click=lambda p: self.send_rename_safe(p, scope, safe_index))
        form.add_button(f"{C.YELLOW}清除指定插槽{C.RESET}",
                        on_click=lambda p: self.send_clear_slot_select(p, scope, safe_index))
        form.add_button(f"{C.RED}删除此保险箱 (返还{refund} {MONEY_NAME}){C.RESET}",
                        on_click=lambda p: self.send_delete_safe_confirm(p, scope, safe_index))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_menu(p))

    def send_delete_safe_confirm(self, player: Player, scope: tuple, safe_index: int):
        info = self.safe.get_safe_info(scope, safe_index)
        s_type = info["type"] if info else "normal"
        type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
        refund = self.safe.get_safe_refund(s_type)
        form = MessageForm(
            title="确认删除",
            content=f"确定要删除 {type_name} #{safe_index + 1} 吗？\n\n"
                    f"保险箱内所有物品将被永久删除！\n返还 {refund} {MONEY_NAME} (60%)",
            button1=f"{C.RED}确认删除{C.RESET}",
            button2=f"{C.GRAY}取消{C.RESET}",
            on_submit=lambda p, choice: self._handle_delete_safe(p, choice, scope, safe_index),
            on_close=lambda p: self.send_safe_detail(p, scope, safe_index),
        )
        player.send_form(form)

    def _handle_delete_safe(self, player: Player, choice: int, scope: tuple, safe_index: int):
        if choice != 0:
            self.send_safe_detail(player, scope, safe_index)
            return
        is_op = bool(getattr(player, "is_op", False))
        safe_type = self.safe.delete_safe(scope, safe_index)
        if safe_type is not None:
            if is_op:
                player.send_message(f"{PFX}{C.YELLOW}保险箱已删除（管理员，无退款）。{C.RESET}")
            else:
                refund = self.safe.get_safe_refund(safe_type)
                self.plugin.change_money(player.name, refund)
                player.send_message(f"{PFX}{C.YELLOW}保险箱已删除，返还 {refund} {MONEY_NAME}。{C.RESET}")
        self.send_safe_menu(player)

    def send_rename_safe(self, player: Player, scope: tuple, safe_index: int, gid=None):
        info = self.safe.get_safe_info(scope, safe_index)
        current = info["name"] if info and info["name"] else ""
        form = ModalForm(
            title="更改保险箱名字",
            controls=[TextInput(label="新名字", placeholder="输入保险箱名称", default_value=current)],
            on_submit=lambda p, data: self._handle_rename_safe(p, data, scope, safe_index, gid),
            on_close=lambda p: self._detail_back(p, scope, safe_index, gid),
        )
        player.send_form(form)

    def _handle_rename_safe(self, player: Player, data: str, scope: tuple, safe_index: int, gid=None):
        try:
            new_name = str(_json.loads(data)[0]).strip()[:20]
            if self.safe.rename_safe(scope, safe_index, new_name):
                player.send_message(f"{PFX}{C.GREEN}保险箱已改名: {new_name}{C.RESET}")
        except Exception:
            pass
        self._detail_back(player, scope, safe_index, gid)

    def send_clear_slot_select(self, player: Player, scope: tuple, safe_index: int, gid=None):
        items = self.safe.get_safe_items(scope, safe_index)
        form = ActionForm(title="清除指定插槽", content="选择要清除的插槽")
        for i, item in enumerate(items):
            if item is not None:
                display = self.safe.format_slot_display(item)
                form.add_button(f"槽位{i + 1}: {C.WHITE}{display}{C.RESET}",
                                on_click=lambda p, s=i: self.send_clear_slot_confirm(p, scope, safe_index, s, gid))
            else:
                form.add_button(f"槽位{i + 1}: {C.GRAY}空{C.RESET}")
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self._detail_back(p, scope, safe_index, gid))
        _send_form(player, form, on_close_cb=lambda p: self._detail_back(p, scope, safe_index, gid))

    def send_clear_slot_confirm(self, player: Player, scope: tuple, safe_index: int, slot: int, gid=None):
        form = MessageForm(
            title="确认清除",
            content=f"是否确定清除当前插槽的物品？\n\n{C.RED}此操作不可恢复！{C.RESET}",
            button1=f"{C.RED}确认清除{C.RESET}",
            button2=f"{C.GRAY}取消{C.RESET}",
            on_submit=lambda p, choice: self._handle_clear_slot(p, choice, scope, safe_index, slot, gid),
            on_close=lambda p: self.send_clear_slot_select(p, scope, safe_index, gid),
        )
        player.send_form(form)

    def _handle_clear_slot(self, player: Player, choice: int, scope: tuple, safe_index: int, slot: int, gid=None):
        if choice == 0:
            if self.safe.clear_safe_slot(scope, safe_index, slot):
                player.send_message(f"{PFX}{C.YELLOW}插槽 {slot + 1} 已清除。{C.RESET}")
        self._detail_back(player, scope, safe_index, gid)

    # ---------- 取出（个人与公会共用，gid 非空时为公会） ----------

    def send_safe_item_action(self, player: Player, scope: tuple, safe_index: int, slot: int, gid=None):
        items = self.safe.get_safe_items(scope, safe_index)
        item = items[slot] if slot < len(items) else None
        if item is None:
            if gid is not None:
                store_ok, _, reason = self._guild_access(player, gid, safe_index)
                if not store_ok:
                    player.send_message(self._access_msg(reason))
                    self._detail_back(player, scope, safe_index, gid)
                    return
            self.send_deposit_select(player, scope, safe_index, gid)
            return
        if gid is not None:
            _, withdraw_ok, reason = self._guild_access(player, gid, safe_index)
            if not withdraw_ok:
                player.send_message(self._access_msg(reason))
                self._detail_back(player, scope, safe_index, gid)
                return
        display = self.safe.format_slot_display(item)
        form = ActionForm(title=f"槽位 {slot + 1}", content=f"{C.WHITE}{display}{C.RESET}")
        form.add_button(f"{C.YELLOW}取出全部 ({item['amount']}){C.RESET}",
                        on_click=lambda p: self._do_withdraw(p, scope, safe_index, slot, item["amount"], gid))
        half = max(1, item["amount"] // 2)
        form.add_button(f"{C.YELLOW}取出一半 ({half}){C.RESET}",
                        on_click=lambda p: self._do_withdraw(p, scope, safe_index, slot, half, gid))
        form.add_button(f"{C.YELLOW}选取数量...{C.RESET}",
                        on_click=lambda p: self.send_withdraw_slider(p, scope, safe_index, slot, item["amount"], gid))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self._detail_back(p, scope, safe_index, gid))
        _send_form(player, form, on_close_cb=lambda p: self._detail_back(p, scope, safe_index, gid))

    def send_withdraw_slider(self, player: Player, scope: tuple, safe_index: int, slot: int,
                             max_amount: int, gid=None):
        form = ModalForm(
            title="取出数量",
            controls=[Slider(label="数量", min=1, max=max_amount, step=1, default_value=max_amount)],
            on_submit=lambda p, data: self._handle_withdraw_slider(p, data, scope, safe_index, slot, gid),
            on_close=lambda p: self.send_safe_item_action(p, scope, safe_index, slot, gid),
        )
        player.send_form(form)

    def _handle_withdraw_slider(self, player: Player, data: str, scope: tuple,
                                safe_index: int, slot: int, gid=None):
        try:
            amount = int(_json.loads(data)[0])
            if amount > 0:
                self._do_withdraw(player, scope, safe_index, slot, amount, gid)
        except Exception:
            pass

    def _do_withdraw(self, player: Player, scope: tuple, safe_index: int, slot: int,
                     amount: int, gid=None):
        with self.safe.lock:
            if gid is not None:
                _, withdraw_ok, reason = self._guild_access(player, gid, safe_index)
                if not withdraw_ok:
                    player.send_message(self._access_msg(reason))
                    self._detail_back(player, scope, safe_index, gid)
                    return
            item_data = self.safe.withdraw_item(scope, safe_index, slot, amount)
            if item_data is None:
                player.send_message(f"{PFX}{C.RED}取出失败{C.RESET}")
                self._detail_back(player, scope, safe_index, gid)
                return
            item_type = item_data["type"]
            item_amount = item_data["amount"]
            ench_list = item_data.get("enchantments", [])
            display_name = item_data.get("display_name", "")
            data_val = item_data.get("data", 0)

            if item_amount > 255:
                self.safe.deposit_item(scope, safe_index, item_type, item_amount,
                                       ench_list, display_name, data=data_val)
                player.send_message(f"{PFX}{C.YELLOW}取出数量过大，应<=255{C.RESET}")
                self._detail_back(player, scope, safe_index, gid)
                return

            potion_types = ("minecraft:potion", "minecraft:lingering_potion", "minecraft:splash_potion")
            if (item_type in potion_types or item_type == "minecraft:ominous_bottle"
                    or (item_type == "minecraft:arrow" and data_val > 0)) and data_val:
                if not self._withdraw_data_item(player, scope, safe_index, item_data, item_type, item_amount,
                                                ench_list, display_name, data_val, potion_types):
                    try:
                        short_type = item_type.split(":")[-1]
                        ok = self.plugin.server.dispatch_command(
                            self.plugin.server.command_sender,
                            f"give {player.name} {short_type} {item_amount} {data_val}")
                        if ok:
                            show_name = display_name if display_name else _item_display_name(item_data)
                            player.send_message(f"{PFX}{C.GREEN}已取出 {show_name} x{item_amount}{C.RESET}")
                        else:
                            self.safe.deposit_item(scope, safe_index, item_type, item_amount,
                                                   ench_list, display_name, data=data_val)
                            player.send_message(f"{PFX}{C.RED}取出失败（give命令未执行）{C.RESET}")
                    except Exception as e:
                        self.safe.deposit_item(scope, safe_index, item_type, item_amount,
                                               ench_list, display_name, data=data_val)
                        player.send_message(f"{PFX}{C.RED}取出药水失败: {e}{C.RESET}")
                self._detail_back(player, scope, safe_index, gid)
                return

            arc_info = {
                "type": item_type,
                "count": item_amount,
                "data": data_val,
                "enchants": {e["id"]: e["level"] for e in ench_list},
                "lore": [],
                "nbt_b64": None,
            }
            try:
                given = self.inventory.give_item_count(player, arc_info)
                leftover = item_amount - given
                if leftover > 0:
                    self.safe.deposit_item(scope, safe_index, item_type, leftover,
                                           ench_list, display_name, data=data_val)
                    player.send_message(f"{PFX}{C.YELLOW}背包空间不足，部分退回保险箱{C.RESET}")
                else:
                    if display_name:
                        self._apply_display_name(player, item_type, data_val, display_name)
                    show_name = display_name if display_name else _item_cn_name(item_type)
                    player.send_message(f"{PFX}{C.GREEN}已取出 {show_name} x{given}{C.RESET}")
            except Exception as e:
                self.safe.deposit_item(scope, safe_index, item_type, item_amount,
                                       ench_list, display_name, data=data_val)
                player.send_message(f"{PFX}{C.RED}取出失败: {e}{C.RESET}")
            self._detail_back(player, scope, safe_index, gid)

    def _withdraw_data_item(self, player, scope, safe_index, item_data, item_type, item_amount,
                            ench_list, display_name, data_val, potion_types) -> bool:
        """带 data 值物品（药水/药箭/不详之瓶）分块放入背包空槽。成功 True，失败 False（走 /give）。"""
        try:
            stack = ItemStack(item_type, 1)
            stack.data = data_val
            applied = False
            try:
                applied = int(stack.data) == int(data_val)
            except Exception:
                applied = False
            if not applied:
                return False
            inv = player.inventory
            max_chunk = 1 if item_type in potion_types else 64
            remaining = item_amount
            placed = 0
            for slot_i in range(inv.size):
                if remaining <= 0:
                    break
                if inv.get_item(slot_i) is not None:
                    continue
                chunk = min(remaining, max_chunk)
                chunk_stack = ItemStack(item_type, chunk)
                chunk_stack.data = data_val
                inv.set_item(slot_i, chunk_stack)
                remaining -= chunk
                placed += chunk
            if remaining > 0:
                self.safe.deposit_item(scope, safe_index, item_type, remaining,
                                       ench_list, display_name, data=data_val)
                player.send_message(f"{PFX}{C.YELLOW}背包空间不足，{remaining}个已退回保险箱{C.RESET}")
            show_name = display_name if display_name else _item_display_name(item_data)
            player.send_message(f"{PFX}{C.GREEN}已取出 {show_name} x{placed}{C.RESET}")
            return True
        except Exception:
            return False

    def _apply_display_name(self, player: Player, item_type: str, data_val: int, display_name: str):
        """给刚发放的物品补上自定义显示名（ARC give 不直接支持 display_name）。"""
        try:
            inv = player.inventory
            for i in range(inv.size - 1, -1, -1):
                st = inv.get_item(i)
                if st is None or st.type is None:
                    continue
                if st.type.id != item_type:
                    continue
                if data_val and getattr(st, "data", 0) != data_val:
                    continue
                meta = st.item_meta
                if meta is None:
                    try:
                        meta = player.server.item_factory.get_item_meta(item_type)
                    except Exception:
                        return
                if meta is not None:
                    meta.display_name = display_name
                    st.set_item_meta(meta)
                    inv.set_item(i, st)
                return
        except Exception:
            pass

    # ---------- 存入（个人与公会共用，gid 非空时为公会） ----------

    def send_deposit_select(self, player: Player, scope: tuple, safe_index: int, gid=None):
        if gid is not None:
            store_ok, _, reason = self._guild_access(player, gid, safe_index)
            if not store_ok:
                player.send_message(self._access_msg(reason))
                self._detail_back(player, scope, safe_index, gid)
                return
        inv_items = self.inventory.get_inventory_items(player)
        if not inv_items:
            player.send_message(f"{PFX}{C.RED}背包没有可存入的物品{C.RESET}")
            self._detail_back(player, scope, safe_index, gid)
            return
        groups: dict[tuple, dict] = {}
        for it in inv_items:
            t = it.get("type", "")
            if "shulker_box" in t or "bundle" in t:
                continue
            ench = it.get("enchants") or {}
            data_val = it.get("data", 0)
            key = (t, data_val, tuple(sorted(ench.items())))
            if key not in groups:
                groups[key] = {"type": t, "data": data_val, "enchants": dict(ench),
                               "display_name": it.get("name", ""), "count": 0}
            groups[key]["count"] += it.get("count", 0)
        form = ActionForm(title=f"存入保险箱 #{safe_index + 1}", content="选择要存入的物品")
        for g in groups.values():
            label = self._format_deposit_label(g)
            form.add_button(label, on_click=lambda p, gg=g: self.send_deposit_amount(p, scope, safe_index, gg, gid))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self._detail_back(p, scope, safe_index, gid))
        _send_form(player, form, on_close_cb=lambda p: self._detail_back(p, scope, safe_index, gid))

    def _format_deposit_label(self, g: dict) -> str:
        name = g.get("display_name") or _item_cn_name(g.get("type", ""))
        if g.get("data"):
            name += f" (ID:{g['data']})"
        return f"{C.WHITE}{name}{C.RESET} x{g.get('count', 0)}{_fmt_enchant_text(g.get('enchants'))}"

    def send_deposit_amount(self, player: Player, scope: tuple, safe_index: int, group: dict, gid=None):
        max_amount = group.get("count", 1)
        form = ModalForm(
            title="存入数量",
            controls=[Slider(label="存入数量", min=1, max=max_amount, step=1, default_value=max_amount)],
            on_submit=lambda p, data: self._handle_deposit(p, data, scope, safe_index, group, gid),
            on_close=lambda p: self._detail_back(p, scope, safe_index, gid),
        )
        player.send_form(form)

    def _handle_deposit(self, player: Player, data: str, scope: tuple, safe_index: int,
                        group: dict, gid=None):
        try:
            amount = int(_json.loads(data)[0])
        except Exception:
            return
        if amount <= 0:
            return
        with self.safe.lock:
            if gid is not None:
                store_ok, _, reason = self._guild_access(player, gid, safe_index)
                if not store_ok:
                    player.send_message(self._access_msg(reason))
                    self._detail_back(player, scope, safe_index, gid)
                    return
            ench_list = [{"id": e, "level": lv} for e, lv in (group.get("enchants") or {}).items()]
            stored, actual_safe, _slot = self.safe.deposit_item(
                scope, safe_index, group["type"], amount, ench_list,
                group.get("display_name", ""), data=group.get("data", 0))
            if stored <= 0:
                player.send_message(f"{PFX}{C.RED}保险箱已满！{C.RESET}")
                self._detail_back(player, scope, safe_index, gid)
                return
            arc_info = {
                "type": group["type"],
                "count": stored,
                "data": group.get("data", 0),
                "enchants": group.get("enchants") or {},
                "lore": [],
                "nbt_b64": None,
            }
            if self.inventory.remove_item(player, arc_info):
                player.send_message(f"{PFX}{C.GREEN}已存入 {_item_cn_name(group['type'])} "
                                    f"x{stored} 到保险箱 #{actual_safe + 1}{C.RESET}")
            else:
                self.safe.withdraw_item(scope, actual_safe, _slot, stored)
                player.send_message(f"{PFX}{C.RED}存入失败，已退回保险箱记录{C.RESET}")
            self._detail_back(player, scope, safe_index, gid)

    # ---------- 公会仓库 ----------

    def send_guild_safe_menu(self, player: Player):
        gctx = self._guild_ctx(player)
        if gctx is None:
            player.send_message(f"{PFX}{C.RED}你当前不在任何公会中。{C.RESET}")
            form = ActionForm(title="公会仓库", content=f"{C.GRAY}未加入公会，无法使用公会仓库。{C.RESET}")
            form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_type_menu(p))
            _send_form(player, form, on_close_cb=lambda p: self.send_safe_type_menu(p))
            return
        gid = gctx["gid"]
        scope = self._g_scope(gid)
        count = self.safe.get_safe_count(scope)
        total = self._guild_total_contrib(gid)
        form = ActionForm(
            title="公会仓库",
            content=f"公会: {gctx['name']} | 职级: {_ROLE_LABEL.get(gctx['role'], gctx['role'])}\n"
                    f"保险箱: {count}/{MAX_SAFES} | 公共贡献点: {total}",
        )
        if gctx["role"] == "owner":
            if self.safe.can_buy_safe(scope):
                form.add_button(f"{C.GREEN}购买保险箱 (消耗公共贡献点){C.RESET}",
                                on_click=lambda p: self.send_guild_buy_safe(p, gid))
            form.add_button(f"{C.LIGHT_PURPLE}仓库权限管理{C.RESET}",
                            on_click=lambda p: self.send_guild_manage_menu(p, gid))
        for i in range(count):
            info = self.safe.get_safe_info(scope, i)
            s_type = info["type"] if info else "normal"
            s_slots = info["slots"] if info else 4
            type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
            safe_name = info["name"] if info and info["name"] else f"#{i + 1}"
            items = self.safe.get_safe_items(scope, i)
            used = sum(1 for it in items if it is not None)
            form.add_button(
                f"{C.AQUA}{type_name} {safe_name}{C.RESET} ({used}/{s_slots})",
                on_click=lambda p, idx=i: self.send_guild_safe_detail(p, gid, idx))
        form.add_button(f"{C.GRAY}返回保险箱类型{C.RESET}", on_click=lambda p: self.send_safe_type_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_type_menu(p))

    def send_guild_safe_detail(self, player: Player, gid, safe_index: int):
        gctx = self._guild_ctx(player)
        if gctx is None or int(gctx.get("gid") or 0) != int(gid):
            player.send_message(f"{PFX}{C.RED}你当前不在该公会中。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        scope = self._g_scope(gid)
        items = self.safe.get_safe_items(scope, safe_index)
        info = self.safe.get_safe_info(scope, safe_index)
        if info is None:
            player.send_message(f"{PFX}{C.RED}保险箱不存在。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        slots = info["slots"]
        s_type = info["type"]
        type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
        safe_name = info["name"] if info.get("name") else f"#{safe_index + 1}"
        limit = self.safe.get_contribution_limit(scope, safe_index)
        store_ok, withdraw_ok, reason = self._guild_access(player, gid, safe_index)
        can_manage = self._can_manage_safe(player, gctx)
        limit_txt = "无" if limit <= 0 else str(limit)
        form = ActionForm(
            title=f"{type_name} {safe_name}",
            content=f"{type_name} - {slots}格 | 贡献点限制: {limit_txt}\n"
                    f"存取权限: {'可存可取' if store_ok and withdraw_ok else ('仅存' if store_ok else ('仅取' if withdraw_ok else '禁止存取'))}",
        )
        for i in range(slots):
            item = items[i] if i < len(items) else None
            if item is not None:
                display = self.safe.format_slot_display(item)
                if withdraw_ok:
                    form.add_button(f"槽位{i + 1}: {C.WHITE}{display}{C.RESET}",
                                    on_click=lambda p, s=i: self.send_safe_item_action(p, scope, safe_index, s, gid))
                else:
                    form.add_button(f"槽位{i + 1}: {C.WHITE}{display}{C.RESET}",
                                    on_click=lambda p: player.send_message(
                                        f"{PFX}{C.RED}你没有取出权限。{C.RESET}"))
            else:
                if store_ok:
                    form.add_button(f"槽位{i + 1}: {C.GRAY}空{C.RESET}",
                                    on_click=lambda p, s=i: self.send_safe_item_action(p, scope, safe_index, s, gid))
                else:
                    form.add_button(f"槽位{i + 1}: {C.GRAY}空{C.RESET}",
                                    on_click=lambda p: player.send_message(
                                        f"{PFX}{C.RED}你没有存入权限。{C.RESET}"))
        if store_ok:
            form.add_button(f"{C.GREEN}存入物品{C.RESET}",
                            on_click=lambda p: self.send_deposit_select(p, scope, safe_index, gid))
        if can_manage:
            form.add_button(f"{C.GOLD}设置贡献点限制{C.RESET}",
                            on_click=lambda p: self.send_guild_contribution_limit(p, gid, safe_index))
            form.add_button(f"{C.AQUA}更改保险箱名字{C.RESET}",
                            on_click=lambda p: self.send_rename_safe(p, scope, safe_index, gid))
            form.add_button(f"{C.YELLOW}清除指定插槽{C.RESET}",
                            on_click=lambda p: self.send_clear_slot_select(p, scope, safe_index, gid))
            form.add_button(f"{C.RED}删除此保险箱 (返还{self.safe.get_safe_refund(s_type)} {CONTRIB_NAME}){C.RESET}",
                            on_click=lambda p: self.send_guild_delete_confirm(p, gid, safe_index))
        if gctx["role"] == "owner":
            form.add_button(f"{C.LIGHT_PURPLE}管理成员存取权限{C.RESET}",
                            on_click=lambda p: self.send_guild_safe_perms(p, gid, safe_index))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_safe_menu(p))

    # ---------- 购买（仅会长，扣公会公共贡献点） ----------

    def send_guild_buy_safe(self, player: Player, gid):
        gctx = self._guild_ctx(player)
        if gctx is None or gctx["role"] != "owner" or int(gctx["gid"]) != int(gid):
            player.send_message(f"{PFX}{C.RED}只有会长可以购买公会保险箱。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        total = self._guild_total_contrib(gid)
        form = ActionForm(
            title="购买公会保险箱",
            content=f"公会公共贡献点: {total}\n选择保险箱类型（购买消耗公共贡献点）:",
        )
        for stype, cfg in SAFE_TYPES.items():
            form.add_button(
                f"{cfg['name']} - {cfg['slots']}格 - {cfg['price']} {CONTRIB_NAME}",
                on_click=lambda p, t=stype: self._buy_guild_safe(p, gid, t))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_safe_menu(p))

    def _buy_guild_safe(self, player: Player, gid, safe_type: str):
        gctx = self._guild_ctx(player)
        if gctx is None or gctx["role"] != "owner" or int(gctx["gid"]) != int(gid):
            player.send_message(f"{PFX}{C.RED}只有会长可以购买公会保险箱。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        scope = self._g_scope(gid)
        if not self.safe.can_buy_safe(scope):
            player.send_message(f"{PFX}{C.RED}公会仓库已达最大数量 ({MAX_SAFES})。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        cfg = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"])
        price = cfg["price"]
        ok, err, new_total = self._consume_guild_contrib(gid, price)
        if not ok:
            player.send_message(f"{PFX}{C.RED}公会公共贡献点不足！需要 {price}{C.RESET}")
            self.send_guild_buy_safe(player, gid)
            return
        if self.safe.buy_safe(scope, safe_type):
            count = self.safe.get_safe_count(scope)
            player.send_message(f"{PFX}{C.GREEN}购买公会 {cfg['name']} 成功！({count}/{MAX_SAFES}) "
                                f"剩余贡献点 {new_total}{C.RESET}")
        else:
            self._refund_guild_contrib(gid, price)
            player.send_message(f"{PFX}{C.RED}公会仓库已达最大数量，已退还贡献点。{C.RESET}")
        self.send_guild_safe_menu(player)

    def send_guild_delete_confirm(self, player: Player, gid, safe_index: int):
        gctx = self._guild_ctx(player)
        if not self._can_manage_safe(player, gctx):
            player.send_message(f"{PFX}{C.RED}你没有仓库管理权限。{C.RESET}")
            self.send_guild_safe_detail(player, gid, safe_index)
            return
        info = self.safe.get_safe_info(self._g_scope(gid), safe_index)
        s_type = info["type"] if info else "normal"
        type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
        refund = self.safe.get_safe_refund(s_type)
        form = MessageForm(
            title="确认删除公会保险箱",
            content=f"确定要删除 {type_name} #{safe_index + 1} 吗？\n\n"
                    f"保险箱内所有物品将被永久删除！\n返还 {refund} {CONTRIB_NAME} (60%) 到公会公共贡献点",
            button1=f"{C.RED}确认删除{C.RESET}",
            button2=f"{C.GRAY}取消{C.RESET}",
            on_submit=lambda p, choice: self._handle_delete_guild_safe(p, choice, gid, safe_index),
            on_close=lambda p: self.send_guild_safe_detail(p, gid, safe_index),
        )
        player.send_form(form)

    def _handle_delete_guild_safe(self, player: Player, choice: int, gid, safe_index: int):
        if choice != 0:
            self.send_guild_safe_detail(player, gid, safe_index)
            return
        scope = self._g_scope(gid)
        safe_type = self.safe.delete_safe(scope, safe_index)
        if safe_type is not None:
            refund = self.safe.get_safe_refund(safe_type)
            self._refund_guild_contrib(gid, refund)
            player.send_message(f"{PFX}{C.YELLOW}公会保险箱已删除，返还 {refund} {CONTRIB_NAME} 到公会公共贡献点。{C.RESET}")
        self.send_guild_safe_menu(player)

    # ---------- 仓库权限管理（仅会长） ----------

    def send_guild_manage_menu(self, player: Player, gid):
        gctx = self._guild_ctx(player)
        if gctx is None or gctx["role"] != "owner" or int(gctx["gid"]) != int(gid):
            player.send_message(f"{PFX}{C.RED}只有会长可以管理仓库权限。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        form = ActionForm(title="仓库权限管理", content="选择要管理的项目")
        form.add_button(f"{C.LIGHT_PURPLE}成员存取权限{C.RESET}",
                        on_click=lambda p: self.send_guild_perms_safes(p, gid))
        form.add_button(f"{C.GOLD}仓库管理授权{C.RESET}",
                        on_click=lambda p: self.send_guild_manager_auth(p, gid))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_safe_menu(p))

    def send_guild_perms_safes(self, player: Player, gid):
        scope = self._g_scope(gid)
        count = self.safe.get_safe_count(scope)
        form = ActionForm(title="成员存取权限", content="选择要管理的保险箱")
        for i in range(count):
            info = self.safe.get_safe_info(scope, i)
            s_type = info["type"] if info else "normal"
            type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
            safe_name = info["name"] if info and info["name"] else f"#{i + 1}"
            form.add_button(f"{C.AQUA}{type_name} {safe_name}{C.RESET}",
                            on_click=lambda p, idx=i: self.send_guild_safe_perms(p, gid, idx))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_manage_menu(p, gid))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_manage_menu(p, gid))

    def send_guild_safe_perms(self, player: Player, gid, safe_index: int):
        scope = self._g_scope(gid)
        limit = self.safe.get_contribution_limit(scope, safe_index)
        members = self._list_guild_members(gid)
        form = ActionForm(
            title="成员存取权限",
            content=f"保险箱 #{safe_index + 1} | 贡献点限制: {limit}\n点击成员设置其存取权限:",
        )
        form.add_button(f"{C.GOLD}设置贡献点限制 (当前 {limit}){C.RESET}",
                        on_click=lambda p: self.send_guild_contribution_limit(p, gid, safe_index))
        for m in members:
            if m["role"] == "owner":
                continue
            p = self.safe.get_safe_permission(scope, safe_index, m["xuid"])
            mode = mode_from_override(p["store"], p["withdraw"]) if p else MODE_BOTH
            form.add_button(
                f"{C.WHITE}{m['name']}{C.RESET} [{_ROLE_LABEL.get(m['role'], m['role'])}] "
                f"{C.AQUA}{permission_mode_label(mode)}{C.RESET} (贡献 {m['contribution']})",
                on_click=lambda pl, mm=m: self.send_guild_member_perm(pl, gid, safe_index, mm["xuid"], mm["name"]))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_perms_safes(p, gid))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_perms_safes(p, gid))

    def send_guild_member_perm(self, player: Player, gid, safe_index: int, xuid: str, name: str):
        scope = self._g_scope(gid)
        p = self.safe.get_safe_permission(scope, safe_index, xuid)
        mode = mode_from_override(p["store"], p["withdraw"]) if p else MODE_BOTH
        form = ActionForm(
            title=f"设置 {name} 的权限",
            content=f"当前: {permission_mode_label(mode)}",
        )
        for i, m in enumerate(_MODE_LIST):
            form.add_button(permission_mode_label(m),
                            on_click=lambda pl, idx=i: self._handle_guild_member_perm(pl, idx, gid, safe_index, xuid))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_safe_perms(p, gid, safe_index))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_safe_perms(p, gid, safe_index))

    def _handle_guild_member_perm(self, player: Player, choice: int, gid, safe_index: int, xuid: str):
        if 0 <= choice < len(_MODE_LIST):
            mode = _MODE_LIST[choice]
            self.safe.set_safe_permission(self._g_scope(gid), safe_index, xuid, mode)
            player.send_message(f"{PFX}{C.GREEN}已设置该成员权限: {permission_mode_label(mode)}{C.RESET}")
        self.send_guild_safe_perms(player, gid, safe_index)

    def send_guild_contribution_limit(self, player: Player, gid, safe_index: int):
        gctx = self._guild_ctx(player)
        if not self._can_manage_safe(player, gctx):
            player.send_message(f"{PFX}{C.RED}你没有仓库管理权限。{C.RESET}")
            self.send_guild_safe_detail(player, gid, safe_index)
            return
        current = self.safe.get_contribution_limit(self._g_scope(gid), safe_index)
        form = ModalForm(
            title="设置贡献点限制",
            controls=[TextInput(label="个人贡献点下限（0=无限制）",
                                placeholder="输入数字", default_value=str(current))],
            on_submit=lambda p, data: self._handle_guild_contribution_limit(p, data, gid, safe_index),
            on_close=lambda p: self.send_guild_safe_detail(p, gid, safe_index),
        )
        player.send_form(form)

    def _handle_guild_contribution_limit(self, player: Player, data: str, gid, safe_index: int):
        try:
            value = int(str(_json.loads(data)[0]).strip())
        except Exception:
            player.send_message(f"{PFX}{C.RED}输入无效，请填写数字。{C.RESET}")
            self.send_guild_safe_detail(player, gid, safe_index)
            return
        if value < 0:
            value = 0
        self.safe.set_contribution_limit(self._g_scope(gid), safe_index, value)
        player.send_message(f"{PFX}{C.GREEN}贡献点限制已设为 {value}（0=无限制，普通成员个人贡献点低于此值将无法存取）。{C.RESET}")
        self.send_guild_safe_detail(player, gid, safe_index)

    def send_guild_manager_auth(self, player: Player, gid):
        gctx = self._guild_ctx(player)
        if gctx is None or gctx["role"] != "owner" or int(gctx["gid"]) != int(gid):
            player.send_message(f"{PFX}{C.RED}只有会长可以管理仓库授权。{C.RESET}")
            self.send_guild_safe_menu(player)
            return
        members = self._list_guild_members(gid)
        managers = [m for m in members if m["role"] == "manager"]
        granted = self.safe.get_guild_managers(gid)
        form = ActionForm(
            title="仓库管理授权",
            content="点击管理员切换其仓库管理权限（可改名/删除/清除插槽/设置贡献点限制）",
        )
        for m in managers:
            tag = f"{C.GREEN}已授权{C.RESET}" if m["xuid"] in granted else f"{C.GRAY}未授权{C.RESET}"
            form.add_button(f"{C.WHITE}{m['name']}{C.RESET} - {tag}",
                            on_click=lambda pl, x=m["xuid"]: self._toggle_guild_manager(pl, gid, x))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_guild_manage_menu(p, gid))
        _send_form(player, form, on_close_cb=lambda p: self.send_guild_manage_menu(p, gid))

    def _toggle_guild_manager(self, player: Player, gid, xuid: str):
        granted = self.safe.get_guild_managers(gid)
        new_val = xuid not in granted
        self.safe.set_guild_manager(gid, xuid, new_val)
        if new_val:
            player.send_message(f"{PFX}{C.GREEN}已授予该管理员仓库管理权限。{C.RESET}")
        else:
            player.send_message(f"{PFX}{C.YELLOW}已撤销该管理员的仓库管理权限。{C.RESET}")
        self.send_guild_manager_auth(player, gid)
