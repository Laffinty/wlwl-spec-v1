#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WLWL v1.0 规范文本检查器。

实现一致性册 §6.1 里可机械判定的检查:

  1 标点契约       —— 正文不得出现全角 , : ; ( )
  2 跨册引用有效性 —— § 引用指向确实存在的章节
  3 诊断码唯一性与归属 —— 码在语言核心册 §11.2 / §11.3 只定义一次;正文提到的每个码都有归属
  4 规范性用语加粗 —— 必须 / 不得 / 应当 / 可以 作规范关键词时必须加粗
  5 实现注分离     —— 实现注段落必须独立成段
  6 成员面双向对拍 —— 注册镜像册附录 A ↔ 各章成员表:成员集合相等 + `[stub]` 标记一致
  7 示例有效性     —— wlwl 代码块必须在参考实现上运行且退出码为 0
  8 失败口径对拍   —— 标准库册每一章的失败口径在参考实现上逐条对齐(一致性册 §6.2 的 V-05)

另有两项实现层面的检查(章节号唯一性、相对链接有效)不在 §6.1 的七项里,
但同样以错误级上报。

**编号空间**:语言三册共用一个编号空间(§1–§19,§0.x 为各册自己的导言);
标准库各册共用另一个编号空间(§1–§22)。两套空间彼此独立,靠册名消歧。
因此「标准库册 §5.2」与「语言核心册 §5.2」是不同处;不带册名的裸引用
按「当前册 → 语言册 → 全部册」的顺序解析。

用法:
    python script/check_spec.py                       # 检查本仓库
    python script/check_spec.py --impl <wlwl 可执行文件>

第 7 项需要参考实现;未给出 --impl 时跳过并报为「已跳过」。
退出码: 0 = 无错误(可含提示); 1 = 有错误。
"""

import argparse
import io
import os
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NORMATIVE_DIRS = ["docs/spec", "docs/stdlib"]
# 外部审计原文的归档件:逐字保留审计方的原始写法(全角中文标点等),
# **不受**本仓的标点契约约束 —— 否则归档动作本身就会把原文改写掉。
# 判据:docs/audit/ 下以「第三方审计报告」开头的文件。
AUDIT_ARCHIVE_PREFIX = "第三方审计报告"
SKIP_FILES = []  # 额外的逐文件豁免(默认空)

FULLWIDTH = {
    "\uff0c": ",",
    "\uff1a": ":",
    "\uff1b": ";",
    "\uff08": "(",
    "\uff09": ")",
}

# 不可见控制字符(除 \t \n \r 外)。它们在 diff 与代码评审里**完全看不出来**,
# 却会破坏文件比对、渲染与部分工具的解析 —— 必须当成错误。
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 册名 → 册文件关键字。用于「带册名的跨册引用」。
BOOK_ALIASES = {
    "总纲": "overview",
    "语言核心册": "language",
    "语言册": "language",
    "对象与行为类型册": "objects",
    "对象册": "objects",
    "并发册": "concurrency",
    "一致性册": "conformance",
    "标准库册": "stdlib",
    "标准库·核心册": "stdlib-core",
    "标准库·文本册": "stdlib-text",
    "标准库·系统册": "stdlib-system",
    "标准库·安全册": "stdlib-security",
    "标准库·官方包册": "stdlib-official",
    "标准库·注册镜像册": "stdlib-registry",
    "路线图册": "roadmap",
    "审计处置表": "audit",
}

# 表格行的**首个单元格**若恰好是下列某个册标签,该行内的裸 § 引用按该册解析。
# 精确匹配(去粗体标记后全等),不做包含匹配 —— 包含匹配会把
# 「并发原语」之类的普通首格误当成册标签。
ROW_BOOKS = {
    "总纲": "overview",
    "语言核心": "language",
    "语言核心册": "language",
    "语言册": "language",
    "对象与行为类型": "objects",
    "对象与行为类型册": "objects",
    "对象册": "objects",
    "并发": "concurrency",
    "并发册": "concurrency",
    "一致性": "conformance",
    "一致性册": "conformance",
    "标准库": "stdlib",
    "标准库·核心": "stdlib-core",
    "标准库·核心册": "stdlib-core",
    "标准库·文本": "stdlib-text",
    "标准库·文本册": "stdlib-text",
    "标准库·系统": "stdlib-system",
    "标准库·系统册": "stdlib-system",
    "标准库·安全": "stdlib-security",
    "标准库·安全册": "stdlib-security",
    "标准库·官方包": "stdlib-official",
    "标准库·官方包册": "stdlib-official",
    "标准库·注册镜像": "stdlib-registry",
    "标准库·注册镜像册": "stdlib-registry",
    "路线图": "roadmap",
    "路线图册": "roadmap",
    "审计": "audit",
    "审计处置表": "audit",
}

SECTION_RE = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+)*)\s+\S")
NORM_WORDS = ["必须", "不得", "应当", "可以"]
BOLD_NORM_RE = re.compile(r"\*\*(必须|不得|应当|可以)\*\*")
IMPL_NOTE_RE = re.compile(r"^\*\*实现注(?:（非规范）|\(非规范\))?\*\*")


def _span(lo, hi):
    return {str(i) for i in range(lo, hi + 1)}


# 各册的**规划节号范围**。落在范围内但尚未成稿 ⇒ 提示;落在范围外 ⇒ 错误。
PLANNED_SCOPE = {
    "overview": _span(0, 0) | {f"0.{i}" for i in range(1, 12)},
    "language": _span(1, 12) | _span(18, 19),
    "objects": _span(13, 16),
    "concurrency": _span(17, 17),
    "conformance": _span(1, 7),
    "stdlib-core": _span(0, 0) | {f"0.{i}" for i in range(1, 11)} | _span(1, 22),
    "stdlib-text": _span(0, 0) | {f"0.{i}" for i in range(1, 11)} | _span(1, 22),
    "stdlib-system": _span(0, 0) | {f"0.{i}" for i in range(1, 11)} | _span(1, 22),
    "stdlib-security": _span(0, 0) | {f"0.{i}" for i in range(1, 11)} | _span(1, 22),
    "stdlib-official": _span(0, 0) | {f"0.{i}" for i in range(1, 11)} | _span(1, 22),
    "stdlib-registry": _span(0, 0) | {f"0.{i}" for i in range(1, 11)} | _span(1, 22),
    "roadmap": _span(1, 4),
    "audit": _span(1, 4),
}


def in_scope(key, num):
    scope = PLANNED_SCOPE.get(key)
    if scope is None:
        return False
    if num in scope:
        return True
    # 带小节号时,其顶层章节号在范围内即算在范围内。
    return num.split(".")[0] in scope


QUAL_RE = re.compile(
    r"(?P<qual>"
    + "|".join(sorted(BOOK_ALIASES, key=len, reverse=True))
    + r")(?:\]\([^)]*\))?\s*\(?\s*§(?P<num>\d+(?:\.\d+)*)"
)
BARE_RE = re.compile(r"(?<![\w.])(?P<num>§\d+(?:\.\d+)*)")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_code_blocks(text):
    out_lines, blocks, cur, cur_lang = [], [], None, None
    for line in text.split("\n"):
        m = re.match(r"^\s*```(\w*)\s*$", line)
        if m:
            if cur is None:
                cur, cur_lang = [], m.group(1)
            else:
                blocks.append((cur_lang, "\n".join(cur)))
                cur, cur_lang = None, None
            continue
        if cur is not None:
            cur.append(line)
        else:
            out_lines.append(line)
    if cur is not None:
        blocks.append((cur_lang, "\n".join(cur)))
    return "\n".join(out_lines), blocks


def spec_files():
    out = []
    for d in NORMATIVE_DIRS:
        base = os.path.join(REPO, d)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.endswith(".md"):
                out.append(os.path.join(base, name))
    return out


def _ignored_dir_names():
    """要跳过的目录名。

    默认集之外,还可以用 `WLWL_SPEC_IGNORE_DIRS` 追加(逗号分隔)。
    """
    names = {
        ".git",
        "target",
        "node_modules",
        ".scratch",
        "__pycache__",
    }
    extra = os.environ.get("WLWL_SPEC_IGNORE_DIRS", "")
    for n in extra.split(","):
        n = n.strip()
        if n:
            names.add(n)
    return names


def all_md_files():
    """本仓的 .md 文件。

    ⚠ **不进入嵌套的 git 仓库。** CI 会把上游实现仓 checkout 到工作区里
    (规范仓的 workflow:`upstream/`),那是**另一个仓库**的文档 —— 它的体例
    (全角中文标点等)与本仓的规范文本不同,扫它只会制造 1400+ 条假阳性。
    判据:某个子目录**自带 `.git`** ⇒ 它是另一个仓库的根 ⇒ 整棵子树跳过。
    """
    ignore = _ignored_dir_names()
    out = []
    for root, dirs, names in os.walk(REPO):
        pruned = []
        for d in dirs:
            if d in ignore:
                continue
            sub = os.path.join(root, d)
            if sub != REPO and os.path.exists(os.path.join(sub, ".git")):
                # 另一个 git 仓库的根 —— 整棵子树不进检查范围。
                continue
            pruned.append(d)
        dirs[:] = pruned
        for n in sorted(names):
            if n.endswith(".md"):
                p = os.path.join(root, n)
                rel = os.path.relpath(p, REPO).replace("\\", "/")
                if rel in SKIP_FILES:
                    continue
                if (
                    rel.startswith("docs/audit/")
                    and os.path.basename(rel).startswith(AUDIT_ARCHIVE_PREFIX)
                ):
                    continue
                out.append(p)
    return out


def book_key(path):
    name = os.path.basename(path)
    # 长键优先:否则 "wlwl-v1.0-stdlib-core.md" 会被 "stdlib" 抢先命中。
    for key in sorted(BOOK_ALIASES.values(), key=len, reverse=True):
        if key in name:
            return key
    return name


def collect_sections(files):
    """返回 (每册的 {节号: 相对路径}, 全局并集)。"""
    per_book, union = {}, {}
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        key = book_key(p)
        secs = per_book.setdefault(key, {})
        text, _ = strip_code_blocks(read(p))
        for line in text.split("\n"):
            m = SECTION_RE.match(line)
            if not m:
                continue
            num = m.group(1)
            if num in secs and secs[num] != rel:
                print(f"  [重复定义] {key} 内 §{num} 出现于 {secs[num]} 与 {rel}")
            secs[num] = rel
            union.setdefault(num, []).append((key, rel))
    return per_book, union


def gh_error(path, line, message):
    """在 GitHub Actions 里把一条错误发成 **annotation**。

    为什么要这一步:CI 日志要鉴权才能读(403),而 annotation 走 check-runs API
    就能拿到 —— 规范文本的门禁失败必须**在 UI 上直接可见**,而不只是躺在日志里。
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    safe = message.replace("%", "%25").replace("\r", " ").replace("\n", " ")
    print(f"::error file={path},line={line}::{safe}")


def check_punctuation(files, errors):
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        text, _ = strip_code_blocks(read(p))
        for i, line in enumerate(text.split("\n"), 1):
            m = CONTROL_RE.search(line)
            if m:
                errors.append(
                    f"{rel}:{i} 不可见控制字符 U+{ord(m.group(0)):04X} —— "
                    f"它在 diff 里看不出来,却会破坏比对与解析: {line.strip()[:60]!r}"
                )
            for ch, name in FULLWIDTH.items():
                if ch in line:
                    errors.append(
                        f"{rel}:{i} 标点契约:全角 {name!r} 出现在正文 —— {line.strip()[:70]}"
                    )


def resolve(qual, num, cur_key, per_book, union, ctx_key=None):
    """返回 (状态, 解析到的册)。状态 ∈ {ok, not_yet, missing, needs_qual}。

    `cur_key` 是**本册**(同册引用不需要册名);
    `ctx_key` 是本行最近一个带册名的引用所确定的册,只用于消歧。
    """
    if qual:
        key = BOOK_ALIASES.get(qual)
        if key is None:
            return "missing", None
        if key == "stdlib":
            for k, secs in per_book.items():
                if k.startswith("stdlib") and num in secs:
                    return "ok", k
            if any(in_scope(k, num) for k in per_book if k.startswith("stdlib")):
                return "not_yet", None
            return "missing", None
        if num in per_book.get(key, {}):
            return "ok", key
        if in_scope(key, num):
            return "not_yet", None
        return "missing", None

    # 1) 同册优先:本册自己的章节,裸写即可。
    if num in per_book.get(cur_key, {}):
        return "ok", cur_key
    # 2) 上下文册:行内最近一个带册名的引用所确定的册。
    if ctx_key and ctx_key != cur_key:
        if num in per_book.get(ctx_key, {}):
            return "ok", ctx_key
        if ctx_key == "stdlib":
            for k in per_book:
                if k.startswith("stdlib") and num in per_book[k]:
                    return "ok", k
    # 3) 标准库各册的 §1–§22 是**它们自己的**编号空间 —— 在标准库册里,
    #    §N 一律指本册的 §N,不去和语言册的 §N 混比。
    if cur_key.startswith("stdlib"):
        top = num.split(".")[0]
        if top.isdigit() and 1 <= int(top) <= 22:
            secs = per_book.get(cur_key, {})
            if "." in num and top in secs:
                # 本册有这个顶层章节,但没有这个小节 ⇒ 引用了不存在的子节。
                return "missing", cur_key
            return "ok", cur_key
    # 4) 兜底:别册。
    for k in per_book:
        if num in per_book[k]:
            hits = [k2 for k2 in per_book if num in per_book[k2]]
            if len(hits) == 1:
                # 该节号只存在于唯一一册 —— 裸写**不歧义**,读者能唯一确定。
                return "ok", hits[0]
            return "needs_qual", k
    for k in per_book:
        if in_scope(k, num):
            return "not_yet", None
    return "missing", None


def row_book_key(line):
    """表格行内若出现某个册标签,该行内的裸 § 引用按该册解析。

    册标签可以出现在该行的**任意**单元格 —— 规范矩阵的列序是
    「主题 | 归属册 | 节号」,册名在第二格;待验证清单的列序是
    「编号 | 命题 | 归属册」,册名在第三格且**带节号**(如「语言核心册 §18」)。

    标签的识别:单元格**以**册标签开头,且其后是行尾或空格+§。
    不做纯包含匹配 —— 那会把「并发原语」之类的普通单元格误当成册标签。
    """
    s = line.strip()
    if not s.startswith("|"):
        return None
    for cell in split_cells(s)[1:]:
        label = cell.strip().strip("*").strip()
        for name, key in ROW_BOOKS.items():
            if label == name or label.startswith(name + " §") or label.startswith(name + " §"):
                return key
    return None


def check_references(files, per_book, union, errors, warns):
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        cur_key = book_key(p)
        text, _ = strip_code_blocks(read(p))
        for i, line in enumerate(text.split("\n"), 1):
            row_key = row_book_key(line)
            line_key = row_key or cur_key
            # 引号里的是**举例**,不是真引用 —— 整段挖空,不做扫描。
            scan = _blank_quoted(QUOTE_RE.sub("\x00", line))
            scan = _blank_quoted(DQUOTE_RE.sub("\x00", scan))
            scan = _blank_quoted(re.sub(r'"[^"]*"', "\x00", scan))
            scan = scan.replace("\x00", " ")

            # 上下文必须**按位置**求值:行内有多个册名时,一个裸引用归到
            # **它前面**最近的那个册名,而不是行内最后一个。
            # 例:「总纲 §0.6、§0.7 → 一致性册全文 → 语言核心册 §1–§9」
            # 里的 §0.7 属总纲,§11 属语言核心册。
            events = []
            for m in QUAL_RE.finditer(scan):
                events.append((m.start(), 0, m))
            for m in BARE_RE.finditer(scan):
                if any(e[2].start() <= m.start() < e[2].end() for e in events):
                    continue
                events.append((m.start(), 1, m))
            events.sort(key=lambda e: e[0])

            ctx_key = line_key
            for _pos, kind, m in events:
                if kind == 0:
                    qk = BOOK_ALIASES.get(m.group("qual"))
                    if qk == "stdlib":
                        for k, secs in per_book.items():
                            if k.startswith("stdlib") and m.group("num") in secs:
                                qk = k
                                break
                    if qk:
                        ctx_key = qk
                    status, key = resolve(
                        m.group("qual"), m.group("num"), line_key, per_book, union
                    )
                    num = m.group("num")
                    if status == "missing":
                        errors.append(
                            f"{rel}:{i} 引用 {m.group('qual')} §{num} 指向不存在的章节"
                        )
                    elif status == "not_yet":
                        warns.append(
                            f"{rel}:{i} 引用 {m.group('qual')} §{num} 指向尚未成稿的章节"
                        )
                    continue

                num = m.group("num")[1:]
                tail = scan[m.end():m.end() + 1]
                head = scan[max(0, m.start() - 1):m.start()]
                status, key = resolve(None, num, line_key, per_book, union, ctx_key)
                if status == "missing":
                    errors.append(f"{rel}:{i} 引用 §{num} 指向不存在的章节")
                elif status == "not_yet":
                    warns.append(f"{rel}:{i} 引用 §{num} 指向尚未成稿的章节")
                elif status == "needs_qual" and row_key is None and is_l_norm(p):
                    if tail in ("–", "-") or head in ("–", "-"):
                        continue
                    warns.append(
                        f"{rel}:{i} 裸引用 §{num} 实际落在 {key} 册,跨册引用应当带册名"
                    )


CODE_SPAN_RE = re.compile(r"`[^`]*`")
BOLD_SPAN_RE = re.compile(r"\*\*[^*]+\*\*")
QUOTE_RE = re.compile(r"「[^」]*」")
DQUOTE_RE = re.compile(r"[“][^”]*[”]")


def _blank_quoted(s):
    """把 \x00 标记段替换成等长空格 —— 保留列号,便于定位。"""
    return re.sub(r"\x00[^\x00]*\x00", lambda m: " " * len(m.group(0)), s)
HEADING_RE = re.compile(r"^#{2,4}\s+(.*)$")
# 诊断码的**定义形态**:表格行的首个单元格恰好是该码。
DIAG_DEF_RE = re.compile(r"^\|\s*`([EW]\d{4})`\s*\|")
# 成员名的形态:大写字母、数字、下划线。
MEMBER_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\b")
# 命名空间形态:小写点分。
NS_RE = re.compile(r"^\|\s*`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)?)`\s*\|")

# 规范用语检查只管 L-规范册;README / 路线图 / 审计处置表是非规范的支撑文档。
L_NORM_DIRS = ("docs/spec", "docs/stdlib")


def is_l_norm(path):
    rel = os.path.relpath(path, REPO).replace("\\", "/")
    return rel.startswith(L_NORM_DIRS)


def check_bold_normatives(files, warns):
    """规范关键词必须加粗。

    判定前先剥掉整段加粗、再剥行内代码跨 —— 顺序很重要:若先剥代码跨,
    `**`wlwl`**` 会留下 `****`,加粗正则随即跨段匹配,吃掉后面的 `**`。
    再剥掉「」引用 —— 引号里的词是被提及,不是在使用。
    「规范性用语」定义段本身跳过:那里在解释关键词,不是在使用它。
    """
    for p in files:
        if not is_l_norm(p):
            continue
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        text, _ = strip_code_blocks(read(p))
        heading = ""
        for i, line in enumerate(text.split("\n"), 1):
            h = HEADING_RE.match(line)
            if h:
                heading = h.group(1)
            s = line.strip()
            if not s or IMPL_NOTE_RE.match(s):
                continue
            if "规范性用语" in heading or "条款关键词加粗" in heading:
                continue
            stripped = QUOTE_RE.sub("", BOLD_SPAN_RE.sub("", s))
            stripped = CODE_SPAN_RE.sub("", stripped)
            for w in NORM_WORDS:
                for _ in re.finditer(re.escape(w), stripped):
                    warns.append(f"{rel}:{i} 规范用语 {w!r} 未加粗 —— {s[:70]}")


def check_impl_note_separation(files, warns):
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        text, _ = strip_code_blocks(read(p))
        for i, line in enumerate(text.split("\n"), 1):
            s = line.strip()
            if not IMPL_NOTE_RE.match(s):
                continue
            for w in NORM_WORDS:
                if w in s and not re.search(r"\*\*" + w + r"\*\*", s):
                    warns.append(
                        f"{rel}:{i} 实现注段落含规范关键词 {w!r},可能未与规范条款分离"
                    )


def check_links(errors):
    for p in all_md_files():
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        text = read(p)
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target = target.split("#")[0].strip()
            if not target:
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(p), target))
            if not os.path.exists(resolved):
                errors.append(f"{rel} 相对链接指向不存在的文件: {target}")


CODE_RE = re.compile(r"(?<![0-9A-Za-z_])([EW])(\d{4})(?![0-9A-Za-z_])")
# 范围行的两端**各自带反引号**:`E0010`–`E0013`。不带反引号会漏掉整段范围。
CODE_RANGE_RE = re.compile(
    r"(?<![0-9A-Za-z_])`?([EW])(\d{4})`?\s*[-–—~]{1,2}\s*`?(?:[EW])?(\d{4})`?(?![0-9A-Za-z_])"
)


def _codes_in_text(text):
    """抽出正文里提到的全部诊断码(含 `A0000`–`B0000` 区间的展开)。"""
    out = set()
    for m in CODE_RANGE_RE.finditer(text):
        pre, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        if hi >= lo and hi - lo < 500:
            for i in range(lo, hi + 1):
                out.add(f"{pre}{i:04d}")
    for m in CODE_RE.finditer(text):
        out.add(m.group(0))
    return out


def check_diag_attribution(files, errors, warns):
    """规范正文提到的每个诊断码,**必须**在语言核心册的码表里有归属。

    方向一(规范 → 码表):码表里没有的码,说明规范**提了它却没定义它**。
    这与「方向二(码表 → 规范)」互补 —— 后者由实现仓的守卫承担,跨仓。
    """
    core = os.path.join(REPO, "docs", "spec", "wlwl-v1.0-language.md")
    if not os.path.exists(core):
        return
    core_text, _ = strip_code_blocks(read(core))
    # 码表范围:§11.2 与 §11.3 两节。表里可能**并列**多个码
    # (`| `E0001` / `E0002` / `E0003` |`),所以按「表格行里出现的全部码」收集,
    # 而不是只认「首格恰好一个码」的行。
    start = core_text.find("### 11.2 错误码")
    end = core_text.find("### 11.4", start) if start >= 0 else -1
    if start < 0 or end < 0:
        return
    table = core_text[start:end]
    defined = set()
    for line in table.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        defined |= _codes_in_text(s)
    # 规范**自己**声明「故意不存在」的码(§11.2 里明写)不算缺口。
    # 逐码扫,不能用 finditer —— 一个码的匹配会把并列的另一个码**吞进自己的跨度**,
    # 漏掉它(`E0055` 与 `E0057` 就是这样漏掉后者的)。
    deliberately = set()
    for m in re.finditer(r"`([EW]\d{4})`", table):
        tail = table[m.end(): m.end() + 40]
        if "不存在" in tail:
            deliberately.add(m.group(1))

    for p in files:
        if not is_l_norm(p):
            continue
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        text, _ = strip_code_blocks(read(p))
        for code in sorted(_codes_in_text(text)):
            if code in defined or code in deliberately:
                continue
            errors.append(
                f"{rel} 正文提到诊断码 {code},但语言核心册 §11.2 的码表与注册码清单里没有它 —— "
                f"规范提了它却没定义它"
            )


def check_diag_uniqueness(files, errors, warns):
    """诊断码的归属与唯一性。

    诊断码表的归属是**语言核心册 §11.2 / §11.3**。因此:
      · 同一个码在语言册里被定义两次 → 错误;
      · 别的册在表格首格位置「定义」了一个码 → 错误(该码的归属不在那儿);
      · 正文里**引用**一个码不算定义(只认表格首格)。
    """
    defined = {}
    for p in files:
        if not is_l_norm(p):
            continue
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        text, _ = strip_code_blocks(read(p))
        for i, line in enumerate(text.split("\n"), 1):
            m = DIAG_DEF_RE.match(line.strip())
            if not m:
                # 首个单元格可能并列多个码:`E0001` / `E0002` / `E0003`
                cells = line.strip().split("|")
                if len(cells) < 2:
                    continue
                first = cells[1]
                if not first.strip().startswith("`"):
                    continue
                codes = re.findall(r"`([EW]\d{4})`", first)
                if not codes:
                    continue
                m = None
            else:
                codes = [m.group(1)]
            for code in codes:
                if code in defined:
                    errors.append(
                        f"诊断码唯一性:`{code}` 在 {defined[code][0]}:{defined[code][1]} "
                        f"与 {rel}:{i} 重复定义"
                    )
                else:
                    defined[code] = (rel, i)
    # 归属:除语言册外,任何册都不该在表格首格定义诊断码。
    for code, (rel, i) in defined.items():
        if "wlwl-v1.0-language.md" not in rel:
            errors.append(
                f"诊断码归属:`{code}` 在 {rel}:{i} 被定义 —— 诊断码表的归属是语言核心册 §11.2/§11.3"
            )


def split_cells(row):
    """GFM 感知的表格行切分 —— **尊重 `\\|` 转义**。

    朴素的 split("|") 会把签名格里的转义竖线(例如
    `ENV_GET(...) -> STRING \\| NULL`)切成两半,导致该成员抽不出来,
    成员面对拍会误报「仅注册镜像有」。这是实测踩到的。
    """
    cells, cur, i = [], [], 0
    while i < len(row):
        c = row[i]
        if c == "\\" and i + 1 < len(row) and row[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if c == "|":
            cells.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    cells.append("".join(cur))
    return cells


def _members_from_signature_cell(cell):
    """从成员表的首格(签名)里取成员名。"""
    names = set()
    for tok in re.findall(r"`([^`]+)`", cell):
        m = re.match(r"([A-Z][A-Z0-9_]*)\s*\(", tok)
        if m:
            names.add(m.group(1))
    return names


def check_member_surface(files, errors, warns):
    """成员面双向对拍:注册镜像册 ↔ 各标准库册的成员表。

    两侧都存在该命名空间时,成员集合**必须**相等;
    只有一侧存在时(另一册尚未成稿)只报提示。
    """
    registry = os.path.join(REPO, "docs", "stdlib", "wlwl-v1.0-stdlib-registry.md")
    if not os.path.exists(registry):
        warns.append("成员面对拍:注册镜像册尚不存在,已跳过")
        return

    reg = {}
    text, _ = strip_code_blocks(read(registry))
    for line in text.split("\n"):
        s = line.strip()
        m = NS_RE.match(s)
        if not m:
            continue
        cells = split_cells(s)
        if len(cells) < 4:
            continue
        ns = m.group(1)
        members = set(re.findall(r"`([A-Z][A-Z0-9_]*)`", cells[2]))
        if ns in reg:
            reg[ns] |= members
        else:
            reg[ns] = members

    chapters = {}
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        if not rel.startswith("docs/stdlib/"):
            continue
        if "registry" in rel:
            continue
        text, _ = strip_code_blocks(read(p))
        cur_ns = None
        for line in text.split("\n"):
            h = re.match(r"^#{2,3}\s+.*`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)?)`", line)
            if h:
                cur_ns = h.group(1)
            if not cur_ns:
                continue
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = split_cells(s)
            if len(cells) < 3:
                continue
            names = _members_from_signature_cell(cells[1])
            if not names:
                continue
            chapters.setdefault(cur_ns, set()).update(names)

    for ns in sorted(set(reg) | set(chapters)):
        if ns not in reg:
            warns.append(f"成员面对拍:命名空间 {ns} 尚未进入注册镜像册")
            continue
        if ns not in chapters:
            warns.append(f"成员面对拍:命名空间 {ns} 的成员表尚未成稿")
            continue
        only_reg = sorted(reg[ns] - chapters[ns])
        only_ch = sorted(chapters[ns] - reg[ns])
        if only_reg or only_ch:
            errors.append(
                f"成员面对拍:{ns} 两侧不一致 —— "
                f"仅注册镜像有:{only_reg or '无'};仅成员表有:{only_ch or '无'}"
            )
        else:
            print(f"  成员面对拍:{ns} —— {len(reg[ns])} 个成员,两侧一致")


# 注册镜像册的成员列:`` `NAME` `` 或 `` `NAME` `[stub]` `` —— 标记跟在名字后面。
REG_MEMBER_RE = re.compile(r"`([A-Z][A-Z0-9_]*)`(\s+`\[stub\]`)?")


def check_stub_markers(files, errors, warns):
    """`[stub]` 标记在注册镜像册与各章成员表之间**必须**一致。

    标记有两处落点(一致性册 §4.4 定的读法):各章成员表的签名格之后,
    与注册镜像册附录 A 的成员列。手工同步两处必然漂 ⇒ 机械判定。
    """
    registry = os.path.join(REPO, "docs", "stdlib", "wlwl-v1.0-stdlib-registry.md")
    if not os.path.exists(registry):
        return

    reg_stubs = set()
    text, _ = strip_code_blocks(read(registry))
    for line in text.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = split_cells(s)
        if len(cells) < 4:
            continue
        ns = NS_RE.match(s)
        if not ns:
            continue
        for m in REG_MEMBER_RE.finditer(cells[2]):
            if m.group(2):
                reg_stubs.add((ns.group(1), m.group(1)))

    chapter_stubs = set()
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        if not rel.startswith("docs/stdlib/") or "registry" in rel:
            continue
        text, _ = strip_code_blocks(read(p))
        cur_ns = None
        for line in text.split("\n"):
            h = re.match(r"^#{2,3}\s+.*`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)?)`", line)
            if h:
                cur_ns = h.group(1)
            if not cur_ns:
                continue
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = split_cells(s)
            if len(cells) < 3:
                continue
            names = _members_from_signature_cell(cells[1])
            if not names:
                continue
            if "[stub]" in cells[1]:
                for n in names:
                    chapter_stubs.add((cur_ns, n))

    for ns, name in sorted(chapter_stubs - reg_stubs):
        errors.append(
            f"[stub] 标记不一致:{ns}::{name} 在章节成员表标了 [stub],"
            f"注册镜像册附录 A 里没有 —— 两处必须同时标"
        )
    for ns, name in sorted(reg_stubs - chapter_stubs):
        errors.append(
            f"[stub] 标记不一致:{ns}::{name} 在注册镜像册附录 A 标了 [stub],"
            f"章节成员表里没有 —— 两处必须同时标"
        )
    if chapter_stubs or reg_stubs:
        print(f"  [stub] 标记对拍 —— {len(chapter_stubs)} 个桩成员,两侧一致")


# ── 第 8 项:失败口径对拍(一致性册 §6.2 的 V-05) ──────────────────
#
# 规范侧:各标准库册**成员表**的「失败」列 ⇒ (成员 → 预期诊断码)。
# 实现侧:实现仓的**断言位** ⇒ ((命名空间, 成员) → 已被钉住的诊断码)。
# 两者相减 = 缺口清单。缺口**只报提示** —— 「没钉住」不是规范文本的错。

# 语言核心册统一规定、**适用于全部成员**的两条码。
# 这两条按**章级 blanket** 判定(每章 1–2 条代表性用例即可),
# 不按「成员 × 码」逐个算缺口 —— 否则每个成员都会虚报两条,缺口清单失去意义。
BLANKET_CODES = ("E0022", "E0030")

# `ERR(E0047)` 形态:码在**诊断载荷**里 —— 与「诊断流」里的码是两回事。
# 语言核心册 §11.2 把 E0046–E0049 定为诊断级,但 std.test 的失败列写的是
# `ERR(E0047)`(成员把 ERR **值**交给调用方,不是中止运行的诊断)。
PAYLOAD_CODE_RE = re.compile(r"ERR\s*\(\s*`?([EW]\d{4})`?\s*\)")

# 成员表的**表头名** —— 按名字定位列,不写死下标。
# v1.0 的成员表是 5 列(签名/语义/失败/复杂度/层),v0.11 的旧文本是 4 列
# (签名/语义/失败/层)—— 失败列恰好都在第 3 格,但下标法一改版就崩。
SIG_HEADER = "签名"
FAIL_HEADER = "失败"

# 成员表行里的分隔行:`|---|---|---|---|---|`
SEP_CELL_RE = re.compile(r":?-{2,}:?")


def _is_sep_row(cells):
    body = [c.strip() for c in cells if c.strip()]
    return bool(body) and all(SEP_CELL_RE.fullmatch(c) for c in body)


def _clause_codes(clause):
    """失败列**一句**里的诊断码 —— 区分「诊断流」与「诊断载荷」两个通道。

    `ERR(E0047)` 里的码属载荷通道;其余裸码属诊断流通道。
    """
    payload = set()

    def _take(m):
        payload.add(m.group(1))
        return " " * len(m.group(0))

    rest = PAYLOAD_CODE_RE.sub(_take, clause)
    out = {(c, "payload") for c in payload}
    out |= {(c, "diag") for c in _codes_in_text(rest)}
    return out


def _iter_member_rows(files):
    """遍历各标准库册**成员表**的数据行。

    产出 (册内相对路径, 行号, 命名空间, 成员名列表, 失败列原文)。

    七条解析规则(逐条对应实测踩出来的坑):

    1. **按表头名定位「失败」列**,不写死列下标(5 列 / 4 列两版都吃);
    2. **围栏先行** —— 先 `strip_code_blocks()` 再解析表格,否则示例代码块里的
       `|` 会造出假行;
    3. **转义竖线** —— 用 GFM 感知的 `split_cells()`,朴素 `split("|")` 会把
       `ENV_GET(...) -> STRING` + 反斜杠竖线 这类签名格切碎;
    4. **区间展开** —— `E0090`–`E0094` 沿用 `CODE_RANGE_RE`(三种破折号);
    5. **`同上` 继承** —— 失败列写「同上」时继承同章上一成员行的失败列;
    6. **多成员行逐成员分句** —— 失败列按 `;` 分句,句内出现被反引号包住的
       成员名则该句只归属该成员,否则归属全行成员;
    7. **成员名提取** —— 签名格按 `` `NAME( `` 取,同一格里的多个重载去重。
    """
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        if not rel.startswith("docs/stdlib/") or "registry" in rel:
            continue
        # 规则 2:围栏先行。
        text, _ = strip_code_blocks(read(p))
        cur_ns = None
        sig_col = fail_col = None
        in_table = False  # 上一行是不是表格行
        for i, line in enumerate(text.split("\n"), 1):
            if re.match(r"^#{1,6}\s", line):
                # **任何**标题都重置「表状态」—— 标题带不带命名空间都一样。
                # 只按「带命名空间的标题」重置表状态的话,`### 3.5 …` 这类
                # 小节标题会让上一张成员表的列定位**继续生效** ⇒ 后面那张
                # schema 表 / 情形表被当成员表解析,造出假错误。
                h = re.match(
                    r"^#{2,3}\s+.*`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)?)`", line
                )
                if h:
                    cur_ns = h.group(1)
                sig_col = fail_col = None
                in_table = False
                continue
            s = line.strip()
            if not s.startswith("|"):
                in_table = False
                continue
            # 规则 3:GFM 感知的切分(尊重转义竖线)。
            cells = split_cells(s)
            heads = [c.strip() for c in cells]
            if not in_table:
                # **新表** —— 上一张表的列定位作废。GFM 里两张表之间必有空行
                # ⇒「上一行不是 `|` 开头」就是新表的铁证。不这么做,成员表
                # 之后的任何表(记法表、schema 表、情形表)都会被当成员表解析,
                # 造出几百条「解析不出成员名」的假错误。
                in_table = True
                sig_col = fail_col = None
                # 规则 1:按表头名定位列 —— 只认「签名 + 失败」的表头。
                if FAIL_HEADER in heads and SIG_HEADER in heads:
                    sig_col = heads.index(SIG_HEADER)
                    fail_col = heads.index(FAIL_HEADER)
                continue
            if _is_sep_row(cells):
                continue  # 分隔行不是数据行
            if cur_ns is None or fail_col is None:
                continue
            if sig_col >= len(cells) or fail_col >= len(cells):
                yield (rel, i, cur_ns, [], "", "")  # 列数不足 —— 交给调用方报错
                continue
            # 规则 7:成员名提取(同一格里的多个重载去重)。
            sig = cells[sig_col]
            members = sorted(_members_from_signature_cell(sig))
            fail = cells[fail_col].strip()
            # 规则 5(「同上」继承)由调用方按章处理 —— 它需要跨行状态。
            yield (rel, i, cur_ns, members, fail, sig.strip())


def parse_failure_surface(files, errors):
    """从各标准库册的**成员表**解析出 (命名空间, 成员) → 预期诊断码。

    返回 {ns: {member: set((code, channel))}},channel ∈ {"diag", "payload"}。
    """
    surface = {}
    conflicts = set()
    prev_fail = {}  # (ns) → 同章上一成员行的失败列(已解引用)
    for rel, i, ns, members, fail, sig in _iter_member_rows(files):
        if not members:
            errors.append(
                f"{rel}:{i} 成员表行解析不出成员名 —— 签名格 {sig[:60]!r}"
            )
            continue
        if fail in ("同上", "同上。"):
            inherited = prev_fail.get(ns)
            if inherited is None:
                errors.append(
                    f"{rel}:{i} 失败列写「同上」但同章没有上一成员行可继承"
                )
                continue
            fail = inherited
        elif not fail:
            errors.append(f"{rel}:{i} 成员表的失败列为空 —— {'、'.join(members)}")
            continue
        prev_fail[ns] = fail
        # 规则 6:逐成员分句。
        for clause in fail.split(";"):
            clause = clause.strip()
            if not clause:
                continue
            named = [
                m for m in re.findall(r"`([A-Z][A-Z0-9_]*)`", clause) if m in members
            ]
            targets = named if named else members
            # 规则 4:区间展开在 _clause_codes / _codes_in_text 里。
            for code, channel in _clause_codes(clause):
                for mem in targets:
                    bucket = surface.setdefault(ns, {}).setdefault(mem, set())
                    bucket.add((code, channel))
    # 归属冲突:同一 (成员, 码) 同时落在两个通道上 ⇒ 规范文本自相矛盾。
    for ns, members in surface.items():
        for mem, pairs in members.items():
            by_code = {}
            for code, channel in pairs:
                by_code.setdefault(code, set()).add(channel)
            for code, channels in by_code.items():
                if len(channels) > 1:
                    key = (ns, mem, code)
                    if key in conflicts:
                        continue
                    conflicts.add(key)
                    errors.append(
                        f"失败口径归属冲突:{ns}::{mem} 的 {code} 同时被登记为"
                        f"诊断流与诊断载荷 —— 同一 (成员, 码) 只能有一条通道"
                    )
    return surface


def parse_member_namespaces(files):
    """各标准库册的 (命名空间 → 成员名集合)。

    与成员面对拍同一套读法,但**不**要求两侧相等 —— 这里要的是「这一章有哪些
    成员」,供实现侧行内单测的归属判定用。
    """
    out = {}
    for rel, i, ns, members, _fail, _sig in _iter_member_rows(files):
        if members and ns:
            out.setdefault(ns, set()).update(members)
    return out


# ── 实现侧 ─────────────────────────────────────────────────────

# 实现仓的三个候选位置(按顺序):环境变量 → CI 的 upstream/ → 本地并列仓。
IMPL_ENV_VAR = "WLWL_IMPL_PATH"


def _impl_root_norm(p):
    """把候选路径归一成「含 crates/ 的 impl 目录」。"""
    p = os.path.normpath(p)
    if os.path.isdir(os.path.join(p, "crates")):
        return p
    if os.path.isdir(os.path.join(p, "impl", "crates")):
        return os.path.join(p, "impl")
    return p


def find_impl_root(base=None):
    """找参考实现的**源码**目录(不是可执行文件)。

    CI 把实现仓 checkout 到 `upstream/`(见 .github/workflows/spec-check.yml),
    所以 impl 目录在 `upstream/impl`;本地并列放在 `../wlwl/impl`。
    都找不到 ⇒ 返回 None(第 8 项报「已跳过」,**不得**当成通过)。

    ⚠ `base` 是**查找的起点目录**,默认本仓所在位置。`negctl.py` 在**临时副本**里
    运行本模块 ⇒ 那时「本仓」是副本 ⇒ 副本的旁边没有实现仓 ⇒ 必须由调用方把
    **原始**规范仓的位置传进来(否则第 8 项的阴性对照会因「找不到实现仓」而
    全部跳过 —— 2026-10-22 在 CI 上踩过:11/11 里有 1 项因此「漏网」)。
    """
    root0 = os.path.abspath(base) if base else REPO
    cands = []
    env = os.environ.get(IMPL_ENV_VAR, "").strip()
    if env:
        cands.append(env)
    cands.append(os.path.join(root0, "upstream", "impl"))
    cands.append(os.path.join(root0, "..", "wlwl", "impl"))
    for c in cands:
        root = _impl_root_norm(c)
        if os.path.isdir(os.path.join(root, "crates")):
            return root
    return None


# 契约测试里的 IMPORT 路径 → 命名空间。
IMPORT_PATH_RE = re.compile(r"IMPORT\s*\(\s*\"([^\"]+)\"")


def _ns_of_import(path):
    """IMPORT / SPEC 的路径 → 命名空间。认不出来就返回 None(不硬套)。"""
    if path.startswith("wlwl:std."):
        return path[len("wlwl:"):]
    if path in ("ai:ask", "agent:task"):
        return path.split(":")[0]
    return None


# `expect: "!E0030 MAP: …"` —— 诊断流;码后面可跟成员名。
DIAG_EXPECT_RE = re.compile(r"^!([EW]\d{4})(?:\s+([A-Z][A-Z0-9_]*))?\b")
# `expect: "[code: E0047, kind: …]"` —— 诊断载荷里的码。
PAYLOAD_EXPECT_RE = re.compile(r"\bcode:\s*[\"']?([EW]\d{4})")
# IMPORT 的成员数组:`["MAP", "FILTER"]` / `["TASK": "AGENT_TASK"]`(改名形态)。
IMPORT_MEMBERS_RE = re.compile(r"IMPORT\s*\(\s*\"[^\"]+\"\s*,\s*\[(.*?)\]", re.S)
IMPORT_MEMBER_RE = re.compile(
    r"\"([A-Z][A-Z0-9_]*)\"\s*(?::\s*\"[A-Z][A-Z0-9_]*\")?"
)


def _imported_members(src):
    out = set()
    for m in IMPORT_MEMBERS_RE.finditer(src):
        for name in IMPORT_MEMBER_RE.findall(m.group(1)):
            out.add(name)
    return out


def _called_imported(src, imported):
    """src 里**被调用**的已导入成员。

    只认「已导入 ∧ 出现为调用」—— 否则 `IMPORT(..., ["MAP"])` 里的名字会把
    源码里别处的同名标识符算进来。
    """
    out = set()
    for name in imported:
        if re.search(r"(?<![A-Z0-9_])" + re.escape(name) + r"\s*\(", src):
            out.add(name)
    return out


def _derive_member(src):
    """从 src 推出被断言的成员 —— 诊断文本里没带成员名时的回退。

    只有**恰好一个**已导入成员被调用时才推得出来;多于一个 ⇒ 推不出 ⇒ 放弃
    (宁可少算「已钉」,不可把没钉住的说成钉住了)。
    """
    called = _called_imported(src, _imported_members(src))
    if len(called) == 1:
        return next(iter(called))
    return None


def _case_pin(src, expect):
    """一条契约用例 ⇒ (命名空间, 成员, 码, 通道) 或 None。

    **只认断言位**:期望值里带 `!EXXXX`(诊断流)或 `code: EXXXX`(载荷)才算。
    源码里的普通文本、注释、码注册表都不算。
    """
    nss = set()
    for m in IMPORT_PATH_RE.finditer(src):
        ns = _ns_of_import(m.group(1))
        if ns:
            nss.add(ns)
    if len(nss) != 1:
        return None
    ns = nss.pop()
    dm = DIAG_EXPECT_RE.match(expect)
    if dm:
        # 归属**优先按 src 推导**,消息里的成员名只作**兜底**。
        #
        # ⚠ 为什么不反过来:诊断消息里点名的成员常是**内部原因**,不是用户调的那个
        # —— 例:`ABS` 的 INTEGER 下界报「!E0034 NEG: cannot negate INTEGER_MIN」
        # (E0034 的规范语义就是「整数取负下界」,见语言核心册 §11.2),可用户调的是
        # `ABS` ⇒ 按消息归属会把这条钉住算到 `NEG` 名下,于是 `std.math::ABS`
        # 的 E0034 被误报成「未钉住」。⇒ 归属看**谁被调用**。
        member = _derive_member(src) or dm.group(2)
        if not member:
            return None
        return (ns, member, dm.group(1), "diag")
    pm = PAYLOAD_EXPECT_RE.search(expect)
    if pm:
        member = _derive_member(src)
        if not member:
            return None
        return (ns, member, pm.group(1), "payload")
    return None


RUST_RAW_STR_RE = re.compile(r'r#"(.*?)"#', re.S)
RUST_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)
RUST_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}


def _unescape_rust(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in RUST_ESCAPES:
            out.append(RUST_ESCAPES[s[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _string_after(line, key):
    """取 `key:` 之后的第一个 Rust 字符串字面量的值。"""
    idx = line.find(key)
    if idx < 0:
        return None
    rest = line[idx + len(key):]
    m = RUST_RAW_STR_RE.search(rest)
    if m:
        return m.group(1)
    m = RUST_STR_RE.search(rest)
    if m:
        return _unescape_rust(m.group(1))
    return None


def scan_contract_pins(impl_root):
    """扫契约测试表(`crates/wlwl-eval/tests/*.rs`)里的断言位。

    契约表是「成员 → 期望值」的冻结表,它的 `expect` 字段就是断言位:
    `expect: "!E0030 MAP: …"`(诊断流)与 `expect: "[code: E0047, …]"`(载荷)。
    命名空间取**同一条用例**的 `src` 里的 IMPORT 路径 ⇒ 跨章文件
    (`str_math_contract.rs` 既有 std.str 又有 std.math)也不会串。
    """
    pins = set()
    base = os.path.join(impl_root, "crates", "wlwl-eval", "tests")
    if not os.path.isdir(base):
        return pins
    for name in sorted(os.listdir(base)):
        if not name.endswith(".rs"):
            continue
        path = os.path.join(base, name)
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        last_src = None
        for line in text.split("\n"):
            if "src:" in line:
                v = _string_after(line, "src:")
                if v is not None:
                    last_src = v
            if "expect:" in line:
                v = _string_after(line, "expect:")
                if v is None or last_src is None:
                    continue
                pin = _case_pin(last_src, v)
                if pin:
                    pins.add(pin)
    return pins


# ── 通用断言行扫描(行内单测 / 非表格形态的契约测试)─────────────
#
# ⚠ 口径必须硬,否则假绿:
#   · **注释先剥掉** —— 文档注释里满是码,那是说明不是断言;
#   · **只认断言位** —— 断言宏的实参,以及「循环的集合字面量 + 循环体里有断言」;
#   · **码注册表整个 crate 不进**(`wlwl-error`)—— 那是码的定义,不是断言;
#   · **归属必须唯一** —— 命名空间从文件自身声明的路径取,取不唯一就整个文件不算。

RUST_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
RUST_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
TEST_ATTR_RE = re.compile(r"#\[\s*test\s*\]")
FN_NAME_RE = re.compile(r"\bfn\s+([a-z0-9_]+)\s*\(")
ASSERT_MACRO_RE = re.compile(r"\bassert(?:_eq|_ne)?!\s*\(")
ERROR_CODE_ENUM_RE = re.compile(r"ErrorCode::([EW]\d{4})")
DIAG_CODE_STRING_RE = re.compile(r"\"!([EW]\d{4})")
BARE_CODE_STRING_RE = re.compile(r"\"([EW]\d{4})")
FOR_IN_LIST_RE = re.compile(r"\bfor\b[^;{}]*?\bin\s*\[")
# 这些 crate 与「成员 → 失败口径」无关 —— 码注册表 / 类型系统 / 解析器。
SKIP_CRATES = ("wlwl-error", "wlwl-types", "wlwl-lexer", "wlwl-parser", "wlwl-ast")


def _strip_rust_comments(text):
    text = RUST_BLOCK_COMMENT_RE.sub(" ", text)
    return RUST_LINE_COMMENT_RE.sub(" ", text)


def _file_ns_candidates(text, known_ns):
    """文件级命名空间的**候选集**。

    两处来源:模块自己声明的 `path: "wlwl:std.x"`,与文件里出现的
    `IMPORT("wlwl:std.x", …)`。一个文件可能跨多个命名空间
    (`fs_contract.rs` 同时碰 std.fs 与 std.env)⇒ 这里给**集合**,归属
    交给每个测试函数单独解析(见 `_resolve_member`)。
    """
    nss = set()
    for m in re.finditer(r"path:\s*\"([^\"]+)\"", text):
        raw = m.group(1)
        ns = _ns_of_import(raw)
        if ns is None and raw in known_ns:
            ns = raw
        if ns:
            nss.add(ns)
    for m in IMPORT_PATH_RE.finditer(text):
        ns = _ns_of_import(m.group(1))
        if ns:
            nss.add(ns)
    return nss


def _resolve_member(test_name, candidates, member_index):
    """(测试函数名, 候选命名空间) → (命名空间, 成员) 或 None。

    **必须唯一**:只有一个候选命名空间能把这个测试函数名认领成自己的成员
    时才算数。认不出、或两个候选都认领(例如 `JOIN` 同属 std.collection 与
    std.str)⇒ 放弃 —— 宁可多报缺口,不可错认命名空间。
    """
    matches = []
    for ns in sorted(candidates):
        members = member_index.get(ns)
        if not members:
            continue
        m = _member_from_test_name(test_name, members)
        if m:
            matches.append((ns, m))
    if len(matches) != 1:
        return None
    return matches[0]


def _rust_braced_span(text, start):
    """从 text[start] 起的第一个「函数体的 `{`」开始,取**配平**的 `}`。

    返回 (body_start, body_end) —— body_start 指向 `{` 之后,body_end 指向
    配平的 `}` 本身(闭区间);找不到返回 None。

    ⚠ **为什么要自己扫**:函数体里可以有嵌套块(`impl` / `struct` / `trait` /
    闭包 / `match`),而「截到第一个整行只有 `}`」那条老口径会在**嵌套块的
    结尾**截断 ⇒ 断言写在嵌套块之后时**看不见** ⇒ 它的 pin **不计数**
    ⇒ 缺口清单会**漏报**。⇒ 口径改成**括号配平**。

    ⚠ 扫描时必须跳过:行注释、块注释、字符串字面量、原始字符串、字符字面量
    —— 它们里面可能有 `{` / `}`(例如 `"{"` 或 `'}'`)。
    """
    i = start
    n = len(text)
    # 第一步:找到函数体的 `{`(跳过签名里的内容)。
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue
        if c == "r" and i + 1 < n and text[i + 1] in '"#':
            j = i + 1
            hashes = 0
            while j < n and text[j] == "#":
                hashes += 1
                j += 1
            if j < n and text[j] == '"':
                j += 1
                end = '"' + "#" * hashes
                k = text.find(end, j)
                i = n if k < 0 else k + len(end)
                continue
        if c == '"':
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            break
        i += 1
    if i >= n:
        return None
    # 第二步:从 `{` 起做配平。
    depth = 0
    j = i
    while j < n:
        c = text[j]
        if c == "/" and j + 1 < n and text[j + 1] == "/":
            k = text.find("\n", j)
            j = n if k < 0 else k + 1
            continue
        if c == "/" and j + 1 < n and text[j + 1] == "*":
            d = 1
            j += 2
            while j < n and d:
                if text.startswith("/*", j):
                    d += 1
                    j += 2
                elif text.startswith("*/", j):
                    d -= 1
                    j += 2
                else:
                    j += 1
            continue
        if c == "r" and j + 1 < n and text[j + 1] in '"#':
            k = j + 1
            hashes = 0
            while k < n and text[k] == "#":
                hashes += 1
                k += 1
            if k < n and text[k] == '"':
                k += 1
                end = '"' + "#" * hashes
                m = text.find(end, k)
                j = n if m < 0 else m + len(end)
                continue
        if c == '"':
            j += 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            continue
        if c == "'":
            # 字符字面量 vs 生命周期标注:`'a'` 是字面量,`'static` 是标注。
            if j + 2 < n and text[j + 2] == "'":
                j += 3
                continue
            if j + 3 < n and text[j + 1] == "\\" and text[j + 3] == "'":
                j += 4
                continue
            j += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (i + 1, j)
        j += 1
    return None


def _iter_test_fns(body):
    """产出 (测试函数名, 函数体文本)。只认带 `#[test]` 的函数。

    ⚠ 函数体按**括号配平**取(见 `_rust_braced_span`)—— 老口径「截到第一个
    整行只有 `}`」会在**嵌套块的结尾**截断 ⇒ 断言写在嵌套块之后时看不见
    ⇒ pin 不计数 ⇒ 缺口清单**漏报**。⇒ 口径改成配平(2026-10-22)。
    """
    lines = body.split("\n")
    n = len(lines)
    i = 0
    while i < n:
        if not TEST_ATTR_RE.search(lines[i]):
            i += 1
            continue
        j = i
        name = None
        while j < n and j <= i + 3:
            m = FN_NAME_RE.search(lines[j])
            if m:
                name = m.group(1)
                break
            j += 1
        if name is None:
            i += 1
            continue
        # 函数体:从签名行末尾起做**括号配平**。
        # ⚠ 用**行偏移**换算成字符偏移(签名可能跨行)。
        offset = sum(len(x) + 1 for x in lines[:j])
        span = _rust_braced_span(body, offset)
        if span is None:
            # 配平失败(文件被截断 / 语法异常)⇒ 退回老口径,至少不丢整行。
            k = j
            while k < n and lines[k].strip() != "}":
                k += 1
            yield name, "\n".join(lines[j:min(k + 1, n)])
            i = k + 1
            continue
        start, end = span
        yield name, body[start:end]
        # 跳到函数体之后,避免把函数体里的 `#[test]`(嵌套定义)重复认领。
        # ⚠ 行号换算:用 `end` 处**所在行**(不是「从文件开头数起的换行数」——
        #   那是绝对偏移,加到行号上会跳飞)。
        i = body.count("\n", 0, end) + 1


def _balanced(text, start, open_ch, close_ch):
    """从 text[start] 处的括号开始,取配平后的**内容**。找不到就返回 None。"""
    depth = 0
    for j in range(start, len(text)):
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1:j], j
    return None, -1


def _codes_in_rust_text(text):
    """一段 Rust 文本里的诊断码 —— 按**通道**分开。

    · `ErrorCode::EXXXX` 与 `"!EXXXX"` ⇒ **诊断流**(`!` 是诊断显示形态的前缀);
    · 裸的 `"EXXXX"` ⇒ **分不清**(既可能是诊断码被摘出来比对,也可能载荷里的
      `code` 字段)⇒ 记成 `ambiguous`,匹配时可当任一通道用,但**不**参与
      「诊断流 / 载荷」的精确判定。

    契约表那侧(`expect:` 字段)是**精确**的:`!EXXXX` 是诊断流、`code: EXXXX`
    是载荷 —— 两者不混。
    """
    diag = set(ERROR_CODE_ENUM_RE.findall(text))
    diag |= set(DIAG_CODE_STRING_RE.findall(text))
    ambiguous = set(BARE_CODE_STRING_RE.findall(text))
    return diag, ambiguous


def _asserted_codes(body):
    """一个测试函数体里**被断言位**覆盖的诊断码。

    只认两处:
    1. 断言宏(`assert_eq!` / `assert!` / `assert_ne!`)的实参里出现的码;
    2. `for … in [ … ]` 的**集合字面量**里出现的码 —— 但只在**同一个循环体
       里有断言**时才认(否则只是数据,不是断言)。
    """
    diag, ambiguous = set(), set()
    for m in ASSERT_MACRO_RE.finditer(body):
        args, _end = _balanced(body, m.end() - 1, "(", ")")
        if args is None:
            continue
        d, a = _codes_in_rust_text(args)
        diag |= d
        ambiguous |= a
    for m in FOR_IN_LIST_RE.finditer(body):
        lit, close = _balanced(body, m.end() - 1, "[", "]")
        if lit is None:
            continue
        # 循环体里必须有断言,否则集合字面量只是数据。
        if not ASSERT_MACRO_RE.search(body[close:]):
            continue
        d, a = _codes_in_rust_text(lit)
        diag |= d
        ambiguous |= a
    return diag, ambiguous


def _member_from_test_name(test_name, member_names):
    """从 `#[test] fn <name>` 推出被测成员。

    Rust 的测试名是 snake_case,成员名是 UPPER_SNAKE ⇒ 把成员名转成
    snake 形态做**最长匹配**(`SORT_BY` 优先于 `SORT`)。匹配不上就放弃。
    """
    best = None
    for m in member_names:
        snake = m.lower()
        if re.search(r"(?<![a-z0-9])" + re.escape(snake) + r"(?![a-z0-9])", test_name):
            if best is None or len(m) > len(best):
                best = m
    return best


def scan_assert_pins(impl_root, member_index):
    """扫实现仓全部 .rs 里的**断言位**。

    覆盖两类:
      · `crates/*/src/**.rs` 的 `#[cfg(test)]` 行内单测;
      · `crates/*/tests/*.rs` 的集成测试(整份文件都是测试)。
    归属口径:命名空间取**文件自身声明**的路径(取不唯一 ⇒ 整个文件不算);
    成员取 `#[test] fn <name>` 的最长匹配。取不到归属 ⇒ 不算已钉。
    """
    pins = set()
    base = os.path.join(impl_root, "crates")
    if not os.path.isdir(base):
        return pins
    known_ns = set(member_index)
    for crate in sorted(os.listdir(base)):
        if crate in SKIP_CRATES:
            continue
        for sub in ("src", "tests"):
            root_dir = os.path.join(base, crate, sub)
            if not os.path.isdir(root_dir):
                continue
            for root, _dirs, names in os.walk(root_dir):
                for name in sorted(names):
                    if not name.endswith(".rs"):
                        continue
                    path = os.path.join(root, name)
                    with io.open(path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    stripped = _strip_rust_comments(raw)
                    candidates = _file_ns_candidates(stripped, known_ns)
                    if not candidates:
                        continue
                    if sub == "src":
                        cut = raw.find("#[cfg(test)]")
                        if cut < 0:
                            continue
                        body = _strip_rust_comments(raw[cut:])
                    else:
                        body = stripped
                    for test_name, fn_body in _iter_test_fns(body):
                        resolved = _resolve_member(test_name, candidates, member_index)
                        if resolved is None:
                            continue
                        ns, member = resolved
                        diag, ambiguous = _asserted_codes(fn_body)
                        for c in diag:
                            pins.add((ns, member, c, "diag"))
                        for c in ambiguous:
                            pins.add((ns, member, c, "ambiguous"))
    return pins


def _is_pinned(pin_set, code, channel):
    """(成员, 码, 通道) 是否已被实现侧钉住。

    `ambiguous`(裸字符串码,分不清是诊断流还是载荷)可当任一通道用;**精确**
    的通道不互相顶替 —— 诊断流 ≠ 载荷。
    """
    return (code, channel) in pin_set or (code, "ambiguous") in pin_set


def check_failure_surface(files, errors, warns):
    """第 8 项:标准库册的失败口径逐条对齐(一致性册 §6.2 的 V-05)。

    规范侧解析出「成员 → 预期诊断码」,实现侧扫断言位算出「哪些 (成员, 码)
    已被钉住」,两者相减 = **缺口清单**。

    缺口只报**提示**:「没钉住」不是规范文本的错,是覆盖面的事实陈述。
    错误级只报规范文本自身的问题(空失败列 / 解析不出成员名 / 归属冲突)。
    """
    surface = parse_failure_surface(files, errors)
    member_index = parse_member_namespaces(files)
    if not surface:
        warns.append("第 8 项(失败口径对拍)已跳过:各标准库册没有可解析的成员表")
        return

    impl_root = find_impl_root()
    if not impl_root:
        warns.append(
            "第 8 项(失败口径对拍)已跳过:未找到参考实现的源码目录"
            f"(查过 ${IMPL_ENV_VAR}、upstream/impl、../wlwl/impl)—— "
            "**已跳过不等于通过**"
        )
        return

    pins = scan_contract_pins(impl_root) | scan_assert_pins(impl_root, member_index)
    pinned = {}
    for ns, member, code, channel in pins:
        pinned.setdefault((ns, member), set()).add((code, channel))

    total_members = total_pairs = total_pinned = total_gaps = 0
    for ns in sorted(surface):
        members = surface[ns]
        pairs = set()
        for mem, codes in members.items():
            for code, channel in codes:
                if code in BLANKET_CODES:
                    continue
                pairs.add((mem, code, channel))
        codes_in_chapter = {c for _m, c, _ch in pairs}
        got = set()
        for mem, code, channel in pairs:
            if _is_pinned(pinned.get((ns, mem), ()), code, channel):
                got.add((mem, code, channel))
        gaps = sorted(pairs - got)
        # 章级 blanket:E0022 / E0030 每章 1–2 条代表性用例即可。
        blanket_gaps = []
        for bc in BLANKET_CODES:
            has = any(
                _is_pinned(pinned.get((ns, mem), ()), bc, "diag") for mem in members
            )
            if not has:
                blanket_gaps.append(bc)
        n_members = len(member_index.get(ns, members))
        total_members += n_members
        total_pairs += len(pairs)
        total_pinned += len(got)
        total_gaps += len(gaps) + len(blanket_gaps)
        print(
            f"  失败口径对拍:{ns} —— 成员 {n_members} / 码种 {len(codes_in_chapter)} / "
            f"(成员,码)对 {len(pairs)} / 已钉 {len(got)} / 缺口 {len(gaps) + len(blanket_gaps)}"
        )
        for bc in blanket_gaps:
            warns.append(
                f"失败口径对拍缺口:{ns} —— 章级 blanket {bc} 的失败口径未被钉住"
                "(语言核心册规定它适用于全部成员,每章 1–2 条代表性用例即可)"
            )
        for mem, code, channel in gaps:
            warns.append(
                f"失败口径对拍缺口:{ns}::{mem} —— 失败口径未被钉住"
                f"(预期 {code} {channel};实现侧没有钉住它的断言位)"
            )
    print(
        f"  失败口径对拍合计 —— 成员 {total_members} / (成员,码)对 {total_pairs} / "
        f"已钉 {total_pinned} / 缺口 {total_gaps}(实现仓:{impl_root})"
    )



def check_samples(files, impl, errors, warns):
    if not impl:
        warns.append("第 7 项(示例有效性)已跳过:未提供 --impl")
        return 0
    total = 0
    for p in files:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        _, blocks = strip_code_blocks(read(p))
        for idx, (lang, body) in enumerate(blocks, 1):
            if lang != "wlwl":
                continue
            total += 1
            with tempfile.NamedTemporaryFile(
                "w", suffix=".wll", delete=False, encoding="utf-8", newline="\n"
            ) as tf:
                tf.write(body + "\n")
                tmp = tf.name
            try:
                r = subprocess.run(
                    [impl, "run", tmp],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if r.returncode != 0:
                    msg = (
                        f"{rel} 第 {total} 个 wlwl 代码块运行失败(rc={r.returncode}): "
                        f"{(r.stderr or r.stdout).strip()[:120]}"
                    )
                    errors.append(msg)
                    gh_error(rel, idx, msg)
            except subprocess.TimeoutExpired:
                msg = f"{rel} 第 {idx} 个 wlwl 代码块运行超时"
                errors.append(msg)
                gh_error(rel, idx, msg)
            except Exception as e:  # noqa: BLE001
                msg = f"{rel} 第 {idx} 个 wlwl 代码块无法运行: {e}"
                errors.append(msg)
                gh_error(rel, idx, msg)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", help="参考实现的 wlwl 可执行文件路径")
    args = ap.parse_args()

    files = all_md_files()
    if not files:
        print("未找到规范册文件。", file=sys.stderr)
        return 1

    errors, warns = [], []

    check_punctuation(files, errors)
    per_book, union = collect_sections(files)
    check_references(files, per_book, union, errors, warns)
    check_bold_normatives(files, warns)
    check_impl_note_separation(files, warns)
    check_diag_uniqueness(files, errors, warns)
    check_diag_attribution(files, errors, warns)
    check_member_surface(files, errors, warns)
    check_stub_markers(files, errors, warns)
    check_failure_surface(files, errors, warns)
    check_links(errors)
    n_samples = check_samples(files, args.impl, errors, warns)

    total_secs = sum(len(v) for v in per_book.values())
    print(f"规范册文件:{len(files)}  册数:{len(per_book)}  已定义章节:{total_secs}")
    print(f"错误:{len(errors)}  提示:{len(warns)}")
    if n_samples:
        print(f"第 7 项:已运行 {n_samples} 个 wlwl 代码块")

    if errors:
        print("\n== 错误 ==")
        for e in errors:
            print("  " + e)
    if warns:
        cats = {}
        for w in warns:
            if "尚未成稿" in w:
                k = "指向尚未成稿的章节"
            elif "应当带册名" in w:
                k = "裸引用应当带册名"
            elif "未加粗" in w:
                k = "规范用语未加粗"
            elif "实现注段落含规范关键词" in w:
                k = "实现注未与规范条款分离"
            elif "失败口径对拍缺口" in w:
                k = "失败口径对拍缺口(第 8 项)"
            else:
                k = "其它"
            cats[k] = cats.get(k, 0) + 1
        print("\n== 提示分类 ==")
        for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
            print(f"  {k}: {v}")
        print("\n== 提示明细 ==")
        for w in warns[:600]:
            print("  " + w)
        if len(warns) > 600:
            print(f"  …… 另有 {len(warns) - 600} 条提示未列出")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
