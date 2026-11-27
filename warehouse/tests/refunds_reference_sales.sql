select refund.line_id
from {{ ref('stg_order_events') }} as refund
where refund.event_type = 'refund'
  and not exists (
      select 1
      from {{ ref('stg_order_events') }} as sale
      where sale.order_id = refund.order_id
        and sale.event_type = 'sale'
        and sale.event_date <= refund.event_date
  )
