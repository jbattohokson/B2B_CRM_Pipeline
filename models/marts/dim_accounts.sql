-- Account dimension for the star schema.
select * from {{ ref('stg_accounts') }}
