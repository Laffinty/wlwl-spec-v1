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

# 册名 → 册文件关键字。用于「带册名的跨册引用」。
BOOK_ALIASES = {
    "总纲": "overview",
    "语言核心册": "language",
    "对象册": "objects",
    "对象与行为类型册": "objects",
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
    r"(?P<qual>" + "|".join(sorted(BOOK_ALIASES, key=len, reverse=True)) + r")\s*§(?P<num>\d+(?:\.\d+)*)"
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
            for ch, name in FULLWIDTH.items():
                if ch in line:
                    errors.append(
                        f"{rel}:{i} 标点契约:全角 {name!r} 出现在正文 —— {line.strip()[:70]}"
                    )


def resolve(qual, num, cur_key, per_book, union):
    """返回 (状态, 解析到的册)。状态 ∈ {ok, not_yet, missing, needs_qual}。"""
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

    if num in per_book.get(cur_key, {}):
        return "ok", cur_key
    for k in per_book:
        if num in per_book[k]:
            return "needs_qual", k
    for k in per_book:
        if in_scope(k, num):
            return "not_yet", None
    return "missing", None


def row_book_key(line):
    """表格行的首个单元格若点名了某册,该行内的裸 § 引用按该册解析。"""
    s = line.strip()
    if not s.startswith("|"):
        return None
    first = s.split("|")[1] if len(s.split("|")) > 1 else ""
    for alias, key in BOOK_ALIASES.items():
        if alias in first:
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
            consumed = set()
            for m in QUAL_RE.finditer(line):
                consumed.update(range(m.start(), m.end()))
                status, key = resolve(m.group("qual"), m.group("num"), line_key, per_book, union)
                num = m.group("num")
                if status == "missing":
                    errors.append(f"{rel}:{i} 引用 {m.group('qual')} §{num} 指向不存在的章节")
                elif status == "not_yet":
                    warns.append(f"{rel}:{i} 引用 {m.group('qual')} §{num} 指向尚未成稿的章节")
            for m in BARE_RE.finditer(line):
                if any(s in consumed for s in range(m.start(), m.end())):
                    continue
                num = m.group("num")[1:]
                tail = line[m.end():m.end() + 1]
                head = line[max(0, m.start() - 1):m.start()]
                status, key = resolve(None, num, line_key, per_book, union)
                if status == "missing":
                    errors.append(f"{rel}:{i} 引用 §{num} 指向不存在的章节")
                elif status == "not_yet":
                    warns.append(f"{rel}:{i} 引用 §{num} 指向尚未成稿的章节")
                elif status == "needs_qual" and row_key is None:
                    if tail in ("–", "-") or head in ("–", "-"):
                        continue
                    warns.append(
                        f"{rel}:{i} 裸引用 §{num} 实际落在 {key} 册,跨册引用应当带册名"
                    )


CODE_SPAN_RE = re.compile(r"`[^`]*`")
BOLD_SPAN_RE = re.compile(r"\*\*[^*]+\*\*")
QUOTE_RE = re.compile(r"「[^」]*」")
HEADING_RE = re.compile(r"^#{2,4}\s+(.*)$")

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
