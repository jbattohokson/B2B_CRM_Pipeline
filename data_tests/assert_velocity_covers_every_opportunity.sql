-- fct_pipeline_velocity must have exactly one row per raw opportunity (no join fan-out or drops).
select
    (select count(*) from {{ source('raw', 'opportunities') }}) as raw_rows,
    (select count(*) from {{ ref('fct_pipeline_velocity') }}) as fact_rows
where raw_rows <> fact_rows
