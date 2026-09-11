"""Local OpenAI-compatible fixture for research browser regression tests.

Run only against an isolated test backend. No requests leave this process.
"""

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'data': [{
            'id': 'study-model', 'name': 'Study model',
            'object': 'model', 'owned_by': 'openai',
        }]}).encode())

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self.send_response(200)
        if data.get('stream'):
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            slow = 'STOP' in str(data.get('messages', []))
            content = 'A slow response ' * 30 if slow else 'A helpful research response with evidence.'
            try:
                # Exercise the same frontend timing event used by plan sessions.
                # Cypress stubs timing persistence; this fixture creates no plan data.
                event = {'event': {'type': 'chat:completion', 'data': {
                    'experiment_request_id': str(uuid.uuid4()),
                }}}
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.flush()
                for word in content.split(' '):
                    chunk = {'id': 'fixture-response', 'choices': [{
                        'index': 0, 'delta': {'content': word + ' '}, 'finish_reason': None,
                    }]}
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                    self.wfile.flush()
                    time.sleep(0.2 if slow else 0.04)
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'id': 'fixture-response', 'choices': [{
                    'index': 0, 'message': {
                        'role': 'assistant', 'content': 'A helpful research response with evidence.',
                    }, 'finish_reason': 'stop',
                }], 'usage': {'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18},
            }).encode())


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 18081), Handler).serve_forever()
