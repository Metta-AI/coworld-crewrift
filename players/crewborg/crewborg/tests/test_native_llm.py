import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from crewborg.strategy.commander.llm import build_commander_client_from_env
from crewborg.strategy.meeting.llm import build_meeting_llm_client_from_env


def test_native_sidecar_drives_meeting_and_commander(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, dict(self.headers), request))
            decision = {'schema_version': 1, 'action': 'wait'}
            if 'target_room' in json.loads(request['messages'][0]['content'])['response_schema']:
                decision = {'schema_version': 1, 'target_room': None, 'reason': 'hold'}
            response = json.dumps({
                'id': 'msg_native', 'type': 'message', 'role': 'assistant',
                'model': request['model'], 'stop_reason': 'end_turn', 'stop_sequence': None,
                'content': [{'type': 'text', 'text': json.dumps(decision)}],
                'usage': {'input_tokens': 10, 'output_tokens': 3},
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        'COWORLD_LLM_ENDPOINT': f'http://127.0.0.1:{server.server_port}',
        'COWORLD_LLM_MODEL': 'anthropic/claude-haiku-4.5',
        'CREWBORG_LLM_MODEL': 'retired-model',
        'CREWBORG_LLM_MEETINGS': '1', 'CREWBORG_LLM_COMMANDER': '1',
        'USE_BEDROCK': '1', 'ANTHROPIC_API_KEY': 'local-key-must-not-be-sent',
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    try:
        meeting = build_meeting_llm_client_from_env()
        commander = build_commander_client_from_env()
        assert meeting.enabled and commander.enabled
        assert meeting.decide({'self': {'role': 'crewmate'}}, trigger='meeting_start').decision.action == 'wait'
        assert commander.decide({'self': {'role': 'crewmate'}}).priorities['reason'] == 'hold'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert len(requests) == 2
    for path, headers, body in requests:
        assert path == '/v1/messages'
        assert body['model'] == 'anthropic/claude-haiku-4.5'
        assert 'anthropic_version' not in body
        assert 'local-key-must-not-be-sent' not in headers.values()
