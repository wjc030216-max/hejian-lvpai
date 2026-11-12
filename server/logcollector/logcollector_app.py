"""Central log collector on the main node.

Receives batched, structured log entries from every private service, appends
them to rotating per-category files under LOG_ROOT, and mirrors them into the
shared MySQL table ``phase2_service_logs`` for the operator console. Only
internal services holding the shared JWT secret may write here.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import jwt
from flask import Flask, jsonify, request
from sqlalchemy import text

from common.service_app import serve
from common.redaction import redact_sensitive_text


LOG_ROOT = Path(os.getenv("LOG_ROOT", "/var/lib/hejian-lvpai/logs"))
LOG_ROOT.mkdir(parents=True, exist_ok=True)
ALLOWED_CATEGORIES = {
    "login", "payment", "image", "video", "asset", "tos", "file", "face",
    "orchestration", "admin", "api", "system",
}
ALLOWED_SUBJECTS = {
    "local-phase2", "local-video-storage", "local-phase2-storage",
    "phase2-file", "phase2-image",
}

app = Flask(__name__)
_file_handlers = {}
_engine = None


def _db():
    global _engine
    if _engine is None:
        from common.database_schema import engine_for, ensure_service_log_table
        _engine = engine_for(os.environ["AITRAVEL_DATABASE_URL"])
        ensure_service_log_table(_engine)
    return _engine


def _beijing_day(created):
    """Return the Beijing calendar day (UTC+8) for a UTC epoch timestamp."""
    return time.strftime("%Y-%m-%d", time.gmtime(float(created) + 8 * 3600))


def _file_handler(category: str, created: float):
    day = _beijing_day(created)
    key = (category, day)
    handler = _file_handlers.get(key)
    if handler is None:
        directory = LOG_ROOT / category
        directory.mkdir(parents=True, exist_ok=True)
        # Logs are a permanent operational record. Keep one append-only file
        # per Beijing calendar day and never rotate/delete old days.
        handler = logging.FileHandler(
            directory / f"{day}.log", mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        _file_handlers[key] = handler
        for old_key in [k for k in _file_handlers if k[0] == category and k[1] != day]:
            _file_handlers.pop(old_key).close()
    return handler


@app.before_request
def verify_internal():
    if request.path == "/health":
        return
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    try:
        claims = jwt.decode(token, os.environ["AITRAVEL_JWT_SECRET"], algorithms=["HS256"],
                            options={"require": ["exp", "sub"]})
        if claims.get("sub") not in ALLOWED_SUBJECTS:
            raise jwt.InvalidTokenError("unknown service subject")
    except (jwt.InvalidTokenError, KeyError):
        app.logger.warning("log ingest rejected: unauthorized")
        return jsonify(code=401, message="服务身份验证失败"), 401


@app.post("/api/v1/logs/ingest")
def ingest():
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return jsonify(code=400, message="缺少日志条目"), 400
    now = time.time()
    dropped = int(body.get("dropped") or 0)
    if dropped:
        items = [{
            "service": "log-client", "category": "system", "level": "warning",
            "message": f"日志客户端队列已满，丢弃 {dropped} 条",
            "context": "", "created": now,
        }] + items
    rows = []
    for item in items[:500]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "system").lower()
        if category not in ALLOWED_CATEGORIES:
            category = "system"
        service = str(item.get("service") or "unknown")[:64]
        level = str(item.get("level") or "info").lower()[:16]
        message = redact_sensitive_text(item.get("message") or "")[:4000]
        context = redact_sensitive_text(item.get("context") or "")[:4000]
        created = float(item.get("created") or now)
        request_id = str(item.get("request_id") or "")[:64]
        project_id = str(item.get("project_id") or "")[:128]
        task_id = str(item.get("task_id") or "")[:128]
        source_ip = str(item.get("source_ip") or "")[:64]
        method = str(item.get("method") or "")[:16]
        path = str(item.get("path") or "")[:1024]
        status_code = int(item.get("status_code") or 0) if str(item.get("status_code") or "").isdigit() else 0
        duration_raw = item.get("duration_ms")
        duration_ms = float(duration_raw) if isinstance(duration_raw, (int, float)) else 0.0
        error_type = str(item.get("error_type") or "")[:128]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(created + 8 * 3600))
        tags = " ".join(value for value in (
            f"request_id={request_id}" if request_id else "",
            f"project_id={project_id}" if project_id else "",
            f"task_id={task_id}" if task_id else "",
            f"http={method} {path} {status_code} {duration_ms:.2f}ms" if method else "",
            f"error_type={error_type}" if error_type else "",
        ) if value)
        line = (
            f"{timestamp} [{service}] {message}"
            + (f" | {context}" if context else "")
            + (f" | {tags}" if tags else "")
        )
        _file_handler(category, created).handle(
            logging.LogRecord(category, logging.getLevelName(level.upper()) if level.upper() in
                              ("DEBUG", "INFO", "WARNING", "ERROR") else logging.INFO,
                              Path(__file__).name, 0, line, (), None)
        )
        rows.append((
            service, category, level, message, context, created,
            request_id, project_id, task_id, source_ip, method, path,
            status_code, duration_ms, error_type,
        ))
    if rows:
        try:
            with _db().begin() as db:
                for row in rows:
                    db.execute(text(
                        "INSERT INTO phase2_service_logs(service,category,level,message,context,created,"
                        "request_id,project_id,task_id,source_ip,method,path,status_code,duration_ms,error_type) "
                        "VALUES(:service,:category,:level,:message,:context,:created,"
                        ":request_id,:project_id,:task_id,:source_ip,:method,:path,:status_code,"
                        ":duration_ms,:error_type)"),
                        dict(service=row[0], category=row[1], level=row[2], message=row[3],
                             context=row[4], created=row[5], request_id=row[6],
                             project_id=row[7], task_id=row[8], source_ip=row[9],
                             method=row[10], path=row[11], status_code=row[12],
                             duration_ms=row[13], error_type=row[14]))
        except Exception:
            app.logger.exception("log collector database write failed")
    app.logger.info("log ingest accepted=%d", len(rows))
    return jsonify(code=0, message="ok", accepted=len(rows))


@app.get("/health")
def health():
    return jsonify(status="ok", service="phase2-logcollector", categories=sorted(ALLOWED_CATEGORIES))


if __name__ == "__main__":
    serve(app, 10031)
