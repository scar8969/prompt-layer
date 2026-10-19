"""Dialect handling: the small, honest differences between SQLite and Postgres.

The studio's SQL was written portably on purpose (see ``studio/db.py``), so moving
to Postgres is a change of connection layer, not a rewrite. What is left is a
known, finite list, and each item here exists because the two engines genuinely
disagree:

===========================  ==========================  ======================
Idiom                        SQLite                      Postgres
===========================  ==========================  ======================
Placeholder                  ``?``                       ``%s``
Literal percent in SQL       ``50%``                     ``50%%``
Conflict-tolerant insert     ``INSERT OR IGNORE``        ``ON CONFLICT DO NOTHING``
Integrity error class        ``sqlite3.IntegrityError``  ``psycopg.errors.*``
Schema introspection         ``PRAGMA table_info``       ``information_schema``
Table list                   ``sqlite_master``           ``information_schema``
===========================  ==========================  ======================

The percent rule deserves a note, because getting it wrong is silent. Psycopg
uses ``%`` for parameter interpolation, so a literal percent anywhere in the
statement must be doubled -- but doubling when *no* parameters are passed is
also wrong on psycopg 3 (it leaves the doubled form alone only when it
interpolates). :func:`to_postgres_sql` therefore doubles percents only for
statements that actually carry parameters, which is exactly the case where
psycopg will interpolate.
"""

from __future__ import annotations

import re
from typing import Any, Tuple

#: ``INSERT OR IGNORE INTO x ...`` -> ``INSERT INTO x ... ON CONFLICT DO NOTHING``
#: The studio uses this only for join tables whose primary key is the pair, where
#: "already linked" and "linked it" are the same end state.
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)

#: ``INSERT OR REPLACE`` has no single Postgres equivalent, so it is refused
#: loudly rather than silently rewritten into something semantically different.
_INSERT_OR_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\b", re.IGNORECASE)


def is_insert_or_ignore(sql: str) -> bool:
    return bool(_INSERT_OR_IGNORE.match(sql))


def to_postgres_sql(sql: str, *, has_params: bool) -> str:
    """Translate one SQLite-flavoured statement into its Postgres spelling.

    Only the constructs listed in the module docstring are touched. Anything
    else is passed through unchanged, so an unfamiliar statement fails at the
    server with a real error rather than being mangled here.
    """
    if _INSERT_OR_REPLACE.match(sql):
        raise NotImplementedError(
            "INSERT OR REPLACE has no portable equivalent; rewrite the caller to "
            "UPDATE-then-INSERT inside one transaction"
        )
    text = sql
    if _INSERT_OR_IGNORE.match(text):
        text = _INSERT_OR_IGNORE.sub("INSERT INTO", text)
        if re.search(r"\bON\s+CONFLICT\b", text, re.IGNORECASE) is None:
            text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if has_params:
        # Only when psycopg is going to interpolate: then '%' is its escape char.
        text = text.replace("%", "%%")
        # ...and the placeholders we just doubled must come back as %s.
        text = re.sub(r"\?(?=(?:[^']*'[^']*')*[^']*$)", "%s", text)
    return text


def split_statements(sql: str) -> Tuple[str, ...]:
    """Split a migration file into statements for Postgres.

    A semicolon only ends a statement when it is *outside* a string literal and
    outside a line comment. The naive "line ends with ;" rule is wrong here: the
    migration files contain prose comments that end in a semicolon, and cutting
    there produced ``syntax error at end of input`` on the first Postgres run.
    Dollar-quoted strings (``$$ ... $$``) are handled too, because a future
    migration may well want a function body.
    """
    statements: List[str] = []
    buffer: List[str] = []
    index = 0
    length = len(sql)
    in_line_comment = False
    quote: Optional[str] = None
    dollar_tag: Optional[str] = None

    while index < length:
        char = sql[index]
        pair = sql[index : index + 2]

        if in_line_comment:
            buffer.append(char)
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                buffer.append(char)
                index += 1
            continue

        if quote is not None:
            buffer.append(char)
            if char == quote:
                # A doubled quote is an escaped quote, not the end of the literal.
                if sql[index + 1 : index + 2] == quote:
                    buffer.append(quote)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if pair == "--":
            in_line_comment = True
            buffer.append(pair)
            index += 2
            continue

        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            continue

        if char == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                candidate = sql[index : end + 1]
                # $$ or $tag$ -- only a valid tag character set counts.
                if candidate[1:-1].replace("_", "").isalnum() or candidate == "$$":
                    dollar_tag = candidate
                    buffer.append(candidate)
                    index = end + 1
                    continue

        if char == ";":
            buffer.append(char)
            statement = "".join(buffer).strip()
            if statement.strip(";").strip():
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    tail = "".join(buffer).strip()
    if tail.strip(";").strip():
        statements.append(tail)
    return tuple(statements)


def row_to_dict(columns: Tuple[str, ...], row: Any) -> dict:
    """Build a dict from a DB-API row.

    ``dict(row)`` is the natural idiom and it is wrong on psycopg: a plain tuple
    raises ``ValueError: dictionary update sequence element #0 has length N;
    2 is required``. Building from ``cursor.description`` is correct on both
    drivers, so every row goes through here.
    """
    return {name: value for name, value in zip(columns, row)}
