# Workorder Provision API Validation Notes

## API Under Test
- Method: POST
- Route: /v1/workorders/provision
- Goal: Create a workorder in app using passed details.

## Core Validation Checklist
1. employee exists (active or inactive allowed)
2. accountId is compatible with providerId
3. serviceTypeId is compatible with providerId
4. serviceTypeId is compatible with contractId
5. optionTypeId is compatible with contractId
6. catalogItemId is compatible with contractId
7. catalogItemId is compatible with providerId and serviceTypeId
8. accessories are compatible with catalogItemId
9. accessories are compatible with providerId
10. accessories maximum count is 3
11. options are compatible with providerId and serviceTypeId
12. providerContactId is compatible with providerId
13. If providerContact is provided, exactly one of providerUserId or providerGroupId should be provided
14. If providerContact.UserId is provided, it must be active and valid for provider

## Payload Keys and Expectations
- employee: EmployeeIdentifier, required
- catalogItemId: number, required
- providerId: number, required
- contractId: number, required
- contractStartDate: string YYYY-MM-DD, required
- serviceTypeId: int, required
- accountNumber: string, required
- options: array<number>, optional
- accessories: array of {id, count}, optional
- subject: string, optional
- isUrgent: boolean, optional
- referenceNo: string, optional
- showInProviderPortal: boolean, optional
- providerContactId: string, optional
- attentionTo: string, optional
- shippingAddress: address object, required
- costCenterId: int, required or optional based on parameter 276
- requestor: EmployeeIdentifier, required
- dueDate: string YYYY-MM-DD, optional
- comments: string, optional

## Database Query Catalog

### Employee and Requestor
```sql
select * from cimpl.employee;
```

### Providers
```sql
select * from cimpl.Provider where Active = 1;
```

### Service Types
```sql
select * from cimpl.Servicetype where active = 1;
```

### Accounts by Provider
```sql
select * from cimpl.account where providerid = 7;
```

### Contracts
```sql
select * from cimpl.contract;
```

### Option compatibility for provider + serviceType
```sql
select * from cimpl.OptionType where OptionTypeId in (
select OptionTypeId from cimpl.OptionTypeProviderServiceTypeCompatibility where providerid = 7 and ServiceTypeId = 116
);
```

### Hardware type and compatibility for catalog
```sql
select * from cimpl.hardwaretype;

select * from cimpl.HardwareTypeContractCompatibility where HardwareTypeId = 1;
select * from cimpl.HardwareTypeProviderCompatibility where HardwareTypeId = 1;
select * from cimpl.ServiceTypeHardwareTypeCompatibility where HardwareTypeId = 1;

select * from cimpl.HardwareType ht
inner join cimpl.HardwareTypeContractCompatibility htc on ht.HardwareTypeId = htc.hardwaretypeid
inner join cimpl.HardwareTypeProviderCompatibility htpc on ht.HardwareTypeId = htpc.HardwareTypeId
inner join cimpl.ServiceTypeHardwareTypeCompatibility stht on ht.HardwareTypeId = stht.HardwareTypeId;
```

### Accessories compatible with selected catalog item
```sql
select * from cimpl.HardwareType ht
inner join cimpl.HardwareTypeCompatibility htc on ht.HardwareTypeId = htc.ToHardwareTypeId and ht.HardwareCategoryId = 2
inner join cimpl.HardwareTypeProviderCompatibility htp on ht.HardwareTypeId = htp.HardwareTypeId
where htc.FromHardwareTypeId = 1;
```

### Addresses
```sql
select * from cimpl.Address;
```

### Cost Centers
```sql
select * from cimpl.Organization where OrganizationTypeId in (4,5,12,20,13,28,21,29);
```

### Supported Languages
```sql
select distinct Culture from cimpl.Translations;
```

### Provider Contact by Provider
```sql
select * from cimpl.ProviderContact;
```

### Parameter controlling cost center requirement
```sql
select * from cimpl.parameter where ParameterNameId = 276;
```

## Cost Center Requirement Toggle (Parameter 276)
- Use parameter value to determine if costCenterId is mandatory.
- For testing, value can be adjusted true/false based on scenario.

## Notes for Test Design
- Positive cases should validate workorder creation persistence and relationship consistency.
- Negative cases should validate status code plus validation message text.
- For compatibility-driven fields, seed payload with DB-backed IDs selected from the compatibility tables above.
- Keep one data generation path for stable "valid payload" and mutate one field per negative case.
