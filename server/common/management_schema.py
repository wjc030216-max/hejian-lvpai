"""Shared catalogue operation tables; real selections and display baselines never mix."""
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS phase2_template_settings (
        template_code VARCHAR(128) PRIMARY KEY, heat_base BIGINT NOT NULL DEFAULT 0,
        show_heat INT NOT NULL DEFAULT 0, revision INT NOT NULL DEFAULT 1)''',
    '''CREATE TABLE IF NOT EXISTS phase2_template_selections (
        template_code VARCHAR(128) NOT NULL, owner_hash VARCHAR(64) NOT NULL,
        created DOUBLE NOT NULL, PRIMARY KEY(template_code,owner_hash))''',
    '''CREATE TABLE IF NOT EXISTS phase2_admin_audit (
        id VARCHAR(32) PRIMARY KEY, actor VARCHAR(80) NOT NULL, action VARCHAR(64) NOT NULL,
        target VARCHAR(128) NOT NULL, detail TEXT NOT NULL, created DOUBLE NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS phase2_admin_attempts (
        id VARCHAR(32) PRIMARY KEY, source VARCHAR(64) NOT NULL, created DOUBLE NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS phase2_task_events (
        id VARCHAR(32) PRIMARY KEY, task_id VARCHAR(64) NOT NULL, kind VARCHAR(24) NOT NULL,
        status VARCHAR(32) NOT NULL, message TEXT NOT NULL, created DOUBLE NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS phase2_media_jobs (
        id VARCHAR(32) PRIMARY KEY, template_code VARCHAR(128) NOT NULL,
        revision VARCHAR(32) NOT NULL, status VARCHAR(16) NOT NULL,
        error TEXT, created DOUBLE NOT NULL, updated DOUBLE NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS phase2_free_settings (
        id INT PRIMARY KEY, total INT NOT NULL, updated_at DOUBLE NOT NULL,
        updated_by VARCHAR(80))''',
    '''CREATE TABLE IF NOT EXISTS phase2_free_daily (
        day VARCHAR(10) PRIMARY KEY, total INT NOT NULL, used INT NOT NULL DEFAULT 0,
        updated_at DOUBLE NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS phase2_refunds (
        refund_no VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL UNIQUE,
        order_no VARCHAR(64) NOT NULL, amount_fen INT NOT NULL,
        status VARCHAR(32) NOT NULL, attempts INT NOT NULL DEFAULT 0,
        next_run DOUBLE NOT NULL DEFAULT 0, provider_order_no VARCHAR(64),
        provider_trxid VARCHAR(128), provider_trxstatus VARCHAR(32),
        error_code VARCHAR(64), error_message TEXT, request_payload LONGTEXT,
        response_payload LONGTEXT, requested_at DOUBLE NOT NULL,
        updated_at DOUBLE NOT NULL, finished_at DOUBLE, operator VARCHAR(80))''',
)


def init_tables(connection,execute):
    for sql in SCHEMA: execute(connection,sql)


def heat_label(count):
    return f'{count / 10000:.1f}万人选择' if count>=10000 else f'{count}人选择'


INDEXES=(('phase2_admin_audit','idx_admin_audit_time','created,id'),
         ('phase2_admin_attempts','idx_admin_attempt_source','source,created'),
         ('phase2_task_events','idx_task_event_time','created,id'),
         ('phase2_template_selections','idx_template_selection_time','created'),
         ('phase2_refunds','idx_refund_status','status,next_run'),
         ('phase2_projects','idx_project_paid','paid_at,id'),
         ('phase2_projects','idx_project_status_paid','status,paid_at'),
         ('phase2_orders','idx_order_paid','paid_at,order_no'))

def ensure_indexes(db):
    from sqlalchemy import inspect,text
    from sqlalchemy.exc import OperationalError
    tables=set(inspect(db).get_table_names())
    for table,name,columns in INDEXES:
        if table not in tables: continue
        if name in {index['name'] for index in inspect(db).get_indexes(table)}: continue
        try: db.execute(text('CREATE INDEX '+name+' ON '+table+'('+columns+')'))
        except OperationalError as error:
            # Concurrent cold starts may create the same named index.
            if getattr(error.orig,'args',[None])[0]!=1061 and 'already exists' not in str(error.orig): raise
