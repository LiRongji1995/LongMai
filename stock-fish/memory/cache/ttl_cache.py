"""
通用线程安全 TTL 缓存

提供内存缓存 + 可选磁盘持久化，支持 LRU 淘汰。
"""
import json
import hashlib
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional, Callable, Dict, List

from loguru import logger
from memory.atomic_io import atomic_write_json


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    data: Any
    cached_at: float
    ttl: int            # 有效期（秒）
    data_type: str = "" # 数据类型标签
    symbol: str = ""    # 股票代码


class TtlCache:
    """线程安全 TTL 缓存，带 LRU 淘汰 + 可选磁盘持久化"""

    def __init__(self, name: str, persist_dir: Optional[Path] = None,
                 default_ttl: int = 300, max_entries: int = 10000):
        self.name = name
        self.persist_dir = Path(persist_dir) if persist_dir else None
        if default_ttl < 0 or max_entries < 1:
            raise ValueError("invalid cache capacity or ttl")
        self.default_ttl = default_ttl
        self.max_entries = max_entries

        self._lock = threading.RLock()
        self._cache: Dict[str, CacheEntry] = {}
        self._access_order: List[str] = []  # 最近访问的 key（尾部最新）
        self._hits = 0
        self._misses = 0
        self._expired = 0
        self._persisted_paths: Dict[str, Path] = {}

        # 如果 persist_dir 存在，尝试恢复
        if persist_dir:
            self._loaded_from_disk = self.load_persisted()
        else:
            self._loaded_from_disk = 0

    # ── 公共 API ──

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，过期或缺失返回 None"""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            age = time.time() - entry.cached_at
            if age >= entry.ttl:
                self._evict(key)
                self._expired += 1
                self._misses += 1
                return None

            self._hits += 1
            self._touch(key)
            return entry.data

    def set(self, key: str, data: Any, ttl: Optional[int] = None,
            data_type: str = "", symbol: str = "") -> None:
        """写入缓存"""
        with self._lock:
            effective_ttl = self.default_ttl if ttl is None else ttl
            if effective_ttl < 0:
                raise ValueError("ttl cannot be negative")
            if len(self._cache) >= self.max_entries and key not in self._cache:
                self._evict_lru()

            previous = self._cache.get(key)
            self._cache[key] = CacheEntry(
                key=key, data=data, cached_at=time.time(),
                ttl=effective_ttl,
                data_type=data_type, symbol=symbol,
            )
            try:
                self._persist(key)
            except BaseException:
                if previous is None:
                    self._cache.pop(key, None)
                else:
                    self._cache[key] = previous
                raise
            self._touch(key)

    def get_or_fetch(self, key: str, fetch_fn: Callable[[], Any],
                     ttl: Optional[int] = None,
                     data_type: str = "", symbol: str = "") -> Any:
        """缓存命中返回，未命中调用 fetch_fn 填充并返回"""
        cached = self.get(key)
        if cached is not None:
            return cached

        data = fetch_fn()
        if data is not None:
            self.set(key, data, ttl, data_type, symbol)
        return data

    def invalidate(self, key: str) -> None:
        """清除指定 key"""
        with self._lock:
            self._evict(key)

    def invalidate_by_symbol(self, symbol: str) -> int:
        """清除某只股票的所有缓存条目，返回清除数量"""
        with self._lock:
            keys = [k for k, v in self._cache.items() if v.symbol == symbol]
            for k in keys:
                self._evict(k)
            return len(keys)

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            for key in list(self._cache):
                self._remove_persisted(key)
            if self.persist_dir:
                for path in self.persist_dir.glob("*.json"):
                    try:
                        CacheEntry(**json.loads(path.read_text(encoding="utf-8")))
                    except (ValueError, TypeError):
                        continue  # Preserve unrelated/corrupt files.
                    path.unlink()
            self._cache.clear()
            self._access_order.clear()
            self._hits = 0
            self._misses = 0
            self._expired = 0

    def stats(self) -> dict:
        """缓存统计信息"""
        with self._lock:
            total_ops = self._hits + self._misses
            hit_rate = self._hits / total_ops if total_ops > 0 else 0

            oldest = None
            youngest = None
            if self._cache:
                now = time.time()
                ages = [now - e.cached_at for e in self._cache.values()]
                oldest = max(ages) if ages else None
                youngest = min(ages) if ages else None

            return {
                "name": self.name,
                "size": len(self._cache),
                "max_entries": self.max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "expired": self._expired,
                "hit_rate": round(hit_rate, 3),
                "oldest_entry_age_s": oldest,
                "youngest_entry_age_s": youngest,
                "loaded_from_disk": self._loaded_from_disk,
            }

    # ── 内部方法 ──

    def _touch(self, key: str) -> None:
        """更新 LRU 顺序（移到尾部）"""
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _evict(self, key: str) -> None:
        """从缓存中删除条目"""
        self._cache.pop(key, None)
        if key in self._access_order:
            self._access_order.remove(key)
        self._remove_persisted(key)

    def _evict_lru(self) -> None:
        """淘汰最久未访问的条目（头部）"""
        if self._access_order:
            oldest = self._access_order.pop(0)
            self._cache.pop(oldest, None)
            self._remove_persisted(oldest)

    # ── 磁盘持久化 ──

    def _persist_key_path(self, key: str) -> Path:
        """获取持久化文件路径"""
        if not self.persist_dir:
            return None
        # 对 key 做安全文件名处理
        safe = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.persist_dir / f"{safe}.json"

    def _persist(self, key: str) -> None:
        """将单个条目写入磁盘"""
        path = self._persist_key_path(key)
        if not path:
            return
        entry = self._cache.get(key)
        if not entry:
            return
        atomic_write_json(path, asdict(entry))
        old_path = self._persisted_paths.get(key)
        if old_path and old_path != path:
            old_path.unlink(missing_ok=True)
        self._persisted_paths[key] = path

    def _remove_persisted(self, key: str) -> None:
        """删除磁盘上的持久化文件"""
        paths = {self._persist_key_path(key), self._persisted_paths.get(key)} - {None}
        for path in paths:
            path.unlink(missing_ok=True)
        self._persisted_paths.pop(key, None)

    def load_persisted(self) -> int:
        """启动时从磁盘恢复有效条目，返回恢复数量"""
        if not self.persist_dir or not self.persist_dir.exists():
            return 0

        count = 0
        now = time.time()
        for f in self.persist_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                entry = CacheEntry(**raw)
                # 跳过已过期的
                if now - entry.cached_at > entry.ttl:
                    continue
                if count >= self.max_entries:
                    break
                self._cache[entry.key] = entry
                self._persisted_paths[entry.key] = f
                self._access_order.append(entry.key)
                count += 1
            except (ValueError, TypeError, OSError):
                logger.warning(f"TtlCache [{self.name}] 无法恢复文件 {f.name}; preserved for inspection")

        if count > 0:
            logger.info(f"TtlCache [{self.name}] 从磁盘恢复 {count} 条目")
        return count
