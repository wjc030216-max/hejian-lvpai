"""Centralized, non-blocking service logging.

Every node writes business events into one place (the main node's log
collector) so operators never have to SSH into three machines. The channel is
fire-and-forget: a full queue drops events instead of blocking a request, and
normal local logging keeps working even when the collector is unreachable.

Categories are the stable taxonomy used by the operator console:
  login, payment, image, video, asset, tos, file, face, orchestration, admin.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
import urllib.request
from contextvars import ContextVar
from pathlib import Path

from common.internal_auth import local_headers
from common.redaction import redact_sensitive_text


SERVICE = os.getenv("PHASE2_LOG_SERVICE", "unknown")
COLLECTOR = os.getenv("LOG_COLLECTOR_URL", "").rstrip("/")
MAX_QUEUE = int(os.getenv("PHASE2_LOG_QUEUE", "2000"))
FLUSH_INTERVAL = float(os.getenv("PHASE2_LOG_FLUSH_SECONDS", "1.0"))
LOCAL_LOG_ROOT = Path(os.getenv(
    "PHASE2_LOCAL_LOG_ROOT", "/var/lib/hejian-lvpai/service-logs"))
LOCAL_LOG_ROOT.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("hejian-lvpai.service")
_logger.propagate = False
_logger.setLevel(logging.INFO)
_REQUEST_CONTEXT = ContextVar("service_log_request_context", default={})
_local_handlers = {}
_local_handlers_lock = threading.Lock()


def current_context():
    return dict(_REQUEST_CONTEXT.get() or {})


def bind_request(**values):
    current = current_context()
    current.update({key: value for key, value in values.items() if value not in (None, "")})
    return _REQUEST_CONTEXT.set(current)


def reset_request(_token=None):
    # Flask test clients can nest requests in one context. Clearing the
    # mapping is deterministic and avoids reusing a ContextVar token twice.
    _REQUEST_CONTEXT.set({})


def _local_handler(category):
    day = time.strftime("%Y-%m-%d", time.localtime())
    key = (str(LOCAL_LOG_ROOT), SERVICE, category, day)
    with _local_handlers_lock:
        handler = _local_handlers.get(key)
        if handler is None:
            directory = LOCAL_LOG_ROOT / SERVICE / category
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(
                directory / f"{day}.jsonl", mode="a", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _local_handlers[key] = handler
        return handler


def _log_context(record):
    values = {}
    for key in (
            "request_id", "project_id", "task_id", "source_ip", "method",
            "path", "status_code", "duration_ms", "error_type"):
        value = getattr(record, "svc_" + key, None)
        if value not in (None, ""):
            values[key] = value
    return values


class _CentralHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self._queue = queue.Queue(maxsize=MAX_QUEUE)
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="service-log", daemon=True)
        self._thread.start()

    def emit(self, record):
        if not COLLECTOR:
            return
        try:
            entry = {
                "service": getattr(record, "svc_service", SERVICE),
                "category": getattr(record, "svc_category", "system"),
                "level": record.levelname.lower(),
                "message": redact_sensitive_text(record.getMessage())[:4000],
                "context": redact_sensitive_text(
                    getattr(record, "svc_context", "") or ""
                )[:4000],
                "created": record.created,
            }
            entry.update(_log_context(record))
            self._queue.put_nowait(entry)
        except queue.Full:
            self._dropped += 1
        except Exception:
            self.handleError(record)

    def _run(self):
        while True:
            try:
                time.sleep(FLUSH_INTERVAL)
                items = []
                while len(items) < 500:
                    try:
                        items.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                if not items:
                    continue
                dropped = self._dropped
                self._dropped = 0
                data = json.dumps({"items": items, "dropped": dropped}).encode("utf-8")
                headers = {"Content-Type": "application/json", **local_headers("local-phase2")}
                request = urllib.request.Request(
                    COLLECTOR, data=data, headers=headers, method="POST"
                )
                urllib.request.urlopen(request, timeout=5).close()
                self._fail_since = 0
            except Exception:
                # The collector may be restarting. Put the batch back in the
                # in-memory queue so a transient outage does not silently lose it.
                if 'items' in locals():
                    for item in items:
                        try:
                            self._queue.put_nowait(item)
                        except queue.Full:
                            self._dropped += 1
                    if 'dropped' in locals():
                        self._dropped += dropped
                    items = []
                    dropped = 0
                self._fail_since = getattr(self, "_fail_since", 0) + 1
                if self._fail_since % 10 == 1:
                    try:
                        sys.stderr.write("service_log: log collector unreachable, batch requeued in memory\n")
                    except Exception:
                        pass
                time.sleep(1)


def install(service: str | None = None) -> None:
    """Attach the central handler once; pass the canonical service name."""
    if service:
        globals()["SERVICE"] = service
    if not any(isinstance(h, _CentralHandler) for h in _logger.handlers):
        _logger.addHandler(_CentralHandler())
    event("system", "服务启动", context=f"service={SERVICE}")


def install_http_access(app) -> None:
    """Record detailed API requests without bodies, query strings or tokens."""
    from flask import g, request

    if getattr(app, "_phase2_access_logging", False):
        return
    app._phase2_access_logging = True

    def request_source_ip():
        return (
            request.headers.get("X-Real-IP", "").strip()
            or request.remote_addr
            or ""
        )[:64]

    @app.before_request
    def _bind_request_context():
        if request.path == "/health" or request.path.endswith("/health"):
            return None
        supplied_request_id = request.headers.get("X-Request-ID", "").strip()
        request_id = (
            supplied_request_id
            if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_request_id)
            else uuid.uuid4().hex
        )
        values = request.view_args or {}
        g._phase2_log_token = bind_request(
            request_id=request_id,
            project_id=values.get("pid") or "",
            task_id=values.get("task_id") or "",
            source_ip=request_source_ip(),
            method=request.method,
            path=request.path,
        )
        g._phase2_log_started = time.perf_counter()
        g._phase2_request_id = request_id

    @app.after_request
    def _write_request_log(response):
        request_id = getattr(g, "_phase2_request_id", "")
        if not request_id:
            return response
        response.headers["X-Request-ID"] = request_id
        started = getattr(g, "_phase2_log_started", time.perf_counter())
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        context = {
            "method": request.method if request else "",
            "path": request.path if request else "",
            "status": response.status_code,
            "duration_ms": duration_ms,
            "remote_addr": request_source_ip() if request else "",
            "user_agent": (
                request.headers.get("User-Agent", "")[:200] if request else ""),
        }
        event(
            "api", "HTTP 请求完成", context=json.dumps(context, ensure_ascii=False),
            request_id=request_id,
            project_id=(request.view_args or {}).get("pid", "") if request else "",
            task_id=(request.view_args or {}).get("task_id", "") if request else "",
            source_ip=context["remote_addr"], method=context["method"],
            path=context["path"], status_code=response.status_code,
            duration_ms=duration_ms)
        return response

    @app.teardown_request
    def _clear_request_context(_error=None):
        token = getattr(g, "_phase2_log_token", None)
        if token is not None:
            reset_request(token)


def get(category: str, context: str = ""):
    """Return a logger adapter tagged with a stable operator category."""
    return logging.LoggerAdapter(
        _logger, {"svc_category": category, "svc_service": SERVICE, "svc_context": context}
    )


def event(category: str, message: str, context: str = "", level: str = "info",
          **fields) -> None:
    """One-shot helper for call sites that prefer a single line."""
    request_context = current_context()
    request_context.update({key: value for key, value in fields.items() if value not in (None, "")})
    record_fields = {
        "service": SERVICE, "category": category, "context": context,
        **{key: request_context.get(key, value) for key, value in fields.items()},
    }
    for key in (
            "request_id", "project_id", "task_id", "source_ip", "method",
            "path", "status_code", "duration_ms", "error_type"):
        if request_context.get(key) not in (None, ""):
            record_fields[key] = request_context[key]
    payload = {
        "created": time.time(),
        "service": SERVICE,
        "category": category,
        "level": level if level in ("debug", "info", "warning", "error") else "info",
        "message": redact_sensitive_text(message)[:4000],
        "context": redact_sensitive_text(context or "")[:4000],
        **_log_context(type("Record", (), {
            "svc_" + key: value for key, value in record_fields.items()
        })()),
    }
    _local_handler(category).handle(
        logging.LogRecord(category, logging.INFO, Path(__file__).name, 0,
                          json.dumps(payload, ensure_ascii=False), (), None))
    record = _logger.makeRecord(
        _logger.name, getattr(logging, payload["level"].upper(), logging.INFO),
        Path(__file__).name, 0, message, (), None)
    for key, value in record_fields.items():
        setattr(record, "svc_" + key, value)
    _logger.handle(record)
