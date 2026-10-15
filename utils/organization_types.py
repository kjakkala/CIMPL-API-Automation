"""Organization type bit-flag helpers used by database-backed API tests."""

from __future__ import annotations

COST_CENTER_TYPE_FLAG = 4


def has_cost_center_type(organization_type_id: int | None) -> bool:
    """Return whether an OrganizationTypeId includes the CostCenter flag."""
    return organization_type_id is not None and (int(organization_type_id) & COST_CENTER_TYPE_FLAG) == COST_CENTER_TYPE_FLAG


def cost_center_type_sql(column: str, *, required: bool = True) -> str:
    """Return a SQL predicate for the CostCenter flag on an organization type column."""
    operator = "=" if required else "<>"
    return f"({column} & {COST_CENTER_TYPE_FLAG}) {operator} {COST_CENTER_TYPE_FLAG}"
