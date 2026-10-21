import io
import os
import sys

REPO = r"D:\Project\wlwl-spec-v1"
OLD = "层归属变更(下沉/上浮)**不算**破坏性变更,但必须附基准数据并登记"
NEW = "层归属变更(下沉/上浮)**不算**破坏性变更,但**必须**附基准数据并登记"

FILES = [
    "docs/stdlib/wlwl-v1.0-stdlib-core.md",
    "docs/stdlib/wlwl-v1.0-stdlib-text.md",
    "docs/stdlib/wlwl-v1.0-stdlib-system.md",
    "docs/stdlib/wlwl-v1.0-stdlib-security.md",
    "docs/stdlib/wlwl-v1.0-stdlib-official.md",
]

for rel in FILES:
    path = os.path.join(REPO, rel.replace("/", os.sep))
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    n = text.count(OLD)
    if n != 1:
        print(f"[跳过] {rel}:命中 {n} 次")
        continue
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace(OLD, NEW, 1))
    print(f"[已改] {rel}")
