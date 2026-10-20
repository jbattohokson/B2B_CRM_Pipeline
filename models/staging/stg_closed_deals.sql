-- One row per won lead. A declared monthly revenue of 0 means the seller didn't
-- declare one (797 of 842 deals), so it becomes NULL rather than a real zero.
select
    mql_id,
    seller_id,
    sdr_id,
    sr_id,
    won_date as won_at,
    cast(won_date as date) as won_date,
    coalesce(business_segment, 'unknown') as business_segment,
    coalesce(lead_type, 'unknown') as lead_type,
    coalesce(lead_behaviour_profile, 'unknown') as lead_behaviour_profile,
    coalesce(business_type, 'unknown') as business_type,
    has_company,
    has_gtin,
    average_stock,
    declared_product_catalog_size,
    case when declared_monthly_revenue > 0 then declared_monthly_revenue end as declared_monthly_revenue_brl
from {{ source('raw', 'closed_deals') }}
