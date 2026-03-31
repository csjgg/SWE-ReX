"""Tests for Tencent AGS (Agent Sandbox) deployment and runtime in SWE-ReX.

These tests cover configuration, factory methods, and mocked lifecycle operations.
Integration tests that require real AGS credentials are marked with @pytest.mark.slow.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from swerex.deployment.config import TencentAGSDeploymentConfig, get_deployment
from swerex.exceptions import DeploymentNotStartedError, EnvironmentExpiredError
from swerex.runtime.abstract import IsAliveResponse
from swerex.runtime.config import AGSRuntimeConfig, get_runtime


# =====================================================================
# AGSRuntimeConfig tests
# =====================================================================


class TestAGSRuntimeConfig:
    """Tests for AGSRuntimeConfig."""

    def test_default_values(self):
        config = AGSRuntimeConfig()
        assert config.type == "ags"
        assert config.auth_token == ""
        assert config.ags_token == ""
        assert config.host == "https://127.0.0.1"
        assert config.port is None
        assert config.timeout == 60.0
        assert config.skip_ssl_verify is False

    def test_custom_values(self):
        config = AGSRuntimeConfig(
            ags_token="test-token",
            host="https://8000-inst.example.com",
            timeout=30.0,
            skip_ssl_verify=True,
        )
        assert config.ags_token == "test-token"
        assert config.host == "https://8000-inst.example.com"
        assert config.timeout == 30.0
        assert config.skip_ssl_verify is True

    def test_forbids_extra_fields(self):
        with pytest.raises(Exception):
            AGSRuntimeConfig(unknown_field="value")

    def test_get_runtime_returns_ags_runtime(self):
        config = AGSRuntimeConfig(ags_token="test", host="https://example.com")
        runtime = config.get_runtime()
        from swerex.runtime.ags import AGSRuntime

        assert isinstance(runtime, AGSRuntime)

    def test_get_runtime_via_factory(self):
        config = AGSRuntimeConfig(ags_token="test", host="https://example.com")
        runtime = get_runtime(config)
        from swerex.runtime.ags import AGSRuntime

        assert isinstance(runtime, AGSRuntime)


# =====================================================================
# TencentAGSDeploymentConfig tests
# =====================================================================


class TestTencentAGSDeploymentConfig:
    """Tests for TencentAGSDeploymentConfig."""

    def test_default_values(self):
        config = TencentAGSDeploymentConfig(tool_id="sdt-test")
        assert config.type == "tencentags"
        assert config.tool_id == "sdt-test"
        assert config.image == ""
        assert config.region == "ap-chongqing"
        assert config.domain == "ap-chongqing.tencentags.com"
        assert config.http_endpoint == "ags.tencentcloudapi.com"
        assert config.port == 8000
        assert config.timeout == "1h"
        assert config.startup_timeout == 300.0
        assert config.runtime_timeout == 60.0
        assert config.skip_ssl_verify is False

    def test_custom_values(self):
        config = TencentAGSDeploymentConfig(
            tool_id="sdt-custom",
            image="swebench/sweb.eval.x86_64.django__django-16379:latest",
            region="ap-guangzhou",
            domain="ap-guangzhou.tencentags.com",
            timeout="30m",
            startup_timeout=300.0,
            secret_id="my-id",
            secret_key="my-key",
        )
        assert config.tool_id == "sdt-custom"
        assert config.image == "swebench/sweb.eval.x86_64.django__django-16379:latest"
        assert config.region == "ap-guangzhou"
        assert config.timeout == "30m"
        assert config.startup_timeout == 300.0
        assert config.secret_id == "my-id"
        assert config.secret_key == "my-key"

    def test_env_var_fallback(self):
        """Credentials should be read from environment variables if not provided."""
        with patch.dict(os.environ, {
            "TENCENTCLOUD_SECRET_ID": "env-id",
            "TENCENTCLOUD_SECRET_KEY": "env-key",
        }):
            config = TencentAGSDeploymentConfig(tool_id="sdt-test")
            assert config.secret_id == "env-id"
            assert config.secret_key == "env-key"

    def test_explicit_creds_override_env(self):
        """Explicit credentials should take precedence over environment variables."""
        with patch.dict(os.environ, {
            "TENCENTCLOUD_SECRET_ID": "env-id",
            "TENCENTCLOUD_SECRET_KEY": "env-key",
        }):
            config = TencentAGSDeploymentConfig(
                tool_id="sdt-test",
                secret_id="explicit-id",
                secret_key="explicit-key",
            )
            assert config.secret_id == "explicit-id"
            assert config.secret_key == "explicit-key"

    def test_forbids_extra_fields(self):
        with pytest.raises(Exception):
            TencentAGSDeploymentConfig(tool_id="sdt-test", unknown_field="value")

    def test_get_deployment_returns_ags_deployment(self):
        config = TencentAGSDeploymentConfig(
            tool_id="sdt-test",
            secret_id="test-id",
            secret_key="test-key",
        )
        deployment = config.get_deployment()
        from swerex.deployment.ags import TencentAGSDeployment

        assert isinstance(deployment, TencentAGSDeployment)

    def test_get_deployment_via_factory(self):
        config = TencentAGSDeploymentConfig(
            tool_id="sdt-test",
            secret_id="test-id",
            secret_key="test-key",
        )
        deployment = get_deployment(config)
        from swerex.deployment.ags import TencentAGSDeployment

        assert isinstance(deployment, TencentAGSDeployment)


# =====================================================================
# AGSRuntime tests (mocked)
# =====================================================================


class TestAGSRuntime:
    """Tests for AGSRuntime behavior (no real network calls)."""

    def test_headers_with_ags_token(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(ags_token="my-ags-token", host="https://example.com")
        assert runtime._headers == {"X-Access-Token": "my-ags-token"}

    def test_headers_with_both_tokens(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(
            ags_token="ags-token",
            auth_token="api-token",
            host="https://example.com",
        )
        headers = runtime._headers
        assert headers["X-Access-Token"] == "ags-token"
        assert headers["X-API-Key"] == "api-token"

    def test_headers_empty_tokens(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(host="https://example.com")
        assert runtime._headers == {}

    def test_ssl_param_default(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(host="https://example.com")
        assert runtime._ssl_param is None

    def test_ssl_param_skip(self):
        import ssl

        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(host="https://example.com", skip_ssl_verify=True)
        ssl_ctx = runtime._ssl_param
        assert isinstance(ssl_ctx, ssl.SSLContext)
        assert ssl_ctx.check_hostname is False

    def test_host_auto_prefix(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(host="example.com", ags_token="t")
        assert runtime._config.host.startswith("https://")

    @pytest.mark.asyncio
    async def test_token_refresh_callback(self):
        from swerex.runtime.ags import AGSRuntime

        refresh_called = False

        async def mock_refresher():
            nonlocal refresh_called
            refresh_called = True
            return "new-token"

        runtime = AGSRuntime(
            ags_token="old-token",
            host="https://example.com",
            token_refresher=mock_refresher,
        )
        await runtime._ensure_valid_token()
        assert refresh_called
        assert runtime._config.ags_token == "new-token"

    @pytest.mark.asyncio
    async def test_no_token_refresh_without_callback(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(ags_token="stable-token", host="https://example.com")
        await runtime._ensure_valid_token()
        assert runtime._config.ags_token == "stable-token"

    def test_classify_404_as_environment_expired(self):
        from swerex.runtime.ags import AGSRuntime

        runtime = AGSRuntime(host="https://example.com")
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(real_url="https://example.com/execute"),
            history=(),
            status=404,
            message="Not Found",
            headers=None,
        )

        classified = runtime._classify_request_exception(error, "https://example.com/execute")
        assert isinstance(classified, EnvironmentExpiredError)
        assert "expired or was stopped" in str(classified)


# =====================================================================
# TencentAGSDeployment tests (mocked)
# =====================================================================


class TestTencentAGSDeployment:
    """Tests for TencentAGSDeployment lifecycle (mocked SDK calls)."""

    def test_init_requires_no_start(self):
        """Deployment should be created without starting."""
        from swerex.deployment.ags import TencentAGSDeployment

        deployment = TencentAGSDeployment(
            tool_id="sdt-test",
            secret_id="test-id",
            secret_key="test-key",
        )
        assert deployment._instance_id is None
        assert deployment._runtime is None

    def test_runtime_raises_before_start(self):
        """Accessing runtime before start should raise DeploymentNotStartedError."""
        from swerex.deployment.ags import TencentAGSDeployment

        deployment = TencentAGSDeployment(
            tool_id="sdt-test",
            secret_id="test-id",
            secret_key="test-key",
        )
        with pytest.raises(DeploymentNotStartedError):
            _ = deployment.runtime

    @pytest.mark.asyncio
    async def test_start_requires_tool_id(self):
        """Start should fail if tool_id is empty."""
        from swerex.deployment.ags import TencentAGSDeployment

        deployment = TencentAGSDeployment(
            tool_id="",
            secret_id="test-id",
            secret_key="test-key",
        )
        with pytest.raises(ValueError, match="tool_id is required"):
            await deployment.start()

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        """Stop should be safe to call even if not started."""
        from swerex.deployment.ags import TencentAGSDeployment

        deployment = TencentAGSDeployment(
            tool_id="sdt-test",
            secret_id="test-id",
            secret_key="test-key",
        )
        # Should not raise
        await deployment.stop()
        assert deployment._instance_id is None
        assert deployment._runtime is None


# =====================================================================
# TokenInfo tests
# =====================================================================


class TestTokenInfo:
    """Tests for TokenInfo expiration logic."""

    def test_not_expired(self):
        from swerex.deployment.ags import TokenInfo

        token = TokenInfo(
            token="test",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            instance_id="inst-123",
        )
        assert not token.is_expired()

    def test_expired(self):
        from swerex.deployment.ags import TokenInfo

        token = TokenInfo(
            token="test",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            instance_id="inst-123",
        )
        assert token.is_expired()

    def test_about_to_expire(self):
        from swerex.deployment.ags import TokenInfo

        # Within the 60-second threshold
        token = TokenInfo(
            token="test",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            instance_id="inst-123",
        )
        assert token.is_expired()

    def test_custom_threshold(self):
        from swerex.deployment.ags import TokenInfo

        token = TokenInfo(
            token="test",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            instance_id="inst-123",
        )
        # With a smaller threshold, it should not be expired
        assert not token.is_expired(threshold_seconds=10)


# =====================================================================
# Timestamp parsing tests
# =====================================================================


class TestTimestampParsing:
    """Tests for TencentAGSDeployment._parse_timestamp."""

    def _get_deployment(self):
        from swerex.deployment.ags import TencentAGSDeployment

        return TencentAGSDeployment(
            tool_id="sdt-test",
            secret_id="id",
            secret_key="key",
        )

    def test_iso_format(self):
        d = self._get_deployment()
        result = d._parse_timestamp("2025-01-15T10:30:00+00:00")
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_iso_format_with_z(self):
        d = self._get_deployment()
        result = d._parse_timestamp("2025-01-15T10:30:00Z")
        assert result.year == 2025

    def test_unix_timestamp(self):
        d = self._get_deployment()
        result = d._parse_timestamp("1705312200")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_invalid_fallback(self):
        d = self._get_deployment()
        result = d._parse_timestamp("not-a-timestamp")
        # Should fallback to ~1 hour from now
        now = datetime.now(timezone.utc)
        assert abs((result - now).total_seconds() - 3600) < 60


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
