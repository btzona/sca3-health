#!/usr/bin/env python3
"""Offline tests for the summary client. No real tokens or model hosts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch

COMPLETION = json.dumps(
    {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}
).encode()
PARENT = (
    "The study measured a protein in blood from people with SCA3. "
    "Levels were higher in people who already had symptoms."
)
ENV_NAMES = (
    "SCA3_SUMMARY_URL",
    "SCA3_SUMMARY_TOKEN",
    "SCA3_SUMMARY_API_KEY",
    "SCA3_SUMMARY_MODEL",
    "SCA3_SUMMARY_BACKOFF",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_STEP_SUMMARY",
)


class ScriptedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.seen.append(
            {
                "authorization": self.headers.get("Authorization"),
                "api_key": self.headers.get("api-key"),
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "api_version": self.headers.get("X-GitHub-Api-Version"),
            }
        )
        if self.server.script:
            status, body, content_type, extra = self.server.script.pop(0)
        else:
            status, body, content_type, extra = 500, b"empty script", "text/plain", {}
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def stop_server(server) -> None:
    server.shutdown()
    server.server_close()


def start_server(script):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedHandler)
    server.script = list(script)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/chat/completions"


class SummaryClientTests(unittest.TestCase):
    def setUp(self):
        self._perform = fetch.perform_models_post
        self._env = {name: os.environ.get(name) for name in ENV_NAMES}
        for name in ENV_NAMES:
            os.environ.pop(name, None)
        os.environ["SCA3_SUMMARY_BACKOFF"] = "0"
        fetch._models_failure_logged = False
        fetch._models_stopped = False
        fetch._resolved_model = None
        fetch._resolved_model_key = None

    def tearDown(self):
        fetch.perform_models_post = self._perform
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        fetch._models_failure_logged = False
        fetch._models_stopped = False
        fetch._resolved_model = None
        fetch._resolved_model_key = None

    def capture(self, fn):
        buffer = StringIO()
        with redirect_stdout(buffer):
            result = fn()
        return result, buffer.getvalue()

    def test_retirement_stub_is_named_with_diagnostics(self):
        url = "https://models.github.ai/inference/chat/completions"
        self.assertTrue(fetch.is_retirement_stub(url, 200, b"OK\r\n"))
        self.assertTrue(fetch.is_retirement_stub(url, 200, "  ok  "))
        self.assertFalse(fetch.is_retirement_stub(url, 503, b"OK\r\n"))
        self.assertFalse(fetch.is_retirement_stub("http://127.0.0.1/chat", 200, b"OK\r\n"))
        described = fetch.describe_models_response(
            url,
            200,
            b"OK\r\n",
            {"Content-Type": "text/plain"},
            url,
        )
        self.assertIn("content-type=text/plain", described)
        self.assertIn("request-id=(none)", described)
        self.assertIn(repr("OK\r\n"), described)
        self.assertIn("retired on 30 July 2026", described)
        self.assertNotIn("Tried", described)

    def test_github_ok_is_not_retried(self):
        calls = []

        def fake(url, body, token, extra):
            calls.append(token)
            return 200, b"OK\r\n", {"Content-Type": "text/plain", "X-Request-Id": "req-retired"}, url

        fetch.perform_models_post = fake
        os.environ["SCA3_SUMMARY_URL"] = "https://models.github.ai/inference/chat/completions"
        os.environ["GITHUB_TOKEN"] = "ghs_diagnostic_token_value"

        def run():
            first = fetch.post_chat_completion({"model": "openai/gpt-4.1-mini", "messages": []})
            second = fetch.post_chat_completion({"model": "openai/gpt-4.1-mini", "messages": []})
            return first, second

        (first, second), out = self.capture(run)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(calls, ["ghs_diagnostic_token_value"])
        self.assertIn("content-type=text/plain", out)
        self.assertIn("x-request-id=req-retired", out)
        self.assertIn("retired on 30 July 2026", out)
        self.assertNotIn("ghs_diagnostic_token_value", out)
        self.assertEqual(fetch.check_models(), 0)

    def test_check_models_retirement_exits_zero(self):
        def fake(url, body, token, extra):
            return 200, b"OK\r\n", {"Content-Type": "text/plain"}, url

        fetch.perform_models_post = fake
        os.environ["GITHUB_TOKEN"] = "ghs_diagnostic_token_value"
        code, out = self.capture(fetch.check_models)
        self.assertEqual(code, 0)
        self.assertIn("HTTP 200", out)
        self.assertIn("content-type=text/plain", out)
        self.assertIn("request-id=(none)", out)
        self.assertIn("retired on 30 July 2026", out)
        self.assertNotIn("ghs_diagnostic_token_value", out)

    def test_missing_token_does_not_call_the_endpoint(self):
        def fake(*_args, **_kwargs):
            raise AssertionError("endpoint should not be called")

        fetch.perform_models_post = fake
        code, out = self.capture(fetch.check_models)
        self.assertEqual(code, 0)
        self.assertIn("No token was available", out)
        self.assertIn("retired on 30 July 2026", out)

    def test_blank_summary_url_keeps_the_default(self):
        os.environ["SCA3_SUMMARY_URL"] = "  "
        self.assertEqual(fetch.summary_url(), fetch.DEFAULT_SUMMARY_URL)

    def test_transient_non_json_is_retried(self):
        server, url = start_server(
            [
                (200, b"OK\r\n", "text/plain", {"X-Request-Id": "try-1"}),
                (502, b"<html>bad gateway</html>", "text/html", {"X-Request-Id": "try-2"}),
                (200, COMPLETION, "application/json", {"X-Request-Id": "try-3"}),
            ]
        )
        self.addCleanup(stop_server, server)
        os.environ["SCA3_SUMMARY_URL"] = url
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"
        os.environ["GITHUB_TOKEN"] = "ghs_should_not_be_sent_xxxxx"
        parsed, out = self.capture(lambda: fetch.post_chat_completion({"model": "m", "messages": []}))
        self.assertEqual(parsed["choices"][0]["message"]["content"], "pong")
        self.assertEqual(len(server.seen), 3)
        self.assertEqual(server.seen[0]["authorization"], "Bearer provider-token-abcdefgh")
        self.assertNotIn("ghs_should_not_be_sent_xxxxx", server.seen[0]["authorization"])
        self.assertIsNone(server.seen[0]["api_version"])
        self.assertIn("application/vnd.github+json", server.seen[0]["accept"])
        self.assertEqual(server.seen[0]["content_type"], "application/json")
        self.assertEqual(out, "")

    def test_auth_failure_stops_and_redacts_the_token(self):
        server, url = start_server(
            [
                (
                    401,
                    b'{"error":"nope provider-token-abcdefgh"}',
                    "application/json",
                    {"X-Request-Id": "nope-1"},
                )
            ]
        )
        self.addCleanup(stop_server, server)
        os.environ["SCA3_SUMMARY_URL"] = url
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"

        def run():
            return (
                fetch.post_chat_completion({"model": "m", "messages": []}),
                fetch.post_chat_completion({"model": "m", "messages": []}),
            )

        (first, second), out = self.capture(run)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(server.seen), 1)
        self.assertIn("HTTP 401", out)
        self.assertIn("content-type=application/json", out)
        self.assertIn("x-request-id=nope-1", out)
        self.assertIn("[redacted]", out)
        self.assertNotIn("provider-token-abcdefgh", out)

    def test_github_token_is_not_sent_to_another_host(self):
        server, url = start_server([])
        self.addCleanup(stop_server, server)
        os.environ["SCA3_SUMMARY_URL"] = url
        os.environ["GITHUB_TOKEN"] = "ghs_should_not_leave_github_xxxx"
        code, out = self.capture(fetch.check_models)
        self.assertEqual(code, 0)
        self.assertEqual(server.seen, [])
        self.assertIn("GITHUB_TOKEN is not sent", out)
        self.assertNotIn("ghs_should_not_leave_github_xxxx", out)

    def test_api_key_header_without_github_token(self):
        server, url = start_server([(200, COMPLETION, "application/json", {})])
        self.addCleanup(stop_server, server)
        os.environ["SCA3_SUMMARY_URL"] = url
        os.environ["SCA3_SUMMARY_API_KEY"] = "foundry-key-abcdefghij"
        os.environ["GITHUB_TOKEN"] = "ghs_should_not_be_sent_xxxxx"
        parsed, out = self.capture(lambda: fetch.post_chat_completion({"model": "m", "messages": []}))
        self.assertEqual(parsed["choices"][0]["message"]["content"], "pong")
        self.assertEqual(server.seen[0]["api_key"], "foundry-key-abcdefghij")
        self.assertIsNone(server.seen[0]["authorization"])
        self.assertNotIn("foundry-key-abcdefghij", out)
        self.assertNotIn("ghs_should_not_be_sent_xxxxx", out)

    def test_github_request_headers(self):
        headers = fetch.models_request_headers(
            "https://models.github.ai/inference/chat/completions",
            "ghs_example",
            {"api-key": "ignored-on-purpose"},
        )
        self.assertEqual(headers["X-GitHub-Api-Version"], "2026-03-10")
        self.assertIn("application/vnd.github+json", headers["Accept"])
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer ghs_example")
        self.assertEqual(headers["api-key"], "ignored-on-purpose")
        self.assertIn("AtaxinLedger", headers["User-Agent"])

    def test_check_passes_on_a_completion(self):
        server, url = start_server([(200, COMPLETION, "application/json", {"X-Request-Id": "ok-1"})])
        self.addCleanup(stop_server, server)
        os.environ["SCA3_SUMMARY_URL"] = url
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"
        os.environ["SCA3_SUMMARY_MODEL"] = "openai/gpt-4.1-mini"
        code, out = self.capture(fetch.check_models)
        self.assertEqual(code, 0)
        self.assertIn("Summary endpoint check passed", out)
        self.assertIn("'pong'", out)
        self.assertIsNone(server.seen[0]["api_version"])

    def test_parent_summary_round_trip(self):
        body = json.dumps({"choices": [{"message": {"content": PARENT}}]}).encode()
        server, url = start_server([(200, body, "application/json", {})])
        self.addCleanup(stop_server, server)
        os.environ["SCA3_SUMMARY_URL"] = url
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"
        text, out = self.capture(lambda: fetch.github_model_summary("A paper", "abstract " * 20))
        self.assertEqual(text, PARENT)
        self.assertEqual(out, "")

    def test_timeouts_retry_then_stop(self):
        calls = []

        def fake(url, body, token, extra):
            calls.append(url)
            raise TimeoutError("timed out")

        fetch.perform_models_post = fake
        os.environ["SCA3_SUMMARY_URL"] = "https://example.test/chat/completions"
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"

        def run():
            return (
                fetch.post_chat_completion({"model": "m", "messages": []}),
                fetch.post_chat_completion({"model": "m", "messages": []}),
            )

        (first, second), out = self.capture(run)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(calls), fetch.SUMMARY_ATTEMPTS)
        self.assertIn("could not be reached", out)
        self.assertIn("Tried 3 times", out)
        self.assertNotIn("provider-token-abcdefgh", out)

    def test_warning_is_copied_to_the_step_summary(self):
        handle = tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        os.environ["GITHUB_STEP_SUMMARY"] = handle.name

        def fake(url, body, token, extra):
            return 200, b"OK\r\n", {"Content-Type": "text/plain", "X-GitHub-Request-Id": "gh-req"}, url

        fetch.perform_models_post = fake
        os.environ["GITHUB_TOKEN"] = "ghs_diagnostic_token_value"
        _code, out = self.capture(fetch.check_models)
        self.assertIn("::warning title=Summaries unavailable::", out)
        written = Path(handle.name).read_text(encoding="utf-8")
        self.assertIn("### Summaries", written)
        self.assertIn("x-github-request-id=gh-req", written)
        self.assertIn("retired on 30 July 2026", written)
        self.assertNotIn("ghs_diagnostic_token_value", written)


if __name__ == "__main__":
    unittest.main()
