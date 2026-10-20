-- Every won deal belongs to exactly one SDR and one sales rep.
select 'sdrs' as rollup, sum(deals_won) as deals from {{ ref('dim_sdrs') }}
having sum(deals_won) <> (select count(*) from {{ ref('stg_closed_deals') }})
union all
select 'sales_reps', sum(deals_won) from {{ ref('dim_sales_reps') }}
having sum(deals_won) <> (select count(*) from {{ ref('stg_closed_deals') }})
