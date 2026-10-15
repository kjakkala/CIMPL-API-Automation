"""REQ02 coverage for POST /requestApi/SelfServiceRequests/Upgrade."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from tests.workorders.test_post_workorders_upgrade import _seed, _service_body, _seed_filters_from_env
from utils.post_api_db_scripts import PostApiDbScripts

SELF_SERVICE_URL = os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/") + "/SelfServiceRequests/Upgrade"
OPERATION_TYPE_ID = 20

@dataclass(frozen=True)
class SelfServiceUpgradeCase:
    case_id: str
    title: str
    label: str

_CASES = [
("17838910","Create Self Service Upgrade with compatible data","Positive"),("17838911","Upgrade with required fields only","Positive"),("17838912","Employee ID only","Positive"),("17838913","Employee number only","Positive"),("17838914","Employee email only","Positive"),("17838915","Requester ID only","Positive"),("17838916","Requester number only","Positive"),("17838917","Requester email only","Positive"),("17838918","Employee and requester different active employees","Positive"),
("17838919","Missing requester","Validation"),("17838920","Null requester","Validation"),("17838921","Empty requester object","Validation"),("17838922","Invalid requester identifier","Validation"),("17838923","Inactive requester","Validation"),("17838924","Requester identifier mismatch","Validation"),
    ("17838925","Missing service","Validation"),("17838926","Null service","Validation"),("17838927","Service ID only","Positive"),("17838928","Service provider/account/number mismatch","Validation"),("17838929","Invalid service ID","Validation"),("17838930","Inactive service","Validation"),("17838931","Service with open workorder","Validation"),
("17838932","Missing catalog item","Validation"),("17838933","Null catalog item","Validation"),("17838934","Inactive catalog item","Validation"),("17838935","Unsupported hardware category","Positive"),("17838936","Catalog incompatible with provider","Validation"),("17838937","Catalog incompatible with service type","Validation"),("17838938","Catalog incompatible with contract","Validation"),
("17838939","Missing service type","Validation"),("17838940","Invalid service type","Validation"),("17838941","Service type incompatible with provider","Validation"),("17838942","Missing contract","Validation"),("17838943","Null contract","Validation"),("17838944","Inactive contract","Validation"),("17838945","Contract wrong provider","Validation"),("17838946","Contract wrong service type","Validation"),("17838947","Contract incompatible with catalog","Validation"),
("17838948","Current asset omitted","Positive"),("17838949","Current primary asset","Positive"),("17838950","Current non-primary asset","Positive"),("17838951","Current asset from another service","Validation"),("17838952","Invalid current asset","Validation"),
("17838953","Warehouse omitted","Validation"),("17838954","Warehouse null","Validation"),("17838955","Invalid warehouse","Validation"),("17838956","Inactive warehouse","Validation"),("17838957","Warehouse CanAdd disabled","Validation"),
    ("17838958","Compatible new option","Positive"),("17838959","Multiple compatible new options","Positive"),("17838960","Options omitted","Positive"),("17838961","Options null","Positive"),("17838962","Duplicate option IDs","Positive"),("17838963","Option already on contract","Validation"),("17838964","Option incompatible with provider or service type","Validation"),
("17838965","Compatible accessory","Positive"),("17838966","Multiple compatible accessories","Positive"),("17838967","Accessories omitted","Positive"),("17838968","Accessories null","Positive"),("17838969","Accessory incompatible with new catalog","Validation"),("17838970","Accessory incompatible with service","Validation"),
    ("17838971","Ignore warnings false with incompatible upgrade","Validation"),("17838972","Ignore warnings true with incompatible upgrade","Validation"),("17838973","Missing shipping address","Validation"),("17838974","Shipping address missing child field","Validation"),("17838975","Invalid country code","Validation"),("17838976","Missing cost center","Validation"),("17838977","Invalid cost center","Validation"),("17838978","Due date null","Validation"),("17838979","Past due date","Validation"),("17838980","Invalid calendar due date","Validation"),("17838981","Comments null","Positive"),("17838982","Comments SQL injection input","Positive"),("17838983","Comments XSS input","Positive"),("17838984","Unauthorized request","Authorization"),("17839018","Due date blank","Validation"),("17839019","Due date omitted","Validation"),("17839020","Service without hardware and warehouse omitted","Validation"),("17839021","Service with hardware and compatible warehouse","Positive"),("17839022","Service with hardware warehouse omitted ignoreWarnings false","Validation"),("17839023","Service with hardware warehouse omitted ignoreWarnings true","Validation")]
CASES=[SelfServiceUpgradeCase(*row) for row in _CASES]
CASES=[SelfServiceUpgradeCase(case.case_id,case.title,"Validation") if case.case_id=="17838911" else case for case in CASES]
CASE_PARAMS=[pytest.param(case,id=f"C{case.case_id}",marks=[pytest.mark.swagger_section("Self Service Requests"),pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/Upgrade"),pytest.mark.testrail_case(case.case_id),pytest.mark.dashboard_title(case.title)]) for case in CASES]

def _json(response):
    try:
        value=response.json(); return value if isinstance(value,dict) else {"raw":value}
    except ValueError: return {}

def _employee(db, *, active=True, exclude_id=None):
    status="=1" if active else "<>1"; active_filter="Active=1" if active else "Active=0"; exclusion="AND EmployeeId<>?" if exclude_id is not None else ""; params=(exclude_id,) if exclude_id is not None else ()
    row=db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE "+active_filter+" AND EmployeeStatusId"+status+" AND EMail IS NOT NULL "+exclusion+" ORDER BY EmployeeId DESC",params)
    assert row,"No employee seed available"; return {"id":int(row["id"]),"number":str(row.get("number") or ""),"email":str(row["email"])}

def preview_seed_compatibility(db, *, provider_id=None, service_type_id=None, limit=2):
    """Same eligibility as Upgrade preview, plus Contract.SelfProvisioningEnabled=1 required for self service."""
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) si.ProviderId AS providerId, p.Name AS providerName, si.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.Provider p ON p.ProviderId=si.ProviderId
        INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId=si.ServiceTypeId
        INNER JOIN cimpl.ServiceInventoryHardwareInventoryRelation hir ON hir.ServiceInventoryId=si.ServiceInventoryId AND hir.IsPrimary=1
        INNER JOIN cimpl.Contract c ON c.ProviderId=si.ProviderId AND c.ServiceTypeId=si.ServiceTypeId AND c.Active=1 AND c.ContractId<>si.ContractId AND c.SelfProvisioningEnabled=1
        INNER JOIN cimpl.HardwareTypeContractCompatibility htc ON htc.ContractId=c.ContractId
        INNER JOIN cimpl.HardwareTypeProviderCompatibility htp ON htp.HardwareTypeId=htc.HardwareTypeId AND htp.ProviderId=si.ProviderId
        INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility sth ON sth.HardwareTypeId=htc.HardwareTypeId AND sth.ServiceTypeId=si.ServiceTypeId
        INNER JOIN cimpl.HardwareType new_hw ON new_hw.HardwareTypeId=htc.HardwareTypeId AND new_hw.Active=1 AND new_hw.HardwareCategoryId IN (1,3,4)
        WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL"""
        + provider_filter + service_type_filter +
        """ AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 tsi_latest.TransactId FROM cimpl.TransactServiceInventory tsi_latest WHERE tsi_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi_latest.TransactId DESC))
        AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 tws_latest.WorkorderId FROM cimpl.TransactWorkorderService tws_latest WHERE tws_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tws_latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled'))
        AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
        ORDER BY si.ProviderId DESC""",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]

def _payload(db,case):
    provider_id, service_type_id = _seed_filters_from_env()
    seed=_seed(db, self_service=True, provider_id=provider_id, service_type_id=service_type_id); requester=_employee(db); marker=f"AUTO-SELF-SERVICE-REQ02-C{case.case_id}"
    address=db.fetch_one("SELECT TOP 1 AddressLine1 AS line1,AddressLine2 AS line2,City AS city,PostalCode AS postalCode,p.Code AS state,c.Iso3 AS countryCode FROM cimpl.Address a INNER JOIN cimpl.Province p ON p.ProvinceId=a.ProvinceId INNER JOIN cimpl.Country c ON c.CountryId=a.CountryId WHERE a.Active=1 AND a.IsCompanyShippingAddress=1 AND a.AddressLine1 IS NOT NULL AND a.City IS NOT NULL AND a.PostalCode IS NOT NULL ORDER BY a.AddressId DESC")
    assert address,"No shipping address seed available"
    warehouse=db.fetch_scalar("SELECT TOP 1 w.WarehouseId FROM cimpl.Warehouse w INNER JOIN cimpl.WarehouseServiceTypeCompatibility ws ON ws.WarehouseId=w.WarehouseId AND ws.ServiceTypeId=? WHERE w.Active=1 AND w.CanAdd=1 ORDER BY w.WarehouseId DESC",(seed["serviceTypeId"],))
    assert warehouse,"No active CanAdd warehouse compatible with selected service type"
    body={"newCatalogItemId":int(seed["newCatalogItemId"]),"newServiceTypeId":int(seed["serviceTypeId"]),"newContractId":int(seed["newContractId"]),"shippingAddress":{key:str(value or "") for key,value in address.items()},"currentAssetId":int(seed["currentAssetId"]),"warehouseId":int(warehouse),"newOptions":[],"accessories":[],"service":_service_body(seed),"ignoreWarnings":True,"costCenterId":int(seed["costCenterId"]),"requester":requester,"dueDate":(date.today()+timedelta(days=2)).isoformat(),"comments":marker}
    return body,{"seed":seed,"marker":marker}

def _mutate(case,body,context,db):
    title=case.title.lower(); seed=context["seed"]
    if "required fields only" in title:
        body.pop("warehouseId", None); body.pop("currentAssetId", None); body.pop("newOptions", None); body.pop("accessories", None); body.pop("shippingAddress", None); body.pop("dueDate", None); body.pop("comments", None)
    elif "employee id only" in title: body["requester"]={"id":body["requester"]["id"]}
    elif "employee number only" in title: body["requester"]={"number":body["requester"]["number"]}
    elif "employee email only" in title: body["requester"]={"email":body["requester"]["email"]}
    elif "different active" in title: body["requester"]=_employee(db,exclude_id=body["requester"]["id"])
    elif "requester id only" in title: body["requester"]={"id":body["requester"]["id"]}
    elif "requester number only" in title: body["requester"]={"number":body["requester"]["number"]}
    elif "requester email only" in title: body["requester"]={"email":body["requester"]["email"]}
    elif "missing requester" in title: body.pop("requester",None)
    elif "null requester" in title: body["requester"]=None
    elif "empty requester" in title: body["requester"]={}
    elif "invalid requester" in title: body["requester"]={"id":999999999}
    elif "inactive requester" in title: body["requester"]=_employee(db,active=False)
    elif "requester identifier mismatch" in title: body["requester"]["email"]=_employee(db,exclude_id=body["requester"]["id"])["email"]
    elif "missing service" in title: body.pop("service",None)
    elif "null service" in title: body["service"]=None
    elif "service id only" in title: body["service"]={"id":body["service"]["id"]}
    elif "service provider/account/number" in title: body["service"]["accountNumber"]="INVALID-ACCOUNT"
    elif "service with open workorder" in title:
        service=db.fetch_one("SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.OpenWorkorderStatusView os ON os.WorkorderStatusId=ws.WorkorderStatusId INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId=tws.ServiceInventoryId INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY tws.WorkorderId DESC")
        if not service: pytest.skip("No active service with an open workorder is available")
        body["service"]={"id":int(service["id"]),"accountNumber":str(service["accountNumber"]),"providerId":int(service["providerId"]),"serviceNumber":str(service["serviceNumber"])}
    elif "invalid service" in title: body["service"]["id"]=999999999
    elif "inactive service" in title: body["service"]["id"]=311
    elif "missing catalog" in title: body.pop("newCatalogItemId",None)
    elif "null catalog" in title: body["newCatalogItemId"]=None
    elif "unsupported catalog" in title: body["newCatalogItemId"]=int(db.fetch_scalar("SELECT TOP 1 HardwareTypeId FROM cimpl.HardwareType WHERE Active=1 AND HardwareCategoryId NOT IN (1,3,4) ORDER BY HardwareTypeId DESC"))
    elif "inactive catalog" in title or "catalog incompatible" in title: body["newCatalogItemId"]=999999999
    elif "missing service type" in title: body.pop("newServiceTypeId",None)
    elif "invalid service type" in title or "service type incompatible" in title: body["newServiceTypeId"]=999999999
    elif "missing contract" in title: body.pop("newContractId",None)
    elif "null contract" in title: body["newContractId"]=None
    elif "inactive contract" in title: body["newContractId"]=int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=0 ORDER BY ContractId DESC"))
    elif "contract wrong provider" in title: body["newContractId"]=int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=1 AND ProviderId<>? ORDER BY ContractId DESC",(seed["providerId"],)))
    elif "contract wrong service" in title or "contract incompatible" in title: body["newContractId"]=999999999
    elif "current asset omitted" in title: body.pop("currentAssetId",None)
    elif "current non-primary" in title:
        asset=db.fetch_scalar("SELECT TOP 1 HardwareInventoryId FROM cimpl.ServiceInventoryHardwareInventoryRelation WHERE ServiceInventoryId=? AND IsPrimary=0",(seed["serviceId"],))
        if asset is None: pytest.skip(f"No non-primary asset exists for selected service {seed['serviceId']}")
        body["currentAssetId"]=int(asset)
    elif "current asset from another" in title or "invalid current" in title: body["currentAssetId"]=999999999
    elif "warehouse omitted ignorewarnings false" in title: body.pop("warehouseId",None); body["ignoreWarnings"]=False
    elif "warehouse omitted ignorewarnings true" in title: body.pop("warehouseId",None); body["ignoreWarnings"]=True
    elif "warehouse omitted" in title: body.pop("warehouseId",None)
    elif "warehouse null" in title: body["warehouseId"]=None
    elif "invalid warehouse" in title: body["warehouseId"]=999999999
    elif "inactive warehouse" in title: body["warehouseId"]=int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=0 ORDER BY WarehouseId DESC"))
    elif "canadd" in title: body["warehouseId"]=int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=1 AND CanAdd=0 ORDER BY WarehouseId DESC"))
    elif "options omitted" in title: body.pop("newOptions",None)
    elif "options null" in title: body["newOptions"]=None
    elif "duplicate option" in title: option=_compatible_options(db,seed,1)[0]; body["newOptions"]=[option,option]
    elif "option already" in title:
        option=db.fetch_scalar("SELECT TOP 1 cor.OptionTypeId FROM cimpl.ContractOptionTypeRelation cor INNER JOIN cimpl.OptionType ot ON ot.OptionTypeId=cor.OptionTypeId AND ot.Active=1 WHERE cor.ContractId=? ORDER BY cor.OptionTypeId DESC",(seed["newContractId"]))
        assert option,"No contract-included option seed available"; body["newOptions"]=[int(option)]
    elif "option incompatible" in title: body["newOptions"]=[999999999]
    elif "multiple compatible" in title: body["newOptions"]=_compatible_options(db,seed,2)
    elif "compatible new option" in title: body["newOptions"]=_compatible_options(db,seed,1)
    elif "accessories omitted" in title: body.pop("accessories",None)
    elif "accessories null" in title: body["accessories"]=None
    elif "accessory incompatible" in title: body["accessories"]=[999999999]
    elif "multiple compatible accessories" in title: body["accessories"]=_compatible_accessories(db,seed,2)
    elif "compatible accessory" in title: body["accessories"]=_compatible_accessories(db,seed,1)
    elif "ignore warnings false" in title: body["ignoreWarnings"]=False; body["newCatalogItemId"]=999999999
    elif "ignore warnings true" in title: body["ignoreWarnings"]=True; body["newCatalogItemId"]=999999999
    elif "missing shipping" in title: body.pop("shippingAddress",None)
    elif "shipping address missing child" in title: body["shippingAddress"].pop("postalCode",None)
    elif "invalid country" in title: body["shippingAddress"]["countryCode"]="ZZZ"
    elif "missing cost" in title: body.pop("costCenterId",None)
    elif "invalid cost" in title: body["costCenterId"]=999999999
    elif "due date blank" in title: body["dueDate"]=""
    elif "due date omitted" in title: body.pop("dueDate",None)
    elif "due date null" in title: body["dueDate"]=None
    elif "past due" in title: body["dueDate"]=(date.today()-timedelta(days=1)).isoformat()
    elif "invalid calendar" in title: body["dueDate"]="2026-02-30"
    elif "comments null" in title: body["comments"]=None
    elif "sql injection" in title: body["comments"]="'; DROP TABLE SelfServiceActionLog;--"
    elif "xss" in title: body["comments"]="<script>alert('x')</script>"

def _log_row(db,number):
    return db.fetch_one("SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,ServiceTypeId,ProviderId,OperationTypeId,WorkorderNumber,WorkorderId,RequesterEmployeeId,ReceiverEmployeeId,DueDate,Comment,ActionDetail FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC",(number,))

def _compatible_options(db, seed, count=1):
    rows=db.fetch_all("SELECT TOP (?) otc.OptionTypeId AS id FROM cimpl.OptionTypeProviderServiceTypeCompatibility otc INNER JOIN cimpl.OptionType ot ON ot.OptionTypeId=otc.OptionTypeId AND ot.Active=1 WHERE otc.ProviderId=? AND otc.ServiceTypeId=? AND otc.SelfProvisioningEnabled=1 AND NOT EXISTS (SELECT 1 FROM cimpl.ContractOptionTypeRelation cor WHERE cor.ContractId=? AND cor.OptionTypeId=otc.OptionTypeId) ORDER BY otc.OptionTypeId DESC",(count,seed["providerId"],seed["serviceTypeId"],seed["newContractId"]))
    if len(rows)<count: pytest.skip(f"Fewer than {count} compatible self-service options exist")
    return [int(row["id"]) for row in rows]

def _compatible_accessories(db, seed, count=1):
    rows=db.fetch_all("SELECT TOP (?) hc.ToHardwareTypeId AS id FROM cimpl.HardwareTypeCompatibility hc INNER JOIN cimpl.HardwareType h ON h.HardwareTypeId=hc.ToHardwareTypeId AND h.Active=1 AND h.HardwareCategoryId=2 INNER JOIN cimpl.HardwareTypeProviderCompatibility hp ON hp.HardwareTypeId=hc.ToHardwareTypeId AND hp.ProviderId=? WHERE hc.FromHardwareTypeId=? ORDER BY hc.ToHardwareTypeId DESC",(count,seed["providerId"],seed["newCatalogItemId"]))
    if len(rows)<count: pytest.skip(f"Fewer than {count} compatible accessories exist")
    return [int(row["id"]) for row in rows]

class TestPostSelfServiceUpgrade:
    @pytest.mark.parametrize("case",CASE_PARAMS)
    def test_post_self_service_upgrade(self,case,api_token,db,request):
        body,context=_payload(db,case); _mutate(case,body,context,db); scripts=PostApiDbScripts(db); before=scripts.count_rows(table="cimpl.SelfServiceActionLog",where_sql="Comment LIKE ?",params=(f"%{context['marker']}%",)); metrics=[f"CaseId: {case.case_id}",f"Request body: {json.dumps(body,default=str,ensure_ascii=True)}","OperationTypeId: 20"]; headers={"Authorization":f"Bearer {api_token}","Accept":"application/json","Content-Type":"application/json"}; response=requests.post(SELF_SERVICE_URL,json=body,headers={} if case.label=="Authorization" else headers,timeout=120); payload=_json(response)
        try:
            if case.label=="Positive":
                assert response.status_code in {200,201},f"Expected success, got {response.status_code}: {response.text}"; number=str(payload.get("selfServiceRequestNumber") or ""); UUID(str(payload.get("trackingId") or "")); row=_log_row(db,number); assert row,"No SelfServiceActionLog row for response Number"; assert int(row["OperationTypeId"])==20; assert int(row["StatusId"])==1; metrics.extend([f"Response: {json.dumps(payload,default=str)}",f"SelfServiceActionLog: {json.dumps(row,default=str)}"])
            elif case.label=="Authorization": assert response.status_code in {401,403},f"Expected 401/403, got {response.status_code}: {response.text}"
            else:
                assert response.status_code in {400,404,409,500},f"Expected validation error, got {response.status_code}: {response.text}"; after=scripts.count_rows(table="cimpl.SelfServiceActionLog",where_sql="Comment LIKE ?",params=(f"%{context['marker']}%",)); scripts.assert_count_delta(before=before,after=after,expected_delta=0,context=case.title); metrics.extend([f"Response status: {response.status_code}",f"Response body: {response.text}"])
        finally:
            metrics.append(f"Response payload: {json.dumps(payload,default=str)}"); request.node.user_properties.append(("dashboard_metrics","\n".join(metrics)))
