"""Postgres access for the Sales pipeline (ADR 0003, ADR 0005).

The connection string comes from a single environment variable, `DATABASE_URL`,
so the same code runs from a laptop and from a GitHub Actions runner. Bringing a
database up to date is one command:

    python db.py            # or: python db.py migrate

The schema is an ordered list of `migrations/NNNN-<slug>.sql` files applied once
each and recorded in `schema_migrations` (ADR 0015). Re-running `migrate` when
nothing is pending does nothing. There is no down command: a mistake is
corrected by writing the next migration, and a fresh database is built by
running every migration from `0001`.

The objects (see `migrations/`):

- `raw_toast_responses` --- raw Toast responses as `jsonb`, the replay/audit
  safety net. `insert_raw_response` saves one; `read_raw_responses` reads them
  back for re-normalization without contacting Toast.
- Canonical Sales as a source-to-product model (ADR 0005). The fact `sales` is
  keyed one row per `(date, restaurant_guid, source_type, source_name)`;
  `upsert_sales` writes fact rows with replace-on-repeat semantics.
  `product_sources` maps each source up to one Product; `upsert_product_sources`
  seeds that map. `read_sales` reads the `product_sales` view, which rolls the
  fact up through the map to the `(product, date, quantity)` frame the readers
  consume — so the write side is at source grain and the read side at Product
  grain, exactly as ADR 0005 lays out.
- The config-driven, write-once Demand Forecast log (ADR 0006).
  `read_active_config` returns the configuration the daily job runs under, with
  its version stamped in; `insert_forecasts` appends the log rows
  `forecast_engine.run_forecasts` produces, write-once, so a retry fills gaps
  and never rewrites what a past morning predicted.
- Scheduled Reports as a subscription table over that log (ADR 0010).
  `read_due_reports` returns the active reports whose weekdays include the day
  the caller names, so which reports exist and when they fire is data rather
  than a workflow file per report.

`source_name` is stored in the normalized (stripped, lower-cased) form
`normalize.py` matches on; callers pass names already normalized (as
`normalize.py` produces them), and both the fact and the map store them that
way so the view's join lines up.
"""
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

import env

CONNECTION_ENV_VAR = "DATABASE_URL"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# `NNNN-<slug>.sql`. The four-digit prefix is the version — the identity the
# database records — and the slug is for the reader, so renaming a slug does not
# re-apply a migration. The pattern is enforced rather than assumed: a file the
# runner cannot parse is a file it would otherwise silently skip.
MIGRATION_FILENAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.sql$")

# Bootstrap DDL for the ledger itself. This cannot be migration `0001` — the
# runner needs the table to know what `0001` is — so it is created on demand,
# idempotently, before anything is applied. RLS with no policies for the same
# reason as every other table in `public` (see `0001-baseline.sql`): Supabase
# exposes this schema through its Data API, and the pipeline's `postgres` role
# bypasses RLS while `anon`/`authenticated` get nothing.
MIGRATIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    filename   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
"""


def connection_string(environ: Optional[Mapping[str, str]] = None) -> str:
    """The Postgres connection string from the environment — from `.env` on a
    laptop, from secrets on a runner (see `env.load_env`). Raising here, rather
    than letting psycopg fail on an empty DSN, keeps the missing-config error
    pointing at the one variable a developer has to set."""
    return env.require(
        CONNECTION_ENV_VAR,
        env.resolve(environ),
        RuntimeError,
        "Point it at your Postgres database, e.g. "
        f"{CONNECTION_ENV_VAR}='postgresql://user:pass@host:5432/dbname'. "
        "See docs/postgres.md.",
    )


def connect(environ: Optional[Mapping[str, str]] = None, **kwargs) -> psycopg.Connection:
    """Open a connection to the database named by `DATABASE_URL`. Extra keyword
    arguments pass straight through to `psycopg.connect` — the reader seam uses
    `connect_timeout` so an unreachable host fails fast rather than hanging on a
    TCP timeout."""
    return psycopg.connect(connection_string(environ), **kwargs)


# ---------------------------------------------------------------------------
# Migrations (ADR 0015)
# ---------------------------------------------------------------------------


def migration_files(directory: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """Every migration in version order, as `(version, path)`.

    Sorting is on the four-digit prefix, so ordering is the number and never the
    slug — `0009-...` before `0010-...`, which a plain filename sort gets right
    today and gets wrong the moment a version needs five digits. A `.sql` file
    that does not match `NNNN-<slug>.sql` raises rather than being skipped: the
    failure mode this guards against is a migration that looks committed, never
    runs, and is noticed only when a later one fails against a schema that never
    got its column. Duplicate versions raise for the same reason — two PRs each
    adding `0007` merge cleanly in git and would leave one of them permanently
    unapplied."""
    directory = MIGRATIONS_DIR if directory is None else directory
    found: Dict[str, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILENAME.match(path.name)
        if match is None:
            raise RuntimeError(
                f"{path.name} is not a migration filename. Migrations are "
                f"`NNNN-<slug>.sql`, e.g. 0002-add-selections-table.sql."
            )
        version = match.group(1)
        if version in found:
            raise RuntimeError(
                f"Two migrations share version {version}: {found[version].name} "
                f"and {path.name}. Renumber the later one."
            )
        found[version] = path
    return sorted(found.items())


def applied_versions(conn: psycopg.Connection) -> Set[str]:
    """The versions `schema_migrations` records as applied. Creates the ledger
    if it is not there yet, so a database that has never been migrated reads as
    "nothing applied" rather than failing on a missing table."""
    conn.execute(MIGRATIONS_TABLE_DDL)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def pending_migrations(
    conn: psycopg.Connection, directory: Optional[Path] = None
) -> List[Tuple[str, Path]]:
    """The migrations on disk that this database has not applied, in order.

    Two ways the repo and the database can disagree are refused here rather than
    papered over. A pending version *below* the highest applied one means a
    branch was merged out of order, and applying it now would run it against a
    schema its author never saw. A recorded version with no file means the
    database has run something this checkout does not contain — the schema is
    ahead of the code, and every assumption the code makes about it is
    unverified."""
    applied = applied_versions(conn)
    on_disk = migration_files(directory)
    known = {version for version, _ in on_disk}

    orphaned = sorted(applied - known)
    if orphaned:
        raise RuntimeError(
            f"The database has applied {', '.join(orphaned)}, which this "
            f"checkout has no file for. It is running a schema newer than this "
            f"code — update the checkout rather than migrating."
        )

    pending = [(version, path) for version, path in on_disk if version not in applied]
    if pending and applied:
        newest_applied = max(applied)
        late = [version for version, _ in pending if version < newest_applied]
        if late:
            raise RuntimeError(
                f"Migration(s) {', '.join(late)} are pending but {newest_applied} "
                f"has already been applied — a branch merged out of order. "
                f"Renumber them above {newest_applied} so they run against the "
                f"schema they were written for."
            )
    return pending


def apply_migrations(
    conn: psycopg.Connection, directory: Optional[Path] = None
) -> List[str]:
    """Apply every pending migration in order and return the versions applied.

    Each file gets its own transaction, so a migration that fails partway leaves
    nothing behind and the ones before it stay applied — Postgres has
    transactional DDL, and this is the rollback that matters day to day (ADR
    0015). The ledger row is written inside that same transaction, so a file
    can never be recorded as applied without its statements having committed,
    nor commit without being recorded.

    The caveat that buys: statements Postgres refuses to run inside a
    transaction block — `CREATE INDEX CONCURRENTLY`, `VACUUM` — cannot go in a
    migration. `docs/postgres.md` says what to do instead."""
    # Autocommit first, and only then read the ledger: `pending_migrations`
    # creates `schema_migrations` if it is missing, which outside autocommit
    # opens an implicit transaction that psycopg then refuses to switch out of.
    # Autocommit here does not mean "no transactions" — it means *these*
    # explicit `conn.transaction()` blocks are the only ones, one per file,
    # rather than one ambient transaction spanning every migration.
    was_autocommit = conn.autocommit
    conn.autocommit = True
    try:
        pending = pending_migrations(conn, directory)
        for version, path in pending:
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)",
                    (version, path.name),
                )
    finally:
        conn.autocommit = was_autocommit
    return [version for version, _ in pending]


def baseline(
    conn: psycopg.Connection, version: str, directory: Optional[Path] = None
) -> None:
    """Record `version` as applied without running it.

    This exists for exactly one moment: the live database was built by applying
    `schema.sql` long before migrations existed, so `0001` is already true of it
    and running it would be a no-op at best. Marking it applied is what makes an
    already-live schema migration zero (ADR 0015). No DDL runs and no row is
    touched. Re-running is harmless — the insert is a no-op once the row is
    there — so a half-finished baseline can simply be repeated."""
    files = dict(migration_files(directory))
    if version not in files:
        raise RuntimeError(
            f"No migration {version} to baseline. Known: {', '.join(files) or 'none'}."
        )
    conn.execute(MIGRATIONS_TABLE_DDL)
    conn.execute(
        "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s) "
        "ON CONFLICT (version) DO NOTHING",
        (version, files[version].name),
    )
    conn.commit()


def insert_raw_response(
    conn: psycopg.Connection,
    restaurant_guid: str,
    business_date,
    response,
    fetched_at=None,
) -> None:
    """Save one raw Toast response as jsonb. `business_date` accepts anything
    Postgres reads as a date (a `date`, or a 'YYYY-MM-DD' string). `fetched_at`
    defaults to the database's `now()` when omitted."""
    conn.execute(
        "INSERT INTO raw_toast_responses "
        "(restaurant_guid, business_date, fetched_at, response) "
        "VALUES (%s, %s, COALESCE(%s, now()), %s)",
        (restaurant_guid, business_date, fetched_at, Jsonb(response)),
    )
    conn.commit()


def read_raw_responses(
    conn: psycopg.Connection, business_date=None
) -> list:
    """The saved raw response payloads, oldest fetch first, for re-normalization
    without contacting Toast — each is a stored Toast response verbatim (a menu
    report is a JSON array of rows). Pass `business_date` to read one day's
    captures."""
    if business_date is None:
        rows = conn.execute(
            "SELECT response FROM raw_toast_responses ORDER BY fetched_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT response FROM raw_toast_responses "
            "WHERE business_date = %s ORDER BY fetched_at",
            (business_date,),
        ).fetchall()
    return [row[0] for row in rows]


def seed_products(
    conn: psycopg.Connection,
    mapping: Dict[str, Iterable[tuple]],
) -> None:
    """Seed the Product mapping without committing: `mapping` is
    `{product_name: [(source_type, source_name), ...]}`, many sources to one
    Product. Idempotent — a Product is inserted once and its sources are
    re-pointed rather than duplicated. Source names are stored verbatim, so pass
    them already normalized (see module docstring) — that is what lines the map
    up with the fact under the view's join.

    This does not commit, so it can join a larger transaction (the ticket 03
    migration writes the map, the raw shards and the fact in one transaction).
    `upsert_product_sources` wraps it with a commit for standalone callers."""
    with conn.cursor() as cur:
        for product, sources in mapping.items():
            # ON CONFLICT ... DO UPDATE (not DO NOTHING) so RETURNING yields the
            # id whether the Product is new or already present.
            product_id = cur.execute(
                "INSERT INTO products (name) VALUES (%s) "
                "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (product,),
            ).fetchone()[0]
            source_rows = [
                (product_id, str(source_type), str(source_name))
                for source_type, source_name in sources
            ]
            if source_rows:
                cur.executemany(
                    "INSERT INTO product_sources (product_id, source_type, source_name) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (source_type, source_name) "
                    "DO UPDATE SET product_id = EXCLUDED.product_id",
                    source_rows,
                )


def upsert_product_sources(
    conn: psycopg.Connection,
    mapping: Dict[str, Iterable[tuple]],
) -> None:
    """Seed the Product mapping and commit. See `seed_products` for the
    semantics; this is the standalone (self-committing) entry point."""
    seed_products(conn, mapping)
    conn.commit()


def sales_rows(frame: pd.DataFrame) -> List[tuple]:
    """The Sales fact frame as `(date, restaurant_guid, source_type,
    source_name, quantity)` tuples ready for Postgres: dates as python `date`,
    quantities as `float`. Pure, so the frame-to-rows mapping is testable
    without a database."""
    return [
        (
            pd.Timestamp(row.date).date(),
            str(row.restaurant_guid),
            str(row.source_type),
            str(row.source_name),
            float(row.quantity),
        )
        for row in frame.itertuples(index=False)
    ]


def upsert_sales(conn: psycopg.Connection, frame: pd.DataFrame) -> None:
    """Write Sales fact rows with replace-on-repeat semantics: a second write of
    the same `(date, restaurant_guid, source_type, source_name)` replaces that
    row's quantity rather than adding a duplicate. This is what lets ADR 0004's
    daily job re-pull a business date across three days without accumulating
    duplicates."""
    rows = sales_rows(frame)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sales (date, restaurant_guid, source_type, source_name, quantity) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (date, restaurant_guid, source_type, source_name) "
            "DO UPDATE SET quantity = EXCLUDED.quantity",
            rows,
        )
    conn.commit()


# The fact's columns, and the subset that is its primary key — the upsert's
# COPY target, SELECT list and ON CONFLICT key are all spelled from these.
SALES_KEY_COLUMNS = ("date", "restaurant_guid", "source_type", "source_name")
SALES_COLUMNS = SALES_KEY_COLUMNS + ("quantity",)


def bulk_upsert_sales(conn: psycopg.Connection, rows: Iterable[tuple]) -> int:
    """Load many fact rows the way the ticket 03 migration needs and the daily
    `upsert_sales` is the wrong tool for: COPY every row into a temp staging
    table in one stream, then a single `INSERT ... SELECT ... ON CONFLICT` upsert
    into `sales`. That keeps the atomic replace-on-repeat semantics the daily job
    depends on — a repeat key still replaces its quantity — while paying one
    round trip instead of one per row over the whole history.

    `rows` are `(date, restaurant_guid, source_type, source_name, quantity)`
    tuples (as `sales_rows` produces). Callers must not hand in two rows with the
    same key — dedupe upstream — so the staged SELECT never affects a target row
    twice. Returns the number of rows staged. Does not commit: the migration
    wraps the raw shards, the map and the fact in one transaction and commits
    once, so a failure rolls back to nothing. The temp table is dropped on that
    commit (`ON COMMIT DROP`)."""
    columns = ", ".join(SALES_COLUMNS)
    key = ", ".join(SALES_KEY_COLUMNS)
    staged = 0
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE sales_staging "
            "(LIKE sales INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        with cur.copy(f"COPY sales_staging ({columns}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
                staged += 1
        cur.execute(
            f"INSERT INTO sales ({columns}) "
            f"SELECT {columns} FROM sales_staging "
            f"ON CONFLICT ({key}) DO UPDATE SET quantity = EXCLUDED.quantity"
        )
    return staged


def bulk_insert_raw_responses(
    conn: psycopg.Connection,
    shards: Iterable[tuple],
    batch_size: int = 1000,
) -> int:
    """Insert many raw-response shards in multi-row batches, not one statement
    per shard. Each shard is `(restaurant_guid, business_date, fetched_at,
    response)` — the ticket 03 migration shards each saved raw file into one row
    per `(restaurant, business_date)`, capture time from the filename. The insert
    is `ON CONFLICT (restaurant_guid, business_date, fetched_at) DO NOTHING`, so
    re-running the migration adds no duplicate captures. Returns the number of
    shards *actually* inserted — conflict-skipped rows are not counted, so a
    re-run over an already-loaded history returns 0. Does not commit — it joins
    the migration's single transaction."""
    shards = list(shards)
    if not shards:
        return 0
    inserted = 0
    with conn.cursor() as cur:
        for start in range(0, len(shards), batch_size):
            batch = shards[start : start + batch_size]
            values_sql = ",".join(["(%s, %s, %s, %s)"] * len(batch))
            params: List = []
            for restaurant_guid, business_date, fetched_at, response in batch:
                params += [restaurant_guid, business_date, fetched_at, Jsonb(response)]
            cur.execute(
                "INSERT INTO raw_toast_responses "
                "(restaurant_guid, business_date, fetched_at, response) "
                f"VALUES {values_sql} "
                "ON CONFLICT (restaurant_guid, business_date, fetched_at) DO NOTHING",
                params,
            )
            inserted += cur.rowcount
    return inserted


def read_sales(conn: psycopg.Connection) -> pd.DataFrame:
    """The canonical Sales history as a `(product, date, quantity)` frame, read
    from the `product_sales` view — the fact rolled up through the Product
    mapping, summed across locations and sources. Same shape
    `sales_history.load_sales_history()` returns today: `date` a datetime64
    column, `quantity` a float, sorted by `(date, product)`."""
    rows = conn.execute(
        "SELECT product, date, quantity FROM product_sales ORDER BY date, product"
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["product", "date", "quantity"])
    # Pinned to nanoseconds, not left to pd.to_datetime's inference: from pandas
    # 3.0 it infers second resolution from Postgres' date values, and the readers
    # merge this frame against Demand Forecasts that forecast.py pins to
    # datetime64[ns]. A coarser dtype here is the regression ticket 04 guards
    # against, and it must match what the parquet loader returned before.
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    frame["quantity"] = frame["quantity"].astype(float)
    return frame


# --- The config-driven, write-once Demand Forecast log (ADR 0006) ----------


def read_active_config(conn: psycopg.Connection) -> Dict:
    """The active forecast configuration, as the document `run_forecasts` takes.

    "Active" is `is_active = true`; nothing in the schema stops two rows holding
    that at once, so the newest `version` wins rather than leaving which
    configuration a morning ran under up to the planner. The row's `version` is
    stamped into the returned document under `"version"` (winning over any
    `"version"` the stored document carries, since the row's is the identity
    `forecasts.config_version` references) — that is the provenance every logged
    row is keyed by, and reading the config and knowing its version are never
    separate questions, so they are not separate calls.

    Raises when no configuration is active: a morning with nothing to run is a
    misconfiguration to fix, not an empty forecast to log."""
    row = conn.execute(
        "SELECT version, config FROM forecast_configs "
        "WHERE is_active ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "No active forecast configuration: forecast_configs has no row with "
            "is_active = true. Insert a configuration document and activate it "
            "(see docs/adr/0006-daily-forecasts-are-a-config-driven-write-once-"
            "log.md) before running the daily forecast."
        )
    version, config = row
    return {**config, "version": version}


# The log's columns, in the order `forecast_engine.LOG_COLUMNS` returns them and
# the insert spells them; the leading five are its primary key, which the
# write-once insert conflicts on. Spelled here rather than imported so db.py
# stays free of the engine (and of pandas-heavy model imports); a test pins the
# two lists together, so a rename on either side fails loudly.
FORECAST_KEY_COLUMNS = ("as_of", "config_version", "model", "target", "target_date")
FORECAST_COLUMNS = FORECAST_KEY_COLUMNS + ("forecast_quantity",)


def forecast_rows(frame: pd.DataFrame) -> List[tuple]:
    """The Demand Forecast log frame as `(as_of, config_version, model, target,
    target_date, forecast_quantity)` tuples ready for Postgres: dates as python
    `date`, the version as `int`, the quantity as `float`, so no numpy scalar
    reaches the driver. Dates go through `pd.Timestamp` as `sales_rows` does, so
    a datetime64 column narrows here too rather than at the call site. Pure, so
    the frame-to-rows mapping is testable without a database."""
    return [
        (
            pd.Timestamp(row.as_of).date(),
            int(row.config_version),
            str(row.model),
            str(row.target),
            pd.Timestamp(row.target_date).date(),
            float(row.forecast_quantity),
        )
        for row in frame.itertuples(index=False)
    ]


def insert_forecasts(conn: psycopg.Connection, frame: pd.DataFrame) -> int:
    """Append `run_forecasts`' rows to the write-once log, returning how many
    were actually written.

    `ON CONFLICT (as_of, config_version, model, target, target_date) DO NOTHING`
    is the write-once contract: a same-morning retry fills the gaps a failed run
    left and leaves every already-logged forecast exactly as it was recorded, so
    a logged row stays frozen at what was predicted that morning (ADR 0006). A
    re-run under a *new* config version conflicts with nothing and adds its rows
    alongside the old, which is what makes a configuration change additive
    rather than a rewrite of history. Conflict-skipped rows are not counted, so
    a fully redundant retry returns 0."""
    rows = forecast_rows(frame)
    if not rows:
        return 0
    columns = ", ".join(FORECAST_COLUMNS)
    placeholders = ", ".join(["%s"] * len(FORECAST_COLUMNS))
    key = ", ".join(FORECAST_KEY_COLUMNS)
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO forecasts ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT ({key}) DO NOTHING",
            rows,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


# --- Scheduled Reports as a subscription table over the log (ADR 0010) -----


def postgres_weekday(day: dt.date) -> int:
    """`day`'s weekday in Postgres' `EXTRACT(DOW)` convention, 0 = Sunday.

    `report_configs.days_of_week` is stored in that convention so a row reads
    the same from `psql` as from here, and python's `date.weekday()` is
    Monday-based — so the two are off by one *and* wrap at different ends of the
    week. Converting in one named function, rather than inline in the query,
    keeps the mapping testable without a database."""
    return (day.weekday() + 1) % 7


def read_due_reports(conn: psycopg.Connection, today: dt.date) -> List[Dict]:
    """The active Scheduled Reports that fire on `today`'s weekday, oldest
    first, each as one flat dict: its stored `config` document with the row's
    `id` and `forecast_config_version` merged in.

    `today` is an argument rather than a reading of the clock because the caller
    owns which day it is — the date in the restaurants' timezone, not the
    runner's UTC date, exactly as `daily_forecast.py` computes its `as_of`. From
    20:00 ET a runner has already rolled over, and asking the database for the
    weekday would quietly deliver the wrong day's reports.

    The row's columns win over anything the document happens to spell, the same
    rule `read_active_config` applies to `version`: `forecast_config_version` is
    the foreign key the report is *defined by* (ADR 0010), so a stale copy of it
    inside the jsonb must not shadow it. `days_of_week` is deliberately not
    returned — it is the question this reader has already answered, and a caller
    re-checking it would be a second place for the weekday convention to drift.

    No due reports is not an error: most days there are none."""
    rows = conn.execute(
        "SELECT id, forecast_config_version, config FROM report_configs "
        "WHERE is_active AND %s = ANY (days_of_week) ORDER BY id",
        (postgres_weekday(today),),
    ).fetchall()
    return [
        {**config, "id": report_id, "forecast_config_version": version}
        for report_id, version, config in rows
    ]


def read_forecast_config(conn: psycopg.Connection, version: int) -> Dict:
    """The forecast configuration a Scheduled Report references, as
    `report_payload.build_payload` takes it: the stored document with its own
    `version`, its `is_active` flag, and `active_version` — the version
    `forecast_configs` marks active *now* — stamped in.

    `is_active` and `active_version` are what let a refusal distinguish "this
    report points at a superseded configuration" from "today's run did not
    happen", and name the version the row should be repointed at (ADR 0010).
    They are read here rather than derived in the payload builder because which
    version is active is a database question, and the builder is pure.

    `active_version` is the newest active version, matching how
    `read_active_config` settles the same ambiguity, and is `None` when nothing
    is active at all. Raises when `version` names no row: `report_configs`'
    foreign key makes that impossible for a report read out of the table, so it
    means the configuration was deleted underneath one."""
    row = conn.execute(
        "SELECT is_active, config FROM forecast_configs WHERE version = %s",
        (version,),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"No forecast configuration with version {version}. A Scheduled "
            "Report references one by foreign key, so this means the "
            "configuration row was deleted — restore it, or repoint the "
            "report_configs row at a version that exists."
        )
    is_active, config = row
    active = conn.execute(
        "SELECT version FROM forecast_configs WHERE is_active "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return {
        **config,
        "version": version,
        "is_active": is_active,
        "active_version": active[0] if active else None,
    }


def read_latest_forecasts(conn: psycopg.Connection, config_version: int) -> pd.DataFrame:
    """The `forecasts` rows at the newest Forecast Origin logged under
    `config_version`, in the frame `report_payload.build_payload` reads.

    Only the newest origin, not the version's whole history: the payload builder
    needs the origin to *be* today and refuses otherwise, so every older origin
    would be read and discarded — and the log grows by one origin a day forever.
    An empty frame is not an error; it is how "nothing has ever been logged under
    this version" reaches the builder, which refuses on it with the same message
    as an origin that is merely late."""
    rows = conn.execute(
        "SELECT as_of, config_version, model, target, target_date, forecast_quantity "
        "FROM forecasts WHERE config_version = %s AND as_of = ("
        "  SELECT max(as_of) FROM forecasts WHERE config_version = %s"
        ") ORDER BY model, target, target_date",
        (config_version, config_version),
    ).fetchall()
    return pd.DataFrame(rows, columns=list(FORECAST_COLUMNS))


USAGE = """usage: python db.py [migrate | status | baseline NNNN]

  migrate         apply every pending migration, in order (the default)
  status          list what is applied and what is pending, and change nothing
  baseline NNNN   record NNNN as applied without running it, for a database
                  that already has that schema

There is no down command. Forward-only (ADR 0015): correct a mistake with the
next migration; recover a catastrophe with Supabase point-in-time recovery;
reset a local database by dropping it and migrating from 0001."""


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "migrate"

    if command == "status":
        with connect() as conn:
            applied = applied_versions(conn)
            pending = pending_migrations(conn)
        for version, path in migration_files():
            mark = "pending" if version in {v for v, _ in pending} else "applied"
            print(f"  {mark:>7}  {path.name}")
        print(f"{len(applied)} applied, {len(pending)} pending")
        return 0

    if command == "baseline":
        if len(args) != 2:
            print(USAGE)
            print("\nbaseline needs a version, e.g. python db.py baseline 0001")
            return 2
        with connect() as conn:
            baseline(conn, args[1])
        print(f"recorded {args[1]} as applied; no DDL was run")
        return 0

    if command != "migrate":
        print(USAGE)
        print(f"\nunknown command: {command!r}")
        return 2

    with connect() as conn:
        applied = apply_migrations(conn)
    for version in applied:
        print(f"applied {version}")
    print(f"{len(applied)} migration(s) applied" if applied else "nothing to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
