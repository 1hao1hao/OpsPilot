"""Runtime error taxonomy used by recovery and retry decisions."""


class RuntimeExecutionError(RuntimeError):
    code = "runtime_execution_error"
    retryable = False


class CheckpointVersionMismatch(RuntimeExecutionError):
    code = "checkpoint_version_mismatch"


class WorkerCrash(BaseException):
    """Test fault that models process death and bypasses normal failure handling."""
