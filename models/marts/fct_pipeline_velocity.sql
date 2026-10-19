/*
  One row per opportunity: the core fact table for pipeline velocity, risk and forecasting.

  Key metrics:
    weighted_amount       amount * stage_probability for open deals (expected revenue), 0 once closed
    sales_cycle_days      created -> close for closed deals (null while open)
    deal_age_days         created -> as-of date for open deals (null once closed)
    days_in_<stage>       time spent in each funnel stage (to as-of date if currently there)
    is_stalled            open, in Negotiation, for more than var('stalled_days_threshold') days
    is_in_forecast_quarter  open, with an expected close date in the quarter after the as-of date
*/
with stage_days as (
    select
        opportunity_id,
        sum(case when stage_name = 'Prospecting' then days_in_stage end) as days_in_prospecting,
        sum(case when stage_name = 'Discovery' then days_in_stage end) as days_in_discovery,
        sum(case when stage_name = 'Demo' then days_in_stage end) as days_in_demo,
        sum(case when stage_name = 'Negotiation' then days_in_stage end) as days_in_negotiation,
        -- Furthest open stage reached (1 = Prospecting ... 4 = Negotiation).
        max(case when stage_order <= 4 then stage_order end) as furthest_stage_order,
        -- The stage a lost deal was lost from.
        max(case when stage_outcome = 'lost' then stage_name end) as lost_from_stage
    from {{ ref('fct_stage_history') }}
    group by opportunity_id
),

activity as (
    select
        opportunity_id,
        count(*) as activity_count,
        max(activity_date) as last_activity_date,
        count(*) filter (
            where activity_date > {{ as_of_date() }} - interval 30 day
        ) as activities_last_30_days
    from {{ ref('stg_activities') }}
    group by opportunity_id
),

forecast_window as (
    -- The quarter right after the as-of date: the quarter being forecast.
    select
        cast(date_trunc('quarter', {{ as_of_date() }} + interval 1 day) as date) as quarter_start,
        cast(date_trunc('quarter', {{ as_of_date() }} + interval 1 day) + interval 3 month as date) as quarter_end
)

select
    o.opportunity_id,
    o.account_id,
    a.company_name,
    a.industry,
    a.account_tier,
    a.account_region,
    o.rep_id,
    r.rep_name,
    r.rep_region,

    -- Status
    o.stage_name,
    o.stage_order,
    o.is_open,
    o.is_won,
    o.is_lost,
    sd.furthest_stage_order,
    sd.lost_from_stage,

    -- Money
    o.amount,
    o.stage_probability,
    case when o.is_open then o.amount * o.stage_probability else 0 end as weighted_amount,

    -- Dates
    o.created_date,
    o.close_date,
    o.stage_entered_date,
    cast(date_trunc('quarter', o.created_date) as date) as created_quarter,
    cast(date_trunc('quarter', o.close_date) as date) as close_quarter,

    -- Velocity
    case when o.is_closed then {{ days_between('o.created_date', 'o.close_date') }} end as sales_cycle_days,
    case when o.is_open then {{ days_between('o.created_date', as_of_date()) }} end as deal_age_days,
    case when o.is_open then {{ days_between('o.stage_entered_date', as_of_date()) }} end as days_in_current_stage,
    sd.days_in_prospecting,
    sd.days_in_discovery,
    sd.days_in_demo,
    sd.days_in_negotiation,

    -- Engagement
    coalesce(act.activity_count, 0) as activity_count,
    act.last_activity_date,
    case when o.is_open then {{ days_between('act.last_activity_date', as_of_date()) }} end as days_since_last_activity,
    coalesce(act.activities_last_30_days, 0) as activities_last_30_days,

    -- Risk and forecast flags
    (
        o.is_open
        and o.stage_name = 'Negotiation'
        and {{ days_between('o.stage_entered_date', as_of_date()) }} > {{ var('stalled_days_threshold') }}
    ) as is_stalled,
    (
        o.is_open
        and o.close_date >= fw.quarter_start
        and o.close_date < fw.quarter_end
    ) as is_in_forecast_quarter

from {{ ref('stg_opportunities') }} o
join {{ ref('stg_accounts') }} a using (account_id)
join {{ ref('stg_reps') }} r using (rep_id)
join stage_days sd using (opportunity_id)
left join activity act using (opportunity_id)
cross join forecast_window fw
