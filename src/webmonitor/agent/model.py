"""SDK-compatible transport gate: truncated responses never release tool arguments."""
import json
from agents.exceptions import ModelBehaviorError
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel


class CompleteToolStream:
    """Preserve the provider stream interface while checking its actual terminal reason."""
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    async def __aiter__(self):
        calls = {}
        finish = None
        async for chunk in self.stream:
            for choice in chunk.choices:
                if choice.index != 0:
                    raise ModelBehaviorError("Multiple completion choices are not supported")
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
                for call in choice.delta.tool_calls or []:
                    if call.type not in {None, "function"}:
                        raise ModelBehaviorError("Unsupported tool call type")
                    record = calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                    if call.id:
                        record["id"] = call.id
                    if call.function:
                        if call.function.name:
                            record["name"] = call.function.name
                        record["arguments"] += call.function.arguments or ""
            if finish in {"length", "content_filter"}:
                raise ModelBehaviorError("Model response is incomplete")
            yield chunk
        if calls:
            if finish != "tool_calls":
                raise ModelBehaviorError("Tool response ended without successful completion")
            for record in calls.values():
                try:
                    arguments = json.loads(record["arguments"])
                except (ValueError, TypeError):
                    raise ModelBehaviorError("Tool arguments are incomplete") from None
                if not record["id"] or not record["name"] or not isinstance(arguments, dict):
                    raise ModelBehaviorError("Tool call is incomplete")
        elif finish != "stop":
            raise ModelBehaviorError("Model response ended without a terminal reason")

    async def close(self):
        await self.stream.close()


class CompleteChatCompletionsModel(OpenAIChatCompletionsModel):
    """Pinned SDK adapter, not a second model/tool loop."""
    async def _fetch_response(self, *args, **kwargs):
        result = await super()._fetch_response(*args, **kwargs)
        if isinstance(result, tuple):
            response, stream = result
            return response, CompleteToolStream(stream)
        return result
