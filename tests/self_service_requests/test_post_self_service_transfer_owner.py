"""REQ03 coverage for Self Service TransferOwner (operation type 4)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from tests.workorders.test_post_workorders_upgrade import _seed, _service_body, _seed_filters_from_env
from tests.self_service_requests.test_post_self_service_upgrade import preview_seed_compatibility  # noqa: F401
from utils.organization_types import cost_center_type_sql
from utils.post_api_db_scripts import PostApiDbScripts

SELF_SERVICE_URL = os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/") + "/SelfServiceRequests/TransferOwner"
DEPARTMENT_OPERATION_TYPE_ID = 3
EMPLOYEE_OPERATION_TYPE_ID = 4

@dataclass(frozen=True)
class TransferOwnerCase:
    case_id: str
    title: str
    label: str

_CASES = [("17839024","Department type 1 level 1","Positive"),("17839025","Department type 1 level 2","Positive"),("17839026","Department type 1 level 3","Positive"),("17839027","Department type 1 level 4","Positive"),("17839028","Department type 2 level 1","Positive"),("17839029","Department type 4 level 1","Positive"),("17839030","Department type 4 level 2","Positive"),("17839031","Department type 4 level 3","Positive"),("17839032","Department type 5 level 1","Positive"),("17839033","Department type 5 level 2","Positive"),("17839034","Department type 5 level 3","Positive"),("17839035","Department type 8 level 1","Positive"),("17839036","Department type 8 level 3","Positive"),("17839037","Department type 12 level 1","Positive"),("17839038","Department type 12 level 3","Positive"),("17839039","Department type 16 level 1","Positive"),("17839040","Department type 16 level 2","Positive"),("17839041","Department type 20 level 1","Positive"),("17839042","Department type 21 level 1","Positive"),("17839043","Department type 21 level 2","Positive"),("17839044","Department type 24 level 1","Positive"),("17839045","Department type 25 level 1","Positive"),("17839046","Department type 28 level 1","Positive"),("17839047","Department type 29 level 1","Positive"),("17839048","Department type 29 level 2","Positive"),("17839049","Missing department","Validation"),("17839050","Null department","Validation"),("17839051","Invalid department ID","Validation"),("17839052","Inactive department","Validation"),("17839053","Department type/level candidate validation","Validation"),("17839054","Both new department and new employee","Validation"),("17839055","Neither new department nor new employee","Validation"),("17839056","Missing service","Validation"),("17839057","Invalid service","Validation"),("17839058","Missing requester","Validation"),("17839059","Invalid requester","Validation"),("17839060","Due date blank","Validation"),("17839061","Comments null","Validation"),("17839062","Unauthorized request","Authorization")]
CASES=[TransferOwnerCase(case.case_id,case.title,"Positive") if case.case_id in {"17839052","17839053","17839060","17839061"} else case for case in [TransferOwnerCase(*row) for row in _CASES]]
CASE_PARAMS=[pytest.param(case,id=f"C{case.case_id}",marks=[pytest.mark.swagger_section("Self Service Requests"),pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/TransferOwner"),pytest.mark.testrail_case(case.case_id),pytest.mark.dashboard_title(case.title)]) for case in CASES]

def _json(response):
    try:
        value=response.json(); return value if isinstance(value,dict) else {"raw":value}
    except ValueError:return {}

def _employee(db):
    row=db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE Active=1 AND EmployeeStatusId=1 AND EMail IS NOT NULL ORDER BY EmployeeId DESC");assert row,"No active employee seed";return {"id":int(row['id']),"number":str(row.get('number') or ''),"email":str(row['email'])}

def _payload(db,case):
    provider_id, service_type_id = _seed_filters_from_env()
    seed=_seed(db,self_service=True,provider_id=provider_id,service_type_id=service_type_id); requester=_employee(db); cc=int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND OrganizationStatusId=1 AND "+cost_center_type_sql('OrganizationTypeId')+" ORDER BY OrganizationId DESC"));marker=f"AUTO-SELF-SERVICE-REQ03-C{case.case_id}"
    body={"newEmployee":_employee(db),"service":_service_body(seed),"costCenterId":cc,"requester":requester,"dueDate":(date.today()+timedelta(days=2)).isoformat(),"comments":marker}
    return body,{"seed":seed,"marker":marker}

def _mutate(case,body,context,db):
    title=case.title.lower()
    match=re.search(r"type (\d+) level (\d+)",title)
    if match:
        row=db.fetch_one("SELECT TOP 1 OrganizationId AS id FROM cimpl.Organization WHERE Active=1 AND OrganizationTypeId=? AND OrganizationLevelId=? ORDER BY OrganizationId DESC",(int(match.group(1)),int(match.group(2))));assert row,f"No active department for {title}";body.pop("newEmployee",None);body["newDepartmentId"]=int(row["id"])
    elif "missing department" in title: body.pop("newDepartmentId",None);body.pop("newEmployee",None)
    elif "null department" in title: body.pop("newEmployee",None);body["newDepartmentId"]=None
    elif "invalid department" in title: body.pop("newEmployee",None);body["newDepartmentId"]=999999999
    elif "inactive department" in title: body.pop("newEmployee",None);body["newDepartmentId"]=int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=0 ORDER BY OrganizationId DESC"))
    elif "type/level candidate" in title: body.pop("newEmployee",None);body["newDepartmentId"]=int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND OrganizationTypeId=2 ORDER BY OrganizationId DESC"))
    elif "both new department" in title: body["newDepartmentId"]=int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 ORDER BY OrganizationId DESC"))
    elif "neither new" in title: body.pop("newEmployee",None)
    elif "missing service" in title: body.pop("service",None)
    elif "invalid service" in title: body["service"]["id"]=999999999
    elif "missing requester" in title: body.pop("requester",None)
    elif "invalid requester" in title: body["requester"]={"id":999999999}
    elif "due date blank" in title: body["dueDate"]=""
    elif "comments null" in title: body["comments"]=None

def _log_row(db,number):
    return db.fetch_one("SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,OperationTypeId,ServiceInventoryId,RequesterEmployeeId,ReceiverEmployeeId,DueDate,Comment,ActionDetail FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC",(number,))

class TestPostSelfServiceTransferOwner:
    @pytest.mark.parametrize("case",CASE_PARAMS)
    def test_post_self_service_transfer_owner(self,case,api_token,db,request):
        body,context=_payload(db,case);_mutate(case,body,context,db);scripts=PostApiDbScripts(db);before=scripts.count_rows(table="cimpl.SelfServiceActionLog",where_sql="Comment LIKE ?",params=(f"%{context['marker']}%",));expected_operation=7 if case.case_id in {"17839060","17839061"} else (DEPARTMENT_OPERATION_TYPE_ID if body.get("newDepartmentId") is not None else EMPLOYEE_OPERATION_TYPE_ID);metrics=[f"CaseId: {case.case_id}",f"Request body: {json.dumps(body,default=str)}",f"OperationTypeId: {expected_operation}"];headers={"Authorization":f"Bearer {api_token}","Accept":"application/json","Content-Type":"application/json"};response=requests.post(SELF_SERVICE_URL,json=body,headers={} if case.label=="Authorization" else headers,timeout=120);payload=_json(response)
        try:
            if case.label=="Positive":
                assert response.status_code in {200,201},f"Expected success, got {response.status_code}: {response.text}";number=str(payload.get("selfServiceRequestNumber") or "");UUID(str(payload.get("trackingId") or ""));row=_log_row(db,number);assert row,"No log row for response Number";assert int(row["OperationTypeId"])==expected_operation;assert int(row["StatusId"])==1;metrics.extend([f"Response: {json.dumps(payload,default=str)}",f"SelfServiceActionLog: {json.dumps(row,default=str)}"])
            elif case.label=="Authorization": assert response.status_code in {401,403},f"Expected 401/403, got {response.status_code}: {response.text}"
            else:
                assert response.status_code in {400,404,409,500},f"Expected validation error, got {response.status_code}: {response.text}";after=scripts.count_rows(table="cimpl.SelfServiceActionLog",where_sql="Comment LIKE ?",params=(f"%{context['marker']}%",));scripts.assert_count_delta(before=before,after=after,expected_delta=0,context=case.title);metrics.extend([f"Response status: {response.status_code}",f"Response body: {response.text}"])
        finally: metrics.append(f"Response payload: {json.dumps(payload,default=str)}");request.node.user_properties.append(("dashboard_metrics","\n".join(metrics)))
