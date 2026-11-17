# Aurora — terminal coding agent

LangChain agent that talks to you **in the terminal** and can execute Python 3.11 code in the
Aurora ephemeral sandbox. Powered by OpenRouter.

**Model:** default `cohere/north-mini-code:free`.

> `thinkingmachines/inkling-2-small:free` está restringido por OpenRouter a harnesses
> agénticos listados (devuelve `403 ... only available on agentic harnesses` por API directa),
> así que el default usa `cohere/north-mini-code:free`. Puedes sobreescribir el modelo:
> `OPENROUTER_MODEL=cohere/north-mini-code:free uv run python agent.py` o cualquier otro id
> de OpenRouter (p.ej. `thinkingmachines/inkling-2-small` de pago).

## Setup

```bash
cd agent
uv sync
```

Environment variables:

| Var | Required | Default | Purpose |
| :--- | :--- | :--- | :--- |
| `OPENROUTER_API_KEY` | yes | — | OpenRouter API key |
| `OPENROUTER_MODEL` | no | `cohere/north-mini-code:free` | Model to use |
| `OPENROUTER_BASE_URL` | no | `https://openrouter.ai/api/v1` | OpenRouter endpoint |
| `AURORA_API_URL` | no | `https://aurora.jeanvides.com/api/v1` | Aurora API base (public tunnel, Vercel → LocalStack) |
| `AURORA_API_KEY` | recommended | — | Aurora account API key (preferred auth; generate in the `/auth` page) |
| `AURORA_EMAIL` / `AURORA_PASSWORD` | fallback | — | Aurora credentials (login on first call) |

## Run

```bash
# interactive terminal chat
OPENROUTER_API_KEY=... uv run python agent.py
```

You'll get:

```
> print the first 5 primes
[thinking…]
  [tool] execute_python({"code": "def is_prime(n): ..."})
  [tool result] {"STATUS": "COMPLETED", ... "stdout": "[2, 3, 5, 7, 11]\n" ...}

[2, 3, 5, 7, 11]
```

Commands: `/help` `/clear` `/tools` `/exit`

## How the tool works

`execute_python(code: str, timeout: float = 5.0, memory_limit_mb: int = 128)` calls the Aurora
`POST /execute` API and returns a compact JSON with `STATUS`, `stdout`, `stderr`, `duration`,
telemetry, etc. The agent decides when to call it — it never executes code locally.
