/*
  Stage-to-stage conversion and stage duration, by account tier plus an 'All' rollup.
  Grain: one row per (account_tier, open funnel stage).

  conversion_rate only counts resolved stage visits: advanced / (advanced + lost).
  Deals still sitting in a stage haven't had an outcome yet. Counting them as
  "not converted" would make recent cohorts look worse than they are (right-censoring).

  pct_of_created_reaching_stage is the classic funnel view: the share of all deals
  that ever entered this stage, out of the deals that entered Prospecting.
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
)

select
    account_tier,
    stage_name,
    stage_order,
    deals_entered,
    deals_advanced,
    deals_lost,
    deals_open,
    deals_advanced / nullif(deals_advanced + deals_lost, 0) as conversion_rate,
    deals_entered / first_value(deals_entered) over (
        partition by account_tier order by stage_order
    ) as pct_of_created_reaching_stage,
    round(avg_days_in_stage, 1) as avg_days_in_stage,
    median_days_in_stage
from by_tier
order by
    case account_tier when 'SMB' then 1 when 'Mid-Market' then 2 when 'Enterprise' then 3 else 4 end,
    stage_order
