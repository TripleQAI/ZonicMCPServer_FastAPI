# Dynamic Custom Function Package Registration — Lazy Install-on-First-Use

> **Carried over from the Django project.** The design is unchanged in the FastAPI port; Django paths map as follows: `core/settings.py` -> `app/config.py` (typed `Settings`), `core/asgi.py` and `mcp_server/asgi.py` -> `app/main.py`, and `engine/`, `infra/`, `models/`, `mcp_server/` -> the same folders under `app/`. Settings are read with `get_settings()` instead of `django.conf.settings`.

**Status:** design / plan (no code yet)
**Scope:** the `function` executor (`engine/executors/function/`) only —
**additive, off by default, isolated from the database & API executors, config over an
HTTP API (no direct DB), and lazy: no listener, no Kafka, no background task.**

## 1. Goal

Let operators make new custom function packages available to the MCP server **at
runtime, without a restart** — while the feature stays **disabled by default**,
**cannot affect existing database/API functionality**, and adds **no standing cost**
(no consumer, no poller, no background task).

1. A user **inserts a package config row into PostgreSQL** whenever they want a new
   package available (exposed to the server through an HTTP config API).
2. The **first `function` tool call that references a not-yet-registered package**
   triggers the work: the server **fetches the config from the API**, **installs** the
   package, **registers** its functions, caches the result, and serves the call. Every
   later call is a fast cache hit. No restart, no listener.

Nothing is hard-coded, and the MCP server **never connects to Postgres directly** — the
package list, versions, module paths and aliases are read through an HTTP config API
behind a small interface.

## 2. Required behaviors & constraints

- **B1 — Dynamic config:** package definitions are inserted into Postgres on demand.
- **B2 — Lazy runtime registration:** the first tool call for an unregistered package
  → fetch config from the HTTP API → install → import + register → serve. Subsequent
  calls are cached.
- **C1 — No impact on existing functionality:** installation/registration must **not**
  affect the database or API executors, the cache, or any current behavior. *(§4)*
- **C2 — Flag-controlled, off by default:** a single master flag enables the feature;
  when false (the default) the subsystem is fully **disabled**. *(§3)*
- **C3 — Clean, low-code, no hard-coding, proper patterns.** *(§10)*
- **C4 — No direct DB connection:** config is fetched from a **PostgreSQL server API**
  over HTTP, behind an interface (a *port*). *(§7)*
- **C5 — No standing cost:** no Kafka, no CDC, no background consumer or poller. Work
  happens **only** when a package is actually invoked. *(§6)*

## 3. Safe by default — the master feature flag

A **single** setting gates the entire subsystem:

```
FUNCTION_DYNAMIC_REGISTRATION_ENABLED = False   # default
```

**When `False` (default) — the server behaves exactly as it does today:**
- The `function` executor uses only the existing static `settings.FUNCTION_ALLOWLIST`.
- No config API is contacted; nothing is ever installed.
- **Zero** new external dependencies are touched and **zero** new behavior runs.

**When `True`:** the executor's lazy path is active — an unregistered package is
resolved, installed, and registered on first use.

Design notes (this is what keeps it clean):
- **The executor stays flag-agnostic.** It always calls
  `get_function_registry().ensure_available(function_path)`. The registry factory returns
  a **Null Object** (`NullFunctionRegistry`, a no-op) when disabled, or the
  **`LazyFunctionRegistry`** when enabled — so there are **no `if enabled:` branches** in
  the executor, and the disabled path is byte-for-byte today's behavior.
- **One decision point** — the flag is read only where the registry is constructed.
- **Instant rollback** — flip the flag to `False`; no logic redeploy.

## 4. Isolation — no impact, and no direct DB connection

The subsystem is **additive and quarantined**; config comes over HTTP, so it **opens no
database connection at all**:

| Concern | Existing (untouched) | Dynamic registration (new, isolated) |
|---|---|---|
| Data access | MSSQL via pyodbc (`DB_CONNECTION_STRING`) | **HTTP** to a Postgres-backed **config API** (`CONFIG_API_URL`) — no DB driver, no connection |
| HTTP | API executor via httpx + the HTTP cache | its own config-API calls (own client/timeout) |
| Code touched | database/api executors, base executor, cache, resolver — **unchanged** | only the `function` executor's registry lookup |
| Runtime | request path | first-call install (subprocess), then cached |

Guarantees:
- **No database connection** — config is read only through the HTTP API (like the
  existing cache-over-HTTP design). No pyodbc/psycopg pool; the database executor is
  untouched.
- **No standing cost** — no consumer, no poller, no background task. When idle, the
  subsystem does **nothing**; it acts only on a tool call that needs an unregistered
  package.
- **No import-time side effects** — the new modules do **no I/O on import**.
- **Fault isolation** — if the config API is unreachable or a package can't install, only
  **that tool call** fails (as `status=error`); database, API, and static-allowlist
  function tools are unaffected. Nothing crashes.
- **Process isolation** — `pip install` runs in a **subprocess**, off the event loop; it
  only adds importable modules, never mutating DB/API state.
- **Purely additive** — existing behavior is unchanged whether the flag is off *or* on.

## 5. Baseline: how the function executor works today

`FunctionExecutor._run` resolves a callable by dotted path with
`importlib.import_module(module_path)` + `getattr`, caches it in an `lru_cache`, and
gates it through `settings.FUNCTION_ALLOWLIST`. Facts that shape this design:

- **Import ≠ install.** `import_module` only finds modules already on `sys.path`. To
  register a package it must first be **installed** (pip into the venv) or on
  `PYTHONPATH`.
- Allowlisting is a static setting today; lazy registration extends it dynamically — via
  the registry, so the disabled path is unchanged.
- `_import_callable` is `lru_cache`d and Python caches finders — both must be
  **invalidated** after a runtime install or the new module won't be importable.

## 6. Lazy registration — the core flow

The **trigger is the tool call itself** — there is no listener.

```
   ┌────────┐  INSERT config    ┌──────────────┐        ┌──────────────┐
   │  User  │ ────────────────► │  PostgreSQL   │ ◄reads─│  Config API   │
   └────────┘                   │ custom_packages│        │  (HTTP)       │
                                └──────────────┘        └──────▲───────┘
                                                               │ (on miss) get(pkg)
   function tool call: functionPath = 'mycompany.utils.clean'  │
                       │                                        │
                       ▼                                        │
   ┌──────────────────────────────────────────────────────────┴────────┐
   │ FunctionExecutor._run  (flag on)                                    │
   │   await registry.ensure_available(functionPath)                     │
   │     ├─ already registered ─► fast path (no I/O)                     │
   │     └─ not registered ─► single-flight lock per package             │
   │           ├─ fetch config  PackageConfigSource.get(pkg)  [HTTP]     │
   │           ├─ install       PackageInstaller (pip, pinned, subproc)  │
   │           ├─ invalidate caches → import → validate                  │
   │           └─ register in FunctionRegistry (allowlist + alias)       │
   │   import callable + call                                            │
   └─────────────────────────────────────────────────────────────────────┘
```

Step by step (flag on):

1. A `function` tool call arrives with `functionPath` (or an alias).
2. `_run` calls `registry.ensure_available(functionPath)`.
3. **Registered already?** (in the registry / on `sys.path`) → return immediately; import
   and call as today. **This is the common, fast path.**
4. **Not registered?** Take a **single-flight lock** for that package (so concurrent first
   calls install once), re-check, then:
   a. `PackageConfigSource.get(pkg)` — fetch the authoritative config over HTTP.
   b. If allowed (namespace + index checks, §12), `PackageInstaller` runs `pip install`
      (pinned + hashes) in a subprocess.
   c. `importlib.invalidate_caches()`, import to validate, add the prefix/alias to the
      registry.
5. Import the callable and call it.
6. Every later call on this replica is a cache hit (registry + `lru_cache`) — no HTTP, no
   pip.

Each replica loads lazily on its **own** first use — naturally per-replica, **no
coordination, no broadcast machinery.**

## 7. Components & the config-source interface

| Component | Location (proposed) | Responsibility |
|---|---|---|
| Config store | PostgreSQL (behind the API) | `custom_packages` rows |
| Config API | HTTP service (infra) | exposes the config over HTTP; MCP reads it here |
| **Config source (port)** | `engine/executors/function/config_source.py` | **interface**; HTTP adapter reads `CONFIG_API_URL` |
| Function registry | `engine/executors/function/registry.py` | `LazyFunctionRegistry` + `NullFunctionRegistry`; `ensure_available` |
| Package installer | `engine/executors/function/installer.py` | pip install (pinned + hashes), cache invalidation |
| Executor integration | `engine/executors/function/executor.py` | call `ensure_available` then import (flag-agnostic) |

*No Kafka consumer, no CDC connector, no outbox, no bootstrap/reconcile module — removed
by the lazy model.*

### The config source — the interface (a *port*)

The subsystem depends on an **abstraction**, not on Postgres. Define the port; the HTTP
adapter plugs in when the API contract is fixed.

```python
# engine/executors/function/config_source.py  — the PORT (implement later)
@dataclass
class PackageConfig:
    name: str
    distribution: str                 # pip spec, e.g. 'mycompany-utils==1.4.2'
    module_path: str                  # import path, e.g. 'mycompany.utils'
    allow_prefix: str | None = None   # defaults to module_path
    alias: str | None = None
    index_url: str | None = None
    hashes: list[str] | None = None

class PackageConfigSource(ABC):
    """Where package config comes from. Backed by the HTTP config API — never the DB."""
    @abstractmethod
    async def get(self, ref: str) -> PackageConfig | None: ...   # lazy lookup by name/module
    # (optional) report_status(ref, status, ...) for a UI/admin view.

# HttpPackageConfigSource(PackageConfigSource): calls CONFIG_API_URL over httpx,
# reusing the graceful-degradation style of infra/cache.py (errors -> None).
```

The registry and installer depend on `PackageConfigSource`, so the config's origin is
swappable and testable with a fake — no Postgres or HTTP in tests.

## 8. Concurrency, caching & idempotency

The lazy model's correctness hinges on these (all in-process, no distributed coordination):

- **Single-flight** — an `asyncio.Lock` **per package key** so N concurrent first-calls
  install the package **once**; the others await the winner and then proceed. Prevents
  duplicate concurrent `pip` runs.
- **Positive cache** — a registered package lives in the registry (and the existing
  `_import_callable` `lru_cache`); after first use, resolution is **O(1)**, no HTTP/pip.
- **Negative cache** — a package that isn't in config, is disallowed, or fails to install
  is cached as "unavailable" for a **short TTL**, so repeated calls don't hammer the API
  or retry `pip` every time. TTL-bounded → self-heals once the config/package is fixed.
- **Idempotency** — if `pip` already sees the pinned version satisfied it's a no-op;
  re-registering an already-registered prefix is a no-op.

## 9. Data model (behind the config API — confirm against the real table)

The MCP server never sees this directly; the **config API** owns it.

```sql
CREATE TABLE custom_packages (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  distribution TEXT NOT NULL,             -- pip spec, e.g. 'mycompany-utils==1.4.2'
  index_url    TEXT,
  hashes       TEXT[],                     -- for pip --require-hashes
  module_path  TEXT NOT NULL,
  allow_prefix TEXT,
  alias        TEXT,
  functions    JSONB,
  enabled      BOOLEAN NOT NULL DEFAULT TRUE,
  status       TEXT NOT NULL DEFAULT 'pending', -- pending|registered|failed (optional)
  version      TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The config API exposes at minimum `GET get(ref)` (by name or module path). No outbox
table is needed — there are no events.

## 10. Design patterns (clean, low-code, no hard-coding)

- **Lazy Loading / Lazy Initialization** — the whole model: install + register on first
  use, never before.
- **Cache-Aside** — check the registry (cache); on a miss, load from the config API +
  install, then populate the registry. Positive + negative caching.
- **Single-Flight (in-flight dedup)** — per-package lock so concurrent first-calls do the
  work once.
- **Ports & Adapters / Dependency Inversion** — `PackageConfigSource` is a **port**; the
  HTTP adapter is one implementation. No direct DB code.
- **Feature Toggle** — one master flag selects the registry implementation.
- **Null Object** — `NullFunctionRegistry` when disabled → the executor is uniform with
  **no `if enabled:` branching**; disabled path equals today's behavior.
- **Registry** — `FunctionRegistry` (mirrors `get_connection_registry` /
  `get_cache_service`) as the runtime source of truth for allowlist + aliases.
- **Strategy** — installer strategy (pip now; wheel/artifact later) behind an interface.
- **Bulkhead / fault isolation** — a failed install fails only that tool call.

## 11. Reliability & correctness

- **First-call latency** — the **first** call per package per replica pays the `pip`
  install time (seconds). It runs in a **subprocess off the event loop**, so it doesn't
  block other requests; and it happens **once**. Every later call is cached. *(Optional
  pre-warm is an open decision, §18.)*
- **Only what's used** — packages that are never invoked are never installed — the core
  efficiency win of the lazy model.
- **Per-replica, no coordination** — each replica installs on its own first use; there is
  no shared state, no broadcast, no consumer group. New replicas self-provision on demand.
- **Failure surfacing** — a config-fetch or install failure surfaces as the tool's
  `status=error` (via `BaseExecutor`'s try/except) + a short negative-cache entry. There
  are **no background failures** to manage (no events, no DLQ).
- **Cache invalidation** — after install, `importlib.invalidate_caches()` + clear the
  `_import_callable` `lru_cache`.

## 12. ⚠️ Security — this is a remote-code-execution surface

Runtime `pip install` + `import` driven by config means **anyone who can insert into
`custom_packages` can cause arbitrary code to run** the first time a matching function is
invoked. (Only when the flag in §3 is on.) Mandatory controls:

- **Pin + verify** — exact `name==version` with `--require-hashes`; reject unpinned/
  unhashed specs.
- **Trusted index only** — private index / allowlist of package names or namespaces
  (e.g. `mycompany-*` + a vetted set); never open PyPI by default.
- **Module namespace allowlist** — `module_path` must match an allowed prefix so config
  can't register `os`, `subprocess`, etc.
- **Authenticated config API** — the MCP server authenticates (token/mTLS) and can only
  **read** config; restrict who can write `custom_packages`.
- **Isolation** — run installs in a constrained subprocess; consider an install sidecar
  with egress only to the private index.
- **Audit** — log every lazy fetch/install/register (who/what/version/hash); emit metrics.
- **Kill switch** — the master flag (§3) plus per-package `enabled=false` (the config API
  returns it as unavailable → the executor won't install it).

Treat the config API as a **privileged control plane**, not user data.

## 13. Configuration (settings / env — nothing hard-coded)

- **`FUNCTION_DYNAMIC_REGISTRATION_ENABLED`** — **master switch, default `False`.** When
  `False`, no extra config is needed and the server runs exactly as today.
- `CONFIG_API_URL` — base URL of the PostgreSQL-backed config API (HTTP). **No DB DSN.**
- `CONFIG_API_TOKEN` / auth — credentials for the config API.
- `FUNCTION_PACKAGE_INDEX_URL`, `FUNCTION_PACKAGE_ALLOWED_NAMES` /
  `FUNCTION_ALLOWED_MODULE_PREFIXES` — install & import trust boundaries.
- `FUNCTION_NEGATIVE_CACHE_TTL_SECONDS` — how long an "unavailable" result is cached.
- `requirements.txt` — **no new runtime deps required**; the existing `httpx` client is
  reused for the config API. (**No Kafka client, no psycopg.**)

## 14. Lifecycle — what runs, and when

- **Flag off (default):** nothing. Static allowlist only. No config API, no installs.
- **Flag on, package already available:** fast path — registry/`lru_cache` hit, import,
  call. No HTTP, no pip.
- **Flag on, first use of a new package:** single-flight → fetch config → install
  (subprocess) → register → call. One-time cost, this replica only.
- **Flag on, config fetch/install fails:** the tool call returns `status=error`; a short
  negative-cache entry avoids hammering; self-heals after the TTL when the config is fixed.
- **Startup / shutdown:** nothing special — no background task to start or stop.

## 15. Observability & operations

- Optional `status` write-back to the config API (`registered` / `failed` + version) for a
  UI/admin view.
- Metrics: lazy-resolve count, config-fetch + install durations, install success/failure,
  registry size, negative-cache hits.
- Read-only introspection of the live registry (what's registered on this replica).

## 16. Files & modules (when implemented)

**New (application code):**
`engine/executors/function/config_source.py` (**the port + HTTP adapter**),
`engine/executors/function/registry.py` (`FunctionRegistry`, `LazyFunctionRegistry`,
`NullFunctionRegistry`, single-flight + caches),
`engine/executors/function/installer.py` (pip install, pinning, cache invalidation)

**Edited:** `engine/executors/function/executor.py` (call `ensure_available`, flag-
agnostic), `core/settings.py` (`FUNCTION_DYNAMIC_REGISTRATION_ENABLED` default `False`,
`CONFIG_API_URL`, trust boundaries, negative-cache TTL), `.env`/`.env.example`

**Infra (not application code):** the config API service + `custom_packages` table.
*(No Kafka, no Debezium, no outbox, no replication slot.)*

## 17. Phased roadmap

1. **Flag + Null registry + config port (zero behavior change):** add the master flag
   (default `False`), `FunctionRegistry` + `NullFunctionRegistry`, `ensure_available` on
   the executor path, and the `PackageConfigSource` **interface** (with a fake for tests).
   Flag off = nothing changes — ship this safely first.
2. **Lazy resolve + HTTP adapter:** implement `HttpPackageConfigSource` and
   `LazyFunctionRegistry.ensure_available` (fetch config, register an already-installed
   package), with single-flight + positive/negative caching. Still no pip.
3. **Runtime install:** `PackageInstaller` (pinned + hashes + allowed index) with cache
   invalidation — full install-on-first-use.
4. **Hardening:** security controls (§12), observability (§15), optional pre-warm.

## 18. Open decisions

1. **Config API contract** — endpoint/path and the `PackageConfig` JSON shape for
   `get(ref)` (and optional `report_status`)? Auth (token vs mTLS)?
2. **Lookup key** — resolve the package from the full `functionPath` by module prefix, or
   require an explicit package name / alias in the tool config?
3. **Negative-cache TTL** — default value; separate TTLs for "not found" vs "install
   failed"?
4. **Optional pre-warm** — keep pure lazy *(chosen)*, or add an optional startup pre-warm
   of a small "always-on" set to avoid first-call latency for hot packages?
5. **Install isolation** — per-replica venv *(rec)* vs a shared read-only package cache.
6. **Trust boundary** — private index? allowed name/namespace list? who controls the table?

---
*Prerequisite reminder:* installation makes a package **importable**; the config (fetched
over the API) selects **which** package to install and register. With the master flag off
(the default), none of this runs, the server opens no config connection, and there is no
background task at all — it is exactly as it is today. When on, the cost is a **one-time,
first-call** install per package per replica, and only for packages that are actually
used.
