from __future__ import annotations

import io
import unittest

from rulence._jsonrpc import MAX_MESSAGE_BYTES, read_message


class JsonRpcFramingTests(unittest.TestCase):
    def test_reads_valid_framed_message(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1}'
        stream = io.BytesIO(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)

        message, framing = read_message(stream)

        self.assertEqual(message, {"jsonrpc": "2.0", "id": 1})
        self.assertEqual(framing, "headers")

    def test_reads_jsonl_message(self) -> None:
        message, framing = read_message(io.BytesIO(b'{"jsonrpc":"2.0","id":2}\n'))

        self.assertEqual(message, {"jsonrpc": "2.0", "id": 2})
        self.assertEqual(framing, "jsonl")

    def test_rejects_non_numeric_content_length(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            read_message(io.BytesIO(b"Content-Length: abc\r\n\r\n{}"))

        self.assertIn("invalid Content-Length", str(ctx.exception))

    def test_rejects_oversized_content_length(self) -> None:
        oversized = MAX_MESSAGE_BYTES + 1
        with self.assertRaises(ValueError) as ctx:
            read_message(io.BytesIO(f"Content-Length: {oversized}\r\n\r\n".encode() + b"{}"))

        self.assertIn("exceeds MAX_MESSAGE_BYTES", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
