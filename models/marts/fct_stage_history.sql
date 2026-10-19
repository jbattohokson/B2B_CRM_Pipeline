/*
  One row per opportunity per stage entered, with how long the deal spent there
  and what happened next. This drives stage-duration and stage-to-stage
  conversion metrics.

  stage_outcome (open funnel stages only; null for the terminal Closed-* rows):
    advanced  deal moved on to the next stage (from Negotiation, that means Closed-Won)
    lost      deal went to Closed-Lost from this stage
    open      deal is still in this stage as of the as-of date
*/
with history as (
    select
        h.*,
        lead(h.stage_name) over (
            partition by h.opportunity_id order by h.stage_sequence
        ) as next_stage_name
    from {{ ref('stg_stage_history') }} h
)

select
    h.opportunity_id || '-' || h.stage_sequence as stage_history_key,
    h.opportunity_id,
    o.account_id,
    a.account_tier,
    o.rep_id,
    h.stage_sequence,
    h.stage_name,
    h.stage_order,
    h.entered_date,
    h.exited_date,
    h.is_current_stage,
    h.next_stage_name,
    case
        when h.stage_name in ('Closed-Won', 'Closed-Lost') then null
        when h.next_stage_name is null then 'open'
        when h.next_stage_name = 'Closed-Lost' then 'lost'
        else 'advanced'
    end as stage_outcome,
    -- Days in stage. A stage the deal is still in counts up to the as-of date.
    -- Terminal Closed-* rows have no duration.
    case
        when h.stage_name in ('Closed-Won', 'Closed-Lost') then null
        else {{ days_between('h.entered_date', 'coalesce(h.exited_date, ' ~ as_of_date() ~ ')') }}
    end as days_in_stage
from history h
join {{ ref('stg_opportunities') }} o using (opportunity_id)
join {{ ref('stg_accounts') }} a using (account_id)
