-- Calendar dimension for the star schema.
select * from {{ ref('stg_dates') }}
