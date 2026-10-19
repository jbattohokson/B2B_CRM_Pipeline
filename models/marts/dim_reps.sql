-- Rep dimension for the star schema (attributes only; performance lives in dim_sales_performance).
select * from {{ ref('stg_reps') }}
