from __future__ import annotations

from typing import Any


class PostApiDbScripts:
    """Reusable DB validation helpers for POST API tests."""

    def __init__(self, db):
        self.db = db

    def count_rows(self, *, table: str, where_sql: str = "", params: tuple = ()) -> int:
        query = f"SELECT COUNT(*) FROM {table}"
        if where_sql.strip():
            query += f" WHERE {where_sql}"
        return int(self.db.fetch_scalar(query, params) or 0)

    def max_id(self, *, table: str, id_column: str) -> int:
        query = f"SELECT ISNULL(MAX({id_column}), 0) FROM {table}"
        return int(self.db.fetch_scalar(query) or 0)

    def assert_count_delta(self, *, before: int, after: int, expected_delta: int, context: str):
        actual_delta = int(after) - int(before)
        assert actual_delta == expected_delta, (
            f"{context}: expected row-count delta {expected_delta}, got {actual_delta}. "
            f"Before={before}, After={after}"
        )

    def assert_single_row(self, *, query: str, params: tuple, context: str) -> dict[str, Any]:
        rows = self.db.fetch_all(query, params)
        assert rows, f"{context}: expected at least one DB row"
        assert len(rows) == 1, f"{context}: expected exactly one DB row, got {len(rows)}"
        return rows[0]

    def assert_field_values(self, *, row: dict[str, Any], expected: dict[str, Any], context: str):
        for key, expected_value in expected.items():
            assert key in row, f"{context}: DB row missing field '{key}'"
            actual_value = row.get(key)
            assert actual_value == expected_value, (
                f"{context}: DB value mismatch for '{key}'. "
                f"Expected={expected_value!r}, Actual={actual_value!r}"
            )

    def format_snapshot(self, row: dict[str, Any], *, keys: list[str]) -> str:
        parts: list[str] = []
        for key in keys:
            parts.append(f"{key}={row.get(key)!r}")
        return ", ".join(parts)