# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# cleanscan.lib.cache
"""cleanscan.lib.cache —— 依赖扫描结果的增量缓存，缓存键随索引指纹变化而失效。

扫描结果缓存在 ``<store>/cleanscan/cache/<key>.json``，其中 ``key`` 对索引指纹
（cleanread ``meta.enriched_at``，缺失时退回到 db mtime）、seed 与 budget 做哈希。
cleanread 重新索引时 ``enriched_at`` 改变，缓存键随之改变，旧条目不再命中。cleanread
存储库本身只读，本模块只写入同级的 cache 目录。
"""
from __future__ import annotations

import hashlib
import json

from lib import stores
from lib.graph import Budget


#### 取索引指纹：优先 enriched_at，否则 db mtime，均无时返回 "0" [@380kkm 2026-06-05] ####
def _fingerprint(store: "stores.Store") -> str:
    val = store.meta("enriched_at")
    if val:
        return val
    try:
        return str(int(store.db_path.stat().st_mtime))
    except OSError:
        return "0"


#### 由（索引指纹、seed、budget）算出稳定的 16 位十六进制缓存键 [@380kkm 2026-06-05] ####
def cache_key(store: "stores.Store", seed: str, budget: Budget) -> str:
    payload = {
        "fp": _fingerprint(store),
        "seed": seed,
        "max_nodes": budget.max_nodes,
        "max_depth": budget.max_depth,
        "direction": budget.direction,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


#### 定位与存储库同级的 cache 目录 [@380kkm 2026-06-05] ####
def _cache_dir(store: "stores.Store"):
    return store.db_path.parent / "cleanscan" / "cache"


#### 按键读取缓存切片，缺失或损坏时返回 None [@380kkm 2026-06-05] ####
def get(store: "stores.Store", key: str) -> dict | None:
    path = _cache_dir(store) / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


#### 把切片按键写入 cache 目录（按需创建目录） [@380kkm 2026-06-05] ####
def put(store: "stores.Store", key: str, data: dict) -> None:
    d = _cache_dir(store)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


#### 取缓存切片：命中返回已缓存的结果，否则计算后写入缓存 [@380kkm 2026-06-05] ####
def cached_scan(store: "stores.Store", seed: str, budget: Budget | None = None,
                alias: str | None = None, use_cache: bool = True) -> tuple[dict, bool]:
    """返回 ``(graph_dict, hit)`` —— 命中则返回已缓存的结果，否则计算并写入缓存。"""
    # 局部 import
    from lib import render, scope

    budget = budget or Budget()
    key = cache_key(store, seed, budget)
    if use_cache:
        hit = get(store, key)
        if hit is not None:
            return hit, True
    data = render.graph_to_dict(scope.scan(store, seed, budget, alias=alias))
    if use_cache:
        put(store, key, data)
    return data, False
#### /取缓存切片 ####
