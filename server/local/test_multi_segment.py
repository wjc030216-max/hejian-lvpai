"""Two-segment templates, outfit variants and group-scoped recovery."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from admin.batch_import import import_batch
from admin.store import AdminStore
from common.durable_generation import Generation
from common.portrait_provider import _build_role_bindings
from common.task_journal import CloudFailed, TaskJournal
from local.results import save_local_video_segments, validate_final_video
from phase2.runtime_templates import normalize_template, runtime_from_folder
from template_fixture import create_multi_segment_template_folder


class MultiSegmentTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = create_multi_segment_template_folder(self.root)
        self.metadata = json.loads((self.folder / "1-基础元数据.txt").read_text(encoding="utf-8"))

    def _template(self):
        one = self.metadata["module_1"]
        two = self.metadata["module_2"]
        return normalize_template({
            "template_code": "P2B-multi",
            "video_name": one["video_name"],
            "story_hook": one["story_hook"],
            "actor_count": one["actor_count"],
            "character_settings": one["character_settings"],
            "relationship_tags": two["relationship_tags"],
            "video_genre": two["video_genre"],
            "video_specification": two["video_specification"],
            "location_association": two["recommendation_persona"]["location_association"],
        })

    def test_runtime_keeps_two_outfit_groups_and_b_portrait(self):
        runtime = runtime_from_folder(self._template(), self.folder, asset_url=lambda _folder, name: name)
        self.assertEqual(runtime["duration_seconds"], 30)
        self.assertEqual(runtime["segment_count"], 2)
        self.assertEqual([item["code"] for item in runtime["segments"]], ["A", "B"])
        self.assertEqual(runtime["portrait_group"], "B")
        self.assertEqual([Path(value).name for value in runtime["segments"][0]["costume_url"]],
                         ["9.1.A-角色服装图-男主角A.png", "9.2.A-角色服装图-女主角A.png"])
        self.assertEqual([Path(value).name for value in runtime["segments"][1]["costume_url"]],
                         ["9.1.B-角色服装图-男主角A.png", "9.2.B-角色服装图-女主角A.png"])
        self.assertEqual(Path(runtime["segments"][1]["storyboard_url"]).name, "10.2-模板分镜图-B.png")

    def test_batch_import_accepts_30_second_two_group_template(self):
        incoming = self.root / "incoming"
        incoming.mkdir()
        shutil.copytree(self.folder, incoming / self.folder.name)
        assets = self.root / "assets"
        store = AdminStore("sqlite:///" + str(self.root / "admin.db"))
        self.addCleanup(store.engine.dispose)
        report = import_batch(incoming, assets, store, commit=True)
        self.assertEqual(report["summary"]["imported"], 1, report)
        item = store.list_templates()["items"][0]
        self.assertEqual(item["video_specification"], "30秒")
        self.assertEqual(item["segment_count"], 2)

    def test_portrait_worker_generates_one_view_per_outfit_and_reuses_face(self):
        runtime = runtime_from_folder(self._template(), self.folder, asset_url=lambda _folder, name: name)
        model = {
            "ids": ["男主角A", "女主角A"],
            "groups": runtime["segments"],
            "scene_urls": runtime["screen_url"],
            "portrait_group": "B",
            "portrait_prompt": runtime["portrait_prompt"],
        }
        with tempfile.TemporaryDirectory() as task_dir:
            journal = TaskJournal("sqlite:///" + str(Path(task_dir) / "tasks.db"))
            self.addCleanup(journal.engine.dispose)
            api = SimpleNamespace(
                logger=logging.getLogger("test"),
                PROMPT_STEP1="换装",
                _build_role_bindings=_build_role_bindings,
                fetch_template_model_by_code=Mock(return_value=model),
                call_internal_api=Mock(side_effect=["view-a1", "view-a2", "view-b1", "view-b2", "portrait"]),
            )
            worker = Generation("character", api, journal)
            self.addCleanup(worker.pool.shutdown)
            actors = [
                {"character_id": "男主角A", "image": "face-1", "source": "user_face"},
                {"character_id": "女主角A", "image": "face-2", "source": "user_face"},
            ]
            task, _ = journal.create("character", "multi-run", {
                "template_code": "P2S-multi", "actors": actors,
            })
            worker.tick(task["task_id"])
            saved = journal.get(task["task_id"])
            self.assertEqual(saved["status"], "completed")
            calls = api.call_internal_api.call_args_list
            self.assertEqual(len(calls), 5)
            self.assertEqual(calls[0].kwargs["reference_urls"], ["face-1", "9.1.A-角色服装图-男主角A.png"])
            self.assertEqual(calls[2].kwargs["reference_urls"], ["face-1", "9.1.B-角色服装图-男主角A.png"])
            self.assertEqual(saved["portrait_group"], "B")
            self.assertEqual(set(saved["role_mapping_by_group"]), {"A", "B"})
            self.assertEqual(saved["role_mapping"][0]["outfit_group"], "B")
            self.assertTrue(saved["outfit_views"]["A"]["男主角A"]["three_view_url"])
            worker.pool.shutdown()
            journal.engine.dispose()

    def test_template_role_uses_the_matching_outfit_for_each_group(self):
        runtime = runtime_from_folder(self._template(), self.folder, asset_url=lambda _folder, name: name)
        model = {
            "ids": ["男主角A", "女主角A"],
            "groups": runtime["segments"],
            "scene_urls": runtime["screen_url"],
            "portrait_group": "B",
            "portrait_prompt": runtime["portrait_prompt"],
        }
        with tempfile.TemporaryDirectory() as task_dir:
            journal = TaskJournal("sqlite:///" + str(Path(task_dir) / "tasks.db"))
            self.addCleanup(journal.engine.dispose)
            api = SimpleNamespace(
                logger=logging.getLogger("test"), PROMPT_STEP1="换装",
                _build_role_bindings=_build_role_bindings,
                fetch_template_model_by_code=Mock(return_value=model),
                call_internal_api=Mock(side_effect=["view-a1", "view-b1", "portrait"]),
            )
            worker = Generation("character", api, journal)
            self.addCleanup(worker.pool.shutdown)
            actors = [
                {"character_id": "男主角A", "image": "face-1", "source": "user_face"},
                {"character_id": "女主角A", "image": "3.2.B-角色设定图-女主角A.png",
                 "source": "template"},
            ]
            task, _ = journal.create("character", "template-role", {
                "template_code": "P2S-multi", "actors": actors,
            })
            worker.tick(task["task_id"])
            saved = journal.get(task["task_id"])
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["role_mapping_by_group"]["A"][1]["three_view_url"],
                             "3.2.A-角色设定图-女主角A.png")
            self.assertEqual(saved["role_mapping_by_group"]["B"][1]["three_view_url"],
                             "3.2.B-角色设定图-女主角A.png")
            worker.pool.shutdown()
            journal.engine.dispose()

    def test_two_video_tasks_retry_only_failed_group(self):
        runtime = runtime_from_folder(self._template(), self.folder, asset_url=lambda _folder, name: name)
        model = {
            "ids": ["男主角A"],
            "groups": [dict(group, actor_url=group["actor_url"][:1],
                            costume_url=group["costume_url"][:1]) for group in runtime["segments"]],
            "scene_urls": runtime["screen_url"],
            "bgm_url": "",
        }
        mapping = [{
            "character_id": "男主角A", "source": "user_face",
            "user_face_url": "face", "three_view_url": "view",
        }]
        with tempfile.TemporaryDirectory() as task_dir:
            journal = TaskJournal("sqlite:///" + str(Path(task_dir) / "tasks.db"))
            self.addCleanup(journal.engine.dispose)
            poll = Mock(side_effect=["source-a", CloudFailed("B 云端失败"), "source-b"])
            api = SimpleNamespace(
                logger=logging.getLogger("test"),
                fetch_template_model=Mock(return_value=model),
                create_asset=Mock(side_effect=lambda url: "asset-" + str(url)),
                wait_asset_active=Mock(return_value=True),
                call_volcano_video=Mock(side_effect=["cloud-a", "cloud-b", "cloud-b2"]),
                poll_volcano_task=poll,
                VIDEO_GENERATE_AUDIO=True, VIDEO_DURATION=15, VIDEO_WATERMARK=False,
            )
            worker = Generation("video", api, journal)
            self.addCleanup(worker.pool.shutdown)
            task, _ = journal.create("video", "multi-run", {
                "template_code": "P2S-multi",
                "character_task_id": "portrait",
                "role_mapping": mapping,
            })
            target = Path(task_dir) / "result.mp4"
            target.write_bytes(b"video")
            worker.tick(task["task_id"])
            failed = journal.get(task["task_id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["failed_group"], "B")
            journal.retry(task["task_id"], "multi-run")
            with patch("local.results.save_local_video_segments", return_value=str(target)), patch(
                    "common.storage_client.upload_result", return_value="stored"):
                worker.tick(task["task_id"])
            saved = journal.get(task["task_id"])
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(api.call_volcano_video.call_count, 3)
            self.assertEqual(saved["steps"]["video_submit:A"]["result"], "cloud-a")
            self.assertEqual(saved["steps"]["video_submit:B"]["result"], "cloud-b2")
            self.assertEqual(saved["video_process"]["phase"], "completed")
            worker.pool.shutdown()
            journal.engine.dispose()

    def test_two_segments_are_joined_with_one_audio_track(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "a.mp4", root / "b.mp4"
            music = root / "music.wav"
            for index, target in enumerate((first, second), start=1):
                subprocess.run([
                    "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={'red' if index == 1 else 'blue'}:s=90x160:d=0.4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
                ], check=True, timeout=30)
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=0.8", str(music),
            ], check=True, timeout=30)
            def download(url, target):
                source = {"a": first, "b": second, "music": music}[url]
                shutil.copyfile(source, target)
            with patch.dict(os.environ, {
                "LOCAL_RESULT_ROOT": str(root / "results"),
                "EXTERNAL_DOMAIN": "http://127.0.0.1:8090",
            }), patch("local.results._download", side_effect=download):
                result = save_local_video_segments(
                    ["a", "b"], "music", "local-user", "multi-task", segment_seconds=0.4,
                    prepare_only=True,
                )
            output = Path(result)
            self.assertTrue(output.is_file())
            info = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,width,height:format=duration", "-of", "json", str(output),
            ]))
            self.assertAlmostEqual(float(info["format"]["duration"]), 0.8, places=2)
            self.assertEqual({item["codec_type"] for item in info["streams"]}, {"video", "audio"})
            video_stream = next(item for item in info["streams"] if item["codec_type"] == "video")
            self.assertEqual((video_stream["width"], video_stream["height"]), (720, 1280))
            validated = validate_final_video(output, 0.8)
            self.assertAlmostEqual(validated["duration"], 0.8, places=2)
            with self.assertRaises(RuntimeError):
                validate_final_video(output, 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
