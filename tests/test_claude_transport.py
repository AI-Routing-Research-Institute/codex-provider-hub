import asyncio
import unittest

import httpx
from curl_cffi.requests.exceptions import RequestException

from local_proxy.transports.claude import ClaudeCurlClient


class FakeCurlResponse:
    status_code = 200
    reason = "OK"
    headers = {"content-type": "text/event-stream", "x-upstream": "fixture"}

    def __init__(self) -> None:
        self.closed = False
        self.quit_now = asyncio.Event()

    async def aiter_content(self):
        yield b"event: message_start\n"
        yield b'data: {"type":"message_start"}\n\n'

    async def aclose(self) -> None:
        self.closed = True
        self.quit_now.set()


class HangingCurlResponse(FakeCurlResponse):
    def __init__(self) -> None:
        super().__init__()
        self.astream_task = asyncio.create_task(asyncio.sleep(60))

    async def aclose(self) -> None:
        await self.astream_task


class FakeSession:
    def __init__(self, response=None, error=None) -> None:
        self.response = response or FakeCurlResponse()
        self.error = error
        self.calls = []
        self.closed = False

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self) -> None:
        self.closed = True


class ClaudeCurlClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_response_charset_falls_back_to_utf8(self) -> None:
        upstream = FakeCurlResponse()
        upstream.encoding = "x-not-a-real-encoding"
        client = ClaudeCurlClient(session=FakeSession(response=upstream))

        response = await client.send(
            client.build_request("POST", "https://provider.example/v1/messages"),
            stream=True,
        )

        self.assertEqual(response.encoding, "utf-8")

    async def test_forwards_request_and_streams_raw_chunks(self) -> None:
        session = FakeSession()
        client = ClaudeCurlClient(session=session)
        request = client.build_request(
            "POST",
            "https://provider.example/v1/messages",
            params=[("beta", "true"), ("tag", "a"), ("tag", "b")],
            headers={"anthropic-version": "2023-06-01", "x-api-key": "secret"},
            content=b'{"stream":true}',
        )

        response = await client.send(request, stream=True)
        body = b"".join([chunk async for chunk in response.aiter_raw()])

        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://provider.example/v1/messages")
        self.assertEqual(call["params"], [("beta", "true"), ("tag", "a"), ("tag", "b")])
        self.assertEqual(call["data"], b'{"stream":true}')
        self.assertEqual(call["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(call["headers"]["x-api-key"], "secret")
        self.assertEqual(call["accept_encoding"], "identity")
        self.assertTrue(call["stream"])
        self.assertFalse(call["allow_redirects"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/event-stream")
        self.assertEqual(body, b'event: message_start\ndata: {"type":"message_start"}\n\n')

    async def test_closing_response_cancels_upstream(self) -> None:
        upstream = FakeCurlResponse()
        client = ClaudeCurlClient(session=FakeSession(response=upstream))
        response = await client.send(
            client.build_request("POST", "https://provider.example/v1/messages"),
            stream=True,
        )

        await response.aclose()

        self.assertTrue(upstream.closed)
        self.assertTrue(upstream.quit_now.is_set())

    async def test_closing_silent_stream_cancels_curl_task(self) -> None:
        upstream = HangingCurlResponse()
        client = ClaudeCurlClient(session=FakeSession(response=upstream))
        response = await client.send(
            client.build_request("POST", "https://provider.example/v1/messages"),
            stream=True,
        )

        await asyncio.wait_for(response.aclose(), timeout=0.2)

        self.assertTrue(upstream.quit_now.is_set())
        self.assertTrue(upstream.astream_task.cancelled())

    async def test_curl_failure_is_translated_for_existing_retry_logic(self) -> None:
        session = FakeSession(error=RequestException("curl connection failed"))
        client = ClaudeCurlClient(session=session)

        with self.assertRaises(httpx.TransportError) as captured:
            await client.send(
                client.build_request("POST", "https://provider.example/v1/messages"),
                stream=True,
            )

        self.assertIn("curl connection failed", str(captured.exception))

    async def test_client_close_closes_session(self) -> None:
        session = FakeSession()
        client = ClaudeCurlClient(session=session)

        await client.aclose()

        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
