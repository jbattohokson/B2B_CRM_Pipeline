-- One row per account. Standardizes names; tier and region are already clean.
select
    account_id,
    company_name,
    industry,
    tier as account_tier,
    region as account_region,
    employee_count,
    arr as current_arr,
    arr > 0 as is_active_customer
from {{ source('raw', 'accounts') }}
