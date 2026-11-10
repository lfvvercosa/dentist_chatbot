"""Application entry point for a chat message, independent of HTTP or any channel."""

from app.llm.client import ChatMessage, LLMClient


class ConversationService:
    """Keeps each conversation's history and asks the LLM for the next reply.

    History lives in memory, so it is lost on restart (fine for V1; V3 moves it to PostgreSQL).
    """

    def __init__(self, llm: LLMClient, system_prompt: str):
        self._llm = llm
        self._system_prompt = system_prompt
        self._histories: dict[str, list[ChatMessage]] = {}

    def handle_message(self, conversation_id: str, message: str) -> str:
        history = self._histories.setdefault(conversation_id, [])
        user_message = ChatMessage("user", message)

        result = self._llm.generate(self._system_prompt, [*history, user_message])

        # Save the turn only after a successful reply: if the call fails, the history
        # never holds a user message without an answer.
        history += [user_message, ChatMessage("assistant", result.text)]
        return result.text
