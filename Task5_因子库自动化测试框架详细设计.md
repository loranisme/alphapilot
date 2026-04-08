# 任务五：因子库自动化测试框架详细设计文档

## 一、模块概述与业务定位

### 1.1 为什么需要因子库自动化测试

因子库是量化投资平台的核心资产，因子计算逻辑的正确性直接决定了策略的有效性。然而，因子计算涉及大量数学运算和边界情况，手工测试难以覆盖所有场景。

因子库自动化测试框架的核心价值在于：
1. **保障质量**：确保因子计算结果正确无误
2. **加速迭代**：支持持续集成，快速发现回归问题
3. **记录变更**：追踪因子计算逻辑的历史变更
4. **降低风险**：在上线前发现潜在问题

### 1.2 模块在整体架构中的位置

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          测试执行层（Test Runner）                           │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ Pytest执行器   │  │ 参数化测试     │  │ Mock数据生成   │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          业务逻辑层（Services）                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 单元测试服务   │  │ 回归测试服务   │  │ 边界测试服务   │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 性能测试服务   │  │ 对比测试服务   │  │ 报告生成服务   │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          数据访问层（Repositories）                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 测试用例管理   │  │ 测试结果存储   │  │ 测试覆盖率     │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
             ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
             │   MySQL      │  │   Redis      │  │   Allure     │
             │ (用例/结果)  │  │ (缓存)       │  │  (测试报告)  │
             └──────────────┘  └──────────────┘  └──────────────┘
```

**上游依赖模块**：
- **因子库模块**：被测试的因子计算代码
- **版本控制系统**：获取代码变更

**下游输出模块**：
- **代码质量门禁**：控制代码能否合并
- **测试报告系统**：生成测试报告
- **因子管理模块**：标记因子版本

### 1.3 核心业务目标

1. **单元测试**：验证单个因子计算逻辑正确性
2. **回归测试**：确保新代码不破坏已有功能
3. **边界测试**：处理空值、极端值、停牌日等边界情况
4. **性能测试**：监控因子计算耗时和内存占用
5. **版本对比**：对比不同版本的计算结果
6. **覆盖率分析**：统计测试覆盖范围

---

## 二、业务需求深度理解

### 2.1 什么是因子单元测试

因子单元测试是针对单个因子计算函数的测试，验证其在各种输入下的输出是否符合预期。

#### 2.1.1 测试场景

| 测试类型 | 输入 | 预期输出 |
|---------|------|---------|
| 正常输入 | 有效的OHLCV数据 | 正确的因子值 |
| 空值输入 | 包含NaN的数据 | 正确的空值处理 |
| 极端输入 | 极端价格数据 | 合理的极端值处理 |
| 边界输入 | 单日数据/空数据 | 正确的边界处理 |

#### 2.1.2 单元测试示例

```python
def test_ma5():
    """
    测试5日简单移动平均因子
    
    MA5 = (C1 + C2 + C3 + C4 + C5) / 5
    """
    # 准备测试数据
    close_prices = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    
    # 执行因子计算
    ma5 = calculate_ma5(close_prices)
    
    # 验证结果
    expected = pd.Series([np.nan, np.nan, np.nan, np.nan, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0])
    assert ma5.equals(expected), f"MA5计算错误: {ma5} != {expected}"
```

#### 2.1.3 参数化测试

对于同类因子的测试，使用参数化减少重复代码：

```python
@pytest.mark.parametrize("factor_name,close_prices,expected", [
    ("MA5", [10, 11, 12, 13, 14, 15], [np.nan, np.nan, np.nan, np.nan, 12.0, 13.0]),
    ("MA10", [10, 11, 12, 13, 14, 15], [np.nan]*5 + [11.5]),
    ("MA20", [10, 11, 12, 13, 14, 15], [np.nan]*10 + [11.0]),
])
def test_moving_average(factor_name, close_prices, expected):
    """测试移动平均因子系列"""
    ma = calculate_moving_average(close_prices, window=5 if factor_name == "MA5" else 10 if factor_name == "MA10" else 20)
    expected_series = pd.Series(expected)
    assert ma.equals(expected_series), f"{factor_name}计算错误"
```

### 2.2 什么是回归测试

回归测试是验证新代码修改不会破坏已有功能的测试。

#### 2.2.1 回归测试策略

| 策略 | 描述 | 适用场景 |
|-----|------|---------|
| 全量回归 | 运行所有测试用例 | 重大变更 |
| 选择回归 | 只运行受影响的用例 | 小范围修改 |
| 增量回归 | 只运行新增用例 | 新增因子 |
| 持续回归 | 每次提交都运行 | 自动化流程 |

#### 2.2.2 版本对比测试

```python
def test_factor_regression():
    """
    回归测试：确保新版本计算结果与旧版本一致
    """
    # 准备测试数据
    test_data = generate_test_data(seed=42, rows=1000)
    
    # 获取旧版本计算结果
    old_result = calculate_factor_with_version("v1.0", "MA5", test_data)
    
    # 获取新版本计算结果
    new_result = calculate_factor_with_version("v1.1", "MA5", test_data)
    
    # 对比结果
    diff = (old_result - new_result).abs()
    max_diff = diff.max()
    
    assert max_diff < 1e-10, f"回归测试失败: 最大差异 {max_diff}"
    
    # 对比统计信息
    old_stats = {'mean': old_result.mean(), 'std': old_result.std()}
    new_stats = {'mean': new_result.mean(), 'std': new_result.std()}
    
    assert abs(old_stats['mean'] - new_stats['mean']) < 1e-10
    assert abs(old_stats['std'] - new_stats['std']) < 1e-10
```

### 2.3 什么是边界测试

边界测试是测试因子在极端或异常输入下的行为。

#### 2.3.1 边界场景

| 边界类型 | 场景描述 | 预期行为 |
|---------|---------|---------|
| 空数据 | 无数据输入 | 返回空Series/抛出异常 |
| 单日数据 | 只有一天数据 | 正确处理或报错 |
| 全空值 | 所有值都是NaN | 返回全空Series |
| 极端值 | 价格远超正常范围 | 正确处理或截断 |
| 停牌日 | 连续停牌 | 正确跳过 |
| 涨跌停 | 价格涨停/跌停 | 正确处理 |

#### 2.3.2 边界测试示例

```python
def test_ma5_edge_cases():
    """测试MA5因子的边界情况"""
    
    # 空数据
    empty_data = pd.Series([], dtype=float)
    result = calculate_ma5(empty_data)
    assert len(result) == 0, "空数据应返回空结果"
    
    # 单日数据
    single_data = pd.Series([10.0])
    result = calculate_ma5(single_data)
    assert pd.isna(result.iloc[0]), "单日数据应返回NaN"
    
    # 全空值
    nan_data = pd.Series([np.nan, np.nan, np.nan, np.nan, np.nan])
    result = calculate_ma5(nan_data)
    assert result.isna().all(), "全空值应返回全空结果"
    
    # 极端值
    extreme_data = pd.Series([0.01, 0.01, 0.01, 0.01, 1000000.0])  # 最后一个值异常大
    result = calculate_ma5(extreme_data)
    # 应该能处理极端值，不会崩溃
    assert len(result) == len(extreme_data), "极端值不应导致结果长度变化"
```

### 2.4 什么是性能测试

性能测试是监控因子计算的耗时和资源占用。

#### 2.4.1 性能指标

| 指标 | 描述 | 阈值 |
|-----|------|------|
| 计算耗时 | 因子计算所需时间 | < 100ms |
| 内存占用 | 计算过程中的内存峰值 | < 100MB |
| 吞吐量 | 每秒能计算的因子数 | > 1000/秒 |

#### 2.4.2 性能测试示例

```python
import time
import memory_profiler

def test_ma5_performance():
    """测试MA5因子的性能"""
    
    # 准备大量测试数据
    large_data = generate_test_data(rows=100000, seed=42)
    
    # 计时
    start_time = time.time()
    result = calculate_ma5(large_data['close'])
    end_time = time.time()
    
    elapsed = end_time - start_time
    print(f"计算耗时: {elapsed:.4f}秒")
    
    # 检查是否满足性能要求
    assert elapsed < 1.0, f"计算耗时 {elapsed:.2f}秒 超过1秒限制"
    
    # 内存监控
    mem_before = memory_profiler.memory_usage()[0]
    result = calculate_ma5(large_data['close'])
    mem_after = memory_profiler.memory_usage()[0]
    
    mem_used = mem_after - mem_before
    print(f"内存占用: {mem_used:.2f}MB")
    
    assert mem_used < 100, f"内存占用 {mem_used:.2f}MB 超过100MB限制"


def test_throughput():
    """测试因子吞吐量"""
    test_cases = generate_test_cases(100)  # 100个测试用例
    
    start_time = time.time()
    for case in test_cases:
        calculate_factor(case)
    end_time = time.time()
    
    total = end_time - start_time
    throughput = len(test_cases) / total
    
    print(f"吞吐量: {throughput:.2f} 个因子/秒")
    assert throughput > 10, f"吞吐量 {throughput:.2f} 低于10个/秒"
```

### 2.5 因子回测验证框架

#### 2.5.1 回测正确性验证

```python
class BacktestValidationFramework:
    """回测验证框架"""
    
    def __init__(self):
        self.validation_results = []
    
    def validate_backtest_result(self, 
                                 strategy_returns: pd.Series,
                                 benchmark_returns: pd.Series,
                                 expected_metrics: Dict) -> Dict:
        """
        验证回测结果正确性
        
        检查项目：
        1. 收益率计算正确性
        2. 最大回撤计算正确性
        3. 夏普比率计算正确性
        4. 策略与基准对比一致性
        """
        # 计算实际指标
        actual_metrics = self._calculate_metrics(strategy_returns, benchmark_returns)
        
        # 对比预期指标
        result = {'passed': True, 'details': []}
        
        for metric, expected_value in expected_metrics.items():
            actual_value = actual_metrics.get(metric)
            
            if actual_value is None:
                result['passed'] = False
                result['details'].append({
                    'metric': metric,
                    'status': 'missing',
                    'message': f'指标{metric}缺失'
                })
            elif abs(actual_value - expected_value) > 1e-6:
                result['passed'] = False
                result['details'].append({
                    'metric': metric,
                    'expected': expected_value,
                    'actual': actual_value,
                    'diff': actual_value - expected_value,
                    'status': 'failed',
                    'message': f'{metric}不匹配'
                })
            else:
                result['details'].append({
                    'metric': metric,
                    'status': 'passed',
                    'message': f'{metric}正确'
                })
        
        self.validation_results.append(result)
        return result
    
    def _calculate_metrics(self, 
                           strategy_returns: pd.Series,
                           benchmark_returns: pd.Series) -> Dict:
        """计算回测指标"""
        # 年化收益率
        annual_return = strategy_returns.mean() * 252
        
        # 年化波动率
        annual_vol = strategy_returns.std() * np.sqrt(252)
        
        # 夏普比率
        sharpe_ratio = annual_return / annual_vol if annual_vol > 0 else 0
        
        # 最大回撤
        cumulative = (1 + strategy_returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()
        
        # 相对基准超额收益
        excess_return = strategy_returns.mean() - benchmark_returns.mean()
        
        return {
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'excess_return': excess_return,
            'total_return': strategy_returns.sum()
        }
```

#### 2.5.2 策略信号验证

```python
class SignalValidator:
    """交易信号验证器"""
    
    def validate_signal_generation(self,
                                   price_data: pd.DataFrame,
                                   signals: pd.Series,
                                   expected_signal_logic: str) -> Dict:
        """
        验证交易信号生成正确性
        
        检查项目：
        1. 信号不包含NaN
        2. 信号值在有效范围内
        3. 信号数量合理
        4. 信号符合预期逻辑
        """
        result = {'passed': True, 'issues': []}
        
        # 检查NaN
        if signals.isna().any():
            nan_count = signals.isna().sum()
            result['passed'] = False
            result['issues'].append(f"信号包含{nan_count}个NaN")
        
        # 检查信号值范围
        valid_values = {-1, 0, 1}  # 假设信号为-1, 0, 1
        invalid_signals = signals[~signals.isin(valid_values)]
        if len(invalid_signals) > 0:
            result['passed'] = False
            result['issues'].append(f"信号包含无效值: {invalid_signals.unique()}")
        
        # 检查信号数量
        total_signals = (signals != 0).sum()
        signal_ratio = total_signals / len(signals)
        
        if signal_ratio > 0.5:
            result['issues'].append(f"信号过于频繁，比例{signal_ratio:.1%}")
        elif signal_ratio < 0.01:
            result['issues'].append(f"信号过少，比例{signal_ratio:.1%}")
        
        # 模拟交易验证
        trades = self._generate_trades(signals)
        trade_validation = self._validate_trades(trades, price_data)
        result['trade_validation'] = trade_validation
        
        return result
    
    def _generate_trades(self, signals: pd.Series) -> pd.DataFrame:
        """生成交易记录"""
        trades = []
        current_position = 0
        
        for i, signal in enumerate(signals):
            if signal != current_position:
                trades.append({
                    'date': signals.index[i] if hasattr(signals, 'index') else i,
                    'action': 'BUY' if signal == 1 else 'SELL' if signal == -1 else 'CLOSE',
                    'position': signal
                })
                current_position = signal
        
        return pd.DataFrame(trades)
    
    def _validate_trades(self, trades: pd.DataFrame, 
                         price_data: pd.DataFrame) -> Dict:
        """验证交易记录"""
        if len(trades) == 0:
            return {'passed': True, 'message': '无交易'}
        
        # 检查交易顺序
        position_changes = trades['position'].diff().fillna(0)
        if not (position_changes.abs() <= 1).all():
            return {'passed': False, 'message': '仓位变化超过1'}
        
        return {'passed': True, 'total_trades': len(trades)}
```

### 2.6 蒙特卡洛测试

#### 2.6.1 因子值分布检验

```python
class MonteCarloTester:
    """蒙特卡洛测试器"""
    
    def __init__(self, n_simulations: int = 1000):
        """
        参数：
        - n_simulations: 模拟次数
        """
        self.n_simulations = n_simulations
    
    def test_factor_distribution(self,
                                 factor_values: pd.Series,
                                 expected_distribution: str = 'normal') -> Dict:
        """
        使用蒙特卡洛方法检验因子值分布
        
        返回：
        - 分布检验结果
        - p值
        - 是否符合预期分布
        """
        from scipy import stats
        
        # 计算实际统计量
        actual_mean = factor_values.mean()
        actual_std = factor_values.std()
        actual_skewness = stats.skew(factor_values.dropna())
        actual_kurtosis = stats.kurtosis(factor_values.dropna())
        
        # 蒙特卡洛模拟预期分布
        simulated_stats = []
        for _ in range(self.n_simulations):
            if expected_distribution == 'normal':
                simulated = np.random.normal(actual_mean, actual_std, len(factor_values))
            else:
                simulated = np.random.uniform(factor_values.min(), 
                                              factor_values.max(), 
                                              len(factor_values))
            
            simulated_stats.append({
                'mean': np.mean(simulated),
                'std': np.std(simulated),
                'skewness': stats.skew(simulated),
                'kurtosis': stats.kurtosis(simulated)
            })
        
        # 计算p值（实际统计量在模拟分布中的位置）
        simulated_means = [s['mean'] for s in simulated_stats]
        simulated_stds = [s['std'] for s in simulated_stats]
        
        mean_percentile = (np.sum(np.array(simulated_means) <= actual_mean) / 
                          self.n_simulations)
        std_percentile = (np.sum(np.array(simulated_stds) <= actual_std) / 
                         self.n_simulations)
        
        # Kolmogorov-Smirnov检验
        if expected_distribution == 'normal':
            ks_stat, ks_pvalue = stats.kstest(
                factor_values.dropna(), 
                'norm',
                args=(actual_mean, actual_std)
            )
        else:
            ks_stat, ks_pvalue = stats.kstest(
                factor_values.dropna(),
                'uniform',
                args=(factor_values.min(), factor_values.max() - factor_values.min())
            )
        
        return {
            'actual_stats': {
                'mean': actual_mean,
                'std': actual_std,
                'skewness': actual_skewness,
                'kurtosis': actual_kurtosis
            },
            'ks_test': {
                'statistic': ks_stat,
                'p_value': ks_pvalue,
                'passed': ks_pvalue > 0.05
            },
            'monte_carlo_pvalues': {
                'mean': mean_percentile,
                'std': std_percentile
            }
        }
```

#### 2.6.2 因子IC稳定性蒙特卡洛检验

```python
class ICStabilityMonteCarloTester:
    """IC稳定性蒙特卡洛测试器"""
    
    def __init__(self, n_bootstrap: int = 1000, confidence_level: float = 0.95):
        """
        参数：
        - n_bootstrap: Bootstrap次数
        - confidence_level: 置信水平
        """
        self.n_bootstrap = n_bootstrap
        self.confidence_level = confidence_level
    
    def test_ic_stability(self,
                         factor_returns: pd.Series,
                         ic_values: pd.Series) -> Dict:
        """
        使用Bootstrap方法检验IC稳定性
        
        返回：
        - IC均值和标准差的置信区间
        - IC序列的自相关检验结果
        - 稳定性评估
        """
        # Bootstrap重采样计算IC统计量
        ic_means = []
        ic_stds = []
        
        for _ in range(self.n_bootstrap):
            # 有放回抽样
            bootstrap_sample = np.random.choice(ic_values, size=len(ic_values), replace=True)
            ic_means.append(np.mean(bootstrap_sample))
            ic_stds.append(np.std(bootstrap_sample))
        
        # 计算置信区间
        alpha = 1 - self.confidence_level
        ci_lower = np.percentile(ic_means, alpha/2 * 100)
        ci_upper = np.percentile(ic_means, (1 - alpha/2) * 100)
        
        # IC自相关检验
        from statsmodels.stats.stattools import durbin_watson
        dw_stat = durbin_watson(ic_values)
        
        # IC衰减分析
        ic_autocorr = [ic_values.autocorr(lag=i) for i in range(1, 11)]
        
        return {
            'ic_mean': float(ic_values.mean()),
            'ic_std': float(ic_values.std()),
            'ic_mean_ci': {
                'lower': ci_lower,
                'upper': ci_upper,
                'confidence_level': self.confidence_level
            },
            'durbin_watson': dw_stat,
            'autocorrelation': ic_autocorr,
            'stability_assessment': {
                'ic_stable': ci_upper - ci_lower < 0.05 and abs(ic_values.mean()) > 0.03,
                'no_serial_correlation': 1.5 < dw_stat < 2.5,
                'ic_ir decay': ic_autocorr[0] < 0.5 if len(ic_autocorr) > 0 else True
            }
        }
```

### 2.7 因子稳定性测试

#### 2.7.1 因子IC衰减测试

```python
class ICDecayTester:
    """IC衰减测试器"""
    
    def __init__(self, max_lag: int = 12):
        """
        参数：
        - max_lag: 最大滞后月数
        """
        self.max_lag = max_lag
    
    def test_ic_decay(self,
                      factor_values: pd.DataFrame,
                      forward_returns: pd.Series) -> Dict:
        """
        测试IC随持有期的衰减
        
        IC(τ) = Cor(F_t, R_{t+τ})
        
        返回：
        - 各持有期的IC值
        - IC衰减曲线
        - 半衰期估计
        """
        ic_by_lag = {}
        
        for lag in range(1, self.max_lag + 1):
            # 对齐数据（持有期收益向后移动lag期）
            shifted_returns = forward_returns.shift(-lag)
            
            # 计算IC
            valid_idx = factor_values.notna() & shifted_returns.notna()
            if valid_idx.sum() > 30:
                ic = factor_values[valid_idx].corr(shifted_returns[valid_idx])
                ic_by_lag[lag] = ic
        
        # 计算IC衰减半衰期
        ic_series = pd.Series(ic_by_lag)
        initial_ic = ic_series.iloc[0] if len(ic_series) > 0 else 0
        half_ic = initial_ic / 2
        
        # 找到IC衰减到一半的持有期
        half_lag = None
        for lag, ic in ic_by_lag.items():
            if abs(ic) <= abs(half_ic):
                half_lag = lag
                break
        
        return {
            'ic_by_lag': ic_by_lag,
            'decay_curve': ic_series.to_dict(),
            'half_lag': half_lag,
            'interpretation': self._interpret_decay(ic_by_lag, half_lag)
        }
    
    def _interpret_decay(self, ic_by_lag: Dict, half_lag: int) -> str:
        """解读IC衰减结果"""
        if half_lag is None:
            return "IC衰减缓慢，半衰期超过最大持有期"
        elif half_lag <= 2:
            return f"IC快速衰减，半衰期约{half_lag}个月，因子适合短期交易"
        elif half_lag <= 6:
            return f"IC衰减适中，半衰期约{half_lag}个月，因子适合中期交易"
        else:
            return f"IC衰减缓慢，半衰期超过{half_lag}个月，因子适合长期交易"
```

#### 2.7.2 因子分组单调性测试

```python
class FactorMonotonicityTester:
    """因子单调性测试器"""
    
    def __init__(self, n_groups: int = 5):
        """
        参数：
        - n_groups: 分组数量
        """
        self.n_groups = n_groups
    
    def test_monotonicity(self,
                          factor_values: pd.Series,
                          returns: pd.Series) -> Dict:
        """
        测试因子的单调性（分组回测）
        
        检验各组收益是否按预期单调排列
        
        返回：
        - 各组收益
        - 单调性得分
        - 是否通过测试
        """
        # 按因子值分组
        quantiles = pd.qcut(factor_values, q=self.n_groups, labels=False, duplicates='drop')
        
        # 计算各组收益
        group_returns = {}
        for i in range(self.n_groups):
            mask = quantiles == i
            if mask.sum() > 0:
                group_returns[i] = returns[mask].mean()
        
        # 检验单调性
        # 升序排列的因子，收益也应该升序（如果因子与收益正相关）
        returns_list = list(group_returns.values())
        
        # 计算单调性得分
        monotonicity_score = self._calculate_monotonicity_score(returns_list)
        
        # Spearman等级相关
        from scipy.stats import spearmanr
        factor_ranks = list(range(1, self.n_groups + 1))
        corr, p_value = spearmanr(factor_ranks, returns_list)
        
        return {
            'group_returns': group_returns,
            'monotonicity_score': monotonicity_score,
            'spearman_corr': corr,
            'spearman_p_value': p_value,
            'is_monotonic': monotonicity_score > 0.7 and p_value < 0.05,
            'interpretation': self._interpret_monotonicity(monotonicity_score, p_value)
        }
    
    def _calculate_monotonicity_score(self, returns_list: List[float]) -> float:
        """计算单调性得分"""
        if len(returns_list) < 2:
            return 1.0
        
        # 计算相邻组收益差异的方向
        direction_changes = 0
        for i in range(1, len(returns_list)):
            if (returns_list[i] - returns_list[i-1]) * (returns_list[0] - 0) < 0:
                direction_changes += 1
        
        max_changes = len(returns_list) - 1
        return 1 - (direction_changes / max_changes) if max_changes > 0 else 1.0
    
    def _interpret_monotonicity(self, score: float, p_value: float) -> str:
        """解读单调性结果"""
        if score > 0.8 and p_value < 0.01:
            return "因子单调性优秀，分组收益呈明显趋势"
        elif score > 0.6 and p_value < 0.05:
            return "因子单调性良好，分组收益有趋势"
        elif score > 0.4:
            return "因子单调性一般，分组收益趋势不明显"
        else:
            return "因子单调性差，分组收益无明显规律"
```

### 2.8 数据一致性检验

#### 2.8.1 因子值一致性检验

```python
class FactorConsistencyChecker:
    """因子一致性检验器"""
    
    def __init__(self, tolerance: float = 1e-6):
        """
        参数：
        - tolerance: 数值容差
        """
        self.tolerance = tolerance
    
    def check_cross_sectional_consistency(self,
                                           factor_matrix: pd.DataFrame) -> Dict:
        """
        检验因子在横截面上的合理性
        
        检查项目：
        1. 无穷值
        2. NaN比例
        3. 极端值
        4. 与历史分布的一致性
        """
        issues = []
        
        # 检查无穷值
        inf_count = np.isinf(factor_matrix).sum().sum()
        if inf_count > 0:
            issues.append({
                'type': 'inf_values',
                'count': int(inf_count),
                'severity': 'error'
            })
        
        # 检查NaN比例
        nan_ratio = factor_matrix.isna().sum().sum() / factor_matrix.size
        if nan_ratio > 0.5:
            issues.append({
                'type': 'high_nan_ratio',
                'ratio': nan_ratio,
                'severity': 'warning'
            })
        
        # 检查极端值
        z_scores = (factor_matrix - factor_matrix.mean()) / factor_matrix.std()
        extreme_count = (np.abs(z_scores) > 10).sum().sum()
        if extreme_count > 0:
            issues.append({
                'type': 'extreme_values',
                'count': int(extreme_count),
                'severity': 'info'
            })
        
        # 计算描述统计
        stats = {
            'mean': float(factor_matrix.mean().mean()),
            'std': float(factor_matrix.std().mean()),
            'min': float(factor_matrix.min().min()),
            'max': float(factor_matrix.max().max()),
            'nan_ratio': float(nan_ratio),
            'inf_count': int(inf_count),
            'extreme_count': int(extreme_count)
        }
        
        return {
            'passed': len([i for i in issues if i['severity'] == 'error']) == 0,
            'issues': issues,
            'statistics': stats
        }
```

#### 2.8.2 时间序列一致性检验

```python
class TimeSeriesConsistencyChecker:
    """时间序列一致性检验器"""
    
    def __init__(self):
        pass
    
    def check_temporal_consistency(self,
                                    factor_series: pd.Series,
                                    expected_properties: Dict) -> Dict:
        """
        检验因子时间序列的一致性
        
        检查项目：
        1. 序列连续性
        2. 异常跳变
        3. 趋势合理性
        4. 周期性
        """
        issues = []
        
        # 计算日度变化
        daily_changes = factor_series.diff()
        
        # 检测异常跳变
        change_std = daily_changes.std()
        large_jumps = (np.abs(daily_changes) > 5 * change_std).sum()
        
        if large_jumps > 0:
            issues.append({
                'type': 'large_jumps',
                'count': int(large_jumps),
                'severity': 'warning',
                'message': f'发现{large_jumps}个异常跳变'
            })
        
        # 检验自相关
        from statsmodels.stats.stattools import durbin_watson
        dw = durbin_watson(factor_series.dropna())
        
        # 序列相关性检验
        if dw < 1.5:
            issues.append({
                'type': 'high_autocorrelation',
                'durbin_watson': dw,
                'severity': 'info',
                'message': f'序列自相关较强(DW={dw:.2f})'
            })
        
        # 检查缺失日期
        date_diff = factor_series.index.to_series().diff()
        expected_diff = pd.Timedelta(days=1)
        missing_dates = (date_diff != expected_diff).sum()
        
        if missing_dates > 0:
            issues.append({
                'type': 'missing_dates',
                'count': int(missing_dates),
                'severity': 'info'
            })
        
        return {
            'passed': len([i for i in issues if i['severity'] == 'error']) == 0,
            'issues': issues,
            'statistics': {
                'durbin_watson': dw,
                'mean': float(factor_series.mean()),
                'std': float(factor_series.std()),
                'large_jumps': int(large_jumps),
                'missing_dates': int(missing_dates)
            }
        }
```

---

## 三、设计思考过程

### 3.1 需求分析与拆解

#### 第一步：识别核心用户故事

作为因子开发人员，我希望：
1. 编写因子代码后能快速验证正确性
2. 修改因子逻辑后能自动检测回归问题
3. 能看到详细的测试报告
4. 能追踪因子的版本变更

作为质量保障人员，我希望：
1. 测试覆盖率可视化
2. 能追踪测试历史趋势
3. 能快速定位问题根源

#### 第二步：拆解功能模块

| 功能模块 | 优先级 | 核心价值 |
|---------|-------|---------|
| 测试用例框架 | P0 | 提供测试基础 |
| Mock数据生成 | P0 | 构造测试数据 |
| 断言器 | P0 | 验证计算结果 |
| 回归测试 | P1 | 检测版本差异 |
| 边界测试 | P1 | 覆盖边界场景 |
| 性能测试 | P2 | 监控性能指标 |
| 测试报告 | P1 | 可视化结果 |

#### 第三步：确定技术约束

1. **框架选型**：
   - 测试框架：Pytest（生态丰富、功能强大）
   - Mock数据：Pandas + NumPy
   - 报告生成：Allure（美观、支持多种输出）
   - 代码覆盖：Coverage.py

2. **性能要求**：
   - 单因子测试 < 1秒
   - 全量回归测试 < 5分钟
   - 支持并行执行

3. **可扩展性**：
   - 易于添加新因子测试
   - 支持自定义断言
   - 支持参数化配置

### 3.2 架构设计思考

#### 3.2.1 为什么选择Pytest

Pytest相比unittest的优势：

| 对比项 | unittest | pytest |
|-------|---------|--------|
| 语法 | 冗长 | 简洁 |
| 参数化 | 需要继承 | @pytest.mark.parametrize |
| Fixtures | 需继承 | @pytest.fixture |
| 插件生态 | 一般 | 丰富 |
| 报告 | 一般 | 优秀（Allure） |

#### 3.2.2 测试数据管理策略

```python
# 测试数据目录结构
tests/
├── data/
│   ├── unit/          # 单元测试数据
│   │   ├── ma5/
│   │   ├── rsi/
│   │   └── macd/
│   ├── regression/    # 回归测试数据
│   │   ├── v1.0/
│   │   └── v1.1/
│   ├── edge/          # 边界测试数据
│   │   ├── empty/
│   │   ├── extreme/
│   │   └── nan/
│   └── performance/   # 性能测试数据
│       └── large/
└── fixtures/          # 测试夹具
```

### 3.3 核心组件设计思考

#### 3.3.1 测试夹具设计

```python
# conftest.py
import pytest
import pandas as pd
import numpy as np
from app.factors.technical import TechnicalFactors

@pytest.fixture
def sample_price_data():
    """生成样本价格数据用于测试"""
    np.random.seed(42)
    dates = pd.date_range('2023-01-01', periods=252, freq='D')
    
    # 生成模拟价格数据
    returns = np.random.normal(0.0005, 0.02, 252)
    prices = 100 * np.exp(np.cumsum(returns))
    
    data = pd.DataFrame({
        'date': dates,
        'open': prices * (1 + np.random.uniform(-0.01, 0.01, 252)),
        'high': prices * (1 + np.random.uniform(0, 0.02, 252)),
        'low': prices * (1 + np.random.uniform(-0.02, 0, 252)),
        'close': prices,
        'volume': np.random.lognormal(15, 0.5, 252)
    })
    
    return data


@pytest.fixture
def technical_factors():
    """创建技术因子计算器实例"""
    return TechnicalFactors()


@pytest.fixture
def expected_ma5_values():
    """MA5因子的预期计算结果（手工计算或从可靠来源获取）"""
    return pd.Series([
        np.nan, np.nan, np.nan, np.nan,
        100.5, 101.2, 102.0, 102.8, 103.5, 104.2
    ])
```

#### 3.3.2 自定义断言器

```python
# tests/assertions.py
import pandas as pd
import numpy as np
from typing import Union, Optional

class FactorAssertionError(AssertionError):
    """因子断言错误"""
    pass


def assert_factor_equal(
    actual: pd.Series,
    expected: pd.Series,
    tolerance: float = 1e-10,
    check_nan: bool = True
):
    """
    断言两个因子序列相等
    
    Args:
        actual: 实际计算结果
        expected: 预期结果
        tolerance: 数值容忍度
        check_nan: 是否检查NaN位置
    """
    if len(actual) != len(expected):
        raise FactorAssertionError(
            f"长度不一致: 实际 {len(actual)}, 预期 {len(expected)}"
        )
    
    # 检查NaN位置
    if check_nan:
        actual_nan = actual.isna()
        expected_nan = expected.isna()
        if not (actual_nan == expected_nan).all():
            raise FactorAssertionError(
                f"NaN位置不一致:\n实际NaN位置: {actual_nan}\n预期NaN位置: {expected_nan}"
            )
    
    # 比较非NaN值
    mask = ~actual.isna()
    if mask.any():
        diff = (actual[mask] - expected[mask]).abs()
        max_diff = diff.max()
        
        if max_diff > tolerance:
            raise FactorAssertionError(
                f"数值差异过大:\n最大差异: {max_diff}\n"
                f"差异位置: {diff.idxmax()}\n"
                f"实际值: {actual[diff.idxmax()]}\n"
                f"预期值: {expected[diff.idxmax()]}"
            )


def assert_factor_properties(
    actual: pd.Series,
    expected_mean: Optional[float] = None,
    expected_std: Optional[float] = None,
    expected_min: Optional[float] = None,
    expected_max: Optional[float] = None,
    tolerance: float = 0.01
):
    """断言因子序列的统计属性"""
    mask = ~actual.isna()
    
    if expected_mean is not None and mask.any():
        actual_mean = actual[mask].mean()
        if abs(actual_mean - expected_mean) > tolerance:
            raise FactorAssertionError(
                f"均值不匹配: 实际 {actual_mean:.6f}, 预期 {expected_mean:.6f}"
            )
    
    if expected_std is not None and mask.any():
        actual_std = actual[mask].std()
        if abs(actual_std - expected_std) > tolerance:
            raise FactorAssertionError(
                f"标准差不匹配: 实际 {actual_std:.6f}, 预期 {expected_std:.6f}"
            )
    
    if expected_min is not None and mask.any():
        actual_min = actual[mask].min()
        if abs(actual_min - expected_min) > tolerance:
            raise FactorAssertionError(
                f"最小值不匹配: 实际 {actual_min:.6f}, 预期 {expected_min:.6f}"
            )
    
    if expected_max is not None and mask.any():
        actual_max = actual[mask].max()
        if abs(actual_max - expected_max) > tolerance:
            raise FactorAssertionError(
                f"最大值不匹配: 实际 {actual_max:.6f}, 预期 {expected_max:.6f}"
            )
```

#### 3.3.3 测试数据生成器

```python
# tests/data_generator.py
import pandas as pd
import numpy as np
from typing import Dict, List, Optional


class TestDataGenerator:
    """测试数据生成器"""
    
    @staticmethod
    def generate_price_data(
        rows: int = 252,
        base_price: float = 100.0,
        volatility: float = 0.02,
        trend: float = 0.0005,
        seed: Optional[int] = None
    ) -> pd.DataFrame:
        """
        生成模拟价格数据
        
        Args:
            rows: 数据行数
            base_price: 初始价格
            volatility: 日波动率
            trend: 日均收益率
            seed: 随机种子
        """
        if seed is not None:
            np.random.seed(seed)
        
        dates = pd.date_range('2023-01-01', periods=rows, freq='D')
        
        # 生成价格序列（几何布朗运动）
        returns = np.random.normal(trend, volatility, rows)
        close_prices = base_price * np.exp(np.cumsum(returns))
        
        data = pd.DataFrame({
            'date': dates,
            'open': close_prices * (1 + np.random.uniform(-0.005, 0.005, rows)),
            'high': close_prices * (1 + np.random.uniform(0, 0.015, rows)),
            'low': close_prices * (1 + np.random.uniform(-0.015, 0, rows)),
            'close': close_prices,
            'volume': np.random.lognormal(15, 0.5, rows)
        })
        
        return data
    
    @staticmethod
    def generate_extreme_data(
        normal_ratio: float = 0.95,
        extreme_ratio: float = 0.05,
        base_price: float = 100.0
    ) -> pd.Series:
        """
        生成包含极端值的价格数据
        
        Args:
            normal_ratio: 正常值比例
            extreme_ratio: 极端值比例
            base_price: 基础价格
        """
        normal_count = int(100 * normal_ratio)
        extreme_count = int(100 * extreme_ratio)
        
        normal_prices = np.random.normal(base_price, 1, normal_count)
        extreme_prices = np.concatenate([
            np.random.uniform(0.01, 0.1, extreme_count // 2),  # 极低值
            np.random.uniform(10000, 20000, extreme_count // 2)  # 极高值
        ])
        
        return pd.Series(np.concatenate([normal_prices, extreme_prices]))
    
    @staticmethod
    def generate_nan_data(
        nan_ratio: float = 0.3,
        base_price: float = 100.0
    ) -> pd.Series:
        """
        生成包含NaN的数据
        
        Args:
            nan_ratio: NaN比例
            base_price: 基础价格
        """
        data = np.random.normal(base_price, 1, 100)
        nan_mask = np.random.random(100) < nan_ratio
        data[nan_mask] = np.nan
        return pd.Series(data)
    
    @staticmethod
    def generate_suspension_data() -> pd.DataFrame:
        """生成包含停牌的交易数据"""
        data = TestDataGenerator.generate_price_data(100)
        
        # 模拟停牌：某几天没有数据
        data = data.drop([20, 21, 22, 50, 51, 52])
        
        # 重新生成停牌后的数据
        for idx in [20, 21, 22, 50, 51, 52]:
            if idx < len(data):
                # 停牌期间价格不变
                if idx > 0:
                    data.loc[idx, 'close'] = data.loc[idx-1, 'close']
        
        return data.reset_index(drop=True)
```

---

## 四、详细技术方案

### 4.1 测试用例结构设计

#### 4.1.1 测试目录结构

```
tests/
├── conftest.py                 # 全局fixtures和配置
├── assertions.py              # 自定义断言器
├── data_generator.py          # 测试数据生成器
│
├── unit/                      # 单元测试
│   ├── __init__.py
│   ├── test_technical_factors.py
│   ├── test_fundamental_factors.py
│   ├── test_macro_factors.py
│   ├── test_style_factors.py
│   ├── test_ml_factors.py
│   └── test_alternative_factors.py
│
├── regression/                # 回归测试
│   ├── __init__.py
│   ├── test_version_comparison.py
│   └── test_cross_version.py
│
├── edge/                      # 边界测试
│   ├── __init__.py
│   ├── test_empty_data.py
│   ├── test_extreme_values.py
│   ├── test_nan_handling.py
│   ├── test_suspension.py
│   └── test_limit_up_down.py
│
├── performance/               # 性能测试
│   ├── __init__.py
│   ├── test_calculation_speed.py
│   ├── test_memory_usage.py
│   └── test_throughput.py
│
├── integration/               # 集成测试
│   ├── __init__.py
│   ├── test_factor_pipeline.py
│   └── test_data_flow.py
│
├── reports/                   # 测试报告
│   └── .gitkeep
│
└── pytest.ini                 # pytest配置
```

#### 4.1.2 pytest配置

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = 
    -v
    --tb=short
    --strict-markers
    --disable-warnings
    --cov=app.fcov-report=term-missing
    --cov-report=html:tests/reports/htmlcov
    --allureactors
    ---dir=tests/reports/allure
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    smoke: quick sanity checks
    regression: tests for regression checking
    performance: tests for performance metrics

[tool:pytest]
asyncio_mode = auto
```

### 4.2 测试用例示例

#### 4.2.1 技术因子测试

```python
# tests/unit/test_technical_factors.py
import pytest
import pandas as pd
import numpy as np
from app.factors.technical import TechnicalFactors


class TestMovingAverageFactors:
    """移动平均因子测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    @pytest.fixture
    def price_data(self, generate_price_data):
        return generate_price_data(rows=100, seed=42)
    
    def test_ma5_output_type(self, factors, price_data):
        """测试MA5输出类型"""
        result = factors.calculate_ma5(price_data)
        
        assert isinstance(result, pd.Series), "MA5应返回Series类型"
        assert len(result) == len(price_data), "输出长度应与输入一致"
    
    def test_ma5_nan_handling(self, factors, price_data):
        """测试MA5的NaN处理"""
        result = factors.calculate_ma5(price_data)
        
        # 前4个值应该是NaN（window=5）
        assert pd.isna(result.iloc[0]), "第1个值应为NaN"
        assert pd.isna(result.iloc[3]), "第4个值应为NaN"
        assert not pd.isna(result.iloc[4]), "第5个值不应为NaN"
    
    def test_ma5_value(self, factors, price_data):
        """测试MA5计算值"""
        result = factors.calculate_ma5(price_data)
        
        # 验证第5个值
        expected_ma5 = price_data['close'].iloc[0:5].mean()
        assert abs(result.iloc[4] - expected_ma5) < 1e-10, "MA5计算值错误"
    
    def test_ma5_monotonicity(self, factors, price_data):
        """测试MA5的单调性（当价格单调递增时）"""
        # 生成单调递增的价格数据
        increasing_data = price_data.copy()
        increasing_data['close'] = np.linspace(100, 200, len(increasing_data))
        
        result = factors.calculate_ma5(increasing_data)
        
        # MA5应该也是递增的
        ma5_valid = result.dropna()
        assert (ma5_valid.diff().dropna() >= 0).all(), "单调递增价格下MA5应该递增"
    
    def test_ma10_output(self, factors, price_data):
        """测试MA10输出"""
        result = factors.calculate_ma10(price_data)
        
        assert len(result) == len(price_data)
        assert pd.isna(result.iloc[0:9]).all(), "前9个MA10值应为NaN"
        assert not pd.isna(result.iloc[9]), "第10个MA10值不应为NaN"
    
    @pytest.mark.parametrize("window,expected_first_valid", [
        (5, 4), (10, 9), (20, 19), (60, 59)
    ])
    def test_ma_windows(self, factors, price_data, window, expected_first_valid):
        """参数化测试不同窗口大小的MA"""
        result = factors.calculate_ma(price_data['close'], window=window)
        
        assert pd.isna(result.iloc[expected_first_valid - 1]) == (expected_first_valid > 0)
        assert not pd.isna(result.iloc[expected_first_valid]) if expected_first_valid < len(result) else True


class TestRSIFactor:
    """RSI因子测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    @pytest.fixture
    def price_data(self, generate_price_data):
        return generate_price_data(rows=100, seed=42)
    
    def test_rsi_range(self, factors, price_data):
        """测试RSI值范围（0-100）"""
        result = factors.calculate_rsi(price_data, period=14)
        
        valid_result = result.dropna()
        assert (valid_result >= 0).all() and (valid_result <= 100).all(), \
            "RSI值应该在0-100范围内"
    
    def test_rsi_extreme_values(self, factors, price_data):
        """测试RSI极端值"""
        # 连续上涨 → RSI接近100
        rising_data = price_data.copy()
        rising_data['close'] = np.linspace(100, 150, len(rising_data))
        rsi_rising = factors.calculate_rsi(rising_data, period=14)
        
        # 连续下跌 → RSI接近0
        falling_data = price_data.copy()
        falling_data['close'] = np.linspace(150, 100, len(falling_data))
        rsi_falling = factors.calculate_rsi(falling_data, period=14)
        
        # 验证趋势
        valid_rising = rsi_rising.dropna()
        valid_falling = rsi_falling.dropna()
        
        if len(valid_rising) > 0 and len(valid_falling) > 0:
            assert valid_rising.iloc[-1] > 50, "连续上涨后RSI应高于50"
            assert valid_falling.iloc[-1] < 50, "连续下跌后RSI应低于50"
```

#### 4.2.2 边界测试

```python
# tests/edge/test_empty_data.py
import pytest
import pandas as pd
import numpy as np
from app.factors.technical import TechnicalFactors


class TestEmptyDataHandling:
    """空数据处理测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    def test_empty_series(self, factors):
        """测试空序列"""
        empty = pd.Series([], dtype=float)
        
        result = factors.calculate_ma5(empty)
        
        assert len(result) == 0, "空输入应返回空结果"
        assert result.dtype == float, "结果应为float类型"
    
    def test_single_value(self, factors):
        """测试单值数据"""
        single = pd.Series([100.0])
        
        result = factors.calculate_ma5(single)
        
        assert pd.isna(result.iloc[0]), "单值数据的MA5应为NaN"
    
    def test_two_values(self, factors):
        """测试两个值"""
        two_values = pd.Series([100.0, 105.0])
        
        result = factors.calculate_ma5(two_values)
        
        assert result.isna().all(), "少于5个值时MA5应为NaN"


class TestNaNHandling:
    """NaN处理测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    def test_all_nan(self, factors):
        """测试全NaN数据"""
        all_nan = pd.Series([np.nan, np.nan, np.nan, np.nan, np.nan])
        
        result = factors.calculate_ma5(all_nan)
        
        assert result.isna().all(), "全NaN输入应返回全NaN结果"
    
    def test_partial_nan(self, factors):
        """测试部分NaN数据"""
        data = pd.Series([100.0, np.nan, 102.0, np.nan, 104.0, 106.0, 108.0])
        
        result = factors.calculate_ma5(data)
        
        # MA5应该能正确处理部分NaN
        assert len(result) == len(data), "输出长度应与输入一致"
    
    def test_nan_at_beginning(self, factors):
        """测试数据开头有NaN"""
        data = pd.Series([np.nan, np.nan, 100.0, 101.0, 102.0, 103.0, 104.0])
        
        result = factors.calculate_ma5(data)
        
        # 前几个值应该是NaN
        assert pd.isna(result.iloc[0]), "第1个值应为NaN"
        assert pd.isna(result.iloc[1]), "第2个值应为NaN"


class TestExtremeValueHandling:
    """极端值处理测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    def test_zero_price(self, factors):
        """测试价格为0"""
        data = pd.Series([100.0, 100.0, 0.0, 100.0, 100.0, 100.0, 100.0])
        
        result = factors.calculate_ma5(data)
        
        # 不应崩溃
        assert len(result) == len(data)
    
    def test_negative_price(self, factors):
        """测试负价格"""
        data = pd.Series([100.0, 100.0, -50.0, 100.0, 100.0, 100.0, 100.0])
        
        result = factors.calculate_ma5(data)
        
        # 不应崩溃，但结果可能异常
        assert len(result) == len(data)
    
    def test_extreme_high_value(self, factors):
        """测试极端高值"""
        data = pd.Series([100.0, 100.0, 1000000.0, 100.0, 100.0, 100.0, 100.0])
        
        result = factors.calculate_ma5(data)
        
        # 不应崩溃
        assert len(result) == len(data)
    
    def test_infinite_value(self, factors):
        """测试无穷大"""
        data = pd.Series([100.0, 100.0, np.inf, 100.0, 100.0, 100.0, 100.0])
        
        result = factors.calculate_ma5(data)
        
        # 应该能处理inf
        assert not result.isna().all() or result.isna().all()
```

#### 4.2.3 回归测试

```python
# tests/regression/test_version_comparison.py
import pytest
import pandas as pd
import numpy as np


class TestVersionComparison:
    """版本对比测试类"""
    
    @pytest.fixture
    def test_data(self):
        """固定的测试数据"""
        np.random.seed(42)
        dates = pd.date_range('2023-01-01', periods=252, freq='D')
        
        returns = np.random.normal(0.0005, 0.02, 252)
        prices = 100 * np.exp(np.cumsum(returns))
        
        return pd.DataFrame({
            'date': dates,
            'close': prices,
            'open': prices * (1 + np.random.uniform(-0.01, 0.01, 252)),
            'high': prices * (1 + np.random.uniform(0, 0.02, 252)),
            'low': prices * (1 + np.random.uniform(-0.02, 0, 252)),
            'volume': np.random.lognormal(15, 0.5, 252)
        })
    
    def test_ma5_version_consistency(self, test_data):
        """测试MA5在不同版本间的一致性"""
        # 模拟v1.0的计算结果（已存储）
        v1_0_result = self._get_version_result("v1.0", "MA5", test_data)
        
        # 当前版本的计算结果
        from app.factors.technical import TechnicalFactors
        factors = TechnicalFactors()
        current_result = factors.calculate_ma5(test_data)
        
        # 对比
        self._assert_results_close(v1_0_result, current_result)
    
    def test_rsi_version_consistency(self, test_data):
        """测试RSI在不同版本间的一致性"""
        v1_0_result = self._get_version_result("v1.0", "RSI", test_data)
        
        from app.factors.technical import TechnicalFactors
        factors = TechnicalFactors()
        current_result = factors.calculate_rsi(test_data, period=14)
        
        self._assert_results_close(v1_0_result, current_result)
    
    def _assert_results_close(self, expected, actual, tolerance=1e-10):
        """断言两个结果足够接近"""
        mask = ~actual.isna()
        if mask.any():
            diff = (actual[mask] - expected[mask]).abs()
            max_diff = diff.max()
            assert max_diff < tolerance, \
                f"结果差异过大，最大差异: {max_diff}"
    
    def _get_version_result(self, version, factor_name, data):
        """获取指定版本的因子计算结果"""
        # 这里应该从数据库或文件加载历史结果
        # 模拟从v1.0获取结果
        if version == "v1.0":
            # 返回一个固定的预期结果（用于测试）
            return self._calculate_v1_0_result(factor_name, data)
        raise ValueError(f"未知版本: {version}")
    
    def _calculate_v1_0_result(self, factor_name, data):
        """计算v1.0版本的结果（用于测试）"""
        # 这里实现v1.0版本的计算逻辑
        # 测试时使用一个简化的实现
        close = data['close']
        if factor_name == "MA5":
            return close.rolling(5).mean()
        elif factor_name == "RSI":
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            return 100 - (100 / (1 + rs))
        raise ValueError(f"未知因子: {factor_name}")
```

### 4.3 性能测试用例

```python
# tests/performance/test_calculation_speed.py
import pytest
import time
import pandas as pd
import numpy as np
from app.factors.technical import TechnicalFactors


class TestCalculationSpeed:
    """计算速度测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    @pytest.fixture
    def small_data(self):
        """小数据量"""
        np.random.seed(42)
        return pd.DataFrame({
            'close': np.random.uniform(10, 100, 100)
        })
    
    @pytest.fixture
    def medium_data(self):
        """中等数据量"""
        np.random.seed(42)
        return pd.DataFrame({
            'close': np.random.uniform(10, 100, 1000)
        })
    
    @pytest.fixture
    def large_data(self):
        """大数据量"""
        np.random.seed(42)
        return pd.DataFrame({
            'close': np.random.uniform(10, 100, 10000)
        })
    
    @pytest.mark.performance
    def test_ma5_small_data(self, factors, small_data):
        """测试小数据量下MA5的计算速度"""
        start = time.time()
        result = factors.calculate_ma5(small_data)
        elapsed = time.time() - start
        
        print(f"MA5计算耗时（小数据）: {elapsed*1000:.2f}ms")
        assert elapsed < 0.1, f"计算耗时 {elapsed:.3f}秒 超过100ms"
    
    @pytest.mark.performance
    def test_ma5_medium_data(self, factors, medium_data):
        """测试中等数据量下MA5的计算速度"""
        start = time.time()
        result = factors.calculate_ma5(medium_data)
        elapsed = time.time() - start
        
        print(f"MA5计算耗时（中等数据）: {elapsed*1000:.2f}ms")
        assert elapsed < 0.5, f"计算耗时 {elapsed:.3f}秒 超过500ms"
    
    @pytest.mark.performance
    def test_ma5_large_data(self, factors, large_data):
        """测试大数据量下MA5的计算速度"""
        start = time.time()
        result = factors.calculate_ma5(large_data)
        elapsed = time.time() - start
        
        print(f"MA5计算耗时（大数据）: {elapsed*1000:.2f}ms")
        assert elapsed < 2.0, f"计算耗时 {elapsed:.3f}秒 超过2秒"
    
    @pytest.mark.performance
    @pytest.mark.parametrize("factor_name,window", [
        ("MA5", 5), ("MA10", 10), ("MA20", 20), ("MA60", 60)
    ])
    def test_multiple_factors(self, factors, large_data, factor_name, window):
        """测试多个因子的计算速度"""
        start = time.time()
        
        if factor_name.startswith("MA"):
            result = factors.calculate_ma(large_data['close'], window=window)
        
        elapsed = time.time() - start
        
        print(f"{factor_name}计算耗时: {elapsed*1000:.2f}ms")
        assert elapsed < 1.0, f"{factor_name}计算耗时 {elapsed:.3f}秒 超过1秒"


class TestThroughput:
    """吞吐量测试类"""
    
    @pytest.fixture
    def factors(self):
        return TechnicalFactors()
    
    @pytest.fixture
    def test_cases(self, generate_price_data):
        """生成测试用例"""
        return [generate_price_data(rows=100, seed=i) for i in range(100)]
    
    @pytest.mark.performance
    def test_throughput(self, factors, test_cases):
        """测试因子吞吐量"""
        start = time.time()
        
        for data in test_cases:
            factors.calculate_ma5(data)
            factors.calculate_ma10(data)
            factors.calculate_rsi(data, period=14)
        
        elapsed = time.time() - start
        total_factors = len(test_cases) * 3  # 3个因子
        
        throughput = total_factors / elapsed
        print(f"吞吐量: {throughput:.2f} 个因子/秒")
        
        assert throughput > 10, f"吞吐量 {throughput:.2f} 低于10个/秒"
```

### 4.4 测试报告配置

#### 4.4.1 Allure报告配置

```python
# tests/conftest.py
import pytest
import allure


@pytest.fixture
def attach_screenshot():
    """截图附件"""
    pass  # 在测试中动态添加


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """生成测试报告时添加额外信息"""
    outcome = yield
    report = outcome.get_result()
    
    if report.when == 'call' and report.failed():
        # 添加失败截图
        allure.attach(
            item.funcargs.get('screenshot', b''),
            name='failure_screenshot',
            attachment_type=allure.attachment_type.PNG
        )
```

#### 4.4.2 HTML测试报告

```python
# tests/report_generator.py
import pytest
import pandas as pd


class TestReportGenerator:
    """测试报告生成器"""
    
    def __init__(self, test_results):
        self.results = test_results
    
    def generate_summary(self):
        """生成测试摘要"""
        total = len(self.results)
        passed = len([r for r in self.results if r['status'] == 'passed'])
        failed = total - passed
        skipped = len([r for r in self.results if r['status'] == 'skipped'])
        
        return {
            'total': total,
            'passed': passed,
            'failed': failed,
            'skipped': skipped,
            'pass_rate': f"{passed/total*100:.1f}%",
            'duration': sum(r['duration'] for r in self.results)
        }
    
    def generate_detail_report(self):
        """生成详细报告"""
        report = []
        
        for result in self.results:
            if result['status'] == 'failed':
                report.append({
                    'name': result['name'],
                    'message': result.get('message', ''),
                    'traceback': result.get('traceback', ''),
                    'duration': result['duration']
                })
        
        return sorted(report, key=lambda x: x['duration'], reverse=True)
    
    def export_to_html(self, template_path, output_path):
        """导出HTML报告"""
        summary = self.generate_summary()
        details = self.generate_detail_report()
        
        html = f"""
        <html>
        <head>
            <title>因子库测试报告</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .summary {{ background: #f0f0f0; padding: 20px; border-radius: 5px; }}
                .passed {{ color: green; }}
                .failed {{ color: red; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
            </style>
        </head>
        <body>
            <h1>因子库测试报告</h1>
            <div class="summary">
                <h2>测试摘要</h2>
                <p>总测试数: {summary['total']}</p>
                <p class="passed">通过: {summary['passed']}</p>
                <p class="failed">失败: {summary['failed']}</p>
                <p>跳过: {summary['skipped']}</p>
                <p>通过率: {summary['pass_rate']}</p>
            </div>
            
            <h2>失败详情</h2>
            <table>
                <tr><th>测试名称</th><th>错误信息</th><th>耗时</th></tr>
                {"".join(f"<tr><td>{d['name']}</td><td>{d['message']}</td><td>{d['duration']:.2f}s</td></tr>" for d in details)}
            </table>
        </body>
        </html>
        """
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
```

---

## 五、验证方法与测试方案

### 5.1 测试用例设计原则

#### 5.1.1 测试覆盖原则

| 覆盖类型 | 目标 | 方法 |
|---------|------|------|
| 代码覆盖 | > 80% | Coverage.py |
| 因子覆盖 | 100% | 所有因子都有测试 |
| 场景覆盖 | > 90% | 正常/边界/异常场景 |
| 版本覆盖 | 关键版本 | 回归测试 |

#### 5.1.2 测试数据管理

```python
# tests/data/test_data_management.py
"""
测试数据管理最佳实践：

1. 使用固定的随机种子确保可重复性
2. 将预期结果与测试数据一起存储
3. 定期更新测试数据以覆盖新场景
4. 对大测试数据使用采样
"""


def test_with_fixed_seed():
    """使用固定随机种子的测试"""
    np.random.seed(42)
    data = generate_test_data()
    
    # 预期结果应该与随机种子42时一致
    expected = load_expected_result("test_with_fixed_seed")
    
    result = calculate_factor(data)
    assert result.equals(expected)


def test_with_sampled_data():
    """使用采样数据的性能测试"""
    # 对于大数据量测试，使用采样数据
    full_data = generate_test_data(rows=1000000)
    sampled_data = full_data.sample(n=1000, random_state=42)
    
    result = calculate_factor(sampled_data)
    assert len(result) == 1000
```

### 5.2 测试质量门禁

```python
# tests/quality_gates.py
"""
测试质量门禁：

在测试流程中集成以下检查：

1. 测试覆盖率 >= 80%
2. 所有单元测试通过
3. 回归测试通过
4. 性能测试在阈值内
"""


class QualityGateChecker:
    """质量门禁检查器"""
    
    def __init__(self, coverage_threshold=80):
        self.coverage_threshold = coverage_threshold
    
    def check_coverage(self, coverage_report):
        """检查测试覆盖率"""
        if coverage_report['line_coverage'] < self.coverage_threshold:
            raise QualityGateError(
                f"测试覆盖率 {coverage_report['line_coverage']}% "
                f"低于阈值 {self.coverage_threshold}%"
            )
    
    def check_test_results(self, test_results):
        """检查测试结果"""
        failed = [r for r in test_results if r['status'] == 'failed']
        if failed:
            raise QualityGateError(
                f"存在 {len(failed)} 个失败的测试用例"
            )
    
    def check_performance(self, performance_results):
        """检查性能结果"""
        slow_tests = [r for r in performance_results 
                     if r['duration'] > r['threshold']]
        if slow_tests:
            raise QualityGateError(
                f"存在 {len(slow_tests)} 个性能不达标的测试"
            )


class QualityGateError(Exception):
    """质量门禁错误"""
    pass
```

---

## 六、部署与运维

### 6.1 本地运行测试

```bash
# 运行所有测试
pytest

# 运行单元测试
pytest tests/unit/

# 运行回归测试
pytest tests/regression/

# 运行边界测试
pytest tests/edge/

# 运行性能测试
pytest tests/performance/ -m performance

# 生成测试报告
pytest --allure-dir=tests/reports/allure
allure serve tests/reports/allure
```

### 6.2 监控指标

| 指标 | 阈值 | 告警方式 |
|-----|------|---------|
| 测试通过率 | < 95% | 钉钉通知 |
| 测试覆盖率 | < 80% | 阻断合并 |
| 回归失败 | > 0 | 阻断合并 |
| 性能退化 | > 10% | 邮件通知 |

---

## 附录：相关三方库和API

### 一、测试框架

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| pytest | >=7.0 | 测试框架 | `pip install pytest` |
| pytest-asyncio | >=0.21 | 异步测试 | `pip install pytest-asyncio` |
| pytest-cov | >=4.0 | 覆盖率 | `pip install pytest-cov` |
| pytest-xdist | >=3.0 | 并行执行 | `pip install pytest-xdist` |
| pytest-mock | >=3.10 | Mock支持 | `pip install pytest-mock` |
| pytest-randomly | >=3.0 | 随机顺序 | `pip install pytest-randomly` |

### 二、测试报告库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| allure-pytest | >=2.0 | Allure报告 | `pip install allure-pytest` |
| pytest-html | >=4.0 | HTML报告 | `pip install pytest-html` |
| pytest-json-report | >=1.0 | JSON报告 | `pip install pytest-json-report` |

### 三、Mock与数据生成

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| Faker | >=19.0 | 假数据生成 | `pip install faker` |
| factory-boy | >=3.0 | 测试工厂 | `pip install factory-boy` |
| hypothesis | >=6.0 | 属性测试 | `pip install hypothesis` |
| mimesis | >=7.0 | 数据生成 | `pip install mimesis` |

### 四、性能测试库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| memory-profiler | >=0.60 | 内存监控 | `pip install memory-profiler` |
| line-profiler | >=4.0 | 行级分析 | `pip install line-profiler` |
| pytest-benchmark | >=4.0 | 基准测试 | `pip install pytest-benchmark` |
| locust | >=2.0 | 负载测试 | `pip install locust` |

### 五、代码覆盖率

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| coverage | >=7.0 | 代码覆盖 | `pip install coverage` |
| pytest-cov | >=4.0 | pytest集成 | `pip install pytest-cov` |

### 六、代码示例

**Pytest基本用法**

```python
# tests/test_example.py
import pytest
import pandas as pd
import numpy as np

def test_ma5_calculation():
    """测试MA5计算"""
    # 准备数据
    close_prices = pd.Series([10, 11, 12, 13, 14, 15])
    
    # 执行计算
    from app.factors.technical import calculate_ma5
    result = calculate_ma5(close_prices)
    
    # 验证结果
    expected = pd.Series([np.nan, np.nan, np.nan, np.nan, 12.0, 13.0])
    assert result.equals(expected)


@pytest.fixture
def sample_price_data():
    """测试夹具：生成样本数据"""
    np.random.seed(42)
    return pd.Series(np.random.uniform(10, 100, 100))


def test_with_fixture(sample_price_data):
    """使用夹具的测试"""
    from app.factors.technical import calculate_ma5
    result = calculate_ma5(sample_price_data)
    assert len(result) == len(sample_price_data)


@pytest.mark.parametrize("window,expected_first_valid", [
    (5, 4), (10, 9), (20, 19), (60, 59)
])
def test_ma_windows(window, expected_first_valid):
    """参数化测试"""
    from app.factors.technical import calculate_ma
    data = pd.Series(range(100))
    result = calculate_ma(data, window=window)
    assert pd.isna(result.iloc[expected_first_valid - 1]) == (expected_first_valid > 0)
```

**异步测试**

```python
# tests/test_async.py
import pytest
import asyncio

@pytest.mark.asyncio
async def test_async_factor_calculation():
    """异步因子计算测试"""
    from app.factors.async_calculator import async_calculate_factor
    
    data = [1, 2, 3, 4, 5]
    result = await async_calculate_factor(data)
    
    assert result is not None
    assert len(result) == len(data)


@pytest.mark.asyncio
async def test_concurrent_factors():
    """并发因子计算测试"""
    from app.factors.async_calculator import async_calculate_factor
    
    tasks = [
        async_calculate_factor([1, 2, 3, 4, 5]),
        async_calculate_factor([5, 4, 3, 2, 1]),
        async_calculate_factor([10, 20, 30, 40, 50])
    ]
    
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 3
```

**Mock测试**

```python
# tests/test_mock.py
from unittest.mock import Mock, patch
import pytest

def test_with_mock():
    """使用Mock的测试"""
    # 创建Mock对象
    mock_calculator = Mock()
    mock_calculator.calculate.return_value = 100
    
    # 使用Mock
    result = mock_calculator.calculate(50, 50)
    
    assert result == 100
    mock_calculator.calculate.assert_called_once_with(50, 50)


def test_with_patch():
    """使用patch的测试"""
    from app.services.calculator import CalculatorService
    
    with patch('app.services.calculator.ExternalAPI') as mock_api:
        mock_api.get_data.return_value = {'price': 100}
        
        service = CalculatorService()
        result = service.calculate_with_external()
        
        assert result == 100
        mock_api.get_data.assert_called_once()
```

**属性测试**

```python
# tests/test_property.py
from hypothesis import given, settings
from hypothesis import strategies as st
import pandas as pd

@given(st.lists(st.floats(min_value=-1000, max_value=1000), min_size=5))
@settings(max_examples=100)
def test_ma5_properties(close_prices):
    """属性测试：MA5的基本性质"""
    from app.factors.technical import calculate_ma5
    
    series = pd.Series(close_prices)
    result = calculate_ma5(series)
    
    # 结果长度应与输入一致
    assert len(result) == len(series)
    
    # 前4个值应该是NaN
    assert result.iloc[0:4].isna().all()
    
    # 有效值数量 = 总数 - window + 1
    valid_count = result.dropna().shape[0]
    assert valid_count == max(0, len(series) - 5 + 1)
```

**基准测试**

```python
# tests/test_benchmark.py
import pytest

@pytest.fixture
def large_dataset():
    """大数据集"""
    import pandas as pd
    import numpy as np
    np.random.seed(42)
    return pd.DataFrame({
        'close': np.random.uniform(10, 100, 10000)
    })


def test_ma5_performance(benchmark, large_dataset):
    """MA5性能基准测试"""
    from app.factors.technical import calculate_ma5
    
    result = benchmark(calculate_ma5, large_dataset['close'])
    
    assert result is not None


def test_rsi_performance(benchmark, large_dataset):
    """RSI性能基准测试"""
    from app.factors.technical import calculate_rsi
    
    result = benchmark(calculate_rsi, large_dataset, period=14)
    
    assert result is not None
```

### 七、资源链接

| 资源类型 | 链接 | 说明 |
|---------|------|------|
| Pytest文档 | https://docs.pytest.org/ | 官方文档 |
| Allure报告 | https://allurereport.org/ | 测试报告 |
| Coverage.py | https://coverage.readthedocs.io/ | 代码覆盖 |
| Hypothesis | https://hypothesis.works/ | 属性测试 |
| pytest-asyncio | https://pytest-asyncio.readthedocs.io/ | 异步测试 |
| Faker | https://faker.readthedocs.io/ | 假数据生成 |
