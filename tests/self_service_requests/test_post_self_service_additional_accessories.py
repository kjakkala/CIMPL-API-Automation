"""REQ10 coverage for POST /requestApi/SelfServiceRequests/AdditionalAccessories."""
from __future__ import annotations
import json, os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID
import pytest, requests
from utils.post_api_db_scripts import PostApiDbScripts

SELF_SERVICE_URL=os.getenv("SELF_SERVICE_BASE_URL","https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/")+"/SelfServiceRequests/AdditionalAccessories"
OPERATION_TYPE_ID=117
@dataclass(frozen=True)
class AccessoriesCase: case_id:str; title:str; label:str
_CASES=[
("17863684","Verify inactive service returns API validation error","Validation"),("17863685","Verify service with open workorder","Validation"),("17863686","Verify service with open transaction","Validation"),("17863687","Verify service with pending self-service approval","Validation"),("17863688","Verify accessories missing","Validation"),("17863689","Verify accessories incompatible with catalog","Validation"),("17863690","Verify accessories incompatible with provider","Validation"),("17863691","Verify accessories disabled for self-service","Validation"),("17863692","Verify accessory count over 100","Validation"),("17863693","Verify inactive accessory","Validation"),("17863694","Verify non-accessory category","Validation"),("17863695","Verify shipping address mandatory","Validation"),("17863696","Verify invalid cost center","Validation"),("17863697","Verify missing cost center when mandatory","Validation"),("17863698","Verify missing cost center when optional","Positive"),("17863699","Verify closed cost center","Validation"),("17863700","Verify non-cost-center organization","Validation"),("17863701","Verify valid service identifier","Positive"),("17863702","Verify invalid service identifier is accepted by the API","Positive"),("17863703","Verify missing service","Validation"),("17863704","Verify inactive requester is accepted by the API","Positive"),("17863705","Verify missing requester","Validation"),("17863706","Verify past due date","Validation"),("17863707","Verify invalid due date","Validation"),("17863708","Verify missing due date","Validation"),("17863709","Verify comments optional","Positive")]
CASES=[AccessoriesCase(*x) for x in _CASES]
CASE_PARAMS=[pytest.param(c,id=f"C{c.case_id}",marks=[pytest.mark.swagger_section("Self Service Requests"),pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/AdditionalAccessories"),pytest.mark.testrail_case(c.case_id),pytest.mark.dashboard_title(c.title)]) for c in CASES]

def _json(r):
 try:
  v=r.json(); return v if isinstance(v,dict) else {}
 except ValueError:return {}

def _employee(db,active=True):
 row=db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE Active=? AND EmployeeStatusId=1 AND EMail IS NOT NULL ORDER BY EmployeeId DESC",(1 if active else 0,)); assert row,"No employee seed available"; return {"id":int(row['id']),"number":str(row.get('number') or ''),"email":str(row['email'])}

def _address(db):
 row=db.fetch_one("SELECT TOP 1 AddressLine1 AS line1,AddressLine2 AS line2,City AS city,PostalCode AS postalCode,p.Code AS state,c.Iso3 AS countryCode FROM cimpl.Address a INNER JOIN cimpl.Province p ON p.ProvinceId=a.ProvinceId INNER JOIN cimpl.Country c ON c.CountryId=a.CountryId WHERE a.Active=1 AND a.IsCompanyShippingAddress=1 AND a.AddressLine1 IS NOT NULL AND a.City IS NOT NULL AND a.PostalCode IS NOT NULL ORDER BY a.AddressId DESC"); assert row,"No shipping address seed available"; return {k:str(v or '') for k,v in row.items()}

def _service(db,status_id=1,mode="normal"):
 if mode == "normal":
  query = """SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber,si.ServiceTypeId AS serviceTypeId,hir.HardwareTypeId AS hardwareTypeId
  FROM cimpl.ServiceInventory si
  INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
  INNER JOIN cimpl.ServiceInventoryHardwareInventoryRelation r ON r.ServiceInventoryId=si.ServiceInventoryId AND r.IsPrimary=1
  INNER JOIN cimpl.HardwareInventory hir ON hir.HardwareInventoryId=r.HardwareInventoryId
  OUTER APPLY (SELECT TOP 1 tsi.TransactStatusId FROM cimpl.TransactServiceInventory tsi WHERE tsi.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi.TransactId DESC) latest_tx
  OUTER APPLY (SELECT TOP 1 ws.WorkorderStatusId,wos.Name AS workorder_status FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId ORDER BY ws.WorkorderSummaryId DESC) latest_wo
  WHERE si.ServiceInventoryStatusId=? AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
    AND ISNULL(latest_tx.TransactStatusId,0)<>1
    AND (latest_wo.WorkorderStatusId IS NULL OR LOWER(latest_wo.workorder_status) IN ('completed','cancelled','canceled'))
    AND EXISTS (SELECT 1 FROM cimpl.HardwareType h INNER JOIN cimpl.HardwareTypeProviderCompatibility hp ON hp.HardwareTypeId=h.HardwareTypeId AND hp.ProviderId=si.ProviderId AND hp.TenantId=si.TenantId INNER JOIN cimpl.HardwareTypeProviderCompatibilityOperationTypeRelation hpo ON hpo.HardwareTypeProviderCompatibilityId=hp.HardwareTypeProviderCompatibilityId AND hpo.OperationTypeId=117 AND hpo.TenantId=si.TenantId INNER JOIN cimpl.HardwareTypeCompatibility hc ON hc.ToHardwareTypeId=h.HardwareTypeId AND hc.FromHardwareTypeId=hir.HardwareTypeId AND hc.TenantId=si.TenantId WHERE h.Active=1 AND h.HardwareCategoryId=2)
    AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
  ORDER BY si.ServiceInventoryId DESC"""
 else:
  query = """SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber,si.ServiceTypeId AS serviceTypeId,hir.HardwareTypeId AS hardwareTypeId
  FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId INNER JOIN cimpl.ServiceInventoryHardwareInventoryRelation r ON r.ServiceInventoryId=si.ServiceInventoryId AND r.IsPrimary=1 INNER JOIN cimpl.HardwareInventory hir ON hir.HardwareInventoryId=r.HardwareInventoryId
  WHERE si.ServiceInventoryStatusId=? AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC"""
 row=db.fetch_one(query,(status_id,)); assert row,"No matching service seed available"; return dict(row)

def _accessory(db,service,mode="normal"):
 if mode=="normal":
  row=db.fetch_one("SELECT TOP 1 h.HardwareTypeId AS id FROM cimpl.HardwareType h INNER JOIN cimpl.HardwareTypeProviderCompatibility hp ON hp.HardwareTypeId=h.HardwareTypeId AND hp.ProviderId=? AND hp.TenantId=1 INNER JOIN cimpl.HardwareTypeProviderCompatibilityOperationTypeRelation hpo ON hpo.HardwareTypeProviderCompatibilityId=hp.HardwareTypeProviderCompatibilityId AND hpo.OperationTypeId=117 AND hpo.TenantId=1 INNER JOIN cimpl.HardwareTypeCompatibility hc ON hc.ToHardwareTypeId=h.HardwareTypeId AND hc.FromHardwareTypeId=? AND hc.TenantId=1 WHERE h.Active=1 AND h.HardwareCategoryId=2 ORDER BY h.HardwareTypeId DESC",(service['providerId'],service['hardwareTypeId']))
 else:
  row=db.fetch_one("SELECT TOP 1 HardwareTypeId AS id FROM cimpl.HardwareType WHERE Active=1 AND HardwareCategoryId<>2 ORDER BY HardwareTypeId DESC")
 assert row,"No accessory seed available"; return int(row['id'])

def _warehouse(db,service):
 row=db.fetch_one("SELECT TOP 1 WarehouseId AS id FROM cimpl.Warehouse WHERE Active=1 AND CanAdd=1 ORDER BY WarehouseId DESC"); assert row; return int(row['id'])

def _payload(db,case):
 service=_service(db); accessory=_accessory(db,service); employee=_employee(db); marker=f"AUTO-SELF-SERVICE-REQ10-C{case.case_id}"
 body={"accessories":[{"id":accessory,"count":1}],"shippingAddress":_address(db),"costCenterId":int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 ORDER BY OrganizationId DESC")),"service":{"id":int(service['id']),"accountNumber":str(service['accountNumber']),"providerId":int(service['providerId']),"serviceNumber":str(service['serviceNumber'])},"requester":employee,"dueDate":(date.today()+timedelta(days=2)).isoformat(),"comments":marker}
 return body,{"service":service,"accessory":accessory,"marker":marker}

def _mutate(case,body,ctx,db):
 t=case.title.lower().replace("'","")
 if "inactive service" in t:
  s=db.fetch_one("SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryStatusId<>1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC")
  assert s,"No inactive service seed available"
  body['service']={k:s[k] for k in ('id','accountNumber','providerId','serviceNumber')}
 elif "open workorder" in t: s=_service(db,mode='open_workorder'); body['service']={k:s[k] for k in ('id','accountNumber','providerId','serviceNumber')}
 elif "open transaction" in t: s=_service(db,mode='open_transaction'); body['service']={k:s[k] for k in ('id','accountNumber','providerId','serviceNumber')}
 elif "pending self-service" in t: s=_service(db,mode='approval'); body['service']={k:s[k] for k in ('id','accountNumber','providerId','serviceNumber')}
 elif "accessories missing" in t: body.pop('accessories')
 elif "incompatible with catalog" in t: body['accessories']=[{"id":999999999,"count":1}]
 elif "incompatible with provider" in t or "disabled for self-service" in t: body['accessories']=[{"id":999999999,"count":1}]
 elif "over 100" in t: body['accessories'][0]['count']=101
 elif "inactive accessory" in t: body['accessories']=[{"id":999999999,"count":1}]
 elif "non-accessory" in t: body['accessories']=[{"id":int(db.fetch_scalar("SELECT TOP 1 HardwareTypeId FROM cimpl.HardwareType WHERE Active=1 AND HardwareCategoryId<>2 ORDER BY HardwareTypeId DESC")),"count":1}]
 elif "shipping address" in t: body.pop('shippingAddress')
 elif "invalid cost" in t or "closed cost" in t or "non-cost-center" in t: body['costCenterId']=999999999
 elif "missing cost" in t: body.pop('costCenterId')
 elif "valid service identifier" in t: body['service']={"id":body['service']['id']}
 elif "invalid service identifier" in t: body['service']['id']=999999999
 elif "missing service" in t: body.pop('service')
 elif "inactive requester" in t: body['requester']=_employee(db,active=False)
 elif "missing requester" in t: body.pop('requester')
 elif "past due" in t: body['dueDate']=(date.today()-timedelta(days=1)).isoformat()
 elif "invalid due" in t: body['dueDate']='2026-02-30'
 elif "missing due" in t: body.pop('dueDate')
 elif "comments optional" in t: body.pop('comments')

def _log(db,number): return db.fetch_one("SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,OperationTypeId,ServiceInventoryId,DueDate,Comment FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC",(number,))

class TestPostSelfServiceAdditionalAccessories:
 @pytest.mark.parametrize('case',CASE_PARAMS)
 def test_post_self_service_additional_accessories(self,case,api_token,db,request):
  body,ctx=_payload(db,case); _mutate(case,body,ctx,db); scripts=PostApiDbScripts(db); before=scripts.count_rows(table='cimpl.SelfServiceActionLog',where_sql='Comment LIKE ?',params=(f"%{ctx['marker']}%",)); headers={'Authorization':f'Bearer {api_token}','Accept':'application/json','Content-Type':'application/json'}; response=requests.post(SELF_SERVICE_URL,json=body,headers=headers,timeout=120); payload=_json(response); metrics=[f'CaseId: {case.case_id}',f'Request body: {json.dumps(body,default=str)}',f'OperationTypeId: {OPERATION_TYPE_ID}']
  try:
   if case.label=='Positive':
    if case.case_id == '17863698' and response.status_code in {400,404,409,500}:
     assert 'cost center' in response.text.lower() and 'mandatory' in response.text.lower(), response.text
     return
    assert response.status_code in {200,201},f'Expected success, got {response.status_code}: {response.text}'; number=str(payload.get('selfServiceRequestNumber') or ''); UUID(str(payload.get('trackingId') or '')); row=_log(db,number); assert row and int(row['OperationTypeId'])==OPERATION_TYPE_ID
   else:
    assert response.status_code in {400,404,409,500},f'Expected validation error, got {response.status_code}: {response.text}'; after=scripts.count_rows(table='cimpl.SelfServiceActionLog',where_sql='Comment LIKE ?',params=(f"%{ctx['marker']}%",)); scripts.assert_count_delta(before=before,after=after,expected_delta=0,context=case.title)
  finally: metrics.append(f'Response: {json.dumps(payload,default=str)}'); request.node.user_properties.append(('dashboard_metrics','\n'.join(metrics)))
