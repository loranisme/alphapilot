# 因子验证记分卡（scorecard）设计

- 日期：2026-08-09
- 状态：待评审
- 决策口径：**纯指标仪表盘，不出判决**（不引入 PASS/COND/REJECT 阈值，避免又造一个可过拟合的旋钮；判决由使用者依据指标自行下）

## 1. 背景与问题

平台目前把因子验证的证据散在 7–13 张 CSV 里（`fold_metrics` / `cost_stress` / `industry_exposure` / `regime_stability` / `group_stratification` / 消融的 `*_correlation` 等），没有一层把它们汇成"每因子一行 / 每组合一行"的可读仪表盘。研究员要判断"这因子真不真、稳不稳、做不做得了"，得自己翻多张表。

本设计新增一个**纯汇总层**：把已算好的证据 + 少量新命名指标，汇成两大板块的指标仪表盘（因子层 + 组合/回测层）＋ 分组回测 ＋ 相关性分析，覆盖单因子与多因子合成。**不产生新的统计判定，只做汇总与呈现。**

## 2. 目标 / 非目标

**目标**
- 单因子逐因子指标表（因子层）
- 组合/回测逐路径（raw/soft/strict）或多因子合成指标表（组合层）
- 分组（quantile）回测的**聚合**统计（每组年化/夏普/累计，非仅逐日组均值）
- 相关性分析接进单/多因子报告
- 一页人读仪表盘 `scorecard.md` + 对应 CSV

**非目标（本次不做）**
- 判决引擎（PASS/COND/REJECT）——明确不做
- 独立 CLI 入口 `validate-factors`——留待第二步（YAGNI）
- 新的显著性方法学——沿用现有 `significance.py` / 诊断，不改

## 3. 模块边界

新增 `research_platform/scorecard.py`（纯函数，无 IO、无网络）。runner 持有原始因子面板与实验结果，调用 scorecard 构造表格后落盘。reporting.py 保持专注，不承担逐单因子汇总。

- 输入：`factors: dict[str, DataFrame]`、`forward_returns`、`asset_returns`、`industry`、`experiment`（含 `scores` / `portfolios`）、`config`（horizon、n_groups、periods_per_year 等）、可选 `adv_dollar`。
- 输出：若干 `DataFrame`（见 §6）+ markdown 字符串。
- 依赖：`evaluation`（evaluate_ic、run_quantile_backtest、group_stratification_table）、`portfolio`（PortfolioResult、capacity_curve）、`correlation`（factor_value_correlation、ic_correlation、connected_correlation_clusters、factor_rank_turnover）、`regime`。

## 4. 指标定义（精确口径）

约定：`ppy` = periods_per_year（默认 252）；`h` = horizon（重平衡间隔）；IC 序列指逐日横截面 IC。

**板块① 因子层**（逐单因子一行）
- `rank_ic`：逐日 Spearman IC 的均值（= `evaluate_ic(method="spearman")` 的均值）
- `rank_ic_t`：`mean / (std / sqrt(n))`，n = 有效 IC 天数
- `pearson_ic`：逐日 Pearson IC 的均值
- `icir`：`ic_mean / ic_std`（每重平衡）；`icir_annualized = icir * sqrt(ppy / h)`
- `ic_hit_rate`：`mean(sign(ic_t) == sign(ic_mean))`
- `monotonicity`：组序(1..n) 与各组均值前向收益的 Spearman（取自分组回测）
- `long_short_mean` / `long_short_t`：top−bottom 组价差均值与朴素 t
- `rank_turnover`：`factor_rank_turnover`（方向对齐后）
- `coverage`、`n_obs`

**板块② 组合/回测层**（逐路径或多因子合成一行）
- 复用 `PortfolioResult.metrics`：`annualized_return`、`annualized_volatility`、`sharpe`、`max_drawdown`、`average_turnover`、`total_cost`
- 新增：
  - `sortino`：`annualized_return / (downside_dev * sqrt(ppy))`，`downside_dev = std(min(net_ret, 0), ddof=1)`；下行波动为 0 → NaN
  - `calmar`：`annualized_return / abs(max_drawdown)`；回撤为 0 → NaN
  - `win_rate`：`mean(net_return > 0)`（正收益期占比）
- 接入：`capacity`（④，给 ADV 时）、`regime_consistency`（③，IC 符号一致性）、`industry_exposure`（平均最大行业净暴露）

**分组回测**（每 因子/合成 × 组 一行）——新建 builder，区别于已有的 `group_stratification_table`（后者给逐路径的紧凑摘要，保留、不动）。本 builder 复用 `run_quantile_backtest` 的逐日各组收益，聚合成每组时序统计：
- 每日按得分分 `n_groups`（默认 5）等权分组，组收益 = 该组前向收益均值，得到每组收益序列
- 每组聚合：`annualized_return`、`sharpe`、`cumulative_return`、`avg_count`
- 组间：`monotonicity`（组序×组年化）、`long_short_sharpe`、`long_short_t`（top−bottom 序列）

**相关性分析**（均以**方阵**呈现，方便人读）
- `factor_value_correlation`：逐日横截面 Spearman 的中位数，输出 因子×因子 方阵（对角线 1）
- `ic_correlation`：IC 序列相关（带收缩），同样方阵
- `correlation_clusters`：`connected_correlation_clusters(@0.75)`，以"簇 → 成员"可读分组呈现（不再是逐对长表）

## 5. 数据流

```
runner
  ├─ factors, close/forward, asset_returns, industry, experiment(scores,portfolios)
  └─ scorecard.build_* ──► factor_scorecard / portfolio_scorecard / group_backtest
                            + correlation tables + scorecard.md（纯汇总，无判决）
                          └─► 落盘到 outputs/<experiment>/
```

## 6. 产出物

**人读优先**：`scorecard.md` 是主交付物，所有表用 markdown 表格渲染、数字按量纲四舍五入、列名用可读短标签；CSV 是机器留存副本。

`scorecard.md`（一页仪表盘，无判决）依次包含：
- **板块① 因子层表**：逐因子一行 —— RankIC / t / ICIR(年化) / IC命中率 / 单调性 / 多空t / 秩换手 / 覆盖率。IC 类保留 3–4 位小数，比率/百分比 1–2 位。
- **板块② 组合层表**：逐路径一行 —— 年化%/夏普/Sortino/最大回撤%/Calmar/胜率%/换手/成本/容量/regime一致/行业暴露。
- **分组回测表**：每组一行（组年化%、夏普、累计%、平均只数），末尾多空(夏普, t)。
- **相关性矩阵**：`因子值相关` 渲染成 因子×因子 方阵（对角线 1，保留 2 位，行列均带因子名）；`IC 相关` 同样方阵；`相关性聚类` 以"簇 → 成员"分组列出。

CSV 副本：`factor_scorecard.csv`、`portfolio_scorecard.csv`、`group_backtest.csv`、`factor_value_correlation.csv`（方阵）、`ic_correlation.csv`（方阵）、`correlation_clusters.csv`。

格式化：scorecard.py 内提供 `render_markdown(tables)` —— 对每张表四舍五入 + 列重命名 + `to_markdown`；相关矩阵用 `index=True` 保留行名以呈现方阵，指标表用 `index=False`。`write_scorecard(tables, markdown, output_dir)` 复用 `reporting._atomic_text` 原子写：先写 `scorecard.md`（主），再 `sorted(tables)` 落 CSV 副本；不改 `write_oos_report` 本身。先接进 OOS runner（`run_oos_alpha_validation`）。

（可选，非本次）相关矩阵的**彩色热力图** HTML/artifact 视图 —— 需要再单独提。

## 7. 测试计划

- 新指标单测（已知信号构造）：`icir`（IC 稳定→高 ICIR）、`ic_hit_rate`（全正→1）、`sortino`（无下行→NaN）、`calmar`（= 年化/|回撤|）、`win_rate`（正收益期占比）
- 分组回测聚合单测：单调信号 → 组年化单调↑、多空夏普>0
- 汇总单测：`build_factor_scorecard` / `build_portfolio_scorecard` 产出预期列集合、逐路径覆盖 raw/soft/strict
- 确定性：同输入两次运行结果一致（复用现有 real-runner 复现测试口径）
- 边界：空/NaN、名数不足 `min_names`、零波动（sharpe/sortino/calmar → NaN）

## 8. 风险与取舍

- ICIR 年化系数 `sqrt(ppy/h)` 是常见近似，重叠样本下偏乐观；表中同时给未年化 `icir` 与年化值，并在文档注明口径。
- 不出判决是刻意选择；若日后要 gate，应复用已有门禁而非新阈值（记录于此备忘）。
