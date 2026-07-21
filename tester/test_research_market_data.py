import numpy as np
import pandas as pd

from research_platform.market_data import load_research_ohlcv, sanitize_research_ohlcv


def test_sanitize_preserves_missing_dates_and_normalizes_timezone():
    frame = pd.DataFrame(
        {
            "Open": [10.0, 11.0],
            "High": [11.0, 12.0],
            "Low": [9.0, 10.0],
            "Close": [10.5, 11.5],
            "Volume": [100.0, 120.0],
        },
        index=pd.to_datetime(
            ["2024-01-02 20:00:00-05:00", "2024-01-04 20:00:00-05:00"],
            utc=True,
        ),
    )

    result = sanitize_research_ohlcv(frame)

    assert result.index.equals(pd.DatetimeIndex(["2024-01-03", "2024-01-05"]))
    assert pd.Timestamp("2024-01-04") not in result.index


def test_sanitize_marks_invalid_prices_and_volume_missing():
    frame = pd.DataFrame(
        {
            "Open": [10.0, 10.0, 10.0],
            "High": [11.0, 9.0, 11.0],
            "Low": [9.0, 8.0, 12.0],
            "Close": [10.5, 10.5, 10.5],
            "Volume": [100.0, 0.0, -1.0],
        },
        index=pd.date_range("2024-01-01", periods=3),
    )

    result = sanitize_research_ohlcv(frame)

    assert result.loc["2024-01-01", ["Open", "High", "Low", "Close"]].notna().all()
    assert result.loc["2024-01-02", ["Open", "High", "Low", "Close"]].isna().all()
    assert result.loc["2024-01-03", ["Open", "High", "Low", "Close"]].isna().all()
    assert result.loc["2024-01-02":, "Volume"].isna().all()


def test_appending_future_rows_does_not_change_sanitized_history():
    base = pd.DataFrame(
        {
            "Open": [10.0, 11.0],
            "High": [11.0, 12.0],
            "Low": [9.0, 10.0],
            "Close": [10.5, 11.5],
            "Volume": [100.0, 120.0],
        },
        index=pd.date_range("2024-01-01", periods=2),
    )
    future = pd.DataFrame(
        {
            "Open": [1_000.0],
            "High": [1_100.0],
            "Low": [900.0],
            "Close": [1_050.0],
            "Volume": [1e9],
        },
        index=[pd.Timestamp("2030-01-01")],
    )

    expected = sanitize_research_ohlcv(base)
    actual = sanitize_research_ohlcv(pd.concat([base, future])).loc[expected.index]

    pd.testing.assert_frame_equal(actual, expected)


def test_loader_reads_requested_files_and_records_metadata(tmp_path):
    frame = pd.DataFrame(
        {
            "Date": ["2024-01-01", "2024-01-02"],
            "Open": [10.0, 11.0],
            "High": [11.0, 12.0],
            "Low": [9.0, 10.0],
            "Close": [10.5, 11.5],
            "Volume": [100.0, np.inf],
        }
    )
    frame.to_csv(tmp_path / "AAA_20years.csv", index=False)

    bundle = load_research_ohlcv(tmp_path, ["AAA", "MISSING"])

    assert tuple(bundle.frames) == ("AAA",)
    assert bundle.frames["AAA"].loc["2024-01-02", "Volume"] != np.inf
    assert bundle.metadata["requested_tickers"] == 2
    assert bundle.metadata["loaded_tickers"] == 1
    assert bundle.metadata["adjustment_semantics"] == "source prices used as stored"
