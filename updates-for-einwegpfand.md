• Database plan

  - receipt_items currently enforces non‑negative money columns (see product-database-plan.md:75-92). Drop or relax the CHECK(line_* >= 0) constraints so line_net/
    line_tax/line_gross may be negative and represent Einwegleergut refunds while keeping quantity > 0.
  - Add a semantic column such as line_type TEXT NOT NULL DEFAULT 'SALE' CHECK(line_type IN ('SALE','DEPOSIT_CHARGE','DEPOSIT_REFUND','DISCOUNT','OTHER')) so you can
    explicitly flag Einweg refunds vs. regular items; this keeps downstream analytics from misinterpreting negative numbers.
  - If you want to correlate refunds with the charge that created the deposit, add related_item_id INTEGER REFERENCES receipt_items(item_id) ON DELETE SET NULL plus an
    optional deposit_code TEXT (e.g., EINWEG, MEHRWEG) to make reconciliation trivial.
  - Consider extending receipts (lines 59-73) with summary columns such as deposit_charged_cents, deposit_refunded_cents, and gross_excluding_deposit to preserve both
    the store total and the “true spend”.
  - If you prefer keeping monetary columns positive, an alternative is a small receipt_adjustments table keyed to receipts with direction ±1; however, most German
    retailers already encode Einweg as negative line items, so letting receipt_items handle signed amounts is simpler.

  Prompt in src/paperless_automation/orchestrator/productdb/extraction.py

  - Extend the items object to include the new line_type field described above, and tell the model which tokens map to each type (e.g., Einweg-Leergut, “PFAND RUECK” →
    DEPOSIT_REFUND, “PFAND” with positive value → DEPOSIT_CHARGE).
  - Update the rule block around line_gross (extraction.py:247-279) to say that deposit refunds must be emitted with negative line_gross/line_net (still in cents) while
    quantity remains positive; explicitly call out cues such as “Rückgabe”, “Leergut”, “Pfandbon”.
  - In the validation section (extraction.py:281-287), replace “must be positive integers” with “absolute value must be positive; sign encodes charge (+) vs refund (−)”
    so the LLM stops “fixing” the negative amounts.
  - Add a short reminder that Einweg refunds typically carry tax = 0.00, but if the receipt prints a tax rate next to the line it should be used; this helps the
    downstream tax_rate CHECK stay satisfied.