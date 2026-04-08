# 任务四：市场异动智能识别与通知详细设计文档

## 一、模块概述与业务定位

### 1.1 为什么需要市场异动监控

在金融市场中，异动往往意味着机会或风险：

- **机会**：某只股票突然涨停，可能有重大利好
- **风险**：某行业集体暴跌，可能有系统性风险
- **联动**：期货暴跌，可能导致相关股票跟跌

传统的人工盯盘方式效率低下，而且容易遗漏重要信息。市场异动智能识别与通知系统的核心价值在于：**替代人工盯盘，实时监测市场数据流，在检测到异常模式时主动推送通知**。

### 1.2 模块在整体架构中的位置

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户层（前端界面）                               │
│      实时行情看板 │ 异动监控配置 │ 告警历史 │ 规则管理                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API服务层（FastAPI）                               │
│   /api/v1/market/realtime     │  /api/v1/market/anomalies                   │
│   /api/v1/market/rules        │  /api/v1/market/notifications               │
│   /api/v1/market/patterns     │  /api/v1/market/summary                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
             ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
             │   WebSocket  │  │   REST API   │  │  告警服务    │
             │  (实时推送)  │  │  (查询)      │  │  (通知)      │
             └──────────────┘  └──────────────┘  └──────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          业务逻辑层（Services）                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 异动检测引擎   │  │ 技术形态识别   │  │ 市场情绪监测   │                 │
│  │ (Anomaly)      │  │ (Patterns)     │  │ (Sentiment)    │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 关联分析引擎   │  │ 告警聚合服务   │  │ 通知推送服务   │                 │
│  │ (Correlation)  │  │ (Aggregation)  │  │ (Notification) │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          数据接入层（Data Ingestion）                        │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ AkShare实时    │  │ Tushare实时   │  │ 期货/ETF数据   │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                         ┌─────────────────────────┐
                         │    外部数据源           │
                         │  (行情/财务/新闻/社交)  │
                         └─────────────────────────┘
```

**上游数据源**：
- **AkShare**：提供A股实时行情数据
- **Tushare**：提供行情和财务数据
- **东方财富**：提供新闻和研报数据
- **Twitter/雪球**：提供社交媒体情绪

**下游消费方**：
- **风控模块**：根据异动调整风控参数
- **交易模块**：根据异动触发交易信号
- **前端展示**：实时展示市场状态

### 1.3 核心业务目标

1. **价格异动监测**：发现涨跌幅突变、成交量异常
2. **技术形态识别**：识别突破、金叉、死叉等形态
3. **市场情绪监测**：监测涨跌停家数、ETF折溢价
4. **关联品种联动**：发现期货与现货的异常联动
5. **智能告警**：根据规则和机器学习触发告警

---

## 二、业务需求深度理解

### 2.1 什么是价格异动

价格异动是指价格或成交量的**异常变化**，可能预示着重大事件。

#### 2.1.1 涨跌幅异动

| 异动类型 | 定义 | 阈值 | 可能原因 |
|---------|------|------|---------|
| 快速涨停 | 短时间内涨幅接近涨停 | 5分钟内涨>9% | 利好消息 |
| 炸板 | 涨停板被打开 | 从涨停回落>3% | 抛压过大 |
| 闪崩 | 短时间内大幅下跌 | 5分钟内跌>-5% | 流动性危机 |
| 放量滞涨 | 放量但不涨 | 成交量>5日均量2倍，涨幅<1% | 主力出货 |

#### 2.1.2 成交量异动

| 异动类型 | 定义 | 阈值 | 可能原因 |
|---------|------|------|---------|
| 巨量成交 | 成交量异常放大 | >5日均量5倍 | 主力建仓/出货 |
| 地量成交 | 成交量极度萎缩 | <5日均量20% | 流动性枯竭 |
| 脉冲放量 | 成交量瞬间放大 | 1分钟量>日量10% | 大单成交 |

#### 2.1.3 涨跌幅异动计算

```python
def detect_price_anomaly(current_price, reference_price, time_window_minutes=5):
    """
    检测价格异动
    
    Args:
        current_price: 当前价格
        reference_price: 参考价格（time_window分钟前）
        time_window_minutes: 时间窗口
    
    Returns:
        异动类型和详情
    """
    change_pct = (current_price - reference_price) / reference_price * 100
    
    if change_pct >= 9.0:
        return {
            'type': 'RAPID_LIMIT_UP',
            'change_pct': change_pct,
            'severity': 'critical',
            'description': '5分钟内涨幅接近涨停'
        }
    elif change_pct >= 5.0:
        return {
            'type': 'SIGNIFICANT_RISE',
            'change_pct': change_pct,
            'severity': 'warning',
            'description': '5分钟内涨幅超过5%'
        }
    elif change_pct <= -5.0:
        return {
            'type': 'FLASH_CRASH',
            'change_pct': change_pct,
            'severity': 'critical',
            'description': '5分钟内跌幅超过5%'
        }
    elif change_pct <= -9.0:
        return {
            'type': 'RAPID_LIMIT_DOWN',
            'change_pct': change_pct,
            'severity': 'critical',
            'description': '5分钟内跌幅接近跌停'
        }
    
    return {'type': 'NORMAL', 'change_pct': change_pct}
```

### 2.2 什么是技术形态识别

技术形态是价格走势的特定模式，通常被视为买入或卖出信号。

#### 2.2.1 常见技术形态

| 形态名称 | 图形特征 | 交易含义 |
|---------|---------|---------|
| 突破 | 价格突破压力位/支撑位 | 趋势延续或反转 |
| 金叉 | 短期均线突破长期均线 | 买入信号 |
| 死叉 | 短期均线跌破长期均线 | 卖出信号 |
| 头肩顶 | 三个峰值，中间最高 | 顶部反转信号 |
| 头肩底 | 三个谷底，中间最低 | 底部反转信号 |
| 三角形整理 | 价格收敛于三角形 | 等待突破方向 |

#### 2.2.2 突破检测算法

```python
def detect_breakout(prices, window=20, threshold=0.02):
    """
    检测价格突破
    
    Args:
        prices: 价格序列
        window: 计算窗口
        threshold: 突破阈值（百分比）
    
    Returns:
        是否突破及方向
    """
    if len(prices) < window:
        return {'is_breakout': False, 'reason': '数据不足'}
    
    # 计算支撑/压力位
    high = prices[-window:].max()
    low = prices[-window:].min()
    current = prices[-1]
    
    # 计算波动率
    volatility = (high - low) / prices[-window]
    
    # 检测向上突破
    if current > high * (1 - threshold * volatility):
        return {
            'is_breakout': True,
            'direction': 'UP',
            'level': high,
            'breakout_strength': (current - high) / high
        }
    
    # 检测向下突破
    if current < low * (1 + threshold * volatility):
        return {
            'is_breakout': True,
            'direction': 'DOWN',
            'level': low,
            'breakout_strength': (low - current) / low
        }
    
    return {'is_breakout': False, 'direction': None}
```

#### 2.2.3 金叉死叉检测算法

```python
def detect_crossover(prices, short_window=5, long_window=20):
    """
    检测均线金叉/死叉
    
    Args:
        prices: 价格序列
        short_window: 短期均线窗口
        long_window: 长期均线窗口
    
    Returns:
        交叉类型和方向
    """
    if len(prices) < long_window:
        return {'is_crossover': False, 'reason': '数据不足'}
    
    # 计算均线
    short_ma = prices[-short_window:].mean()
    long_ma = prices[-long_window:].mean()
    
    # 前一天的均线
    short_ma_prev = prices[-short_window-1:-1].mean()
    long_ma_prev = prices[-long_window-1:-1].mean()
    
    # 检测金叉（短均线从下方突破长均线）
    if short_ma_prev <= long_ma_prev and short_ma > long_ma:
        return {
            'is_crossover': True,
            'type': 'GOLDEN_CROSS',
            'description': '金叉出现，买入信号',
            'short_ma': short_ma,
            'long_ma': long_ma
        }
    
    # 检测死叉（短均线从上方跌破长均线）
    if short_ma_prev >= long_ma_prev and short_ma < long_ma:
        return {
            'is_crossover': True,
            'type': 'DEATH_CROSS',
            'description': '死叉出现，卖出信号',
            'short_ma': short_ma,
            'long_ma': long_ma
        }
    
    return {'is_crossover': False, 'type': None}
```

### 2.3 什么是市场情绪监测

市场情绪反映整体市场的乐观或悲观程度。

#### 2.3.1 情绪指标

| 指标 | 计算方法 | 解读 |
|-----|---------|------|
| 涨跌停比 | 涨停家数/跌停家数 | >3市场乐观，<1市场悲观 |
| 炸板率 | 炸板数/涨停数 | 高炸板率表示分歧大 |
| 成交量变化 | 今日量/昨日量 | 放量表示活跃，缩量表示冷淡 |
| ETF折溢价 | (ETF价格-净值)/净值 | 折价表示悲观，溢价表示乐观 |

#### 2.3.2 情绪指标计算

```python
def calculate_market_sentiment(market_data):
    """
    计算市场情绪指标
    
    Args:
        market_data: 市场数据（包含涨跌停、成交量等）
    
    Returns:
        情绪指标和分数
    """
    up_limit = len(market_data[market_data['pct_change'] >= 9.9])
    down_limit = len(market_data[market_data['pct_change'] <= -9.9])
    
    # 涨跌停比
    limit_ratio = up_limit / max(down_limit, 1)
    
    # 炸板率
    total_up_attempts = len(market_data[market_data['pct_change'] >= 7])
    if total_up_attempts > 0:
        break_rate = (total_up_attempts - up_limit) / total_up_attempts
    else:
        break_rate = 0
    
    # 成交量变化
    volume_ratio = market_data['volume'].sum() / market_data['volume_yesterday'].sum()
    
    # 情绪分数（0-100）
    sentiment_score = 50  # 基准分
    
    # 涨跌停比贡献
    if limit_ratio > 3:
        sentiment_score += 10
    elif limit_ratio < 1:
        sentiment_score -= 10
    
    # 炸板率贡献
    if break_rate > 0.5:
        sentiment_score -= 5
    
    # 成交量贡献
    if volume_ratio > 1.5:
        sentiment_score += 5
    elif volume_ratio < 0.7:
        sentiment_score -= 5
    
    sentiment_score = max(0, min(100, sentiment_score))
    
    return {
        'limit_ratio': limit_ratio,
        'break_rate': break_rate,
        'volume_ratio': volume_ratio,
        'sentiment_score': sentiment_score,
        'sentiment_label': 'bullish' if sentiment_score > 60 else 'bearish' if sentiment_score < 40 else 'neutral'
    }
```

### 2.4 什么是关联品种联动

关联品种之间存在价格联动，异常联动可能预示事件。

#### 2.4.1 常见关联

| 关联类型 | 品种A | 品种B | 联动原因 |
|---------|------|------|---------|
| 现货-期货 | 沪深300 | IF期货 | 套利机制 |
| 跨市场 | A股 | 港股 | 资金流动 |
| 行业-ETF | 银行股 | 银行ETF | 复制关系 |
| 商品-股票 | 黄金 | 山东黄金 | 同一资产 |

#### 2.4.2 联动检测算法

```python
def detect_correlation_anomaly(prices_a, prices_b, window=60, threshold=0.8):
    """
    检测关联品种的异常联动
    
    Args:
        prices_a: 品种A价格序列
        prices_b: 品种B价格序列
        window: 计算窗口
        threshold: 异常联动阈值
    
    Returns:
        是否异常联动
    """
    if len(prices_a) < window or len(prices_b) < window:
        return {'is_anomaly': False, 'reason': '数据不足'}
    
    # 计算收益率
    returns_a = prices_a.pct_change().dropna()
    returns_b = prices_b.pct_change().dropna()
    
    # 计算滚动相关系数
    correlation = returns_a[-window:].corr(returns_b[-window:])
    
    # 计算相关系数变化
    correlation_prev = returns_a[-2*window:-window].corr(returns_b[-2*window:-window])
    correlation_change = abs(correlation - correlation_prev)
    
    # 检测异常联动
    if abs(correlation) > threshold and correlation_change > 0.3:
        return {
            'is_anomaly': True,
            'correlation': correlation,
            'correlation_change': correlation_change,
            'severity': 'critical' if abs(correlation_change) > 0.5 else 'warning',
            'description': f'相关系数从{correlation_prev:.2f}变为{correlation:.2f}'
        }
    
    return {'is_anomaly': False, 'correlation': correlation}
```

### 2.5 复杂技术形态识别

#### 2.5.1 双顶/双底形态识别

```python
class DoubleTopBottomDetector:
    """双顶/双底形态识别器"""
    
    def __init__(self, tolerance: float = 0.03, min_distance: int = 10):
        """
        参数：
        - tolerance: 双顶/双底高度容差（3%）
        - min_distance: 两个顶部/底部之间的最小距离
        """
        self.tolerance = tolerance
        self.min_distance = min_distance
    
    def detect(self, prices: pd.Series) -> Dict:
        """
        检测双顶/双底形态
        
        双顶特征：
        1. 两个高度相近的顶部
        2. 中间有一个明显的低谷
        3. 价格跌破颈线后确认
        """
        peaks = self._find_peaks(prices, window=5)
        
        if len(peaks) < 2:
            return {'is_pattern': False, 'reason': '峰值不足'}
        
        # 检查双顶
        for i in range(len(peaks) - 1):
            peak1 = peaks[i]
            peak2 = peaks[i + 1]
            
            # 检查距离
            if peak2['index'] - peak1['index'] < self.min_distance:
                continue
            
            # 检查高度相近
            price_diff = abs(peak1['value'] - peak2['value']) / peak1['value']
            if price_diff <= self.tolerance:
                # 找到两个峰之间的谷
                trough = self._find_trough(prices, peak1['index'], peak2['index'])
                
                # 计算颈线
                neckline = trough['value']
                
                # 检查是否跌破颈线
                if prices.iloc[-1] < neckline:
                    return {
                        'is_pattern': True,
                        'type': 'DOUBLE_TOP',
                        'severity': 'warning',
                        'signal': 'bearish',
                        'peak1': {'index': peak1['index'], 'price': peak1['value']},
                        'peak2': {'index': peak2['index'], 'price': peak2['value']},
                        'neckline': neckline,
                        'description': f'双顶形态，左峰{peak1["value"]:.2f}，右峰{peak2["value"]:.2f}'
                    }
        
        return {'is_pattern': False, 'reason': '不符合双顶特征'}
    
    def _find_peaks(self, prices: pd.Series, window: int = 5) -> List[Dict]:
        """找局部极大值"""
        peaks = []
        for i in range(window, len(prices) - window):
            if prices.iloc[i] == prices.iloc[i-window:i+window+1].max():
                peaks.append({'index': i, 'value': prices.iloc[i]})
        return peaks
    
    def _find_trough(self, prices: pd.Series, start: int, end: int) -> Dict:
        """找局部极小值"""
        trough_idx = prices.iloc[start:end+1].idxmin()
        return {'index': trough_idx, 'value': prices.loc[trough_idx]}
```

#### 2.5.2 三角形整理形态识别

```python
class TrianglePatternDetector:
    """三角形整理形态识别器"""
    
    def __init__(self, min_points: int = 10):
        """
        参数：
        - min_points: 最小点数
        """
        self.min_points = min_points
    
    def detect(self, prices: pd.Series) -> Dict:
        """
        检测三角形整理形态
        
        三角形特征：
        1. 价格高点逐渐降低（上升三角形）或低点逐渐抬高（下降三角形）
        2. 波动范围逐渐收窄
        """
        if len(prices) < self.min_points:
            return {'is_pattern': False, 'reason': '数据不足'}
        
        # 计算趋势线
        highs = prices.rolling(5).max()
        lows = prices.rolling(5).min()
        
        # 检查高点趋势
        recent_highs = highs.iloc[-self.min_points:]
        high_slope = self._calculate_slope(recent_highs)
        
        # 检查低点趋势
        recent_lows = lows.iloc[-self.min_points:]
        low_slope = self._calculate_slope(recent_lows)
        
        # 波动收窄
        volatility_now = (highs.iloc[-1] - lows.iloc[-1]) / prices.iloc[-1]
        volatility_early = (highs.iloc[-self.min_points] - lows.iloc[-self.min_points]) / prices.iloc[-self.min_points]
        volatility_narrowing = volatility_now < volatility_early * 0.7
        
        if volatility_narrowing:
            if high_slope < 0 and low_slope > 0:
                return {
                    'is_pattern': True,
                    'type': 'SYMMETRICAL_TRIANGLE',
                    'signal': 'neutral',
                    'description': '对称三角形整理，等待突破方向',
                    'volatility_contraction': (1 - volatility_now / volatility_early) * 100
                }
            elif high_slope < 0 and low_slope <= 0:
                return {
                    'is_pattern': True,
                    'type': 'DESCENDING_TRIANGLE',
                    'signal': 'bearish',
                    'description': '下降三角形，倾向于向下突破',
                    'volatility_contraction': (1 - volatility_now / volatility_early) * 100
                }
            elif high_slope >= 0 and low_slope > 0:
                return {
                    'is_pattern': True,
                    'type': 'ASCENDING_TRIANGLE',
                    'signal': 'bullish',
                    'description': '上升三角形，倾向于向上突破',
                    'volatility_contraction': (1 - volatility_now / volatility_early) * 100
                }
        
        return {'is_pattern': False, 'reason': '不符合三角形特征'}
    
    def _calculate_slope(self, series: pd.Series) -> float:
        """计算趋势线斜率"""
        x = np.arange(len(series))
        y = series.values
        slope, _ = np.polyfit(x, y, 1)
        return slope
```

#### 2.5.3 RSI超买超卖检测

```python
class RSIAnalyzer:
    """RSI超买超卖分析器"""
    
    def __init__(self, period: int = 14, overbought: float = 70, oversold: float = 30):
        """
        参数：
        - period: RSI计算周期
        - overbought: 超买阈值
        - oversold: 超卖阈值
        """
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
    
    def calculate(self, prices: pd.Series) -> pd.Series:
        """计算RSI"""
        delta = prices.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=self.period).mean()
        avg_loss = loss.rolling(window=self.period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def detect_extremes(self, prices: pd.Series) -> Dict:
        """检测超买超卖"""
        rsi = self.calculate(prices)
        current_rsi = rsi.iloc[-1]
        
        if current_rsi >= self.overbought:
            return {
                'is_extreme': True,
                'type': 'OVERBOUGHT',
                'signal': 'bearish',
                'rsi': current_rsi,
                'description': f'RSI={current_rsi:.1f}，进入超买区域，可能回调'
            }
        elif current_rsi <= self.oversold:
            return {
                'is_extreme': True,
                'type': 'OVERSOLD',
                'signal': 'bullish',
                'rsi': current_rsi,
                'description': f'RSI={current_rsi:.1f}，进入超卖区域，可能反弹'
            }
        
        return {
            'is_extreme': False,
            'rsi': current_rsi,
            'description': f'RSI={current_rsi:.1f}，处于中性区域'
        }
```

### 2.6 波动率异动检测

#### 2.6.1 GARCH波动率模型

```python
class GARCHVolatilityDetector:
    """GARCH波动率异动检测器"""
    
    def __init__(self, volatility_threshold: float = 2.0):
        """
        参数：
        - volatility_threshold: 波动率阈值（标准差倍数）
        """
        self.threshold = volatility_threshold
    
    def fit_garch(self, returns: pd.Series) -> Dict:
        """
        拟合GARCH模型并预测波动率
        
        GARCH(1,1)模型：
        σ²_t = ω + α × ε²_{t-1} + β × σ²_{t-1}
        """
        try:
            from arch import arch_model
            
            # 拟合GARCH(1,1)模型
            model = arch_model(returns * 100, vol='Garch', p=1, q=1, mean='Constant')
            result = model.fit(disp='off')
            
            # 预测未来波动率
            forecast = result.forecast(horizon=1)
            predicted_volatility = np.sqrt(forecast.variance.iloc[-1].values[0]) / 100
            
            return {
                'model_fitted': True,
                'omega': result.params['omega'],
                'alpha': result.params['alpha[1]'],
                'beta': result.params['beta[1]'],
                'predicted_volatility': predicted_volatility,
                'persistence': result.params['alpha[1]'] + result.params['beta[1]']
            }
        except Exception as e:
            # GARCH拟合失败，使用简单波动率
            historical_vol = returns.std() * np.sqrt(252)
            return {
                'model_fitted': False,
                'predicted_volatility': historical_vol,
                'method': 'historical'
            }
    
    def detect_volatility_anomaly(self, returns: pd.Series) -> Dict:
        """检测波动率异常"""
        garch_result = self.fit_garch(returns)
        
        # 历史波动率
        historical_vol = returns.std() * np.sqrt(252)
        
        # 预测波动率
        predicted_vol = garch_result['predicted_volatility']
        
        # 波动率变化
        vol_change = (predicted_vol - historical_vol) / historical_vol
        
        # 检测异常
        if vol_change > self.threshold:
            return {
                'is_anomaly': True,
                'type': 'VOLATILITY_SPIKE',
                'severity': 'critical',
                'current_volatility': historical_vol,
                'predicted_volatility': predicted_vol,
                'change_ratio': vol_change,
                'description': f'波动率可能飙升{vol_change*100:.0f}%，当前{historical_vol*100:.1f}%'
            }
        elif vol_change < -self.threshold:
            return {
                'is_anomaly': True,
                'type': 'VOLATILITY_DROP',
                'severity': 'warning',
                'current_volatility': historical_vol,
                'predicted_volatility': predicted_vol,
                'change_ratio': vol_change,
                'description': f'波动率可能下降{-vol_change*100:.0f}%'
            }
        
        return {
            'is_anomaly': False,
            'current_volatility': historical_vol,
            'predicted_volatility': predicted_vol,
            'change_ratio': vol_change
        }
```

#### 2.6.2 波动率微笑检测

```python
class VolatilitySmileDetector:
    """波动率微笑检测器（期权隐含波动率分析）"""
    
    def __init__(self, skew_threshold: float = 0.1):
        """
        参数：
        - skew_threshold: 偏度阈值
        """
        self.skew_threshold = skew_threshold
    
    def detect_skew(self, strikes: np.ndarray, 
                    ivs: np.ndarray) -> Dict:
        """
        检测波动率偏斜
        
        正常情况：ATM期权IV最低，OTM认购和认沽IV逐渐升高
        异常情况：深度OTM期权IV异常高（恐慌指标）
        """
        # 计算偏度
        atm_idx = np.argmin(np.abs(strikes - strikes.mean()))
        otm_put_iv = ivs[:atm_idx].mean() if atm_idx > 0 else ivs[0]
        otm_call_iv = ivs[atm_idx+1:].mean() if atm_idx < len(ivs) - 1 else ivs[-1]
        
        # 波动率偏斜
        put_call_skew = otm_put_iv - otm_call_iv
        
        if put_call_skew > self.skew_threshold:
            return {
                'is_anomaly': True,
                'type': 'SKEW_PUT',
                'severity': 'warning',
                'skew': put_call_skew,
                'description': '认沽期权IV异常高于认购，市场恐慌情绪'
            }
        elif put_call_skew < -self.skew_threshold:
            return {
                'is_anomaly': True,
                'type': 'SKEW_CALL',
                'severity': 'info',
                'skew': put_call_skew,
                'description': '认购期权IV高于认沽，市场乐观情绪'
            }
        
        return {
            'is_anomaly': False,
            'skew': put_call_skew,
            'description': '波动率微笑正常'
        }
```

### 2.7 事件驱动检测

#### 2.7.1 财报发布事件检测

```python
class EarningsEventDetector:
    """财报发布事件检测器"""
    
    def __init__(self, price_change_threshold: float = 0.05):
        """
        参数：
        - price_change_threshold: 价格变化阈值
        """
        self.threshold = price_change_threshold
    
    def detect_earnings_reaction(self, symbol: str,
                                  pre_announcement_return: float,
                                  post_announcement_return: float) -> Dict:
        """
        检测财报发布后的市场反应
        
        参数：
        - pre_announcement_return: 公告前N日累计收益
        - post_announcement_return: 公告后累计收益
        """
        # 异常收益检测
        if post_announcement_return > self.threshold:
            return {
                'is_anomaly': True,
                'type': 'EARNINGS_BEAT',
                'severity': 'info',
                'return': post_announcement_return,
                'description': f'财报超预期，公告后上涨{post_announcement_return*100:.1f}%'
            }
        elif post_announcement_return < -self.threshold:
            return {
                'is_anomaly': True,
                'type': 'EARNINGS_MISS',
                'severity': 'warning',
                'return': post_announcement_return,
                'description': f'财报不及预期，公告后下跌{abs(post_announcement_return)*100:.1f}%'
            }
        
        # 内幕交易检测（公告前异常收益）
        if abs(pre_announcement_return) > self.threshold * 2:
            return {
                'is_anomaly': True,
                'type': 'POTENTIAL_INSIDER_TRADING',
                'severity': 'critical',
                'pre_return': pre_announcement_return,
                'description': f'公告前异常收益{pre_announcement_return*100:.1f}%，可能存在内幕交易'
            }
        
        return {
            'is_anomaly': False,
            'description': '财报反应正常'
        }
```

#### 2.7.2 重大事项事件检测

```python
class MajorEventDetector:
    """重大事项事件检测器"""
    
    def __init__(self):
        # 重大事项类型
        self.event_types = [
            '重大资产重组',
            '股权激励',
            '股份回购',
            '高管增减持',
            '关联交易',
            '对外担保',
            '诉讼仲裁'
        ]
    
    def analyze_event_impact(self, event_type: str,
                            event_details: Dict,
                            price_change: float) -> Dict:
        """
        分析重大事项对股价的影响
        
        参数：
        - event_type: 事件类型
        - event_details: 事件详情
        - price_change: 事件公告后股价变化
        """
        # 根据事件类型设置预期影响
        expected_impacts = {
            '重大资产重组': {'bullish': True, 'weight': 0.2},
            '股权激励': {'bullish': True, 'weight': 0.1},
            '股份回购': {'bullish': True, 'weight': 0.15},
            '高管增持': {'bullish': True, 'weight': 0.1},
            '高管减持': {'bullish': False, 'weight': 0.1},
            '关联交易': {'bullish': None, 'weight': 0.05},
            '对外担保': {'bullish': False, 'weight': 0.05}
        }
        
        if event_type not in expected_impacts:
            return {'is_anomaly': False, 'description': '非重大事项'}
        
        expected = expected_impacts[event_type]
        
        # 检测是否符合预期
        if expected['bullish'] is None:
            # 中性事件，波动即异常
            if abs(price_change) > expected['weight']:
                return {
                    'is_anomaly': True,
                    'type': event_type,
                    'severity': 'info',
                    'price_change': price_change,
                    'description': f'{event_type}公告后异常波动{price_change*100:.1f}%'
                }
        elif expected['bullish'] and price_change > 0:
            # 利好事件，股价上涨符合预期
            return {'is_anomaly': False, 'description': '符合预期'}
        elif expected['bullish'] and price_change < 0:
            # 利好事件，股价下跌
            return {
                'is_anomaly': True,
                'type': event_type,
                'severity': 'warning',
                'price_change': price_change,
                'description': f'{event_type}应为利好，但股价下跌{abs(price_change)*100:.1f}%'
            }
        elif not expected['bullish'] and price_change < 0:
            # 利空事件，股价下跌符合预期
            return {'is_anomaly': False, 'description': '符合预期'}
        elif not expected['bullish'] and price_change > 0:
            # 利空事件，股价上涨
            return {
                'is_anomaly': True,
                'type': event_type,
                'severity': 'info',
                'price_change': price_change,
                'description': f'{event_type}应为利空，但股价上涨{price_change*100:.1f}%'
            }
        
        return {'is_anomaly': False, 'description': '正常'}
```

### 2.8 机器学习异常检测

#### 2.8.1 LSTM价格预测异常检测

```python
class LSTMAnomalyDetector:
    """LSTM价格预测异常检测器"""
    
    def __init__(self, sequence_length: int = 20):
        """
        参数：
        - sequence_length: 输入序列长度
        """
        self.sequence_length = sequence_length
        self.model = None
    
    def build_model(self, input_shape):
        """构建LSTM模型"""
        import tensorflow as tf
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout
        
        model = Sequential([
            LSTM(64, return_sequences=True, input_shape=input_shape),
            Dropout(0.2),
            LSTM(32, return_sequences=False),
            Dropout(0.2),
            Dense(16, activation='relu'),
            Dense(1)
        ])
        
        model.compile(optimizer='adam', loss='mse')
        return model
    
    def detect_anomaly(self, prices: pd.Series) -> Dict:
        """
        检测价格异常
        
        基于LSTM预测的异常检测：
        1. 使用历史数据训练模型
        2. 预测下一个价格
        3. 计算预测误差
        4. 误差超过阈值即为异常
        """
        if len(prices) < self.sequence_length + 30:
            return {'is_anomaly': False, 'reason': '数据不足'}
        
        # 标准化
        scaled_prices = self._scale(prices)
        
        # 准备序列数据
        X, y = self._create_sequences(scaled_prices)
        
        # 训练模型（使用最后30天作为验证）
        if self.model is None:
            self.model = self.build_model((self.sequence_length, 1))
            
            X_train, X_val = X[:-30], X[-30:]
            y_train, y_val = y[:-30], y[-30:]
            
            self.model.fit(X_train, y_train, epochs=20, 
                          validation_data=(X_val, y_val), verbose=0)
        
        # 预测
        predictions = self.model.predict(X[-30:], verbose=0)
        predictions = self._inverse_scale(predictions)
        actuals = prices.iloc[-30:].values
        
        # 计算预测误差
        errors = np.abs(predictions.flatten() - actuals) / actuals
        
        # 检测异常（误差超过3倍标准差）
        error_mean = errors.mean()
        error_std = errors.std()
        threshold = error_mean + 3 * error_std
        
        anomalies = errors > threshold
        
        if anomalies.any():
            anomaly_idx = np.where(anomalies)[0]
            return {
                'is_anomaly': True,
                'type': 'LSTM_PREDICTION_ANOMALY',
                'severity': 'warning',
                'anomaly_count': len(anomaly_idx),
                'max_error': errors.max(),
                'threshold': threshold,
                'description': f'价格预测异常，{len(anomaly_idx)}个时间点预测误差超过阈值'
            }
        
        return {
            'is_anomaly': False,
            'mean_error': error_mean,
            'max_error': errors.max()
        }
    
    def _scale(self, data):
        """标准化"""
        from sklearn.preprocessing import MinMaxScaler
        self.scaler = MinMaxScaler()
        return self.scaler.fit_transform(data.values.reshape(-1, 1))
    
    def _inverse_scale(self, data):
        """反标准化"""
        return self.scaler.inverse_transform(data)
    
    def _create_sequences(self, data):
        """创建序列数据"""
        X, y = [], []
        for i in range(len(data) - self.sequence_length):
            X.append(data[i:i+self.sequence_length])
            y.append(data[i+self.sequence_length])
        return np.array(X), np.array(y)
```

#### 2.8.2 基于Twitter情绪的异常检测

```python
class SocialSentimentAnalyzer:
    """社交媒体情绪异常检测器"""
    
    def __init__(self, sentiment_threshold: float = 0.3):
        """
        参数：
        - sentiment_threshold: 情绪变化阈值
        """
        self.threshold = sentiment_threshold
    
    def analyze_sentiment_change(self, symbol: str,
                                  tweets: List[Dict]) -> Dict:
        """
        分析社交媒体情绪变化
        
        参数：
        - symbol: 股票代码
        - tweets: 推文列表 [{content, timestamp, sentiment}]
        """
        if len(tweets) < 10:
            return {'is_anomaly': False, 'reason': '数据不足'}
        
        # 按时间分组
        recent_tweets = sorted(tweets, key=lambda x: x['timestamp'])[-50:]
        
        # 计算情绪分数
        sentiments = [t['sentiment'] for t in recent_tweets]
        
        # 分割成前后两段
        mid = len(sentiments) // 2
        early_sentiment = np.mean(sentiments[:mid])
        late_sentiment = np.mean(sentiments[mid:])
        
        # 情绪变化
        sentiment_change = late_sentiment - early_sentiment
        
        # 检测异常
        if sentiment_change > self.threshold:
            return {
                'is_anomaly': True,
                'type': 'SENTIMENT_SPIKE',
                'severity': 'info',
                'change': sentiment_change,
                'early_sentiment': early_sentiment,
                'late_sentiment': late_sentiment,
                'description': f'社交媒体情绪急剧看涨，变化{sentiment_change*100:.0f}%'
            }
        elif sentiment_change < -self.threshold:
            return {
                'is_anomaly': True,
                'type': 'SENTIMENT_DROP',
                'severity': 'warning',
                'change': sentiment_change,
                'early_sentiment': early_sentiment,
                'late_sentiment': late_sentiment,
                'description': f'社交媒体情绪急剧看跌，变化{abs(sentiment_change)*100:.0f}%'
            }
        
        return {
            'is_anomaly': False,
            'sentiment_change': sentiment_change,
            'current_sentiment': late_sentiment
        }
```

---

## 三、设计思考过程

### 3.1 需求分析与拆解

#### 第一步：识别核心用户故事

作为风控人员，我希望：
1. 当某只股票突然暴跌时立即收到告警
2. 当某个行业出现集体异动时收到通知
3. 能够自定义监控规则（阈值、标的、频率）

作为量化策略师，我希望：
1. 当出现金叉、死叉等信号时收到通知
2. 当关联品种出现异常联动时收到提醒
3. 能够查看历史异动记录

#### 第二步：拆解功能模块

| 功能模块 | 优先级 | 核心价值 |
|---------|-------|---------|
| 实时数据接入 | P0 | 提供数据基础 |
| 价格异动检测 | P0 | 发现价格异常 |
| 技术形态识别 | P1 | 发现交易信号 |
| 市场情绪监测 | P1 | 了解市场状态 |
| 关联分析 | P2 | 发现联动异常 |
| 告警通知 | P0 | 及时推送告警 |
| 规则配置 | P1 | 自定义规则 |

#### 第三步：确定技术约束

1. **实时性要求**：
   - 数据延迟 < 1秒
   - 告警延迟 < 3秒
   - 支持1000+标的并发监控

2. **数据要求**：
   - 实时行情：每秒更新
   - 分钟线：每分钟更新
   - 日线：每日收盘后更新

3. **可靠性要求**：
   - 系统可用性 > 99.9%
   - 告警送达率 > 99%

### 3.2 架构设计思考

#### 3.2.1 为什么选择WebSocket而非轮询

WebSocket相比HTTP轮询的优势：

| 对比项 | HTTP轮询 | WebSocket |
|-------|---------|----------|
| 延迟 | 秒级到分钟级 | 毫秒级 |
| 服务器负载 | 高（频繁请求） | 低（长连接） |
| 资源消耗 | 高（重复header） | 低（单次握手） |
| 实时性 | 差 | 好 |

**设计决策**：WebSocket用于实时告警推送，HTTP用于历史查询和规则配置。

#### 3.2.2 为什么需要规则引擎

异动检测规则复杂多样：

```python
# 简单规则
rule = {
    "field": "pct_change",
    "condition": ">",
    "value": 9.0
}

# 复杂规则
rule = {
    "type": "composite",
    "operator": "and",
    "rules": [
        {"field": "pct_change", "condition": ">", "value": 5.0},
        {"field": "volume_ratio", "condition": ">", "value": 3.0},
        {"field": "turnover_rate", "condition": ">", "value": 5.0}
    ]
}
```

**设计决策**：使用规则引擎支持动态规则配置和热更新。

#### 3.2.3 为什么需要告警聚合

当市场大幅波动时，可能产生大量告警：

- 单只股票跌停
- 所属行业整体下跌
- 相关期货跟跌

**设计决策**：使用告警聚合机制，避免告警风暴。

```python
def aggregate_alerts(alerts, group_by='concept', time_window=300):
    """
    聚合相关告警
    
    Args:
        alerts: 告警列表
        group_by: 分组字段（concept/industry/related）
        time_window: 时间窗口（秒）
    """
    # 按分组字段和时间窗口聚合
    grouped = {}
    
    for alert in alerts:
        group_key = f"{getattr(alert, group_by)}_{alert.timestamp // time_window}"
        
        if group_key not in grouped:
            grouped[group_key] = []
        grouped[group_key].append(alert)
    
    # 生成聚合告警
    aggregated = []
    for group_key, group_alerts in grouped.items():
        if len(group_alerts) > 1:
            aggregated.append(create_aggregated_alert(group_alerts))
    
    return aggregated
```

### 3.3 核心算法设计思考

#### 3.3.1 异常检测算法

**统计方法**：
```python
def statistical_anomaly_detection(values, threshold=3):
    """
    基于统计的异常检测
    
    Args:
        values: 数据序列
        threshold: 标准差倍数
    """
    mean = values.mean()
    std = values.std()
    
    upper = mean + threshold * std
    lower = mean - threshold * std
    
    anomalies = values[(values > upper) | (values < lower)]
    
    return anomalies
```

**机器学习方法**：
```python
from sklearn.ensemble import IsolationForest

def ml_anomaly_detection(features, contamination=0.01):
    """
    基于Isolation Forest的异常检测
    
    Args:
        features: 特征矩阵
        contamination: 异常比例
    """
    model = IsolationForest(contamination=contamination)
    predictions = model.fit_predict(features)
    
    return predictions == -1  # True表示异常
```

#### 3.3.2 形态识别算法

**头肩顶识别**：
```python
def detect_head_and_shoulders(prices, window=60):
    """
    检测头肩顶形态
    
    头肩顶特征：
    1. 左肩：价格上升后回落
    2. 头部：价格创新高后回落
    3. 右肩：价格回升但低于头部后回落
    """
    # 找局部极大值
    peaks = find_local_peaks(prices, window)
    
    if len(peaks) < 3:
        return {'is_pattern': False, 'reason': '峰值不足'}
    
    # 检查是否是头肩顶
    left_shoulder = peaks[0]
    head = peaks[1]
    right_shoulder = peaks[2]
    
    # 头部高于左右肩
    if head.value > left_shoulder.value and head.value > right_shoulder.value:
        # 左右肩高度相近
        if abs(left_shoulder.value - right_shoulder.value) / left_shoulder.value < 0.1:
            return {
                'is_pattern': True,
                'pattern': 'HEAD_AND_SHOULDERS',
                'severity': 'warning',
                'description': '头肩顶形态，可能反转下跌'
            }
    
    return {'is_pattern': False, 'reason': '不符合头肩顶特征'}
```

---

## 四、详细技术方案

### 4.1 数据库表设计

#### 4.1.1 监控规则配置表

```sql
CREATE TABLE monitoring_rules (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_name VARCHAR(100) NOT NULL COMMENT '规则名称',
    rule_type ENUM('price', 'volume', 'technical', 'sentiment', 'correlation') NOT NULL COMMENT '规则类型',
    target_type ENUM('stock', 'industry', 'concept', 'market') NOT NULL COMMENT '监控对象类型',
    target_value VARCHAR(500) COMMENT '监控对象（股票代码/行业/概念）',
    condition_type VARCHAR(50) NOT NULL COMMENT '条件类型',
    condition_params JSON NOT NULL COMMENT '条件参数',
    severity ENUM('critical', 'warning', 'info') NOT NULL COMMENT '告警级别',
    notification_channels JSON COMMENT '通知渠道',
    enabled TINYINT DEFAULT 1 COMMENT '是否启用',
    cooldown_seconds INT DEFAULT 300 COMMENT '冷却时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rule_type (rule_type),
    INDEX idx_target (target_type, target_value),
    INDEX idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='监控规则配置表';
```

#### 4.1.2 实时数据缓存表

```sql
CREATE TABLE realtime_price_cache (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL COMMENT '股票代码',
    trade_date DATE NOT NULL COMMENT '交易日期',
    trade_time TIME NOT NULL COMMENT '交易时间',
    open DECIMAL(20,4) COMMENT '开盘价',
    high DECIMAL(20,4) COMMENT '最高价',
    low DECIMAL(20,4) COMMENT '最低价',
    close DECIMAL(20,4) COMMENT '收盘价',
    volume BIGINT COMMENT '成交量',
    amount DECIMAL(20,4) COMMENT '成交额',
    pct_change DECIMAL(10,4) COMMENT '涨跌幅',
    updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
    UNIQUE KEY uk_symbol_time (symbol, trade_date, trade_time),
    INDEX idx_symbol (symbol),
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实时价格缓存表';
```

#### 4.1.3 异动告警记录表

```sql
CREATE TABLE anomaly_alerts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    alert_uuid VARCHAR(36) NOT NULL COMMENT '告警唯一标识',
    rule_id BIGINT NOT NULL COMMENT '触发规则ID',
    rule_name VARCHAR(100) NOT NULL,
    alert_type ENUM('price', 'volume', 'technical', 'sentiment', 'correlation') NOT NULL,
    target_type ENUM('stock', 'industry', 'concept', 'market') NOT NULL,
    target_value VARCHAR(100) NOT NULL COMMENT '监控对象',
    severity ENUM('critical', 'warning', 'info') NOT NULL,
    alert_title VARCHAR(200) NOT NULL COMMENT '告警标题',
    alert_content TEXT NOT NULL COMMENT '告警内容',
    alert_details JSON COMMENT '告警详情',
    alert_score DECIMAL(10,4) COMMENT '异动评分',
    first_detected_at DATETIME NOT NULL COMMENT '首次检测时间',
    last_detected_at DATETIME NOT NULL COMMENT '最后检测时间',
    resolved_at DATETIME COMMENT '解决时间',
    status ENUM('active', 'acknowledged', 'resolved', 'ignored') DEFAULT 'active',
    notification_sent TINYINT DEFAULT 0 COMMENT '是否已发送通知',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_alert_uuid (alert_uuid),
    INDEX idx_status (status),
    INDEX idx_severity (severity),
    INDEX idx_target (target_type, target_value),
    INDEX idx_first_detected (first_detected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='异动告警记录表';
```

#### 4.1.4 市场状态快照表

```sql
CREATE TABLE market_state_snapshots (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    trade_date DATE NOT NULL COMMENT '交易日期',
    trade_time TIME NOT NULL COMMENT '快照时间',
    up_limit_count INT DEFAULT 0 COMMENT '涨停家数',
    down_limit_count INT DEFAULT 0 COMMENT '跌停家数',
    up_limit_break_count INT DEFAULT 0 COMMENT '炸板家数',
    total_volume DECIMAL(20,4) COMMENT '总成交量',
    volume_change_ratio DECIMAL(10,4) COMMENT '量比',
    sentiment_score DECIMAL(10,4) COMMENT '情绪评分',
    market_status ENUM('bullish', 'bearish', 'neutral') DEFAULT 'neutral',
    summary TEXT COMMENT '市场综述',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_time (trade_date, trade_time),
    INDEX idx_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='市场状态快照表';
```

### 4.2 API接口设计

#### 4.2.1 实时行情接口

**接口路径**：`GET /api/v1/market/realtime`

**响应结果**：
```json
{
    "code": 200,
    "message": "success",
    "data": {
        "timestamp": "2024-01-10T14:30:00",
        "market_status": "bullish",
        "indices": [
            {
                "symbol": "000001",
                "name": "上证指数",
                "close": 3150.25,
                "pct_change": 0.85,
                "volume": 28000000000
            },
            {
                "symbol": "399001",
                "name": "深证成指",
                "close": 11200.50,
                "pct_change": 1.12,
                "volume": 35000000000
            }
        ],
        "top_gainers": [
            {"symbol": "600519", "name": "贵州茅台", "pct_change": 5.23},
            {"symbol": "000001", "name": "平安银行", "pct_change": 4.85}
        ],
        "top_losers": [
            {"symbol": "300750", "name": "宁德时代", "pct_change": -3.21}
        ],
        "limit_up_count": 35,
        "limit_down_count": 2,
        "sentiment_score": 68
    }
}
```

#### 4.2.2 异动告警接口

**接口路径**：`GET /api/v1/market/anomalies`

**请求参数**：
```json
{
    "start_time": "2024-01-10T09:30:00",
    "end_time": "2024-01-10T15:00:00",
    "severity": "critical",
    "alert_type": "price",
    "target_type": "stock",
    "page": 1,
    "page_size": 20
}
```

**响应结果**：
```json
{
    "code": 200,
    "message": "success",
    "data": {
        "total": 12,
        "page": 1,
        "page_size": 20,
        "anomalies": [
            {
                "id": 1001,
                "uuid": "alert-uuid-123",
                "type": "price",
                "target": "600519",
                "name": "贵州茅台",
                "severity": "critical",
                "title": "快速涨停预警",
                "content": "贵州茅台5分钟内涨幅达到5.23%，接近涨停板",
                "details": {
                    "current_price": 1850.00,
                    "pct_change": 5.23,
                    "volume_ratio": 3.5,
                    "time_window": "5分钟"
                },
                "detected_at": "2024-01-10T10:15:32",
                "status": "active"
            }
        ]
    }
}
```

#### 4.2.3 规则配置接口

**接口路径**：`POST /api/v1/market/rules`

**请求参数**：
```json
{
    "rule_name": "涨停股监控",
    "rule_type": "price",
    "target_type": "stock",
    "target_value": "600519,000001,300750",
    "condition_type": "threshold",
    "condition_params": {
        "field": "pct_change",
        "operator": ">=",
        "value": 9.0
    },
    "severity": "critical",
    "notification_channels": ["websocket", "dingtalk"],
    "cooldown_seconds": 60
}
```

#### 4.2.4 市场情绪接口

**接口路径**：`GET /api/v1/market/sentiment`

**响应结果**：
```json
{
    "code": 200,
    "message": "success",
    "data": {
        "timestamp": "2024-01-10T14:30:00",
        "indices": {
            "shanghai": {"pct_change": 0.85, "trend": "up"},
            "shenzhen": {"pct_change": 1.12, "trend": "up"},
            "gem": {"pct_change": 0.95, "trend": "up"}
        },
        "market_breadth": {
            "up_count": 2850,
            "down_count": 1200,
            "unchanged_count": 150,
            "up_ratio": 0.68
        },
        "limit_status": {
            "limit_up": 35,
            "limit_down": 2,
            "limit_up_break": 5,
            "limit_down_break": 0
        },
        "sector_performance": [
            {"sector": "银行", "pct_change": 2.5, "up_ratio": 0.85},
            {"sector": "非银金融", "pct_change": 2.1, "up_ratio": 0.78}
        ],
        "sentiment_score": 68,
        "sentiment_label": "偏乐观",
        "risk_level": "low"
    }
}
```

### 4.3 服务层设计

#### 4.3.1 异动检测服务

```python
class AnomalyDetectionService:
    """异动检测服务"""
    
    def __init__(self, rule_repo, price_service, alert_service):
        self.rule_repo = rule_repo
        self.price_service = price_service
        self.alert_service = alert_service
    
    async def detect_anomalies(self):
        """
        执行异动检测
        
        设计思考：
        1. 获取所有启用的规则
        2. 获取最新价格数据
        3. 并行执行各规则的检测
        4. 收集并处理异动
        """
        # 获取规则
        rules = await self.rule_repo.get_enabled_rules()
        
        # 获取价格数据
        symbols = self._extract_symbols(rules)
        prices = await self.price_service.get_latest_prices(symbols)
        
        # 并行检测
        all_anomalies = []
        
        for rule in rules:
            anomalies = await self._check_rule(rule, prices)
            all_anomalies.extend(anomalies)
        
        # 聚合告警
        aggregated_alerts = self._aggregate_alerts(all_anomalies)
        
        # 发送告警
        for alert in aggregated_alerts:
            await self.alert_service.send(alert)
        
        return aggregated_alerts
    
    async def _check_rule(self, rule, prices) -> List[Anomaly]:
        """检查单个规则"""
        anomalies = []
        
        # 获取目标标的的价格
        target_prices = self._get_target_prices(prices, rule.target_value)
        
        for symbol, data in target_prices.items():
            # 根据条件类型检测
            if rule.condition_type == 'threshold':
                is_anomaly = self._check_threshold(data, rule.condition_params)
            elif rule.condition_type == 'range':
                is_anomaly = self._check_range(data, rule.condition_params)
            elif rule.condition_type == 'pattern':
                is_anomaly = await self._check_pattern(data, rule.condition_params)
            
            if is_anomaly:
                anomalies.append(Anomaly(
                    rule_id=rule.id,
                    symbol=symbol,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    details=data,
                    condition=rule.condition_params
                ))
        
        return anomalies
    
    def _check_threshold(self, data, params) -> bool:
        """阈值检查"""
        field = params['field']
        operator = params['operator']
        value = params['value']
        
        field_value = data.get(field)
        if field_value is None:
            return False
        
        if operator == '>':
            return field_value > value
        elif operator == '>=':
            return field_value >= value
        elif operator == '<':
            return field_value < value
        elif operator == '<=':
            return field_value <= value
        elif operator == '==':
            return field_value == value
        
        return False
```

#### 4.3.2 技术形态识别服务

```python
class PatternRecognitionService:
    """技术形态识别服务"""
    
    def __init__(self, price_repo):
        self.price_repo = price_repo
    
    async def recognize_patterns(self, symbol, pattern_types=None):
        """
        识别技术形态
        
        设计思考：
        1. 获取历史价格数据
        2. 依次检测各类型形态
        3. 返回识别结果
        """
        if pattern_types is None:
            pattern_types = ['breakout', 'golden_cross', 'death_cross', 
                            'head_and_shoulders', 'double_top', 'double_bottom']
        
        # 获取历史数据
        prices = await self.price_repo.get_daily_prices(symbol, period='1Y')
        
        results = []
        
        for pattern_type in pattern_types:
            if pattern_type == 'breakout':
                result = self._detect_breakout(prices)
            elif pattern_type == 'golden_cross':
                result = self._detect_golden_cross(prices)
            elif pattern_type == 'death_cross':
                result = self._detect_death_cross(prices)
            elif pattern_type == 'head_and_shoulders':
                result = self._detect_head_and_shoulders(prices)
            elif pattern_type == 'double_top':
                result = self._detect_double_top(prices)
            elif pattern_type == 'double_bottom':
                result = self._detect_double_bottom(prices)
            
            if result['is_pattern']:
                results.append(result)
        
        return {
            'symbol': symbol,
            'patterns': results,
            'pattern_count': len(results),
            'bullish_patterns': [p for p in results if p['signal'] == 'bullish'],
            'bearish_patterns': [p for p in results if p['signal'] == 'bearish']
        }
    
    def _detect_golden_cross(self, prices):
        """检测金叉"""
        short_ma = prices['close'].rolling(5).mean().iloc[-1]
        long_ma = prices['close'].rolling(20).mean().iloc[-1]
        
        short_ma_prev = prices['close'].rolling(5).mean().iloc[-2]
        long_ma_prev = prices['close'].rolling(20).mean().iloc[-2]
        
        if short_ma_prev <= long_ma_prev and short_ma > long_ma:
            return {
                'type': 'golden_cross',
                'is_pattern': True,
                'signal': 'bullish',
                'description': '5日均线上穿20日均线，金叉出现',
                'short_ma': short_ma,
                'long_ma': long_ma
            }
        
        return {'type': 'golden_cross', 'is_pattern': False}
```

#### 4.3.3 告警通知服务

```python
class AlertNotificationService:
    """告警通知服务"""
    
    def __init__(self, alert_repo, ws_manager, dingtalk_client, email_client):
        self.alert_repo = alert_repo
        self.ws_manager = ws_manager
        self.dingtalk = dingtalk_client
        self.email = email_client
    
    async def send(self, alert):
        """
        发送告警
        
        设计思考：
        1. 检查冷却期，避免重复告警
        2. 根据配置发送各渠道通知
        3. 记录告警历史
        """
        # 检查冷却期
        if await self._is_in_cooldown(alert):
            return
        
        # 获取通知渠道
        channels = await self._get_notification_channels(alert)
        
        # 发送通知
        tasks = []
        
        if 'websocket' in channels:
            tasks.append(self._send_websocket(alert))
        
        if 'dingtalk' in channels:
            tasks.append(self._send_dingtalk(alert))
        
        if 'email' in channels:
            tasks.append(self._send_email(alert))
        
        await asyncio.gather(*tasks)
        
        # 记录告警
        await self.alert_repo.create(alert)
    
    async def _send_websocket(self, alert):
        """WebSocket推送"""
        await self.ws_manager.broadcast({
            'type': 'anomaly_alert',
            'alert_id': alert.id,
            'uuid': alert.uuid,
            'severity': alert.severity,
            'title': alert.title,
            'content': alert.content,
            'details': alert.details,
            'timestamp': alert.created_at.isoformat()
        })
    
    async def _send_dingtalk(self, alert):
        """钉钉通知"""
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【{alert.severity.upper()}】{alert.title}",
                "text": f"""
## 市场异动告警

**告警级别**: {alert.severity}
**告警时间**: {alert.created_at}
**监控标的**: {alert.target_value}
**告警内容**: {alert.content}

---
                """
            }
        }
        await self.dingtalk.send_message(message)
```

### 4.4 WebSocket实时推送

#### 4.4.1 连接管理

```python
class WebSocketManager:
    """WebSocket连接管理"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        """建立连接"""
        await websocket.accept()
        self.active_connections[client_id] = websocket
    
    async def disconnect(self, client_id: str):
        """断开连接"""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
    
    async def send_personal_message(self, message: dict, client_id: str):
        """发送个人消息"""
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_json(message)
    
    async def broadcast(self, message: dict):
        """广播消息"""
        for connection in self.active_connections.values():
            await connection.send_json(message)
```

#### 4.4.2 前端连接示例

```javascript
// 前端WebSocket连接
const ws = new WebSocket('ws://localhost:8888/ws/market');

ws.onopen = function() {
    console.log('WebSocket连接已建立');
    
    // 订阅特定类型的告警
    ws.send(JSON.stringify({
        action: 'subscribe',
        alert_types: ['price', 'technical'],
        severity: ['critical']
    }));
};

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    
    if (data.type === 'anomaly_alert') {
        // 显示告警通知
        showNotification(data.title, data.content, data.severity);
        
        // 更新告警列表
        addAlertToList(data);
    }
};
```

---

## 五、验证方法与测试方案

### 5.1 功能测试用例

#### 5.1.1 价格异动检测验证

**测试场景**：
- 构造价格数据，包含各种异动类型
- 执行异动检测
- 验证各类型被正确识别

**测试数据构造**：
```python
# 正常数据
normal_prices = generate_prices(100, base=10, volatility=0.02)

# 快速涨停数据
spike_prices = generate_prices(100, base=10, volatility=0.02)
spike_prices[-1] = spike_prices[-2] * 1.095  # 5分钟内涨9.5%

# 闪崩数据
crash_prices = generate_prices(100, base=10, volatility=0.02)
crash_prices[-1] = crash_prices[-2] * 0.94  # 5分钟内跌6%
```

**预期结果**：
- 正常数据：不触发告警
- 快速涨停数据：触发RAPID_LIMIT_UP告警
- 闪崩数据：触发FLASH_CRASH告警

#### 5.1.2 技术形态识别验证

**测试场景**：
- 构造包含金叉的价格数据
- 执行形态识别
- 验证金叉被正确识别

**测试数据构造**：
```python
# 金叉数据
prices = generate_prices(100, base=10, volatility=0.02)
# 短期均线从下方突破长期均线
short_ma = prices.rolling(5).mean()
long_ma = prices.rolling(20).mean()
# 修改最后两天
prices.iloc[-2:] = prices.iloc[-3] * 1.02  # 短期均线上升
```

**预期结果**：
- 正确识别金叉
- 返回信号为bullish

#### 5.1.3 市场情绪监测验证

**测试场景**：
- 构造涨跌停数据
- 执行情绪计算
- 验证情绪评分正确

**测试数据构造**：
```python
market_data = pd.DataFrame({
    'pct_change': [0.5, 0.3, -0.2, 0.1, 0.4, 0.2, 0.6, 0.1, 0.3, 0.2],
    'volume': [1000000] * 10,
    'volume_yesterday': [800000] * 10
})

# 添加涨跌停
market_data['pct_change'].iloc[0] = 9.9  # 涨停
market_data['pct_change'].iloc[1] = 9.8  # 涨停
market_data['pct_change'].iloc[-1] = -9.9  # 跌停
```

**预期结果**：
- 涨停家数：2
- 跌停家数：1
- 情绪评分 > 60（偏乐观）

### 5.2 性能测试用例

#### 5.2.1 异动检测性能

**测试条件**：
- 监控标的：1000只股票
- 数据更新频率：每秒
- 规则数量：10条

**性能指标**：
- 检测延迟 < 1秒
- 内存占用 < 2GB

**测试代码**：
```python
@pytest.mark.asyncio
async def test_detection_performance():
    # 准备1000只股票的价格数据
    prices = generate_prices_for_symbols(1000)
    
    start_time = time.time()
    
    service = AnomalyDetectionService(...)
    anomalies = await service.detect_anomalies()
    
    end_time = time.time()
    
    assert end_time - start_time < 1  # < 1秒
    assert len(anomalies) >= 0  # 能返回结果
```

#### 5.2.2 WebSocket推送性能

**测试条件**：
- 连接数：100
- 消息频率：10条/秒
- 消息大小：1KB

**性能指标**：
- 推送延迟 < 100ms
- 丢包率 < 0.1%

### 5.3 边界测试用例

| 测试场景 | 输入 | 预期行为 |
|---------|------|---------|
| 无数据 | 空价格序列 | 返回空结果 |
| 数据不足 | 少于均线周期 | 返回数据不足提示 |
| 涨跌停停牌 | 涨停/跌停次日停牌 | 正确识别状态 |
| 并发连接过多 | 1000+连接 | 优雅降级 |

---

## 六、部署与运维

### 6.1 部署架构

```
                              ┌─────────────────┐
                              │     Nginx       │
                              │  (负载均衡)     │
                              └────────┬────────┘
                                       │
               ┌────────────────────────┼────────────────────────┐
               │                        │                        │
               ▼                        ▼                        ▼
       ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
       │   API Server  │      │   WebSocket   │      │   Worker      │
       │  (监控服务)   │      │   Server      │      │  (检测任务)   │
       └───────────────┘      └───────────────┘      └───────────────┘
               │                        │                        │
               └────────────────────────┼────────────────────────┘
                                        │
                                       ▼
                         ┌─────────────────────────┐
                         │    Redis                │
                         │  (连接管理/状态缓存)    │
                         └─────────────────────────┘
```

### 6.2 监控指标

| 指标 | 阈值 | 告警方式 |
|-----|------|---------|
| WebSocket连接数 | > 1000 | 日志记录 |
| 告警延迟 | > 3秒 | 钉钉通知 |
| 异动检测耗时 | > 1秒 | 日志记录 |
| 系统CPU | > 80% | 邮件通知 |
| 系统内存 | > 85% | 邮件通知 |

### 6.3 运维脚本

```bash
#!/bin/bash
# 查看WebSocket连接数
echo "WebSocket connections:"
ss -tn | grep 8889 | wc -l

# 查看告警发送统计
curl -s http://localhost:8888/api/v1/market/anomalies/stats

# 重启监控服务
systemctl restart market-monitor

# 查看实时告警
tail -f /var/log/market-monitor/alerts.log
```

---

## 附录：相关三方库和API

### 一、实时数据API

| 服务商 | 数据类型 | 说明 |
|-------|---------|------|
| AkShare | 实时行情 | 免费，秒级延迟 |
| Tushare | 实时/历史 | 免费/付费 |
| 东方财富 | 实时行情 | 需API权限 |
| 同花顺 | 实时行情 | 需API权限 |

### 二、WebSocket库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| websockets | >=10.0 | WebSocket服务端 | `pip install websockets` |
| socket.io | >=4.0 | WebSocket客户端 | `pip install python-socketio` |
| FastAPI | >=0.100 | WebSocket支持 | `pip install fastapi` |

### 三、告警通知库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| dingtalk | >=2.0 | 钉钉通知 | `pip install dingtalk` |
| aiosmtplib | >=2.0 | 异步邮件 | `pip install aiosmtplib` |
| twilio | >=7.0 | 短信通知 | `pip install twilio` |

### 四、技术分析库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| TA-Lib | >=0.4 | 技术指标 | `pip install ta-lib` |
| pandas-ta | >=0.3 | 技术分析 | `pip install pandas-ta` |
| technical | >=1.0 | 技术形态 | `pip install technical` |

### 五、异常检测库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| scikit-learn | >=1.3 | 机器学习 | `pip install scikit-learn` |
| PyOD | >=0.11 | 异常检测 | `pip install pyod` |
| scipy | >=1.10 | 统计检验 | `pip install scipy` |

### 六、代码示例

**使用AkShare获取实时行情**

```python
import akshare as ak

# 获取实时行情
stock_df = ak.stock_zh_a_spot_em()

# 获取涨停板股票
limit_up_df = stock_df[stock_df['涨跌幅'] >= 9.9]
print(f"涨停家数: {len(limit_up_df)}")

# 获取5分钟内涨跌幅
stock_df['5分钟涨跌幅'] = (stock_df['最新价'] / stock_df['昨收'] - 1) * 100
rapid_rise = stock_df[stock_df['5分钟涨跌幅'] >= 5.0]
print(f"5分钟内涨超5%: {len(rapid_rise)}")
```

**WebSocket服务端实现**

```python
import asyncio
from websockets import serve

async def echo(websocket):
    async for message in websocket:
        # 解析订阅请求
        data = json.loads(message)
        
        if data['action'] == 'subscribe':
            # 订阅告警
            await subscribe_alerts(websocket, data)
        
        await websocket.send(f"收到: {message}")

async def subscribe_alerts(websocket, params):
    """订阅告警通知"""
    while True:
        # 检测异动
        anomalies = await detect_anomalies()
        
        # 过滤符合条件的告警
        filtered = filter_anomalies(anomalies, params)
        
        for alert in filtered:
            await websocket.send(json.dumps({
                'type': 'anomaly_alert',
                'data': alert
            }))
        
        await asyncio.sleep(1)  # 每秒检测

async def main():
    async with serve(echo, "localhost", 8889):
        await asyncio.Future()  # 运行 forever

asyncio.run(main())
```

**技术形态识别**

```python
import pandas as pd
import numpy as np

def detect_golden_cross(prices, short_window=5, long_window=20):
    """
    检测金叉
    
    金叉：短期均线从下方突破长期均线
    """
    short_ma = prices['close'].rolling(short_window).mean()
    long_ma = prices['close'].rolling(long_window).mean()
    
    # 获取最近两天
    short_ma_current = short_ma.iloc[-1]
    long_ma_current = long_ma.iloc[-1]
    short_ma_prev = short_ma.iloc[-2]
    long_ma_prev = long_ma.iloc[-2]
    
    # 金叉条件
    if short_ma_prev <= long_ma_prev and short_ma_current > long_ma_current:
        return {
            'is_pattern': True,
            'type': 'golden_cross',
            'signal': 'bullish',
            'short_ma': short_ma_current,
            'long_ma': long_ma_current,
            'description': f'金叉出现，5日均线({short_ma_current:.2f})上穿20日均线({long_ma_current:.2f})'
        }
    
    return {'is_pattern': False}

def detect_breakout(prices, window=20, threshold=0.02):
    """
    检测突破
    
    价格突破近期高点或低点
    """
    recent_high = prices['high'].rolling(window).max().iloc[-1]
    recent_low = prices['low'].rolling(window).min().iloc[-1]
    current_close = prices['close'].iloc[-1]
    
    # 向上突破
    if current_close > recent_high * (1 - threshold):
        strength = (current_close - recent_high) / recent_high
        return {
            'is_pattern': True,
            'type': 'breakout',
            'direction': 'up',
            'level': recent_high,
            'strength': strength,
            'signal': 'bullish'
        }
    
    # 向下突破
    if current_close < recent_low * (1 + threshold):
        strength = (recent_low - current_close) / recent_low
        return {
            'is_pattern': True,
            'type': 'breakout',
            'direction': 'down',
            'level': recent_low,
            'strength': strength,
            'signal': 'bearish'
        }
    
    return {'is_pattern': False}
```

**市场情绪计算**

```python
def calculate_sentiment(market_data):
    """
    计算市场情绪评分（0-100）
    
    评分规则：
    - 基准分：50
    - 涨跌停比>3：+10
    - 涨跌停比<1：-10
    - 炸板率>50%：-5
    - 放量（量比>1.5）：+5
    - 缩量（量比<0.7）：-5
    """
    # 涨跌停统计
    up_count = len(market_data[market_data['pct_change'] >= 9.9])
    down_count = len(market_data[market_data['pct_change'] <= -9.9])
    
    # 涨跌停比
    limit_ratio = up_count / max(down_count, 1)
    
    # 炸板率
    up_attempts = len(market_data[market_data['pct_change'] >= 7])
    break_rate = (up_attempts - up_count) / max(up_attempts, 1)
    
    # 量比
    volume_ratio = market_data['volume'].sum() / market_data['volume_yesterday'].sum()
    
    # 计算评分
    score = 50  # 基准分
    
    if limit_ratio > 3:
        score += 10
    elif limit_ratio < 1:
        score -= 10
    
    if break_rate > 0.5:
        score -= 5
    
    if volume_ratio > 1.5:
        score += 5
    elif volume_ratio < 0.7:
        score -= 5
    
    # 限制在0-100
    score = max(0, min(100, score))
    
    # 情绪标签
    if score > 60:
        label = 'bullish'
    elif score < 40:
        label = 'bearish'
    else:
        label = 'neutral'
    
    return {
        'score': score,
        'label': label,
        'limit_ratio': limit_ratio,
        'break_rate': break_rate,
        'volume_ratio': volume_ratio
    }
```

### 七、资源链接

| 资源类型 | 链接 | 说明 |
|---------|------|------|
| AkShare文档 | https://akshare.xyz/ | 实时数据获取 |
| WebSocket教程 | https://websockets.readthedocs.io/ | WebSocket开发 |
| 技术分析指南 | https://www.investopedia.com/technical-analysis-4689753 | 技术形态说明 |
| TA-Lib文档 | https://ta-lib.org/ | 技术指标库 |
