"""从同一份 src/ 构建两个独立 wheel 并部署。

为什么要两个 wheel：Endstone 0.11.3 的 PythonPluginLoader 一个 wheel 只加载一个插件，
且要求「包名 == endstone_<入口点名>」。详见 pyproject.toml 顶部注释。

产出：
  endstone_arc_inventory_manager-<ver>-py3-none-any.whl   → entry point: arc_inventory_manager（保险箱，/arcim）
  endstone_arc_inventory-<ver>-py3-none-any.whl           → entry point: arc_inventory（背包 API）

用法：python build_all.py [--no-deploy]
"""
import ast
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "src")
DIST = os.path.join(REPO, "dist")
PLUGINS = r"H:\endstone-win_amd64-portable\bedrock_server\plugins"
PY = r"H:\endstone-win_amd64-portable\bin\python\python.exe"

# (构建用的 pyproject 文件, 期望产出的包名, 期望的 entry point 名, 必须存在的模块)
TARGETS = [
    ("pyproject.toml", "endstone_arc_inventory_manager", "arc_inventory_manager",
     ["endstone_arc_inventory_manager/__init__.py",
      "endstone_arc_inventory_manager/plugin.py",
      "endstone_arc_inventory_manager/ui.py",
      "endstone_arc_inventory_manager/safe.py",
      "endstone_arc_inventory_manager/inventory.py",
      "endstone_arc_inventory_manager/item_cn.py"]),
    ("pyproject.inventory.toml", "endstone_arc_inventory", "arc_inventory",
     ["endstone_arc_inventory/__init__.py",
      "endstone_arc_inventory/arc_inventory_plugin.py",
      "endstone_arc_inventory/InventoryManager.py"]),
]


def syntax_check() -> None:
    bad = []
    for root, _, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                try:
                    ast.parse(open(p, encoding="utf-8").read(), filename=p)
                except SyntaxError as e:
                    bad.append(f"{p}: {e}")
    if bad:
        print("语法错误:")
        for b in bad:
            print("  ", b)
        sys.exit(1)
    print("[1/4] 语法检查通过")


def build_one(cfg_name: str, pkg: str, ep_name: str, must: list) -> str:
    """把 src/ + 指定的 pyproject 复制到临时目录构建，返回 wheel 路径。"""
    tmp = tempfile.mkdtemp(prefix=f"arcbuild_{pkg}_")
    try:
        shutil.copytree(SRC, os.path.join(tmp, "src"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        for extra in ("README.md", "LICENSE", ".gitignore"):
            s = os.path.join(REPO, extra)
            if os.path.exists(s):
                shutil.copy2(s, tmp)
        shutil.copy2(os.path.join(REPO, cfg_name), os.path.join(tmp, "pyproject.toml"))

        r = subprocess.run([PY, "-m", "build", "--wheel", "--no-isolation"],
                           cwd=tmp, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-3000:])
            print(r.stderr[-3000:])
            raise SystemExit(f"构建失败: {pkg}")

        whls = glob.glob(os.path.join(tmp, "dist", "*.whl"))
        if len(whls) != 1:
            raise SystemExit(f"期望产出 1 个 wheel，实际 {len(whls)} 个: {whls}")
        whl = whls[0]

        # 校验：包名、entry point、模块齐全、文件大小与源码一致
        base = os.path.basename(whl)
        if not base.startswith(pkg.replace("-", "_") + "-"):
            raise SystemExit(f"wheel 名不符: {base}，期望前缀 {pkg}")
        z = zipfile.ZipFile(whl)
        names = z.namelist()
        missing = [m for m in must if m not in names]
        if missing:
            raise SystemExit(f"wheel 缺模块: {missing}")
        ep_files = [n for n in names if n.endswith("entry_points.txt")]
        if not ep_files:
            raise SystemExit("wheel 缺 entry_points.txt")
        ep_txt = z.read(ep_files[0]).decode()
        if f"{ep_name} =" not in ep_txt:
            raise SystemExit(f"entry point 不符: {ep_txt}")
        # 只能有一个 endstone 入口，否则触发 load_plugin 首个即返回的行为
        ep_lines = [l for l in ep_txt.splitlines()
                    if l.strip() and not l.startswith("[")]
        if len(ep_lines) != 1:
            raise SystemExit(f"wheel 注册了 {len(ep_lines)} 个入口，Endstone 只会加载第一个: {ep_lines}")
        # 包内 .py 大小必须与源码一致
        for n in names:
            if n.endswith(".py"):
                s = os.path.join(SRC, n)
                if os.path.exists(s) and z.getinfo(n).file_size != os.path.getsize(s):
                    raise SystemExit(f"文件大小不一致: {n}")
        print(f"      {base}  ({os.path.getsize(whl)} bytes)  entry={ep_name}")
        out = os.path.join(DIST, base)
        shutil.copy2(whl, out)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    syntax_check()

    if os.path.isdir(DIST):
        # 只删自己产出的 wheel，保留目录里的其它东西
        for w in glob.glob(os.path.join(DIST, "endstone_arc_inventory*.whl")):
            os.remove(w)
    os.makedirs(DIST, exist_ok=True)
    print("[2/4] dist 已清理（只删 endstone_arc_inventory*.whl）")

    built = []
    print("[3/4] 构建 wheel:")
    for cfg, pkg, ep, must in TARGETS:
        built.append(build_one(cfg, pkg, ep, must))

    if "--no-deploy" in sys.argv:
        print("[4/4] 跳过部署（--no-deploy）")
        return

    # 部署：只清本插件自己的旧 wheel，绝不碰 endstone_sle-*.whl
    for old in glob.glob(os.path.join(PLUGINS, "endstone_arc_inventory*.whl")):
        if os.path.abspath(old) not in [os.path.abspath(b) for b in
                                        [os.path.join(PLUGINS, os.path.basename(x)) for x in built]]:
            os.remove(old)
            print("      删除旧 wheel:", os.path.basename(old))
    for b in built:
        shutil.copy2(b, os.path.join(PLUGINS, os.path.basename(b)))
    print("[4/4] 已部署:")
    for b in built:
        print("      ->", os.path.join(PLUGINS, os.path.basename(b)))
    print()
    print("plugins/ 现有 whl:",
          sorted(os.path.basename(w) for w in glob.glob(os.path.join(PLUGINS, "*.whl"))))


if __name__ == "__main__":
    main()
