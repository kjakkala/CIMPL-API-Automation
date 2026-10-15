import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    output_path = os.environ.get("DASHBOARD_CASE_MAP_OUTPUT")
    if not output_path:
        return

    cases = []
    for item in session.items:
        marker = item.get_closest_marker("testrail_case")
        if not marker:
            continue
        case_id = marker.args[0] if marker.args else marker.kwargs.get("value", "")
        if not str(case_id).strip().isdigit():
            continue
        title_marker = item.get_closest_marker("dashboard_title")
        section_marker = item.get_closest_marker("swagger_section")
        cases.append({
            "case_id": str(case_id).strip(),
            "nodeid": item.nodeid,
            "title": str(title_marker.args[0]) if title_marker and title_marker.args else item.name,
            "test_section": str(section_marker.args[0]) if section_marker and section_marker.args else item.path.parent.name,
        })

    Path(output_path).write_text(json.dumps(cases), encoding="utf-8")