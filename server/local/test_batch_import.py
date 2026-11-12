"""Batch-import regression tests without the web admin dependencies."""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import text

from admin.batch_import import import_batch
from admin.store import AdminStore
from template_fixture import create_template_folder


ROOT = Path(__file__).resolve().parents[2]


class BatchImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'incoming'
        self.source.mkdir()
        original = create_template_folder(self.root / 'fixture', '测试模板')
        self.sample = shutil.copytree(original, self.source / original.name)
        prompt = self.sample / '11-定妆照提示词.txt'
        prompt.write_bytes(b'\xef\xbb\xbf' + prompt.read_bytes())
        (self.sample / 'Thumbs.db').write_bytes(b'Windows thumbnail cache')
        self.assets = self.root / 'assets'
        self.store = AdminStore('sqlite:///' + str(self.root / 'admin.db'))
        self.addCleanup(self.store.engine.dispose)

    def test_precheck_and_repeat_are_non_destructive(self):
        bad = self.source / 'broken'
        bad.mkdir()
        (bad / '6-cover.jpg').write_bytes(b'bad')
        (bad / '7-example.mp4').write_bytes(b'bad')
        checked = import_batch(self.source, self.assets)
        self.assertEqual(checked['summary']['valid'], 1)
        self.assertEqual(checked['summary']['failed'], 1)
        self.assertFalse(self.assets.exists())
        first = import_batch(self.source, self.assets, self.store, commit=True)
        self.assertEqual(first['summary']['imported'], 1)
        imported = next(row for row in first['templates'] if row['status'] == 'imported')
        self.assertIn('已上架', imported['reason'])
        with self.store.engine.connect() as db:
            old = json.loads(db.execute(text('SELECT payload_json FROM phase2_templates')).scalar())
        entry = self.store.list_templates()['items'][0]
        self.assertEqual((entry['status'], entry['heat_base'], entry['show_heat']), (1,10000,True))
        second = import_batch(self.source, self.assets, self.store, commit=True)
        self.assertEqual(second['summary']['skipped'], 1)
        with self.store.engine.connect() as db:
            new = json.loads(db.execute(text('SELECT payload_json FROM phase2_templates')).scalar())
        self.assertEqual(old['asset_revision'], new['asset_revision'])
        self.assertEqual(len(list(self.assets.iterdir())), 1)

    def test_same_name_in_two_folders_is_reported_before_import(self):
        shutil.copytree(self.sample, self.source / 'another-folder')
        report = import_batch(self.source, self.assets)
        self.assertEqual(report['summary']['valid'], 1)
        self.assertEqual(report['summary']['skipped'], 1)

    def test_zip_and_matching_folder_import_once(self):
        archive_path = self.source / (self.sample.name + '.zip')
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for asset in self.sample.iterdir():
                if asset.is_file():
                    archive.write(asset, self.sample.name + '/' + asset.name)
        report = import_batch(self.source, self.assets)
        self.assertEqual(report['summary']['valid'], 1)
        self.assertEqual(report['summary']['skipped'], 1)

    def test_scene_and_role_filename_variants(self):
        scene = next(self.sample.glob('2.1-*'))
        scene.rename(scene.with_name(scene.name.replace('2.1-', '2-', 1)))
        actor = next(self.sample.glob('3.1-*'))
        actor.rename(actor.with_name(actor.stem + ' ' + actor.suffix))
        report = import_batch(self.source, self.assets)
        self.assertEqual(report['summary']['valid'], 1, report['templates'])


if __name__ == '__main__':
    unittest.main()
