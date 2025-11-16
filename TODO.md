

  Where everything is correct and as expected, but unfortunately I see in my receipt_items table in my db for the product BioBio Tomaten sort.400g still the quantity 2
  but in the logs I see the focused model got 1 and I see 2025-11-15 20:57:51 [productdb-extraction] INFO: Focused quantity override (match=1.00) for 2025-09-
  03_netto_2.jpeg item 'BioBio Tomaten sort.400g': qty 2.0→1.0

  So why is it not changed in the table?

  Maybe because of the validation?

  [] When quantity is updated, also update the line_gross and unit price. Maybe focused quantity model needs also to output the actual line_gross price? So I can update anything else?