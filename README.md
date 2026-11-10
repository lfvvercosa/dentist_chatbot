# Dental AI Assistant

A conversational assistant for a (fictional) dental clinic, built in four incremental versions in one repository. Each version is tagged (`v1.0` … `v4.0`) and runs on its own.

Planning documents live in [documents/](documents/). Per-version scope and decisions live in [docs/](docs/).

## Current status: V1 in progress

See [docs/v1-scope.md](docs/v1-scope.md) for scope, steps and acceptance checklist.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # create .venv and install dependencies
cp .env.example .env     # then add your Anthropic API key
```

## Development

```bash
uv run pytest            # run tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

## Try a single Claude call

Needs `ANTHROPIC_API_KEY` in `.env`. Costs a fraction of a cent.

```bash
uv run python -m scripts.try_claude
```

## Run the API

```bash
uv run uvicorn app.main:app --reload
```

Needs `ANTHROPIC_API_KEY` in `.env`. Then open http://localhost:8000/docs to try the endpoints
in the browser. `/chat` answers with Claude. Conversation history is kept in memory per
`conversation_id` and is lost when the server restarts.
