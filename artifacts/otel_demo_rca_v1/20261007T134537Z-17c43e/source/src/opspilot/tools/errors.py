"""Tool-layer errors with retry semantics."""


class ToolError(Exception):
    code = "tool_error"
    retryable = False


class UnknownToolError(ToolError):
    code = "unknown_tool"


class ToolValidationError(ToolError):
    code = "invalid_tool_input"


class ToolTimeoutError(ToolError):
    code = "tool_timeout"
    retryable = True


class ToolExecutionError(ToolError):
    code = "tool_execution_error"
    retryable = True

