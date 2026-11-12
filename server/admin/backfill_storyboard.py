"""Regenerate managed_runtime for templates imported before storyboard support.

Run on the admin node (the only node holding the original template asset
revisions). For every template whose managed_runtime lacks ``storyboard_url``,
rebuild the runtime snapshot from its asset folder, then patch existing project
snapshots so in-flight orders also recover without being recreated.

Dry run (no writes)::
    python /app/server/admin/backfill_storyboard.py

Commit::
    python /app/server/admin/backfill_storyboard.py --commit
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy import inspect

from admin.store import AdminStore
from common.media_refs import transform
from phase2.runtime_templates import runtime_from_folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Write repaired payloads")
    args = parser.parse_args()

    root = Path(os.getenv("ADMIN_TEMPLATE_ROOT", "/var/lib/hejian-lvpai/templates"))
    store = AdminStore(os.environ["AITRAVEL_DATABASE_URL"])

    storyboard_by_code = {}
    template_report = []
    with store.engine.connect() as db:
        rows = list(db.execute(text("SELECT template_code,payload_json FROM phase2_templates")).mappings())
    for row in rows:
        code = row["template_code"]
        item = json.loads(row["payload_json"])
        mr = item.get("managed_runtime")
        if not isinstance(mr, dict):
            continue
        if mr.get("storyboard_url"):
            storyboard_by_code[code] = mr["storyboard_url"]
            continue
        revision = item.get("asset_revision") or ""
        folder = root / revision
        if not revision or not folder.is_dir():
            template_report.append({"template_code": code, "status": "skip", "reason": "asset folder missing"})
            continue
        def media_url(_folder, name):
            return "local-media:assets/managed/" + revision + "/" + quote(name, safe="")
        try:
            rebuilt = runtime_from_folder(item, folder, media_url)
        except Exception as exc:
            template_report.append({"template_code": code, "status": "failed", "reason": str(exc)})
            continue
        rebuilt["template_code"] = code
        if args.commit:
            item["managed_runtime"] = rebuilt
            with store.engine.begin() as db:
                db.execute(text("UPDATE phase2_templates SET payload_json=:p WHERE template_code=:c"),
                           {"p": json.dumps(item, ensure_ascii=False), "c": code})
        storyboard_by_code[code] = rebuilt.get("storyboard_url") or ""
        template_report.append({"template_code": code, "status": "repaired" if args.commit else "preview",
                                "storyboard_url": rebuilt.get("storyboard_url") or ""})

    project_report = []
    if storyboard_by_code:
        with store.engine.connect() as db:
            rows = (list(db.execute(text("SELECT id,payload FROM phase2_projects")).mappings())
                    if inspect(db).has_table("phase2_projects") else [])
        for row in rows:
            project = json.loads(row["payload"])
            snapshot = project.get("runtime_snapshot")
            code = project.get("template_code") or ""
            source = storyboard_by_code.get(code) or ""
            if not isinstance(snapshot, dict) or not source:
                continue
            if snapshot.get("storyboard_url"):
                continue
            full = transform(source)
            if args.commit:
                snapshot["storyboard_url"] = full
                template_copy = project.get("template") or {}
                if isinstance(template_copy, dict) and isinstance(template_copy.get("managed_runtime"), dict):
                    template_copy["managed_runtime"]["storyboard_url"] = source
                with store.engine.begin() as db:
                    db.execute(text("UPDATE phase2_projects SET payload=:p WHERE id=:i"),
                               {"p": json.dumps(project, ensure_ascii=False), "i": row["id"]})
            project_report.append({"project_id": row["id"], "template_code": code,
                                   "status": "repaired" if args.commit else "preview"})

    print(json.dumps({"templates": template_report, "projects": project_report}, ensure_ascii=False, indent=2))
    if not args.commit:
        print("Dry run complete. Re-run with --commit to persist.")


if __name__ == "__main__":
    main()
