select
    customer_id,
    min(event_date) filter (where event_type = 'sale') as first_purchase_date
from {{ ref('stg_order_events') }}
where customer_id is not null
group by customer_id
