import pytest

from app.core.security import OutboundRequestBlocked, validate_outbound_url


def test_allowed_exact_domain_passes():
    validate_outbound_url("https://nominatim.openstreetmap.org/search?q=Lisbon")


def test_open_meteo_geocoding_domain_passes():
    validate_outbound_url("https://geocoding-api.open-meteo.com/v1/search?name=Lisbon")


def test_allowed_suffix_domain_passes():
    validate_outbound_url("https://en.wikivoyage.org/wiki/Lisbon")


def test_http_scheme_rejected():
    with pytest.raises(OutboundRequestBlocked):
        validate_outbound_url("http://nominatim.openstreetmap.org/search")


def test_non_allowlisted_domain_rejected():
    with pytest.raises(OutboundRequestBlocked):
        validate_outbound_url("https://evil.example.com/steal")


def test_lookalike_subdomain_attack_rejected():
    with pytest.raises(OutboundRequestBlocked):
        validate_outbound_url("https://nominatim.openstreetmap.org.attacker.com/search")


def test_raw_ip_literal_rejected():
    with pytest.raises(OutboundRequestBlocked):
        validate_outbound_url("https://93.184.216.34/search")


def test_localhost_rejected_via_private_ip_resolution():
    with pytest.raises(OutboundRequestBlocked):
        validate_outbound_url("https://localhost/search", extra_allowed_domains={"localhost"})


def test_file_scheme_rejected():
    with pytest.raises(OutboundRequestBlocked):
        validate_outbound_url("file:///etc/passwd")
