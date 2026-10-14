from textwrap import dedent

from src.config import load_criteria, load_profiles


def test_legacy_single_profile_yaml(criteria_yaml):
    profiles = load_profiles(criteria_yaml)
    assert len(profiles) == 1
    assert profiles[0].name == "default"
    assert profiles[0].property.max_rent_cc == 2000


def test_multi_profile_yaml(tmp_path):
    yaml = tmp_path / "criteria.yaml"
    yaml.write_text(
        dedent(
            """
            profiles:
              - name: t3_main
                property:
                  types: {preferred: [T3], acceptable: [T2]}
                  min_surface: {T3: 45, T2: 40}
                  max_rent_cc: 2000
                  furnished: false
                  floor: {no_ground_floor: true, elevator_required_above: 4}
                zones:
                  include:
                    - {code: "75011"}
                scoring:
                  alert_threshold: 70
                  urgent_threshold: 85
                  price_drop_alert: true
                  min_price_drop_pct: 3.0
              - name: t2_gem_only
                property:
                  types: {preferred: [T2], acceptable: []}
                  min_surface: {T2: 35}
                  max_rent_cc: 1500
                  furnished: false
                  floor: {no_ground_floor: true, elevator_required_above: 6}
                zones:
                  include:
                    - {code: "75003"}
                scoring:
                  alert_threshold: 90
                  urgent_threshold: 95
                  price_drop_alert: false
                  min_price_drop_pct: 5.0
            """
        )
    )
    profiles = load_profiles(yaml)
    assert [p.name for p in profiles] == ["t3_main", "t2_gem_only"]
    assert profiles[1].property.max_rent_cc == 1500
    assert profiles[1].scoring.alert_threshold == 90

    # load_criteria still returns the first profile for backward compat.
    assert load_criteria(yaml).name == "t3_main"
