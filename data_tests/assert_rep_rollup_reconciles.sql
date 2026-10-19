-- Rep-level totals in dim_sales_performance must match the opportunity-level fact exactly.
with fact as (
    select
        sum(amount) filter (where is_open) as open_pipeline,
        sum(weighted_amount) as weighted_pipeline,
        count(*) filter (where is_stalled) as stalled_deals
    from {{ ref('fct_pipeline_velocity') }}
),
reps as (
    select
        sum(open_pipeline_amount) as open_pipeline,
        sum(weighted_pipeline_amount) as weighted_pipeline,
        sum(stalled_deal_count) as stalled_deals
    from {{ ref('dim_sales_performance') }}
)
select *
from fact, reps
where fact.open_pipeline <> reps.open_pipeline
   or fact.weighted_pipeline <> reps.weighted_pipeline
   or fact.stalled_deals <> reps.stalled_deals
