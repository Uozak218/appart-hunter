from src.utils.urls import is_safe_photo_url


def test_accepts_leboncoin_cdn():
    assert is_safe_photo_url("https://img.leboncoin.fr/api/v1/lbcpb1/images/1.jpg")


def test_accepts_seloger_cdn():
    assert is_safe_photo_url("https://v.seloger.com/s/crop/590x330/visuels/2/0/f/0/2/1.jpg")


def test_rejects_localhost():
    assert not is_safe_photo_url("http://localhost:5432/photo.jpg", resolve_dns=False)


def test_rejects_aws_metadata():
    assert not is_safe_photo_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_private_ip_literal():
    assert not is_safe_photo_url("http://10.0.0.1/photo.jpg", resolve_dns=False)


def test_rejects_unknown_domain():
    assert not is_safe_photo_url("https://attacker.com/photo.jpg", resolve_dns=False)


def test_rejects_non_https_scheme():
    assert not is_safe_photo_url("ftp://img.leboncoin.fr/photo.jpg", resolve_dns=False)


def test_rejects_empty():
    assert not is_safe_photo_url("")
    assert not is_safe_photo_url(None)  # type: ignore[arg-type]
