"""Manual check: one real call to Claude (costs a fraction of a cent).

Run from the repo root:  uv run python -m scripts.try_claude
"""

from app.core.settings import Settings
from app.llm.claude import build_claude_client
from app.llm.client import ChatMessage

client = build_claude_client(Settings())
result = client.generate(
    system="You are a friendly dental clinic receptionist. Answer in one or two sentences.",
    messages=[ChatMessage("user", "Olá! Vocês atendem aos sábados?")],
)
print(result.text)
print(f"\nmodel={result.model} tokens in/out={result.input_tokens}/{result.output_tokens}")
