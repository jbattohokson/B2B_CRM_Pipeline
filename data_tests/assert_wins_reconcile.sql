-- Every closed deal shows up as exactly one won lead.
select (select count(*) filter (where is_won) from {{ ref('fct_lead_funnel') }}) as won_leads,
       (select count(*) from {{ ref('stg_closed_deals') }}) as deals
where (select count(*) filter (where is_won) from {{ ref('fct_lead_funnel') }})
   <> (select count(*) from {{ ref('stg_closed_deals') }})
