# Schema Compatibility Policy

## Overview

All event schemas in the fraud-detection-platform are managed via **Confluent Schema
Registry** using **Apache Avro** as the serialization format.  Every event that flows
through Kafka is validated against its registered schema at **publish time** — contract
violations are caught by the producer, not discovered downstream at 3 AM in the
persistence layer.

## Why Avro?

| Criterion | Avro | JSON Schema | Protobuf |
|---|---|---|---|
| Schema evolution rules | Built-in (forward, backward, full) | Limited | Good, but field numbering adds friction |
| Compact wire format | Binary, ~40-60% smaller than JSON | JSON text | Binary, slightly smaller than Avro |
| Schema Registry support | First-class (Confluent) | Supported but less mature | Supported |
| Human readability of schema | JSON-based `.avsc` files | JSON | `.proto` files |
| Language support | Broad (Python, Java, Go, JS) | Broad | Broad |

**Trade-off decision:** Avro gives us the best balance of compact wire format, mature
schema evolution, and first-class Confluent ecosystem support.  Protobuf would be
equally valid — the key decision is using *any* schema registry, not the specific format.

## Compatibility Level

**Default: BACKWARD**

Configured at the Schema Registry level (`SCHEMA_REGISTRY_SCHEMA_COMPATIBILITY_LEVEL`
in `docker-compose.yml`).

### What BACKWARD compatibility means

- **New consumers** (using the latest schema) **can read data written by old producers**
  (using a previous schema version).
- In practice this means:
  - You **can** add new fields — but they **must** have a default value.
  - You **can** remove fields — old data that still has the field will be silently ignored
    by new consumers.
  - You **cannot** rename fields (this is a delete + add, which is backward compatible
    only if the new field has a default).
  - You **cannot** change a field's type (e.g., `string` → `int`).

### Why not FORWARD or FULL?

| Level | Guarantees | Use case |
|---|---|---|
| BACKWARD | New reader can read old data | Default — simplest for evolving consumers |
| FORWARD | Old reader can read new data | Useful when you can't update all consumers at once |
| FULL | Both directions | Maximum safety, but most restrictive on changes |

We chose **BACKWARD** because our deployment model (all services deployed together via
ArgoCD) means consumers are always updated alongside schema changes.  If we move to
independent service deployments, we should upgrade to **FULL** compatibility.

## Schema Version Field

Every event carries a `schema_version` integer field (default: 1).  This is **not** the
Schema Registry's internal version ID — it's an application-level field for:

1. **Auditability** — When debugging, you can see exactly which schema version produced
   a given event in the `raw_event` JSONB column in PostgreSQL.
2. **Consumer routing** — If a consumer needs to handle multiple schema versions
   differently (e.g., a field was added in v2), it can branch on `schema_version`.
3. **Backfill identification** — Events replayed during backfill can be tagged with a
   different `schema_version` if the schema has evolved since the original event.

## Subject Naming Strategy

We use the **TopicNameStrategy** (Confluent default):

```
<topic-name>-value
```

Examples:
- `transactions-value`
- `risk_scores-value`
- `alerts-value`

This means each Kafka topic has exactly one value schema.  If we later need multiple
event types per topic, we'd switch to **RecordNameStrategy** — but for now, one schema
per topic keeps things simple.

## Schema Evolution Workflow

### Adding a new field (safe)

1. Add the field to the `.avsc` file **with a default value**.
2. Run the schema CI check (`schema-ci.yml`) — it validates backward compatibility.
3. Merge the PR.  The Schema Registry auto-registers the new version on first produce.
4. Update consumers to use the new field (they'll read `default` for old events).

### Removing a field (safe)

1. First, ensure no consumer depends on the field.
2. Remove the field from the `.avsc` file.
3. Run the schema CI check.
4. Merge.  Old events with the removed field are silently ignored by new consumers.

### Renaming a field (requires care)

1. Add the new field with a default value.
2. Deploy producers that write both the old and new field.
3. Migrate consumers to read from the new field.
4. Remove the old field.

This is a multi-step migration — never rename in a single PR.

### Changing a field type (breaking — not allowed)

This is a breaking change under BACKWARD compatibility.  Instead:

1. Add a new field with the desired type (e.g., `amount_cents` as `long` alongside
   `amount` as `double`).
2. Migrate consumers to the new field.
3. Deprecate and eventually remove the old field.

## CI Integration

The `schema-ci.yml` GitHub Actions workflow runs on every PR that touches `schemas/`.
It:

1. Starts a temporary Schema Registry (via Docker).
2. Registers the **current** (main branch) schemas.
3. Attempts to register the **PR's** schemas against the existing subjects.
4. Fails the PR if any schema change violates the compatibility policy.

This ensures **breaking changes never reach Kafka**.

## Registered Schemas

| Subject | Schema | Compatibility |
|---|---|---|
| `transactions-value` | `TransactionEvent` (transaction_event.avsc) | BACKWARD |
| `risk_scores-value` | `RiskScoreEvent` (risk_score_event.avsc) | BACKWARD |
| `alerts-value` | `AlertEvent` (alert_event.avsc) | BACKWARD |
