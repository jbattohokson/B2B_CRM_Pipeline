-- Rep-level totals in dim_sales_performance must match the opportunity-level fact exactly.
with fact as (
    select
        sum(amount) filter (where is_open) as open_pipeline,
        sum(weighted_amount) as weighted_pipeline,
        count(*) filter (where is_stalled) as stalled_deals,
        sum(amount) filter (where is_won_this_quarter) as won_qtd,
        sum(historical_weighted_amount) filter (where is_closing_this_quarter) as forecast_historical
    from {{ ref('fct_pipeline_velocity') }}
),
reps as (
    select
        sum(open_pipeline_amount) as open_pipeline,
        sum(weighted_pipeline_amount) as weighted_pipeline,
        sum(stalled_deal_count) as stalled_deals,
        sum(won_qtd) as won_qtd,
        sum(forecast_weighted_pipeline_historical) as forecast_historical
    from {{ ref('dim_sales_performance') }}
)
select *
from fact, reps
where fact.open_pipeline <> reps.open_pipeline
   or fact.weighted_pipeline <> reps.weighted_pipeline
   or fact.stalled_deals <> reps.stalled_deals
   or coalesce(fact.won_qtd, 0) <> reps.won_qtd
   or coalesce(fact.forecast_historical, 0) <> reps.forecast_historical
