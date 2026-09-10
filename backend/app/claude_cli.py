"""Runs the Claude Code CLI as a subprocess chat backend using the machine's existing login."""
import glob
import json
import os
import shutil
import subprocess
import uuid

__all__ = ["ClaudeCLI", "ClaudeCLINotFound", "ClaudeCLIError", "find_cli"]

_GLOBS = [
    r"%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\claude.exe",
    r"%APPDATA%\Claude\claude-code\*\claude.exe",
    "~/.claude/local/claude*",
    "~/.local/bin/claude*",
]


_EXECUTION_TOOLS = ["Bash", "PowerShell", "Write", "Edit", "NotebookEdit"]


class ClaudeCLINotFound(RuntimeError):
    """Error raised when the Claude Code CLI binary cannot be located."""
    pass


class ClaudeCLIError(RuntimeError):
    """Error raised when a CLI call times out, exits non-zero, returns non-JSON or reports an error."""
    pass


def find_cli():
    """Returns the CLI path from CLAUDE_CLI, PATH or newest known install; raises ClaudeCLINotFound if none."""
    env = os.environ.get("CLAUDE_CLI")
    if env and os.path.exists(env):
        return env

    on_path = shutil.which("claude")
    if on_path:
        return on_path

    for pattern in _GLOBS:
        expanded = os.path.expanduser(os.path.expandvars(pattern))
        matches = [m for m in glob.glob(expanded) if os.path.isfile(m)]
        if matches:
            matches.sort(key=lambda p: os.path.basename(os.path.dirname(p)))
            return matches[-1]

    looked = "\n  ".join([
        "CLAUDE_CLI environment variable",
        "claude on PATH",
        *_GLOBS,
    ])
    raise ClaudeCLINotFound(
        "Could not find the Claude Code CLI. Looked in:\n  " + looked +
        "\n\nInstall it with:  npm install -g @anthropic-ai/claude-code"
        "\nOr set CLAUDE_CLI to the binary's full path."
    )


class ClaudeCLI:
    """One Claude CLI conversation that threads history across calls by reusing a session id."""

    def __init__(self, system_prompt=None, model=None, allowed_tools=None,
                 cwd=None, cli_path=None, append=False, timeout=300,
                 config_dir=None, permission_mode="bypassPermissions",
                 block_execution=True):
        """Finds the CLI if no path is given, stores prompt, model, tool and timeout options and a new session id."""
        self.cli_path = cli_path or find_cli()
        self.system_prompt = system_prompt
        self.model = model
        self.allowed_tools = list(allowed_tools) if allowed_tools else []
        self.cwd = cwd
        self.append = append
        self.timeout = timeout
        self.config_dir = config_dir
        self.permission_mode = permission_mode
        self.block_execution = block_execution

        self.session_id = str(uuid.uuid4())
        self._started = False
        self.last_response = {}

    @property
    def cost_usd(self):
        """Returns the notional API cost reported for the last turn."""
        return self.last_response.get("total_cost_usd")

    @property
    def usage(self):
        """Returns the token usage dict reported for the last turn."""
        return self.last_response.get("usage", {})

    @property
    def denied_tools(self):
        """Returns the tool calls the CLI refused during the last turn."""
        return self.last_response.get("permission_denials", [])

    def reset(self):
        """Starts a new session id and clears the last response so conversation history is dropped."""
        self.session_id = str(uuid.uuid4())
        self._started = False
        self.last_response = {}

    def _build_command(self, message):
        """Builds the CLI argument list with prompt, model, tool permissions and session create or resume flags."""
        cmd = [self.cli_path, "-p", message, "--output-format", "json"]

        if self.system_prompt:
            flag = "--append-system-prompt" if self.append else "--system-prompt"
            cmd += [flag, self.system_prompt]
        if self.model:
            cmd += ["--model", self.model]
        if self.allowed_tools:
            cmd += ["--allowedTools", *self.allowed_tools]
            cmd += ["--permission-mode", self.permission_mode]
        elif self.block_execution:
            cmd += ["--disallowedTools", *_EXECUTION_TOOLS]

        if self._started:
            cmd += ["--resume", self.session_id]
        else:
            cmd += ["--session-id", self.session_id]
        return cmd

    def ask(self, message):
        """Sends one message through the CLI, parses the JSON output and returns the reply text."""
        env = dict(os.environ)
        if self.config_dir:
            env["CLAUDE_CONFIG_DIR"] = self.config_dir

        try:
            proc = subprocess.run(
                self._build_command(message),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, cwd=self.cwd, env=env,
            )
        except subprocess.TimeoutExpired:
            raise ClaudeCLIError(f"Claude CLI timed out after {self.timeout}s")

        if proc.returncode != 0:
            raise ClaudeCLIError(
                f"Claude CLI exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        try:
            self.last_response = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise ClaudeCLIError(
                "Claude CLI did not return JSON: " + proc.stdout.strip()[:500]
            )

        if self.last_response.get("is_error"):
            raise ClaudeCLIError(f"Claude CLI reported an error: {self.last_response}")

        self._started = True
        return self.last_response.get("result", "")
