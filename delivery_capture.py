"""Observe this event's successful send calls without altering their payloads."""

import asyncio


def plain_text(message):
    return "\n".join(
        c.text
        for c in getattr(message, "chain", [])
        if isinstance(getattr(c, "text", None), str)
    )


def install_capture(event, capture, failed, *, key="reading"):
    callbacks = getattr(event, "_ziwei_capture_callbacks", None)
    if callbacks is None:
        callbacks = event._ziwei_capture_callbacks = {}
    callbacks[key] = (capture, failed)
    if getattr(event, "_ziwei_send_capture", False):
        return
    send = getattr(event, "send", None)
    if not callable(send):
        raise ValueError("当前平台无法确认回答发送，不能建立分析档案")
    streaming = getattr(event, "send_streaming", None)
    stream_depth = 0

    def capture_failed():
        for _, callback in tuple(callbacks.values()):
            callback()

    async def capture_text(text):
        for callback, _ in tuple(callbacks.values()):
            await callback(text)

    async def observed_send(message, *args, **kwargs):
        try:
            result = await send(message, *args, **kwargs)
        except (Exception, asyncio.CancelledError):
            capture_failed()
            raise
        if not stream_depth:
            if result is False:
                capture_failed()
            else:
                await capture_text(plain_text(message))
        return result

    async def observed_stream(generator, *args, **kwargs):
        nonlocal stream_depth
        pieces = []
        size = 0
        overflow = False

        async def tapped():
            nonlocal size, overflow
            async for chunk in generator:
                text = plain_text(chunk)
                if getattr(event, "_ziwei_delivery", None) or getattr(
                    event, "_ziwei_rectification_delivery", None
                ):
                    size += len(text)
                    if size <= 12000:
                        pieces.append(text)
                    else:
                        overflow = True
                yield chunk

        stream_depth += 1
        try:
            result = await streaming(tapped(), *args, **kwargs)
            if result is False or overflow:
                capture_failed()
            else:
                await capture_text("".join(pieces))
            return result
        except (Exception, asyncio.CancelledError):
            capture_failed()
            raise
        finally:
            stream_depth -= 1

    event.send = observed_send
    if callable(streaming):
        event.send_streaming = observed_stream
    event._ziwei_send_capture = True
