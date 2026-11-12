"""线程本地化 requests.Session，复用连接池，降低高频内部调用在弱网下的建连开销。"""
import os
import threading

import requests
from requests.adapters import HTTPAdapter

_local = threading.local()
POOL_CONNECTIONS = int(os.getenv('HTTP_POOL_CONNECTIONS', '20'))
POOL_MAXSIZE = int(os.getenv('HTTP_POOL_MAXSIZE', '20'))


def session() -> requests.Session:
    """每个线程一个 Session；urllib3 连接池本身线程安全，可安全复用。"""
    current = getattr(_local, 'session', None)
    if current is None:
        current = requests.Session()
        adapter = HTTPAdapter(pool_connections=POOL_CONNECTIONS, pool_maxsize=POOL_MAXSIZE, max_retries=0)
        current.mount('http://', adapter)
        current.mount('https://', adapter)
        _local.session = current
    return current
