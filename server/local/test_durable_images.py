import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from flask import Flask
from common.task_journal import TaskJournal,CloudFailed
from common.durable_images import install


class ImageRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.j=TaskJournal('sqlite:///'+str(Path(self.temp.name)/'images.db')); self.addCleanup(self.j.engine.dispose)
        self.provider=Mock(return_value={'provider_url':'https://provider/image','model':'model','size':'800x1424'})
        self.upload=Mock(return_value='https://local/media/token/image.jpg')
        app=Flask(__name__)
        app.add_url_rule('/generate','generate_image',lambda:None,methods=['POST'])
        with patch('common.durable_images.TaskJournal',return_value=self.j): install(app,self.provider,self.upload)
        self.client=app.test_client()
        self.body={'request_id':'fixed-request','user_id':'run','prompt':'photo','reference_image_urls':['face'],'aspect_ratio':'9:16'}

    def test_storage_failure_reuses_paid_response(self):
        self.upload.side_effect=[RuntimeError('file failed'),'stored-image']
        with patch('common.durable_images.requests.get',return_value=Mock(content=b'image',raise_for_status=lambda:None)):
            self.assertEqual(self.client.post('/generate',json=self.body).status_code,503)
            r=self.client.post('/generate',json=self.body)
            self.assertEqual(r.status_code,200); self.assertEqual(r.json['data']['image_url'],'stored-image')
        self.provider.assert_called_once()

    def test_unknown_provider_response_is_not_retried(self):
        self.provider.side_effect=TimeoutError()
        self.assertEqual(self.client.post('/generate',json=self.body).status_code,409)
        self.assertEqual(self.client.post('/generate',json=self.body).status_code,409)
        self.provider.assert_called_once(); self.upload.assert_not_called()

    def test_explicit_rejection_is_stable_and_different_from_unknown(self):
        self.provider.side_effect=CloudFailed()
        self.assertEqual(self.client.post('/generate',json=self.body).status_code,422)
        self.assertEqual(self.client.post('/generate',json=self.body).status_code,422)
        self.provider.assert_called_once()

    def test_preflight_validation_error_is_failed_not_needs_review(self):
        self.provider.side_effect=ValueError('reference URL is invalid')
        first=self.client.post('/generate',json=self.body)
        second=self.client.post('/generate',json=self.body)
        self.assertEqual(first.status_code,422,first.data)
        self.assertEqual(second.status_code,422,second.data)
        self.provider.assert_called_once()
        self.upload.assert_not_called()


if __name__=='__main__': unittest.main()
