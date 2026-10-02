-- OS10 production schema preflight. READ ONLY; do not remove the guard.
-- Run only against the explicitly selected Railway environment, after
-- verifying current_database() and the host/service classification.
BEGIN TRANSACTION READ ONLY;

SELECT current_database() AS database_name,
       current_schema() AS schema_name,
       current_setting('transaction_read_only') AS transaction_read_only,
       version() AS postgres_version;

-- Required OS10 tables. `present=false` is a delta to review, not an
-- instruction to create the table from this script.
WITH required(table_name) AS (VALUES
    ('bookings'), ('booking_holds'), ('booking_guests'), ('guests'),
    ('guest_documents'), ('customer_accounts'), ('payments'), ('refunds'),
    ('gateway_orders'), ('expense_payments'), ('public_rate_limit_buckets'),
    ('conversations'), ('conversation_messages'), ('housekeeping_tasks'),
    ('maintenance_tickets'), ('inventory_items'), ('stock_movements'),
    ('notification_logs'), ('users'), ('staff_profiles'), ('attendance'),
    ('properties'), ('units'), ('business_settings'), ('audit_logs'), ('policies'),
    ('policy_audit_entries')
)
SELECT r.table_name, to_regclass(format('%I.%I', 'public', r.table_name)) IS NOT NULL AS present
FROM required r ORDER BY r.table_name;

-- Required/important columns. Missing rows are reported without selecting
-- business data. Extend this list when a new release adds schema requirements.
WITH required(table_name, column_name) AS (VALUES
    ('bookings','cancellation_at'), ('bookings','cancellation_checkin_at'),
    ('bookings','cancellation_policy_percent'), ('bookings','cancellation_refund_target'),
    ('bookings','cancellation_refund_source'), ('bookings','cancellation_requires_review'),
    ('bookings','external_reservation_id'), ('bookings','is_archived'),
    ('bookings','adults'), ('bookings','children'), ('bookings','arrival_time'),
    ('bookings','departure_time'), ('bookings','due_date'), ('bookings','internal_notes'),
    ('bookings','special_requests'), ('bookings','stay_range'),
    ('booking_holds','guest_state'), ('booking_holds','guest_address'),
    ('booking_holds','guest_gstin'), ('booking_holds','adults'), ('booking_holds','hold_range'),
    ('customer_accounts','guest_id'), ('customer_accounts','phone_verified_at'),
    ('guests','identity_status'), ('guests','identity_verified_at'),
    ('guests','identity_verified_by'), ('guests','identity_rejection_reason'),
    ('guests','identity_verified_document_id'),
    ('guest_documents','storage_key'), ('guest_documents','storage_backend'),
    ('guest_documents','file_size_bytes'), ('guest_documents','sha256_checksum'),
    ('payments','idempotency_key'), ('payments','refund_of_payment_id'), ('payments','refund_reason'),
    ('refunds','idempotency_key'), ('refunds','gateway_refund_id'),
    ('gateway_orders','hold_id'), ('expense_payments','idempotency_key'),
    ('expense_payments','request_fingerprint'), ('inventory_items','sku'),
    ('staff_profiles','designation_id'), ('staff_profiles','salary_override'),
    ('attendance','working_minutes'), ('notification_logs','category'),
    ('notification_logs','severity'), ('notification_logs','action_id')
)
SELECT r.table_name, r.column_name,
       EXISTS (SELECT 1 FROM information_schema.columns c
               WHERE c.table_schema = 'public'
                 AND c.table_name = r.table_name
                 AND c.column_name = r.column_name) AS present
FROM required r ORDER BY r.table_name, r.column_name;

-- Required PostgreSQL extensions and named indexes/constraints/triggers.
SELECT e.extname, e.extversion
FROM pg_extension e
WHERE e.extname IN ('btree_gist', 'pgcrypto')
ORDER BY e.extname;

WITH required_indexes(index_name) AS (VALUES
    ('no_overlapping_bookings'), ('no_overlapping_holds'),
    ('ux_customer_accounts_guest_id'), ('ix_customer_accounts_phone_unique'),
    ('ix_bookings_external_reservation_id_unique'), ('ux_payments_idempotency_key'),
    ('ux_refunds_idempotency_key'), ('ux_refunds_gateway_refund_id'),
    ('uq_expense_payments_idempotency_key'), ('ix_public_rate_limit_buckets_updated_at')
)
SELECT r.index_name, i.schemaname IS NOT NULL AS present, i.indexdef
FROM required_indexes r
LEFT JOIN pg_indexes i ON i.schemaname = 'public' AND i.indexname = r.index_name
ORDER BY r.index_name;

WITH required_constraints(table_name, constraint_name) AS (VALUES
    ('bookings','no_overlapping_bookings'), ('booking_holds','no_overlapping_holds'),
    ('booking_guests','ux_booking_guest_once'),
    ('customer_accounts','customer_accounts_guest_id_fkey'),
    ('inventory_items','ck_inventory_stock_nonneg'),
    ('security_deposits','ck_deposit_refund_not_exceed_amount'),
    ('refunds','ck_refund_status'), ('expenses','ck_expenses_amount_positive'),
    ('expenses','ck_expenses_approval_status'), ('guests','ck_guest_identity_status')
)
SELECT r.table_name, r.constraint_name, c.contype, c.convalidated,
       pg_get_constraintdef(c.oid) AS definition
FROM required_constraints r
LEFT JOIN pg_constraint c ON c.conrelid = to_regclass(format('%I.%I','public',r.table_name))
                        AND c.conname = r.constraint_name
ORDER BY r.table_name, r.constraint_name;

SELECT t.tgname AS trigger_name, p.proname AS function_name,
       pg_get_triggerdef(t.oid) AS trigger_definition
FROM pg_trigger t
JOIN pg_class rel ON rel.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = rel.relnamespace
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE n.nspname = 'public'
  AND t.tgname = 'trg_policy_audit_append_only'
  AND NOT t.tgisinternal;

-- Conditional data-conflict checks. Unlike a list of bare SELECTs, this
-- block can be run even when production is missing a table/column: it
-- reports SKIPPED for that check rather than aborting the read-only
-- transaction. Notices report counts only, never customer/business rows.
DO $preflight$
DECLARE item record; conflicts bigint;
BEGIN
  FOR item IN
    SELECT * FROM (VALUES
      ('account guest_id duplicate', 'customer_accounts', ARRAY['guest_id']::text[],
       'SELECT count(*) FROM (SELECT guest_id FROM public.customer_accounts WHERE guest_id IS NOT NULL GROUP BY guest_id HAVING count(*) > 1) d'),
      ('customer phone duplicate', 'customer_accounts', ARRAY['phone']::text[],
       'SELECT count(*) FROM (SELECT phone FROM public.customer_accounts WHERE phone IS NOT NULL GROUP BY phone HAVING count(*) > 1) d'),
      ('customer phone normalization collision', 'customer_accounts', ARRAY['id','phone']::text[],
       'SELECT count(*) FROM public.customer_accounts legacy JOIN public.customer_accounts normalized ON normalized.phone = ''+91'' || legacy.phone AND normalized.id <> legacy.id WHERE legacy.phone ~ ''^[0-9]{10}$'''),
      ('external reservation duplicate', 'bookings', ARRAY['external_reservation_id']::text[],
       'SELECT count(*) FROM (SELECT external_reservation_id FROM public.bookings WHERE external_reservation_id IS NOT NULL GROUP BY external_reservation_id HAVING count(*) > 1) d'),
      ('payment idempotency duplicate', 'payments', ARRAY['idempotency_key']::text[],
       'SELECT count(*) FROM (SELECT idempotency_key FROM public.payments WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING count(*) > 1) d'),
      ('refund idempotency duplicate', 'refunds', ARRAY['idempotency_key']::text[],
       'SELECT count(*) FROM (SELECT idempotency_key FROM public.refunds WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING count(*) > 1) d'),
      ('provider refund ID duplicate', 'refunds', ARRAY['gateway_refund_id']::text[],
       'SELECT count(*) FROM (SELECT gateway_refund_id FROM public.refunds WHERE gateway_refund_id IS NOT NULL GROUP BY gateway_refund_id HAVING count(*) > 1) d'),
      ('expense payment idempotency duplicate', 'expense_payments', ARRAY['idempotency_key']::text[],
       'SELECT count(*) FROM (SELECT idempotency_key FROM public.expense_payments WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING count(*) > 1) d'),
      ('inventory SKU duplicate', 'inventory_items', ARRAY['sku']::text[],
       'SELECT count(*) FROM (SELECT sku FROM public.inventory_items WHERE sku IS NOT NULL GROUP BY sku HAVING count(*) > 1) d'),
      ('booking overlap conflicts', 'bookings', ARRAY['id','unit_id','check_in','check_out','status']::text[],
       'SELECT count(*) FROM public.bookings a JOIN public.bookings b ON a.unit_id=b.unit_id AND a.id < b.id AND daterange(a.check_in,a.check_out,''[)'') && daterange(b.check_in,b.check_out,''[)'') WHERE a.status IN (''confirmed'',''checked_in'') AND b.status IN (''confirmed'',''checked_in'')'),
      ('hold overlap conflicts', 'booking_holds', ARRAY['id','unit_id','check_in','check_out','status']::text[],
       'SELECT count(*) FROM public.booking_holds a JOIN public.booking_holds b ON a.unit_id=b.unit_id AND a.id < b.id AND daterange(a.check_in,a.check_out,''[)'') && daterange(b.check_in,b.check_out,''[)'') WHERE a.status IN (''pending_payment'',''paid'') AND b.status IN (''pending_payment'',''paid'')'),
      ('negative inventory rows', 'inventory_items', ARRAY['current_stock']::text[],
       'SELECT count(*) FROM public.inventory_items WHERE current_stock < 0'),
      ('deposit refund over amount', 'security_deposits', ARRAY['refund_amount','amount']::text[],
       'SELECT count(*) FROM public.security_deposits WHERE refund_amount > amount'),
      ('nonpositive expense amounts', 'expenses', ARRAY['amount']::text[],
       'SELECT count(*) FROM public.expenses WHERE amount <= 0'),
      ('invalid expense approval states', 'expenses', ARRAY['approval_status']::text[],
       'SELECT count(*) FROM public.expenses WHERE approval_status NOT IN (''pending'',''approved'',''rejected'')'),
      ('invalid guest identity states', 'guests', ARRAY['identity_status']::text[],
       'SELECT count(*) FROM public.guests WHERE identity_status NOT IN (''not_required'',''pending'',''submitted'',''under_review'',''verified'',''rejected'',''expired'')')
    ) AS checks(label, table_name, columns_required, query_sql)
  LOOP
    IF to_regclass(format('%I.%I', 'public', item.table_name)) IS NULL THEN
      RAISE NOTICE '%: SKIPPED (table missing)', item.label;
      CONTINUE;
    END IF;
    IF EXISTS (
      SELECT 1 FROM unnest(item.columns_required) AS required(column_name)
      WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_schema='public' AND c.table_name=item.table_name
          AND c.column_name=required.column_name
      )
    ) THEN
      RAISE NOTICE '%: SKIPPED (required column missing)', item.label;
      CONTINUE;
    END IF;
    EXECUTE item.query_sql INTO conflicts;
    RAISE NOTICE '%: conflicting groups/rows=%', item.label, conflicts;
  END LOOP;
END
$preflight$;

ROLLBACK;
