"""Reference-aware template cleanup. Operational logs are never deleted."""
import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path
from sqlalchemy import inspect,text
from common.database_schema import engine_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    engine=engine_for(os.environ['AITRAVEL_DATABASE_URL'])
    root=Path(os.environ['ADMIN_TEMPLATE_ROOT']).resolve()
    referenced=set(); cutoff=time.time()-30*86400
    try:
        with engine.begin() as db:
            tables=set(inspect(db).get_table_names())
            # Include old project snapshots and task checkpoints, even completed ones.
            for table,column in [('phase2_templates','payload_json'),('phase2_projects','payload'),('phase2_tasks','payload')]:
                if table not in tables: continue
                for row in db.execute(text('SELECT '+column+' FROM '+table)):
                    referenced.update(re.findall(r'\b[0-9a-f]{32}\b',row[0]))
            candidates=[p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch('[0-9a-f]{32}',p.name)
                        and p.name not in referenced and p.stat().st_mtime<cutoff]
            print(json.dumps(dict(
                mode='apply' if args.apply else 'preview',
                unreferenced_template_revisions=[p.name for p in candidates],
                operational_log_retention='permanent'),ensure_ascii=False))
            if args.apply:
                for path in candidates:
                    if path.resolve().parent!=root: raise RuntimeError('Invalid cleanup target')
                    shutil.rmtree(path)
    finally: engine.dispose()


if __name__=='__main__':main()
