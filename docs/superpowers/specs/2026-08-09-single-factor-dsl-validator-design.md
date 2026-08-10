# 单因子 DSL 自动验证器设计

- 日期：2026-08-09
- 状态：待评审
- 已定口径：**DSL 公式输入**（受限 Python 表达式 + AST 白名单）· **只做单独验证**（不做对现有池的增量/入池分析）· **无判决 + 自动摘要** · **加基准对比** · 输出 **markdown 记分卡 + HTML 图表页**

## 1. 背景与问题

平台现有入口都是绑定特定因子池的 runner（composite 报告 / alpha101 A/B/C / 量价 OOS），没有"任意给一个因子 → 自动全流程验证"的统一入口。全流程各环节已齐备（见 [[platform-completeness-gaps]] 与 `2026-08-09-factor-scorecard-design.md`）。本设计新增一层**单因子摄入 + 编排**：输入一个 DSL 公式串，自动因果求值成因子面板，跑完整单因子验证，连同一小组基准 alpha 并排对比，产出一份人读的记分卡（markdown）+ 一个自包含 HTML 图表页。**不产生新的统计判定，只汇总与呈现。**

## 2. 目标 / 非目标

**目标**
- DSL：把公式串安全地因果求值成因子面板（复用现有 alpha101 算子）。
- 单因子全流程验证：多 horizon IC/ICIR/RankIC + 显著性、分组单调性、单因子组合+成本+容量、regime 稳定性。
- 基准对比：一小组固定基准（DSL 串，可覆盖）走相同指标并排；相关性方阵（新因子 vs 基准）。
- 输出：`scorecard.md`（自动摘要置顶，无判决）+ `scorecard.html`（自包含内联 SVG 图表）+ CSV + `formula.txt` + 台账追加。
- CLI 子命令 `validate-factor` + 可编程入口 `validate_factor(...)`。

**非目标（本次不做）**
- 判决引擎（PASS/FAIL）——明确不做。
- 增量/入池分析（该因子加进现有组合的边际贡献）——用户明确只要单独验证。
- 自造 DSL 文法/解析器——用 Python AST 白名单（方案 A）。
- 新的显著性方法学——复用 `significance.evaluate_factor_grid`。

## 3. 模块边界

**新增**
- `research_platform/formula_dsl.py` —— AST 白名单解析 + 因果求值，纯函数无 IO。
- `research_platform/scorecard_html.py` —— `render_scorecard_html(...)`：自包含 HTML + 内联 SVG 图表，无外部依赖。
- `scripts/validate_factor.py` —— `validate_factor(...)` 编排入口。
- `research_platform/cli.py` —— 加 `validate-factor` 子命令（薄封装，调 `validate_factor`）。

**复用**：`market_data`（OHLCV bundle）、`alpha101`（算子 + `_input_panels`）、`providers.load_pit_context`（PIT universe，见 `run_research_platform_validation`）、`preprocessing`（标准化 + 行业中性变体）、`evaluation`（`evaluate_ic` / `run_quantile_backtest` / `generate_purged_folds`）、`significance.evaluate_factor_grid`（多 horizon 块自助族校正，Phase B 口径见 `scripts/diag_factor_evidence.py`）、`scorecard`（`build_factor_scorecard` / `build_group_backtest` / `build_correlation_views` / `render_scorecard_markdown` / `write_scorecard` / `sortino_ratio` / `calmar_ratio` / `win_rate`）、`portfolio`（`build_buffered_targets` / `simulate_portfolio` / `simulate_portfolio_liquidity_aware` / `capacity_curve`）、`regime`、`registry`（台账）。

## 4. DSL 设计（`formula_dsl.py`）

- `ALLOWED_INPUTS = {"open","high","low","close","volume","returns","vwap","adv20"}` —— 与 `alpha101._input_panels` 产出的面板键一致（`volume`/`adv20` 为美元成交量语义）。
- `ALLOWED_OPERATORS` —— dict `name -> callable`，直接绑 `alpha101` 的模块级算子：`rank, delay, delta, ts_sum, ts_min, ts_max, stddev, ts_rank, correlation, covariance, sign, signed_power, safe_divide, where, adv`；外加两个安全逐元素包装 `log`（`np.log`，非正数→NaN）、`abs`（`np.abs`），均返回有限值面板（inf→NaN）。**不新造分析型算子。**
- `validate_ast(tree)` —— 遍历，仅放行：`Expression`、`Call`（`func` 必须是白名单里的 `Name`；不允许关键字实参/`*args`/`**kwargs`）、`Name`（必须在 `ALLOWED_INPUTS ∪ ALLOWED_OPERATORS`）、数字 `Constant`、`BinOp`（`+ - * / ** %`）、`UnaryOp`（`USub/UAdd`）。其余节点（`Attribute`、`Subscript`、`Lambda`、推导式、`Import`、`Starred`、`BoolOp` 等）一律抛 `FormulaError`，消息点名违规节点/名字并附允许清单。
- `evaluate_formula(expr, panels) -> DataFrame` —— `ast.parse(expr, mode="eval")` → `validate_ast` → `eval(compile(tree,...), {"__builtins__": {}}, namespace)`，`namespace = ALLOWED_OPERATORS | {name: panels[name] for name in used_inputs}`；结果 `_finite`（inf→NaN）。
- 因果性：所有算子皆因果（`delay/delta/ts_*` 向后看），白名单里不存在前视构造；信号在 t、执行 t+1（下游对齐 forward return + purge，与 alpha101 一致）。

## 5. 基准集

- `BENCHMARK_ALPHAS: dict[str, str]`（DSL 串，模块常量，可通过 `validate_factor(..., benchmarks=...)` 覆盖）。默认小集合：
  - `alpha101_012 = "sign(delta(volume,1)) * (-delta(close,1))"`
  - `alpha101_101 = "(close - open) / ((high - low) + 0.001)"`
  - `reversal_5d = "-(close / delay(close,5) - 1)"`
  - `momentum_126d = "delay(close,5) / delay(close,126) - 1"`
- 新因子 + 基准合成一个 `factor_panels: dict[name -> DataFrame]`，全部走**相同**的指标路径。名字冲突（新因子取名撞基准）→ 报错。

## 6. 编排数据流（`validate_factor`）

```
formula(+name) [+ benchmarks + config]
  → load_research_ohlcv(cached) → alpha101._input_panels
  → evaluate_formula(new) + evaluate_formula(each benchmark)  → factor_panels
  → load_pit_context(dates, columns)  → PIT member_mask + industry(masked)
  → 每个 panel: member 掩码 → 变体 {raw: standardize, neutral: industry-neutral}
  → 多 horizon IC 网格 {1,5,10,21,42}: 每 h 算 forward_h, evaluate_ic(spearman) 对新因子+基准都算(供参照与画图)
        → significance.evaluate_factor_grid( **新因子** 的 (new,horizon) IC 面板 )  (studentized max-T 族校正, 族=新因子×horizon)
        → 基准的 IC-by-horizon 仅作参照, 不并入新因子的族校正(否则平白增加假设数、过度惩罚被验因子)
  → 主 horizon(5): build_factor_scorecard + build_group_backtest + build_correlation_views(factor_panels)
  → 每 panel: build_buffered_targets(主 h) → simulate_portfolio(_liquidity_aware) → 组合表(年化/夏普/Sortino/Calmar/胜率/换手/成本) ; capacity_curve(AUM 网格)
  → regime: 每 panel 逐年 IC 符号一致性
  → auto_summary(new 因子的最佳 horizon IC/t、单调性、扣成本 Sharpe、容量$)
  → render_scorecard_markdown(+summary) + render_scorecard_html(...)
  → write: scorecard.md, scorecard.html, *.csv, formula.txt, ledger 追加
```

- **变体**：`raw`（标准化）+ `neutral`（行业中性，复用 `build_industry_score_variants` 的 strict/`neutralize_panel`）。因子层与显著性对两变体各出一份；组合/容量默认用 `raw`（可配置）。
- **组合表**：新增一个编排内的小装配 `build_factor_portfolio_table(factor_panels, close, asset_returns, industry, adv_dollar, config)`，逐因子构缓冲组合 + 模拟，复用 `scorecard.sortino_ratio/calmar_ratio/win_rate` 拼指标（`build_portfolio_scorecard` 绑 OOS experiment 形状，这里不套用它）。
- **组合表口径修订（gross/net + 方向拟合）**：为把"信号质量"与"可交易性"、"符号反了"与"没 alpha"分开，逐因子组合改为：① 按该因子 IC 符号**拟合方向**再交易（`direction = sign(mean IC)`，score×direction 后建仓），列出 `direction`；② 同时用 `cost_bps=0` 与 `cost_bps=10` 各模拟一次，报 `gross_sharpe` / `net_sharpe` / `cost_drag`(=gross 年化 − net 年化)。自动摘要同时给"方向拟合后 扣成本前/后 Sharpe"。容量沿用方向拟合后的目标权重。这样 reversal 这类"gross 正、net 负"的因子一眼看出是被换手成本吃掉，而非无信号；反号因子（如 alpha101_101）不再因符号被双重惩罚。
- **slug** = 公式串的 sha256 前 12 位（确定性、无时间戳）；输出目录 `outputs/factor_validation/<slug>/`。

## 7. 指标定义

单因子/组合/分组/相关的口径**沿用** `2026-08-09-factor-scorecard-design.md` §4（RankIC/t、ICIR 年化、命中率、单调性、多空 t、秩换手；Sortino/Calmar/胜率；分组年化/夏普/累计；相关方阵）。本设计新增两条口径：
- **多 horizon 显著性网格**：对**新因子**在网格每个 h 组一列 `(new,horizon)` 的逐日 IC，交 `evaluate_factor_grid` 做 studentized 块自助 max-T 族校正（**族 = 新因子 × horizon**），报每格 `ic_bar/z_stat/p_global`。口径与 Phase B (`diag_factor_evidence`) 一致，但族只含被验因子——基准的逐 horizon IC 照算、用于并排表与折线图，**不并入族校正**（避免平白增加假设数、过度惩罚被验因子）。
- **自动摘要**（无判决）：一句话，取新因子在显著性网格里 |z| 最大的 horizon，报该 h 的 `RankIC`、`t/z`、是否 `p_global<0.05`、分组 `monotonicity`、`raw` 组合扣成本 `Sharpe`、容量（net Sharpe 掉到某阈值前的 AUM）。纯陈述，不给 PASS/FAIL。

## 8. 产出物

`outputs/factor_validation/<slug>/`：
- `scorecard.md` —— 自动摘要置顶；随后 因子层表（新+基准并排，raw/neutral）、多 horizon 显著性表、分组回测、组合表、容量表、regime 表、相关方阵（新 vs 基准）。四舍五入、列名可读（复用 `render_scorecard_markdown`）。
- `scorecard.html` —— 见 §9。
- `factor_scorecard.csv` 等 CSV 副本（复用 `write_scorecard`）。
- `formula.txt` —— 新因子名+公式串、基准集、config、universe/horizon 网格、data fingerprint（可复现）。
- 台账：`registry` 追加一行（公式、universe、horizon 网格、新因子最佳格的 ic_bar/p_global、组合 Sharpe）。

## 9. HTML 图表页（`scorecard_html.py`）

- `render_scorecard_html(tables, summary, ...) -> str` —— **自包含**：无 `<script src>`、无外部 CSS/字体/图片，全部内联；图表用**手绘内联 SVG**（无 JS、无图表库），确定性（同输入→同字节），主题中性（浅底深字，不依赖 dark/light）。
- 三张图：
  1. 分组柱状图：各因子（新+基准）的 `RankIC`、`ICIR(年)`、`raw` 扣成本 `Sharpe`。
  2. 相关性热力图：`build_correlation_views` 的因子值相关方阵，SVG 方格按值配色（发散色阶，格内标数）。
  3. IC-by-horizon 折线：新因子 vs 基准在 horizon 网格上的 RankIC。
- 顶部同样印自动摘要 + "无判决"说明。原子写（复用 `reporting._atomic_text`）。

## 10. 错误处理

- 公式违规/未知输入名 → `FormulaError`，消息点名并列允许的输入/算子。
- 退化因子（全 NaN / 名数不足 `min_names`）→ 记分卡该因子标注"覆盖不足"，不崩（`build_*` 已有退化守卫）。
- 小 universe / 成本上限不可行 → 复用 `run_oos_alpha_validation._check_universe_feasibility` 的守卫（提取为可复用函数或在编排里同款前置检查）。
- 基准公式本身出错 → 跳过该基准并在记分卡/摘要里记一条 warning，不影响新因子验证。

## 11. 测试计划

- **DSL**：①`alpha101_012` 的公式串求值 == `build_alpha101_factors` 中 id 12 面板（数值一致）；②白名单拒 `__import__("os")` / `close.__class__` / `close[0]` / `lambda x:x` / 非白名单调用 → `FormulaError`；③未知名 `foo` → 报错列清单；④`log(volume)` 对非正数→NaN、inf→NaN。
- **基准**：默认基准集全部 parse+求值成功；合并 `factor_panels` 后每因子一行；相关方阵 (k+1)×(k+1) 对角 1。
- **编排**：合成 OHLCV bundle 跑 `validate_factor` → `scorecard.md`/`scorecard.html`/`formula.txt` 存在、含新因子+基准名、台账追加一行；确定性（同公式→同 slug + 同字节）。
- **自动摘要**：构造一个在某 h 显著、其余不显著的因子，断言摘要挑中该 h。
- **HTML**：`render_scorecard_html` 返回自包含 HTML，含各因子名与三张 SVG；**断言不含 `http://`/`https://`/`<script src`**（全内联）；确定性。

## 12. 风险与取舍

- AST 白名单必须写严：显式列白名单节点类型 + 名字，默认拒绝（deny-by-default），并对 `eval` 传空 `__builtins__`。测试覆盖注入面（import/attr/subscript/lambda）。
- 手绘 SVG 图表比图表库朴素，但换来零依赖 + 确定性 + 自包含（贴合平台离线可复现）。够用即可，不追求交互。
- ICIR 年化 `sqrt(ppy/h)` 在重叠样本下偏乐观，表中同给未年化值并注明（沿用记分卡口径）。
- 基准集是"参照系"而非"入池判据"——摘要不据此下任何相对判决。
