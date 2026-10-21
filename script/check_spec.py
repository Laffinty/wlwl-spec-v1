#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WLWL v1.0 规范文本检查器。

实现一致性册 §6.1 里可机械判定的检查:

  1 标点契约       —— 正文不得出现全角 , : ; ( )
  2 跨册引用有效性 —— § 引用指向确实存在的章节
  3 章节号唯一性   —— §1 起的编号在同一编号空间内不得重复定义
  4 规范性用语     —— 必须 / 不得 / 应当 / 可以 作规范关键词时必须加粗
  5 实现注分离     —— 实现注段落必须独立成段
  6 相对链接有效   —— 文档内的相对链接必须指向存在的文件
  7 示例有效性     —— wlwl 代码块必须在参考实现上运行且退出码为 0

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
SKIP_FILES = ["docs/audit/第三方审计报告-从爱好者到工业化.md"]

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
    + r")(?:\]\([^)]*\))?(?:\([^)]*\))?\s*§(?P<num>\d+(?:\.\d+)*)"
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


def all_md_files():
    out = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "target", "node_modules", ".scratch")]
        for n in sorted(names):
            if n.endswith(".md"):
                p = os.path.join(root, n)
                rel = os.path.relpath(p, REPO).replace("\\", "/")
                if rel in SKIP_FILES:
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

    # 同册优先:本册自己的章节,裸写即可。
    if num in per_book.get(cur_key, {}):
        return "ok", cur_key
    # 标准库各册的 §1–§22 是**它们自己的**编号空间 —— 在标准库册里,
    # §N 一律指本册的 §N,不去和语言册的 §N 混比。
    if cur_key.startswith("stdlib"):
        top = num.split(".")[0]
        if top.isdigit() and 1 <= int(top) <= 22:
            return "ok", cur_key
    if ctx_key and ctx_key != cur_key:
        if num in per_book.get(ctx_key, {}):
            return "ok", ctx_key
        if ctx_key == "stdlib":
            for k in per_book:
                if k.startswith("stdlib") and num in per_book[k]:
                    return "ok", k
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
    """表格行内若**恰好**出现某个册标签,该行内的裸 § 引用按该册解析。

    册标签可以出现在该行的**任意**单元格 —— 规范矩阵的列序是
    「主题 | 归属册 | 节号」,册名在第二格。
    精确匹配(去粗体标记后全等),不做包含匹配 —— 包含匹配会把
    「并发原语」之类的普通单元格误当成册标签。
    """
    s = line.strip()
    if not s.startswith("|"):
        return None
    for cell in s.split("|")[1:]:
        label = cell.strip().strip("*").strip()
        key = ROW_BOOKS.get(label)
        if key:
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
                    errors.append(
                        f"{rel} 第 {idx} 个 wlwl 代码块运行失败(rc={r.returncode}): "
                        f"{(r.stderr or r.stdout).strip()[:120]}"
                    )
            except subprocess.TimeoutExpired:
                errors.append(f"{rel} 第 {idx} 个 wlwl 代码块运行超时")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{rel} 第 {idx} 个 wlwl 代码块无法运行: {e}")
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
    check_member_surface(files, errors, warns)
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
