# -*- coding: utf-8 -*-
"""ARC 保险箱 UI - 表单界面与存取流程。

相比 SLE 原版保险箱的改动：
1. 附魔读取使用 ARC InventoryManager.get_inventory_items（get_enchant_level，避免 meta.enchants unhashable）
2. 存入用 ARC InventoryManager.remove_item 精确匹配扣除（不再用 clear 命令，避免误扣同类型物品）
3. 取出用 ARC InventoryManager.give_item_count 发放（处理背包满/64 拆分），并补还原自定义显示名
4. 经济接入 ARC Core（plugin.get_money / change_money，货币 ARC币）
5. 保险箱存储格式保留 SLE 旧格式（type/amount/enchantments/display_name/container_items/data）
"""

import json as _json

from endstone import ColorFormat, Player
from endstone.form import ActionForm, ModalForm, Slider, TextInput, MessageForm
from endstone.inventory import ItemStack

from endstone_arc_inventory_manager.inventory import InventoryManager, _normalize_enchant_id
from endstone_arc_inventory_manager.safe import SafeManager, MAX_SAFES, SAFE_TYPES, EXP_VAULT_PRICE, _item_display_name
from endstone_arc_inventory_manager.item_cn import _ITEM_CN


C = ColorFormat
PFX = f"{C.GOLD}[ARC-IM]{C.RESET} "
MONEY_NAME = "ARC币"

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

    # ---------- 主菜单 ----------

    def send_main_menu(self, player: Player):
        self.send_safe_type_menu(player)

    def send_safe_type_menu(self, player: Player):
        """保险箱类型选择：个人 / 公会（公会暂未开放）。"""
        form = ActionForm(title="保险箱管理", content="选择保险箱类型")
        form.add_button(f"{C.LIGHT_PURPLE}个人保险箱{C.RESET}",
                        on_click=lambda p: self.send_safe_menu(p))
        form.add_button(f"{C.AQUA}公会保险箱{C.RESET}",
                        on_click=lambda p: self.send_guild_safe_menu(p))
        _send_form(player, form)

    def send_guild_safe_menu(self, player: Player):
        """公会保险箱 - 占位，暂未开放。"""
        form = ActionForm(title="公会保险箱", content=f"{C.GRAY}公会保险箱暂未开放，敬请期待。{C.RESET}")
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_type_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_type_menu(p))

    def send_safe_menu(self, player: Player):
        k = self._k(player)
        safe_count = self.safe.get_safe_count(k)
        balance = self.plugin.get_money(player.name)
        has_exp = self.safe.has_exp_vault(k)
        exp_level = self.safe.get_exp_vault_level(k) if has_exp else 0
        form = ActionForm(
            title="ARC 保险箱",
            content=f"保险箱: {safe_count}/{MAX_SAFES} | 余额: {balance:.2f} {MONEY_NAME}",
        )
        if self.safe.can_buy_safe(k):
            form.add_button(f"{C.GREEN}购买保险箱{C.RESET}",
                            on_click=lambda p: self.send_buy_safe_menu(p))
        for i in range(safe_count):
            info = self.safe.get_safe_info(k, i)
            s_type = info["type"] if info else "normal"
            s_slots = info["slots"] if info else 4
            type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
            safe_name = info["name"] if info and info["name"] else f"#{i + 1}"
            items = self.safe.get_safe_items(k, i)
            used = sum(1 for it in items if it is not None)
            form.add_button(
                f"{C.LIGHT_PURPLE}{type_name} {safe_name}{C.RESET} ({used}/{s_slots})",
                on_click=lambda p, idx=i: self.send_safe_detail(p, idx))
        if has_exp:
            form.add_button(f"{C.AQUA}经验保管箱 (当前: {exp_level}级){C.RESET}",
                            on_click=lambda p: self.send_exp_vault_menu(p))
        else:
            form.add_button(f"{C.AQUA}购买经验保管箱 ({EXP_VAULT_PRICE} {MONEY_NAME}){C.RESET}",
                            on_click=lambda p: self._buy_exp_vault(p))
        form.add_button(f"{C.GRAY}返回保险箱类型{C.RESET}", on_click=lambda p: self.send_safe_type_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_type_menu(p))

    # ---------- 购买 / 删除 ----------

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
        price = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"]).get("price", 5000)
        cfg = SAFE_TYPES.get(safe_type, SAFE_TYPES["normal"])
        is_op = bool(getattr(player, "is_op", False))
        # 管理员 0 元免费购买
        if not is_op:
            if not self.plugin.change_money(player.name, -price):
                player.send_message(f"{PFX}{C.RED}{MONEY_NAME}不足！需要 {price}{C.RESET}")
                return
        if self.safe.buy_safe(self._k(player), safe_type):
            count = self.safe.get_safe_count(self._k(player))
            if is_op:
                player.send_message(f"{PFX}{C.GREEN}管理员免费购买 {cfg['name']} 成功！({count}/{MAX_SAFES}){C.RESET}")
            else:
                player.send_message(f"{PFX}{C.GREEN}购买 {cfg['name']} 成功！({count}/{MAX_SAFES}){C.RESET}")
        else:
            if not is_op:
                self.plugin.change_money(player.name, price)
            player.send_message(f"{PFX}{C.RED}已达最大数量{C.RESET}")
        self.send_safe_menu(player)

    def send_delete_safe_confirm(self, player: Player, safe_index: int):
        info = self.safe.get_safe_info(self._k(player), safe_index)
        s_type = info["type"] if info else "normal"
        type_name = SAFE_TYPES.get(s_type, {}).get("name", "保险箱")
        refund = self.safe.get_safe_refund(s_type)
        form = MessageForm(
            title="确认删除",
            content=f"确定要删除 {type_name} #{safe_index + 1} 吗？\n\n"
                    f"保险箱内所有物品将被永久删除！\n返还 {refund} {MONEY_NAME} (60%)",
            button1=f"{C.RED}确认删除{C.RESET}",
            button2=f"{C.GRAY}取消{C.RESET}",
            on_submit=lambda p, choice: self._handle_delete_safe(p, choice, safe_index),
            on_close=lambda p: self.send_safe_detail(p, safe_index),
        )
        player.send_form(form)

    def _handle_delete_safe(self, player: Player, choice: int, safe_index: int):
        if choice != 0:
            self.send_safe_detail(player, safe_index)
            return
        is_op = bool(getattr(player, "is_op", False))
        safe_type = self.safe.delete_safe(self._k(player), safe_index)
        if safe_type is not None:
            if is_op:
                # 管理员删除无退款
                player.send_message(f"{PFX}{C.YELLOW}保险箱已删除（管理员，无退款）。{C.RESET}")
            else:
                refund = self.safe.get_safe_refund(safe_type)
                self.plugin.change_money(player.name, refund)
                player.send_message(f"{PFX}{C.YELLOW}保险箱已删除，返还 {refund} {MONEY_NAME}。{C.RESET}")
        self.send_safe_menu(player)

    # ---------- 经验保管箱 ----------

    def _buy_exp_vault(self, player: Player):
        if self.safe.has_exp_vault(self._k(player)):
            player.send_message(f"{PFX}{C.RED}已拥有经验保管箱{C.RESET}")
            self.send_safe_menu(player)
            return
        is_op = bool(getattr(player, "is_op", False))
        # 管理员 0 元免费购买
        if not is_op:
            if not self.plugin.change_money(player.name, -EXP_VAULT_PRICE):
                player.send_message(f"{PFX}{C.RED}{MONEY_NAME}不足！需要 {EXP_VAULT_PRICE}{C.RESET}")
                self.send_safe_menu(player)
                return
        if self.safe.buy_exp_vault(self._k(player)):
            if is_op:
                player.send_message(f"{PFX}{C.GREEN}管理员免费购买经验保管箱成功！{C.RESET}")
            else:
                player.send_message(f"{PFX}{C.GREEN}经验保管箱购买成功！{C.RESET}")
        else:
            if not is_op:
                self.plugin.change_money(player.name, EXP_VAULT_PRICE)
            player.send_message(f"{PFX}{C.RED}购买失败{C.RESET}")
        self.send_safe_menu(player)

    def send_exp_vault_menu(self, player: Player):
        exp_level = self.safe.get_exp_vault_level(self._k(player))
        player_level = player.exp_level if hasattr(player, "exp_level") else 0
        form = ActionForm(
            title="经验保管箱",
            content=f"当前储存: {exp_level}级 | 你的等级: {player_level}级",
        )
        form.add_button(f"{C.GREEN}存入经验{C.RESET}", on_click=lambda p: self.send_exp_deposit(p))
        form.add_button(f"{C.YELLOW}取出经验{C.RESET}", on_click=lambda p: self.send_exp_withdraw(p))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_menu(p))

    def send_exp_deposit(self, player: Player):
        player_level = player.exp_level if hasattr(player, "exp_level") else 0
        if player_level <= 0:
            player.send_message(f"{PFX}{C.RED}你没有经验可以存入{C.RESET}")
            self.send_exp_vault_menu(player)
            return
        form = ModalForm(
            title="存入经验",
            controls=[Slider(label="存入等级", min=1, max=player_level, step=1, default_value=player_level)],
            on_submit=lambda p, data: self._handle_exp_deposit(p, data),
            on_close=lambda p: self.send_exp_vault_menu(p),
        )
        player.send_form(form)

    def _handle_exp_deposit(self, player: Player, data: str):
        try:
            amount = int(_json.loads(data)[0])
        except Exception:
            return
        if amount <= 0:
            return
        player_level = player.exp_level if hasattr(player, "exp_level") else 0
        if amount > player_level:
            amount = player_level
        stored = self.safe.deposit_exp(self._k(player), amount)
        if stored > 0:
            try:
                self.plugin.server.dispatch_command(
                    self.plugin.server.command_sender, f"xp -{stored}L {player.name}")
            except Exception as e:
                self.plugin.logger.error(f"[ARC-IM] XP deposit cmd failed: {e}")
            player.send_message(f"{PFX}{C.GREEN}已存入 {stored} 级经验{C.RESET}")
        else:
            player.send_message(f"{PFX}{C.YELLOW}经验保管箱已满{C.RESET}")
        self.send_exp_vault_menu(player)

    def send_exp_withdraw(self, player: Player):
        exp_level = self.safe.get_exp_vault_level(self._k(player))
        if exp_level <= 0:
            player.send_message(f"{PFX}{C.RED}经验保管箱是空的{C.RESET}")
            self.send_exp_vault_menu(player)
            return
        form = ModalForm(
            title="取出经验",
            controls=[Slider(label="取出等级", min=1, max=exp_level, step=1, default_value=exp_level)],
            on_submit=lambda p, data: self._handle_exp_withdraw(p, data),
            on_close=lambda p: self.send_exp_vault_menu(p),
        )
        player.send_form(form)

    def _handle_exp_withdraw(self, player: Player, data: str):
        try:
            amount = int(_json.loads(data)[0])
        except Exception:
            return
        if amount <= 0:
            return
        withdrawn = self.safe.withdraw_exp(self._k(player), amount)
        if withdrawn > 0:
            try:
                self.plugin.server.dispatch_command(
                    self.plugin.server.command_sender, f"xp {withdrawn}L {player.name}")
            except Exception as e:
                self.plugin.logger.error(f"[ARC-IM] XP withdraw cmd failed: {e}")
            player.send_message(f"{PFX}{C.GREEN}已取出 {withdrawn} 级经验{C.RESET}")
        self.send_exp_vault_menu(player)

    # ---------- 保险箱详情 ----------

    def send_safe_detail(self, player: Player, safe_index: int):
        k = self._k(player)
        items = self.safe.get_safe_items(k, safe_index)
        info = self.safe.get_safe_info(k, safe_index)
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
                                on_click=lambda p, s=i: self.send_safe_item_action(p, safe_index, s))
            else:
                form.add_button(f"槽位{i + 1}: {C.GRAY}空{C.RESET}",
                                on_click=lambda p, s=i: self.send_safe_item_action(p, safe_index, s))
        form.add_button(f"{C.GREEN}存入物品{C.RESET}",
                        on_click=lambda p: self.send_deposit_select(p, safe_index))
        form.add_button(f"{C.AQUA}更改保险箱名字{C.RESET}",
                        on_click=lambda p: self.send_rename_safe(p, safe_index))
        form.add_button(f"{C.YELLOW}清除指定插槽{C.RESET}",
                        on_click=lambda p: self.send_clear_slot_select(p, safe_index))
        form.add_button(f"{C.RED}删除此保险箱 (返还{refund} {MONEY_NAME}){C.RESET}",
                        on_click=lambda p: self.send_delete_safe_confirm(p, safe_index))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_menu(p))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_menu(p))

    def send_rename_safe(self, player: Player, safe_index: int):
        info = self.safe.get_safe_info(self._k(player), safe_index)
        current = info["name"] if info and info["name"] else ""
        form = ModalForm(
            title="更改保险箱名字",
            controls=[TextInput(label="新名字", placeholder="输入保险箱名称", default_value=current)],
            on_submit=lambda p, data: self._handle_rename_safe(p, data, safe_index),
            on_close=lambda p: self.send_safe_detail(p, safe_index),
        )
        player.send_form(form)

    def _handle_rename_safe(self, player: Player, data: str, safe_index: int):
        try:
            new_name = str(_json.loads(data)[0]).strip()[:20]
            if self.safe.rename_safe(self._k(player), safe_index, new_name):
                player.send_message(f"{PFX}{C.GREEN}保险箱已改名: {new_name}{C.RESET}")
        except Exception:
            pass
        self.send_safe_detail(player, safe_index)

    def send_clear_slot_select(self, player: Player, safe_index: int):
        items = self.safe.get_safe_items(self._k(player), safe_index)
        form = ActionForm(title="清除指定插槽", content="选择要清除的插槽")
        for i, item in enumerate(items):
            if item is not None:
                display = self.safe.format_slot_display(item)
                form.add_button(f"槽位{i + 1}: {C.WHITE}{display}{C.RESET}",
                                on_click=lambda p, s=i: self.send_clear_slot_confirm(p, safe_index, s))
            else:
                form.add_button(f"槽位{i + 1}: {C.GRAY}空{C.RESET}")
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_detail(p, safe_index))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_detail(p, safe_index))

    def send_clear_slot_confirm(self, player: Player, safe_index: int, slot: int):
        form = MessageForm(
            title="确认清除",
            content=f"是否确定清除当前插槽的物品？\n\n{C.RED}此操作不可恢复！{C.RESET}",
            button1=f"{C.RED}确认清除{C.RESET}",
            button2=f"{C.GRAY}取消{C.RESET}",
            on_submit=lambda p, choice: self._handle_clear_slot(p, choice, safe_index, slot),
            on_close=lambda p: self.send_clear_slot_select(p, safe_index),
        )
        player.send_form(form)

    def _handle_clear_slot(self, player: Player, choice: int, safe_index: int, slot: int):
        if choice == 0:
            if self.safe.clear_safe_slot(self._k(player), safe_index, slot):
                player.send_message(f"{PFX}{C.YELLOW}插槽 {slot + 1} 已清除。{C.RESET}")
        self.send_safe_detail(player, safe_index)

    # ---------- 取出 ----------

    def send_safe_item_action(self, player: Player, safe_index: int, slot: int):
        items = self.safe.get_safe_items(self._k(player), safe_index)
        item = items[slot] if slot < len(items) else None
        if item is None:
            self.send_deposit_select(player, safe_index)
            return
        display = self.safe.format_slot_display(item)
        form = ActionForm(title=f"槽位 {slot + 1}", content=f"{C.WHITE}{display}{C.RESET}")
        form.add_button(f"{C.YELLOW}取出全部 ({item['amount']}){C.RESET}",
                        on_click=lambda p: self._do_withdraw(p, safe_index, slot, item["amount"]))
        half = max(1, item["amount"] // 2)
        form.add_button(f"{C.YELLOW}取出一半 ({half}){C.RESET}",
                        on_click=lambda p: self._do_withdraw(p, safe_index, slot, half))
        form.add_button(f"{C.YELLOW}选取数量...{C.RESET}",
                        on_click=lambda p: self.send_withdraw_slider(p, safe_index, slot, item["amount"]))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_detail(p, safe_index))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_detail(p, safe_index))

    def send_withdraw_slider(self, player: Player, safe_index: int, slot: int, max_amount: int):
        form = ModalForm(
            title="取出数量",
            controls=[Slider(label="数量", min=1, max=max_amount, step=1, default_value=max_amount)],
            on_submit=lambda p, data: self._handle_withdraw_slider(p, data, safe_index, slot),
            on_close=lambda p: self.send_safe_item_action(p, safe_index, slot),
        )
        player.send_form(form)

    def _handle_withdraw_slider(self, player: Player, data: str, safe_index: int, slot: int):
        try:
            amount = int(_json.loads(data)[0])
            if amount > 0:
                self._do_withdraw(player, safe_index, slot, amount)
        except Exception:
            pass

    def _do_withdraw(self, player: Player, safe_index: int, slot: int, amount: int):
        k = self._k(player)
        item_data = self.safe.withdraw_item(k, safe_index, slot, amount)
        if item_data is None:
            player.send_message(f"{PFX}{C.RED}取出失败{C.RESET}")
            return
        item_type = item_data["type"]
        item_amount = item_data["amount"]
        ench_list = item_data.get("enchantments", [])
        display_name = item_data.get("display_name", "")
        data_val = item_data.get("data", 0)

        # 单次取出数量上限（保守限制，与 SLE 一致）
        if item_amount > 255:
            self.safe.deposit_item(k, safe_index, item_type, item_amount, ench_list, display_name, data=data_val)
            player.send_message(f"{PFX}{C.YELLOW}取出数量过大，应<=255{C.RESET}")
            return

        # 药水/药箭/不详之瓶（带 data 值）特殊处理：分块放入空槽，避免 add_item 合并出错（同步自 SLE）
        potion_types = ("minecraft:potion", "minecraft:lingering_potion", "minecraft:splash_potion")
        if (item_type in potion_types or item_type == "minecraft:ominous_bottle"
                or (item_type == "minecraft:arrow" and data_val > 0)) and data_val:
            if not self._withdraw_data_item(player, k, safe_index, item_data, item_type, item_amount,
                                            ench_list, display_name, data_val, potion_types):
                # 分块失败，回退 /give
                try:
                    short_type = item_type.split(":")[-1]
                    ok = self.plugin.server.dispatch_command(
                        self.plugin.server.command_sender,
                        f"give {player.name} {short_type} {item_amount} {data_val}")
                    if ok:
                        show_name = display_name if display_name else _item_display_name(item_data)
                        player.send_message(f"{PFX}{C.GREEN}已取出 {show_name} x{item_amount}{C.RESET}")
                    else:
                        self.safe.deposit_item(k, safe_index, item_type, item_amount,
                                               ench_list, display_name, data=data_val)
                        player.send_message(f"{PFX}{C.RED}取出失败（give命令未执行）{C.RESET}")
                except Exception as e:
                    self.safe.deposit_item(k, safe_index, item_type, item_amount,
                                           ench_list, display_name, data=data_val)
                    player.send_message(f"{PFX}{C.RED}取出药水失败: {e}{C.RESET}")
            return

        # 普通物品：转成 ARC item_info，用 give_item_count 发放（处理背包满/64 拆分/附魔补回）
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
                self.safe.deposit_item(k, safe_index, item_type, leftover,
                                       ench_list, display_name, data=data_val)
                player.send_message(f"{PFX}{C.YELLOW}背包空间不足，部分退回保险箱{C.RESET}")
            else:
                if display_name:
                    self._apply_display_name(player, item_type, data_val, display_name)
                show_name = display_name if display_name else _item_cn_name(item_type)
                player.send_message(f"{PFX}{C.GREEN}已取出 {show_name} x{given}{C.RESET}")
        except Exception as e:
            self.safe.deposit_item(k, safe_index, item_type, item_amount, ench_list, display_name, data=data_val)
            player.send_message(f"{PFX}{C.RED}取出失败: {e}{C.RESET}")

    def _withdraw_data_item(self, player, k, safe_index, item_data, item_type, item_amount,
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
                self.safe.deposit_item(k, safe_index, item_type, remaining,
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
                    # 无附魔物品 item_meta 可能为 None，需用 factory 创建 meta
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

    # ---------- 存入 ----------

    def send_deposit_select(self, player: Player, safe_index: int):
        # 用 ARC InventoryManager 读取背包（get_enchant_level 读附魔，安全）
        inv_items = self.inventory.get_inventory_items(player)
        if not inv_items:
            player.send_message(f"{PFX}{C.RED}背包没有可存入的物品{C.RESET}")
            self.send_safe_detail(player, safe_index)
            return
        groups: dict[tuple, dict] = {}
        for it in inv_items:
            t = it.get("type", "")
            # 潜影盒/收纳袋旧格式无法完整还原，跳过
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
            form.add_button(label, on_click=lambda p, gg=g: self.send_deposit_amount(p, safe_index, gg))
        form.add_button(f"{C.GRAY}返回{C.RESET}", on_click=lambda p: self.send_safe_detail(p, safe_index))
        _send_form(player, form, on_close_cb=lambda p: self.send_safe_detail(p, safe_index))

    def _format_deposit_label(self, g: dict) -> str:
        name = g.get("display_name") or _item_cn_name(g.get("type", ""))
        if g.get("data"):
            name += f" (ID:{g['data']})"
        return f"{C.WHITE}{name}{C.RESET} x{g.get('count', 0)}{_fmt_enchant_text(g.get('enchants'))}"

    def send_deposit_amount(self, player: Player, safe_index: int, group: dict):
        max_amount = group.get("count", 1)
        form = ModalForm(
            title="存入数量",
            controls=[Slider(label="存入数量", min=1, max=max_amount, step=1, default_value=max_amount)],
            on_submit=lambda p, data: self._handle_deposit(p, data, safe_index, group),
            on_close=lambda p: self.send_safe_detail(p, safe_index),
        )
        player.send_form(form)

    def _handle_deposit(self, player: Player, data: str, safe_index: int, group: dict):
        try:
            amount = int(_json.loads(data)[0])
        except Exception:
            return
        if amount <= 0:
            return
        k = self._k(player)
        ench_list = [{"id": e, "level": lv} for e, lv in (group.get("enchants") or {}).items()]
        # 1. 记账入保险箱（SLE 旧格式）
        stored, actual_safe, _slot = self.safe.deposit_item(
            k, safe_index, group["type"], amount, ench_list,
            group.get("display_name", ""), data=group.get("data", 0))
        if stored <= 0:
            player.send_message(f"{PFX}{C.RED}保险箱已满！{C.RESET}")
            self.send_safe_detail(player, safe_index)
            return
        # 2. 用 ARC remove_item 从背包精确扣除（不再 clear 误扣）
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
            # 扣除异常，退回保险箱记账
            self.safe.withdraw_item(k, actual_safe, _slot, stored)
            player.send_message(f"{PFX}{C.RED}存入失败，已退回保险箱记录{C.RESET}")
        self.send_safe_detail(player, safe_index)
