-- MQL-to-deal conversion by lead source, with a 95% Wilson interval so small sources
-- don't look better or worse than the data can support.
with funnel as (
    select * from {{ ref('fct_lead_funnel') }}
),

overall as (
    select count(*) as total_mqls, count(*) filter (where is_won) as total_won from funnel
),

by_source as (
    select
        lead_source,
        count(*) as mqls,
        count(*) filter (where is_won) as deals_won,
        median(days_to_close) as median_days_to_close,
        avg(days_to_close) as avg_days_to_close
    from funnel
    group by lead_source
)

select
    s.lead_source,
    s.mqls,
    s.deals_won,
    s.deals_won::double / s.mqls as conversion_rate,
    {{ wilson_bound('s.deals_won', 's.mqls', 'low') }} as conversion_rate_ci_low,
    {{ wilson_bound('s.deals_won', 's.mqls', 'high') }} as conversion_rate_ci_high,
    s.mqls::double / o.total_mqls as share_of_mqls,
    s.deals_won::double / nullif(o.total_won, 0) as share_of_deals,
    (s.deals_won::double / s.mqls) / nullif(o.total_won::double / o.total_mqls, 0) as conversion_index,
    s.median_days_to_close,
    s.avg_days_to_close
from by_source s
cross join overall o
