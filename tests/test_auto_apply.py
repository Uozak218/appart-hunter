from src.apply.auto_apply import compose_body, evaluate


def _eval(**over):
    base = {
        "enabled": True,
        "score": 80,
        "min_score": 75,
        "already_applied": False,
        "has_email": True,
        "applied_today": 0,
        "daily_cap": 20,
    }
    base.update(over)
    return evaluate(**base)


def test_go_when_all_conditions_met():
    assert _eval().go is True


def test_skip_when_disabled():
    assert _eval(enabled=False).go is False


def test_skip_when_score_below_min():
    d = _eval(score=74, min_score=75)
    assert d.go is False
    assert "74" in d.reason


def test_score_exactly_at_threshold_passes():
    assert _eval(score=75, min_score=75).go is True


def test_skip_when_already_applied():
    d = _eval(already_applied=True)
    assert d.go is False
    assert "applied" in d.reason.lower()


def test_skip_when_no_email():
    d = _eval(has_email=False)
    assert d.go is False
    assert "email" in d.reason.lower()


def test_daily_cap_is_inclusive_boundary():
    assert _eval(applied_today=20, daily_cap=20).go is False
    assert _eval(applied_today=19, daily_cap=20).go is True


def test_compose_body_appends_dossier_link():
    body = compose_body("Bonjour, je suis intéressé.", "https://dossierfacile.fr/x")
    assert "Bonjour, je suis intéressé." in body
    assert "DossierFacile" in body
    assert "https://dossierfacile.fr/x" in body


def test_compose_body_without_dossier_link_is_message_only():
    body = compose_body("Bonjour, je suis intéressé.", "")
    assert body == "Bonjour, je suis intéressé."
    assert "DossierFacile" not in body
