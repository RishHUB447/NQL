"""Reads a database's schema and renders it as compact text for the LLM."""

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.engine import Engine

SAMPLE_SCAN_ROWS = 1000  # rows scanned per column when looking for categorical values
MAX_CATEGORICAL_VALUES = 12
MAX_SAMPLED_COLUMNS = 400  # keeps connecting fast on very wide schemas


@dataclass
class Column:
    name: str
    type: str
    nullable: bool
    primary_key: bool = False
    comment: str | None = None
    # Distinct values, filled only for low-cardinality text columns (e.g. status).
    values: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str  # qualified name of the referenced table
    ref_columns: list[str]


@dataclass
class Table:
    schema: str | None
    name: str
    columns: list[Column]
    foreign_keys: list[ForeignKey]
    comment: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def _qualify(schema: str | None, name: str) -> str:
    return f"{schema}.{name}" if schema else name


def introspect(engine: Engine, sample_values: bool = True) -> list[Table]:
    """Return every table and view in the connection's default schema."""
    insp = sa.inspect(engine)
    # Postgres: qualify names ("public.orders") since users often have several
    # schemas. MySQL: the schema is the database, so leave names bare.
    schema = insp.default_schema_name if engine.dialect.name == "postgresql" else None

    tables: list[Table] = []
    names = insp.get_table_names(schema=schema) + insp.get_view_names(schema=schema)
    for name in sorted(names):
        pk = set(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
        columns = [
            Column(
                name=c["name"],
                type=str(c["type"]).lower(),
                nullable=bool(c.get("nullable", True)),
                primary_key=c["name"] in pk,
                comment=c.get("comment"),
            )
            for c in insp.get_columns(name, schema=schema)
        ]
        fks = [
            ForeignKey(
                columns=fk["constrained_columns"],
                ref_table=_qualify(fk.get("referred_schema") or schema, fk["referred_table"]),
                ref_columns=fk["referred_columns"],
            )
            for fk in insp.get_foreign_keys(name, schema=schema)
        ]
        try:
            comment = insp.get_table_comment(name, schema=schema).get("text")
        except NotImplementedError:
            comment = None
        tables.append(Table(schema, name, columns, fks, comment))

    if sample_values:
        _fill_categorical_values(engine, tables)
    return tables


def _is_text(type_name: str) -> bool:
    return any(t in type_name for t in ("char", "text", "enum", "string"))


def _fill_categorical_values(engine: Engine, tables: list[Table]) -> None:
    """Record distinct values of text columns that look categorical.

    Knowing that orders.status is one of 'paid'/'refunded' stops the model from
    guessing 'PAID' or 'complete'. Only a bounded number of rows is scanned per column.
    """
    sampled = 0
    with engine.connect() as conn:
        for table in tables:
            for col in table.columns:
                if sampled >= MAX_SAMPLED_COLUMNS:
                    return
                if not _is_text(col.type) or col.primary_key:
                    continue
                sampled += 1
                t = sa.table(table.name, sa.column(col.name), schema=table.schema)
                scan = sa.select(t.c[col.name]).limit(SAMPLE_SCAN_ROWS).subquery()
                query = sa.select(scan.c[col.name]).distinct().limit(MAX_CATEGORICAL_VALUES + 1)
                try:
                    values = [r[0] for r in conn.execute(query) if r[0] is not None]
                except sa.exc.SQLAlchemyError:
                    conn.rollback()
                    continue
                if 0 < len(values) <= MAX_CATEGORICAL_VALUES and all(
                    isinstance(v, str) and len(v) <= 40 for v in values
                ):
                    col.values = sorted(values)


def render(tables: list[Table]) -> str:
    """Compact, LLM-friendly schema text. One line per column."""
    lines: list[str] = []
    for t in tables:
        header = f"TABLE {t.qualified_name}"
        if t.comment:
            header += f"  -- {t.comment}"
        lines.append(header)
        fk_by_col = {
            fk.columns[0]: f"{fk.ref_table}.{fk.ref_columns[0]}"
            for fk in t.foreign_keys
            if len(fk.columns) == 1
        }
        for c in t.columns:
            parts = [f"  {c.name} {c.type}"]
            if c.primary_key:
                parts.append("PK")
            elif not c.nullable:
                parts.append("NOT NULL")
            if c.name in fk_by_col:
                parts.append(f"-> {fk_by_col[c.name]}")
            notes = []
            if c.values:
                notes.append("values: " + ", ".join(repr(v) for v in c.values))
            if c.comment:
                notes.append(c.comment)
            line = " ".join(parts)
            if notes:
                line += "  -- " + "; ".join(notes)
            lines.append(line)
        for fk in t.foreign_keys:
            if len(fk.columns) > 1:
                lines.append(
                    f"  FK ({', '.join(fk.columns)}) -> {fk.ref_table} ({', '.join(fk.ref_columns)})"
                )
        lines.append("")
    return "\n".join(lines).strip()


def render_index(tables: list[Table]) -> str:
    """One line per table (name + column names), used for table selection."""
    return "\n".join(
        f"{t.qualified_name}({', '.join(c.name for c in t.columns)})" for t in tables
    )


def with_neighbours(tables: list[Table], chosen: set[str]) -> list[Table]:
    """Chosen tables plus every table they reference or are referenced by."""
    expanded = set(chosen)
    for t in tables:
        for fk in t.foreign_keys:
            if t.qualified_name in chosen:
                expanded.add(fk.ref_table)
            if fk.ref_table in chosen:
                expanded.add(t.qualified_name)
    return [t for t in tables if t.qualified_name in expanded]
