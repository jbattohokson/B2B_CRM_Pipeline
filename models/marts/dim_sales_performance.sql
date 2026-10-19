/*
  One row per rep: pipeline, bookings, velocity, risk and forecast gap as of the as-of date.

  Forecast quarter = the current quarter, the one containing the as-of date
  (2026-Q3 for 2026-08-15). Standard RevOps in-quarter forecast:

    won_qtd                         Closed-Won amount from quarter start through the as-of date
    forecast_weighted_pipeline      sum(amount * CRM stage_probability) for open deals expected to close this quarter
    projected_bookings              won_qtd + forecast_weighted_pipeline
    expected_revenue_gap            quarterly_quota - projected_bookings (positive = projected shortfall)
    projected_attainment            projected_bookings / quarterly_quota

  The *_historical columns repeat the forecast using each stage's historical win
  rate (agg_stage_conversion) in place of the CRM's fixed stage probability.

    remaining_quota                 max(quarterly_quota - won_qtd, 0)
    forecast_pipeline_coverage      unweighted pipeline closing this quarter / remaining_quota
                                    (3x+ is a common healthy target; null once quota is already met)

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
        -- Open pipeline (all future close dates)
        count(*) filter (where is_open) as open_deal_count,
        coalesce(sum(amount) filter (where is_open), 0) as open_pipeline_amount,
        coalesce(sum(weighted_amount), 0) as weighted_pipeline_amount,
        -- This quarter
        coalesce(sum(amount) filter (where is_won_this_quarter), 0) as won_qtd,
        coalesce(sum(amount) filter (where is_closing_this_quarter), 0) as forecast_pipeline_amount,
        coalesce(sum(weighted_amount) filter (where is_closing_this_quarter), 0) as forecast_weighted_pipeline,
        coalesce(sum(historical_weighted_amount) filter (where is_closing_this_quarter), 0)
            as forecast_weighted_pipeline_historical,
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
),

joined as (
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
        coalesce(x.won_qtd, 0) as won_qtd,
        coalesce(x.forecast_pipeline_amount, 0) as forecast_pipeline_amount,
        coalesce(x.forecast_weighted_pipeline, 0) as forecast_weighted_pipeline,
        coalesce(x.forecast_weighted_pipeline_historical, 0) as forecast_weighted_pipeline_historical,
        coalesce(x.stalled_deal_count, 0) as stalled_deal_count,
        coalesce(x.stalled_pipeline_amount, 0) as stalled_pipeline_amount,
        x.avg_days_since_last_activity_open,
        coalesce(x.won_amount_ttm, 0) as won_amount_ttm,
        coalesce(x.won_deal_count_ttm, 0) as won_deal_count_ttm,
        coalesce(x.closed_deal_count_ttm, 0) as closed_deal_count_ttm,
        x.avg_sales_cycle_days_won,
        x.avg_won_deal_size
    from {{ ref('stg_reps') }} r
    left join rollup x using (rep_id)
)

select
    rep_id,
    rep_name,
    rep_region,
    rep_segment,
    annual_quota,
    quarterly_quota,

    -- Pipeline
    open_deal_count,
    open_pipeline_amount,
    weighted_pipeline_amount,

    -- Current-quarter forecast (CRM stage probabilities)
    won_qtd,
    forecast_pipeline_amount,
    forecast_weighted_pipeline,
    won_qtd + forecast_weighted_pipeline as projected_bookings,
    quarterly_quota - (won_qtd + forecast_weighted_pipeline) as expected_revenue_gap,
    round((won_qtd + forecast_weighted_pipeline) / quarterly_quota, 4) as projected_attainment,

    -- Same forecast with historical win rates
    forecast_weighted_pipeline_historical,
    won_qtd + forecast_weighted_pipeline_historical as projected_bookings_historical,
    quarterly_quota - (won_qtd + forecast_weighted_pipeline_historical) as expected_revenue_gap_historical,

    greatest(quarterly_quota - won_qtd, 0) as remaining_quota,
    round(forecast_pipeline_amount / nullif(greatest(quarterly_quota - won_qtd, 0), 0), 2)
        as forecast_pipeline_coverage,

    -- Risk
    stalled_deal_count,
    stalled_pipeline_amount,
    round(avg_days_since_last_activity_open, 1) as avg_days_since_last_activity_open,

    -- TTM performance
    won_amount_ttm,
    won_deal_count_ttm,
    closed_deal_count_ttm,
    round(won_deal_count_ttm / nullif(closed_deal_count_ttm, 0), 4) as win_rate_ttm,
    round(won_amount_ttm / annual_quota, 4) as quota_attainment_ttm,

    -- Velocity
    round(avg_sales_cycle_days_won, 1) as avg_sales_cycle_days_won,
    round(avg_won_deal_size, 0) as avg_won_deal_size
from joined
