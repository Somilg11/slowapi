# rest-api

A typed CRUD service. Start here if you are coming from FastAPI.

```bash
python -m slowfw run main:app --reload
```

```bash
curl localhost:8000/tasks
curl "localhost:8000/tasks?limit=2&done=false"
curl -X POST localhost:8000/tasks -H 'content-type: application/json' \
     -d '{"title":"Write the docs"}'
curl -X PATCH localhost:8000/tasks/1 -H 'content-type: application/json' \
     -d '{"done":true}'
curl -X DELETE localhost:8000/tasks/1 -i
```

## What to notice

- **`internal_note` never appears in a response.** It is on the model, marked
  `hidden()`. Over-serialisation is the most common way an API leaks data;
  here it takes a deliberate act to leak.
- **`?limit=999` is a 422, not a crash and not a full table scan.** The bound
  is declared once on the dependency and enforced everywhere it is used.
- **`/docs` describes exactly this code.** The schema is generated from the
  same annotations that do the validating, so it cannot drift.
- **`created_at` is emitted as `createdAt`.** Python naming inside, JSON
  naming outside, no mapping layer.

```bash
python -m slowfw routes main:app     # the route table
python -m slowfw openapi main:app    # the schema, for CI diffing
```
