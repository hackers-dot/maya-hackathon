import pytest

from src.scanner.checks_dns import check_dmarc, check_spf


@pytest.mark.parametrize("txts,problem", [
    ([], "No SPF record"),
    (["google-site-verification=abc"], "No SPF record"),
    (["v=spf1 -all", "v=spf1 include:x.example ~all"], "2 SPF records"),
    (["v=spf1 include:mail.example +all"], "+all"),
    (["v=spf1 a mx all"], "+all"),
])
def test_spf_problems(txts, problem):
    f = check_spf("shop.example", txts, evidence_id="ev9")
    assert len(f) == 1 and f[0].check == "missing_spf" and f[0].severity == "low"
    assert problem in f[0].technical_explanation
    assert f[0].location == "TXT shop.example" and f[0].evidence[0].record_id == "ev9"
    assert f[0].demo is False


@pytest.mark.parametrize("txts", [["v=spf1 include:mail.example -all"], ['"v=spf1 " "mx ~all"'],
                                  ["other=1", "V=SPF1 -all"]])
def test_spf_ok(txts):
    assert check_spf("shop.example", txts) == []


@pytest.mark.parametrize("txts,problem", [
    ([], "No DMARC record"),
    (["v=DMARC1; rua=mailto:x"], "no valid p= policy"),
    (["v=DMARC1; p=maybe"], "no valid p= policy"),
    (["v=DMARC1; p=none", "v=DMARC1; p=reject"], "2 DMARC records"),
])
def test_dmarc_problems(txts, problem):
    f = check_dmarc("shop.example", txts)
    assert len(f) == 1 and f[0].check == "missing_dmarc"
    assert problem in f[0].technical_explanation and f[0].location == "TXT _dmarc.shop.example"


@pytest.mark.parametrize("txts", [["v=DMARC1; p=reject"], ["v=DMARC1; p=none; rua=mailto:x"],
                                  ['"v=DMARC1; " "p=quarantine"']])
def test_dmarc_ok(txts):
    assert check_dmarc("shop.example", txts) == []
