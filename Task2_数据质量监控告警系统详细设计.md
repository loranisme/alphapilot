# 任务二：数据质量监控告警系统详细设计文档

## 一、模块概述与业务定位

### 1.1 为什么需要数据质量监控

在量化投资领域，数据是策略的"燃料"。如果数据质量出现问题，比如价格数据延迟、因子计算错误、收益数据缺失，直接影响策略的运行效果和风控能力。历史上因数据问题导致的量化事故屡见不鲜：

- **2012年光大证券"乌龙指"事件**：因订单系统数据错误导致巨量委托，引发市场剧烈波动
- **2015年A股股灾**：部分数据源出现数据延迟，导致风控模型失效
- **2020年疫情冲击**：数据源更新延迟，部分策略未能及时响应市场变化

数据质量监控系统的核心价值在于：**在数据问题影响策略运行之前，及时发现并告警**。

### 1.2 模块在整体架构中的位置

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户层（前端界面）                               │
│      数据健康看板 │ 告警历史 │ 规则配置 │ 异常详情                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API服务层（FastAPI）                               │
│   /api/v1/data-quality/health  │  /api/v1/data-quality/alerts               │
│   /api/v1/data-quality/rules   │  /api/v1/data-quality/anomalies            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          业务逻辑层（Services）                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 健康检查服务   │  │ 异常检测服务   │  │ 告警通知服务   │                 │
│  │ (Completeness) │  │ (Anomaly)      │  │ (Notification) │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 时效性服务     │  │ 合理性服务     │  │ 报告生成服务   │                 │
│  │ (Timeliness)   │  │ (Validity)     │  │ (Reporting)    │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          数据访问层（Repositories）                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ 数据源元数据   │  │ 告警历史存储   │  │ 规则配置存储   │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
             ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
             │   MySQL      │  │   Redis      │  │  InfluxDB    │
             │ (配置/告警)  │  │ (缓存/状态)  │  │ (时序指标)   │
             └──────────────┘  └──────────────┘  └──────────────┘
```

**上游依赖模块**：
- **数据同步模块**：提供原始数据的接入点和元数据
- **因子计算模块**：提供因子值数据的质量信息
- **回测引擎模块**：提供回测数据的完整性信息

**下游输出模块**：
- **因子计算模块**：在因子计算前检查数据健康度
- **实时交易模块**：在执行交易前验证数据可靠性
- **风控模块**：根据数据质量调整风控参数

### 1.3 核心业务目标

1. **完整性检查**：确保交易日、股票、字段数据不缺失
2. **合理性校验**：识别异常值、错误值、逻辑矛盾
3. **时效性监控**：确保数据更新及时，不延迟
4. **一致性验证**：确保不同数据源间数据一致
5. **可追溯定位**：快速定位问题数据的来源和原因

---

## 二、业务需求深度理解

### 2.1 什么是数据完整性

数据完整性是指数据集中**没有缺失值**，具体包括：

#### 2.1.1 交易日完整性

交易日数据应该包含所有A股正常的交易日（节假日休市除外）。

**检查方法**：
1. 预定义交易日历（可从交易所日历获取）
2. 对比实际数据中的日期
3. 标记缺失的交易日

**缺失原因**：
- 数据源更新延迟
- 网络故障
- 数据源本身缺失

#### 2.1.2 股票完整性

每个交易日应该包含所有目标股票池的股票数据。

**检查方法**：
1. 定义目标股票池（沪深300、中证500等）
2. 对比实际数据中的股票列表
3. 标记缺失的股票

**缺失原因**：
- 股票停牌
- 新股未上市
- 数据源问题

#### 2.1.3 字段完整性

每条记录应该包含所有必要字段。

**检查方法**：
1. 定义必要字段列表（open、high、low、close、volume等）
2. 检查每条记录的字段完整性
3. 标记缺失字段的记录

### 2.2 什么是数据合理性

数据合理性是指数据值在**合理范围内**，不包含异常值。

#### 2.2.1 数值范围检查

| 字段 | 正常范围 | 异常示例 |
|-----|---------|---------|
| 涨跌幅 | -10% ~ 10% | 15% |
| 成交量 | > 0 | 0 或负数 |
| 市盈率 | 0 ~ 1000 | -50 |
| 市净率 | 0 ~ 100 | 负数 |

#### 2.2.2 逻辑一致性检查

- 开盘价 ≤ 最高价
- 收盘价 ≥ 最低价
- 涨跌幅 = (收盘价 - 昨收价) / 昨收价
- 成交量 ≥ 成交额 / 成交均价

#### 2.2.3 统计异常检测

使用统计学方法检测异常值：

**3σ原则**：
- 数据落在μ±3σ之外的概率约0.3%
- 这些点很可能是异常值

**IQR方法**：
- IQR = Q3 - Q1
- 异常值：< Q1 - 1.5×IQR 或 > Q3 + 1.5×IQR

### 2.3 什么是数据时效性

数据时效性是指数据**更新的及时程度**。

#### 2.3.1 数据更新延迟监控

| 数据类型 | 期望更新时间 | 告警阈值 |
|---------|------------|---------|
| 实时行情 | 秒级 | 延迟>30秒 |
| 日线行情 | 收盘后15分钟 | 延迟>30分钟 |
| 分钟线 | 实时 | 延迟>5分钟 |
| 财务数据 | 财报发布后2小时 | 延迟>24小时 |

#### 2.3.2 数据新鲜度检查

对于实时数据流，检查当前数据时间戳与系统时间的差值：

```
新鲜度 = 当前系统时间 - 最新数据时间戳
```

如果新鲜度超过阈值，触发告警。

#### 2.3.3 数据更新频率监控

```python
class DataFreshnessMonitor:
    """数据新鲜度监控器"""
    
    def __init__(self, expected_intervals: Dict[str, int]):
        """
        参数：
        - expected_intervals: {数据源: 期望更新间隔(秒)}
        """
        self.expected_intervals = expected_intervals
        self.last_update_times = {}
    
    def check_freshness(self, source: str, current_time: datetime) -> Dict:
        """检查数据新鲜度"""
        last_update = self.last_update_times.get(source)
        if last_update is None:
            return {
                'status': 'unknown',
                'message': '暂无更新记录',
                'severity': 'info'
            }
        
        expected_interval = self.expected_intervals.get(source, 3600)
        time_diff = (current_time - last_update).total_seconds()
        
        if time_diff > expected_interval * 2:
            return {
                'status': 'critical',
                'delay_seconds': time_diff,
                'expected_interval': expected_interval,
                'message': f"数据已延迟{int(time_diff/60)}分钟，严重超时",
                'severity': 'critical'
            }
        elif time_diff > expected_interval:
            return {
                'status': 'warning',
                'delay_seconds': time_diff,
                'expected_interval': expected_interval,
                'message': f"数据已延迟{int(time_diff/60)}分钟",
                'severity': 'warning'
            }
        
        return {
            'status': 'healthy',
            'delay_seconds': time_diff,
            'message': '数据更新正常',
            'severity': 'info'
        }
```

### 2.4 什么是数据一致性

#### 2.4.1 多数据源一致性检查

比较不同数据源（如AkShare、Tushare、Wind）的同一数据：

```python
def check_source_consistency(data_source_a: pd.DataFrame, 
                            data_source_b: pd.DataFrame, 
                            tolerance: float = 0.001,
                            key_columns: List[str] = ['symbol', 'date']) -> Dict:
    """
    比较两个数据源的差异
    
    参数：
    - data_source_a: 数据源A
    - data_source_b: 数据源B
    - tolerance: 允许的差异阈值
    - key_columns: 用于匹配的关键列
    
    返回：
    - 不一致记录列表
    """
    # 合并数据
    merged = pd.merge(
        data_source_a, 
        data_source_b, 
        on=key_columns, 
        suffixes=('_a', '_b')
    )
    
    # 获取数值列（排除关键列）
    numeric_cols_a = [c for c in data_source_a.columns if c not in key_columns]
    numeric_cols_b = [c for c in data_source_b.columns if c not in key_columns]
    common_cols = set(numeric_cols_a) & set(numeric_cols_b)
    
    # 计算差异
    inconsistencies = []
    for col in common_cols:
        col_a = f"{col}_a"
        col_b = f"{col}_b"
        
        if col_a in merged.columns and col_b in merged.columns:
            diff = (merged[col_a] - merged[col_b]).abs()
            inconsistent_mask = diff > tolerance
            
            if inconsistent_mask.any():
                inconsistent_records = merged.loc[inconsistent_mask, key_columns + [col_a, col_b, col]]
                inconsistencies.append({
                    'field': col,
                    'inconsistent_count': len(inconsistent_records),
                    'max_diff': diff.max(),
                    'mean_diff': diff.mean(),
                    'records': inconsistent_records.to_dict('records')
                })
    
    # 计算总体不一致率
    total_records = len(merged)
    total_inconsistencies = sum([inc['inconsistent_count'] for inc in inconsistencies])
    inconsistency_rate = total_inconsistencies / (total_records * len(common_cols)) if total_records > 0 and common_cols else 0
    
    return {
        'total_records': total_records,
        'total_inconsistencies': total_inconsistencies,
        'inconsistency_rate': inconsistency_rate,
        'field_details': inconsistencies,
        'is_consistent': inconsistency_rate < 0.01,  # 不一致率<1%
        'recommendation': '数据源存在显著差异，需人工核查' if inconsistency_rate > 0.01 else '数据源一致性良好'
    }
```

### 2.5 高级异常检测算法

#### 2.5.1 Z-Score异常检测

基于统计分布的异常检测方法：

```python
class ZScoreAnomalyDetector:
    """Z-Score异常检测器"""
    
    def __init__(self, threshold: float = 3.0):
        """
        参数：
        - threshold: Z-score阈值，超过此值视为异常
        """
        self.threshold = threshold
    
    def fit(self, data: pd.Series):
        """使用历史数据拟合统计参数"""
        self.mean_ = data.mean()
        self.std_ = data.std()
        return self
    
    def detect(self, data: pd.Series) -> pd.Series:
        """
        检测异常值
        
        返回：
        - 布尔Series，True表示异常
        """
        if self.std_ == 0:
            return pd.Series(False, index=data.index)
        
        z_scores = (data - self.mean_) / self.std_
        return z_scores.abs() > self.threshold
    
    def get_scores(self, data: pd.Series) -> pd.Series:
        """获取Z-score值"""
        if self.std_ == 0:
            return pd.Series(0, index=data.index)
        return (data - self.mean_) / self.std_
```

#### 2.5.2 IQR异常检测

基于四分位距的鲁棒异常检测方法：

```python
class IQROutlierDetector:
    """IQR异常检测器 - 对异常值更鲁棒"""
    
    def __init__(self, k: float = 1.5):
        """
        参数：
        - k: IQR倍数，通常使用1.5（内围界）或3.0（外围界）
        """
        self.k = k
    
    def fit(self, data: pd.Series):
        """拟合IQR参数"""
        self.Q1_ = data.quantile(0.25)
        self.Q3_ = data.quantile(0.75)
        self.IQR_ = self.Q3_ - self.Q1_
        self.lower_bound_ = self.Q1_ - self.k * self.IQR_
        self.upper_bound_ = self.Q3_ + self.k * self.IQR_
        return self
    
    def detect(self, data: pd.Series) -> pd.Series:
        """检测异常值"""
        return (data < self.lower_bound_) | (data > self.upper_bound_)
    
    def get_bounds(self) -> Tuple[float, float]:
        """获取异常值边界"""
        return self.lower_bound_, self.upper_bound_
```

#### 2.5.3 DBSCAN密度聚类异常检测

基于密度的异常检测方法，适合检测多密度分布的数据：

```python
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler

class DBSCANAnomalyDetector:
    """DBSCAN密度聚类异常检测器"""
    
    def __init__(self, eps: float = 0.5, min_samples: int = 5):
        """
        参数：
        - eps: 邻域半径
        - min_samples: 核心点的最小邻居数
        """
        self.eps = eps
        self.min_samples = min_samples
        self.scaler = StandardScaler()
    
    def fit_detect(self, data: np.ndarray) -> np.ndarray:
        """
        拟合并检测异常
        
        返回：
        - 1表示正常，-1表示异常（噪声点）
        """
        # 标准化
        data_scaled = self.scaler.fit_transform(data)
        
        # DBSCAN聚类
        dbscan = DBSCAN(eps=self.eps, min_samples=self.min_samples)
        labels = dbscan.fit_predict(data_scaled)
        
        # 噪声点（标签为-1）即为异常
        return labels
    
    def detect_dataframe(self, df: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        """检测DataFrame中的异常"""
        features = df[feature_columns].values
        labels = self.fit_detect(features)
        return pd.Series(labels == -1, index=df.index)
```

#### 2.5.4 Isolation Forest异常检测

基于随机森林的异常检测方法，适合高维数据：

```python
from sklearn.ensemble import IsolationForest

class IsolationForestDetector:
    """Isolation Forest异常检测器"""
    
    def __init__(self, contamination: float = 0.01, random_state: int = 42):
        """
        参数：
        - contamination: 预期异常比例
        - random_state: 随机种子
        """
        self.contamination = contamination
        self.random_state = random_state
        self.model = IsolationForest(
            contamination=contamination,
            random_state=random_state,
            n_estimators=100
        )
    
    def fit_detect(self, data: np.ndarray) -> np.ndarray:
        """
        拟合并检测异常
        
        返回：
        - 1表示正常，-1表示异常
        """
        self.model.fit(data)
        return self.model.predict(data)
    
    def detect_dataframe(self, df: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        """检测DataFrame中的异常"""
        features = df[feature_columns].values
        labels = self.fit_detect(features)
        return pd.Series(labels == -1, index=df.index)
    
    def get_anomaly_scores(self, data: np.ndarray) -> np.ndarray:
        """获取异常分数（越负越异常）"""
        return self.model.score_samples(data)
```

#### 2.5.5 Local Outlier Factor (LOF)异常检测

基于局部密度的异常检测方法，适合检测局部异常：

```python
from sklearn.neighbors import LocalOutlierFactor

class LOFAnomalyDetector:
    """Local Outlier Factor异常检测器"""
    
    def __init__(self, n_neighbors: int = 20, contamination: float = 0.01):
        """
        参数：
        - n_neighbors: 近邻数量
        - contamination: 预期异常比例
        """
        self.n_neighbors = n_neighbors
        self.contamination = contamination
        self.model = LocalOutlierFactor(
            n_neighbors=n_neighbors,
            contamination=contamination,
            novelty=False
        )
    
    def fit_detect(self, data: np.ndarray) -> np.ndarray:
        """
        拟合并检测异常
        
        返回：
        - 1表示正常，-1表示异常
        """
        return self.model.fit_predict(data)
    
    def get_lof_scores(self, data: np.ndarray) -> np.ndarray:
        """获取LOF分数（越大越异常）"""
        return -self.model.negative_outlier_factor_
```

### 2.6 数据质量评分算法

#### 2.6.1 综合质量评分

```python
class DataQualityScorer:
    """数据质量评分器"""
    
    def __init__(self, 
                 completeness_weight: float = 0.3,
                 validity_weight: float = 0.3,
                 timeliness_weight: float = 0.2,
                 consistency_weight: float = 0.2):
        """
        参数：
        - 各维度权重
        """
        self.weights = {
            'completeness': completeness_weight,
            'validity': validity_weight,
            'timeliness': timeliness_weight,
            'consistency': consistency_weight
        }
        
        # 验证权重和为1
        assert abs(sum(self.weights.values()) - 1.0) < 0.001, "权重之和必须为1"
    
    def calculate_completeness_score(
        self, 
        total_expected: int, 
        total_actual: int,
        field_missing_rate: float = 0.0
    ) -> float:
        """计算完整性得分"""
        if total_expected == 0:
            return 100.0
        
        record_completeness = (total_actual / total_expected) * 100
        field_completeness = (1 - field_missing_rate) * 100
        
        return (record_completeness * 0.7 + field_completeness * 0.3)
    
    def calculate_validity_score(
        self, 
        total_records: int, 
        invalid_records: int
    ) -> float:
        """计算合理性得分"""
        if total_records == 0:
            return 100.0
        
        validity_rate = (total_records - invalid_records) / total_records
        return validity_rate * 100
    
    def calculate_timeliness_score(
        self, 
        delay_seconds: int, 
        expected_interval: int,
        acceptable_delay_ratio: float = 1.5
    ) -> float:
        """计算时效性得分"""
        if expected_interval == 0:
            return 100.0
        
        delay_ratio = delay_seconds / expected_interval
        
        if delay_ratio <= 1.0:
            return 100.0
        elif delay_ratio <= acceptable_delay_ratio:
            # 线性下降
            return 100 - (delay_ratio - 1.0) / (acceptable_delay_ratio - 1.0) * 50
        else:
            # 快速下降
            return max(0, 50 - (delay_ratio - acceptable_delay_ratio) * 25)
    
    def calculate_consistency_score(
        self,
        total_records: int,
        inconsistent_records: int
    ) -> float:
        """计算一致性得分"""
        if total_records == 0:
            return 100.0
        
        consistency_rate = 1 - (inconsistent_records / total_records)
        return consistency_rate * 100
    
    def calculate_overall_score(
        self,
        completeness_score: float,
        validity_score: float,
        timeliness_score: float,
        consistency_score: float
    ) -> float:
        """计算综合得分"""
        return (
            completeness_score * self.weights['completeness'] +
            validity_score * self.weights['validity'] +
            timeliness_score * self.weights['timeliness'] +
            consistency_score * self.weights['consistency']
        )
```

### 2.7 告警收敛与去重机制

#### 2.7.1 告警收敛策略

```python
class AlertConvergenceManager:
    """告警收敛管理器"""
    
    def __init__(self, cooldown_seconds: int = 300):
        """
        参数：
        - cooldown_seconds: 告警冷却时间
        """
        self.cooldown_seconds = cooldown_seconds
        self.alert_history = {}  # {alert_key: last_trigger_time}
        self.alert_counts = {}    # {alert_key: count}
    
    def get_alert_key(self, alert: Alert) -> str:
        """生成告警唯一标识"""
        return f"{alert.rule_id}:{alert.data_source}:{alert.target_field}"
    
    def should_trigger(self, alert: Alert) -> bool:
        """判断是否触发告警"""
        key = self.get_alert_key(alert)
        now = datetime.now()
        
        if key not in self.alert_history:
            return True
        
        last_trigger = self.alert_history[key]
        time_diff = (now - last_trigger).total_seconds()
        
        if time_diff < self.cooldown_seconds:
            # 在冷却期内，不触发告警，增加计数
            self.alert_counts[key] = self.alert_counts.get(key, 0) + 1
            return False
        
        return True
    
    def record_alert(self, alert: Alert):
        """记录告警触发"""
        key = self.get_alert_key(alert)
        self.alert_history[key] = datetime.now()
        self.alert_counts[key] = self.alert_counts.get(key, 0) + 1
```

#### 2.7.2 告警分组与聚合

```python
class AlertAggregator:
    """告警聚合器 - 将相似告警聚合"""
    
    def __init__(self, time_window_seconds: int = 300):
        """
        参数：
        - time_window_seconds: 聚合时间窗口
        """
        self.time_window = time_window_seconds
    
    def aggregate_alerts(self, alerts: List[Alert]) -> List[AggregatedAlert]:
        """
        聚合相似告警
        
        聚合规则：
        1. 相同规则ID和字段的告警
        2. 在时间窗口内的告警
        """
        # 按规则ID分组
        grouped = {}
        for alert in alerts:
            key = (alert.rule_id, alert.data_source, alert.target_field)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(alert)
        
        # 在每个组内按时间聚合
        aggregated = []
        for (rule_id, source, field), group_alerts in grouped.items():
            # 按时间排序
            sorted_alerts = sorted(group_alerts, key=lambda x: x.created_at)
            
            # 分成多个聚合窗口
            windows = self._split_into_windows(sorted_alerts)
            
            for window_alerts in windows:
                aggregated.append(AggregatedAlert(
                    rule_id=rule_id,
                    data_source=source,
                    target_field=field,
                    alerts=window_alerts,
                    total_count=len(window_alerts),
                    first_detected=window_alerts[0].created_at,
                    last_detected=window_alerts[-1].created_at,
                    severity=self._get_aggregated_severity(window_alerts)
                ))
        
        return aggregated
    
    def _split_into_windows(self, alerts: List[Alert]) -> List[List[Alert]]:
        """将告警分割成时间窗口"""
        if not alerts:
            return []
        
        windows = []
        current_window = [alerts[0]]
        window_start = alerts[0].created_at
        
        for alert in alerts[1:]:
            if (alert.created_at - window_start).total_seconds() < self.time_window:
                current_window.append(alert)
            else:
                windows.append(current_window)
                current_window = [alert]
                window_start = alert.created_at
        
        if current_window:
            windows.append(current_window)
        
        return windows
    
    def _get_aggregated_severity(self, alerts: List[Alert]) -> str:
        """获取聚合后的告警级别"""
        severity_order = {'critical': 3, 'warning': 2, 'info': 1}
        max_severity = max(alerts, key=lambda x: severity_order.get(x.severity, 0))
        return max_severity.severity
```

### 2.8 异常根因分析

#### 2.8.1 基于规则的根因分析

```python
class RootCauseAnalyzer:
    """异常根因分析器"""
    
    def __init__(self):
        self.rules = [
            {
                'pattern': 'missing_records',
                'possible_causes': [
                    '数据源更新延迟',
                    '网络连接问题',
                    '数据源服务器故障',
                    'API调用频率限制'
                ],
                'check_steps': [
                    '检查数据源状态页',
                    '检查网络连接',
                    '检查API配额'
                ]
            },
            {
                'pattern': 'invalid_price',
                'possible_causes': [
                    '股票停牌期间数据错误',
                    '复权参数错误',
                    '数据源计算错误',
                    '极端行情导致数据异常'
                ],
                'check_steps': [
                    '验证股票是否停牌',
                    '检查复权因子',
                    '对比其他数据源'
                ]
            },
            {
                'pattern': 'timeliness_delay',
                'possible_causes': [
                    '数据源更新延迟',
                    'ETL任务失败',
                    '数据库性能问题',
                    '网络延迟'
                ],
                'check_steps': [
                    '检查数据源更新时间',
                    '检查ETL日志',
                    '检查数据库性能'
                ]
            }
        ]
    
    def analyze(self, anomaly: Anomaly) -> Dict:
        """
        分析异常根因
        
        返回：
        - 可能原因列表
        - 检查建议
        - 影响评估
        """
        anomaly_type = anomaly.anomaly_type
        
        # 匹配规则
        matched_rules = [r for r in self.rules if r['pattern'] in anomaly_type]
        
        if not matched_rules:
            return {
                'possible_causes': ['未知原因'],
                'check_steps': ['需要人工排查'],
                'impact': '待评估',
                'suggestion': '请联系数据团队进行人工分析'
            }
        
        rule = matched_rules[0]
        
        return {
            'possible_causes': rule['possible_causes'],
            'check_steps': rule['check_steps'],
            'impact': self._assess_impact(anomaly),
            'suggestion': self._generate_suggestion(anomaly, rule)
        }
    
    def _assess_impact(self, anomaly: Anomaly) -> str:
        """评估影响"""
        if anomaly.severity == 'critical':
            return '高影响：可能影响交易决策和风控'
        elif anomaly.severity == 'warning':
            return '中影响：可能影响数据质量分析'
        else:
            return '低影响：仅影响数据完整性统计'
    
    def _generate_suggestion(self, anomaly: Anomaly, rule: Dict) -> str:
        """生成处理建议"""
        if anomaly.severity == 'critical':
            return f"建议立即处理。优先检查：{rule['check_steps'][0]}"
        elif anomaly.severity == 'warning':
            return f"建议及时处理。检查步骤：{', '.join(rule['check_steps'][:2])}"
        else:
            return f"建议记录跟踪。可选检查：{rule['check_steps'][0]}"
```

---

数据一致性是指**不同数据源**或**同一数据源不同时间**的数据保持一致。

#### 2.4.1 多数据源一致性

比较不同数据源（如AkShare、Tushare、Wind）的同一数据：

```python
def check_source_consistency(data_source_a, data_source_b, tolerance=0.001):
    """
    比较两个数据源的差异
    tolerance: 允许的差异阈值
    """
    diff = abs(data_source_a - data_source_b)
    inconsistent_count = (diff > tolerance).sum()
    return inconsistent_count / len(diff) < 0.01  # 不一致比例<1%
```

#### 2.4.2 历史版本一致性

检查同一数据源的历史版本是否一致，防止回滚错误。

---

## 三、设计思考过程

### 3.1 需求分析与拆解

#### 第一步：识别核心用户故事

作为量化团队的运维人员，我希望：
1. 每天早上开盘前自动检查昨晚的数据是否完整更新
2. 当数据出现异常时立即收到告警通知
3. 查看历史告警记录，分析数据问题的规律
4. 配置告警规则，调整告警敏感度

作为量化研究员，我希望：
1. 在使用数据前确认数据质量可靠
2. 查看数据质量报告，评估数据是否可用

#### 第二步：拆解功能模块

| 功能模块 | 优先级 | 核心价值 |
|---------|-------|---------|
| 健康检查API | P0 | 提供数据健康度查询 |
| 完整性检查 | P0 | 发现缺失数据 |
| 合理性校验 | P0 | 发现异常数据 |
| 时效性监控 | P1 | 发现延迟数据 |
| 告警通知 | P1 | 及时通知问题 |
| 告警历史 | P2 | 问题追溯分析 |
| 规则配置 | P2 | 灵活配置阈值 |
| 报告生成 | P3 | 定期汇总报告 |

#### 第三步：确定技术约束

1. **实时性要求**：
   - 完整性检查：每日收盘后1小时内完成
   - 合理性校验：实时检测
   - 时效性监控：每5分钟检查一次

2. **数据量**：
   - 日线数据：4000+股票 × 250交易日 = 100万条/天
   - 分钟线：4000+股票 × 240分钟 = 96万条/天

3. **告警延迟**：
   - 严重问题：5分钟内告警
   - 一般问题：30分钟内告警

### 3.2 架构设计思考

#### 3.2.1 为什么选择这种分层架构

```
数据源 → 采集层 → 检查层 → 存储层 → 通知层 → 用户
```

**分层的好处**：
1. **解耦**：每层独立，可以灵活替换
2. **可扩展**：新增数据源只需修改采集层
3. **可复用**：检查逻辑可被多个数据源复用

#### 3.2.2 为什么使用时序数据库（InfluxDB）

监控系统需要存储大量时间序列数据：

| 特性 | MySQL | InfluxDB |
|-----|-------|----------|
| 写入性能 | 1万/s | 10万/s+ |
| 存储压缩 | 无 | 10:1 |
| 时间查询 | 需索引 | 原生支持 |
| 聚合查询 | 慢 | 快 |

#### 3.2.3 为什么需要规则引擎

数据质量规则复杂多样：

```python
# 简单规则
rule = {
    "field": "close",
    "condition": "between",
    "params": [0, 10000]
}

# 复杂规则
rule = {
    "type": "composite",
    "operator": "and",
    "rules": [
        {"field": "close", "condition": ">", "value": 0},
        {"field": "close", "condition": "<", "value": 10000},
        {"field": "high", "condition": ">=", "value": "close"},
        {"field": "low", "condition": "<=", "value": "close"}
    ]
}
```

规则引擎支持：
- 动态加载规则
- 热更新规则
- 规则组合

### 3.3 核心算法设计思考

#### 3.3.1 异常检测算法

**静态阈值法**：
```python
def static_threshold_check(value, min_val, max_val):
    if value < min_val or value > max_val:
        return {"is_anomaly": True, "reason": f"超出阈值范围[{min_val}, {max_val}]"}
    return {"is_anomaly": False}
```

**统计法（3σ原则）**：
```python
def statistical_check(values, threshold=3):
    mean = np.mean(values)
    std = np.std(values)
    anomaly_mask = np.abs(values - mean) > threshold * std
    return anomaly_mask
```

**IQR法**：
```python
def iqr_check(values, k=1.5):
    Q1 = np.percentile(values, 25)
    Q3 = np.percentile(values, 75)
    IQR = Q3 - Q1
    lower_bound = Q1 - k * IQR
    upper_bound = Q3 + k * IQR
    anomaly_mask = (values < lower_bound) | (values > upper_bound)
    return anomaly_mask
```

**机器学习方法（Isolation Forest）**：
```python
from sklearn.ensemble import IsolationForest

def isolation_forest_check(values, contamination=0.01):
    model = IsolationForest(contamination=contamination)
    predictions = model.fit_predict(values.reshape(-1, 1))
    return predictions == -1  # -1表示异常
```

#### 3.3.2 完整性检查算法

```python
def check_completeness(df, required_dates, required_stocks, required_fields):
    """
    检查数据完整性
    
    Returns:
        missing_dates: 缺失的交易日
        missing_stocks: 缺失的股票
        missing_fields: 缺失的字段
    """
    # 检查交易日
    existing_dates = set(df.index.get_level_values('date'))
    missing_dates = set(required_dates) - existing_dates
    
    # 检查股票
    existing_stocks = set(df.index.get_level_values('stock'))
    missing_stocks = set(required_stocks) - existing_stocks
    
    # 检查字段
    existing_fields = set(df.columns)
    missing_fields = set(required_fields) - existing_fields
    
    return {
        "missing_dates": list(missing_dates),
        "missing_stocks": list(missing_stocks),
        "missing_fields": list(missing_fields),
        "completeness_score": calculate_completeness_score(...)
    }
```

#### 3.3.3 时效性检查算法

```python
def check_timeliness(last_update_time, expected_interval, max_delay):
    """
    检查数据时效性
    
    Args:
        last_update_time: 最后更新时间
        expected_interval: 期望更新间隔
        max_delay: 最大允许延迟
    """
    now = datetime.now()
    time_diff = (now - last_update_time).total_seconds()
    
    if time_diff > max_delay:
        return {
            "is_delayed": True,
            "delay_seconds": time_diff,
            "severity": "critical" if time_diff > max_delay * 2 else "warning"
        }
    return {"is_delayed": False}
```

---

## 四、详细技术方案

### 4.1 数据库表设计

#### 4.1.1 数据源元数据表

```sql
CREATE TABLE data_source_metadata (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_name VARCHAR(50) NOT NULL COMMENT '数据源名称',
    data_type VARCHAR(50) NOT NULL COMMENT '数据类型（如：daily_price, minute_price, factor）',
    table_name VARCHAR(100) NOT NULL COMMENT '对应的数据库表名',
    update_frequency VARCHAR(20) COMMENT '更新频率（realtime/hourly/daily）',
    last_update_time DATETIME COMMENT '最后更新时间',
    last_check_time DATETIME COMMENT '最后检查时间',
    health_status ENUM('healthy', 'warning', 'critical') DEFAULT 'healthy',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_type (source_name, data_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源元数据表';
```

#### 4.1.2 告警规则配置表

```sql
CREATE TABLE alert_rules (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_name VARCHAR(100) NOT NULL COMMENT '规则名称',
    rule_type ENUM('completeness', 'timeliness', 'validity', 'consistency') NOT NULL COMMENT '规则类型',
    data_source VARCHAR(50) NOT NULL COMMENT '数据源',
    target_field VARCHAR(50) COMMENT '目标字段',
    condition_type ENUM('threshold', 'range', 'iqr', 'custom') NOT NULL COMMENT '条件类型',
    condition_params JSON NOT NULL COMMENT '条件参数',
    severity ENUM('critical', 'warning', 'info') NOT NULL COMMENT '告警级别',
    enabled TINYINT DEFAULT 1 COMMENT '是否启用',
    notification_channels JSON COMMENT '通知渠道',
    cooldown_seconds INT DEFAULT 300 COMMENT '告警冷却时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rule_type (rule_type),
    INDEX idx_data_source (data_source),
    INDEX idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='告警规则配置表';
```

#### 4.1.3 告警历史表

```sql
CREATE TABLE alert_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    alert_uuid VARCHAR(36) NOT NULL COMMENT '告警唯一标识',
    rule_id BIGINT NOT NULL COMMENT '触发规则ID',
    rule_name VARCHAR(100) NOT NULL,
    data_source VARCHAR(50) NOT NULL,
    target_field VARCHAR(50),
    alert_type ENUM('completeness', 'timeliness', 'validity', 'consistency') NOT NULL,
    severity ENUM('critical', 'warning', 'info') NOT NULL,
    alert_message TEXT NOT NULL COMMENT '告警详情',
    alert_details JSON COMMENT '告警详细信息',
    anomaly_count INT DEFAULT 1 COMMENT '异常数量',
    first_detected_at DATETIME NOT NULL COMMENT '首次检测时间',
    last_detected_at DATETIME NOT NULL COMMENT '最后检测时间',
    resolved_at DATETIME COMMENT '解决时间',
    status ENUM('active', 'acknowledged', 'resolved', 'ignored') DEFAULT 'active',
    acknowledged_by VARCHAR(50) COMMENT '确认人',
    resolution_note TEXT COMMENT '解决备注',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_alert_uuid (alert_uuid),
    INDEX idx_status (status),
    INDEX idx_severity (severity),
    INDEX idx_first_detected (first_detected_at),
    INDEX idx_rule (rule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='告警历史表';
```

#### 4.1.4 数据质量报告表

```sql
CREATE TABLE data_quality_reports (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    report_date DATE NOT NULL COMMENT '报告日期',
    data_source VARCHAR(50) NOT NULL COMMENT '数据源',
    completeness_score DECIMAL(5,2) COMMENT '完整性得分',
    validity_score DECIMAL(5,2) COMMENT '合理性得分',
    timeliness_score DECIMAL(5,2) COMMENT '时效性得分',
    overall_score DECIMAL(5,2) COMMENT '综合得分',
    total_records INT COMMENT '总记录数',
    missing_records INT COMMENT '缺失记录数',
    anomaly_records INT COMMENT '异常记录数',
    delayed_updates INT COMMENT '延迟更新次数',
    report_details JSON COMMENT '详细报告',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_source (report_date, data_source),
    INDEX idx_report_date (report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据质量报告表';
```

#### 4.1.5 InfluxDB时序数据设计

```influxdb
# 测量（Measurement）：data_quality_metrics
# 标签（Tags）：source, data_type, severity, field
# 字段（Fields）：value, count, score

# 数据点示例
data_quality_metrics,source=akshare,data_type=daily_price,severity=info,field=close completeness_score=99.5,missing_count=12,anomaly_count=0 1704067200000000000

data_quality_metrics,source=tushare,data_type=factor,severity=warning,field=PE timeliness_delay=1800,update_status=delayed 1704067500000000000
```

### 4.2 API接口设计

#### 4.2.1 数据健康度查询接口

**接口路径**：`GET /api/v1/data-quality/health`

**响应结果**：
```json
{
    "code": 200,
    "message": "success",
    "data": {
        "overall_health": "healthy",
        "score": 98.5,
        "last_check_time": "2024-01-10T09:15:00",
        "sources": [
            {
                "name": "akshare_daily_price",
                "status": "healthy",
                "score": 99.2,
                "last_update": "2024-01-10T07:15:00",
                "completeness": 99.8,
                "validity": 98.5,
                "timeliness": 99.5
            },
            {
                "name": "tushare_factor",
                "status": "warning",
                "score": 92.1,
                "last_update": "2024-01-10T06:30:00",
                "completeness": 95.0,
                "validity": 99.0,
                "timeliness": 82.0
            }
        ],
        "trend": {
            "7d_avg_score": 97.8,
            "30d_avg_score": 96.5,
            "trend": "stable"
        }
    }
}
```

#### 4.2.2 告警列表接口

**接口路径**：`GET /api/v1/data-quality/alerts`

**请求参数**：
```json
{
    "start_date": "2024-01-01",
    "end_date": "2024-01-10",
    "status": "active",
    "severity": "critical",
    "data_source": "akshare_daily_price",
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
        "total": 15,
        "page": 1,
        "page_size": 20,
        "alerts": [
            {
                "id": 1001,
                "uuid": "alert-uuid-123",
                "rule_name": "daily_price_completeness",
                "data_source": "akshare_daily_price",
                "severity": "critical",
                "message": "2024-01-10 数据不完整，缺失 125 条记录",
                "first_detected": "2024-01-10T07:30:00",
                "status": "active",
                "anomaly_count": 125
            }
        ]
    }
}
```

#### 4.2.3 告警规则配置接口

**接口路径**：`POST /api/v1/data-quality/rules`

**请求参数**：
```json
{
    "rule_name": "price_range_check",
    "rule_type": "validity",
    "data_source": "akshare_daily_price",
    "target_field": "close",
    "condition_type": "range",
    "condition_params": {
        "min": 0,
        "max": 10000
    },
    "severity": "warning",
    "notification_channels": ["webhook", "dingtalk"],
    "cooldown_seconds": 600
}
```

#### 4.2.4 异常详情接口

**接口路径**：`GET /api/v1/data-quality/anomalies/{alert_id}`

**响应结果**：
```json
{
    "code": 200,
    "message": "success",
    "data": {
        "alert_id": 1001,
        "anomaly_type": "missing_records",
        "details": {
            "missing_dates": ["2024-01-10"],
            "missing_stocks": ["600001", "600002", "600003"],
            "missing_fields": ["turnover_rate"]
        },
        "sample_records": [
            {
                "symbol": "600001",
                "date": "2024-01-10",
                "expected_fields": ["open", "high", "low", "close", "volume"],
                "actual_fields": ["open", "high", "low", "close"],
                "missing_fields": ["volume"]
            }
        ],
        "impact_analysis": {
            "affected_strategies": 3,
            "potential_loss": "high"
        },
        "suggested_actions": [
            "联系数据供应商确认数据更新状态",
            "检查网络连接和API调用是否正常"
        ]
    }
}
```

### 4.3 服务层设计

#### 4.3.1 健康检查服务

```python
class HealthCheckService:
    """数据健康检查服务"""
    
    def __init__(self, source_repo, check_service, metrics_repo):
        self.source_repo = source_repo
        self.check_service = check_service
        self.metrics_repo = metrics_repo
    
    async def check_all_sources(self) -> HealthCheckResult:
        """
        检查所有数据源的健康度
        
        设计思考：
        1. 并行检查所有数据源，提高效率
        2. 分别检查完整性、合理性、时效性
        3. 综合计算健康得分
        """
        sources = await self.source_repo.get_all_sources()
        
        # 并行检查
        tasks = [self._check_single_source(s) for s in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 汇总结果
        overall_score = np.mean([r['score'] for r in results if not isinstance(r, Exception)])
        
        return HealthCheckResult(
            overall_score=overall_score,
            source_results=results,
            check_time=datetime.now()
        )
    
    async def _check_single_source(self, source) -> SourceHealthResult:
        """检查单个数据源"""
        # 检查完整性
        completeness = await self.check_service.check_completeness(source)
        
        # 检查合理性
        validity = await self.check_service.check_validity(source)
        
        # 检查时效性
        timeliness = await self.check_service.check_timeliness(source)
        
        # 计算得分
        score = self._calculate_score(completeness, validity, timeliness)
        
        # 记录指标
        await self.metrics_repo.record_health_metrics(source, score, completeness, validity, timeliness)
        
        return SourceHealthResult(
            name=source.name,
            score=score,
            completeness=completeness,
            validity=validity,
            timeliness=timeliness,
            status=self._determine_status(score)
        )
```

#### 4.3.2 异常检测服务

```python
class AnomalyDetectionService:
    """异常检测服务"""
    
    def __init__(self, rule_repo, alert_service):
        self.rule_repo = rule_repo
        self.alert_service = alert_service
    
    async def detect_anomalies(self, data_source: str, data: pd.DataFrame) -> List[Anomaly]:
        """
        检测数据异常
        
        设计思考：
        1. 获取该数据源的所有规则
        2. 对每条规则进行检测
        3. 收集异常记录
        4. 根据规则触发告警
        """
        rules = await self.rule_repo.get_active_rules(data_source)
        anomalies = []
        
        for rule in rules:
            rule_anomalies = await self._check_rule(rule, data)
            anomalies.extend(rule_anomalies)
        
        # 按规则分组触发告警
        await self._trigger_alerts(data_source, anomalies, rules)
        
        return anomalies
    
    async def _check_rule(self, rule: AlertRule, data: pd.DataFrame) -> List[Anomaly]:
        """检查单个规则"""
        anomalies = []
        
        if rule.condition_type == 'threshold':
            # 阈值检查
            field_data = data[rule.target_field]
            anomaly_mask = self._threshold_check(field_data, rule.condition_params)
            
        elif rule.condition_type == 'range':
            # 范围检查
            field_data = data[rule.target_field]
            anomaly_mask = self._range_check(field_data, rule.condition_params)
            
        elif rule.condition_type == 'iqr':
            # IQR检查
            field_data = data[rule.target_field]
            anomaly_mask = self._iqr_check(field_data, rule.condition_params)
        
        # 收集异常记录
        for idx in data.index[anomaly_mask]:
            anomalies.append(Anomaly(
                rule_id=rule.id,
                rule_name=rule.rule_name,
                data_source=rule.data_source,
                target_field=rule.target_field,
                index=idx,
                value=data.loc[idx, rule.target_field],
                expected_range=self._get_expected_range(rule.condition_params),
                severity=rule.severity
            ))
        
        return anomalies
    
    def _range_check(self, values: pd.Series, params: dict) -> np.ndarray:
        """范围检查"""
        min_val = params.get('min', 0)
        max_val = params.get('max', float('inf'))
        return (values < min_val) | (values > max_val)
    
    def _iqr_check(self, values: pd.Series, params: dict) -> np.ndarray:
        """IQR异常检测"""
        k = params.get('k', 1.5)
        Q1 = values.quantile(0.25)
        Q3 = values.quantile(0.75)
        IQR = Q3 - Q1
        lower = Q1 - k * IQR
        upper = Q3 + k * IQR
        return (values < lower) | (values > upper)
```

#### 4.3.3 告警通知服务

```python
class AlertNotificationService:
    """告警通知服务"""
    
    def __init__(self, alert_repo, websocket_manager, dingtalk_client, email_client):
        self.alert_repo = alert_repo
        self.ws_manager = websocket_manager
        self.dingtalk = dingtalk_client
        self.email = email_client
    
    async def send_alert(self, alert: Alert, notification_channels: List[str]):
        """
        发送告警通知
        
        设计思考：
        1. 根据规则配置决定通知渠道
        2. 不同渠道使用不同的消息格式
        3. 避免重复告警（冷却期机制）
        4. 记录告警发送历史
        """
        # 检查是否在冷却期
        if await self._is_in_cooldown(alert):
            return
        
        # 发送各渠道通知
        tasks = []
        
        if 'websocket' in notification_channels:
            tasks.append(self._send_websocket(alert))
        
        if 'dingtalk' in notification_channels:
            tasks.append(self._send_dingtalk(alert))
        
        if 'email' in notification_channels:
            tasks.append(self._send_email(alert))
        
        await asyncio.gather(*tasks)
        
        # 记录告警
        await self.alert_repo.create_alert(alert)
    
    async def _send_websocket(self, alert: Alert):
        """WebSocket推送"""
        await self.ws_manager.broadcast({
            'type': 'alert',
            'alert_id': alert.id,
            'severity': alert.severity,
            'message': alert.message,
            'timestamp': alert.created_at.isoformat()
        })
    
    async def _send_dingtalk(self, alert: Alert):
        """钉钉通知"""
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【{alert.severity.upper()}】数据质量告警",
                "text": f"""
## 数据质量告警

**告警级别**: {alert.severity}
**告警时间**: {alert.created_at}
**数据源**: {alert.data_source}
**告警内容**: {alert.message}

---
                """
            }
        }
        await self.dingtalk.send_message(message)
```

### 4.4 定时任务设计

#### 4.4.1 健康检查任务

```python
# tasks/scheduled_tasks.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job(trigger=CronTrigger(hour='7', minute='0'))
async def daily_health_check():
    """每日早晨7点执行全量健康检查"""
    health_service = HealthCheckService(...)
    result = await health_service.check_all_sources()
    
    # 生成报告
    await generate_daily_report(result)
    
    # 发送健康度通知
    if result.overall_score < 95:
        await alert_service.send_health_warning(result)

scheduler.start()
```

#### 4.4.2 监控任务

```python
@scheduler.scheduled_job(trigger=CronTrigger(minute='*/5'))
async def realtime_monitoring():
    """每5分钟执行实时监控"""
    monitoring_service = RealtimeMonitoringService(...)
    
    # 检查数据更新时效
    delayed_sources = await monitoring_service.check_timeliness()
    
    # 检查实时异常
    anomalies = await monitoring_service.check_realtime_anomalies()
    
    # 触发告警
    if delayed_sources:
        await alert_service.send_timeliness_alert(delayed_sources)
```

---

## 五、验证方法与测试方案

### 5.1 功能测试用例

#### 5.1.1 完整性检查验证

**测试场景**：
- 数据源：日线行情数据
- 时间：2024-01-02（交易日）

**验证步骤**：
1. 准备测试数据：删除部分记录
2. 执行完整性检查
3. 验证缺失记录被正确识别

**测试数据构造**：
```python
# 正常数据
test_data = pd.DataFrame({
    'symbol': ['600001'] * 10,
    'date': pd.date_range('2024-01-02', periods=10, freq='D'),
    'close': np.random.uniform(10, 20, 10)
})

# 构造缺失：删除2024-01-05的数据
test_data = test_data[test_data['date'] != '2024-01-05']
```

**预期结果**：
- 检测到缺失日期：2024-01-05
- 完整性得分 = 9/10 = 90%

#### 5.1.2 合理性检查验证

**测试场景**：
- 检查收盘价在合理范围内（0-10000）
- 检查涨跌幅在-10%到10%之间

**验证步骤**：
1. 准备测试数据：包含正常值和异常值
2. 执行合理性检查
3. 验证异常值被正确识别

**测试数据构造**：
```python
test_data = pd.DataFrame({
    'symbol': ['600001'] * 5,
    'close': [10.5, -5.0, 20.3, 100000, 15.2],  # 包含异常值
    'pct_change': [0.5, 2.5, -1.2, 15.0, 0.3]  # 包含异常值
})
```

**预期结果**：
- 检测到close异常：-5.0（小于0）
- 检测到close异常：100000（超出范围）
- 检测到pct_change异常：15.0（超出±10%）

#### 5.1.3 时效性检查验证

**测试场景**：
- 检查日线数据是否及时更新

**验证步骤**：
1. 设置最后更新时间
2. 执行时效性检查
3. 验证延迟被正确识别

**测试数据构造**：
```python
# 最后更新时间是1小时前（正常）
normal_source = DataSource(
    name='daily_price',
    last_update=datetime.now() - timedelta(hours=1)
)

# 最后更新时间是2小时前（延迟）
delayed_source = DataSource(
    name='daily_price',
    last_update=datetime.now() - timedelta(hours=2)
)
```

**预期结果**：
- 正常数据源：状态=healthy
- 延迟数据源：状态=warning，延迟=7200秒

### 5.2 性能测试用例

#### 5.2.1 大数据量检查性能

**测试条件**：
- 数据量：100万条记录
- 字段数：10个
- 规则数：5个

**性能指标**：
- 检查时间 < 60秒
- 内存占用 < 4GB

**测试代码**：
```python
import time
import memory_profiler

def test_check_performance():
    # 生成100万条测试数据
    test_data = generate_test_data(rows=1_000_000, cols=10)
    
    start_time = time.time()
    start_memory = memory_profiler.memory_usage()[0]
    
    service = AnomalyDetectionService(...)
    anomalies = await service.detect_anomalies('test_source', test_data)
    
    end_time = time.time()
    end_memory = memory_profiler.memory_usage()[0]
    
    assert end_time - start_time < 60  # < 60秒
    assert (end_memory - start_memory) / 1024 < 4096  # < 4GB
```

### 5.3 边界测试用例

| 测试场景 | 输入 | 预期行为 |
|---------|------|---------|
| 空数据集 | 空DataFrame | 返回完整度0%，不报错 |
| 全是缺失值 | 全部为NaN | 识别为缺失，触发告警 |
| 全是异常值 | 全部超出范围 | 识别为异常，触发告警 |
| 无匹配规则 | 数据源无规则 | 跳过检查，返回正常 |
| 规则参数错误 | 无效的JSON参数 | 返回配置错误 |

### 5.4 集成测试

**测试场景**：完整告警流程

**测试步骤**：
1. 构造异常数据
2. 触发异常检测
3. 验证告警创建
4. 验证WebSocket推送
5. 验证数据库记录

**测试代码**：
```python
@pytest.mark.asyncio
async def test_full_alert_flow():
    # 1. 构造异常数据
    test_data = create_test_data_with_anomalies()
    
    # 2. 触发检测
    anomalies = await anomaly_service.detect_anomalies('test_source', test_data)
    
    # 3. 验证告警
    assert len(anomalies) > 0
    
    # 4. 验证WebSocket
    ws_message = await ws_manager.get_last_message()
    assert ws_message['type'] == 'alert'
    
    # 5. 验证数据库
    alerts = await alert_repo.get_active_alerts()
    assert len(alerts) > 0
```

---

## 六、部署与运维

### 6.1 部署架构

```
                              ┌─────────────────┐
                              │   Grafana       │
                              │  (监控大屏)     │
                              └────────┬────────┘
                                       │
               ┌────────────────────────┼────────────────────────┐
               │                        │                        │
               ▼                        ▼                        ▼
       ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
       │   InfluxDB    │      │  API Server   │      │   Grafana     │
       │  (时序数据)   │◄────►│  (监控服务)   │      │  (可视化)     │
       └───────────────┘      └───────┬───────┘      └───────────────┘
                                      │
                                      ▼
                             ┌─────────────────┐
                             │   Scheduler     │
                             │  (定时任务)     │
                             └────────┬────────┘
                                      │
               ┌──────────────────────┼──────────────────────┐
               │                      │                      │
               ▼                      ▼                      ▼
       ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
       │   MySQL       │      │   Redis       │      │  WebSocket    │
       │  (配置/告警)  │      │  (缓存/状态)  │      │  (实时推送)   │
       └───────────────┘      └───────────────┘      └───────────────┘
```

### 6.2 监控指标

| 指标 | 阈值 | 告警方式 |
|-----|------|---------|
| 数据完整性得分 | < 95% | 钉钉通知 |
| 数据合理性得分 | < 90% | 钉钉通知 |
| 数据时效性得分 | < 80% | 钉钉通知 |
| 告警处理时间 | > 30分钟 | 邮件通知 |
| 数据库连接数 | > 80% | 邮件通知 |

### 6.3 Grafana仪表盘配置

```json
{
  "dashboard": {
    "title": "数据质量监控",
    "panels": [
      {
        "title": "综合健康度",
        "type": "gauge",
        "targets": [
          {
            "query": "SELECT mean(overall_score) FROM data_quality_metrics WHERE $timeFilter"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "min": 0,
            "max": 100,
            "thresholds": {
              "mode": "absolute",
              "steps": [
                {"value": 0, "color": "red"},
                {"value": 80, "color": "yellow"},
                {"value": 95, "color": "green"}
              ]
            }
          }
        }
      },
      {
        "title": "告警趋势",
        "type": "graph",
        "targets": [
          {
            "query": "SELECT count(*) FROM alert_history WHERE $timeFilter GROUP BY time(1h)"
          }
        ]
      }
    ]
  }
}
```

---

## 附录：相关三方库和API

### 一、时序数据库

| 名称 | 版本 | 用途 | 安装/启动 |
|-----|------|------|----------|
| InfluxDB | v2.x | 时序指标存储 | `docker run -d -p 8086:8086 influxdb:2.7` |
| TimescaleDB | v15 | 时序+关系型 | `docker run -d -p 5432:5432 timescale/timescaledb` |
| Prometheus | v2.x | 指标采集存储 | `docker run -d -p 9090:9090 prom/prometheus` |

### 二、监控与可视化

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| Grafana | v10.x | 监控大屏 | `docker run -d -p 3000:3000 grafana/grafana` |
| Telegraf | v1.27 | 指标采集 | `pip install telegraf` |

### 三、告警通知库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| dingtalk | v2.x | 钉钉通知 | `pip install dingtalk` |
| aiosmtplib | v2.x | 异步邮件 | `pip install aiosmtplib` |
| httpx | v0.25 | HTTP客户端 | `pip install httpx` |

### 四、异常检测库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| scikit-learn | >=1.3 | Isolation Forest | `pip install scikit-learn` |
| PyOD | >=0.11 | 异常检测 | `pip install pyod` |
| statsmodels | >=0.14 | 统计检验 | `pip install statsmodels` |

### 五、定时任务库

| 名称 | 版本 | 用途 | 安装命令 |
|-----|------|------|---------|
| APScheduler | v3.10 | 定时任务 | `pip install apscheduler` |
| Celery | v5.3 | 异步任务 | `pip install celery` |

### 六、代码示例

**使用InfluxDB记录指标**

```python
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

client = InfluxDBClient(url="http://localhost:8086", token="my-token", org="my-org")
write_api = client.write_api(write_options=SYNCHRONOUS)

point = (
    Point("data_quality_metrics")
    .tag("source", "akshare")
    .tag("data_type", "daily_price")
    .field("completeness_score", 99.5)
    .field("missing_count", 12)
    .time(time.now())
)
write_api.write(bucket="metrics", org="my-org", record=point)
```

**使用Grafana API创建仪表盘**

```python
import requests

GRAFANA_URL = "http://localhost:3000"
API_KEY = "your-api-key"

dashboard = {
    "dashboard": {
        "title": "数据质量监控",
        "panels": [{
            "title": "综合健康度",
            "type": "gauge",
            "targets": [{
                "expr": "avg(data_quality_metrics{job=\"dq_monitor\"})"
            }]
        }]
    },
    "overwrite": True
}

response = requests.post(
    f"{GRAFANA_URL}/api/dashboards/db",
    json=dashboard,
    headers={"Authorization": f"Bearer {API_KEY}"}
)
```

**使用APScheduler定时任务**

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job(trigger=CronTrigger(hour='7', minute='0'))
async def daily_health_check():
    """每日健康检查"""
    health_service = HealthCheckService()
    result = await health_service.check_all_sources()
    await generate_report(result)

scheduler.start()
```

**使用PyOD进行异常检测**

```python
from pyod.models.iforest import IsolationForest
import numpy as np

def detect_anomalies(data, contamination=0.01):
    """使用Isolation Forest检测异常值"""
    model = IsolationForest(contamination=contamination, random_state=42)
    predictions = model.fit_predict(data.values.reshape(-1, 1))
    return predictions == -1  # True表示异常
```

### 七、资源链接

| 资源类型 | 链接 | 说明 |
|---------|------|------|
| InfluxDB文档 | https://docs.influxdata.com/influxdb/v2/ | 时序数据库指南 |
| Grafana文档 | https://grafana.com/docs/grafana/ | 监控可视化指南 |
| APScheduler文档 | https://apscheduler.readthedocs.io/ | 定时任务指南 |
| PyOD文档 | https://pyod.readthedocs.io/ | 异常检测库 |
| 钉钉开放平台 | https://open.dingtalk.com/ | 钉钉通知接入 |
