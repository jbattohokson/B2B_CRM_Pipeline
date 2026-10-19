-- Every stage visit has exactly one outcome, and the 'All' row equals the sum of the tiers.
select a.stage_name
from {{ ref('agg_stage_conversion') }} a
join (
    select stage_name, sum(deals_entered) as tier_entered
    from {{ ref('agg_stage_conversion') }}
    where account_tier <> 'All'
    group by stage_name
) t using (stage_name)
where a.deals_entered <> a.deals_advanced + a.deals_lost + a.deals_open
   or (a.account_tier = 'All' and a.deals_entered <> t.tier_entered)
