import base64
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
PROBE = Path(sys.argv[2]).resolve()
ROOT.mkdir(mode=0o700)

for mode in ["phase_cancel", "SIGTERM", "SIGINT"]:
    out = ROOT / ("cancel-" + mode)
    out.mkdir(mode=0o700)
    calls = []
    closed = threading.Event()
    body = b"\xe2\x82"

    class Fixture(http.server.BaseHTTPRequestHandler):
        def do_POST(self, calls=calls, body=body, out=out, closed=closed):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            self.send_response(200)
            self.send_header(
                "X-Softmax-Llm-Call-Id", "observed-fixture-" + str(len(calls))
            )
            if len(calls) == 1:
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                (out / "request-started").touch()
                self.connection.settimeout(4)
                assert self.rfile.read(1) == b""
                closed.set()
            else:
                complete = json.dumps(
                    {"content": [{"type": "text", "text": "later phase"}]}
                ).encode()
                self.send_header("Content-Length", str(len(complete)))
                self.end_headers()
                self.wfile.write(complete)
                self.wfile.flush()

        def log_message(self, *_):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        owner = threading.Thread(target=server.serve_forever)
        owner.start()
        process = subprocess.Popen(
            [str(PROBE), str(out / "native.jsonl"), mode],
            env={
                **os.environ,
                "COWORLD_LLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                "COWORLD_LLM_MODEL": "fixture/native",
                "COWORLD_LLM_TEMPERATURE": "0",
                "COWORLD_PLAYER_ARTIFACT_UPLOAD_URL": (out / "artifact.zip").as_uri(),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if mode != "phase_cancel":
                deadline = time.monotonic() + 5
                while not (out / "request-started").exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.001)
                time.sleep(0.03)
                started = time.monotonic()
                process.send_signal(getattr(signal, mode))
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            assert closed.wait(1)
            if mode != "phase_cancel":
                assert time.monotonic() - started < 2.5
            records = [
                json.loads(x) for x in (out / "native.jsonl").read_bytes().splitlines()
            ]
            first = [
                x
                for x in records
                if x["kind"] == "native_generation" and x["tag"] == "first"
            ][-1]
            assert (
                first["transfer_kind"] in ["nhCanceled", "nhInterrupted"]
                and first["response_reader_joined"]
            )
            assert (
                base64.b64decode(first["response_body_b64"]) == body
                and first["raw_response"] is None
                and not first["response_complete"]
            )
            assert len(calls) == (2 if mode == "phase_cancel" else 1)
            assert (
                records[-1]["status"] == "truncated"
                and records[-1]["requests_joined"]
                and records[-1]["cleanup_deadline_met"]
            )
            with zipfile.ZipFile(out / "artifact.zip") as archive:
                assert (
                    archive.read("native.jsonl") == (out / "native.jsonl").read_bytes()
                )
            print(
                mode,
                "actual partial invalid bytes/socket EOF/worker join retained; later-call or global-stop invariant passed",
                flush=True,
            )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
            server.shutdown()
            owner.join()
