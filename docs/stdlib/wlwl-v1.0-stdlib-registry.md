# WLWL 标准库规范 v1.0 —— 成员注册镜像

本册承载 v1.0 标准库的**成员注册镜像**:附录 A 用一张表登记**每个命名空间有哪些成员、每个成员落在哪一实现层、该命名空间的成员契约归哪一册**。

- 附录 A 是**规范性**的:总纲 §0.6 的规范矩阵把「成员注册镜像(冻结快照)」的归属定为本册,一致性册 §4.1 把它列入公共面。
- 成员的**签名、语义、失败口径、复杂度**不在本册,在标准库各册(核心 / 文本 / 系统 / 安全 / 官方包)。本册只回答「有哪些成员」。
- 本册是**冻结快照**:表体不随实现漂移,效力规则见 §0.7。

> **上游基线**:`Laffinty/wlwl` 提交 `1766032`(2026-10-26,未发 tag;v0.12.0 发版前整改批:附录 A 逐字对拍门禁 / 核心内建诊断位置 / 宿主挂起点任务体外失败模式 / 孤儿任务 ERR 终值报告 / IS_OK 族降级为标识符)。
> 核实日期:2026-10-22。核实方法与要求见[一致性册 §6.3](../spec/wlwl-v1.0-conformance.md)。

## 0 导言

### 0.1 本册的构成

本册由 §0 导言与**附录 A** 两部分组成。§0 规定本册的读法、效力与一致性要求;附录 A 是**规范性**的成员面表体,内容为本版的**冻结快照**。

本册**不**定义标准库成员的签名与行为 —— 那些由标准库各册按命名空间分册承担(§0.8)。两者的分工是:各册定义成员**怎么工作**,本册登记成员**有哪些**。

总纲 §0.6 的规范矩阵把「成员注册镜像(冻结快照)」的归属定为本册;一致性册 §4.1 把它列入公共面,§4.3 规定其冻结点。

### 0.2 读者

本册面向三类读者:需要确认某个成员在 v1.0 是否存在的 WLWL 程序员;标准库实现者(需要一份不会漂移的成员面基线);以及核对公共面清单的审计者。完整的读者路径见一致性册 §7.1。

### 0.3 规范性用语

本册中的关键词 **必须**(MUST)、**不得**(MUST NOT)、**应当**(SHOULD)、**可以**(MAY)按以下含义使用:

- **必须 / 不得**:实现的硬性约束。违反时实现必须产生一个诊断(语言核心册 §11)并按 §11.4 终止或报告。
- **应当**:强烈推荐的行为;实现可以偏离,但偏离必须在本册或实现文档中说明。
- **可以**:真正的实现自由度。

标注为**非规范**(non-normative)的段落、注释与附录不构成一致性要求。

### 0.4 一致性

本册**全部**内容为**规范性**。一个实现与本册一致,当且仅当:

1. 它对附录 A 列出的**每个**命名空间提供该行「成员」列所列的**全部**成员;
2. 它**不**把附录 A 未列出的成员当作 v1.0 标准库公共面的组成部分;
3. 它满足 §0.7 规则 3 的集合相等要求。

一个 WLWL 程序与本册一致,当且仅当它只使用附录 A 列出的命名空间成员,或使用语言核心册附录 G 登记的全局内建。

### 0.5 记法

附录 A 的表体是**四**列:

| 列 | 含义 |
|---|---|
| 命名空间 | 成员所属的命名空间,即 `IMPORT` 路径的末段 |
| 成员 | 该命名空间在本版的**全部**成员名,反引号包裹,空格分隔;成员之间无序 |
| 层 | 该成员所在的实现层:R0 内建层 / R1 语言层 / R2 原生层;同一命名空间内各成员层不同时,按成员分别标注 |
| v1.0 册 | 该命名空间的成员契约所在的标准库册(§0.8) |

**层是实现分层,不是规范层级** —— 成员落在哪一层**不**改变它的规范强度(总纲 §0.6 规则 1);层的判定规则见标准库·核心册 §0.7。

**说明**:本册不登记语言核心册附录 G 的全局内建注册表。两张注册表并列,不是同一张表:同一个名字同时出现在内建注册表与某个命名空间的情形是存在的(如 `PRINT` / `PRINT_ERR` / `INPUT` 同时出现在全局内建与 `std.io`)。

**标点约定**:逗号 `,`、冒号 `:`、分号 `;`、圆括号 `()`、方括号 `[]` 一律用**半角**;句号 `。`、顿号 `、`、引号 `「」`、破折号 `——` 用**全角**。全角 `,` `:` `;` `(` `)` **不得**出现在本册正文里。

**引用**:同册内写 `(§0.7)`;跨册写册名加节号,例:"总纲 §0.9"、"一致性册 §3.5"、"标准库·核心册 §0.7"。

### 0.6 条款类型

本册每一条陈述属于规范条款、解释性说明、实现注、示例、演进方向五类之一,判据见一致性册 §2。本册**全部**内容为**规范性**;以**说明**起首的段落是解释性说明,不构成要求;**实现注**只记录上游事实,单独成段。

### 0.7 冻结快照的效力

**本册附录 A 是本版的冻结快照,不是随实现更新的活镜像。** 本册的**单源真相是本规范自身** —— 表体以本册的文本为准。

总纲 §0.9 的 D-07 登记了这项变更:附录 A 由「从实现清单生成的活镜像」改为**冻结快照**。由此产生四条规则:

1. v0.11 的规则「表体由生成器产出,手改无效;单源真相是实现」**不再适用**于本册:表体**不得**由实现清单自动生成,**不得**为追平实现的当次改动而改写;实现侧的成员增删**不**自动改变本册。
2. 实现侧与本镜像不一致时,走一致性册 §5 的**差异登记**:差异在一致性册 §5.4 登记,由实现侧收敛;**不**由镜像跟随实现漂移。
3. 本镜像与各标准库册的成员表**必须**集合相等(一致性册 §3.5 第 3 条;机械检查见一致性册 §6.1 第 6 项)。两侧不一致即为规范缺陷,按一致性册 §7.2 的裁决顺序在规范内解决。
4. 成员的增删改按一致性册 §3.2 与 §3.4 的规范版本规则处理,并按一致性册 §7.4 更新总纲 §0.9 的差异表 —— 那是**规范文本的变更**,**不**是镜像跟随实现。

**说明**:第 2 条确定的是收敛方向 —— 差异由实现侧消化,镜像不随之改写。

### 0.8 命名空间到册的映射

本册把每个命名空间映射到 v1.0 标准库的分册之一(核心 / 文本 / 系统 / 安全 / 官方包),成员契约在对应册的那一章:

| v1.0 册 | 命名空间 | 成员契约 |
|---|---|---|
| 核心 | `std.json` `std.format` `std.collection` `std.str` `std.math` `std.test` `std.result` | [标准库·核心册](wlwl-v1.0-stdlib-core.md) |
| 文本 | `std.text` `std.sanitize` `std.regex` | [标准库·文本册](wlwl-v1.0-stdlib-text.md) |
| 系统 | `std.io` `std.fs` `std.time` `std.rand` `std.env` `std.process` `std.compress` `std.net` | [标准库·系统册](wlwl-v1.0-stdlib-system.md) |
| 安全 | `std.encode` | [标准库·安全册](wlwl-v1.0-stdlib-security.md) |
| 官方包 | `ai` `agent` | [标准库·官方包册](wlwl-v1.0-stdlib-official.md) |

**说明**:分册只是**编排**,不改变成员的规范强度 —— 同一个命名空间编在哪一册,它的成员都承担同样的规范性义务(总纲 §0.6 规则 1)。

## 附录 A 成员注册镜像

**附录 A 是规范性的。** 表体登记 v1.0 标准库的成员面:一行一个命名空间,「成员」列是该命名空间在本版的**全部**成员。

附录 A 与各标准库册的成员表**必须**集合相等(§0.7 规则 3);一致性册 §6.1 第 6 项把它列为发布前的机械检查项。

成员名后的 `[stub]` 标记表示该成员的**返回值形状**不在公共面内(一致性册 §4.4):实现侧**可以**把桩回包替换为真实实现,替换**不**构成破坏性变更。标记**不**改变「成员集合相等」这条规则 —— 它是集合内某个成员的附加属性。

| 命名空间 | 成员 | 层 | v1.0 册 |
|---|---|---|---|
| `std.io` | `PRINT` `INPUT` `PRINT_ERR` | R2 | 系统 |
| `std.fs` | `READ_FILE` `WRITE_FILE` `EXISTS` `PATH_JOIN` `PATH_DIR` `PATH_BASE` `PATH_EXT` `PATH_NORMALIZE` `LIST_DIR` `WALK_DIR` `MKDIR` `MKDIRS` `REMOVE` `COPY` `MOVE` `FILE_SIZE` `READ_BYTES` `WRITE_BYTES` | R2 | 系统 |
| `std.json` | `PARSE` `STRINGIFY` `JSON_TOKENIZE` `JSON_LOCATE` `JSON_VALIDATE` `JSON_GET` `JSON_SET` `JSON_DELETE` `JSON_MERGE` `JSON_CHUNK_ITEMS` `JSON_CHECK` | R2 | 核心 |
| `std.format` | `FORMAT` | R2 | 核心 |
| `std.encode` | `BASE64_ENCODE` `BASE64_DECODE` `HEX_ENCODE` `HEX_DECODE` `URL_ENCODE` `URL_DECODE` `SHA256` `HMAC_SHA256` `PBKDF2_ITER` `ARGON2ID` `RANDOM_BYTES` `RANDOM_HEX` `TIMING_SAFE_EQ` `UTF8_ENCODE` `UTF8_DECODE` `BYTE_LEN` | R2 | 安全 |
| `std.time` | `NOW` `MONOTONIC` `SLEEP` `TEST_BUBBLE` `ADVANCE` `TIMEOUT` `SLEEP_ASYNC` | R2 | 系统 |
| `std.regex` | `RE` `RE_TEST` `RE_SEARCH` `RE_FIND_ALL` `RE_REPLACE` `RE_SPLIT` `RE_GROUP_COUNT` | R2 | 文本 |
| `std.rand` | `SEED` `UNIFORM` `INT_RANGE` `CODEPOINT` | R2 | 系统 |
| `std.env` | `ENV_GET` `ENV_SET` `ENV_KEYS` `ARGS` `SCRIPT_PATH` | R2 | 系统 |
| `std.process` | `PROCESS_RUN` `PROCESS_ID` | R2 | 系统 |
| `std.text` | `TO_UPPER` `TO_LOWER` `NFC` `NFD` `NFC_QC` `GRAPHEME_COUNT` `WIDTH` `TO_INT` | R2 | 文本 |
| `std.sanitize` | `HTML_ESCAPE` `HTML_UNESCAPE` `HTML_SANITIZE` `HTML_SANITIZE_SCRIPT` `CSS_ESCAPE_IDENT` `CSS_ESCAPE_STRING` `HTML_SANITIZE_CSS` `HTML_SANITIZE_URL` | R2 | 文本 |
| `std.collection` | `MAP` `FILTER` `REDUCE` `SORT` `SORT_BY` `ZIP` `RANGE` `ANY` `ALL` `FIND` `ENUMERATE` `TAKE` `DROP` `FLAT` `UNIQ` `GROUP_BY` `JOIN` `CHUNK` `WINDOW` `DEDUP_BY` `MIN_BY` `MAX_BY` `SUM` `PRODUCT` `FOLD_RIGHT` `POSITION` `KEY_BY` | 混合(R1 门面 + R2 `RANGE`/`MAP`/`FILTER`/`CHUNK`/`WINDOW`/`ENUMERATE`/`ZIP`/`UNIQ`/`FLAT`/`JOIN`/`GROUP_BY`/`DEDUP_BY`/`KEY_BY`) | 核心 |
| `std.str` | `JOIN` `SPLIT_LINES` `CHAR_AT` `COUNT` `QUOTE` `INDEX_OF` `CONTAINS_SUB` `CAT` | 混合(R1 `JOIN`/`SPLIT_LINES`/`CHAR_AT`/`COUNT`/`QUOTE`/`INDEX_OF`/`CONTAINS_SUB` + R2 `CAT`) | 核心 |
| `std.math` | `ABS` `MIN` `MAX` `FLOOR` `CEIL` `ROUND` `SQRT` `POW` `CLAMP` `PI` `E` `LN` `LOG2` `LOG10` `EXP` `TRUNC` `SIN` `COS` `TAN` `ASIN` `ACOS` `ATAN` `ATAN2` `SINH` `COSH` `TANH` `SIGN` `DIV_CEIL` `GCD` `LCM` `IS_SQRT` `POW_MOD` `DECIMAL` `DECIMAL_ADD` `DECIMAL_SUB` `DECIMAL_MUL` `DECIMAL_DIV` `DECIMAL_CMP` `DECIMAL_TO_STRING` `DECIMAL_ROUND` `FRACTION` `FRACTION_ADD` `FRACTION_SUB` `FRACTION_MUL` `FRACTION_DIV` `FRACTION_CMP` `FRACTION_TO_STRING` `FRACTION_TO_DECIMAL` `APPROX_EQ` `IS_NAN` `IS_INF` `SIGNIFICANT_DIGITS` `MEDIAN` `MODE` `VARIANCE_S` `VARIANCE_P` `STDDEV_S` `STDDEV_P` `PERCENTILE` `CORRELATION` `LINEAR_FIT` | 混合 | 核心 |
| `std.test` | `TEST` `ASSERT` `ASSERT_EQ` `ASSERT_NEQ` `EXPECT_ERR` `RUN_TESTS` | 混合 | 核心 |
| `ai` | `ASK` `EMBED` `COMPLETE` `ASK_STREAM` `ASK_ALL` | R2 | 官方包 |
| `agent` | `TASK` `TOOL` `CALL_TOOL` `[stub]` `MODEL` `CONTEXT` | R2 | 官方包 |
| `std.compress` | `ZSTD_COMPRESS` `ZSTD_DECOMPRESS` `ZLIB_COMPRESS` `ZLIB_DECOMPRESS` `GZIP_COMPRESS` `GZIP_DECOMPRESS` `DEFLATE_RAW_COMPRESS` `DEFLATE_RAW_DECOMPRESS` | R2 | 系统 |
| `std.net` | `URL_PARSE` `URL_JOIN` `HTTP_GET` `HTTP_POST` `HTTP_REQUEST` `TCP_LISTEN` `TCP_ACCEPT` `TCP_RECV` `TCP_SEND` `TCP_PEER` `TCP_LOCAL_ADDR` `TCP_SET_TIMEOUT` `TCP_CLOSE` `TCP_CLOSE_LISTENER` | R2 | 系统 |
| `std.result` | `RESULT_MAP` `AND_THEN` `FLATTEN` `NULL_OR` `RESULT_ALL` `RESULT_ANY` `TAKE_ERR` | 混合(R1 `RESULT_MAP`/`AND_THEN`/`FLATTEN`/`NULL_OR` + R2 `RESULT_ALL`/`RESULT_ANY`/`TAKE_ERR`) | 核心 |
