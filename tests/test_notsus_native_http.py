import base64
import http.server
import json
import os
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
PROBE = Path(sys.argv[2]).resolve()
ROOT.mkdir(mode=0o700)

for mode in [
    "complete",
    "malformed_json",
    "missing_schema",
    "duplicate_identity",
    "partial_utf8",
    "invalid_text",
    "invalid_usage",
    "usage_extra",
    "duplicate_encoding",
    "unsupported_encoding",
    "obs_text_header",
    "invalid_header_name",
    "sampler_scaled",
    "sampler_unit",
    "sampler_mismatch",
    "sampler_unit_mismatch",
    "sampler_zero",
    "sampler_null",
    "sampler_length",
    "sampler_positive",
    "sampler_nonfinite",
    "sampler_token",
    "sampler_response",
]:
    out = ROOT / mode
    out.mkdir(mode=0o700)
    captured = []
    sentinel = "PRIVATE_NOTSUS_NATIVE_SENTINEL"
    requested_temperature = (
        1.0
        if mode == "sampler_unit"
        else 0.2
        if mode.startswith("sampler_") and mode not in ["sampler_zero", "sampler_null"]
        else 0.0
    )
    sampling_evidence = {
        "policy_revision": "checkpoint",
        "tokenizer_revision": "tokenizer",
        "chat_template": "template",
        "enable_thinking": False,
        "max_new_tokens": 512,
        "max_sequence_length": 1024,
        "sampling_seed": 7,
        "eos_token_ids": [2],
        "prompt_token_ids": [10],
        "completion_token_ids": [12, 2],
        "behavior_log_probs": [-0.2, -0.3],
        "stop_reason": "eos",
        "response": sentinel,
        "sampling": "full_softmax_temperature_one"
        if mode in ["sampler_unit", "sampler_unit_mismatch"]
        else "full_softmax",
    }
    if sampling_evidence["sampling"] == "full_softmax":
        sampling_evidence["temperature"] = (
            0.7 if mode == "sampler_mismatch" else requested_temperature
        )
    if mode == "sampler_length":
        sampling_evidence["behavior_log_probs"] = [-0.2]
    elif mode == "sampler_positive":
        sampling_evidence["behavior_log_probs"] = [0.2, -0.3]
    elif mode == "sampler_nonfinite":
        sampling_evidence["behavior_log_probs"] = [float("-inf"), -0.3]
    elif mode == "sampler_token":
        sampling_evidence["completion_token_ids"] = [-1, 2]
    elif mode == "sampler_response":
        sampling_evidence["response"] = "PRIVATE_MISMATCHED_SAMPLE"
    if mode == "sampler_null":
        sampling_evidence = None
    body = (
        json.dumps(
            {
                "content": [{"type": "text", "text": sentinel}],
                "sampling_evidence": sampling_evidence,
            }
        ).encode()
        if mode.startswith("sampler_")
        else {
            "complete": json.dumps(
                {
                    "content": [{"type": "text", "text": sentinel}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                }
            ).encode(),
            "malformed_json": (sentinel + " not JSON").encode(),
            "missing_schema": json.dumps({"wrong": sentinel}).encode(),
            "duplicate_identity": json.dumps(
                {"content": [{"type": "text", "text": sentinel}]}
            ).encode(),
            "partial_utf8": b"\xe2\x82",
            "invalid_text": json.dumps(
                {"content": [{"type": "text", "text": {"private": sentinel}}]}
            ).encode(),
            "invalid_usage": json.dumps(
                {
                    "content": [{"type": "text", "text": sentinel}],
                    "usage": {"input_tokens": sentinel, "output_tokens": 2},
                }
            ).encode(),
            "usage_extra": json.dumps(
                {
                    "content": [{"type": "text", "text": sentinel}],
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "private": sentinel,
                    },
                }
            ).encode(),
            "duplicate_encoding": json.dumps(
                {"content": [{"type": "text", "text": sentinel}]}
            ).encode(),
            "unsupported_encoding": json.dumps(
                {"content": [{"type": "text", "text": sentinel}]}
            ).encode(),
            "obs_text_header": json.dumps(
                {"content": [{"type": "text", "text": sentinel}]}
            ).encode(),
            "invalid_header_name": json.dumps(
                {"content": [{"type": "text", "text": sentinel}]}
            ).encode(),
        }[mode]
    )
    body = body.replace(b"-Infinity", b"-1e309")
    call = str(uuid.uuid4())

    class Fixture(http.server.BaseHTTPRequestHandler):
        def do_POST(self, captured=captured, mode=mode, body=body, call=call):
            request = self.rfile.read(int(self.headers["Content-Length"]))
            captured.append(
                {
                    "request": json.loads(request),
                    "slot": self.headers["X-Coworld-Player-Slot"],
                }
            )
            self.send_response(200)
            self.send_header("X-Softmax-Llm-Call-Id", call)
            if mode == "obs_text_header":
                self.send_header(
                    "X-Private-Observed", "\xffPRIVATE_NOTSUS_NATIVE_SENTINEL"
                )
            if mode == "duplicate_identity":
                self.send_header("X-Softmax-Llm-Call-Id", str(uuid.uuid4()))
            if mode == "invalid_header_name":
                self.send_header("X-\xff", "PRIVATE_NOTSUS_NATIVE_SENTINEL")
            if mode == "duplicate_encoding":
                self.send_header("Content-Encoding", "identity")
                self.send_header("Content-Encoding", "identity")
            elif mode == "unsupported_encoding":
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *_):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        owner = threading.Thread(target=server.serve_forever)
        owner.start()
        try:
            result = subprocess.run(
                [str(PROBE), str(out / "native.jsonl"), mode],
                check=False,
                env={
                    **os.environ,
                    "COWORLD_LLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                    "COWORLD_LLM_MODEL": "fixture/native",
                    "COWORLD_LLM_TEMPERATURE": str(requested_temperature),
                    "COWORLD_PLAYER_ARTIFACT_UPLOAD_URL": (
                        out / "artifact.zip"
                    ).as_uri(),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            assert sentinel not in result.stdout + result.stderr
            records = [
                json.loads(x) for x in (out / "native.jsonl").read_text().splitlines()
            ]
            assert (
                records[0]["raw_response"] is None
                and records[0]["platform_call_id"] is None
            )
            attempt = [x for x in records if x["kind"] == "native_generation"][-1]
            assert base64.b64decode(attempt["response_body_b64"], validate=True) == body
            assert attempt["response_reader_joined"] is True
            assert attempt["raw_response"] == (
                None if mode == "partial_utf8" else body.decode()
            )
            assert (
                captured[0]["slot"] == "3"
                and captured[0]["request"]["max_tokens"] == 512
                and captured[0]["request"]["temperature"] == requested_temperature
            )
            with zipfile.ZipFile(out / "artifact.zip") as archive:
                assert (
                    archive.read("native.jsonl") == (out / "native.jsonl").read_bytes()
                )
            assert records[-1]["status"] == "truncated"
            if mode in [
                "complete",
                "usage_extra",
                "obs_text_header",
                "sampler_scaled",
                "sampler_unit",
                "sampler_null",
            ]:
                assert (
                    attempt["response_text"] == sentinel
                    and attempt["platform_call_id"] == call
                )
                if mode == "obs_text_header":
                    assert b"\xffPRIVATE_NOTSUS_NATIVE_SENTINEL" in base64.b64decode(
                        attempt["response_headers_b64"], validate=True
                    )
                    assert attempt["response_headers"]["x-private-observed"] == (
                        "\xffPRIVATE_NOTSUS_NATIVE_SENTINEL"
                    )
            else:
                assert attempt["error_kind"] is not None
            if mode == "sampler_nonfinite":
                assert "response" not in attempt
            elif mode.startswith("sampler_"):
                assert attempt["response"]["sampling_evidence"] == sampling_evidence
            print(
                mode,
                "actual bytes/request/header retained; reader joined; private ZIP recovered; public sentinel absent",
                flush=True,
            )
        finally:
            server.shutdown()
            owner.join()
