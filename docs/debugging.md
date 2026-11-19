# Follow a conversation in the debugger

Use Streamlit as the patient and VS Code to pause the FastAPI backend. A paused request stays
pending in Streamlit; continuing execution delivers the reply to the same conversation.

The local `.vscode/launch.json` includes **Dentist: guided conversation (API + Streamlit)**.
Select it in Run and Debug and press F5. It starts the API on `127.0.0.1:8001` without reload
and Streamlit on `127.0.0.1:8501`, pointed at that API. Wait for API startup to complete,
then open http://127.0.0.1:8501. This profile calls live Claude with the configured model and
credentials. It sets the model's network timeout to 300 seconds and disables retries.

The Streamlit profile sets `CHAT_API_TIMEOUT_SECONDS=0`, which passes `timeout=None` to its
HTTP client. That disables the frontend request timeout so you can spend time at breakpoints.
The ordinary frontend default remains 70 seconds; a positive value sets another timeout.
The model's network timeout is separate from waiting at application breakpoints.

The VS Code profiles are local ignored files. To use an existing API debug session on port
8001 and start just its frontend in a terminal:

```bash
CHAT_API_URL=http://127.0.0.1:8001 CHAT_API_TIMEOUT_SECONDS=0 \
  uv run streamlit run frontend/streamlit_app.py --server.port 8501
```

Keep the API process running between messages and use the same Streamlit tab/conversation id.
State and backend history are in memory: stopping/restarting the API discards them. The normal
`uvicorn --reload` command restarts the API when code changes; leave reload off for this session.
The compound's Stop button stops both processes. Pausing and continuing preserves their memory.

## First message

In `app/services/conversation.py`, click the gutter to add breakpoints on these statements:

| Statement | What is available when paused there |
|---|---|
| `now = self._current_time()` | `message`, `conversation_id`, and loaded `state`, including prior history |
| `outcome = self._workflow.process(state, result, now)` | `result`: the validated model reply and structured proposals, before Python applies scheduling decisions |
| `return outcome.reply` | Final `outcome.reply`, accepted state, and the saved user/assistant messages |

Send **“Quero marcar uma consulta”** in Streamlit. It should stay on “Thinking...” while paused.
At the first breakpoint, inspect `message` and `state.messages`. Use F10 (Step Over) to run one
line at a time: capture the clock, build the prompt, then make the model call. That model call
needs to finish before `result` becomes available.

At the workflow call, inspect `result.intent`, `result.service_update`, and `result.text`.
Use F11 (Step Into) to enter the workflow, or F10 to execute it and inspect the returned outcome.
Shift+F11 steps out of the current function. F5 continues to the next enabled breakpoint.
These are VS Code's default debugger shortcuts; the toolbar has the same controls.

For this first message, we expect a consultation preference and a backend question about the
next missing detail. Actual extraction depends on the live model. The workflow checks escalation,
routes the intent, validates proposals, and selects the reply.

At `return outcome.reply`, inspect:

```python
outcome.reply  # exact text about to be returned to Streamlit
state.messages[-1]  # the same assistant reply saved in backend history
state.service_id
state.stage
```

`result.text` is the model's proposed prose. `outcome.reply` is the actual answer: the backend
may replace model prose with a missing-detail question, checked offer, selection summary, or
configured guidance. Press F5 so the HTTP response completes and Streamlit displays it.

Continue with one message at a time. The Streamlit sidebar id should match `conversation_id`;
the next request's `state.messages` should contain the previous successful exchange. Share the
highlighted statement and the relevant proposal/stage when asking for help with the next step.

## Where to step next

| Function | Responsibility to observe |
|---|---|
| `SchedulingWorkflow.process` | Escalation, FAQ, or booking route |
| `PreferenceResolver.resolve` | Detached preference changes, validation, and completeness |
| `AvailabilityService.find_slots` | Exact candidates calculated through the mock calendar |
| `resolve_slot_selection` | Lookup of a saved option without another calendar read |
| `_apply_state` | Apply the proposed successful fields before saving history |

The mock is the active calendar; there are no Google calls or booking writes. Debugger variable
inspection lets you see replies without adding patient text to application logs.

VS Code documents the [Python debugger](https://code.visualstudio.com/docs/python/debugging)
and [compound launch configurations](https://code.visualstudio.com/docs/debugtest/debugging-configuration#_compound-launch-configurations).

## If Streamlit shows HTTP 503

The failed request has finished. Variables appear again when a later request pauses at a
breakpoint; exception locals from the completed turn are no longer available. Select the API
terminal (the launch command contains `uvicorn`, port 8001), rather than the Streamlit terminal,
and find the warning containing `outcome=error`. It records the underlying LLM error. The public
503 alone does not identify the provider's original status.

To inspect a future provider HTTP failure, place a breakpoint on `raise LLMError(...)` inside
the `except anthropic.APIStatusError as exc` branch in app/llm/claude.py. While paused there,
evaluate `exc.status_code` and `exc.message` in Debug Console.

A live request exposed a 400 saying the integer schema's `minimum` property was unsupported.
The Claude adapter now uses `anthropic.transform_schema(AssistantDecision)` for the outgoing
schema, as recommended in the [structured-output docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#using-a-raw-json-schema).
Full Pydantic validation still rejects zero/negative option numbers and malformed proposals.
The fix changes schema preparation, not the patient's message or the debugger timeout.

After changing backend code during this no-reload session, stop and start the guided profile
again to load the fix, then retry the message. The API restart clears in-memory conversations.
