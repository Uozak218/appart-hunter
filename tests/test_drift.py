from src.scoring.drift import DriftReport, format_drift_alert


def test_no_alert_on_small_shift():
    report = DriftReport(
        sample_size=10,
        shifted_by_10_or_more=1,
        shifted_by_20_or_more=0,
        moved_up=1,
        moved_down=0,
        new_prompt_version="v2",
        old_prompt_versions=["v1"],
    )
    assert format_drift_alert(report, threshold_pct=20.0) is None


def test_alert_on_big_shift():
    report = DriftReport(
        sample_size=10,
        shifted_by_10_or_more=5,
        shifted_by_20_or_more=2,
        moved_up=3,
        moved_down=2,
        new_prompt_version="v2",
        old_prompt_versions=["v1"],
    )
    msg = format_drift_alert(report, threshold_pct=20.0)
    assert msg is not None
    assert "50%" in msg
    assert "v1" in msg and "v2" in msg


def test_no_alert_on_empty_sample():
    report = DriftReport(
        sample_size=0,
        shifted_by_10_or_more=0,
        shifted_by_20_or_more=0,
        moved_up=0,
        moved_down=0,
        new_prompt_version="v2",
        old_prompt_versions=[],
    )
    assert format_drift_alert(report) is None


def test_threshold_boundary_just_below():
    report = DriftReport(
        sample_size=10,
        shifted_by_10_or_more=1,  # 10% < 20% threshold
        shifted_by_20_or_more=0,
        moved_up=1,
        moved_down=0,
        new_prompt_version="v2",
        old_prompt_versions=["v1"],
    )
    assert format_drift_alert(report, threshold_pct=20.0) is None


def test_threshold_boundary_at():
    report = DriftReport(
        sample_size=10,
        shifted_by_10_or_more=2,  # exactly 20%
        shifted_by_20_or_more=1,
        moved_up=1,
        moved_down=1,
        new_prompt_version="v2",
        old_prompt_versions=["v1"],
    )
    msg = format_drift_alert(report, threshold_pct=20.0)
    assert msg is not None
