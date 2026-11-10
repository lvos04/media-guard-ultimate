"""Geheugen teruggeven aan het systeem na zware taken.

Grote tijdelijke lijsten (bv. 67.000 'ontbrekend'-rijen, een bibliotheeksync) worden door
Python wél vrijgegeven, maar glibc houdt die pagina's per thread-arena vast. Zonder
``malloc_trim`` groeit het procesgeheugen (RSS) daardoor blijvend, ook al is er geen lek
(gemeten: 1,1 → 1,7 GB in 20 min onder belasting met een grote bibliotheek, terwijl de
Python-heap kleiner werd)."""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import threading
import time

_lock = threading.Lock()
_last = 0.0
_libc = None
try:  # alleen glibc (Linux); elders een no-op
    _name = ctypes.util.find_library("c")
    if _name:
        _libc = ctypes.CDLL(_name)
        _libc.malloc_trim.argtypes = [ctypes.c_size_t]
except (OSError, AttributeError):
    _libc = None


def trim(min_interval: float = 30.0) -> bool:
    """Ruim op en geef vrije heap terug; hooguit eens per ``min_interval`` seconden."""
    global _last
    if _libc is None:
        return False
    with _lock:
        now = time.monotonic()
        if now - _last < min_interval:
            return False
        _last = now
    gc.collect()
    _libc.malloc_trim(0)
    return True
