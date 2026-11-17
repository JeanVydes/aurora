# Aurora — Arquitectura, DynamoDB Single-Table & Patrones de Acceso

> Sin deploys / sin cambios en producción — sólo documentación del estado actual del proyecto.

---

## 1. Vista general

Aurora es un **Ephemeral Code Execution IaaS** (estilo Firecracker). Toda la computación corre en un sandbox efímero por celdas de Lambda/RIE-on-Docker, pero toda la persistencia vive en una única tabla DynamoDB (`EphemeralSandboxTable`) y en una sola instancia de Redis 7. La API existe en dos planos:

- **Control plane** — API Gateway → `ApiHandler` Lambda (Python 3.11) en un contenedor RIE por invocación
- **Data plane** — DynamoDB (Single-Table) + Redis (cache-aside / write-through)


```
Browser (prod: Vercel / dev: local Next.js)
        │
        ▼
Cloudflare Tunnel (prod) / localhost (dev)
        │
        ▼
API Gateway (LocalStack) → Lambda (RIE-in-docker)
        │
  ┌─────┴───────┐
  ▼             ▼
Redis 7      DynamoDB (Single-Table)
cache-aside   ← GSI1 (sparse, INCLUDE) para listado por cuenta
write-through
```

El sandbox simula microVMs: levanta un rootfs temporal en `/tmp/<vm>_`, ejecuta el snippet con `setrlimit(RLIMIT_AS) + tracemalloc` y teardown cuando termina. La telemetría (boot_time_ms, teardown_time_ms, state_transitions) se guarda en el ítem `EXEC#`.

---

## 2. Por qué Single-Table

1. **Una sola tabla** → operaciones CRUD siempre de 2 instrucciones o menos (`GetItem`, `PutItem`, `UpdateItem`, `TransactWriteItems`), incluso en modo serverless.
2. **Sin joins** — la PK/SK sobrecargada propaga la entidad en la misma key; la semántica vive en el nombre de la PK (`ACCOUNT#`, `EXEC#`, `EMAIL#`…).
3. **Consistent read** cuando hace falta (p.ej. lectura de cuota antes de ejecutar).
4. **Sparse index** para las pocas lecturas que sí necesitan orden por fecha.
5. **TTL** en ejecuciones para auto-purga sin ciclos de código.

Tabla: `EphemeralSandboxTable`
Partition Key (`PK`): `String`
Sort Key (`SK`): `String`
Billing mode: `PAY_PER_REQUEST`
TTL attribute: `ttl` (habilitado en la tabla)
Indexes: GSI1 (ver §5)

---

## 3. Entidades y schema

### 3.1 Entidades y sus keys

| Entidad                             | `PK`                            | `SK`                  | Atributos clave                                                                 |
|-------------------------------------|--------------------------------|------------------------|---------------------------------------------------------------------------------|
| **Account**                         | `ACCOUNT#<account_id>`         | `METADATA`             | `account_id`, `email`, `username`, `tier`, `password_hash`, `created_at`, `updated_at` |
| **Email index**                     | `EMAIL#<email>`                | `LOOKUP`               | `account_id`, `email`, `tier`, `created_at` (denormalizado)                     |
| **Monthly quota**                   | `QUOTA#<account_id>#<YYYY-MM>` | `SUMMARY`              | `account_id`, `period` (YYYY-MM), `tier`, `accumulated_seconds`, `limit_seconds` (omitido en PRO), `execution_count`, `updated_at` |
| **Execution**                       | `EXEC#<execution_id>`          | `METADATA`             | `execution_id`, `account_id`, `tier`, `code`, `runtime`, `duration`, `timeout_enforced`, `memory_limit_mb`, `status`, `exit_code`, `stdout`, `stderr`, `telemetry` (map), `ttl`, `GSI1PK`, `GSI1SK` |
| **API key metadata** *(rotación)*   | `APIKEY#<account_id>`          | `METADATA`             | `account_id`, `api_key_id`, `api_key_prefix`, `api_key_hash` (SHA-256), `created_at`, `last_used_at` |
| **API key lookup** *(by hash)*      | `APIKEYLOOKUP#<sha256(key)>`   | `LOOKUP`               | `account_id`, `api_key_id`, `created_at`                                        |

`telemetry` es un `Map` con `vm_id`, `isolation_engine` (`firecracker-microvm`), `host_executor`, `vcpu_count`, `memory_limit_mb`, `boot_time_ms`, `execution_time_ms`, `teardown_time_ms`, `total_lifecycle_ms`, `state_transitions`, `teardown_status`, `boot_timestamp`, `teardown_timestamp`.

**Decisiones:**
- `EXEC#` tiene su propia partición (`EXEC#<id>`) → lecturas directas O(1) para `GET /executions/{id}` sin tocar un índice.
- `ACCOUNT#<id>` / `EXEC#<id>` / `QUOTA#…` comparten PK porque la tabla es única; cada entidad decodifica por la forma del prefijo.
- `EMAIL#` no es la clave principal de la cuenta → no puedes listar cuentas por email (no hace falta; ese es el rol del index).
- `APIKEY#<id>` lleva la cuenta real; el hash vive en `APIKEYLOOKUP#` con RCU 1x siempre.
- Para GSI1: sólo los ítems EXEC llevan `GSI1PK=ACCOUNT#<id>` y `GSI1SK=<created_at>` → **índice sparse** (los demás ítems no aparecen).

### 3.2 Por qué este PK/SK
| Acceso                                              | Clave que lo satisface                              | Costo |
|-----------------------------------------------------|-----------------------------------------------------|-------|
| `POST /auth/login` — buscar cuenta por email        | `EMAIL#<email>`/`LOOKUP` GetItem                   | RCU 1x |
| `GET /executions/{id}` — lectura puntual            | `EXEC#<id>`/`METADATA` GetItem                     | RCU 1x |
| `GET /quota` — cuota del caller                     | `QUOTA#<acc>#<YYYY-MM>`/`SUMMARY` GetItem          | RCU 1x |
| `POST /execute` — reserva cuota                     | `UpdateItem` atómico con `ADD accumulated_seconds`  | WCU 1 |
| `GET /executions` — listado de cuenta               | **GSI1**: `Query(GSI1PK=ACCOUNT#<id>, ScanIndexForward=False, Limit=50)` | RCU ~1x/ítem |
| `POST /execute` — ¿es el ítem dueño? (no se usa)   | N/A (no usamos Scan, la validación la hacemos por PK) | —    |
| Rotación de API key                                 | TransactWriteItems(DELETE lookup viejo + PUT meta + PUT lookup nuevo)    | WCU ×3 |

---

## 4. Operación por endpoint — qué hace, en qué orden

### 4.1 `GET /health`

```
(dispatch) → handle_health → return { status, service, table_name, redis_host, timestamp }
```
DynamoDB: **0 operaciones** · Cache: **0** · Auth: ninguna.

---

### 4.2 `POST /auth/register`

```
parse body → validate email / password / tier ───────► 400 si inválido
get_item(EMAIL#<email>/LOOKUP)  ─miss─►
get_item(ACCOUNT#<custom_id>/METADATA)   ← sólo si tier custom y account_id dado (no recomendado)

put_items TRANSACT (TransactWriteItems, 3 items):
  Put ACCOUNT#<id>/METADATA  ConditionExpression="attribute_not_exists(PK)"
  Put EMAIL#<email>/LOOKUP     ConditionExpression="attribute_not_exists(PK)"
  Put QUOTA#<acc>#<YYYY-MM>/SUMMARY  ConditionExpression="attribute_not_exists(PK)"

→ éxito:
  cache_set_with_ttl(account:<id>, TTL 300s)
  write_through_quota(quota status)
  create_jwt_token (HS256, exp=+3600s)
→ 409 Conflict si ConditionalCheckFailedException (email-o cuenta ya existe)
```

- **Operación DynamoDB**: 2 GetItem + **1 TransactWriteItems** (3 Puts condicionados).
- **Autenticación**: ninguna.
- **Redis**: 2 SETEX (account cache + quota write-through). Sin GET.
- **Transaccional**: sí — los 3 ítems existen o ninguno.

---

### 4.3 `POST /auth/login`

```
parse body → 400 si falta email/password ───────►

get_item(EMAIL#<email>/LOOKUP)            → 401 si no existe
get_item(ACCOUNT#<id>/METADATA)           → 401 si no existe
verify_password(password, account.password_hash)   → 401 si no coincide
create_jwt_token                          → JWT HS256, exp +3600s
cache_set_with_ttl(account:<id>, TTL 300s)
→ 200 { account_id, token, tier, … }
```

- **Operación**: 2 GetItem consecutivos + PBKDF2 verify. **0** escrituras obligatorias (cache write-through esbest-effort).
- **Transaccional**: no.
- **Cache**: read + write-through.

---

### 4.4 `POST /auth/refresh`

```
Bearer token → verify_jwt_token (HS256)             → 401 si inválido/expirado
get_account_cache_aside(account_id)  ──────────────► cache hit (_fuente=cache) / miss (GetItem + SETEX TTL 300s)
create_jwt_token (HS256)
→ 200 { account_id, token: <nuevo JWT>, tier, _fuente }
```

- **Operación**: 0 GetItem obligatorios si cache hit; 1 GetItem si miss.
- **Transaccional**: no.
- **Redis**: read (path crítico) + write-through automático en account side on create only (el refresh no actualiza account item).

---

### 4.5 `POST /execute`

```
Bearer/X-API-Key → authenticate_request(...)        → 401 si falla
parse body (code, timeout, memory_limit_mb)          → 400 / 413

get_account_cache_aside(account_id)                  → tier actual
MAX_CODE_CHARS check                                  → 413 si >65.536

if tier == "PRO":
    enforcement = { effective_timeout: req_timeout, reserved_seconds: 0.0 }
else:
    # FREE — reserva atómica antes de ejecutar
    get_item(QUOTA#<acc>#<YYYY-MM>/SUMMARY, ConsistentRead=True)
    accumulated >= 14_400 → QuotaExceededError → 429
    _compute effective = min(req_timeout, remaining)
    update_item:
        UpdateExpression="SET entity_type = … ADD accumulated_seconds :reserve, execution_count :inc"
        ConditionExpression="attribute_not_exists(accumulated_seconds) OR accumulated_seconds = :old"
        Values: :reserve=effective, :inc=1, :old=current_accumulated
    → en la carrera: falla ConditionExpression → retry hasta 3x → QuotaContentionError → 429

sandbox.run_ephemeral_code(
    code=snippet, runtime="python3.11",
    timeout_seconds=effective_timeout, memory_limit_mb=mem,
) → ejecutar en rootfs efímero /tmp/fc-vm-<id>/

put_item EXEC#<execution_id>/METADATA:
    GSI1PK=ACCOUNT#<acc_id>, GSI1SK=now_iso, ttl=now_epoch+30d,
    entity_type=EXECUTION, status, duration, exit_code, telemetry, …

settle_quota_usage (FREE):   update_quota_delta = duration - reserved  (positivo si overshoot; negativo si refund)
settle_quota_usage (PRO):    update_item ADD accumulated_seconds :dur, execution_count :inc

write_through_quota(quota_status)
cache_set_with_ttl(exec:<id>, TTL 300s, populate_cache=False→ invalida)
→ 200 { execution_id, status, stdout, stderr, duration, quota, _fuente }
```

- **Operación DynamoDB**:
  | Pieza | Operación | Condición |
  |---|---|---|
  | account resolve | `GetItem` (cache-aside) o hit Redis | — |
  | quota read | `GetItem ConsistentRead=True` | — |
  | quota reserve (FREE) | **`UpdateItem ADD`** atómico | `accumulated = :old OR attribute_not_exists(accumulated_seconds)` |
  | quota settle | **`UpdateItem ADD`** atómico | ninguna (settle_refund negativo) |
  | executive persist | **`PutItem`** EXEC# item | ninguna (PK única porque id es UUID) |
  | quota write-through | **Transacción no atómica** (reloj único reservado, settle al final) — converge | — |
- **Transaccional**: **no** entre reserva y settle (son 2 update_item separados por el tiempo de ejecución, imposibles de agrupar en `TransactWriteItems` porque duran segundos). El tsanado atómico es por ítem — optimistic CAS.
- **Cache**: read account (cache-aside), write-through account/quota. exec cache → invalidación (se puebla en el primer GET /executions/{id} via cache-aside).

---

### 4.6 `GET /executions`

```
Bearer/X-API-Key → authenticate_request(...)     → 401
Query GSI1:
  IndexName="GSI1",
  Key("GSI1PK").eq(f"ACCOUNT#{account_id}"),
  ScanIndexForward=False (newest first),
  Limit=50
→ 200 { executions: [...thin INCLUDE payload], count, _fuente: "dynamodb" }
```

- **Operación**: 1 `Query` sobre **GSI1**. Nada de scan.
- **Proyección INCLUDE** en GSI1: execution_id, status, duration, exit_code, memory_limit_mb, runtime, created_at, account_id, tier, telemetry. **Sin code/stdout/stderr**.
- **Cache**: ninguna — siempre consulta fresca.
- **Transaccional**: no.

---

### 4.7 `GET /executions/{id}`

```
Bearer/X-API-Key → authenticate_request(...)   → 401
redis:GET exec:{id} → hit  → _fuente=cache → ownership check → 200
miss →
  get_item(EXEC#<id>/METADATA, ConsistentRead=False)  → 404 si no existe
  cache_set_with_ttl(exec:{id}, TTL 300s)               → _fuente=dynamodb
ownership check (claims.account_id == item.account_id)  → 403 si no
→ 200 { …item, "_fuente": … }
```

- **Operación**: cache-aside. 0 o 1 GetItem.
- **Ownership**: cross-tenant IDs son inefectivos aún si el id es adivinable (UUID v4).

---

### 4.8 `GET /quota`

```
Bearer/X-API-Key → authenticate_request(...)   → 401
get_account_cache_aside(account_id)            → tier (cache o GetItem)
get_quota_status(account_id, tier, table):
  get_item(QUOTA#<acc>#<YYYY-MM>/SUMMARY, ConsistentRead=True)
  → FREE: limit_seconds=14400, remaining=…, execution_count=…
  → PRO:  { unlimited: true, accumulated, execution_count }
→ 200 { …, "_fuente": "cache"|"dynamodb" }
```

- **Operación**: 1 GetItem read cuota.
- **Cache**: account read (cache-aside); cuota siempre se lee de DynamoDB.
- **Non-authoritative tier**: el tier_l del account cache manda; si acabas de hacer upgrade el cache puede tardar 300s en reflejarlo.

---

### 4.9 `POST /subscription/upgrade`

```
Bearer/X-API-Key → authenticate_request(...)   → 401
target_tier = body["tier"] (default PRO)         → validar VALID_TIERS o 400

update_item(ACCOUNT#<id>/METADATA)         → SET tier = target, updated_at = now
update_item(QUOTA#<acc>#<current>/SUMMARY) → SET tier = target, updated_at = now
update_item(EMAIL#<email>/LOOKUP)          → SET tier = target, updated_at = now  (best-effort, catch ClientError)
cache_set_with_ttl(account:<id>, TTL 300s)
get_quota_status(...) → incluye tier actual
→ 200 { …, updated_quota }
```

- **Operación**: 3 `UpdateItem` + cache refresh + GetItem.
- **No transaccional** — son 3 writes sobre entidades distintas (account / quota / email lookup); convergen, pero si se cae el lambda en mitad, el cache es el que manda hasta el próximo miss. Aceptable para un upgrade.
- **Cache**: write-through del account; quota write-through cuando se complete el próximo execute.

---

### 4.10 `GET /api-key`

```
Bearer/X-API-Key → authenticate_request(...)   → 401
get_api_key_meta(table, account_id)
→ 200 { has_key, api_key_id, api_key_prefix, created_at, last_used_at }   (o \{has_key: false\})
```

- **Operación**: 1 GetItem en `APIKEY#<id>`/`METADATA`.
- **Cache**: no — siempre lectura fresca (baja frecuencia).
- **El plaintext nunca se devuelve en este endpoint** (repo de secrets: env). El `lookup` tampoco se expone.

---

### 4.11 `POST /api-key/rotate`

```
Bearer/X-API-Key → authenticate_request(...)   → 401
rotate_api_key(table, account_id):
  key = "aurora_ak_" + token_hex(24)  (192 bits)
  hash = SHA256(key)
  api_key_id = "ak_" + token_hex(6)
  existing = get_api_key_meta(table, id)

  transact WriteItems:
    [Delete APIKEYLOOKUP#<old_sha256>/LOOKUP]         (sólo si existía key previa)
    Put   APIKEY#<id>/METADATA   { api_key_id, prefix, hash, created_at, last_used_at=null }
    Put   APIKEYLOOKUP#<sha256>/LOOKUP   { account_id, api_key_id, created_at }
  → ConditionalCheckFailed → retry con key nueva

→ 200 { api_key, api_key_id, api_key_prefix, created_at }
```

- **Operación**: 1 GetItem + **TransactWriteItems** (2-3 Put/Delete condicionados).
- **Atomicidad**: true — el antiguo LOOKUP, el nuevo META y el nuevo LOOKUP van en una sola tx.
- **El plaintext sólo en respuesta** — nunca se persiste; la tabla guarda `sha256`.

---

### 4.12 `DELETE /api-key`

```
Bearer/X-API-Key → authenticate_request(...)   → 401
revoke_api_key(table, account_id):
  existing = get_api_key_meta(id)  → false si no tenía
  transact WriteItems:
    Delete APIKEY#<id>/METADATA   ConditionExpression=attribute_exists(PK)   (o ajustado)
    [Delete APIKEYLOOKUP#<hash>/LOOKUP]
→ 200 { revoked: true|false, message }
```

- **Operación**: 1 GetItem + **TransactWriteItems** (2 Deletes).
- Revocacióninstantánea: siguiente auth falla con 401 (lookup antiguo borrado).

---

### 4.13 Tabla resumen

| Método | Endpoint                | Auth      | Ops DynamoDB                                      | Transaccional | Cache |
|--------|------------------------|-----------|---------------------------------------------------|--------------|-------|
| GET    | /health                | —         | 0                                               | no            | —     |
| POST   | /auth/register         | —         | 2 GetItem + **TransactWriteItems(3 Put)**         | **sí**        | WR + QT (WR) |
| POST   | /auth/login            | —         | 2 GetItem                                       | no            | WR    |
| POST   | /auth/refresh          | Bearer    | 0 o 1 GetItem                                   | no            | RD (+WR) |
| POST   | /subscription/upgrade   | Bearer/key| 3 UpdateItem + 1 GetItem                          | no            | WR    |
| POST   | /execute               | Bearer/key| 1 GetItem account + 1 GetItem cuota + **UpdateItem ADD** (reserve) + **PutItem** EXEC + **UpdateItem ADD** (settle) + 1 GetItem cuota | no | RD account + QT write-through |
| GET    | /executions            | Bearer/key| **Query GSI1** (Limit 50)                        | no            | —     |
| GET    | /executions/{id}       | Bearer/key| 1 GetItem EXEC (si cache miss)                   | no            | RD    |
| GET    | /quota                 | Bearer/key| 1 GetItem QUOTA (ConsistentRead)                  | no            | RD account |
| GET    | /api-key               | Bearer/key| 1 GetItem APIKEY                                  | no            | —     |
| POST   | /api-key/rotate        | Bearer/key| 1 GetItem + **TransactWriteItems**                 | **sí**        | WR    |
| DELETE | /api-key               | Bearer/key| 1 GetItem + **TransactWriteItems**                 | **sí**        | —     |

(AUTH de cuenta sobre `/execute` siempre hace cache-aside antes de reservar — el tier en los JWT solo sirve para paths rápidos.)


---

## 5. Índices y TTL

### 5.1 GSI1 — Listado de ejecuciones por cuenta

```
IndexName     : GSI1
KeySchema     : GSI1PK (HASH) -> ACCOUNT#<account_id>
                GSI1SK (RANGE, S) — ISO datetime (sortable)
Projection    : INCLUDE
                Non-KeyAttributes: execution_id, status, duration, exit_code,
                                   memory_limit_mb, runtime, created_at,
                                   account_id, tier, telemetry
ProjectionType: INCLUDE (no `code`, no `stdout`, no `stderr`)
BillingMode   : inherited — PAY_PER_REQUEST
```

- **Sparse** — sólo los ítems EXEC# tienen `GSI1PK`/`GSI1SK`; el resto son invisibles al índice.
- **Orden cronológico** gratis (`ISO date` es lexicográficamente ordenable).
- Sin `Scan` en toda la API.
- `ScanIndexForward=False` → newest first.

### 5.2 TTL

Tabla: `TimeToLiveSpecification` `AttributeName=ttl`, `Enabled=true`.

Ítem `EXEC#`: `"ttl": now_epoch + 30*24*3600` (≈30 días). DynamoDB auto-borra ítems expirados — no lifecycle hook, no Lambda scheduler, no cleanup.

### 5.3 por qué no LSI

LSI (Local Secondary Index) comparte la partition key de la tabla base. Para `EXEC#<id>`, un LSI ordenaría por `created_at` dentro de una ejecución concreta — no serviría para listar por cuenta. GSI sí permite otra partition key (`ACCOUNT#<id>`).

También no usamos LSI porque LSI se crea sólo en `CreateTable` — GSI se puede añadir sin recrear la tabla (útil en prod para escalar después sin downtime).

---

## 6. Auth & Seguridad

### 6.1 Dos mecanismos de auth mutuamente excluyentes por request

| Mecanismo      | Header                          | Estado      | Uso                         |
|----------------|---------------------------------|-------------|-----------------------------|
| JWT HS256      | `Authorization: Bearer <hs256>` | Stateless   | Web/browser/agent humans     |
| API key        | `X-API-Key: aurora_ak_<hex48>`  | Revocable   | Agents/scripts (sin sesión)  |

Prioridad: **Bearer primero**, fallback a `X-API-Key`. Si ninguno está → `401`.

### 6.2 JWT

- HS256, secret via `JWT_SECRET` env var. **No hay default** en el código; generar uno robusto en prod.
- Claims: `sub`/`account_id`, `email`, `tier`, `iat`, `exp` (3600s).
- Verificación: `verify_jwt_token(token)` (re-verifica firma + exp).

### 6.3 API keys

- Almacenamiento: **SHA-256 (solo hash)** — nunca se guarda el plaintext.
- Rotación: `TransactWriteItems(Delete old LOOKUP + Put new LOOKUP + Put new META)`.
- Revocación: `TransactWriteItems(Delete META + Delete LOOKUP)`.
- Lookup: `GetItem` O(1) en `APIKEYLOOKUP#<hash>`.
- `last_used_at` se actualiza al rotar (no en hot path de auth — cero WCU en lecturas).
- **Plaintext se entrega una sola vez** (en la respuesta de `/rotate`).

### 6.4 Password hashing

PBKDF2-HMAC-SHA256, 100,000 iterations
Formato: `pbkdf2_sha256$<salt_hex>$<dk_hex>`
Verificación: `verify_password` → constant-time compare (`hmac.compare_digest`).

### 6.5 Ownership / Isolation

- `GET /executions/{id}` → `if item.account_id != claims.account_id: 403`
- `GET /executions` → `Query` GSI1 filtrado por `ACCOUNT#<claims.account_id>`.
- Nunca se expone un ítem de una cuenta ajena por predicado alguno.

### 6.6 Logging sin secretos

`_log()` emite líneas `[aurora:execute:*]` **pipe-delimited** y escapa saltos de línea (`\n` literal) para que cada evento de CloudWatch sea una línea. Nunca loguea tokens ni headers sensibles.

---

## 7. Redis — roles

### 7.1 Cache-Aside (lecturas)

| Key | Contenido | TTL | Pattern |
|-----|-----------|-----|---------|
| `account:<account_id>` | `{ account_id, email, username, tier, created_at }` (sin `password_hash`) | 300s | GET → miss → GetItem DYNAMODB + SETEX |
| `exec:<execution_id>` | ítem completo (stdout/stderr incluidos) | 300s | Ídem |
| `quota:<acc>:<YYYY-MM>` | `{ accumulated_seconds, execution_count, … }` | 300s | **Write-through** en cada escritura |
| `jwt:<sha256(token)[:24]>` | `{ account_id, email, tier }` | 3600s | informativo |

### 7.2 Write-Through (escrituras)

Account/Quota/Execution hacen `put_item`/`update_item` en DynamoDB primero y luego SETEX/DELETE en Redis. Si Redis está caído, el código encoge el error (try/except) y sigue: DynamoDB es la fuente de verdad, el cache "reap-dies" solo.

### 7.3 Invariantes

- El valor cacheado con strong consistency para **ejecuciones** puede ser stale brevemente — el cliente puede recibir el resultado del DynamoDB después.
- El tier tras upgrade PRO se fuerza cache-write-through — próxima lectura viene de Redis con `"tier":"PRO"`.
- Ningún `SCAN`, ningún `SCAN` en Redis tampoco — `GET KEYS(account:*)` nunca se usa.

---

## 8. Tests

15 passed — `uv run pytest -v`

| Archivo | Qué prueba |
|---|---|
| `test_infra_stack.py` | synth CDK — GSI1 proyectado, TTL, Lambda env, API Gateway proxy |
| `test_auth_and_endpoints.py` | register/login/refresh/upgrade + 409/401 casos |
| `test_cache.py` | cache-aside para hit/miss + write-through account/quota/execution |
| `test_quota_and_sandbox.py` | reservation atómica, timeout cap, MEMORY_LIMIT_EXCEEDED, 429 |
| `test_api_key.py` | lifecycle (create/rotate/revoke), scoping, isolation, plaintext one-time |
| `conftest.py` | moto + fakeredis fixtures |

Test stack cubre **paths felices + error paths**: `401` auth, `403` ownership, `409` duplicate register, `413` code too big, `429` quota, `503` (no test) network fall. Coverage de handlers: 100% en handlers vía moto.

---

## 9. Deploy y topología

### 9.1 Local (desarrollo)

```bash
docker compose up -d --build
./scripts/deploy-local.sh bootstrap
./scripts/deploy-local.sh deploy --require-approval never
uvâ run pytest -v
```

El `frontend` corre en `:3000`, habla con LocalStack vía proxy `/api/v1/*` — `LOCALSTACK_HOST=http://emulador:4566` (default en compose).

### 9.2 Producción (este computador)

```bash
docker compose up -d --build
# bring localstack-layer deps into image
./scripts/build-layer.sh
./scripts/deploy-local.sh deploy --require-approval never
docker restart aurora-cloudflared-1
```

Frontend: **Vercel** (`aurora.jeanvides.com`) → Cloudflare Tunnel → LocalStack (este PC) → `/api/v1/*`.

### 9.3 Otros PC (backup / demo)

```bash
./scripts/build-layer.sh
./scripts/deploy-local.sh bootstrap
./scripts/deploy-local.sh deploy --require-approval never
docker compose up -d
```

Mismo repo, mismo código. Diferencia: este PC expone por tunnel público; el otro es local.

---

## 10. Checklist del status actual

- [x] DynamoDB Single-Table (PK/SK)
- [x] Sin `Scan` en todo el código fuente
- [x] GSI1 sparse INCLUDE proyección delgada
- [x] TransactWriteItems para atomicidad en register/rotate/revoke
- [x] Conditional writes para unicidad / concurrency
- [x] Atomic ADD para cuotas (reserva previa, settle posterior)
- [x] TTL habilitado + atributo `ttl` en ejecuciones
- [x] `PAY_PER_REQUEST` billing
- [x] PITR + deletion protection + RETAIN + SSE (en AWS real; LocalStack acepta pero no enforce)
- [x] Cache-aside execution / account
- [x] Write-through account / quota
- [x] JWT HS256 + X-API-Key con sha256 storage
- [x] Ownership check en `GET /executions/{id}` y `GET /executions`
- [x] 12 más tests + fixtures moto/fakeredis
- [x] Logging estructurado sin secretos (`[aurora:execute:*]`)
- [ ] Multi-tenancy a nivel RLS DynamoDB (sólo aplican ownership chequeos; no hay IAM per-account en LStack)
- [ ] Rate limiting por cuenta (sólo cuota mensual, no RPS) — razonable para IaaS
- [ ] Persistencia duradera (`/var/lib/localstack`) — cuando se quiera demo durable
- [ ] `DEBUG=1` en emulador — deshabilitado porque borra state con cada recreación

---

## 11. Glosario

| Término                    | Significado                                                      |
|----------------------------|-------------------------------------------------------------------|
| Single-Table               | Diseño DynamoDB donde todas las entidades comparten una tabla con PK/SK sobrecargada |
| GSI                        | Global Secondary Index — índice independiente en partition key     |
| LSI                        | Local Secondary Index — índice que comparte partition key con la tabla base |
| Sparse index               | Ítems sin las atributos del índice están excluidos                |
| Cache-Aside                | Lectura primero va a cache; miss → DB → SETEX                     |
| Write-Through              | Escritura sincronizada DB + cache inmediata                       |
| TransactWriteItems         | Operación atómica DynamoDB multi-put con condiciones               |
| Optimistic concurrency     | CAS contra valor anterior leído (`accumulated_seconds = :old`)     |
| Executor efímero           | Rootfs temporal `/tmp/fc-vm-<id>_` + `subprocess` + teardown       |
| RIE                        | Runtime Interface Emulator — shim dentro del contenedor lambda      |
| `_fuente`                  | Atributo que indica si la respuesta vino del cache o de DynamoDB   |
