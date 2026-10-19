/*
  Stage-to-stage conversion and stage duration, by account tier plus an 'All' rollup.
  Grain: one row per (account_tier, open funnel stage).

  conversion_rate only counts resolved stage visits: advanced / (advanced + lost).
  Deals still sitting in a stage haven't had an outcome yet. Counting them as
  "not converted" would make recent cohorts look worse than they are (right-censoring).

  pct_of_created_reaching_stage is the classic funnel view: the share of all deals
  that ever entered this stage, out of the deals that entered Prospecting.

  historical_win_rate_from_stage is the chance that a deal sitting in this stage
  eventually closes won, judged by history: the product of this stage's
  conversion_rate and every later stage's. Compare it with the CRM's fixed stage
  probability (10/20/40/70%). fct_pipeline_velocity uses it as an alternative
  weighting for the forecast.
*/
with visits as (
    select *
    from {{ ref('fct_stage_history') }}
    where stage_order between 1 and 4
),

by_tier as (
    select
        coalesce(account_tier, 'All') as account_tier,
        stage_name,
        stage_order,
        count(*) as deals_entered,
        count(*) filter (where stage_outcome = 'advanced') as deals_advanced,
        count(*) filter (where stage_outcome = 'lost') as deals_lost,
        count(*) filter (where stage_outcome = 'open') as deals_open,
        avg(days_in_stage) filter (where stage_outcome <> 'open') as avg_days_in_stage,
        median(days_in_stage) filter (where stage_outcome <> 'open') as median_days_in_stage
    from visits
    group by grouping sets ((account_tier, stage_name, stage_order), (stage_name, stage_order))
),

rates as (
    select
        *,
        deals_advanced / nullif(deals_advanced + deals_lost, 0) as conversion_rate
    from by_tier
)

select
    account_tier,
    stage_name,
    stage_order,
    deals_entered,
    deals_advanced,
    deals_lost,
    deals_open,
    conversion_rate,
    -- Product of conversion rates from this stage through Negotiation -> Closed-Won.
    product(conversion_rate) over (
        partition by account_tier order by stage_order desc
        rows between unbounded preceding and current row
    ) as historical_win_rate_from_stage,
    deals_entered / first_value(deals_entered) over (
        partition by account_tier order by stage_order
    ) as pct_of_created_reaching_stage,
    round(avg_days_in_stage, 1) as avg_days_in_stage,
    median_days_in_stage
from rates
order by
    case account_tier when 'SMB' then 1 when 'Mid-Market' then 2 when 'Enterprise' then 3 else 4 end,
    stage_order
