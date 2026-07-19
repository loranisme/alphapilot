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
