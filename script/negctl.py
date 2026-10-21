#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阴性对照:向规范文本副本注入已知故障,验证 check_spec.py 确实能抓到。

这对应一致性册 §6.4 —— 「检查器报告 0 问题」只是「脚本没报错」,
不是「检查通过」。必须先证明检查器**有能力**抓到故障。

用法: python script/negctl.py [--impl <wlwl 可执行文件>]
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(REPO, "script", "check_spec.py")

IMPL_CANDIDATES = [
    os.path.join(REPO, "..", "wlwl", "impl", "target", "release", "wlwl.exe"),
    os.path.join(REPO, "..", "wlwl", "impl", "target", "debug", "wlwl.exe"),
    os.path.join(REPO, "..", "wlwl", "impl", "target", "release", "wlwl"),
    os.path.join(REPO, "..", "wlwl", "impl", "target", "debug", "wlwl"),
]

# (描述, 目标文件相对路径, 原文, 注入文本, 期望在 stderr 里出现的关键字)
FAULTS = [
    (
        "标点契约:注入全角逗号",
        "docs/spec/wlwl-v1.0-language.md",
        "### 1.1 源文件",
        "### 1.1 源文件\n\n本节测试，全角逗号。",
        "标点契约",
    ),
    (
        "引用有效性:注入指向不存在章节的引用",
        "docs/spec/wlwl-v1.0-language.md",
        "### 1.1 源文件",
        "### 1.1 源文件\n\n见 §99.9。",
        "指向不存在的章节",
    ),
    (
        "相对链接:注入指向不存在文件的链接",
        "README.md",
        "## 册子地图",
        "## 册子地图\n\n[坏的](./docs/spec/不存在.md)",
        "相对链接指向不存在的文件",
    ),
    (
        "示例有效性:注入运行会失败的 wlwl 代码块",
        "docs/spec/wlwl-v1.0-language.md",
        "### 1.1 源文件",
        "### 1.1 源文件\n\n```wlwl\nPRINT(未定义的名字);\n```\n",
        "运行失败",
    ),
]


def find_impl(explicit):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for c in IMPL_CANDIDATES:
        c = os.path.normpath(c)
        if os.path.exists(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", help="参考实现的 wlwl 可执行文件路径")
    args = ap.parse_args()

    impl = find_impl(args.impl)
    if not impl:
        print("未找到参考实现;第 7 项(示例有效性)的阴性对照将被跳过。")
    else:
        print(f"参考实现:{impl}")

    cmd = [sys.executable, os.path.join(REPO, "script", "check_spec.py")]
    if impl:
        cmd += ["--impl", impl]

    tmp = tempfile.mkdtemp(prefix="wlwl-negctl-")
    work = os.path.join(tmp, "repo")
    shutil.copytree(
        REPO,
        work,
        ignore=shutil.ignore_patterns(".git", ".scratch", "__pycache__"),
    )
    try:
        failures = []
        for desc, rel, anchor, injected, expect in FAULTS:
            if expect == "运行失败" and not impl:
                print(f"  [跳过] {desc}(无参考实现)")
                continue
            path = os.path.join(work, rel)
            with io.open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if anchor not in text:
                failures.append(f"{desc}:锚点未命中 —— 注入未落地")
                continue
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace(anchor, injected, 1))

            r = subprocess.run(
                [sys.executable, os.path.join(work, "script", "check_spec.py")]
                + (["--impl", impl] if impl else []),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            out = (r.stdout or "") + (r.stderr or "")
            hit = expect in out
            print(f"  [{'命中' if hit else '漏网'}] {desc}")
            if not hit:
                failures.append(f"{desc}:期望输出中出现 {expect!r},实际未出现")
                print("     ---- 检查器输出 ----")
                print("     " + out.replace("\n", "\n     ")[:1500])

            # 还原,让下一次注入在干净副本上进行。
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)

        if failures:
            print(f"\n阴性对照失败:{len(failures)} 项")
            for f in failures:
                print("  " + f)
            return 1
        print(f"\n阴性对照通过:{len(FAULTS)} 项注入全部被检查器抓到。")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
