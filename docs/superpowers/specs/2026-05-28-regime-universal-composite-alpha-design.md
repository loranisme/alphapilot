# Regime-Universal Composite Alpha — Design

## 目标
把 composite alpha 从「在 2022-2025 AI 牛市里调优」改造成「在任意市场周期都能用」的系统。

## 已确认的约束（来自brainstorming对话）
- 股票池：维持 S&P 500（501只），不扩展
- 数据：从当前 4 年（2022-2026）延伸到 8 年（2018-2026），覆盖 2018-19 震荡市、2020 COVID崩溃+反弹、2021 牛市、2022 熊市、2023-25 AI牛市
- 因子来源：技术因子为主，基本面因子为辅（约 13:6 的比例）
- 架构：三因子族设计，确保任意 regime 下至少有一个因子族在工作

## 诊断结论（来自旧4年数据的stratified backtest）
23个OHLCV因子中：
- **9个因子有效**（正向显著）：reversal_5d, short_reversal_1d, min_ret_reversal, momentum_126d, momentum_63d, weekly_range_reversal, ts_rank_close, vwap_reversion, intraday_position
- **7个因子方向完全反转**（负向高度显著，t < -3.4）：ivol_penalty, upside_volatility_penalty, hl_dispersion_penalty, max_ret_penalty, vol_of_vol_penalty, mad_ratio_penalty, price_volume_corr_penalty — 根因是2022-2025被高波动科技股主导，经典 low-vol anomaly 在5日 horizon 上完全失效
- **4个因子无信号**：median_reversion, stoch_reversal, amihud_illiq（边际，保留）；rsi_reversal, volume_surge_reversal, trend_slope_penalty, skewness_penalty（接近零或边际负，删除）

## 三因子族设计

| 族 | 适用 Regime | 因子（13个技术 + 6个基本面）|
|---|---|---|
| **A. 反转/微观结构** | 震荡市、高波动、熊市 | reversal_5d, short_reversal_1d, min_ret_reversal, vwap_reversion, median_reversion, ts_rank_close, weekly_range_reversal, intraday_position, stoch_reversal, amihud_illiq |
| **B. 动量/趋势** | 牛市、趋势市 | momentum_126d, momentum_63d, price_volume_corr（翻转符号后保留）|
| **C. 价值/质量基本面** | 熊市、价值回归、跨周期稳定 | earnings_yield, book_to_market, roe_ttm, gross_profitability, asset_growth_penalty, eps_momentum |

## 改动清单

1. **数据扩展**：`data_collector.py` 的 `years=4` → `years=8`，重新下载501只股票，重新跑 `data_cleaner.py`
2. **删除10个失效因子**：ivol_penalty, upside_volatility_penalty, hl_dispersion_penalty, max_ret_penalty, vol_of_vol_penalty, mad_ratio_penalty, skewness_penalty, trend_slope_penalty, rsi_reversal, volume_surge_reversal
3. **翻转1个因子符号**：price_volume_corr_penalty → price_volume_corr（去掉负号，因为该因子在IS数据里持续以负权重被使用，说明设计符号是反的）
4. **接入基本面因子**：把 `FundamentalFactorDesigner` 的6个因子接入 `backtester.py` 的 walk-forward 和 stratified 流程，与OHLCV技术因子合并成统一的 multi-factor table
5. **因子族最低保留约束**：修改 `_select_factors`，确保每个因子族（A/B/C）在每个 fold 至少保留1个因子，即使该族整体 IS ICIR 较弱
6. **放宽 IS 门槛**：`min_is_ic_ir` 从 0.15 降到 0.08（数据量从3年扩到8年后，IS估计更稳定，可以放宽门槛让更多因子通过，同时靠因子族约束防止过度集中）

## 验证
- 跑 `tester/test_factor_design.py`、`tester/test_backtester.py` 全部通过
- 跑 `analysis_section/backtester.py`，输出每个因子的 ICIR + t-test 结果
- 对比新旧 OOS walk-forward ICIR、stratified summary

## 范围之外
- 不做 regime 自适应/动态切换（model risk 叠加，5日信号意义不大）
- 不扩展股票池
- 不引入 sector/beta 中性化（后续可选迭代）
