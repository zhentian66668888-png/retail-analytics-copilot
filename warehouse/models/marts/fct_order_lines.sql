select
    line_id,
    order_id,
    customer_id,
    country_code,
    event_date,
    event_type,
    quantity,
    unit_price_usd,
    case event_type
        when 'sale' then quantity * unit_price_usd
        when 'refund' then -quantity * unit_price_usd
        when 'cancelled' then 0
    end as net_amount_usd
from {{ ref('stg_order_events') }}
