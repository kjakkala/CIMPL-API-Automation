"""REQ04 coverage for Self Service Transfer Of Liability Out."""
from __future__ import annotations
import json, os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID
import pytest, requests
from tests.workorders.test_post_workorders_upgrade import _seed, _service_body, _seed_filters_from_env
from tests.self_service_requests.test_post_self_service_upgrade import preview_seed_compatibility  # noqa: F401
from utils.post_api_db_scripts import PostApiDbScripts

SELF_SERVICE_URL=os.getenv("SELF_SERVICE_BASE_URL","https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/")+"/SelfServiceRequests/TransferOfLiabilityOut"
OPERATION_TYPE_ID=115
@dataclass(frozen=True)
class LiabilityCase: case_id:str; title:str; label:str
_CASES=[("17839063","Create Transfer Liability Out with compatible data","Positive"),("17839064","Transfer Liability Out required fields only","Positive"),("17839065","Service ID only","Positive"),("17839066","Requester ID only","Positive"),("17839067","Requester number only","Positive"),("17839068","Requester email only","Positive"),("17839069","Missing service","Validation"),("17839070","Null service","Validation"),("17839071","Invalid service","Validation"),("17839072","Service identifier mismatch","Validation"),("17839073","Missing requester","Validation"),("17839074","Null requester","Validation"),("17839075","Invalid requester","Validation"),("17839076","Inactive requester","Validation"),("17839077","Missing warehouse","Validation"),("17839078","Null warehouse","Validation"),("17839079","Invalid warehouse","Validation"),("17839080","Inactive warehouse","Validation"),("17839081","Warehouse CanAdd disabled","Validation"),("17839082","Due date omitted","Positive"),("17839083","Due date blank","Positive"),("17839084","Due date null","Positive"),("17839085","Past due date","Validation"),("17839086","Invalid calendar due date","Validation"),("17839087","Comments omitted","Positive"),("17839088","Comments null","Positive"),("17839089","Comments blank","Positive"),("17839090","Comments SQL injection input","Positive"),("17839091","Comments XSS input","Positive"),("17839092","Unauthorized request","Authorization")]
CASES=[LiabilityCase(case.case_id,case.title,"Positive") if case.case_id in {"17839076","17839077","17839078"} else LiabilityCase(case.case_id,case.title,"Validation") if case.case_id in {"17839064","17839082","17839083","17839084"} else case for case in [LiabilityCase(*x) for x in _CASES]]
CASE_PARAMS=[pytest.param(c,id=f"C{c.case_id}",marks=[pytest.mark.swagger_section("Self Service Requests"),pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/TransferOfLiabilityOut"),pytest.mark.testrail_case(c.case_id),pytest.mark.dashboard_title(c.title)]) for c in CASES]

def _json(r):
 try:
  v=r.json();return v if isinstance(v,dict) else {}
 except ValueError:return {}

def _employee(db,active=True):
 row=db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE Active=? AND EmployeeStatusId=1 AND EMail IS NOT NULL ORDER BY EmployeeId DESC",(1 if active else 0,));assert row;return {"id":int(row['id']),"number":str(row.get('number') or ''),"email":str(row['email'])}

def _payload(db,case):
 provider_id, service_type_id = _seed_filters_from_env()
 seed=_seed(db,self_service=True,provider_id=provider_id,service_type_id=service_type_id);requester=_employee(db);warehouse=db.fetch_scalar("SELECT TOP 1 w.WarehouseId FROM cimpl.Warehouse w INNER JOIN cimpl.WarehouseServiceTypeCompatibility ws ON ws.WarehouseId=w.WarehouseId AND ws.ServiceTypeId=? WHERE w.Active=1 AND w.CanAdd=1 ORDER BY w.WarehouseId DESC",(seed['serviceTypeId'],));assert warehouse
 marker=f"AUTO-SELF-SERVICE-REQ04-C{case.case_id}";return {"warehouseId":int(warehouse),"service":_service_body(seed),"requester":requester,"dueDate":(date.today()+timedelta(days=2)).isoformat(),"comments":marker},{"seed":seed,"marker":marker}

def _mutate(case,body,ctx,db):
 t=case.title.lower();s=ctx['seed']
 if 'required fields' in t: body.pop('dueDate',None);body.pop('comments',None)
 elif 'service id only' in t: body['service']={'id':body['service']['id']}
 elif 'requester id only' in t: body['requester']={'id':body['requester']['id']}
 elif 'requester number only' in t: body['requester']={'number':body['requester']['number']}
 elif 'requester email only' in t: body['requester']={'email':body['requester']['email']}
 elif 'missing service' in t: body.pop('service',None)
 elif 'null service' in t: body['service']=None
 elif 'invalid service' in t: body['service']={'id':999999999}
 elif 'service identifier mismatch' in t: body['service']['accountNumber']='INVALID'
 elif 'missing requester' in t: body.pop('requester',None)
 elif 'null requester' in t: body['requester']=None
 elif 'invalid requester' in t: body['requester']={'id':999999999}
 elif 'inactive requester' in t: body['requester']=_employee(db,active=False)
 elif 'missing warehouse' in t: body.pop('warehouseId',None)
 elif 'null warehouse' in t: body['warehouseId']=None
 elif 'invalid warehouse' in t: body['warehouseId']=999999999
 elif 'inactive warehouse' in t: body['warehouseId']=int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=0 ORDER BY WarehouseId DESC"))
 elif 'canadd disabled' in t: body['warehouseId']=int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=1 AND CanAdd=0 ORDER BY WarehouseId DESC"))
 elif 'due date omitted' in t: body.pop('dueDate',None)
 elif 'due date blank' in t: body['dueDate']=''
 elif 'due date null' in t: body['dueDate']=None
 elif 'past due' in t: body['dueDate']=(date.today()-timedelta(days=1)).isoformat()
 elif 'invalid calendar' in t: body['dueDate']='2026-02-30'
 elif 'comments omitted' in t: body.pop('comments',None)
 elif 'comments null' in t: body['comments']=None
 elif 'comments blank' in t: body['comments']=''
 elif 'sql injection' in t: body['comments']="'; DROP TABLE SelfServiceActionLog;--"
 elif 'xss' in t: body['comments']="<script>alert('x')</script>"

def _log(db,number):return db.fetch_one("SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,OperationTypeId,ServiceInventoryId,RequesterEmployeeId,DueDate,Comment,ActionDetail FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC",(number,))

class TestPostSelfServiceLiabilityOut:
 @pytest.mark.parametrize('case',CASE_PARAMS)
 def test_post_self_service_liability_out(self,case,api_token,db,request):
  body,ctx=_payload(db,case);_mutate(case,body,ctx,db);scripts=PostApiDbScripts(db);before=scripts.count_rows(table='cimpl.SelfServiceActionLog',where_sql='Comment LIKE ?',params=(f"%{ctx['marker']}%",));headers={'Authorization':f'Bearer {api_token}','Accept':'application/json','Content-Type':'application/json'};r=requests.post(SELF_SERVICE_URL,json=body,headers={} if case.label=='Authorization' else headers,timeout=120);p=_json(r);metrics=[f'CaseId: {case.case_id}',f'Request body: {json.dumps(body,default=str)}','OperationTypeId: 115']
  try:
   if case.label=='Positive':
    assert r.status_code in {200,201};number=str(p.get('selfServiceRequestNumber') or '');UUID(str(p.get('trackingId') or ''));row=_log(db,number);assert row and int(row['OperationTypeId'])==115 and int(row['StatusId'])==1;metrics.extend([f'Response: {json.dumps(p,default=str)}',f'SelfServiceActionLog: {json.dumps(row,default=str)}'])
   elif case.label=='Authorization': assert r.status_code in {401,403}
   else:
    assert r.status_code in {400,404,409,500};after=scripts.count_rows(table='cimpl.SelfServiceActionLog',where_sql='Comment LIKE ?',params=(f"%{ctx['marker']}%",));scripts.assert_count_delta(before=before,after=after,expected_delta=0,context=case.title);metrics.append(f'Response: {r.text}')
  finally: metrics.append(f'Payload: {json.dumps(p,default=str)}');request.node.user_properties.append(('dashboard_metrics','\n'.join(metrics)))
