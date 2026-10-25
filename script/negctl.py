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

# ── 第 7 项「预期诊断」注解的两条阴性对照的常量(2026-10-25 新增)──────
#
# 「不会报的码」用一个**规范正文里没有**的码;「唯一标记」放在 `ERR` 的载荷里
# ⇒ 诊断消息里带着它 ⇒ 判定「检查器报没报这个块」不会撞上别的错误。
EXPECT_ADD_CODE = "E9998"
EXPECT_DROP_CODE = "E0103"
EXPECT_DROP_MARKER = "negctl_expected_diag"
EXPECT_DROP_PROGRAM = [
    f'LET(a, ERR("{EXPECT_DROP_MARKER}"));',
    "PRINT(a);",
    "0",
]

# (描述, 根, 相对路径, 原文, 注入文本, 期望, 判定方式)
#
#   根       "spec" = 规范仓副本;"impl" = 实现仓副本
#   期望     "keyword" 模式 = 输出里必须出现的关键字;
#            "count_drop" 模式 = 该命名空间的「已钉」数必须比基线少
#   模式     "sample_expect_add" / "sample_expect_drop" = 第 7 项「预期诊断」
#            注解的两条阴性对照 ⇒ 目标块**当场**找(见下面的说明),
#            这两个模式**不用**「相对路径 / 原文 / 注入文本」那三列。
#
# ⚠ 为什么这两条不走「静态锚点 → 注入文本」:目标块会随别的 agent 改示例 /
#   改册首而变样 ⇒ 锚点一旦对不上,注入项就退化成「[跳过]」⇒ 门禁静悄悄地
#   失去鉴别力。所以改成**当场**找块(或当场造块)。
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
        # 挑的是 std.io::INPUT 的 E0060 —— 实测它在实现仓里**只**被
        # `crates/wlwl-std/src/io.rs` 的这一条用例钉住(用例名里含成员名
        # `INPUT` ⇒ 归属口径认得出它),所以删掉它「已钉」数必掉。
        #
        # ⚠ 2026-10-25 换靶:原先挑的是 std.fs::READ_FILE 的 E0061。
        #   v1.0.2 的 W-12(G-19)把 std.fs 的 IO 失败从原生诊断改成 `ERR` 值
        #   ⇒ `E0061` / `E0062` 不再由 std.fs 产生 ⇒ 该章的失败列里**没有码**
        #   ⇒ 「(成员,码)对」为 0 ⇒ 「已钉」数恒为 0 ⇒ 删用例**掉不下去**
        #   ⇒ 阴性对照会「漏网」。换到仍带码的一章(std.io)上。
        "失败口径对拍:删掉实现侧钉住 std.io::INPUT 的用例(E0060)",
        "impl",
        "crates/wlwl-std/src/io.rs",
        '''#[test]
    fn read_line_input_io_error_is_e0060() {
        // A `Read` impl that always fails should produce E0060, not
        // E0001 / E0002.
        //
        // ⚠ **用例名里必须带成员名 `INPUT`**:规范仓检查器的归属口径是
        //   「`#[test] fn` 名里含成员名的蛇形形式」⇒ 名字不含 `input` 就
        //   推不出这条断言属于 `std.io::INPUT`,那条 `E0060` 口径就永远
        //   处于「未钉住」状态。
        let r = FailingRead;
        let err = read_input_line(&mut r.bufreader()).unwrap_err();
        assert_eq!(err.code, ErrorCode::E0060);
        assert!(err.message.contains("INPUT: failed to read stdin"));
    }''',
        "",
        "std.io",
        "count_drop",
    ),
    (
        # 第 7 项注入③:给一个当前**通过**的 wlwl 块加一条 `// expect: <码>`,
        # 那个码**实际不会报** ⇒ 码不符 ⇒ 检查器必须报错。
        "示例有效性(预期诊断注解):给一个通过的 wlwl 块加上不会报的 expect 码",
        "spec",
        "",
        "",
        "",
        EXPECT_ADD_CODE,
        "sample_expect_add",
    ),
    (
        # 第 7 项注入④:删掉一个**故意演示诊断**的块的 `// expect:` 注解
        # ⇒ 该块退出码非零 ⇒ 检查器必须报错。
        "示例有效性(预期诊断注解):删掉一个故意演示诊断块的 expect 注解",
        "spec",
        "",
        "",
        "",
        EXPECT_DROP_MARKER,
        "sample_expect_drop",
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
    # ⚠ `dirs_exist_ok=True`:**CI 里 `upstream/` 就在工作区根** ⇒ 复制规范仓到
    # 临时工作区时,`upstream/` 那一整棵(实现仓)会**跟着被复制进去** ⇒ 目标
    # `upstream/impl/crates` **已经存在** ⇒ 不带这个参数就是 FileExistsError
    # (2026-10-22 在 CI 上踩过:negctl 直接崩,门禁红)。
    shutil.copytree(
        src,
        os.path.join(dest, "crates"),
        ignore=shutil.ignore_patterns("target", ".git", "__pycache__"),
        dirs_exist_ok=True,
    )
    os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
    return True


# ⚠ 第 0 项:**提取口径自检** —— 检查器认行内单测时,函数体必须按**括号配平**取
# (不是「截到第一个整行只有 `}`」)⇒ 断言写在**嵌套块之后**时也必须看得见。
# 老口径会在嵌套块的结尾截断 ⇒ 那个 pin **不计数** ⇒ 缺口清单**漏报**。
#
# ⚠ 这条自检的形状就是 V-05 那一笔里 `io.rs` 踩过的坑(断言前面有
# `impl Read for …` ⇒ 老口径看不见 ⇒ 被误判成「钉不住」)。
EXTRACTOR_SNIPPET = '''#[test]
fn sample_case() {
    struct Helper;
    impl Helper {
        fn noop(&self) {}
    }
    let x = 1;
    assert_eq!(x, 1, "the assert after a nested block must be visible");
}
'''


def selfcheck_extractor():
    """返回 (是否通过, 说明)。"""
    seen = [body for _name, body in check_spec._iter_test_fns(EXTRACTOR_SNIPPET)]
    if not seen:
        return False, "检查器没有产出任何行内单测"
    if not any("assert_eq!" in b for b in seen):
        return (
            False,
            "嵌套块之后的断言没被抽出来 ⇒ 提取口径回退成了「截到整行 }」",
        )
    return True, "嵌套块之后的断言可见"


# ── 第 7 项「预期诊断」注解的两条阴性对照的实现 ───────────────────────
#
# 目标块**当场**找 / 当场造 —— 不用静态锚点(理由见 FAULTS 上面的说明)。

FENCE_RE = re.compile(r"^\s*```(\w*)\s*$")


def scan_code_blocks(text):
    """(序号, 语言, 围栏内首行的行号(0 基), 行列表) —— 文本里的围栏块。"""
    out = []
    lines = text.split("\n")
    cur, start, lang = None, None, None
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m:
            if cur is None:
                cur, start, lang = [], i + 1, m.group(1)
            else:
                out.append((len(out) + 1, lang, start, cur))
                cur, start, lang = None, None, None
            continue
        if cur is not None:
            cur.append(line)
    if cur is not None:
        out.append((len(out) + 1, lang, start, cur))
    return out


def spec_files_in(work):
    """副本里的规范文本文件 —— 与检查器**同一套**遍历口径。

    检查器用的是 `check_spec.all_md_files()`(它按 `check_spec.REPO` 走);
    这里临时把 `REPO` 指到副本上再借它一把 ⇒ 两边的文件集合与顺序一致
    ⇒ 块序号也对得上。
    """
    old = check_spec.REPO
    try:
        check_spec.REPO = work
        return check_spec.all_md_files()
    finally:
        check_spec.REPO = old


def wlwl_blocks_in(work):
    """(rel, 绝对路径, 围栏序号, wlwl 序号, 围栏内首行行号, 行列表)。"""
    out = []
    for p in spec_files_in(work):
        rel = os.path.relpath(p, work).replace("\\", "/")
        with io.open(p, "r", encoding="utf-8") as f:
            text = f.read()
        no = 0
        for ordinal, lang, start, lines in scan_code_blocks(text):
            if lang != "wlwl":
                continue
            no += 1
            out.append((rel, p, ordinal, no, start, lines))
    return out


def run_sample(impl, lines, timeout=60):
    """跑一个 wlwl 块。返回 (退出码, stderr)。"""
    src = "\n".join(lines)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".wll", delete=False, encoding="utf-8", newline="\n"
    ) as tf:
        tf.write(src + "\n")
        tmp = tf.name
    try:
        r = subprocess.run(
            [impl, "run", tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return r.returncode, (r.stderr or "")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def line_offset(text, lineno):
    """第 `lineno` 行(0 基)首字符在 text 里的偏移。"""
    off = 0
    for _ in range(lineno):
        nl = text.find("\n", off)
        if nl < 0:
            return len(text)
        off = nl + 1
    return off


def insert_lines(text, lineno, new_lines):
    """在第 `lineno` 行(0 基)之前插入若干行(沿用原文件的换行风格)。"""
    off = line_offset(text, lineno)
    eol = "\r\n" if "\r\n" in text else "\n"
    return text[:off] + "".join(ln + eol for ln in new_lines) + text[off:]


def delete_line(text, lineno):
    """删掉第 `lineno` 行(0 基)(连同它的换行)。"""
    off = line_offset(text, lineno)
    nl = text.find("\n", off)
    if nl < 0:
        return text[:off]
    return text[:off] + text[nl + 1:]


def sample_expect_add(work, impl, run_checker):
    """① 给一个当前**通过**的 wlwl 块加一条 `// expect: <不会报的码>`。

    ⇒ 码不符 ⇒ 检查器必须报错。返回 (是否命中, 说明, 检查器输出)。
    """
    target = None
    for rel, path, _ordinal, _no, start, lines in wlwl_blocks_in(work):
        if not lines:
            continue
        if any(check_spec.EXPECT_LINE_RE.match(ln) for ln in lines):
            continue
        rc, _err = run_sample(impl, lines)
        if rc == 0:
            target = (rel, path, start)
            break
    if not target:
        return False, " —— 找不到当前通过的 wlwl 块", ""
    rel, path, start = target
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(insert_lines(text, start, [f"// expect: {EXPECT_ADD_CODE}"]))
        out = run_checker()
    finally:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    hit = EXPECT_ADD_CODE in out
    return hit, f" —— 期望 {rel} 报出 {EXPECT_ADD_CODE}", out


def sample_expect_drop(work, impl, run_checker):
    """② 删掉一个**故意演示诊断**的块的 `// expect:` 注解。

    ⇒ 该块退出码非零 ⇒ 检查器必须报错。返回 (是否命中, 说明, 检查器输出)。

    目标块**当场造**(静态目标会随别的 agent 改示例而失效):在副本里追加一个
    带注解的、故意失败的 wlwl 块,再把它的注解删掉 —— 这正是「删掉注解」后
    的状态。两步都验:
      ① 带注解 ⇒ 检查器**不得**报它(先证伪「假红」);
      ② 删掉注解 ⇒ 同一个块退出码非零 ⇒ 检查器必须报错。
    """
    # ⚠ 落点必须是「**副本**里的」+「**检查器会扫的**」文件 —— 两个条件都要满足:
    #   ① `check_spec.spec_files()` = 第 7 项真正会跑的那些册(docs/spec + docs/stdlib);
    #      放进 `README.md` 的话那个块**永远不会被跑** ⇒ 注入项静悄悄地失去鉴别力。
    #   ② 但它给的是**原仓**的绝对路径 ⇒ 必须换算到**副本** ——
    #      否则就是往**真仓**里写(negctl 绝不能碰真仓),而且真仓的改动
    #      不会被这次检查器进程看见 ⇒ 注入同样静默失效。
    cands = [
        os.path.join(work, os.path.relpath(p, check_spec.REPO))
        for p in check_spec.spec_files()
    ]
    if not cands:
        return False, " —— 副本里没有检查器会扫的册文件可承载注入块", ""
    host = cands[0]
    with io.open(host, "r", encoding="utf-8") as f:
        text = f.read()

    # 这个块在参考实现上**确实**失败并报 E0103 —— 否则控制项是空的。
    rc, err = run_sample(impl, EXPECT_DROP_PROGRAM)
    codes = [c for c, _m in check_spec.DIAG_RE.findall(err)]
    if rc == 0 or EXPECT_DROP_CODE not in codes:
        return (
            False,
            f" —— 注入块在参考实现上没有报 {EXPECT_DROP_CODE}(rc={rc},实际码 {codes})",
            "",
        )

    block = (
        "```wlwl\n"
        f"// expect: {EXPECT_DROP_CODE}\n"
        + "\n".join(EXPECT_DROP_PROGRAM)
        + "\n```"
    )
    annotated = text + "\n" + block + "\n"
    # 注解行在**围栏之后**一行 ⇒ 偏移 = 原文件的行数 + 1。
    ann_line = len(text.split("\n")) + 1
    if "// expect:" not in annotated.split("\n")[ann_line]:
        return False, " —— 注入块的注解行定位失败", ""
    try:
        with io.open(host, "w", encoding="utf-8", newline="") as f:
            f.write(annotated)
        out_ok = run_checker()
        if EXPECT_DROP_MARKER in out_ok:
            return False, " —— 带注解的块被判红了(注解机制本身有问题)", out_ok
        with io.open(host, "w", encoding="utf-8", newline="") as f:
            f.write(delete_line(annotated, ann_line))
        out = run_checker()
    finally:
        with io.open(host, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    hit = EXPECT_DROP_MARKER in out
    return hit, f" —— 期望删掉注解后检查器报出 {EXPECT_DROP_MARKER}", out


SAMPLE_EXPECT_MODES = {
    "sample_expect_add": sample_expect_add,
    "sample_expect_drop": sample_expect_drop,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", help="参考实现的 wlwl 可执行文件路径")
    args = ap.parse_args()

    impl = find_impl(args.impl)
    if not impl:
        print("未找到参考实现;第 7 项(示例有效性)的阴性对照将被跳过。")
    else:
        print(f"参考实现:{impl}")

    # ⚠ 必须把**原始规范仓**的位置传进去:本模块是在**临时副本**里跑的
    # (`import check_spec` 拿到的 REPO 是副本)⇒ 用副本的位置找实现仓必然找不到
    # ⇒ 第 8 项整项跳过 ⇒ 它那两条阴性对照会「漏网」。2026-10-22 在 CI 上踩过。
    impl_root = check_spec.find_impl_root(base=REPO)
    if not impl_root:
        print(
            "未找到参考实现的源码目录;"
            "第 8 项(失败口径对拍)的两条阴性对照将被跳过。"
        )
    else:
        print(f"参考实现源码:{impl_root}")

    tmp = tempfile.mkdtemp(prefix="wlwl-negctl-")
    work = os.path.join(tmp, "repo")
    # ⚠ **排除 `upstream/`**:CI 里它就是实现仓(几百 MB)⇒ 复制进临时工作区既慢
    # 又会让 `upstream/impl/crates` 预先存在(见 `copy_impl_tree` 的说明)。
    # 实现仓的源码由 `copy_impl_tree` 单独复制进来。
    shutil.copytree(
        REPO,
        work,
        ignore=shutil.ignore_patterns("upstream", ".git", ".scratch", "__pycache__"),
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

        # 第 0 项:提取口径自检(不是注入 ⇒ 不动工作区)。
        ok, why = selfcheck_extractor()
        print(f"  [{'命中' if ok else '漏网'}] 提取口径自检: {why}")
        if ok:
            hit_count += 1
        else:
            failures.append(f"提取口径自检失败:{why}")

        for desc, root, rel, anchor, injected, expect, mode in FAULTS:
            if mode == "count_drop" and not have_impl:
                print(f"  [跳过] {desc}(没有实现仓副本)")
                skip_count += 1
                continue
            if mode in SAMPLE_EXPECT_MODES:
                if not impl:
                    print(f"  [跳过] {desc}(无参考实现)")
                    skip_count += 1
                    continue
                hit, detail, out = SAMPLE_EXPECT_MODES[mode](work, impl, run_checker)
                print(f"  [{'命中' if hit else '漏网'}] {desc}{detail}")
                if hit:
                    hit_count += 1
                else:
                    failures.append(f"{desc}:{detail.replace(' —— ', '', 1)}")
                    print("     ---- 检查器输出 ----")
                    print("     " + out.replace("\n", "\n     ")[:1500])
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
            + f"(共 {len(FAULTS)} 项注入 + 1 项提取口径自检)。"
        )
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
