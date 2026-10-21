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
    (
        "诊断码归属:在标准库册里注入一个诊断码定义",
        "docs/stdlib/wlwl-v1.0-stdlib-core.md",
        "### 0.5 记法",
        "### 0.5 记法\n\n| `E9999` | 注入用 | 注入用 | — | R2 |\n",
        "诊断码归属",
    ),
    (
        "诊断码归属(方向一):塌缩语言核心册码表里的范围,让正文提到的码失去定义",
        "docs/spec/wlwl-v1.0-language.md",
        "| `E0010`–`E0013` | 语法错误 |",
        "| `E0010` | 语法错误 |",
        "规范提了它却没定义它",
    ),
    (
        "[stub] 标记对拍:抽掉注册镜像册里的 [stub] 标记",
        "docs/stdlib/wlwl-v1.0-stdlib-registry.md",
        "`TOOL` `CALL_TOOL` `[stub]`",
        "`TOOL` `CALL_TOOL`",
        "[stub] 标记不一致",
    ),
    (
        "成员面对拍:往注册镜像册注入一个不存在的成员",
        "docs/stdlib/wlwl-v1.0-stdlib-registry.md",
        "## 附录 A 成员注册镜像",
        "## 附录 A 成员注册镜像\n\n| 命名空间 | 成员 | 层 | v1.0 册 |\n|---|---|---|---|\n"
        "| `std.io` | `PRINT` `INPUT` `ZZZ_INJECTED` | R2 | 系统 |\n",
        "成员面对拍",
    ),
    (
        "不可见控制字符:注入一个换页符",
        "docs/spec/wlwl-v1.0-objects.md",
        "## 13 OOP 关键字与对象模型",
        "## 13 OOP 关键字与对象模型\n\n注入\x0c一个换页符。\n",
        "不可见控制字符",
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
        hit_count = 0
        skip_count = 0
        for desc, rel, anchor, injected, expect in FAULTS:
            if expect == "运行失败" and not impl:
                print(f"  [跳过] {desc}(无参考实现)")
                continue
            path = os.path.join(work, rel)
            with io.open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if anchor not in text:
                print(f"  [跳过] {desc}(锚点未命中 —— 目标册尚未成稿)")
                skip_count += 1
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
            if hit:
                hit_count += 1
            else:
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
        print(
            f"\n阴性对照通过:命中 {hit_count} 项"
            + (f",跳过 {skip_count} 项" if skip_count else "")
            + f"(共 {len(FAULTS)} 项注入)。"
        )
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
