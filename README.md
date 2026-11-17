# Ephemeral Code Execution IaaS — Firecracker-style MicroVM Control Plane

Backend serverless multi-tenant construido con **AWS CDK (`cdklocal`)**, **LocalStack (`localstack/localstack:4.14`)**, **Redis (`redis:7-alpine`)**, **DynamoDB (Single-Table Design)** y **Python 3.11**, siguiendo la arquitectura de tres contenedores del Módulo 8 (`8_proyecto_final_entorno.pptx`).

---

## 1. Arquitectura de Contenedores (`docker-compose.yml`)

| Contenedor | Imagen | Puerto | Rol |
| :--- | :--- | :--- | :--- |
| `emulador` | `localstack/localstack:4.14` | `4566:4566` | Emula **API Gateway**, **Lambda** (`LAMBDA_EXECUTOR=docker`) y **DynamoDB** |
| `redis` | `redis:7-alpine` | `6379:6379` | Caché in-memory con persistencia AOF (`--appendonly yes`) para patrones **Cache-Aside** y **TTL (`EXPIRE`)** |
| `cdk-dev` | `python:3.11-slim` + `aws-cdk` + `aws-cdk-local` | CLI (`bash`) | Entorno de desarrollo con `AWS_ENDPOINT_URL=http://emulador:4566` y `REDIS_HOST=redis` |

---

## 2. Estructura de Carpetas

```text
Aurora/
├── Dockerfile                    # uv (uv sync --frozen, venv en /opt/venv)
├── docker-compose.yml
├── pyproject.toml                # Dependencias del proyecto (uv, package = false)
├── uv.lock                       # Lockfile reproducible de uv
├── pytest.ini
└── app/
    ├── cdk.json
    ├── pytest.ini
    ├── infra/
    │   ├── __init__.py
    │   ├── app.py
    │   └── stack.py                # DynamoDB Single-Table + Lambdas Python 3.11 + LambdaRestApi
    ├── layers/
    │   └── dependencias/
    │       ├── pyproject.toml     # Dependencias compartidas de las Lambdas (uv)
    │       └── uv.lock
    ├── lambdas/
    │   ├── api/
    │   │   ├── __init__.py
    │   │   ├── handler.py          # Router principal de API Gateway
    │   │   ├── auth.py             # PBKDF2-HMAC-SHA256 + JWT HS256
    │   │   ├── cache.py            # Redis Cache-Aside con EXPIRE (TTL) y _fuente
    │   │   ├── quota.py            # Control de cuotas mensuales (FREE=14,400s, PRO=ilimitado)
    │   │   └── sandbox.py          # Runner efímero aislado con telemetría microVM Firecracker
    │   └── health/
    │       ├── __init__.py
    │       └── handler.py          # Health check dedicado
    └── tests/
        ├── __init__.py
        ├── conftest.py             # Fixtures con moto (DynamoDB) y fakeredis
        ├── test_cache.py
        ├── test_auth_and_endpoints.py
        └── test_quota_and_sandbox.py
```

---

## 3. Diseño DynamoDB Single-Table (`EphemeralSandboxTable`)

- **Partition Key (`PK`)**: `STRING`
- **Sort Key (`SK`)**: `STRING`
- **GSI1** (sparse, `INCLUDE` projection): `GSI1PK=ACCOUNT#<account_id>`, `GSI1SK=<created_at ISO>` — acceso "ejecuciones recientes de una cuenta" (solo los ítems `EXEC#` lo llevan)
- **TTL**: atributo `ttl` (epoch) — las ejecuciones expiran automáticamente
- **PITR + deletion protection + RETAIN + SSE** (AWS real; LocalStack acepta las opciones pero no las hace cumplir; en local se recrean borradores manualmente)
- **Billing Mode**: `PAY_PER_REQUEST`
- **Sin `Scan` en ninguna parte** — toda lectura es `GetItem` o `Query` (PK/SK o GSI1)

| Entidad | `PK` | `SK` | Atributos Principales |
| :--- | :--- | :--- | :--- |
| **Account** | `ACCOUNT#<id>` | `METADATA` | `account_id`, `email`, `username`, `tier` (`FREE` \| `PRO`), `password_hash`, `created_at` |
| **Email Index** | `EMAIL#<email>` | `LOOKUP` | `account_id`, `email`, `tier` |
| **Monthly Quota** | `QUOTA#<account_id>#<YYYY-MM>` | `SUMMARY` | `account_id`, `period`, `tier`, `accumulated_seconds`, `limit_seconds` (`14400` \| omitido en PRO), `execution_count` |
| **Execution** | `EXEC#<execution_id>` | `METADATA` | `execution_id`, `account_id`, `code`, `runtime`, `duration`, `timeout_enforced`, `status`, `stdout`, `stderr`, `telemetry` (`vm_id`, `boot_time_ms`, `teardown_time_ms`, `state_transitions`), `ttl`, `GSI1PK`, `GSI1SK` |
| **API Key (meta)** | `APIKEY#<account_id>` | `METADATA` | `account_id`, `api_key_id`, `api_key_prefix`, `api_key_hash` (SHA-256), `created_at`, `updated_at`, `last_used_at` |
| **API Key (lookup)** | `APIKEYLOOKUP#<sha256>` | `LOOKUP` | `account_id`, `api_key_id`, `created_at` — lookup O(1) por hash |

---

## 4. Flujo de Trabajo con Docker Compose y `cdklocal`

```bash
# 1. Levantar los 3 contenedores
docker compose up -d --build

# 2. Entrar al contenedor de desarrollo
docker compose exec cdk-dev bash

# 3. Preparar el emulador para despliegues CDK
cdklocal bootstrap

# 4. Sintetizar y desplegar el stack en LocalStack
cdklocal deploy --require-approval never

# 5. Ejecutar pruebas unitarias (con moto y fakeredis, sin requerir el emulador)
#    En el contenedor (venv de uv ya en PATH):
pytest -v
#    En el host, con uv nativo:
uv run pytest -v
```

> **Dependencias con uv (astral):** el proyecto usa `pyproject.toml` + `uv.lock`; no se usa `pip`
> ni `requirements.txt` en ninguna parte.
> Instalar en el host: `uv sync --frozen`. Dentro del `Dockerfile` se ejecuta
> `uv sync --frozen` en `/opt/venv` (fuera de `/app` para sobrevivir al bind mount).
> La lambda layer `app/layers/dependencias/` (proyecto uv propio) se empaqueta con
> `./scripts/build-layer.sh`, que exporta su `uv.lock` y la instala en
> `app/layers/dependencias/python/` con `uv` — sin archivos `requirements.txt`.

---

## 5. Endpoints del API

| Método | Ruta | Auth | Descripción |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | No | Estado del servicio y configuración del plano de control |
| `POST` | `/auth/register` | No | Registra cuenta (`FREE` o `PRO`), inicializa cuota mensual y retorna JWT |
| `POST` | `/auth/login` | No | Autentica credenciales y emite token JWT |
| `POST` | `/execute` | `Bearer <JWT>` | Verifica cuota mensual (`429` si excedida), limita el timeout al saldo restante de cuota, ejecuta el código en un sandbox efímero aislado y guarda telemetría microVM |
| `GET` | `/executions` | `Bearer <JWT>` | Lista ejecuciones de la cuenta (50 más recientes) vía **GSI1** (`Query`, proyección INCLUDE — payload fino, sin code/stdout) |
| `GET` | `/executions/{id}` | `Bearer <JWT>` (solo dueño) | Obtiene resultado y telemetría vía **Cache-Aside** (`"_fuente": "cache" \| "dynamodb"`) |
| `GET` | `/api-key` | `Bearer <JWT>` o `X-API-Key` | Metadata de la API key de la cuenta (`has_key`, `api_key_id`, `api_key_prefix`, `created_at`, `last_used_at`) — nunca el valor |
| `POST` | `/api-key/rotate` | `Bearer <JWT>` o `X-API-Key` | Genera (o reemplaza) la API key. El valor en claro se devuelve **una sola vez**; se guarda SHA-256 |
| `DELETE` | `/api-key` | `Bearer <JWT>` o `X-API-Key` | Revoca la API key (los agentes reciben 401 de inmediato) |

### API keys para agentes/scripts

Cada cuenta puede generar una **API key** para llamar a la API **sin sesión interactiva**:

```bash
# rotar (solo la primera vez usa Bearer)
curl -X POST -H "Authorization: Bearer $JWT" https://api-aurora.jeanvides.com/api/v1/api-key/rotate

# usar desde cualquier script/agente
curl -X POST \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code":"print(6*7)","timeout":5}' \
  https://api-aurora.jeanvides.com/api/v1/execute
```

- Lookup O(1) por hash SHA-256 (`APIKEYLOOKUP#<hash>`) — sin scan, sin leer la key del disco salvo en la creación.
- Revocación/rotación atómica por transacción; la key anterior deja de funcionar inmediatamente.
- `/auth/*`, `/health` y la rotación con Bearer no requieren API key.
## 6. Frontend Minimalist Brutalist Control Plane (`frontend/`)

El proyecto incluye una aplicación web completa en **Next.js 14 (App Router + TypeScript + Tailwind CSS + componentes estilo shadcn)** bajo `frontend/`, diseñada con estética **Brutalist Minimalist** (sin emojis, sin gradientes, bordes sólidos de `2px`, sombras offset `shadow-brutal` y tipografía monoespaciada).

### Características del Frontend
- **Landing Page (`/`)**: Arquitectura del plano de control Firecracker MicroVM, esquema DynamoDB Single-Table (`ACCOUNT#<id>`, `QUOTA#<account_id>#<YYYY-MM>`, `EXEC#<execution_id>`), y planes de suscripción `FREE` y `PRO` con compra dummy instantánea (**0.00 USD / sin paywall**).
- **Auth Context & Refresh Manager (`src/context/auth-context.tsx` y `/auth`)**:
  - Registro (`POST /auth/register`), inicio de sesión (`POST /auth/login`), rotación de JWT (`POST /auth/refresh`), y acceso en 1 clic con cuenta Demo Operator (`operator@aurora.microvm`).
  - Polling en tiempo real configurable (`2s`, `5s`, `15s` o `MANUAL`) y sincronización inmediata tras cada ejecución.
- **Dashboard con Editor Python 3.11, Costes y Telemetría (`/dashboard`)**:
  - **Editor y Dispatcher Manual de Código Python 3.11**: Numeración de líneas, plantillas de prueba (SHA-256 benchmark, bucle con `stderr`, prueba de timeout estricto `time.sleep(5)` y prueba de límite de memoria `32MB > 16MB`), selector de `timeout` y `memory_limit_mb`, y botón manual **DISPATCH MICROVM (`POST /execute`)**.
  - **Métricas de Costes y Uso (`DAILY`, `WEEKLY`, `MONTHLY`, `PROJECTED`)**: Cálculo en tiempo real de segundos acumulados, `GB-seconds`, invocaciones microVM, coste por cómputo y proyección a fin de mes.
  - **Simulador de Cuotas y Cache-Aside (`_fuente`)**: Botones en 1 clic para simular agotamiento de cuota (`14,400s` $\rightarrow$ `HTTP 429`), límite estricto de timeout (`14,399.5s` $\rightarrow$ `0.5s` restantes), compra dummy instantánea de `PRO` (`UNLIMITED`), e inspección de `GET /executions/{id}` observando el cambio de `_fuente: "dynamodb"` a `_fuente: "cache"`.

### Ejecutar el Frontend
```bash
cd frontend
npm install
npm run dev
# Abrir http://localhost:3000
```


## 7. Acceso público — Cloudflare Tunnel + Vercel

El stack corre **100% local** en este computador; el API Gateway se expone por
un **túnel nombrado de Cloudflare** con scope estricto.

| Pieza | Valor |
| :--- | :--- |
| URL pública del API | `https://api-aurora.jeanvides.com` (CNAME → túnel `aurora-api`, cuenta `jeanvides.com`) |
| Scope del túnel | SOLO `^/restapis` → `http://emulador:4566` (API Gateway). Todo lo demás → HTTP 404 |
| Servicio | `cloudflared` en `docker-compose.yml` (corre como contenedor; DNS del ISP en `dns: [181.70.124.110, 190.240.115.150]` porque 8.8.8.8 solo devolvía AAAA y el host no resuelve DNS de Go) |
| Config | `cloudflared/config.yml` (en este repo) |
| Frontend | Vercel: **`vyde/aurora-microvm-frontend`** (scope `vyde`), `Next.js 14` |

**Frontend en Vercel** — el proxy `/api/v1/*` lee `LOCALSTACK_API_URL`
(production/preview/development):

```bash
npx vercel env add LOCALSTACK_API_URL production --scope vyde   # valor: URL base completa
npx vercel --prod --scope vyde --yes
```

> La URL base contiene el **id del API** (`.../restapis/<api-id>/prod/_user_request_`).
> El api-id **cambia en cada `cdklocal deploy`**: actualiza `LOCALSTACK_API_URL`
> (3 entornos) y redepliega. Alternativa robusta: usar discovery con
> `LOCALSTACK_HOST=http://localhost:4566` en local y refrescar este valor en cada deploy.

Verificación rápida:

```bash
curl https://api-aurora.jeanvides.com/restapis/<api-id>/prod/_user_request_/health
# {"status": "healthy", ...}
```

### Notas de infraestructura

- **Lambda Layers son feature Pro de LocalStack**: como workaround Community, la
  `DependenciasLayer` se monta directamente en los contenedores lambda
  (`LAMBDA_DOCKER_FLAGS=-v .../layers/dependencias/python:/opt/python:ro` en `docker-compose.yml`).
  En AWS real, la capa funciona sin workaround.
- **DNS del host**: `/etc/resolv.conf` está vacío (NetworkManager), así que los
  binarios Go (cloudflared CLI) y el daemon Docker no resuelven; por eso el CDK se
  despliega desde el host con `npx cdklocal` y cloudflared corre en contenedor con DNS explícito.
- **root-owned `app/cdk.out/`**: quedó de ejecuciones anteriores como root (sin sudo no se borra);
  los despliegues host usan `-o /tmp/opencode/synth.out`. Para limpiarlo: `sudo rm -rf app/cdk.out`.
- `cdk.out/` (synth) y `app/layers/dependencias/python/` (deps de la capa) son artefactos de build generados.
- **Contenedores lambda huérfanos**: LocalStack recrea un contenedor por invocación/función que se
  redeploya y NO los recicla agresivamente — con el tiempo se acumulan (9+ tras un `cdklocal deploy`).
  Si la API se pone lenta o los invokes fallan con `No route to host`, poda con
  `docker rm -f $(docker ps -aq --filter 'name=lambda-proyectofinalstack')` y espera al frio arranque.
  El frontend además polea `/health`/`/quota`/`/executions` cada 15s para no calentar de más el emulador.
- LocalStack pierde el estado al recrearse (arranque en memoria): la tabla se recrea con
  `cdklocal deploy`; los datos (cuenta demo, ejecuciones) se pierden en cada destroy/deploy.

---

## 8. DynamoDB Single-Table — revisión y correcciones aplicadas

Analizado contra datos reales y luego corregido en código:

1. **Duplicación completa del EXEC** (listado O(1) mediante copia `ACCOUNT#<id>/EXEC#<iso>#<id>` con code+stdout+telemetry ≈ 1KB duplicado por ejecución) → eliminada. Listado ahora vía **GSI1** con proyección INCLUDE (solo atributos de UI; sin `code`/`stdout`).
2. **Cero índices** → añadido **GSI1** (`GSI1PK=ACCOUNT#<id>`, `GSI1SK=<created_at ISO>`), sparse, PAY_PER_REQUEST, proyección INCLUDE de 10 atributos.
3. **Register no atómico** (3 PutItems sueltos) → `TransactWriteItems` con condiciones `attribute_not_exists(PK)`; la caché se puebla solo DESPUÉS del commit; fallo parcial imposible. Duplicado → HTTP 409.
4. **Race de unicidad email/account_id** → `ConditionExpression` sobre los 3 ítems dentro de la transacción: imposible pisar una cuenta ajena o un email existente.
5. **Race de reserva de cuota** (check-then-act: N ejecuciones paralelas sobrepasaban el tope de 14,400s) → **reserva atómica previa a la ejecución**: `UpdateCommand` con `ConditionExpression: accumulated_seconds = :old` (concurrency control optimista, `ConsistentRead`): cada ejecución reserva `min(timeout_solicitado, saldo)` ANTES de correr; al final, `settle_quota_usage` devuelve los segundos no consumidos. Strict cap garantizado aun con concurrencia.
6. **Ejecutions sin caducidad** → atributo `ttl` (epoch, 30 días por defecto, `EXECUTION_TTL_DAYS`) + `TimeToLiveSpecification` en la tabla.
7. **Sin protección de datos** → `PointInTimeRecovery`, `deletion_protection`, `RemovalPolicy.RETAIN`, `SSE` (AWS-managed). LocalStack acepta pero no hace cumplir PITR/deletion-protection; en AWS real aplican.
8. **EMAIL#LOOKUP desactualizado** (tier quedaba `FREE` tras upgrade a PRO) → el upgrade a PRO también actualiza el LOOKUP.
9. **EXECuciones públicas por ID** (cualquiera con el id leía code/stdout — IDOR) → `GET /executions/{id}` ahora **exige Bearer JWT y verifica ownership** (`403` si pertenece a otra cuenta). Además: `code` tope 65,536 chars (HTTP 413), JWT_SECRET sin default (obligatorio por entorno).

**Scan**: ninguna lectura por `Scan` en el código (`get_item`/`query` exclusivamente).


---

## 9. Terminal agent (LangChain + OpenRouter)

En `agent/` hay un agente conversacional en terminal para la API de Aurora:

```bash
cd agent && uv sync
OPENROUTER_API_KEY=... uv run python agent.py
```

Herramienta del agente: `execute_python(code, timeout=5.0, memory_limit_mb=128)` →
`POST /execute`. Por defecto usa el modelo gratuito
`cohere/north-mini-code:free` (el modelo que pediste, `thinkingmachines/inkling-2-small:free`,
está restringido a harnesses agénticos listados en OpenRouter — devuelve 403 por API directa).
Overridable con `OPENROUTER_MODEL=...`. Requiere una cuenta Aurora (`/auth`) o
`AURORA_API_KEY` en el entorno. Ver `agent/README.md`.
