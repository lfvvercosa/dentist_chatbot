"""Application entry point for a chat message, independent of HTTP or any channel."""


class ConversationService:
    def handle_message(self, conversation_id: str, message: str) -> str:
        # Placeholder until step 3 plugs in history and the LLM.
        return f"You said: {message}"
