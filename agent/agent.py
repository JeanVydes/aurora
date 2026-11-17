#!/usr/bin/env python3
"""
Aurora — terminal coding agent.

    uv run python agent.py
"""

import json
import os
import sys

import requests
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "cohere/north-mini-code:free")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

AURORA_API_URL = os.environ.get("AURORA_API_URL", "https://aurora.jeanvides.com/api/v1").rstrip("/")
AURORA_API_KEY = os.environ.get("AURORA_API_KEY")  # required — generate from /auth
AURORA_EMAIL = os.environ.get("AURORA_EMAIL")
AURORA_PASSWORD = os.environ.get("AURORA_PASSWORD")

console = Console()


def _aurora_headers() -> dict:
    """Resolve the auth header for the Aurora API (api key > login fallback)."""
    if AURORA_API_KEY:
        return {"X-API-Key": AURORA_API_KEY}
    if not (AURORA_EMAIL and AURORA_PASSWORD):
        raise RuntimeError(
            "No Aurora credentials. Set AURORA_API_KEY (from /auth), or AURORA_EMAIL + AURORA_PASSWORD."
        )
    resp = requests.post(
        f"{AURORA_API_URL}/auth/login",
        json={"email": AURORA_EMAIL, "password": AURORA_PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError(f"Aurora login failed: {resp.text[:200]}")
    return {"Authorization": f"Bearer {token}"}


def _aurora_session():
    """Reuse a session/token between tool calls within one run."""
    global _SESSION
    try:
        return _SESSION
    except NameError:
        _SESSION = _aurora_headers()
        return _SESSION


SYSTEM_PROMPT = """You are Aurora, a terse, precise coding agent with a sandboxed Python 3.11 executor.

Environment:
- The only code you can run is Python 3.11 via the `execute_python` tool.
- The executor is ephemeral: ~5s default timeout (max 300s), 128MB memory ceiling (max 512MB), 64KB output cap.
- Network access inside the sandbox: none.

Rules:
- Answer briefly. Prefer showing real results over describing them. If you do not know the output, run the code.
- If the executor says STATUS: TIMEOUT or MEMORY_LIMIT_EXCEEDED, explain exactly why (and, if asked, retry with a different timeout/memory_limit_mb).
- Never invent stdout. Always call the tool when the answer depends on running code.
"""


_TOOLS: list = []


@tool
def execute_python(code: str, timeout: float = 5.0, memory_limit_mb: int = 128) -> str:
    """Run Python 3.11 code in an ephemeral Aurora sandbox and return its result.

    Args:
        code: The Python source to execute
        timeout: wall-clock budget in seconds (0.05..300)
        memory_limit_mb: memory ceiling in MB (16..512)

    Returns:
        Compact JSON with STATUS, stdout, stderr, duration, exit code and telemetry.
    """
    try:
        headers = _aurora_session()
        resp = requests.post(
            f"{AURORA_API_URL}/execute",
            headers=headers,
            json={
                "code": code,
                "runtime": "python3.11",
                "timeout": float(timeout),
                "memory_limit_mb": int(memory_limit_mb),
            },
            timeout=300,
        )
        try:
            data = resp.json()
        except ValueError:
            return f"STATUS: HTTP {resp.status_code}\n{resp.text[:2000]}"
        if not resp.ok:
            return f"STATUS: HTTP {resp.status_code}\n{json.dumps(data, ensure_ascii=False)}"
        return json.dumps(
            {
                "STATUS": data.get("status"),
                "execution_id": data.get("execution_id"),
                "duration": data.get("duration"),
                "exit_code": data.get("exit_code"),
                "stdout": data.get("stdout"),
                "stderr": data.get("stderr"),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return f"STATUS: ERROR\n{exc}"


_TOOLS = [execute_python]


def _agent():
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is required")

    model = ChatOpenAI(
        model=OPENROUTER_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        temperature=0.2,
        timeout=120,
        max_retries=2,
    )
    return create_agent(model, _TOOLS, system_prompt=SYSTEM_PROMPT)


def _status_color(status: str | None) -> str:
    if status == "COMPLETED":
        return "green"
    if status in {"TIMEOUT", "MEMORY_LIMIT_EXCEEDED"}:
        return "yellow"
    return "red"


def _print_tool_call(name: str, args: dict) -> None:
    code = args.get("code") if isinstance(args, dict) else None
    params = []
    if isinstance(args, dict):
        if args.get("timeout") is not None:
            params.append(f"timeout={args['timeout']}s")
        if args.get("memory_limit_mb") is not None:
            params.append(f"memory={args['memory_limit_mb']}MB")
    body = [Syntax(code, "python", theme="monokai", word_wrap=True, line_numbers=True)] if code else [Text(json.dumps(args, ensure_ascii=False, indent=2))]
    if params:
        body.insert(0, Text("  ".join(params), style="dim"))
    console.print(
        Panel(
            Group(*body),
            title=f"[bold cyan]⚙ {name}[/]",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )
    )


def _print_tool_result(content: str) -> None:
    try:
        data = json.loads(content)
    except Exception:
        console.print(Panel(Text(content), title="[bold]tool result[/]", border_style="white"))
        return

    status = str(data.get("STATUS", "?"))
    color = _status_color(status)
    meta = Text.assemble(
        (f"■ {status}", f"bold {color}"),
        ("  ·  ", "dim"),
        (str(data.get("execution_id", "?")), "dim"),
        ("  ·  ", "dim"),
        (f"{data.get('duration', '?')}s", "cyan"),
        ("  ·  exit ", "dim"),
        (str(data.get("exit_code", "?")), "dim"),
    )

    stdout = (data.get("stdout") or "").rstrip("\n")
    stderr = (data.get("stderr") or "").rstrip("\n")
    if not stdout and not stderr:
        body = Text("(no output)", style="dim italic")
    elif stderr and not stdout:
        body = Text(stderr, style="red")
    else:
        body = Text(stdout)
        if stderr:
            body = Text.assemble(body, Text("\n" + stderr, style="red"))

    console.print(
        Panel(
            Group(meta, Text(""), body),
            title="[bold]tool result[/]",
            title_align="left",
            border_style=color,
            padding=(0, 1),
        )
    )


def main() -> int:
    agent = _agent()
    console.print(
        Panel(
            Group(
                Text("Aurora", style="bold white") + Text(" — terminal coding agent", style="dim"),
                Text.from_markup(f"[dim]model[/]  [cyan]{OPENROUTER_MODEL}[/]"),
                Text.from_markup(f"[dim]api[/]    [cyan]{AURORA_API_URL}[/]"),
                Text(""),
                Text.from_markup("[dim]/help  /clear  /tools  /exit[/]"),
            ),
            title="[bold cyan]AURORA[/]",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    messages: list = []

    while True:
        try:
            console.print("\n[bold yellow]❯ [/]", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            return 0

        if not user_input:
            continue
        if user_input in ("/exit", "/quit", ":q"):
            console.print("bye")
            return 0
        if user_input == "/clear":
            messages = []
            console.print("[dim]history cleared[/]")
            continue
        if user_input == "/help":
            console.print(
                Panel(
                    Group(
                        Text.from_markup("[cyan]/help[/]    Show this help"),
                        Text.from_markup("[cyan]/clear[/]   Reset conversation history"),
                        Text.from_markup("[cyan]/tools[/]   List available tools"),
                        Text.from_markup("[cyan]/exit[/]    Quit"),
                    ),
                    title="[bold]commands[/]",
                    title_align="left",
                    border_style="green",
                    padding=(0, 1),
                )
            )
            continue
        if user_input == "/tools":
            for t in _TOOLS:
                desc = (t.description or "").strip().splitlines()[0]
                console.print(f"[bold cyan]⚙ {t.name}[/]  [dim]{desc}[/]")
            continue

        messages.append(HumanMessage(content=user_input))
        try:
            with console.status("[cyan]thinking…", spinner="dots"):
                result = agent.invoke({"messages": messages})
            new_msgs = result["messages"][len(messages):]
            for m in new_msgs:
                if isinstance(m, AIMessage):
                    for tc in getattr(m, "tool_calls", []) or []:
                        _print_tool_call(tc.get("name") or "?", tc.get("args") or {})
                    if m.content:
                        console.print()
                        console.print(m.content)
                elif isinstance(m, ToolMessage):
                    _print_tool_result(str(m.content))
            messages = result["messages"]
        except Exception as exc:
            console.print(Panel(Text(str(exc), style="white"), title="[bold red]error[/]", border_style="red"))
            messages.pop()


if __name__ == "__main__":
    sys.exit(main())
