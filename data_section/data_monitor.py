import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings('ignore')

class DataQualityMonitor:
    """Data quality monitoring class"""

    def __init__(self, data_dir: str = 'data', trading_calendar: List[str] = None,
                 raw_subdir: str = 'raw', cleaned_subdir: str = 'cleaned', report_subdir: str = 'reports'):
        self.data_dir = data_dir
        self.raw_subdir = raw_subdir
        self.cleaned_subdir = cleaned_subdir
        self.report_subdir = report_subdir
        self.alerts = []
        # Predefined trading calendar (simplified: weekdays excluding holidays)
        if trading_calendar is None:
            self.trading_calendar = self._generate_trading_calendar()
        else:
            self.trading_calendar = trading_calendar

    def _generate_trading_calendar(self) -> List[str]:
        """Generate a simplified trading calendar (weekdays)"""
        start_date = pd.Timestamp('2006-01-01')
        end_date = pd.Timestamp('2026-12-31')
        calendar = pd.date_range(start=start_date, end=end_date, freq='B')  # Business days
        return [d.strftime('%Y-%m-%d') for d in calendar]

    def _get_dataset_subdir(self, dataset_label: str) -> str:
        if dataset_label == 'cleaned':
            return self.cleaned_subdir
        return self.raw_subdir

    def _resolve_data_file_path(self, ticker: str, file_suffix: str, dataset_label: str) -> str:
        filename = f'{ticker}{file_suffix}.csv'
        dataset_subdir = self._get_dataset_subdir(dataset_label)
        preferred_path = os.path.join(self.data_dir, dataset_subdir, filename)
        legacy_path = os.path.join(self.data_dir, filename)
        if os.path.exists(preferred_path):
            return preferred_path
        if os.path.exists(legacy_path):
            return legacy_path
        return preferred_path

    def _resolve_report_path(self, report_name: str) -> str:
        report_dir = os.path.join(self.data_dir, self.report_subdir)
        os.makedirs(report_dir, exist_ok=True)
        return os.path.join(report_dir, report_name)

    def load_data(self, ticker: str, file_suffix: str = '_20years', dataset_label: str = 'raw') -> pd.DataFrame:
        """Load data for a single ticker"""
        file_path = self._resolve_data_file_path(ticker, file_suffix, dataset_label)
        if not os.path.exists(file_path):
            self.add_alert(f'ERROR: {dataset_label} data file not found for {ticker}', 'critical')
            return pd.DataFrame()

        try:
            df = pd.read_csv(file_path, index_col=0, parse_dates=[0])
            df.index = pd.to_datetime(df.index, utc=True).tz_convert('America/New_York')
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception as e:
            self.add_alert(f'ERROR: Failed to load {dataset_label} data for {ticker}: {str(e)}', 'critical')
            return pd.DataFrame()

    def check_completeness(self, df: pd.DataFrame, ticker: str):
        """Check data completeness"""
        if df.empty:
            return

        # Check for missing values
        missing_count = df.isnull().sum().sum()
        if missing_count > 0:
            self.add_alert(f'WARNING: {ticker} has {missing_count} missing values', 'warning')

        # Check trading day integrity against calendar
        actual_dates = set(df.index.strftime('%Y-%m-%d'))
        expected_dates = set(self.trading_calendar)
        missing_dates = expected_dates - actual_dates
        if missing_dates:
            self.add_alert(f'WARNING: {ticker} missing {len(missing_dates)} trading days', 'warning')

        # Check for large date gaps
        if len(df) > 1:
            date_diff = df.index.to_series().diff().dt.days
            gaps = date_diff[date_diff > 7]  # Assume weekends are 7 days
            if len(gaps) > 0:
                self.add_alert(f'WARNING: {ticker} has {len(gaps)} large date gaps', 'warning')

    def check_validity(self, df: pd.DataFrame, ticker: str):
        """Check data validity"""
        if df.empty:
            return

        # Check for non-positive volumes
        if 'Volume' in df.columns:
            invalid_volume = (df['Volume'] <= 0).sum()
            if invalid_volume > 0:
                self.add_alert(f'ERROR: {ticker} has {invalid_volume} non-positive volume values', 'critical')

        # Check OHLC logic: Open <= High, Close >= Low
        if all(col in df.columns for col in ['Open', 'High', 'Low', 'Close']):
            invalid_logic = ((df['Open'] > df['High']) | (df['Close'] < df['Low'])).sum()
            if invalid_logic > 0:
                self.add_alert(f'ERROR: {ticker} has {invalid_logic} records with invalid OHLC logic', 'critical')

        # Detect outliers using multiple methods
        for col in ['Close', 'Volume']:
            if col in df.columns:
                # IQR
                iqr_detector = IQROutlierDetector()
                iqr_detector.fit(df[col])
                iqr_outliers = iqr_detector.detect(df[col])
                iqr_count = iqr_outliers.sum()
                if iqr_count > 0:
                    self.add_alert(f'WARNING: {ticker} {col} has {iqr_count} IQR outliers', 'warning')

                # Z-Score
                z_detector = ZScoreAnomalyDetector()
                z_detector.fit(df[col])
                z_outliers = z_detector.detect(df[col])
                z_count = z_outliers.sum()
                if z_count > 0:
                    self.add_alert(f'WARNING: {ticker} {col} has {z_count} Z-Score outliers', 'warning')

                # DBSCAN (for demonstration, simplified)
                try:
                    db_detector = DBSCANAnomalyDetector()
                    db_outliers = db_detector.fit_detect(df[[col]].values)
                    db_count = (db_outliers == -1).sum()  # -1 indicates outliers
                    if db_count > 0:
                        self.add_alert(f'WARNING: {ticker} {col} has {db_count} DBSCAN outliers', 'warning')
                except:
                    pass  # Skip if fails

    def check_timeliness(self, df: pd.DataFrame, ticker: str):
        """Check data timeliness"""
        if df.empty:
            return

        # Check last update time
        last_update = df.index.max()
        current_time = pd.Timestamp.now(tz='America/New_York')
        time_diff = (current_time - last_update).days

        if time_diff > 1:  # Assume daily data should be updated within 1 day
            self.add_alert(f'WARNING: {ticker} data is {time_diff} days old', 'warning')

    def check_consistency(self, df1: pd.DataFrame, df2: pd.DataFrame, ticker: str, source1: str, source2: str):
        """Check consistency between two data sources"""
        if df1.empty or df2.empty:
            return

        # Simplified consistency check: compare means
        common_cols = set(df1.columns) & set(df2.columns)
        for col in common_cols:
            mean1 = df1[col].mean()
            mean2 = df2[col].mean()
            diff = abs(mean1 - mean2)
            if diff > 0.01:  # Tolerance
                self.add_alert(f'WARNING: {ticker} {col} inconsistency between {source1} and {source2}: diff {diff:.4f}', 'warning')

    def add_alert(self, message: str, severity: str):
        """Add an alert"""
        self.alerts.append({
            'timestamp': datetime.now(),
            'message': message,
            'severity': severity
        })
        print(f"[{severity.upper()}] {message}")

    def _calculate_quality_scores(self, df: pd.DataFrame, scorer: 'DataQualityScorer') -> Dict:
        """Calculate quality scores for a single dataframe"""
        completeness = scorer.calculate_completeness_score(
            total_expected=len(self.trading_calendar),
            total_actual=len(df),
            field_missing_rate=df.isnull().sum().sum() / (len(df) * len(df.columns)) if len(df) > 0 else 0
        )

        invalid_records = 0
        if 'Volume' in df.columns:
            invalid_records += (df['Volume'] <= 0).sum()
        if all(col in df.columns for col in ['Open', 'High', 'Low', 'Close']):
            invalid_records += ((df['Open'] > df['High']) | (df['Close'] < df['Low'])).sum()

        validity = scorer.calculate_validity_score(
            total_records=len(df),
            invalid_records=invalid_records
        )

        last_update = df.index.max()
        current_time = pd.Timestamp.now(tz='America/New_York')
        delay_days = (current_time - last_update).days
        timeliness = scorer.calculate_timeliness_score(delay_days=delay_days)
        consistency = 100.0  # Simplified for single ticker
        overall_score = scorer.calculate_overall_score(completeness, validity, timeliness, consistency)

        return {
            'overall': overall_score,
            'completeness': completeness,
            'validity': validity,
            'timeliness': timeliness,
            'consistency': consistency,
            'interpretation': scorer.score_interpretation(overall_score)
        }

    def monitor_all_tickers(self, tickers: List[str], check_consistency: bool = False,
                            file_suffix: str = '_20years', dataset_label: str = 'raw',
                            report_name: str = 'data_quality_report.txt', generate_report: bool = True) -> Dict:
        """Monitor all tickers and return quality scores"""
        self.alerts = []
        print(f"Starting {dataset_label} data quality monitoring...")

        dfs = {}
        quality_scores = {}
        scorer = DataQualityScorer()

        for ticker in tickers:
            print(f"\nMonitoring {ticker}...")
            df = self.load_data(ticker, file_suffix=file_suffix, dataset_label=dataset_label)
            dfs[ticker] = df
            if not df.empty:
                self.check_completeness(df, ticker)
                self.check_validity(df, ticker)
                self.check_timeliness(df, ticker)

                quality_scores[ticker] = self._calculate_quality_scores(df, scorer)
                score = quality_scores[ticker]['overall']
                interpretation = quality_scores[ticker]['interpretation']
                print(f"  Quality Score: {score:.2f} ({interpretation})")

        # Optional consistency check between tickers
        if check_consistency and len(dfs) > 1:
            print("\nChecking consistency between data sources...")
            tickers_list = list(dfs.keys())
            for i in range(len(tickers_list)):
                for j in range(i+1, len(tickers_list)):
                    ticker1, ticker2 = tickers_list[i], tickers_list[j]
                    self.check_consistency(dfs[ticker1], dfs[ticker2], f"{ticker1}_vs_{ticker2}", ticker1, ticker2)

        print(f"\nMonitoring completed. Total alerts: {len(self.alerts)}")

        # Summary report
        severity_counts = {}
        for alert in self.alerts:
            sev = alert['severity']
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        print("Alert Summary:")
        for sev, count in severity_counts.items():
            print(f"  {sev}: {count}")

        # Generate report with quality scores
        if generate_report:
            self.generate_report(
                quality_scores,
                report_name=report_name,
                title=f"Data Quality Monitoring Report ({dataset_label})"
            )

        return quality_scores

    def monitor_data_dict(
        self,
        ticker_data: Dict[str, pd.DataFrame],
        check_consistency: bool = False,
        dataset_label: str = 'raw',
        report_name: str = 'data_quality_report.txt',
        generate_report: bool = True,
    ) -> Dict:
        """
        Monitor in-memory multi-ticker data dict (no csv dependency).
        """
        self.alerts = []
        print(f"Starting {dataset_label} data quality monitoring (in-memory)...")

        dfs: Dict[str, pd.DataFrame] = {}
        quality_scores: Dict = {}
        scorer = DataQualityScorer()

        for ticker, df in ticker_data.items():
            print(f"\nMonitoring {ticker}...")
            if df is None or df.empty:
                self.add_alert(f'ERROR: {dataset_label} in-memory data is empty for {ticker}', 'critical')
                continue

            dfs[ticker] = df
            self.check_completeness(df, ticker)
            self.check_validity(df, ticker)
            self.check_timeliness(df, ticker)
            quality_scores[ticker] = self._calculate_quality_scores(df, scorer)
            score = quality_scores[ticker]['overall']
            interpretation = quality_scores[ticker]['interpretation']
            print(f"  Quality Score: {score:.2f} ({interpretation})")

        if check_consistency and len(dfs) > 1:
            print("\nChecking consistency between data sources...")
            tickers_list = list(dfs.keys())
            for i in range(len(tickers_list)):
                for j in range(i + 1, len(tickers_list)):
                    ticker1, ticker2 = tickers_list[i], tickers_list[j]
                    self.check_consistency(dfs[ticker1], dfs[ticker2], f"{ticker1}_vs_{ticker2}", ticker1, ticker2)

        print(f"\nMonitoring completed. Total alerts: {len(self.alerts)}")
        severity_counts = {}
        for alert in self.alerts:
            sev = alert['severity']
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        print("Alert Summary:")
        for sev, count in severity_counts.items():
            print(f"  {sev}: {count}")

        if generate_report:
            self.generate_report(
                quality_scores,
                report_name=report_name,
                title=f"Data Quality Monitoring Report ({dataset_label}, in-memory)",
            )

        return quality_scores

    def compare_quality_before_after(self, tickers: List[str],
                                     raw_suffix: str = '_20years',
                                     cleaned_suffix: str = '_cleaned',
                                     raw_report_name: str = 'data_quality_report_raw.txt',
                                     cleaned_report_name: str = 'data_quality_report_cleaned.txt',
                                     comparison_report_name: str = 'data_quality_comparison_report.txt') -> Tuple[Dict, Dict]:
        """Run monitor for raw and cleaned data, then generate before/after comparison report"""
        print("\nRunning quality comparison: raw vs cleaned...")
        raw_scores = self.monitor_all_tickers(
            tickers,
            check_consistency=False,
            file_suffix=raw_suffix,
            dataset_label='raw',
            report_name=raw_report_name,
            generate_report=True
        )
        cleaned_scores = self.monitor_all_tickers(
            tickers,
            check_consistency=False,
            file_suffix=cleaned_suffix,
            dataset_label='cleaned',
            report_name=cleaned_report_name,
            generate_report=True
        )
        self.generate_comparison_report(raw_scores, cleaned_scores, report_name=comparison_report_name)
        return raw_scores, cleaned_scores

    def generate_report(self, quality_scores: Dict = None, report_name: str = 'data_quality_report.txt',
                        title: str = 'Data Quality Monitoring Report'):
        """Generate a comprehensive report with quality scores"""
        report_path = self._resolve_report_path(report_name)
        with open(report_path, 'w') as f:
            f.write(f"{title}\n")
            f.write(f"Generated at: {datetime.now()}\n")
            f.write(f"Total alerts: {len(self.alerts)}\n\n")
            
            # Quality scores section
            if quality_scores:
                f.write("=" * 80 + "\n")
                f.write("DATA QUALITY SCORES\n")
                f.write("=" * 80 + "\n\n")
                
                for ticker, scores in quality_scores.items():
                    f.write(f"Ticker: {ticker}\n")
                    f.write(f"  Overall Score: {scores['overall']:.2f} ({scores['interpretation']})\n")
                    f.write(f"  Completeness: {scores['completeness']:.2f}\n")
                    f.write(f"  Validity: {scores['validity']:.2f}\n")
                    f.write(f"  Timeliness: {scores['timeliness']:.2f}\n")
                    f.write(f"  Consistency: {scores['consistency']:.2f}\n")
                    f.write("\n")
                
                f.write("=" * 80 + "\n")
                f.write("SCORE INTERPRETATION\n")
                f.write("=" * 80 + "\n")
                f.write("  Excellent: >= 90\n")
                f.write("  Good: 80-89\n")
                f.write("  Fair: 70-79\n")
                f.write("  Poor: 60-69\n")
                f.write("  Critical: < 60\n\n")
            
            f.write("=" * 80 + "\n")
            f.write("DETAILED ALERTS\n")
            f.write("=" * 80 + "\n\n")
            
            for alert in self.alerts:
                f.write(f"[{alert['severity'].upper()}] {alert['timestamp']}: {alert['message']}\n")
        print(f"Report saved to {report_path}")

    def generate_comparison_report(self, raw_scores: Dict, cleaned_scores: Dict,
                                   report_name: str = 'data_quality_comparison_report.txt'):
        """Generate before/after quality score comparison report"""
        report_path = self._resolve_report_path(report_name)
        with open(report_path, 'w') as f:
            f.write("Data Quality Before/After Cleaning Comparison Report\n")
            f.write(f"Generated at: {datetime.now()}\n")
            f.write("=" * 80 + "\n\n")

            f.write("RAW vs CLEANED SCORE COMPARISON\n")
            f.write("=" * 80 + "\n\n")

            dimensions = ['overall', 'completeness', 'validity', 'timeliness', 'consistency']
            common_tickers = sorted(set(raw_scores.keys()) | set(cleaned_scores.keys()))
            for ticker in common_tickers:
                f.write(f"Ticker: {ticker}\n")
                raw = raw_scores.get(ticker)
                cleaned = cleaned_scores.get(ticker)

                if raw is None:
                    f.write("  Raw score missing\n\n")
                    continue
                if cleaned is None:
                    f.write("  Cleaned score missing\n\n")
                    continue

                for dim in dimensions:
                    delta = cleaned[dim] - raw[dim]
                    f.write(
                        f"  {dim.title()}: {raw[dim]:.2f} -> {cleaned[dim]:.2f} "
                        f"(Delta: {delta:+.2f})\n"
                    )
                f.write(f"  Result: {raw['interpretation']} -> {cleaned['interpretation']}\n\n")

            f.write("=" * 80 + "\n")
            f.write("SCORE INTERPRETATION\n")
            f.write("=" * 80 + "\n")
            f.write("  Excellent: >= 90\n")
            f.write("  Good: 80-89\n")
            f.write("  Fair: 70-79\n")
            f.write("  Poor: 60-69\n")
            f.write("  Critical: < 60\n")
        print(f"Comparison report saved to {report_path}")

class ZScoreAnomalyDetector:
    """Z-Score anomaly detector"""

    def __init__(self, threshold: float = 3.0):
        self.threshold = threshold

    def fit(self, data: pd.Series):
        self.mean_ = data.mean()
        self.std_ = data.std()
        return self

    def detect(self, data: pd.Series) -> pd.Series:
        if self.std_ == 0:
            return pd.Series(False, index=data.index)
        z_scores = (data - self.mean_) / self.std_
        return z_scores.abs() > self.threshold

class DBSCANAnomalyDetector:
    """DBSCAN anomaly detector"""

    def __init__(self, eps: float = 0.5, min_samples: int = 5):
        self.eps = eps
        self.min_samples = min_samples
        self.scaler = StandardScaler()

    def fit_detect(self, data: np.ndarray) -> np.ndarray:
        scaled_data = self.scaler.fit_transform(data)
        db = DBSCAN(eps=self.eps, min_samples=self.min_samples)
        labels = db.fit_predict(scaled_data)
        return labels  # -1 for outliers

class IQROutlierDetector:
    """IQR outlier detector"""

    def __init__(self, k: float = 1.5):
        self.k = k

    def fit(self, data: pd.Series):
        self.Q1_ = data.quantile(0.25)
        self.Q3_ = data.quantile(0.75)
        self.IQR_ = self.Q3_ - self.Q1_
        self.lower_bound_ = self.Q1_ - self.k * self.IQR_
        self.upper_bound_ = self.Q3_ + self.k * self.IQR_
        return self

    def detect(self, data: pd.Series) -> pd.Series:
        return (data < self.lower_bound_) | (data > self.upper_bound_)

class DataQualityScorer:
    """Data quality scoring system based on Task2.6.1"""

    def __init__(self, 
                 completeness_weight: float = 0.3,
                 validity_weight: float = 0.3,
                 timeliness_weight: float = 0.2,
                 consistency_weight: float = 0.2):
        """
        Initialize scorer with dimension weights
        Weights must sum to 1.0
        """
        self.weights = {
            'completeness': completeness_weight,
            'validity': validity_weight,
            'timeliness': timeliness_weight,
            'consistency': consistency_weight
        }
        assert abs(sum(self.weights.values()) - 1.0) < 0.001, "Weights must sum to 1"

    def calculate_completeness_score(self, total_expected: int, total_actual: int, field_missing_rate: float = 0.0) -> float:
        """Calculate completeness score (0-100)"""
        if total_expected == 0:
            return 100.0
        record_completeness = (total_actual / total_expected) * 100
        field_completeness = (1 - field_missing_rate) * 100
        return (record_completeness * 0.7 + field_completeness * 0.3)

    def calculate_validity_score(self, total_records: int, invalid_records: int) -> float:
        """Calculate validity score (0-100)"""
        if total_records == 0:
            return 100.0
        validity_rate = (total_records - invalid_records) / total_records
        return validity_rate * 100

    def calculate_timeliness_score(self, delay_days: int, expected_delay_days: int = 1, acceptable_delay_ratio: float = 1.5) -> float:
        """Calculate timeliness score (0-100)"""
        if expected_delay_days == 0:
            return 100.0
        delay_ratio = delay_days / expected_delay_days
        if delay_ratio <= 1.0:
            return 100.0
        elif delay_ratio <= acceptable_delay_ratio:
            return 100 - (delay_ratio - 1.0) / (acceptable_delay_ratio - 1.0) * 50
        else:
            return max(0, 50 - (delay_ratio - acceptable_delay_ratio) * 25)

    def calculate_consistency_score(self, total_records: int, inconsistent_records: int) -> float:
        """Calculate consistency score (0-100)"""
        if total_records == 0:
            return 100.0
        consistency_rate = 1 - (inconsistent_records / total_records)
        return consistency_rate * 100

    def calculate_overall_score(self, completeness: float, validity: float, timeliness: float, consistency: float) -> float:
        """Calculate overall quality score (0-100)"""
        return (
            completeness * self.weights['completeness'] +
            validity * self.weights['validity'] +
            timeliness * self.weights['timeliness'] +
            consistency * self.weights['consistency']
        )

    def score_interpretation(self, score: float) -> str:
        """Interpret quality score"""
        if score >= 90:
            return "Excellent"
        elif score >= 80:
            return "Good"
        elif score >= 70:
            return "Fair"
        elif score >= 60:
            return "Poor"
        else:
            return "Critical"

if __name__ == "__main__":
    raw_dir = os.path.join('data', 'raw')
    csv_files = sorted([f for f in os.listdir(raw_dir) if f.endswith('_20years.csv')]) if os.path.exists(raw_dir) else []
    tickers = [f.replace('_20years.csv', '') for f in csv_files]
    if len(tickers) == 0:
        raise ValueError("No raw csv files found under data/raw. Please run data_collector first.")

    monitor = DataQualityMonitor()
    monitor.compare_quality_before_after(tickers)
