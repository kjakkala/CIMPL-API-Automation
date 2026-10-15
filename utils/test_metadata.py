import pytest


def api_case(*, section: str, operation: str, testrail_id: str, title: str):
    """Attach Swagger and TestRail metadata to a pytest test case."""

    def decorator(func):
        marks = [
            pytest.mark.swagger_section(section),
            pytest.mark.swagger_operation(operation),
            pytest.mark.testrail_case(testrail_id),
            pytest.mark.dashboard_title(title),
        ]
        for mark in reversed(marks):
            func = mark(func)
        return func

    return decorator