# Factor Research Platform（still on construction）

这是一个面向美股截面因子研究的单机平台，覆盖数据清洗、技术/基本面因子、IC 与分层评估、walk-forward、行业/规模中性化、可交易多空组合和结构化实验输出。

## 当前能力

- `data_section/`：OHLCV 采集、清洗和质量监控；
- `factor_section/`：技术因子、基本面因子和合成信号；
- `research_platform/`：稳定数据契约、PIT 元数据、预处理、中性化、评估、组合与报告；
- `analysis_section/backtester.py`：现有研究入口及兼容接口；
- `tester/`：轻量单元测试和显式标记的真实数据测试。

## 安装与验证

```bash
python -m pip install -e '.[test]'
python -m pytest -q -k 'not RealDataIntegration and not IntegrationRealData'
python -m research_platform.cli validate-data --config configs/research_platform_example.yaml
```

## 运行实验

```bash
python -m research_platform.cli run-experiment \
  --config configs/research_platform_example.yaml \
  --output-dir outputs/research_platform_validation
```

输出目录包含：

- `experiment_summary.json`：配置、数据指纹和核心指标；
- `factor_diagnostics.csv`：raw 与 neutralized 的 IC/分层对比；
- `portfolio_metrics.csv`：成本前后组合指标、换手和回撤；
- `quality_report.json`：分类覆盖、泄漏检查和质量门；
- `report.md`：便于人工审阅的汇总。

## 数据与研究边界

第一版只使用免费公开数据。历史标普成分股由当前成分与公开变更记录反向重建，行业分类优先使用公开 GICS 快照，缺失时显式降级为 SEC SIC。免费数据不等同于商业 point-in-time 数据库；所有来源、覆盖和降级必须写入质量报告。

信号在交易日 `t` 生成，组合从 `t+1` 开始持有。walk-forward 在训练与测试之间至少 purge 一个预测周期。IC 是排序预测能力，不能替代真实组合收益；组合报告会单独扣除换手交易成本。行业中性化保证暴露控制，不保证 alpha 一定提高。

## 真 OOS Alpha 改进实验

下面的命令使用本地免费 OHLCV 和缓存的公开 GICS 快照，按固定参数运行
Raw、Soft Neutral（主路径）和 Strict Neutral（诊断路径）：

```bash
python scripts/run_oos_alpha_validation.py
```

固定研究约束包括：5 日预测与调仓、至少 5 日 purge、4 个 IS 时间块中至少
3 个方向一致、近期半段同向、因子权重非负且单因子不超过 20%、Soft Neutral
强度 0.5、20% 入场/30% 退出缓冲、单名 2% 上限和单边 10 bps 成本。

输出位于 `outputs/oos_alpha_improvement/`：

- `fold_metrics.csv`：逐 fold 的 Raw/Soft/Strict IC、收益、波动和 Sharpe；
- `year_metrics.csv`：逐年稳定性；
- `cost_stress.csv`：0/5/10/20 bps 成本压力；
- `industry_exposure.csv`：每日行业净暴露；
- `quality_report.json`：固定验收门槛的实际 PASS/FAIL；
- `metadata.json`：数据指纹、固定配置和非 PIT 分类声明；
- `report.md`：便于人工审阅的汇总。

研究门槛失败是有效结果，并不代表程序执行失败。失败后不允许根据 OOS 结果
调整中性化强度、调仓频率、缓冲区或权重；应回到新的 IS 研究假设后再开独立实验。

## Alpha101 扩充与相关性筛选实验

下面的命令在同一份本地免费 OHLCV、同一组 walk-forward 折叠和同一交易成本下
比较三条研究臂：A 为现有 13 因子和原稳定性选择器，B 为现有 13 因子和相关性
选择器，C 为现有 13 因子加 12 个精选 Alpha101 因子和相关性选择器。

```bash
python scripts/run_alpha101_correlation_oos.py
```

相关性选择器先在每个 IS 折叠内按因子值相关性做 0.75 阈值的连通分量聚类，
每簇只保留一个代表；随后对方向对齐的 IC 相关矩阵做 50% 对角收缩和软惩罚，
再加入固定的 5 日 rank 换手惩罚。有效折必须保留 5–6 个因子，单因子权重不超过
20%；若不足 5 个，组合保持旧仓，不放宽门槛。

输出位于 `outputs/alpha101_correlation_oos/`：

- `factor_value_correlation.csv`、`ic_correlation.csv`：逐折相关性与有效样本数；
- `correlation_clusters.csv`、`factor_selection_by_fold.csv`：冗余簇、代表与最终选择；
- `candidate_coverage.csv`：全部候选的覆盖率、稳定性和淘汰原因；
- `ablation_metrics.csv`、`fold_metrics.csv`、`year_metrics.csv`：A/B/C 总体、逐折和逐年结果；
- `cost_stress.csv`、`industry_exposure.csv`：0/5/10/20 bps 成本与行业暴露；
- `quality_report.json`、`metadata.json`、`report.md`：工程门、研究门、数据指纹和人工汇总。

12 个候选来自 Zura Kakushadze 的
[101 Formulaic Alphas](https://arxiv.org/abs/1601.00991)：#2、#7、#12、#17、
#21、#22、#30、#34、#35、#40、#46、#101。本实现仅用于个人研究；论文
Appendix A 的公式与代码权利仍归其权利人。免费数据没有历史 PIT 成分股/行业数据库，
行业分类仍是当前 GICS 快照；真实 VWAP、历史市值和 PIT 行业依赖公式未纳入本次实验。
