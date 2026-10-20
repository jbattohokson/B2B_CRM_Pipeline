-- Won deals by the seller's business segment. Declared revenue is self-reported by
-- only a handful of sellers, so it's summarized as a count and a median, never a total.
select
    business_segment,
    count(*) as deals_won,
    count(*)::double / sum(count(*)) over () as share_of_deals,
    median(days_to_close) as median_days_to_close,
    count(*) filter (where business_type = 'manufacturer') as manufacturer_deals,
    count(*) filter (where business_type = 'reseller') as reseller_deals,
    count(declared_monthly_revenue_brl) as deals_with_declared_revenue,
    median(declared_monthly_revenue_brl) as median_declared_monthly_revenue_brl
from {{ ref('fct_lead_funnel') }}
where is_won
group by business_segment
