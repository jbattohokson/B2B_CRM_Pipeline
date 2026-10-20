{# The data's "today", as a DATE literal (see vars.as_of_date in dbt_project.yml). #}
{% macro as_of_date() -%}
    cast('{{ var("as_of_date") }}' as date)
{%- endmacro %}


{# Whole days from start_date to end_date. #}
{% macro days_between(start_date, end_date) -%}
    date_diff('day', {{ start_date }}, {{ end_date }})
{%- endmacro %}


{#
  Wilson score interval bound for a proportion (95% by default). Unlike the
  plain normal approximation it stays inside [0, 1] and behaves for small
  groups, which matters here: some lead sources have under 100 MQLs.
#}
{% macro wilson_bound(successes, trials, side, z=1.96) -%}
    case when {{ trials }} > 0 then (
        ({{ successes }}::double / {{ trials }}) + ({{ z }} * {{ z }}) / (2.0 * {{ trials }})
        {{ '-' if side == 'low' else '+' }} {{ z }} * sqrt(
            ({{ successes }}::double / {{ trials }}) * (1 - {{ successes }}::double / {{ trials }}) / {{ trials }}
            + ({{ z }} * {{ z }}) / (4.0 * {{ trials }} * {{ trials }})
        )
    ) / (1 + ({{ z }} * {{ z }}) / {{ trials }}) end
{%- endmacro %}
