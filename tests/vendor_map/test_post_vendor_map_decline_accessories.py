from __future__ import annotations

import pytest

from tests.vendor_map._accessory_action_common import run_accessory_action
from tests.vendor_map._vendor_map_common import VendorMapCase, case_param

API_PATH = "vendormap/declineAccessories"
PREFIX = "VMP18"
MAP_KEY = "vendorAccessoriesMapId"
CASES = [
    VendorMapCase("01", "Functional", "Decline one accessory", "POST a valid vendorId and one vendorAccessoriesMapId required by the live API", "HTTP 200/201 and the accessory is Declined/Not Active"),
    VendorMapCase("02", "Functional", "Decline multiple accessories", "POST a vendorId and multiple accessory map IDs", "All selected accessories are Declined/Not Active"),
    VendorMapCase("03", "Validation", "Reject missing vendorId", "POST only vendorAccessoriesMapId", "HTTP 400 or equivalent validation error"),
    VendorMapCase("04", "Validation", "Reject missing accessory map IDs", "POST vendorId with an empty ID list", "HTTP 400 or equivalent validation error"),
    VendorMapCase("05", "Compatibility", "Persist declined accessory state", "Read VendorAccessoryMap and HardwareType before and after decline", "Accessory status changes and category-2 HardwareType.Active becomes false"),
    VendorMapCase("06", "Security", "Reject unauthenticated decline", "POST without Authorization", "HTTP 401/403 expected; current API returns HTTP 500 with IDENTITY_REQUEST_DOESNT_HAVE_CLAIMS and must not return data"),
    VendorMapCase("07", "Security", "Reject cross-vendor accessory IDs", "Use a map ID belonging to a different vendor", "Request is rejected and the other row is unchanged"),
    VendorMapCase("08", "Non-functional", "Return generic success response promptly", "Decline one accessory and measure duration", "HTTP 200/201 with generic response within 60 seconds"),
]
CASE_PARAMS = [case_param(case, prefix=PREFIX, operation="POST /v1/vendormap/declineAccessories") for case in CASES]


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_decline_accessories(case, api_context, db, request):
    run_accessory_action(case, api_context, db, request, path=API_PATH, map_key=MAP_KEY, active_expected=False)