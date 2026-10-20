-- Conversion per landing page, only for pages with enough leads to compare
-- (vars.min_landing_page_mqls). 41 of 495 pages qualify.
select
    landing_page_id,
    count(*) as mqls,
    count(*) filter (where is_won) as deals_won,
    count(*) filter (where is_won)::double / count(*) as conversion_rate,
    {{ wilson_bound('count(*) filter (where is_won)', 'count(*)', 'low') }} as conversion_rate_ci_low,
    {{ wilson_bound('count(*) filter (where is_won)', 'count(*)', 'high') }} as conversion_rate_ci_high,
    mode(lead_source) as main_lead_source
from {{ ref('fct_lead_funnel') }}
group by landing_page_id
having count(*) >= {{ var('min_landing_page_mqls') }}
