-- Conversion by month of first contact, overall and within 30/60/90/180 days.
-- A windowed rate is NULL until every lead in the cohort has had the full window,
-- so recent cohorts are never compared on an unfair, shorter clock.
select
    cohort_month,
    count(*) as mqls,
    count(*) filter (where is_won) as deals_won,
    count(*) filter (where is_won)::double / count(*) as conversion_rate_to_date,
    median(days_to_close) as median_days_to_close,
    {% for n in [30, 60, 90, 180] %}
    count(*) filter (where won_within_{{ n }}_days) as won_within_{{ n }}_days,
    case when bool_and(window_{{ n }}_complete)
         then count(*) filter (where won_within_{{ n }}_days)::double / count(*) end as conversion_rate_{{ n }}d
    {{- "," if not loop.last }}
    {% endfor %}
from {{ ref('fct_lead_funnel') }}
group by cohort_month
