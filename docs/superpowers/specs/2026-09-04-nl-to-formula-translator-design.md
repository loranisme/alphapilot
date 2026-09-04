# 自然语言 → 因子公式 翻译层设计（Phase 1：不含 LLM）

- 日期：2026-09-04
- 状态：待评审
- 已定口径：**翻译层放在项目内**（不是只在会话里）· **超纲则拒绝 + 说明缺什么 + 给最接近变体，绝不悄悄用代理字段** · **默认确认，`--yes` 可跳过** · **技术路子 = 能力卡 + 结构化输出 + DSL 校验闭环** · **本期先不接 LLM，其余全部做完**

## 1. 背景与问题

平台已能做到"给一个 DSL 公式 → 全流程单因子验证 + alpha101 基准对比 + 记分卡（md/HTML）+ 台账"。目标是把入口前移一步：**用自然语言描述因子想法**，自动转成可验证的公式，再走现有工作流。

关键约束（真实踩过的坑）：DSL 词汇表只有 **8 个输入**（`open/high/low/close/volume/returns/vwap/adv20`，全部 OHLCV 派生）+ **17 个因果算子**。自然语言想法极易越界（分析师预期、期权隐含波动率、情绪分、新闻），此前一批 WorldQuant alpha 正是因此**一个都无法在本平台复现**。因此翻译层最重要的能力不是"能翻"，而是**能诚实地判定"表达不了"并说清缺什么**。

## 2. 目标 / 非目标

**目标（Phase 1）**
- 从 DSL 白名单**自动生成能力卡**，作为防幻觉的单一真源，且可独立使用。
- 定义翻译结果的**结构化契约**与**可插拔翻译器协议**。
- 实现**校验-重试闭环**：以 `validate_ast` 为唯一裁判，非法公式把报错喂回翻译器重试。
- **溯源**：自然语言原文与翻译元信息写入 `formula.txt` 与台账，保证跑分可复现。
- CLI：`vocabulary`（打印能力卡）与 `validate-idea`（含确认门与 `--yes`）。
- 全部单测**不联网**。

**非目标（本期明确不做）**
- **不接入任何 LLM 客户端**（Phase 2 再做）。因此本期**不提供**"自然语言进 → 结果出"的全自动链路。
- 不新增数据字段（分析师/期权/情绪/新闻仍然没有）。
- 不改动 DSL 白名单语义、`validate_factor` 验证流程、alpha101 基准、记分卡输出。

## 3. 模块边界

**新增**
- `research_platform/formula_dsl.py` 内新增 `describe_vocabulary()`（放在 DSL 模块内，保证与白名单同源）。
- `research_platform/nl_translator.py`（新模块）：`TranslationResult`、`Translator` 协议、`translate_idea()`、`StubTranslator`。
- `research_platform/cli.py`：新增 `vocabulary`、`validate-idea` 两个子命令。

**修改**
- `scripts/validate_factor.py`：`validate_factor(...)` 增加可选参数 `provenance: dict | None = None`，合并进 `formula.txt` 与台账 `ExperimentRecord.metadata`。向后兼容（默认 `None` 时行为不变）。

**复用（不改）**：`formula_dsl.validate_ast / evaluate_formula / FormulaError`、`validate_factor` 全流程、`DEFAULT_BENCHMARKS`（alpha101 基准）、`scorecard*`、`registry`。

## 4. 组件

### 4.1 能力卡 `describe_vocabulary() -> str`
从 `ALLOWED_INPUTS` 与 `ALLOWED_OPERATORS` **自动生成**，算子签名用 `inspect.signature` 反射。内容分四段：
1. **INPUTS**：8 个输入名 + 语义注解（`volume`/`adv20` 为 OHLC4 **美元**成交量，alpha101 口径；`returns` 为日收益）。
2. **OPERATORS**：每个算子一行 `name(参数签名)`。
3. **因果性约定**：所有算子只向后看；信号在 t、执行在 t+1；不存在前视构造。
4. **EXAMPLES**：3–4 条精选范例（5 日反转、alpha101_012 式量价、6 月动量、相关性型）。

确定性：同一份白名单永远生成同一段文本（无时间戳/随机）。

### 4.2 结构化契约 `TranslationResult`（frozen dataclass）
`feasible: bool` · `formula: str | None` · `explanation: str` · `missing_data: tuple[str, ...]` · `nearest_formula: str | None` · `nearest_caveat: str | None` · `attempts: int` · `translator: str` · `raw_output: str`

### 4.3 翻译器协议 `Translator`
```python
class Translator(Protocol):
    name: str
    def __call__(self, idea: str, vocabulary: str, feedback: str | None = None) -> dict: ...
```
返回 dict，键：`feasible, formula, explanation, missing_data, nearest_formula, nearest_caveat`。`feedback` 在重试时携带上一次的 `FormulaError` 原文。**Phase 2 的 LLM 只需实现这一个方法。**

### 4.4 校验-重试闭环 `translate_idea(idea, translator, max_retries=2) -> TranslationResult`
1. 取能力卡；循环最多 `max_retries + 1` 次：
2. 调 `translator(idea, vocabulary, feedback)`；
3. **载荷形状校验**：`feasible` 必须存在且为 bool；`explanation` 必须为 str；当 `feasible=True` 时 `formula` 必须为非空 str。其余键可缺省（`missing_data` 默认空、`nearest_formula`/`nearest_caveat` 默认 None）。缺键或类型错 → 记为畸形，把原因作为 `feedback` 重试；
4. `feasible=False` → 若带 `nearest_formula`，**用 `validate_ast` 校验**，不合法则丢弃并在 `nearest_caveat` 注明；返回不可行结果；
5. `feasible=True` → 用 `validate_ast` 校验 `formula`：合法则返回可行结果（`attempts` 记录次数）；不合法则把 `FormulaError` 原文作为 `feedback` 继续重试；
6. 次数耗尽 → 返回 `feasible=False`，`explanation` 说明"翻译器多次生成非法公式"，并附最后一次 `raw_output`。

**DSL 是唯一裁判**：翻译器自称可行不算数，必须过 `validate_ast`。

### 4.5 `StubTranslator`
按预置脚本依次返回若干载荷（供测试与端到端演练），`name="stub"`。

### 4.6 溯源 `provenance`
`validate_factor(..., provenance=None)`：非空时把键值追加进 `formula.txt`，并放入台账 `ExperimentRecord.metadata["provenance"]`。`validate-idea` 传入 `{"idea": 原文, "explanation": ..., "translator": ..., "attempts": ...}`。**翻译可以不确定，但跑分必须可复现** —— 公式与来源都落盘。

### 4.7 CLI
- `vocabulary` —— 打印能力卡，exit 0。
**翻译器解析（注入缝）**：CLI 通过模块级 `resolve_translator() -> Translator | None` 取翻译器。**Phase 1 该函数恒返回 `None`**（无内置翻译器，`StubTranslator` 仅供测试，不经 CLI 暴露），因此 `validate-idea` 在 Phase 1 **总是走能力卡指引分支**；测试通过 monkeypatch 该函数注入 stub 来覆盖其余分支。Phase 2 只需让它返回 `ClaudeTranslator`。

- `validate-idea --idea "..." [--name NAME] [--yes] [--output-dir DIR]`：
  - **Phase 1 无内置翻译器**：不报错退出，而是打印能力卡 + 可操作指引（"把这段能力卡与你的想法交给 LLM，拿到公式后用 `validate-factor --formula=...` 跑"），exit 2。
  - 有翻译器时：翻译 → 不可行则打印 `missing_data` 与（已校验合法的）最接近变体，**exit 2 且不跑**；可行则打印公式与解释，未加 `--yes` 时在 stdin 上确认，拒绝则 exit 3；确认或 `--yes` 则调 `validate_factor(formula, name=..., provenance=...)` 并打印摘要与输出目录。

**退出码**：0 成功 · 1 运行错误 · 2 不可行/翻译器不可用 · 3 用户拒绝。

## 5. 数据流

```
--idea "自然语言"
  → describe_vocabulary()            (与白名单同源)
  → translator(idea, vocabulary, feedback)   ← Phase 2 插 LLM
  → validate_ast 校验（非法则喂回错误重试 ≤2）
  → 不可行 → 打印 missing_data + 已校验的 nearest → exit 2，不跑
  → 可行   → 展示公式+解释 →（--yes 或确认）
            → validate_factor(formula, provenance=...)   ← 现有全流程 + alpha101 基准
            → scorecard.md / scorecard.html / CSV / 台账
```

## 6. 错误处理

- 翻译器未配置 → 打印能力卡 + 指引，exit 2（可操作，非崩溃）。
- 载荷畸形（缺键/类型错）→ 作为 feedback 重试；耗尽则 exit 2 并附原始输出。
- 公式非法 → `FormulaError` 原文喂回重试 ≤2；耗尽按不可行处理。
- `nearest_formula` 非法 → 丢弃，`nearest_caveat` 注明，不影响主流程。
- 不可行 → **绝不自动跑代理变体**。
- 用户拒绝确认 → exit 3，不产生任何输出文件。
- 翻译器自身抛异常 → 捕获并作为运行错误 exit 1，不产生半截输出。

## 7. 测试计划（全部不联网）

- **能力卡**：`describe_vocabulary()` 输出**包含每一个** `ALLOWED_INPUTS` 与 `ALLOWED_OPERATORS` 名称（防白名单/prompt 漂移，最关键的一条）；含因果性说明；两次调用字节一致。
- **`translate_idea`（StubTranslator）**：可行路径；不可行路径带 `missing_data`；首次非法公式、二次合法 → `attempts == 2`；重试耗尽 → `feasible=False`；`nearest_formula` 非法 → 被丢弃且有 caveat；载荷畸形 → 重试后不可行。
- **CLI**：`vocabulary` 输出含输入名；`validate-idea` 无翻译器时 exit 2 且打印指引；注入 stub 翻译器与假 `validate_factor` 时 `--yes` 跳过确认。
- **溯源**：`validate_factor(..., provenance={...})` 后 `formula.txt` 含该键值，台账 `metadata["provenance"]` 存在；`provenance=None` 时行为与现状一致。

## 8. Phase 2（本期不做，留好接口）

实现 `ClaudeTranslator`（满足 `Translator` 协议）+ `anthropic` 依赖 + API key 配置与缺失时的清晰报错。骨架、能力卡、校验闭环、CLI、溯源、测试均已就位，插上即通。

## 9. 风险与取舍

- **本期不交付全自动**：这是刻意的分期，已在非目标中写死，避免预期错位。
- **能力卡漂移**：靠"覆盖全部 input/operator"的测试守住。
- **协议可能微调**：真正接 LLM 时 `Translator` 签名或需小改；协议刻意保持极简（dict 进 dict 出）以降低返工成本。
- **表达力天花板**：只有 OHLCV 派生字段，大量想法本就不可行；这是数据层限制，翻译层只负责如实说明，不负责绕过。
