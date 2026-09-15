# AlphaPilot

把**自然语言表达的因子假设**，翻译成**只用本仓已有数据字段**的可计算 alpha 公式，再自动送进完整的单因子验证流程，并与 Alpha101 基准因子对照。

核心不是"能翻译"，而是**当一个想法用现有数据表达不了时，系统必须如实拒绝、说清缺哪些数据、并给出最接近的可表达变体——绝不悄悄用相近字段顶替。**

## 设计主线

```
你的想法（自然语言）
      │
      ├── 能力卡 describe_vocabulary()   ← 由 DSL 白名单自动生成，防幻觉的单一真源
      ▼
   LLM 翻译器（Claude, 结构化输出）
      │
      ▼
   validate_ast()  ← 唯一裁判：翻译器自称可行不算数
      │
      ├── 不可行 → 打印缺失数据 + 已校验的最接近变体 → 停，不跑
      ▼
   validate_factor()  ← 现有全流程，一行未改
      │
      ▼
   记分卡 scorecard.md / scorecard.html + CSV + 实验台账
```

**能力卡不是翻译器。** 它是从白名单自动生成的词汇表参考与防幻觉护栏，同时充当 LLM 的提示词素材；真正做翻译的是 LLM，而最终裁判是 `validate_ast`。

完整设计见 [`docs/superpowers/specs/2026-09-04-nl-to-formula-translator-design.md`](docs/superpowers/specs/2026-09-04-nl-to-formula-translator-design.md)。

## 实现状态

| 环节 | 状态 |
|---|---|
| DSL 公式求值与 AST 白名单校验 (`formula_dsl.py`) | ✅ 已实现 |
| 单因子全流程验证 + Alpha101 基准对比 (`validate_factor.py`) | ✅ 已实现 |
| 记分卡 md / 自包含 HTML / CSV / 台账 | ✅ 已实现 |
| 能力卡 `describe_vocabulary()` | ✅ 已实现 |
| LLM 翻译器 `nl_translator.py` + `validate-idea` 子命令 | ✅ 已实现 |

翻译层需要 `pip install -e '.[llm]'` 并配置 `ANTHROPIC_API_KEY`（模型 `claude-opus-5`）。
**没有配置时不会报错退出**：`validate-idea` 会打印能力卡和手工流程指引，你照样可以把卡片
连同想法交给任意 LLM，拿到公式后走 `validate-factor`。

## 可用的数据字段与算子

这是"已有数据字段"的完整边界——翻译出的公式只能由这些元素构成。

**8 个输入**（全部 OHLCV 派生）

`open` · `high` · `low` · `close` · `volume` · `returns` · `vwap` · `adv20`

其中 `volume` / `adv20` 按 Alpha101 口径为 OHLC4 **美元**成交量，`returns` 为日收益。

**17 个因果算子**

`rank` · `delay` · `delta` · `ts_sum` · `ts_min` · `ts_max` · `stddev` · `ts_rank` · `correlation` · `covariance` · `sign` · `signed_power` · `safe_divide` · `where` · `adv` · `log` · `abs`

全部算子只向后看，前视构造在语法层面就不可表达。DSL 走 AST 白名单（deny-by-default、空 `__builtins__`），不支持属性访问、下标、lambda、推导式与关键字参数。

**没有的数据**：分析师一致预期、期权隐含波动率、情绪分、新闻、库存、经营现金流、基本面科目。涉及这些字段的想法，系统应当拒绝而不是找替代品。

## 快速开始

```bash
python -m pip install -e '.[test]'
python -m pytest -q -k 'not RealDataIntegration and not IntegrationRealData'
```

从一句话开始（需要 `[llm]` 依赖与 API key）：

```bash
python -m research_platform.cli validate-idea --idea "5 日反转，按 20 日均量加权"
```

它会翻译成公式、展示公式与解释、等你确认，然后跑完整验证；如果这个想法需要本仓没有的数据，
它会列出缺什么并**停下不跑**，绝不用相近字段顶替。

想先看能用哪些字段和算子：

```bash
python -m research_platform.cli vocabulary
```

也可以直接给公式（以 `-` 开头的公式必须用 `--formula=` 等号形式，否则 argparse 会当成参数名）：

```bash
python -m research_platform.cli validate-factor --formula='-(close / delay(close,5) - 1)' --name rev5
```

输出写到 `outputs/factor_validation/`，其中 `<slug>` 是公式 sha256 的前 12 位——同一条公式永远落在同一目录：

```
outputs/factor_validation/
├── ledger.jsonl          # 实验台账，跨次运行累积
└── <slug>/
    ├── scorecard.md      # 胜率、IC/ICIR、分组回测单调性、gross/net Sharpe、成本拖累、容量、相关性矩阵
    ├── scorecard.html    # 自包含单页（内联 SVG，无 JS、无外链）：分组柱状图、相关性热力图、IC-by-horizon、调仓频率权衡
    ├── formula.txt       # 公式与溯源信息，保证跑分可复现
    └── *.csv             # 各表原始数据
```

记分卡**只给指标，不给判决**（no verdict）。

基准因子固定为 4 条，用于横向对照：

| 名称 | 公式 |
|---|---|
| `alpha101_012` | `sign(delta(volume,1)) * (-delta(close,1))` |
| `alpha101_101` | `(close - open) / ((high - low) + 0.001)` |
| `reversal_5d` | `-(close / delay(close,5) - 1)` |
| `momentum_126d` | `delay(close,5) / delay(close,126) - 1` |

## 代码结构

- `research_platform/`：数据契约、PIT 宇宙、预处理与中性化、IC/分层评估、块自举显著性、缓冲目标仓位与流动性成本/容量、regime 分段、多口径宇宙、实验台账、记分卡（md/HTML）与 DSL；
- `scripts/validate_factor.py`：单因子验证编排入口；
- `factor_section/`：Alpha101 因果算子与技术因子，`composite_alpha_latest.csv` 的生产者；
- `data_section/`：OHLCV 采集、清洗与质量监控（`data/raw` → `data/cleaned` 的数据生产链）；
- `tester/`：单元测试与显式标记的真实数据测试；
- `docs/superpowers/`：全部设计文档与实施计划。

## 数据与研究边界

只使用免费公开数据。历史标普成分股由当前成分与公开变更记录反向重建，行业分类优先使用公开 GICS 快照，缺失时显式降级为 SEC SIC。免费数据不等同于商业 point-in-time 数据库；所有来源、覆盖和降级必须写入质量报告。

信号在交易日 `t` 生成，组合从 `t+1` 开始持有。walk-forward 在训练与测试之间至少 purge 一个预测周期。IC 是排序预测能力，不能替代真实组合收益；组合报告会单独扣除换手交易成本。行业中性化保证暴露控制，不保证 alpha 一定提高。

研究门槛失败是有效结果，并不代表程序执行失败。失败后不允许根据 OOS 结果反调中性化强度、调仓频率、缓冲区或权重；应回到新的 IS 研究假设后再开独立实验。

## 组合研究实验（既有工作流）

在同一份本地 OHLCV、同一组 walk-forward 折叠和同一交易成本下跑多因子组合与消融：

```bash
python scripts/run_oos_alpha_validation.py        # Raw / Soft Neutral / Strict Neutral 三条路径
python scripts/run_alpha101_correlation_oos.py    # A/B/C 消融：稳定性选择器 vs 相关性选择器
```

A 为原 13 因子加稳定性选择器，B 为同样 13 因子加相关性选择器，C 为 13 因子加 12 个精选 Alpha101 因子和相关性选择器。相关性选择器先按因子值相关性做 0.75 阈值聚类去冗余，再对方向对齐的 IC 相关矩阵做 50% 对角收缩与软惩罚，并计入 5 日 rank 换手惩罚。

输出含逐折/逐年指标、相关性与聚类明细、候选覆盖率与淘汰原因、0/5/10/20 bps 成本压力、行业净暴露、质量门与数据指纹。

## 致谢与许可

Alpha101 候选因子来自 Zura Kakushadze 的 [101 Formulaic Alphas](https://arxiv.org/abs/1601.00991)。本实现仅用于个人研究；论文 Appendix A 的公式与代码权利仍归其权利人。
