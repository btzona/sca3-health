#!/usr/bin/env python3
"""Offline tests for the summary client. No real tokens or model hosts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
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
    "SCA3_SUMMARY_PACE",
    "GEMINI_API_KEY",
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
        self._summaries_out = fetch.SUMMARIES_OUT
        self._env = {name: os.environ.get(name) for name in ENV_NAMES}
        for name in ENV_NAMES:
            os.environ.pop(name, None)
        os.environ["SCA3_SUMMARY_BACKOFF"] = "0"
        os.environ["SCA3_SUMMARY_PACE"] = "0"
        fetch._models_failure_logged = False
        fetch._models_stopped = False
        fetch._summaries_note_logged = False
        fetch._last_summary_post_at = None
        fetch._resolved_model = None
        fetch._resolved_model_key = None

    def tearDown(self):
        fetch.perform_models_post = self._perform
        fetch.SUMMARIES_OUT = self._summaries_out
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        fetch._models_failure_logged = False
        fetch._models_stopped = False
        fetch._summaries_note_logged = False
        fetch._last_summary_post_at = None
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
        os.environ["SCA3_SUMMARY_URL"] = "https://models.github.ai/inference/chat/completions"
        os.environ["GITHUB_TOKEN"] = "ghs_diagnostic_token_value"
        code, out = self.capture(fetch.check_models)
        self.assertEqual(code, 0)
        self.assertIn("HTTP 200", out)
        self.assertIn("content-type=text/plain", out)
        self.assertIn("request-id=(none)", out)
        self.assertIn("retired on 30 July 2026", out)
        self.assertNotIn("ghs_diagnostic_token_value", out)

    def test_missing_keys_log_one_friendly_note(self):
        def fake(*_args, **_kwargs):
            raise AssertionError("endpoint should not be called")

        fetch.perform_models_post = fake
        os.environ["GITHUB_TOKEN"] = "ghs_diagnostic_token_value"

        def run_twice():
            return fetch.check_models(), fetch.check_models()

        (first, second), out = self.capture(run_twice)
        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(out.count("Summaries are paused"), 1)
        self.assertIn("GEMINI_API_KEY", out)
        self.assertIn("Settings → Secrets and variables → Actions", out)
        self.assertNotIn("::warning", out)
        self.assertNotIn("::error", out)
        self.assertNotIn("ghs_diagnostic_token_value", out)
        self.assertNotIn("retired on 30 July 2026", out)

    def test_blank_summary_url_is_unset(self):
        os.environ["SCA3_SUMMARY_URL"] = "  "
        self.assertEqual(fetch.summary_url(), "")
        self.assertFalse(fetch.summaries_configured())
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"
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
        os.environ["SCA3_SUMMARY_URL"] = "https://models.github.ai/inference/chat/completions"
        os.environ["GITHUB_TOKEN"] = "ghs_diagnostic_token_value"
        _code, out = self.capture(fetch.check_models)
        self.assertIn("::warning title=Summaries unavailable::", out)
        written = Path(handle.name).read_text(encoding="utf-8")
        self.assertIn("### Summaries", written)
        self.assertIn("x-github-request-id=gh-req", written)
        self.assertIn("retired on 30 July 2026", written)
        self.assertNotIn("ghs_diagnostic_token_value", written)

    def test_gemini_key_defaults_url_model_and_bearer(self):
        os.environ["GEMINI_API_KEY"] = "gemini-test-key-abcdef"
        os.environ["GITHUB_TOKEN"] = "ghs_should_not_be_sent_xxxxx"
        os.environ["SCA3_SUMMARY_API_KEY"] = "foundry-key-abcdefghij"
        self.assertEqual(fetch.summary_url(), fetch.GEMINI_SUMMARY_URL)
        self.assertEqual(fetch.summary_model_name(), fetch.GEMINI_SUMMARY_MODEL)
        self.assertTrue(fetch.summaries_configured())
        token, extra = fetch.summary_auth(fetch.summary_url())
        self.assertEqual(token, "gemini-test-key-abcdef")
        self.assertEqual(extra, {})
        headers = fetch.models_request_headers(fetch.summary_url(), token, extra)
        self.assertEqual(headers["Authorization"], "Bearer gemini-test-key-abcdef")
        self.assertNotIn("api-key", headers)
        self.assertEqual(headers["Accept"], "application/json")
        self.assertNotIn("X-GitHub-Api-Version", headers)
        seen = []

        def fake(url, body, used_token, used_extra):
            seen.append((url, used_token, used_extra, json.loads(body.decode())))
            return 200, COMPLETION, {"Content-Type": "application/json"}, url

        fetch.perform_models_post = fake
        code, out = self.capture(fetch.check_models)
        self.assertEqual(code, 0)
        self.assertEqual(seen[0][0], fetch.GEMINI_SUMMARY_URL)
        self.assertEqual(seen[0][1], "gemini-test-key-abcdef")
        self.assertEqual(seen[0][2], {})
        self.assertEqual(set(seen[0][3]), {"model", "messages"})
        self.assertEqual(seen[0][3]["model"], "gemini-3.8-flash")
        self.assertNotIn("temperature", seen[0][3])
        self.assertNotIn("max_tokens", seen[0][3])
        self.assertNotIn("gemini-test-key-abcdef", out)
        self.assertNotIn("ghs_should_not_be_sent_xxxxx", out)
        self.assertNotIn("foundry-key-abcdefghij", out)
        self.assertIn("Summary endpoint check passed", out)

    def test_gemini_model_override_and_explicit_url(self):
        os.environ["GEMINI_API_KEY"] = "gemini-test-key-abcdef"
        os.environ["SCA3_SUMMARY_MODEL"] = "gemini-2.5-pro"
        self.assertEqual(fetch.summary_url(), fetch.GEMINI_SUMMARY_URL)
        self.assertEqual(fetch.summary_model_name(), "gemini-2.5-pro")
        payload = fetch.chat_completion_payload(
            fetch.resolve_summary_model(),
            [{"role": "user", "content": "Reply with the single word pong."}],
            max_tokens=16,
        )
        self.assertEqual(set(payload), {"model", "messages"})
        self.assertEqual(payload["model"], "gemini-2.5-pro")

        os.environ["SCA3_SUMMARY_URL"] = "https://example.test/chat/completions"
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"
        os.environ["SCA3_SUMMARY_MODEL"] = "custom-deploy"
        fetch._resolved_model = None
        fetch._resolved_model_key = None
        self.assertEqual(fetch.summary_url(), "https://example.test/chat/completions")
        self.assertEqual(fetch.summary_model_name(), "custom-deploy")
        token, extra = fetch.summary_auth(fetch.summary_url())
        self.assertEqual(token, "provider-token-abcdefgh")
        self.assertNotEqual(token, "gemini-test-key-abcdef")
        other = fetch.chat_completion_payload("custom-deploy", [{"role": "user", "content": "hi"}], max_tokens=16)
        self.assertEqual(other["temperature"], 0)
        self.assertEqual(other["max_tokens"], 16)

    def test_rate_limit_wait_respects_retry_after_or_backs_off(self):
        os.environ.pop("SCA3_SUMMARY_BACKOFF", None)
        self.assertEqual(fetch.retry_wait_seconds(0, 429, {"Retry-After": "2"}), 15.0)
        self.assertEqual(fetch.retry_wait_seconds(0, 429, {"Retry-After": "22"}), 22.0)
        self.assertEqual(fetch.retry_wait_seconds(0, 429, {"Retry-After": "90"}), 60.0)
        self.assertEqual(fetch.retry_wait_seconds(0, 429, {}), 15.0)
        self.assertEqual(fetch.retry_wait_seconds(1, 429, {}), 30.0)
        self.assertEqual(fetch.retry_wait_seconds(0, 503, {}), fetch.SUMMARY_BACKOFF_SECONDS[0])
        os.environ["SCA3_SUMMARY_BACKOFF"] = "0"
        self.assertEqual(fetch.retry_wait_seconds(0, 429, {"Retry-After": "22"}), 0.0)

    def test_429_is_retried_after_the_long_backoff(self):
        os.environ.pop("SCA3_SUMMARY_BACKOFF", None)
        os.environ["SCA3_SUMMARY_URL"] = "https://example.test/chat/completions"
        os.environ["SCA3_SUMMARY_TOKEN"] = "provider-token-abcdefgh"
        responses = [
            (429, b'{"error":"slow down"}', {"Content-Type": "application/json", "Retry-After": "18"}),
            (200, COMPLETION, {"Content-Type": "application/json"}),
        ]

        def fake(url, body, token, extra):
            status, raw, headers = responses.pop(0)
            return status, raw, headers, url

        sleeps = []
        original_sleep = time.sleep
        time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            fetch.perform_models_post = fake
            parsed, out = self.capture(lambda: fetch.post_chat_completion({"model": "m", "messages": []}))
        finally:
            time.sleep = original_sleep
        self.assertEqual(parsed["choices"][0]["message"]["content"], "pong")
        self.assertEqual(sleeps, [18.0])
        self.assertEqual(responses, [])
        self.assertEqual(out, "")
        self.assertFalse(fetch._models_stopped)

    def test_gemini_paces_between_summary_requests(self):
        os.environ.pop("SCA3_SUMMARY_PACE", None)
        os.environ["GEMINI_API_KEY"] = "gemini-test-key-abcdef"
        calls = []

        def fake(url, body, token, extra):
            calls.append(url)
            return 200, COMPLETION, {"Content-Type": "application/json"}, url

        fetch.perform_models_post = fake
        clock = {"t": 50.0}
        sleeps = []
        original_sleep = time.sleep
        original_mono = time.monotonic

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["t"] += seconds

        time.sleep = fake_sleep
        time.monotonic = lambda: clock["t"]
        try:
            first = fetch.post_chat_completion({"model": "gemini-3.8-flash", "messages": []})
            second = fetch.post_chat_completion({"model": "gemini-3.8-flash", "messages": []})
        finally:
            time.sleep = original_sleep
            time.monotonic = original_mono
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [fetch.GEMINI_PACE_SECONDS])
        self.assertGreaterEqual(fetch.GEMINI_PACE_SECONDS, 15.0)

    def test_paused_fill_summaries_does_not_call_out(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("summaries should stay offline when no key is set")

        original_lookup = fetch.europepmc_by_doi
        self.addCleanup(setattr, fetch, "europepmc_by_doi", original_lookup)
        fetch.perform_models_post = boom
        fetch.europepmc_by_doi = boom
        with tempfile.TemporaryDirectory() as tmp:
            fetch.SUMMARIES_OUT = Path(tmp) / "summaries.json"
            _ignored, out = self.capture(
                lambda: fetch.fill_summaries(
                    [
                        {
                            "id": "10.1000/example",
                            "link": "https://doi.org/10.1000/example",
                            "title": "A paper",
                        }
                    ]
                )
            )
        self.assertIn("Summaries are paused", out)
        self.assertIn("GEMINI_API_KEY", out)
        self.assertNotIn("::warning", out)
        self.assertIn("Summaries unchanged", out)


if __name__ == "__main__":
    unittest.main()
