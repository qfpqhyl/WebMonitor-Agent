import pytest
from agents.exceptions import ModelBehaviorError
from agents.models.chatcmpl_stream_handler import ChatCmplStreamHandler
from openai.types.chat import ChatCompletionChunk
from webmonitor.agent.model import CompleteToolStream


async def provider(arguments, reason):
    yield ChatCompletionChunk.model_validate({"id":"completion","object":"chat.completion.chunk","created":1,"model":"model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call1","type":"function","function":{"name":"create_monitor_task","arguments":arguments}}]},"finish_reason":None}]})
    if reason:
        yield ChatCompletionChunk.model_validate({"id":"completion","object":"chat.completion.chunk","created":1,"model":"model","choices":[{"index":0,"delta":{},"finish_reason":reason}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,reason", [('{}','length'),('{}',None),('{"draft_id":','tool_calls'),('{}','content_filter'),('{}','stop')])
async def test_incomplete_stream_never_releases_tool_calls(arguments,reason):
    released=[]
    with pytest.raises(ModelBehaviorError):
        async for chunk in ChatCmplStreamHandler.buffer_tool_call_stream(CompleteToolStream(provider(arguments,reason))):
            released.extend(call for choice in chunk.choices for call in choice.delta.tool_calls or [])
    assert released==[]


@pytest.mark.asyncio
async def test_completed_tool_arguments_released_once():
    released=[]
    async for chunk in ChatCmplStreamHandler.buffer_tool_call_stream(CompleteToolStream(provider('{"draft_id":"approved"}','tool_calls'))):
        released.extend(call for choice in chunk.choices for call in choice.delta.tool_calls or [])
    assert [(call.id,call.function.name,call.function.arguments) for call in released]==[('call1','create_monitor_task','{"draft_id":"approved"}')]
