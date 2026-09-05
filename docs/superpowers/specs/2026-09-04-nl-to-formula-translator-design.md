# 自然语言 → 因子公式 翻译层设计（单期交付，含 LLM）

- 日期：2026-09-04（2026-09-05 修订：把原 Phase 2 的 LLM 并回本期）
- 状态：待评审
- 已定口径：**翻译层放在项目内**（不是只在会话里）· **超纲则拒绝 + 说明缺什么 + 给最接近变体，绝不悄悄用代理字段** · **默认确认，`--yes` 可跳过** · **技术路子 = 能力卡 + 结构化输出 + DSL 校验闭环** · **本期直接接上 Claude，交付真·端到端**

## 1. 背景与问题

平台已能做到"给一个 DSL 公式 → 全流程单因子验证 + alpha101 基准对比 + 记分卡（md/HTML）+ 台账"。目标是把入口前移一步：**用自然语言描述因子想法**，自动转成可验证的公式，再走现有工作流。

关键约束（真实踩过的坑）：DSL 词汇表只有 **8 个输入**（`open/high/low/close/volume/returns/vwap/adv20`，全部 OHLCV 派生）+ **17 个因果算子**。自然语言想法极易越界（分析师预期、期权隐含波动率、情绪分、新闻、库存、经营现金流），此前一批 WorldQuant alpha 正是因此**一个都无法在本平台复现**。因此翻译层最重要的能力不是"能翻"，而是**能诚实地判定"表达不了"并说清缺什么**。

**能力卡不是翻译器**，这点必须写清楚以免混淆：能力卡是从白名单自动生成的**词汇表参考 + 防幻觉护栏**，同时充当 LLM 的提示词素材。链路是：

```
你的想法 + 能力卡 → LLM(翻译器) → 公式 → validate_ast(唯一裁判) → 现有回测流程
```

## 2. 目标 / 非目标

**目标**
- 从 DSL 白名单**自动生成能力卡**，作为防幻觉的单一真源，且可独立使用（`vocabulary` 子命令）。
- 定义翻译结果的**结构化契约**与**可插拔翻译器协议**。
- **接上 Claude**：`ClaudeTranslator` 用 `anthropic` SDK 的结构化输出直出契约对象。
- 实现**校验-重试闭环**：以 `validate_ast` 为唯一裁判，非法公式把报错喂回翻译器重试。
- **优雅降级**：没装 `anthropic` 或没有凭证时不崩，退回"打印能力卡 + 指引"，人工也能用。
- **溯源**：自然语言原文、模型 id、翻译元信息写入 `formula.txt` 与台账，保证跑分可复现。
- CLI：`vocabulary`（打印能力卡）与 `validate-idea`（含确认门与 `--yes`）。
- 单测**默认全部不联网**；另留一个默认跳过的联网冒烟测试。

**非目标**
- 不新增数据字段（分析师/期权/情绪/新闻/库存仍然没有）。
- 不改动 DSL 白名单语义、`validate_factor` 验证流程、alpha101 基准、记分卡输出。
- 不做多轮对话式澄清（一次翻译 + 至多 2 次因非法公式的重试）。
- 不做 prompt caching（能力卡前缀太短，够不到最小可缓存长度，加了也不生效）。

## 3. 模块边界

**新增**
- `research_platform/formula_dsl.py` 内新增 `describe_vocabulary()`（放在 DSL 模块内，保证与白名单同源）。
- `research_platform/nl_translator.py`（新模块）：`TranslationPayload`（Pydantic）、`TranslationResult`（frozen dataclass）、`Translator` 协议、`translate_idea()`、`StubTranslator`、`ClaudeTranslator`、`resolve_translator()`。
- `research_platform/cli.py`：新增 `vocabulary`、`validate-idea` 两个子命令。

**修改**
- `scripts/validate_factor.py`：`validate_factor(...)` 增加可选参数 `provenance: dict | None = None`，合并进 `formula.txt` 与台账 `ExperimentRecord.metadata`。向后兼容（默认 `None` 时行为不变）。
- `pyproject.toml`：核心依赖加 `pydantic>=2`；新增可选依赖组 `llm = ["anthropic>=0.40"]`。

**复用（不改）**：`formula_dsl.validate_ast / evaluate_formula / FormulaError`、`validate_factor` 全流程、`DEFAULT_BENCHMARKS`（alpha101 基准）、`scorecard*`、`registry`。

## 4. 组件

### 4.1 能力卡 `describe_vocabulary() -> str`
从 `ALLOWED_INPUTS` 与 `ALLOWED_OPERATORS` **自动生成**，算子签名用 `inspect.signature` 反射。内容分四段：
1. **INPUTS**：8 个输入名 + 语义注解（`volume`/`adv20` 为 OHLC4 **美元**成交量，alpha101 口径；`returns` 为日收益）。
2. **OPERATORS**：每个算子一行 `name(参数签名)`。
3. **因果性约定**：所有算子只向后看；信号在 t、执行在 t+1；不存在前视构造；不支持关键字参数、布尔常量、下标、属性访问。
4. **EXAMPLES**：3–4 条精选范例（5 日反转、alpha101_012 式量价、6 月动量、相关性型）。

确定性：同一份白名单永远生成同一段文本（无时间戳/随机）。

### 4.2 结构化契约

**`TranslationPayload`（Pydantic `BaseModel`，翻译器的返回类型）**
`feasible: bool` · `formula: str | None` · `explanation: str` · `missing_data: list[str] = []` · `nearest_formula: str | None = None` · `nearest_caveat: str | None = None`

它同时是 **SDK 的 `output_format` schema**——形状与类型由 SDK 校验，**本模块不再手写"查缺键/类型错"那一层**。

**`TranslationResult`（frozen dataclass，`translate_idea` 的返回类型）**
在 payload 字段之外附加：`attempts: int` · `translator: str` · `model: str | None` · `raw_output: str`。

走结构化输出后模型不再返回自由文本，故 `raw_output` 定义为**最后一次 payload 的 JSON**（`payload.model_dump_json()`），用于重试耗尽时回显"它到底给了什么"。

### 4.3 翻译器协议 `Translator`
```python
class Translator(Protocol):
    name: str
    model: str | None
    def __call__(self, idea: str, vocabulary: str,
                 feedback: str | None = None) -> TranslationPayload: ...
```
`feedback` 在重试时携带上一次的 `FormulaError` 原文。

### 4.4 `ClaudeTranslator`

- 客户端：零参 `anthropic.Anthropic()`，凭证从环境解析（`ANTHROPIC_API_KEY`）。**不接受 key 作为 CLI 参数，不打印、不落盘任何凭证。**
- 模型：`claude-opus-5`。
- 调用：
```python
resp = client.messages.parse(
    model="claude-opus-5",
    max_tokens=8000,
    system=SYSTEM_PROMPT,                 # 含能力卡 + 拒绝契约
    messages=[{"role": "user", "content": user_msg}],
    output_format=TranslationPayload,     # Pydantic 模型
)
return resp.parsed_output                 # 已校验的 TranslationPayload 实例
```
- 依赖注入：`ClaudeTranslator(client=None)`，`None` 时自建；测试传入假 client。
- **系统提示词把拒绝契约写死**：只准使用能力卡列出的输入与算子；表达不了就 `feasible=false` 并在 `missing_data` 列出缺的数据字段（用业务语言，如"分析师一致预期 EPS"）；**禁止发明字段、禁止用相近字段顶替**；`nearest_formula` 若给，也只能用表内元素，并在 `nearest_caveat` 说明它与原想法的差别。
- 用户消息：想法原文；重试时追加上一轮公式与 `FormulaError` 原文。
- 异常按 SDK 类型化处理，不做字符串匹配：`AuthenticationError` → 当作翻译器不可用（exit 2 + 指引）；`RateLimitError` / `APIConnectionError`（SDK 自带退避重试）耗尽后 → 运行错误 exit 1；`BadRequestError` → 运行错误 exit 1 并回显消息。
- **`anthropic` 必须懒加载**：`nl_translator.py` **模块级不得 `import anthropic`**（本仓当前环境就没装它）。客户端构造与异常归类都在函数内部惰性导入；异常归类用 `_classify(exc)` 辅助函数做 `isinstance` 判断，SDK 缺席时一律归为运行错误。否则 `import nl_translator` 会在没装包的机器上直接炸，优雅降级就是空话。
- 成本：能力卡 ~1–2K token + 想法，一次翻译远低于一分钱；不做缓存。

### 4.5 `resolve_translator() -> Translator | None`（注入缝）
1. `import anthropic` 失败 → `None`；
2. 构造 `anthropic.Anthropic()` 失败（未解析到凭证）→ `None`；
3. 否则 → `ClaudeTranslator(client=...)`。

CLI 只通过这一个模块级函数取翻译器，测试用 monkeypatch 注入 stub。返回 `None` 时 CLI 走能力卡指引分支——**没 key 的人照样能用**。

### 4.6 校验-重试闭环 `translate_idea(idea, translator, max_retries=2) -> TranslationResult`
1. 取能力卡；循环最多 `max_retries + 1` 次：
2. 调 `translator(idea, vocabulary, feedback)` 得到 `TranslationPayload`（形状已由 SDK/类型保证）；
3. `feasible=False` → 若带 `nearest_formula`，**用 `validate_ast` 校验**，不合法则丢弃并在 `nearest_caveat` 注明；返回不可行结果；
4. `feasible=True` → 用 `validate_ast` 校验 `formula`：合法则返回可行结果（`attempts` 记录次数）；不合法则把 `FormulaError` 原文作为 `feedback` 继续重试；
5. 次数耗尽 → 返回 `feasible=False`，`explanation` 说明"翻译器多次生成非法公式"，并附最后一次 `raw_output`。

**DSL 是唯一裁判**：翻译器自称可行不算数，必须过 `validate_ast`。

### 4.7 `StubTranslator`
按预置脚本依次返回若干 `TranslationPayload`（供测试与离线演练），`name="stub"`、`model=None`。不经 CLI 暴露。

### 4.8 溯源 `provenance`
`validate_factor(..., provenance=None)`：非空时把键值追加进 `formula.txt`，并放入台账 `ExperimentRecord.metadata["provenance"]`。`validate-idea` 传入 `{"idea": 原文, "explanation": ..., "translator": ..., "model": ..., "attempts": ...}`。**翻译可以不确定，但跑分必须可复现** —— 公式与来源都落盘；拿到 `formula.txt` 里的公式重跑 `validate-factor`，结果逐位一致。

### 4.9 CLI
- `vocabulary` —— 打印能力卡，exit 0。
- `validate-idea --idea "..." [--name NAME] [--yes] [--output-dir DIR]`：
  - 无翻译器（未装 `anthropic` / 无凭证 / 鉴权失败）→ 打印能力卡 + 可操作指引（"装 `pip install -e '.[llm]'` 并设置 `ANTHROPIC_API_KEY`；或把这段能力卡与你的想法交给任意 LLM，拿到公式后用 `validate-factor --formula=...` 跑"），exit 2。
  - 有翻译器：翻译 → 不可行则打印 `missing_data` 与（已校验合法的）最接近变体，**exit 2 且不跑**；可行则打印公式与解释，未加 `--yes` 时在 stdin 上确认，拒绝则 exit 3；确认或 `--yes` 则调 `validate_factor(formula, name=..., provenance=...)` 并打印摘要与输出目录。

**退出码**：0 成功 · 1 运行错误 · 2 不可行/翻译器不可用 · 3 用户拒绝。

## 5. 数据流

```
--idea "自然语言"
  → describe_vocabulary()                      (与白名单同源)
  → ClaudeTranslator: messages.parse(output_format=TranslationPayload)
  → validate_ast 校验（非法则把 FormulaError 喂回重试 ≤2）
  → 不可行 → 打印 missing_data + 已校验的 nearest → exit 2，不跑
  → 可行   → 展示公式+解释 →（--yes 或确认）
            → validate_factor(formula, provenance=...)   ← 现有全流程 + alpha101 基准
            → scorecard.md / scorecard.html / CSV / 台账
```

一条命令跑完：
```
factor-research validate-idea --idea "5日反转，按20日均量加权"
```

## 6. 错误处理

- 翻译器不可用（缺包/缺凭证/鉴权失败）→ 打印能力卡 + 指引，exit 2（可操作，非崩溃）。
- 公式非法 → `FormulaError` 原文喂回重试 ≤2；耗尽按不可行处理，附最后一次原始输出。
- `nearest_formula` 非法 → 丢弃，`nearest_caveat` 注明，不影响主流程。
- 不可行 → **绝不自动跑代理变体**（`nearest_formula` 只展示，跑不跑由你另起一条命令决定）。
- 用户拒绝确认 → exit 3，不产生任何输出文件。
- 限流/连接异常（SDK 退避重试后仍失败）→ exit 1，不产生半截输出。
- 翻译器其他异常 → 捕获为运行错误 exit 1。

## 7. 测试计划

**默认全部不联网。**
- **能力卡**：`describe_vocabulary()` 输出**包含每一个** `ALLOWED_INPUTS` 与 `ALLOWED_OPERATORS` 名称（防白名单/prompt 漂移，最关键的一条）；含因果性说明；两次调用字节一致。
- **`translate_idea`（StubTranslator）**：可行路径；不可行路径带 `missing_data`；首次非法公式、二次合法 → `attempts == 2`；重试耗尽 → `feasible=False`；`nearest_formula` 非法 → 被丢弃且有 caveat。
- **`ClaudeTranslator`（假 client 注入）**：透传 `parsed_output`；调用参数正确（`model="claude-opus-5"`、`output_format=TranslationPayload`、system 含能力卡）；`feedback` 出现在用户消息里。假 client 是鸭子类型对象，**不需要装 `anthropic`**，这几条在任何环境都跑。
- **异常归类**（`AuthenticationError` → 不可用；`RateLimitError` → 运行错误）：这几条必须引用 SDK 异常类，故用 `pytest.importorskip("anthropic")` 守住，未装包时跳过而非报错。
- **模块可导入性**：断言 `import research_platform.nl_translator` 在**没有 `anthropic`** 的环境下成功（本仓当前就是这个环境，等于长期看守这条）。
- **`resolve_translator`**：缺包 → `None`；构造抛异常 → `None`；正常 → 返回 `ClaudeTranslator`。
- **CLI**：`vocabulary` 输出含输入名；`validate-idea` 无翻译器时 exit 2 且打印指引；注入 stub 翻译器与假 `validate_factor` 时 `--yes` 跳过确认；不可行 → exit 2 且 `validate_factor` 未被调用（用 spy 断言）。
- **溯源**：`validate_factor(..., provenance={...})` 后 `formula.txt` 含该键值，台账 `metadata["provenance"]` 存在；`provenance=None` 时行为与现状一致。
- **联网冒烟（默认跳过）**：`@pytest.mark.skipif(not os.getenv("RUN_LLM_TESTS"))`，真调一次 Claude，断言"5日反转"能翻出过 `validate_ast` 的公式、且"用分析师一致预期"返回 `feasible=False` 且 `missing_data` 非空。

## 8. 风险与取舍

- **翻译不确定**：同一句话两次可能给出不同公式。三重兜底——确认门（默认要你点头）、`validate_ast`（唯一裁判）、provenance（公式落盘，跑分可复现）。
- **能力卡漂移**：靠"覆盖全部 input/operator"的测试守住；能力卡与白名单同源、自动生成，不存在两份真源。
- **拒绝契约靠提示词**：模型仍可能嘴硬说可行。`validate_ast` 挡掉发明的字段名（`unknown name '...'`），这是硬约束而非提示词约束；但"用 `volume` 顶替'机构成交额'"这种**语义**代理，AST 挡不住——由 `explanation` 展示 + 确认门交给你判断。这是本设计已知的、留给人的那一环。
- **成本与联网**：默认走网。没凭证不影响其余功能（优雅降级），CI 不联网。
- **表达力天花板**：只有 OHLCV 派生字段，大量想法本就不可行；这是数据层限制，翻译层只负责如实说明，不负责绕过。
