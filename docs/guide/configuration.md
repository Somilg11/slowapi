# Configuration

Twelve-factor configuration without a dependency: declare a dataclass, and
values are read from the environment, coerced to the declared type, and
validated once at startup rather than the first time a request touches them.

## Built-in settings

```python
from slowapi.config import Settings

settings = Settings.load()          # reads .env, then the environment
```

| Field | Environment variable | Default |
| --- | --- | --- |
| `app_name` | `APP_NAME` | `"slowapi-app"` |
| `environment` | `ENVIRONMENT` | `"development"` |
| `debug` | `DEBUG` | `False` |
| `host` | `HOST` | `"127.0.0.1"` |
| `port` | `PORT` | `8000` |
| `server` | `SERVER` | `"auto"` |
| `workers` | `WORKERS` | `1` |
| `reload` | `RELOAD` | `False` |
| `log_level` | `LOG_LEVEL` | `"INFO"` |
| `log_json` | `LOG_JSON` | JSON in production |
| `secret_key` | `SECRET_KEY` | `""` |
| `max_body_size` | `MAX_BODY_SIZE` | 16 MB |
| `cors_origins` | `CORS_ORIGINS` | `[]` |
| `trusted_hosts` | `TRUSTED_HOSTS` | `["*"]` |
| `docs_enabled` | `DOCS_ENABLED` | off in production |

Two of these encode a policy worth knowing about:

```python
settings.show_docs          # False in production unless DOCS_ENABLED=true
settings.use_json_logs      # True in production unless LOG_JSON=false
settings.require_secret()   # raises in production if SECRET_KEY is unset
```

`require_secret()` is the important one. In development it returns a marked
placeholder; in production it refuses to start:

```
ConfigurationError: SECRET_KEY must be set in production.
Generate one with `python -m slowapi secret`.
```

An application that boots with a default signing key is an application whose
sessions can be forged.

## Your own settings

```python
from dataclasses import dataclass, field

from slowapi.config import from_env


@dataclass
class AppSettings:
    database_url: str                       # required: no default
    redis_url: str = "redis://localhost:6379/0"
    pool_size: int = 10
    feature_flags: list[str] = field(default_factory=list)
    request_timeout: float = 5.0


settings = from_env(AppSettings)            # DATABASE_URL, REDIS_URL, POOL_SIZE, ...
```

Coercion uses the same engine as request validation, so `POOL_SIZE=abc` fails
with a clear message rather than a `TypeError` three layers deep. Missing
required fields fail at load. Lists are comma-separated in the environment.

Namespace them with a prefix:

```python
settings = from_env(AppSettings, prefix="MYAPP_")     # MYAPP_DATABASE_URL
```

## `.env` files

```bash
# .env — never committed; .env.example is
ENVIRONMENT=development
DEBUG=true
DATABASE_URL=postgresql://localhost/dev
SECRET_KEY=generate-me
```

```python
from slowapi.config import load_dotenv

load_dotenv()                       # or Settings.load(), which calls it
```

Real environment variables win over `.env` by default, so a stray file copied
into a container cannot override the deployment. Pass `override=True` to
reverse that, which is occasionally right in tests and never right in
production.

## Wiring settings into the app

Register them as a provider so services can inject them:

```python
from slowapi import InjectionToken, Provider, SlowAPI

SETTINGS = InjectionToken("SETTINGS")

settings = from_env(AppSettings)

app = SlowAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url="/docs" if settings.show_docs else None,
    openapi_url="/openapi.json" if settings.show_docs else None,
    max_body_size=settings.max_body_size,
    providers=[Provider.value(SETTINGS, settings)],
)
```

```python
@injectable()
class Database:
    def __init__(self, config: AppSettings = Inject(SETTINGS)) -> None:
        self.pool = create_pool(config.database_url, size=config.pool_size)
```

## Environments

Keep the difference between environments in *values*, not in branches:

```python
# Good: one code path, different values
app.use(SessionMiddleware(settings.require_secret(), secure=settings.is_production))

# Avoid: two code paths, only one of which is ever tested in CI
if settings.is_production:
    app.use(SessionMiddleware(SECRET, secure=True))
else:
    app.use(SessionMiddleware("dev", secure=False))
```

## Secrets

Do not put secrets in `.env` files in production. Use your platform's secret
manager and let it populate the environment — AWS Secrets Manager, GCP Secret
Manager, Kubernetes secrets, Docker secrets. The code does not change; only
where the value comes from does.

Generate a strong key:

```bash
python -m slowapi secret            # 48 random bytes, URL-safe
```

Rotate without logging everyone out by keeping the previous key for
verification only:

```python
SessionMiddleware(NEW_SECRET, fallback_secrets=[OLD_SECRET])
```
