-- Lead-source rows add back up to the whole funnel.
select sum(mqls) as mqls, sum(deals_won) as won
from {{ ref('agg_lead_source_conversion') }}
having sum(mqls) <> (select count(*) from {{ ref('fct_lead_funnel') }})
    or sum(deals_won) <> (select count(*) filter (where is_won) from {{ ref('fct_lead_funnel') }})
