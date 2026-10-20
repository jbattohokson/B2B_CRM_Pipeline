-- One row per MQL: the lead, whether and when it closed, and who closed it.
-- won_within_<N>_days only counts as a fair 0/1 once N days have passed since first
-- contact (window_<N>_complete); later leads haven't had the chance yet.
with leads as (
    select * from {{ ref('stg_mqls') }}
),

deals as (
    select * from {{ ref('stg_closed_deals') }}
),

joined as (
    select
        l.mql_id,
        l.first_contact_date,
        l.cohort_month,
        l.lead_source,
        l.is_source_missing,
        l.landing_page_id,
        d.mql_id is not null as is_won,
        d.won_date,
        d.won_at,
        {{ days_between('l.first_contact_date', 'd.won_date') }} as days_to_close_raw,
        d.seller_id,
        d.sdr_id,
        d.sr_id,
        d.business_segment,
        d.lead_type,
        d.lead_behaviour_profile,
        d.business_type,
        d.declared_monthly_revenue_brl,
        {{ days_between('l.first_contact_date', as_of_date()) }} as days_since_first_contact
    from leads l
    left join deals d using (mql_id)
)

select
    *,
    -- One real deal closes 2 days before its lead's first contact. Keep the deal, flag it,
    -- and leave it out of cycle-time statistics.
    coalesce(days_to_close_raw < 0, false) as is_close_before_first_contact,
    case when days_to_close_raw >= 0 then days_to_close_raw end as days_to_close,
    is_won and days_to_close_raw between 0 and 30 as won_within_30_days,
    is_won and days_to_close_raw between 0 and 60 as won_within_60_days,
    is_won and days_to_close_raw between 0 and 90 as won_within_90_days,
    is_won and days_to_close_raw between 0 and 180 as won_within_180_days,
    days_since_first_contact >= 30 as window_30_complete,
    days_since_first_contact >= 60 as window_60_complete,
    days_since_first_contact >= 90 as window_90_complete,
    days_since_first_contact >= 180 as window_180_complete
from joined
