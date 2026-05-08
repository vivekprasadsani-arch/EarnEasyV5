from hashlib import md5
from typing import Union
import asyncio

def md5_hash(text: Union[str, bytes]) -> str:
    if isinstance(text, str):
        text = text.encode('utf-8')
    return md5(text).hexdigest()

import threading

# Global lock state for browser initialization - using threading.Lock for true global synchronization
_global_browser_lock = threading.Lock()

def get_browser_init_lock():
    """Get the global thread-safe browser initialization lock."""
    return _global_browser_lock 