from src.notifier.heartbeat import HeartbeatReport, format_heartbeat


def test_empty_heartbeat():
    report = HeartbeatReport(
        seen_24h=0,
        scored_24h=0,
        alerts_24h=0,
        scams_24h=0,
        by_source=[],
        scraper_health=[],
        claude_cost_eur_24h=0.0,
        top_reject_reasons=[],
        score_buckets=[],
        near_misses=[],
    )
    msg = format_heartbeat(report)
    assert "Rien n'a encore tourné" in msg


def test_heartbeat_with_content():
    report = HeartbeatReport(
        seen_24h=42,
        scored_24h=20,
        alerts_24h=3,
        scams_24h=1,
        by_source=[("pap", 28), ("seloger", 14)],
        scraper_health=[("pap", 0, 28), ("seloger", 1, 14), ("leboncoin", 5, 0)],
        claude_cost_eur_24h=0.87,
        top_reject_reasons=[],
        score_buckets=[],
        near_misses=[],
    )
    msg = format_heartbeat(report)
    assert "42" in msg
    assert "20" in msg
    assert "3" in msg
    assert "0.87" in msg
    assert "🟢" in msg  # healthy scrapers
    assert "🔴" in msg  # leboncoin down (5 failures)
    assert "PAP 28" in msg


def test_heartbeat_scraper_health_emoji_boundaries():
    report = HeartbeatReport(
        seen_24h=10,
        scored_24h=5,
        alerts_24h=0,
        scams_24h=0,
        by_source=[("pap", 10)],
        scraper_health=[
            ("pap", 0, 10),  # green
            ("seloger", 2, 0),  # amber
            ("leboncoin", 10, 0),  # red
        ],
        claude_cost_eur_24h=0.02,
        top_reject_reasons=[],
        score_buckets=[],
        near_misses=[],
    )
    msg = format_heartbeat(report)
    assert "🟢" in msg
    assert "🟡" in msg
    assert "🔴" in msg
