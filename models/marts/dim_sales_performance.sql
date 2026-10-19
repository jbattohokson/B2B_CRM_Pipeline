/*
  One row per rep: pipeline, bookings, velocity, risk and forecast gap as of the as-of date.

  Forecast quarter = the quarter right after the as-of date (2026-Q4 for 2026-09-30).
    forecast_weighted_pipeline   sum of amount * stage_probability for open deals expected to close in it
    expected_revenue_gap         quarterly_quota - forecast_weighted_pipeline (positive = projected shortfall)
    forecast_pipeline_coverage   unweighted forecast-quarter pipeline / quarterly_quota (3x+ is a common healthy target)

  Trailing-twelve-month (TTM) metrics use deals closed in the 365 days up to the as-of date.
*/
with deals as (
    select
        f.*,
        f.close_date > {{ as_of_date() }} - interval 365 day
            and f.close_date <= {{ as_of_date() }}
            and not f.is_open as closed_in_ttm
    from {{ ref('fct_pipeline_velocity') }} f
),

rollup as (
    select
        rep_id,
        -- Open pipeline
        count(*) filter (where is_open) as open_deal_count,
        coalesce(sum(amount) filter (where is_open), 0) as open_pipeline_amount,
        coalesce(sum(weighted_amount), 0) as weighted_pipeline_amount,
        coalesce(sum(amount) filter (where is_in_forecast_quarter), 0) as forecast_pipeline_amount,
        coalesce(sum(weighted_amount) filter (where is_in_forecast_quarter), 0) as forecast_weighted_pipeline,
        -- Risk
        count(*) filter (where is_stalled) as stalled_deal_count,
        coalesce(sum(amount) filter (where is_stalled), 0) as stalled_pipeline_amount,
        avg(days_since_last_activity) filter (where is_open) as avg_days_since_last_activity_open,
        -- Bookings and win rate (TTM)
        coalesce(sum(amount) filter (where closed_in_ttm and is_won), 0) as won_amount_ttm,
        count(*) filter (where closed_in_ttm and is_won) as won_deal_count_ttm,
        count(*) filter (where closed_in_ttm) as closed_deal_count_ttm,
        -- Velocity (all won deals)
        avg(sales_cycle_days) filter (where is_won) as avg_sales_cycle_days_won,
        avg(amount) filter (where is_won) as avg_won_deal_size
    from deals
    group by rep_id
)

select
    r.rep_id,
    r.rep_name,
    r.rep_region,
    r.rep_segment,
    r.annual_quota,
    r.quarterly_quota,

    coalesce(x.open_deal_count, 0) as open_deal_count,
    coalesce(x.open_pipeline_amount, 0) as open_pipeline_amount,
    coalesce(x.weighted_pipeline_amount, 0) as weighted_pipeline_amount,
    coalesce(x.forecast_pipeline_amount, 0) as forecast_pipeline_amount,
    coalesce(x.forecast_weighted_pipeline, 0) as forecast_weighted_pipeline,
    r.quarterly_quota - coalesce(x.forecast_weighted_pipeline, 0) as expected_revenue_gap,
    round(coalesce(x.forecast_pipeline_amount, 0) / r.quarterly_quota, 2) as forecast_pipeline_coverage,

    coalesce(x.stalled_deal_count, 0) as stalled_deal_count,
    coalesce(x.stalled_pipeline_amount, 0) as stalled_pipeline_amount,
    round(x.avg_days_since_last_activity_open, 1) as avg_days_since_last_activity_open,

    coalesce(x.won_amount_ttm, 0) as won_amount_ttm,
    coalesce(x.won_deal_count_ttm, 0) as won_deal_count_ttm,
    coalesce(x.closed_deal_count_ttm, 0) as closed_deal_count_ttm,
    round(coalesce(x.won_deal_count_ttm, 0) / nullif(x.closed_deal_count_ttm, 0), 4) as win_rate_ttm,
    round(coalesce(x.won_amount_ttm, 0) / r.annual_quota, 4) as quota_attainment_ttm,

    round(x.avg_sales_cycle_days_won, 1) as avg_sales_cycle_days_won,
    round(x.avg_won_deal_size, 0) as avg_won_deal_size
from {{ ref('stg_reps') }} r
left join rollup x using (rep_id)
