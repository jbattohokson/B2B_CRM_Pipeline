-- One row per sales rep (closer). The export only names reps on WON deals, so this
-- measures volume and speed. A conversion rate per rep can't be computed from this data.
with wins as (
    select * from {{ ref('fct_lead_funnel') }} where is_won
),

ranked_source as (
    select sr_id, lead_source,
           row_number() over (partition by sr_id order by count(*) desc, lead_source) as rn
    from wins group by sr_id, lead_source
),

ranked_segment as (
    select sr_id, business_segment,
           row_number() over (partition by sr_id order by count(*) desc, business_segment) as rn
    from wins group by sr_id, business_segment
),

stats as (
    select
        sr_id,
        count(*) as deals_won,
        median(days_to_close) as median_days_to_close,
        avg(days_to_close) as avg_days_to_close,
        count(*) filter (where won_within_30_days) as deals_won_within_30_days,
        count(distinct sdr_id) as distinct_sdrs_paired_with,
        min(won_date) as first_win_date,
        max(won_date) as last_win_date,
        count(distinct date_trunc('month', won_date)) as active_months
    from wins
    group by sr_id
)

select
    s.sr_id,
    'Rep ' || lpad(cast(row_number() over (order by s.sr_id) as varchar), 2, '0') as sales_rep_label,
    s.deals_won,
    s.deals_won::double / sum(s.deals_won) over () as share_of_deals,
    s.median_days_to_close,
    s.avg_days_to_close,
    s.deals_won_within_30_days::double / s.deals_won as share_closed_within_30_days,
    s.distinct_sdrs_paired_with,
    s.first_win_date,
    s.last_win_date,
    s.active_months,
    s.deals_won::double / s.active_months as deals_per_active_month,
    src.lead_source as top_lead_source,
    seg.business_segment as top_business_segment
from stats s
left join ranked_source src on src.sr_id = s.sr_id and src.rn = 1
left join ranked_segment seg on seg.sr_id = s.sr_id and seg.rn = 1
