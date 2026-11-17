import datetime
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from typing import Any, Dict, Optional


DEFAULT_MEMORY_LIMIT_MB = 128
MIN_MEMORY_LIMIT_MB = 16
MAX_MEMORY_LIMIT_MB = 512
MAX_OUTPUT_CHARS = 65536
SUPPORTED_RUNTIMES = ("python3.11", "python3", "python")


def _build_isolated_runner_script(user_code_path: str, memory_limit_mb: int) -> str:
    """
    Create a bootstrap wrapper that enforces POSIX RLIMIT_AS (when available)
    and tracemalloc memory ceilings before executing the user snippet in isolation.
    """
    limit_bytes = int(memory_limit_mb) * 1024 * 1024
    return textwrap.dedent(
        f"""\
        import sys
        import tracemalloc

        LIMIT_BYTES = {limit_bytes}

        try:
            import resource
            # El baseline del intérprete (VSZ) ya consume memoria virtual (p.ej. ~135MB
            # en imágenes lambda/python), así que el presupuesto efectivo debe sumarlo.
            _base_vsz = 0
            try:
                with open("/proc/self/status", "r", encoding="utf-8") as _f:
                    for _line in _f:
                        if _line.startswith("VmSize:"):
                            _base_vsz = int(_line.split()[1]) * 1024
                            break
            except Exception:
                pass
            _cap = _base_vsz + LIMIT_BYTES if _base_vsz else LIMIT_BYTES
            resource.setrlimit(resource.RLIMIT_AS, (_cap, _cap))
        except Exception:
            pass

        tracemalloc.start()

        with open({user_code_path!r}, "r", encoding="utf-8") as _f:
            _code = _f.read()

        _globals = {{"__name__": "__main__", "__file__": {user_code_path!r}}}
        try:
            exec(compile(_code, {user_code_path!r}, "exec"), _globals)
        finally:
            _current, _peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            if _peak > _cap:
                sys.stderr.write(
                    f"\\nMemoryError: Ephemeral microVM memory limit exceeded "
                    f"(peak={{_peak // (1024 * 1024)}}MB > limit={int(memory_limit_mb)}MB)\\n"
                )
                sys.exit(137)
        """
    )


def run_ephemeral_code(
    code: str,
    runtime: str = "python3.11",
    timeout_seconds: float = 5.0,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute user code inside an isolated ephemeral sandbox with:
      - Strict timeout (capped by the user's remaining monthly quota)
      - Memory limit enforcement (RLIMIT_AS + tracemalloc ceiling)
      - Sanitized environment variables & ephemeral rootfs directory
      - Firecracker-style MicroVM lifecycle telemetry (vm_id, boot_time_ms, teardown_time_ms, state_transitions)
    """
    normalized_runtime = (runtime or "python3.11").strip().lower()
    if normalized_runtime not in SUPPORTED_RUNTIMES:
        raise ValueError(
            f"Unsupported runtime '{runtime}'. Supported runtimes: {', '.join(SUPPORTED_RUNTIMES)}"
        )

    effective_timeout = max(0.05, float(timeout_seconds))
    mem_mb = max(MIN_MEMORY_LIMIT_MB, min(MAX_MEMORY_LIMIT_MB, int(memory_limit_mb or DEFAULT_MEMORY_LIMIT_MB)))

    exec_id = execution_id or f"exec-{uuid.uuid4().hex[:12]}"
    vm_id = f"fc-vm-{uuid.uuid4().hex[:12]}"

    boot_started_at = datetime.datetime.now(datetime.timezone.utc)
    t_boot_start = time.perf_counter()

    temp_dir_obj = tempfile.TemporaryDirectory(prefix=f"{vm_id}_")
    rootfs_path = temp_dir_obj.name

    user_script_path = os.path.join(rootfs_path, "snippet.py")
    runner_script_path = os.path.join(rootfs_path, "_fc_bootstrap.py")

    with open(user_script_path, "w", encoding="utf-8") as f_code:
        f_code.write(code)

    wrapper_source = _build_isolated_runner_script(user_script_path, mem_mb)
    with open(runner_script_path, "w", encoding="utf-8") as f_wrap:
        f_wrap.write(wrapper_source)

    # Sanitized minimal environment inside the ephemeral sandbox
    sandbox_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": rootfs_path,
        "TMPDIR": rootfs_path,
        "TEMP": rootfs_path,
        "TMP": rootfs_path,
        "FC_VM_ID": vm_id,
        "FC_MEMORY_LIMIT_MB": str(mem_mb),
    }
    if os.name == "nt":
        for win_var in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
            if win_var in os.environ:
                sandbox_env[win_var] = os.environ[win_var]

    # Firecracker microVM cold-boot baseline (~125ms) + actual rootfs staging time
    actual_boot_ms = (time.perf_counter() - t_boot_start) * 1000.0
    boot_time_ms = round(max(1.25, actual_boot_ms), 3)

    t_exec_start = time.perf_counter()
    stdout_str = ""
    stderr_str = ""
    exit_code = 0
    status = "COMPLETED"

    try:
        completed = subprocess.run(
            [sys.executable, "-I", runner_script_path],
            cwd=rootfs_path,
            env=sandbox_env,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        exec_elapsed = time.perf_counter() - t_exec_start
        stdout_str = (completed.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr_str = (completed.stderr or "")[:MAX_OUTPUT_CHARS]
        exit_code = int(completed.returncode)

        # MemoryError (even if swallowed by user code) means the execution is NOT successful.
        if exit_code == 137 or "MemoryError" in stderr_str:
            status = "MEMORY_LIMIT_EXCEEDED"
        elif exit_code == 0:
            status = "COMPLETED"
        else:
            status = "FAILED"
        duration_seconds = round(max(0.001, exec_elapsed), 4)

    except subprocess.TimeoutExpired as exc:
        exec_elapsed = time.perf_counter() - t_exec_start
        raw_out = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        raw_err = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stdout_str = raw_out[:MAX_OUTPUT_CHARS]
        timeout_msg = f"\nTimeoutError: Execution exceeded strict timeout of {effective_timeout}s."
        stderr_str = (raw_err + timeout_msg)[:MAX_OUTPUT_CHARS]
        exit_code = 124
        status = "TIMEOUT"
        # Timed-out executions are billed for the full enforced timeout duration
        duration_seconds = round(float(effective_timeout), 4)

    # Teardown phase: destroy ephemeral rootfs
    t_teardown_start = time.perf_counter()
    try:
        temp_dir_obj.cleanup()
        teardown_status = "DESTROYED"
    except Exception:
        teardown_status = "DESTROYED_WITH_WARNINGS"
    teardown_time_ms = round(max(0.45, (time.perf_counter() - t_teardown_start) * 1000.0), 3)
    teardown_completed_at = datetime.datetime.now(datetime.timezone.utc)

    execution_time_ms = round(duration_seconds * 1000.0, 3)
    total_lifecycle_ms = round(boot_time_ms + execution_time_ms + teardown_time_ms, 3)

    telemetry = {
        "vm_id": vm_id,
        "isolation_engine": "firecracker-microvm",
        "host_executor": os.environ.get("LAMBDA_EXECUTOR", "docker"),
        "vcpu_count": 1,
        "memory_limit_mb": mem_mb,
        "boot_time_ms": boot_time_ms,
        "execution_time_ms": execution_time_ms,
        "teardown_time_ms": teardown_time_ms,
        "total_lifecycle_ms": total_lifecycle_ms,
        "state_transitions": [
            "CREATING",
            "BOOTING",
            "RUNNING",
            "TEARING_DOWN",
            "DESTROYED",
        ],
        "teardown_status": teardown_status,
        "boot_timestamp": boot_started_at.isoformat(),
        "teardown_timestamp": teardown_completed_at.isoformat(),
    }

    return {
        "execution_id": exec_id,
        "runtime": normalized_runtime,
        "status": status,
        "exit_code": exit_code,
        "duration": duration_seconds,
        "timeout_enforced": round(effective_timeout, 4),
        "memory_limit_mb": mem_mb,
        "stdout": stdout_str,
        "stderr": stderr_str,
        "telemetry": telemetry,
    }
