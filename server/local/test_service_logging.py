import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from common import service_log


class ServiceLoggingTests(unittest.TestCase):
    def test_access_log_is_detailed_and_written_to_permanent_local_file(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Flask('access-log-test')

            @app.get('/api/example/<project_id>')
            def example(project_id):
                return {'project_id': project_id}

            with patch.object(service_log, 'LOCAL_LOG_ROOT', Path(temp)), \
                    patch.object(service_log, 'COLLECTOR', ''):
                service_log.install_http_access(app)
                response = app.test_client().get(
                    '/api/example/p2_demo',
                    headers={'X-Request-ID': 'request-demo'})

            self.assertEqual(response.headers['X-Request-ID'], 'request-demo')
            files = list(Path(temp).glob('**/*.jsonl'))
            self.assertTrue(files)
            payloads = [
                json.loads(line)
                for path in files
                for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip()
            ]
            request_log = next(item for item in payloads if item['category'] == 'api')
            self.assertEqual(request_log['request_id'], 'request-demo')
            self.assertEqual(request_log['status_code'], 200)
            self.assertEqual(request_log['method'], 'GET')
            self.assertEqual(request_log['path'], '/api/example/p2_demo')
            self.assertGreaterEqual(request_log['duration_ms'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
