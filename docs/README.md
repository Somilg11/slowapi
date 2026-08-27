# SlowAPI documentation

SlowAPI is a Python web framework that is a WSGI application and an ASGI
application at the same time, with Express-style ergonomics, FastAPI-style
typing, and NestJS-style structure.

If you have ten minutes, read [Quickstart](quickstart.md). If you have an
hour, read the guide in order — it is written to be read start to finish, not
only grepped.

## Getting started

| Page | What it covers |
| --- | --- |
| [Installation](installation.md) | Install, optional extras, supported versions |
| [Quickstart](quickstart.md) | A working service in ten minutes |
| [Migrating](migration.md) | Coming from Flask, FastAPI, Express, or NestJS |

## Guide

Read in this order the first time.

| Page | What it covers |
| --- | --- |
| [Routing](guide/routing.md) | Paths, converters, both syntaxes, reverse URLs |
| [Requests](guide/requests.md) | Reading query, headers, cookies, bodies, uploads |
| [Responses](guide/responses.md) | The two response styles, files, streaming, cookies |
| [Validation](guide/validation.md) | Typed parameters, DTOs, constraints, error shape |
| [Middleware](guide/middleware.md) | The onion, built-ins, writing your own |
| [Dependency injection](guide/dependency-injection.md) | `Depends`, providers, scopes, teardown |
| [Controllers and modules](guide/controllers-modules.md) | Structuring an application that grew |
| [Guards, interceptors, pipes](guide/guards-interceptors-pipes.md) | Cross-cutting concerns, in order |
| [Serialization](guide/serialization.md) | Deciding what leaves the process |
| [Templates and static files](guide/templates-static.md) | Server-rendered pages |
| [Errors](guide/errors.md) | Exception handling and what clients are told |
| [Configuration](guide/configuration.md) | Typed settings from the environment |
| [Testing](guide/testing.md) | The dual-protocol test client |
| [Security](guide/security.md) | What is on by default, and what is not |
| [Deployment](guide/deployment.md) | Choosing a server, Docker, workers, health |

## Internals

| Page | What it covers |
| --- | --- |
| [Dual-protocol dispatch](internals/dual-protocol.md) | How one handler serves two protocols |
| [Architecture](internals/architecture.md) | Module map and the request lifecycle |

## Reference

| Page | What it covers |
| --- | --- |
| [CLI](reference/cli.md) | `slowapi run`, `routes`, `openapi`, `secret`, `new` |
| [API reference](reference/api.md) | Every public name, grouped |
| [FAQ](faq.md) | Questions that come up repeatedly |

## Project

| Page | What it covers |
| --- | --- |
| [Growth plan](growth.md) | Why this exists and where it is going |
| [Contributing](../CONTRIBUTING.md) | How to work on SlowAPI |
| [Changelog](../CHANGELOG.md) | What changed, and when |
