select order_id
from {{ ref('stg_order_events') }}
group by order_id
having count(distinct country_code) > 1
    or count(distinct customer_id) > 1
    or (count(customer_id) > 0 and count(customer_id) < count(*))
    or count(distinct event_date) filter (where event_type = 'sale') > 1
