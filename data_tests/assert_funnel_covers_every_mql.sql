-- Every lead appears in the funnel exactly once.
select (select count(*) from {{ ref('fct_lead_funnel') }}) as got,
       (select count(*) from {{ ref('stg_mqls') }}) as expected
where (select count(*) from {{ ref('fct_lead_funnel') }}) <> (select count(*) from {{ ref('stg_mqls') }})
