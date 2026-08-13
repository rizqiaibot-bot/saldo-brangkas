-- ============================================================
-- HARDEN RLS SALDO BRANGKAS (non-destruktif, idempotent)
-- Project: dsryxvelpbuitjmnswxc
-- Tidak menyentuh tabel pos_* / data existing.
-- ============================================================

-- 1. Tabel whitelist user Brankas
CREATE TABLE IF NOT EXISTS brangkas_users (
  user_id    uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE brangkas_users ENABLE ROW LEVEL SECURITY;

-- 2. Helper function pengecek whitelist (SECURITY DEFINER)
--    search_path dikosongkan + tabel di-qualify penuh utk cegah hijacking.
CREATE OR REPLACE FUNCTION public.is_brangkas_user(uid uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (SELECT 1 FROM public.brangkas_users WHERE user_id = uid);
$$;

-- 3. Masukkan owner ke whitelist
INSERT INTO brangkas_users (user_id)
VALUES ('893e20e6-70cc-46b9-aa29-71e849709a42')
ON CONFLICT (user_id) DO NOTHING;

-- 4. Hapus policy lama "Allow all"
DROP POLICY IF EXISTS "Allow all" ON brangkas_setting;
DROP POLICY IF EXISTS "Allow all" ON brangkas_transaksi;

-- 5. Policy baru (role authenticated + whitelist)
-- brangkas_setting: SELECT + INSERT + UPDATE
CREATE POLICY "brangkas_setting_select" ON brangkas_setting FOR SELECT TO authenticated USING (public.is_brangkas_user(auth.uid()));
CREATE POLICY "brangkas_setting_insert" ON brangkas_setting FOR INSERT TO authenticated WITH CHECK (public.is_brangkas_user(auth.uid()));
CREATE POLICY "brangkas_setting_update" ON brangkas_setting FOR UPDATE TO authenticated USING (public.is_brangkas_user(auth.uid())) WITH CHECK (public.is_brangkas_user(auth.uid()));

-- brangkas_transaksi: SELECT + INSERT + UPDATE + DELETE
CREATE POLICY "brangkas_transaksi_select" ON brangkas_transaksi FOR SELECT TO authenticated USING (public.is_brangkas_user(auth.uid()));
CREATE POLICY "brangkas_transaksi_insert" ON brangkas_transaksi FOR INSERT TO authenticated WITH CHECK (public.is_brangkas_user(auth.uid()));
CREATE POLICY "brangkas_transaksi_update" ON brangkas_transaksi FOR UPDATE TO authenticated USING (public.is_brangkas_user(auth.uid())) WITH CHECK (public.is_brangkas_user(auth.uid()));
CREATE POLICY "brangkas_transaksi_delete" ON brangkas_transaksi FOR DELETE TO authenticated USING (public.is_brangkas_user(auth.uid()));

-- brangkas_modal_laci: INSERT + UPDATE
CREATE POLICY "brangkas_modal_laci_insert" ON brangkas_modal_laci FOR INSERT TO authenticated WITH CHECK (public.is_brangkas_user(auth.uid()));
CREATE POLICY "brangkas_modal_laci_update" ON brangkas_modal_laci FOR UPDATE TO authenticated USING (public.is_brangkas_user(auth.uid())) WITH CHECK (public.is_brangkas_user(auth.uid()));

-- brangkas_audit_laci: INSERT
CREATE POLICY "brangkas_audit_laci_insert" ON brangkas_audit_laci FOR INSERT TO authenticated WITH CHECK (public.is_brangkas_user(auth.uid()));
