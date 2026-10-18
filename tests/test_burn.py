"""Tests for api_key_burn_watch. Live-provider paths use fixtures or mocks."""

import argparse
import json
import os
import statistics

import pytest

import api_key_burn_watch as bw

FIXTURES = "tests/fixtures"


def _ns(**kw):
    base = dict(dry_run=True, fixtures=FIXTURES,
                baseline_file="/nonexistent-baseline.json", webhook="")
    base.update(kw)
    return argparse.Namespace(**base)


def test_baseline_math_on_fixture(tmp_path):
    daily = bw.load_fixture(FIXTURES, "openai")
    spends = [d["spend_usd"] for d in daily]
    assert len(spends) == 14
    median = statistics.median(spends)
    assert median == pytest.approx(0.485, abs=0.001)
    # 3x median threshold must NOT fire on a normal day
    assert 0.5 < bw.SPIKE_MULTIPLE * median
    # but WOULD fire on the $281 OpenCode-style surprise charge
    assert 281.0 > bw.SPIKE_MULTIPLE * median


def test_baseline_dry_run_writes_file(tmp_path):
    out = tmp_path / "baseline.json"
    rc = bw.cmd_baseline(_ns(baseline_file=str(out)))
    assert rc == 0
    data = json.loads(out.read_text())
    assert "openai" in data["providers"]
    pdata = data["providers"]["openai"]
    assert pdata["days"] == 14
    assert pdata["median_daily_spend_usd"] == pytest.approx(0.485, abs=0.001)


def _write_baseline_and_today(tmp_path, spend_usd, hour):
    """Build a baseline file + a today.json fixture, return the check rc."""
    out = tmp_path / "baseline.json"
    assert bw.cmd_baseline(_ns(baseline_file=str(out))) == 0
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "openai_14d.json").write_text(
        (bw.pathlib.Path(FIXTURES) / "openai_14d.json").read_text())
    (fx / "today.json").write_text(
        json.dumps({"openai": {"spend_usd": spend_usd, "hour": hour}}))
    return bw.cmd_check(_ns(baseline_file=str(out), fixtures=str(fx)))


def test_check_spike_triggers_alert(tmp_path, capsys):
    rc = _write_baseline_and_today(tmp_path, spend_usd=281.0, hour=14)
    assert rc == 2  # 2 == alert
    out = capsys.readouterr().out
    assert "SPEND ALERT" in out
    assert "exceeds 3.0x" in out


def test_check_clean_day_no_alert(tmp_path, capsys):
    rc = _write_baseline_and_today(tmp_path, spend_usd=0.5, hour=14)
    assert rc == 0
    assert "No anomalies detected." in capsys.readouterr().out


def test_check_offhours_usage_alerts(tmp_path, capsys):
    rc = _write_baseline_and_today(tmp_path, spend_usd=3.5, hour=3)
    assert rc == 2
    assert "unusual hours" in capsys.readouterr().out


def test_check_zero_baseline_any_spend_alerts(tmp_path, capsys):
    out = tmp_path / "baseline.json"
    out.write_text(json.dumps({
        "generated_at": "2026-10-18T00:00:00+00:00",
        "providers": {"openai": {"days": 14, "median_daily_spend_usd": 0.0,
                                 "max_daily_spend_usd": 0.0, "daily": []}},
        "unavailable": {},
    }))
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "today.json").write_text(
        json.dumps({"openai": {"spend_usd": 168.0, "hour": 14}}))
    rc = bw.cmd_check(_ns(baseline_file=str(out), fixtures=str(fx)))
    assert rc == 2
    assert "SPEND ALERT" in capsys.readouterr().out


def test_check_without_baseline_errors(tmp_path, capsys):
    rc = bw.cmd_check(_ns(baseline_file=str(tmp_path / "missing.json")))
    assert rc == 3
    assert "run `baseline` first" in capsys.readouterr().err


def test_provider_http_error_is_recorded_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def boom(url, headers, timeout=20):
        raise bw.ProviderError("HTTP 401: Unauthorized")

    monkeypatch.setattr(bw, "_http_get_json", boom)
    out = tmp_path / "baseline.json"
    rc = bw.cmd_baseline(_ns(dry_run=False, baseline_file=str(out)))
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["providers"] == {}
    assert "openai" in data["unavailable"]
    assert "401" in data["unavailable"]["openai"]


def test_unsupported_providers_recorded_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaFakeForTest")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = tmp_path / "baseline.json"
    rc = bw.cmd_baseline(_ns(dry_run=False, baseline_file=str(out)))
    assert rc == 0
    data = json.loads(out.read_text())
    assert "no public usage/billing API" in data["unavailable"]["anthropic"]
    assert "no public billing endpoint" in data["unavailable"]["google"]


def test_no_keys_present_graceful_error(tmp_path, monkeypatch, capsys):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    rc = bw.cmd_baseline(_ns(dry_run=False,
                             baseline_file=str(tmp_path / "b.json")))
    assert rc == 3
    assert "no provider API keys" in capsys.readouterr().err


def test_openai_fetch_parses_costs_shape(monkeypatch):
    payload = {
        "data": [
            {"start_time": 1760745600,
             "results": [{"amount": {"value": 0.42, "currency": "usd"}}]},
            {"start_time": 1760832000,
             "results": [{"amount": {"value": 0.51, "currency": "usd"}}]},
        ]
    }
    monkeypatch.setattr(bw, "_http_get_json", lambda url, headers, timeout=20: payload)
    daily = bw.fetch_openai_daily_spend("sk-fake", days=2)
    assert [d["spend_usd"] for d in daily] == [0.42, 0.51]
    assert daily[0]["date"] == "2025-10-18"  # 1760745600 UTC


def test_openai_fetch_rejects_non_usd(monkeypatch):
    payload = {"data": [{"start_time": 1760745600,
                         "results": [{"amount": {"value": 5, "currency": "eur"}}]}]}
    monkeypatch.setattr(bw, "_http_get_json", lambda url, headers, timeout=20: payload)
    with pytest.raises(bw.ProviderError, match="non-USD"):
        bw.fetch_openai_daily_spend("sk-fake", days=1)


def test_openai_fetch_rejects_bad_shape(monkeypatch):
    monkeypatch.setattr(bw, "_http_get_json",
                        lambda url, headers, timeout=20: {"nope": True})
    with pytest.raises(bw.ProviderError, match="unexpected response shape"):
        bw.fetch_openai_daily_spend("sk-fake", days=1)
