{# The simulation's "today", as a DATE literal (see vars.as_of_date in dbt_project.yml). #}
{% macro as_of_date() -%}
    cast('{{ var("as_of_date") }}' as date)
{%- endmacro %}


{#
  Funnel position of a stage. Closed-Lost has no position, because a deal can
  be lost from any open stage.
#}
{% macro stage_order(stage_column) -%}
    case {{ stage_column }}
        when 'Prospecting' then 1
        when 'Discovery' then 2
        when 'Demo' then 3
        when 'Negotiation' then 4
        when 'Closed-Won' then 5
    end
{%- endmacro %}


{# Whole days from start_date to end_date. #}
{% macro days_between(start_date, end_date) -%}
    date_diff('day', {{ start_date }}, {{ end_date }})
{%- endmacro %}
