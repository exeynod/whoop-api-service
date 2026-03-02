from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.smoke
def test_docker_container_smoke_healthcheck():
    if os.getenv("RUN_DOCKER_SMOKE") != "1":
        pytest.skip("Set RUN_DOCKER_SMOKE=1 to run Docker smoke test")

    env = os.environ.copy()
    env.setdefault("WHOOP_CLIENT_ID", "client-id")
    env.setdefault("WHOOP_CLIENT_SECRET", "client-secret")
    env.setdefault("WHOOP_REDIRECT_URI", "http://127.0.0.1:8001/auth/callback")
    env.setdefault("TZ", "Europe/Moscow")

    env_path = Path(".env")
    created_env_file = False
    if not env_path.exists():
        env_path.write_text(
            "\n".join(
                [
                    "WHOOP_CLIENT_ID=client-id",
                    "WHOOP_CLIENT_SECRET=client-secret",
                    "WHOOP_REDIRECT_URI=http://127.0.0.1:8001/auth/callback",
                    "TZ=Europe/Moscow",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        created_env_file = True

    up_cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "up",
        "-d",
        "--build",
        "whoop-service",
    ]
    down_cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "down",
        "-v",
    ]

    try:
        subprocess.run(up_cmd, check=True, env=env, capture_output=True, text=True)

        deadline = time.time() + 40
        last_error = "health status is not healthy"
        while time.time() < deadline:
            try:
                status_cmd = [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Health.Status}}",
                    "whoop-service",
                ]
                result = subprocess.run(
                    status_cmd,
                    check=False,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                status_value = result.stdout.strip()
                if status_value == "healthy":
                    return
                if status_value:
                    last_error = f"health={status_value}"
            except Exception as exc:  # pragma: no cover - smoke retry path
                last_error = str(exc)
            time.sleep(1)

        pytest.fail(f"Container healthcheck failed: {last_error}")
    finally:
        subprocess.run(down_cmd, check=False, env=env, capture_output=True, text=True)
        if created_env_file:
            env_path.unlink(missing_ok=True)
