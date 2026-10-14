from src.config import load_criteria


def test_load_criteria(criteria_yaml):
    c = load_criteria(criteria_yaml)
    assert c.property.max_rent_cc == 2000
    assert "T3" in c.property.types.preferred
    assert c.property.min_surface["T3"] == 45
    assert c.scoring.urgent_threshold == 85
    codes = {z.code for z in c.zones.include}
    assert codes == {"75011", "75003"}
    assert "meuble" in c.excludes


def test_default_criteria_when_file_missing(tmp_path):
    c = load_criteria(tmp_path / "nope.yaml")
    assert c.property.max_rent_cc == 2000
    assert c.scoring.alert_threshold == 70
