#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阴性对照:向规范文本副本注入已知故障,验证 check_spec.py 确实能抓到。

这对应一致性册 §6.4 —— 「检查器报告 0 问题」只是「脚本没报错」,
不是「检查通过」。必须先证明检查器**有能力**抓到故障。

第 8 项(失败口径对拍)需要**实现仓的源码**,所以这里除了复制规范仓,
还把实现仓的 `crates/` 复制进工作区的 `upstream/impl/`,并用
`WLWL_IMPL_PATH` 指过去 —— 注入②就是在这份**副本**上删掉一条钉住用例。

用法: python script/negctl.py [--impl <wlwl 可执行文件>]
"""
import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHECKER = os.path.join(HERE, "check_spec.py")
sys.path.insert(0, HERE)
import check_spec  # noqa: E402  (需要它那套「实现仓在哪儿」的查找顺序)

IMPL_CANDIDATES = [
    os.path.join(REPO, "..", "wlwl", "impl", "target", "release", "wlwl.exe"),
    os.path.join(REPO, "..", "wlwl", "impl", "target", "debug", "wlwl.exe"),
    os.path.join(REPO, "..", "wlwl", "impl", "target", "release", "wlwl"),
    os.path.join(REPO, "..", "wlwl", "impl", "target", "debug", "wlwl"),
]

# (描述, 根, 相对路径, 原文, 注入文本, 期望, 判定方式)
#
#   根       "spec" = 规范仓副本;"impl" = 实现仓副本
#   期望     "keyword" 模式 = 输出里必须出现的关键字;
#            "count_drop" 模式 = 该命名空间的「已钉」数必须比基线少
FAULTS = [
    (
        "标点契约:注入全角逗号",
        "spec",
        "docs/spec/wlwl-v1.0-language.md",
        "### 1.1 源文件",
        "### 1.1 源文件\n\n本节测试，全角逗号。",
        "标点契约",
        "keyword",
    ),
    (
        "引用有效性:注入指向不存在章节的引用",
        "spec",
        "docs/spec/wlwl-v1.0-language.md",
        "### 1.1 源文件",
        "### 1.1 源文件\n\n见 §99.9。",
        "指向不存在的章节",
        "keyword",
    ),
    (
        "相对链接:注入指向不存在文件的链接",
        "spec",
        "README.md",
        "## 册子地图",
        "## 册子地图\n\n[坏的](./docs/spec/不存在.md)",
        "相对链接指向不存在的文件",
        "keyword",
    ),
    (
        "示例有效性:注入运行会失败的 wlwl 代码块",
        "spec",
        "docs/spec/wlwl-v1.0-language.md",
        "### 1.1 源文件",
        "### 1.1 源文件\n\n```wlwl\nPRINT(未定义的名字);\n```\n",
        "运行失败",
        "keyword",
    ),
    (
        "诊断码归属:在标准库册里注入一个诊断码定义",
        "spec",
        "docs/stdlib/wlwl-v1.0-stdlib-core.md",
        "### 0.5 记法",
        "### 0.5 记法\n\n| `E9999` | 注入用 | 注入用 | — | R2 |\n",
        "诊断码归属",
        "keyword",
    ),
    (
        "诊断码归属(方向一):塌缩语言核心册码表里的范围,让正文提到的码失去定义",
        "spec",
        "docs/spec/wlwl-v1.0-language.md",
        "| `E0010`–`E0013` | 语法错误 |",
        "| `E0010` | 语法错误 |",
        "规范提了它却没定义它",
        "keyword",
    ),
    (
        "[stub] 标记对拍:抽掉注册镜像册里的 [stub] 标记",
        "spec",
        "docs/stdlib/wlwl-v1.0-stdlib-registry.md",
        "`TOOL` `CALL_TOOL` `[stub]`",
        "`TOOL` `CALL_TOOL`",
        "[stub] 标记不一致",
        "keyword",
    ),
    (
        "成员面对拍:往注册镜像册注入一个不存在的成员",
        "spec",
        "docs/stdlib/wlwl-v1.0-stdlib-registry.md",
        "## 附录 A 成员注册镜像",
        "## 附录 A 成员注册镜像\n\n| 命名空间 | 成员 | 层 | v1.0 册 |\n|---|---|---|---|\n"
        "| `std.io` | `PRINT` `INPUT` `ZZZ_INJECTED` | R2 | 系统 |\n",
        "成员面对拍",
        "keyword",
    ),
    (
        "不可见控制字符:注入一个换页符",
        "spec",
        "docs/spec/wlwl-v1.0-objects.md",
        "## 13 OOP 关键字与对象模型",
        "## 13 OOP 关键字与对象模型\n\n注入\x0c一个换页符。\n",
        "不可见控制字符",
        "keyword",
    ),
    (
        # 第 8 项注入①:把某个成员失败列里的码改掉 ⇒ 规范侧要求的 (成员, 码)
        # 在实现侧**没有断言位**钉住 ⇒ 检查器必须报「失败口径未被钉住」。
        "失败口径对拍:把成员失败列的 E0030 改成 E0031",
        "spec",
        "docs/stdlib/wlwl-v1.0-stdlib-core.md",
        "| `MAP(arr, f) -> ARRAY` | `[f(v), ...]` | 非数组实参 → `E0030`;",
        "| `MAP(arr, f) -> ARRAY` | `[f(v), ...]` | 非数组实参 → `E0031`;",
        "失败口径未被钉住",
        "keyword",
    ),
    (
        # 第 8 项注入②:从实现仓**副本**里删掉一条钉住某成员的用例 ⇒ 该章的
        # 「已钉」数必须下降。判定靠与**基线**比,不是靠关键字。
        # 挑的是 std.fs::READ_FILE 的 E0061 —— 实测它在实现仓里**只**被
        # `fs_contract.rs` 的这一条用例钉住(eval 仓里那条的测试函数名不含
        # 成员名,归不到 READ_FILE 头上),所以删掉它「已钉」数必掉。
        "失败口径对拍:删掉实现侧钉住 READ_FILE 的用例(E0061)",
        "impl",
        "crates/wlwl-eval/tests/fs_contract.rs",
        r'''#[test]
fn read_file_of_a_missing_file_is_still_e0061() {
    let dir = io_scratch();
    let target = abs(&dir, "no_such_file_zzz.txt");
    let out = run_with_dir(&format!("{RW}READ_FILE(\"{target}\")"), &dir);
    let (code, msg) = split_diag(&out);
    assert_eq!(
        code, "E0061",
        "a missing file on the read side must stay E0061, got {out}"
    );
    assert!(
        msg.contains("file not found"),
        "the read-side message must stay as it was, got {msg}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}
''',
        "",
        "std.fs",
        "count_drop",
    ),
]

# 检查器输出的每章读数:「失败口径对拍:<ns> —— 成员 N / 码种 C / (成员,码)对 P / 已钉 X / 缺口 Y」
READING_RE = re.compile(
    r"失败口径对拍:(\S+) —— 成员 (\d+) / 码种 (\d+) / "
    r"\(成员,码\)对 (\d+) / 已钉 (\d+) / 缺口 (\d+)"
)


def parse_pinned_counts(out):
    """从检查器输出里抽出每章的「已钉」数。"""
    counts = {}
    for m in READING_RE.finditer(out):
        counts[m.group(1)] = int(m.group(5))
    return counts


def find_impl(explicit):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for c in IMPL_CANDIDATES:
        c = os.path.normpath(c)
        if os.path.exists(c):
            return c
    return None


def copy_impl_tree(impl_root, dest):
    """把实现仓的 `crates/` 复制到 `<dest>/crates`。

    复制的是**副本** —— 注入②要在这份副本上删用例,绝不能碰真仓。
    另建一个空的 `.git/` 目录:规范仓的检查器遇到「自带 .git 的子目录」
    会整棵子树跳过(CI 里 `upstream/` 就是另一个仓)—— 不这么做,复制进来的
    文档会被卷进第 1–5 项的扫描。
    """
    src = os.path.join(impl_root, "crates")
    if not os.path.isdir(src):
        return False
    os.makedirs(dest, exist_ok=True)
    shutil.copytree(
        src,
        os.path.join(dest, "crates"),
        ignore=shutil.ignore_patterns("target", ".git", "__pycache__"),
    )
    os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", help="参考实现的 wlwl 可执行文件路径")
    args = ap.parse_args()

    impl = find_impl(args.impl)
    if not impl:
        print("未找到参考实现;第 7 项(示例有效性)的阴性对照将被跳过。")
    else:
        print(f"参考实现:{impl}")

    impl_root = check_spec.find_impl_root()
    if not impl_root:
        print(
            "未找到参考实现的源码目录;"
            "第 8 项(失败口径对拍)的两条阴性对照将被跳过。"
        )
    else:
        print(f"参考实现源码:{impl_root}")

    tmp = tempfile.mkdtemp(prefix="wlwl-negctl-")
    work = os.path.join(tmp, "repo")
    shutil.copytree(
        REPO,
        work,
        ignore=shutil.ignore_patterns(".git", ".scratch", "__pycache__"),
    )
    try:
        env = dict(os.environ)
        impl_copy_root = os.path.join(work, "upstream", "impl")
        have_impl = False
        if impl_root:
            have_impl = copy_impl_tree(impl_root, impl_copy_root)
            if have_impl:
                env[check_spec.IMPL_ENV_VAR] = impl_copy_root
        cmd = [sys.executable, os.path.join(work, "script", "check_spec.py")]
        if impl:
            cmd += ["--impl", impl]

        def run_checker():
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                env=env,
            )
            return (r.stdout or "") + (r.stderr or "")

        # 基线:干净工作区的每章「已钉」数。注入②靠它判定「钉住数下降」。
        baseline = parse_pinned_counts(run_checker()) if have_impl else {}

        failures = []
        hit_count = 0
        skip_count = 0
        for desc, root, rel, anchor, injected, expect, mode in FAULTS:
            if mode == "count_drop" and not have_impl:
                print(f"  [跳过] {desc}(没有实现仓副本)")
                skip_count += 1
                continue
            if expect == "运行失败" and not impl:
                print(f"  [跳过] {desc}(无参考实现)")
                skip_count += 1
                continue
            if root == "impl":
                path = os.path.join(impl_copy_root, rel)
            else:
                path = os.path.join(work, rel)
            with io.open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if anchor not in text:
                print(f"  [跳过] {desc}(锚点未命中 —— 目标册尚未成稿)")
                skip_count += 1
                continue
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace(anchor, injected, 1))

            out = run_checker()
            if mode == "keyword":
                hit = expect in out
                detail = ""
            else:
                counts = parse_pinned_counts(out)
                before = baseline.get(expect)
                after = counts.get(expect)
                hit = before is not None and after is not None and after < before
                detail = f" —— {expect} 的钉住数 {before} → {after}(钉住数下降)"
            print(f"  [{'命中' if hit else '漏网'}] {desc}{detail}")
            if hit:
                hit_count += 1
            else:
                if mode == "keyword":
                    failures.append(f"{desc}:期望输出中出现 {expect!r},实际未出现")
                else:
                    failures.append(
                        f"{desc}:期望 {expect} 的「已钉」数下降,"
                        f"实际基线 {baseline.get(expect)} → "
                        f"{parse_pinned_counts(out).get(expect)}"
                    )
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
