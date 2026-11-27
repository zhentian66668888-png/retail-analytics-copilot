select
    trim(line_id) as line_id,
    trim(order_id) as order_id,
    nullif(trim(customer_id), '') as customer_id,
    upper(trim(country_code)) as country_code,
    cast(event_date as date) as event_date,
    lower(trim(event_type)) as event_type,
    cast(quantity as integer) as quantity,
    cast(unit_price_usd as decimal(12, 2)) as unit_price_usd
from {{ ref('raw_order_events') }}
