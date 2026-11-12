"""One idempotent schema upgrade for every phase-two service (MySQL and local SQLite)."""
import hashlib
import json
import time
from sqlalchemy import create_engine, inspect, text
from common.management_schema import SCHEMA, ensure_indexes

VERSION = '20261023-concurrency-v2'


def _ensure_task_lease_columns(db):
    if 'phase2_tasks' not in set(inspect(db).get_table_names()):
        return
    columns = {column['name'] for column in inspect(db).get_columns('phase2_tasks')}
    for name, definition in (
        ('lease_owner', 'VARCHAR(64) NULL'),
        ('lease_until', 'DOUBLE NOT NULL DEFAULT 0'),
    ):
        if name not in columns:
            db.execute(text('ALTER TABLE phase2_tasks ADD COLUMN ' + name + ' ' + definition))


def engine_for(url, **options):
    if url.startswith('mysql'):
        options['connect_args'] = dict(connect_timeout=5, read_timeout=15, write_timeout=15)
    return create_engine(url, pool_pre_ping=True, **options)


def template_identity(scene, name):
    return hashlib.sha256((str(scene).strip() + '\0' + str(name).strip()).encode()).hexdigest()


def ensure_service_log_table(engine):
    """Idempotent, run on every startup so the log collector can add this table
    without triggering the versioned full migration."""
    mysql = engine.dialect.name == 'mysql'
    pk = 'BIGINT PRIMARY KEY AUTO_INCREMENT' if mysql else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    with engine.begin() as db:
        db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_service_logs (
            id ''' + pk + ''', service VARCHAR(64) NOT NULL,
            category VARCHAR(32) NOT NULL, level VARCHAR(16) NOT NULL,
            message TEXT NOT NULL, context TEXT,
            created DOUBLE NOT NULL,
            request_id VARCHAR(64) NOT NULL DEFAULT '',
            project_id VARCHAR(128) NOT NULL DEFAULT '',
            task_id VARCHAR(128) NOT NULL DEFAULT '',
            source_ip VARCHAR(64) NOT NULL DEFAULT '',
            method VARCHAR(16) NOT NULL DEFAULT '',
            path VARCHAR(1024) NOT NULL DEFAULT '',
            status_code INT NOT NULL DEFAULT 0,
            duration_ms DOUBLE NOT NULL DEFAULT 0,
            error_type VARCHAR(128) NOT NULL DEFAULT '')'''))
        cols = {c['name']: c for c in inspect(db).get_columns('phase2_service_logs')}
        if 'context' in cols and 'text' not in str(cols['context']['type']).lower():
            db.execute(text('ALTER TABLE phase2_service_logs MODIFY context TEXT'))
        columns = {
            'request_id': 'VARCHAR(64) NOT NULL DEFAULT \'\'',
            'project_id': 'VARCHAR(128) NOT NULL DEFAULT \'\'',
            'task_id': 'VARCHAR(128) NOT NULL DEFAULT \'\'',
            'source_ip': 'VARCHAR(64) NOT NULL DEFAULT \'\'',
            'method': 'VARCHAR(16) NOT NULL DEFAULT \'\'',
            'path': 'VARCHAR(1024) NOT NULL DEFAULT \'\'',
            'status_code': 'INT NOT NULL DEFAULT 0',
            'duration_ms': 'DOUBLE NOT NULL DEFAULT 0',
            'error_type': 'VARCHAR(128) NOT NULL DEFAULT \'\'',
        }
        for name, definition in columns.items():
            if name not in cols:
                db.execute(text(
                    'ALTER TABLE phase2_service_logs ADD COLUMN '
                    + name + ' ' + definition))
        if mysql:
            existing = {x['name'] for x in inspect(db).get_indexes('phase2_service_logs')}
            if 'idx_svc_log_created' not in existing:
                db.execute(text('CREATE INDEX idx_svc_log_created ON phase2_service_logs (created)'))
            if 'idx_svc_log_cat' not in existing:
                db.execute(text('CREATE INDEX idx_svc_log_cat ON phase2_service_logs (category, created)'))
            if 'idx_svc_log_request' not in existing:
                db.execute(text(
                    'CREATE INDEX idx_svc_log_request ON phase2_service_logs (request_id)'))
            if 'idx_svc_log_project' not in existing:
                db.execute(text(
                    'CREATE INDEX idx_svc_log_project ON phase2_service_logs (project_id)'))


def ensure_schema(engine):
    """Serialized upgrades preserve existing rows and template codes/snapshots."""
    mysql = engine.dialect.name == 'mysql'
    with engine.connect() as db:
        if mysql and db.execute(text("SELECT GET_LOCK('phase2-schema',10)")).scalar() != 1:
            raise RuntimeError('数据库结构升级正在进行，请稍后重启服务')
        try:
            db.execute(text('CREATE TABLE IF NOT EXISTS phase2_schema_versions (name VARCHAR(80) PRIMARY KEY, applied_at DOUBLE NOT NULL)'))
            # Shared operational tables must be ensured even after the main
            # schema version was applied, so additive tables ship without a
            # second heavyweight catalogue migration.
            for sql in SCHEMA:
                db.execute(text(sql))
            ensure_indexes(db)
            db.commit()
            _ensure_task_lease_columns(db)
            db.commit()
            if db.execute(text('SELECT name FROM phase2_schema_versions WHERE name=:v'), {'v': VERSION}).first():
                return
            auto = 'BIGINT PRIMARY KEY AUTO_INCREMENT' if mysql else 'INTEGER PRIMARY KEY AUTOINCREMENT'
            large = 'LONGTEXT' if mysql else 'TEXT'
            db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_templates (
                id ''' + auto + ''', template_code VARCHAR(128) NOT NULL UNIQUE,
                location_association VARCHAR(128), video_genre VARCHAR(128), actor_count INT,
                status INT NOT NULL DEFAULT 1, sort_order INT NOT NULL DEFAULT 0,
                payload_json ''' + large + ''' NOT NULL,
                identity_hash VARCHAR(64), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)'''))
            cols = {c['name']: c for c in inspect(db).get_columns('phase2_templates')}
            for name, definition in [('identity_hash', 'VARCHAR(64)'), ('created_at', 'TIMESTAMP NULL'), ('updated_at', 'TIMESTAMP NULL')]:
                if name not in cols:
                    db.execute(text('ALTER TABLE phase2_templates ADD COLUMN ' + name + ' ' + definition))
            if mysql:
                db.execute(text('ALTER TABLE phase2_templates MODIFY payload_json LONGTEXT NOT NULL, MODIFY status INT NOT NULL DEFAULT 1'))
            # A previous interrupted upgrade may already have populated some
            # identities. Rebuild them from payloads so an old holder cannot
            # block a valid row through the unique index.
            db.execute(text('UPDATE phase2_templates SET identity_hash=NULL'))
            known = set()
            rows = list(db.execute(text('SELECT id,payload_json FROM phase2_templates ORDER BY status DESC,id')))
            for tid, payload in rows:
                try:
                    p = json.loads(payload)
                    key = template_identity(p['location_association'], p['video_name'])
                except (ValueError, KeyError, TypeError):
                    db.execute(text('UPDATE phase2_templates SET status=0 WHERE id=:id'), {'id': tid})
                    continue
                if key in known:
                    # Retain historical IDs and assets, but do not publish duplicate identities.
                    db.execute(text('UPDATE phase2_templates SET status=0,identity_hash=NULL WHERE id=:id'), {'id': tid})
                else:
                    known.add(key)
                    db.execute(text('UPDATE phase2_templates SET identity_hash=:key WHERE id=:id'), {'id': tid, 'key': key})
            db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_tasks (
                id VARCHAR(64) PRIMARY KEY, kind VARCHAR(24) NOT NULL, run_id VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL, next_run DOUBLE NOT NULL DEFAULT 0,
                lease_owner VARCHAR(64) NULL, lease_until DOUBLE NOT NULL DEFAULT 0,
                payload ''' + large + ''' NOT NULL, UNIQUE(kind,run_id))'''))
            _ensure_task_lease_columns(db)
            if mysql:
                db.execute(text('ALTER TABLE phase2_tasks MODIFY payload LONGTEXT NOT NULL'))
            for sql in SCHEMA:
                db.execute(text(sql))
            db.execute(text('''CREATE TABLE IF NOT EXISTS phase2_media_objects (
                id VARCHAR(64) NOT NULL, kind VARCHAR(24) NOT NULL, bucket VARCHAR(128) NOT NULL,
                object_key VARCHAR(512) NOT NULL, mime VARCHAR(64) NOT NULL, size_bytes BIGINT NOT NULL,
                width INT NOT NULL DEFAULT 0, height INT NOT NULL DEFAULT 0,
                sha256 VARCHAR(64) NOT NULL, created_at DOUBLE NOT NULL,
                PRIMARY KEY(id,kind))'''))
            indexes = [('phase2_templates','uq_template_identity','identity_hash',True),
                       ('phase2_templates','idx_template_catalog','status,location_association,actor_count',False),
                       ('phase2_tasks','idx_task_due','kind,status,next_run',False),
                       ('phase2_task_events','idx_task_history','task_id,created',False)]
            for table, name, columns, unique in indexes:
                if name not in {x['name'] for x in inspect(db).get_indexes(table)}:
                    db.execute(text('CREATE ' + ('UNIQUE ' if unique else '') + 'INDEX ' + name + ' ON ' + table + '(' + columns + ')'))
            ensure_indexes(db)
            db.execute(text('INSERT INTO phase2_schema_versions(name,applied_at) VALUES(:v,:t)'), {'v': VERSION, 't': time.time()})
            db.commit()
        finally:
            if mysql:
                db.execute(text("SELECT RELEASE_LOCK('phase2-schema')"))
