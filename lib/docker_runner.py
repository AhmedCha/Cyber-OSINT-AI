import logging
import subprocess
from typing import List, Optional, Union

logger = logging.getLogger(__name__)


def run_docker_service_up(
    service_name: str, timeout: int = 60, compose_file: Optional[str] = None
) -> bool:
    """Starts a specific Docker Compose service in detached mode (-d)."""
    cmd = ["docker", "compose"]
    if compose_file:
        cmd.extend(["-f", compose_file])
    cmd.extend(["up", "-d", service_name])

    try:
        logger.debug(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        )
        logger.debug(
            f"Service '{service_name}' started successfully. Output: {result.stdout.strip()}"
        )
        return True
    except subprocess.TimeoutExpired:
        logger.error(
            f"Timeout ({timeout}s) expired while starting service '{service_name}'."
        )
        return False
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to start service '{service_name}'. Exit code: {e.returncode}. Stderr: {e.stderr.strip()}"
        )
        return False
    except Exception as e:
        logger.error(
            f"Unexpected error running docker compose up for '{service_name}': {e}"
        )
        return False


def run_docker_tool(
    tool_name: str,
    extra_args: Optional[List[str]] = None,
    timeout: int = 300,
    target_identifier: Optional[str] = None,
    compose_file: Optional[str] = None,
    capture_stdout: bool = False,
) -> Union[List[str], str]:
    """Executes a short-lived tool inside a Docker Compose container via `docker compose run`.

    If capture_stdout is True, returns raw string output. Otherwise returns stdout split into non-empty lines.
    """
    cmd = ["docker", "compose"]
    if compose_file:
        cmd.extend(["-f", compose_file])
    cmd.extend(["run", "--rm"])
    if extra_args:
        cmd.extend(extra_args)
    else:
        cmd.append(tool_name)

    target_log = f" for '{target_identifier}'" if target_identifier else ""

    try:
        logger.debug(
            f"Executing docker tool '{tool_name}'{target_log}: {' '.join(cmd)}"
        )
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        )
        logger.debug(f"Tool '{tool_name}' completed successfully{target_log}.")

        if capture_stdout:
            return result.stdout.strip()

        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    except subprocess.TimeoutExpired:
        logger.error(
            f"Timeout ({timeout}s) expired while running tool '{tool_name}'{target_log}."
        )
        return "" if capture_stdout else []
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Tool '{tool_name}' failed{target_log}. Exit code: {e.returncode}. Stderr: {e.stderr.strip()}"
        )
        return "" if capture_stdout else []
    except Exception as e:
        logger.error(f"Unexpected error running tool '{tool_name}'{target_log}: {e}")
        return "" if capture_stdout else []
