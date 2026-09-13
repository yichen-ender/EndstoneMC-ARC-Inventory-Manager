# -*- coding: utf-8 -*-
"""SafeManager 纯逻辑单测（不依赖 endstone 运行时）：双段隔离、存取逻辑、NBT 隔离、并发守恒、权限评估。

运行： python tests/test_safe.py
"""
import random
import sys
import tempfile
import threading
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
sys.path.insert(0, SRC)

from endstone_arc_inventory_manager.safe import (
    SafeManager, MODE_BOTH, MODE_STORE_ONLY, MODE_WITHDRAW_ONLY, MODE_NONE,
)

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))


tmp = Path(tempfile.mkdtemp(prefix="safetest_"))
sm = SafeManager(tmp)

# ---- 1. 个人/公会双段隔离 ----
p_scope = sm.p_scope("xuid_A")
g_scope = sm.g_scope(42)
sm.buy_safe(p_scope, "small")
sm.buy_safe(g_scope, "normal")
check("个人与公会段相互独立", sm.get_safe_count(p_scope) == 1 and sm.get_safe_count(g_scope) == 1)
check("个人与公会上限各自为6", sm.can_buy_safe(p_scope) and sm.can_buy_safe(g_scope))

# ---- 2. 存入合并 / 分槽 / 满仓 ----
stored, si, slot = sm.deposit_item(g_scope, 0, "minecraft:apple", 10)
check("首存10个苹果入普通箱", stored == 10 and si == 0 and slot == 0)
stored2, si2, slot2 = sm.deposit_item(g_scope, 0, "minecraft:apple", 5)
check("同物品合并叠放", stored2 == 5 and si2 == 0 and slot2 == 0)
items = sm.get_safe_items(g_scope, 0)
check("槽0数量=15", items[0]["amount"] == 15)
# 同物品跨箱叠放：苹果已存在 index0 槽0，存入时优先叠到该堆
sm.buy_safe(g_scope, "large")  # index 1, 6 slots（空）
stored3, si3, slot3 = sm.deposit_item(g_scope, 1, "minecraft:apple", 1)
check("同物品跨箱叠放到已有堆叠", stored3 == 1 and si3 == 0 and slot3 == 0)
# 不同物品：落到首选箱空槽
stored4, si4, slot4 = sm.deposit_item(g_scope, 1, "minecraft:diamond", 2)
check("不同物品存入首选箱空槽", stored4 == 2 and si4 == 1 and slot4 == 0)
# 同物品再存 → 叠放到 index1 槽0
stored5, si5, slot5 = sm.deposit_item(g_scope, 1, "minecraft:diamond", 3)
check("同物品叠放到同一槽", stored5 == 3 and si5 == 1 and slot5 == 0)

# ---- 3. 取出夹紧 ----
# index0 槽0 = 16 苹果（10+5 再叠 1），取100 → 夹紧为16
wd = sm.withdraw_item(g_scope, 0, 0, 100)
check("取出数量被夹紧到实际存量", wd["amount"] == 16 and wd["type"] == "minecraft:apple")
check("取出后槽0清空", sm.get_safe_items(g_scope, 0)[0] is None)

# ---- 4. NBT 物品（潜影盒/收纳袋）----
n_scope = sm.p_scope("xuid_NBT")
sm.buy_safe(n_scope, "large")

BOX_A = "eyJib3giOiJBIn0="   # 假装是 base64(JSON) 的 NBT，内容不同即视为不同物品
BOX_B = "eyJib3giOiJCIn0="

sa, _, _ = sm.deposit_item(n_scope, 0, "minecraft:shulker_box", 1, nbt_b64=BOX_A, nbt_items=3)
check("带 NBT 的潜影盒可存入", sa == 1)

# 内容不同 → 必须各占独立槽位，不能合并
sb, _, slot_b = sm.deposit_item(n_scope, 0, "minecraft:shulker_box", 1, nbt_b64=BOX_B, nbt_items=7)
check("内容不同的潜影盒不合并", sb == 1 and slot_b == 1)

# 内容相同 → 应叠放到同一槽
sc, _, slot_c = sm.deposit_item(n_scope, 0, "minecraft:shulker_box", 2, nbt_b64=BOX_A, nbt_items=3)
check("内容相同的潜影盒合并", sc == 2 and slot_c == 0)

n_items = sm.get_safe_items(n_scope, 0)
check("槽0 数量为 1+2=3", n_items[0]["amount"] == 3)
check("槽0 保留了 nbt_b64", n_items[0].get("nbt_b64") == BOX_A)
check("槽0 保留了 nbt_items", n_items[0].get("nbt_items") == 3)
check("槽1 是另一个内容的盒子", n_items[1].get("nbt_b64") == BOX_B)

# 取出时必须原样带回 NBT
got = sm.withdraw_item(n_scope, 0, 0, 3)
check("取出带 NBT 的物品", got is not None and got["amount"] == 3)
check("取出后 nbt_b64 原样返回", got.get("nbt_b64") == BOX_A)
check("取出后 nbt_items 原样返回", got.get("nbt_items") == 3)
check("取出后槽位清空", sm.get_safe_items(n_scope, 0)[0] is None)

# 无 NBT 的普通物品不应被 NBT 字段影响（向后兼容旧数据）
sp_, _, _ = sm.deposit_item(n_scope, 0, "minecraft:stone", 64)
plain = sm.get_safe_items(n_scope, 0)[1 - 1] if False else sm.get_safe_items(n_scope, 0)[0]
check("普通物品不带 nbt_b64", not plain.get("nbt_b64"))
pw = sm.withdraw_item(n_scope, 0, 0, 10)
check("普通物品取出返回空 nbt_b64", (pw.get("nbt_b64") or "") == "")

# 旧数据（条目里根本没有 nbt_b64 字段）应能正常匹配与叠加
old_scope = sm.p_scope("xuid_OLD")
sm.buy_safe(old_scope, "normal")
# 直接构造一条 1.2.1 时期的旧格式条目
old_items = sm.get_safe_items(old_scope, 0)
old_items[0] = {"type": "minecraft:apple", "amount": 5, "enchantments": [],
                "display_name": "", "container_items": [], "data": 0}
stored_old, _, slot_old = sm.deposit_item(old_scope, 0, "minecraft:apple", 3)
check("旧格式数据仍可叠放", stored_old == 3 and slot_old == 0)
check("旧格式数据叠放后数量正确", sm.get_safe_items(old_scope, 0)[0]["amount"] == 8)

# ---- 5. 权限评估 ----
g2 = sm.g_scope(7)
sm.buy_safe(g2, "normal")
# 会长无视一切
check("会长始终可存可取", sm.evaluate_access(g2, 0, "owner_xuid", "owner", 0) == (True, True, None))
# 管理员默认可存可取，无视贡献点限制
sm.set_contribution_limit(g2, 0, 1000)
check("管理员无视贡献点限制", sm.evaluate_access(g2, 0, "mgr_xuid", "manager", 500) == (True, True, None))
# 成员受贡献点限制
check("成员贡献点不足被禁", sm.evaluate_access(g2, 0, "mem_xuid", "member", 500) == (False, False, "CONTRIB_LOW"))
check("成员贡献点足够放行", sm.evaluate_access(g2, 0, "mem_xuid", "member", 1500) == (True, True, None))
# 逐人覆盖
sm.set_safe_permission(g2, 0, "mem_xuid", MODE_STORE_ONLY)
check("覆盖=仅存(禁取)", sm.evaluate_access(g2, 0, "mem_xuid", "member", 1500) == (True, False, None))
sm.set_safe_permission(g2, 0, "mem_xuid", MODE_WITHDRAW_ONLY)
check("覆盖=仅取(禁存)", sm.evaluate_access(g2, 0, "mem_xuid", "member", 1500) == (False, True, None))
sm.set_safe_permission(g2, 0, "mem_xuid", MODE_NONE)
check("覆盖=双向禁止", sm.evaluate_access(g2, 0, "mem_xuid", "member", 1500) == (False, False, None))
sm.set_safe_permission(g2, 0, "mem_xuid", MODE_BOTH)
check("覆盖=可存可取(删除覆盖)", sm.get_safe_permission(g2, 0, "mem_xuid") is None and
      sm.evaluate_access(g2, 0, "mem_xuid", "member", 1500) == (True, True, None))

# ---- 6. 并发守恒（防刷物品核心） ----
g3 = sm.g_scope(99)
sm.buy_safe(g3, "large")  # 6 slots
initial = 3000
sm.deposit_item(g3, 0, "minecraft:apple", initial)
N_THREADS = 8
DEPOSITS = 150  # 每线程 150 次存入 1 个
deposited_total = N_THREADS * DEPOSITS
barrier = threading.Barrier(N_THREADS)
lock_total = threading.Lock()
actual_withdrawn = [0]


def worker():
    barrier.wait()
    # 每次：先存1个苹果，再从随机槽尝试取1个（可能取空失败）
    for _ in range(DEPOSITS):
        sm.deposit_item(g3, 0, "minecraft:apple", 1)
        with sm.lock:
            items = sm.get_safe_items(g3, 0)
            idx = random.randrange(len(items))
            got = sm.withdraw_item(g3, 0, idx, 1)
        if got is not None:
            with lock_total:
                actual_withdrawn[0] += got["amount"]


threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
for t in threads:
    t.start()
for t in threads:
    t.join()

final_count = 0
for it in sm.get_safe_items(g3, 0):
    if it is not None and it["type"] == "minecraft:apple":
        final_count += it["amount"]
expected = initial + deposited_total - actual_withdrawn[0]
check("并发存取后总量守恒（不丢不增）", final_count == expected)
check("并发取出的数量>0", actual_withdrawn[0] > 0)

# ---- 结果输出 ----
fails = [n for n, ok in RESULTS if not ok]
print("PASS: %d / %d" % (len(RESULTS) - len(fails), len(RESULTS)))
for n, ok in RESULTS:
    print(("[OK]   " if ok else "[FAIL] ") + n)
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
