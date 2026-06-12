"""Tests for the autouse no-live-LLM guard in tests/conftest.py."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


def test_offline_guard_strips_all_live_provider_env_vars(tmp_path: Path) -> None:
    """The autouse conftest guard must strip every env var that lets get_client()
    reach a live provider.

    get_client() PREFERS OPENROUTER_API_KEY over ANTHROPIC_API_KEY, and ORC_PROVIDER
    can force either path — stripping only ANTHROPIC_API_KEY leaves "offline" tests
    able to silently hit the real API. The guard is autouse (it applies to this test
    too), so we observe it from outside: run a probe test in a subprocess with the
    real conftest copied beside it and the live-provider vars exported.
    """
    conftest_src = Path(__file__).resolve().parents[1] / "conftest.py"
    shutil.copy(conftest_src, tmp_path / "conftest.py")
    (tmp_path / "test_probe.py").write_text(
        textwrap.dedent(
            """\
            import os


            def test_live_provider_env_is_absent() -> None:
                assert os.environ.get("OPENROUTER_API_KEY") is None
                assert os.environ.get("ANTHROPIC_API_KEY") is None
                assert os.environ.get("ORC_PROVIDER") is None
            """
        )
    )
    env = {k: v for k, v in os.environ.items() if k != "ORC_TEST_ALLOW_LIVE_LLM"}
    env.update(
        OPENROUTER_API_KEY="sk-or-fake-for-test",
        ANTHROPIC_API_KEY="sk-ant-fake-for-test",
        ORC_PROVIDER="openrouter",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
