from typing import get_args

import pytest

from src.models import Severity
from src.severity import TABLE, UnknownCheck, rule, severity

REQUIRED = [
    "missing_csp", "missing_hsts", "missing_x_frame_options", "missing_x_content_type_options",
    "cookie_missing_secure", "cookie_missing_httponly", "cookie_missing_samesite", "exposed_env_file",
    "exposed_git_dir", "exposed_backup_file", "server_version_disclosure", "directory_listing",
    "weak_tls_or_expiring_cert", "missing_spf", "missing_dmarc",
]


@pytest.mark.parametrize("check_id", REQUIRED)
def test_required_checks_have_valid_rules(check_id):
    r = rule(check_id)
    assert r.severity in get_args(Severity)
    assert r.rationale and r.remediation


def test_every_table_entry_uses_a_known_severity():
    assert {r.severity for r in TABLE.values()} <= set(get_args(Severity))


def test_fixed_values():
    assert severity("exposed_env_file") == "critical"
    assert severity("missing_x_content_type_options") == "low"


@pytest.mark.parametrize("bad", ["", "made_up_check", "MISSING_CSP", None])
def test_unknown_check_raises(bad):
    with pytest.raises(UnknownCheck):
        severity(bad)
