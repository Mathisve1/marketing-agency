// Yuvo Studio — Phase 1C brand reads.
//
// OPERATOR SURFACE. Brand records carry the workspace-internal config
// (brand_tone, audience_assumption, internal palette). Do not import this
// module from anything under `web/app/client/*` — that surface is
// restricted to `web/lib/data/content.ts`.

import type { Brand } from "@/lib/types";
import { DEMO_BRANDS } from "@/lib/demo-data";
import { getSupabaseServerClient } from "@/lib/supabase/client";
import type { BrandRow } from "@/lib/supabase/types";
import { brandRowToBrand } from "./mappers";
import { SupabaseDataError, getDataSource } from "./_source";

// The exact column projection used everywhere we read brands. Kept in
// sync with BrandRow in web/lib/supabase/types.ts.
const BRAND_SELECT =
  "id, workspace_id, name, niche, website_url, brand_tone, " +
  "audience_assumption, primary_color_hex, thumbnail_path";

/** Returns every brand the operator's workspace can see.
 *  Phase 1C: demo seed → in-memory; supabase → SELECT from public.brands. */
export async function getAgencyBrands(): Promise<Brand[]> {
  if (getDataSource() === "demo") return [...DEMO_BRANDS];

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("brands")
    .select(BRAND_SELECT)
    .order("name", { ascending: true });

  if (error) throw new SupabaseDataError("getAgencyBrands", error);
  if (!data) return [];
  return (data as unknown as BrandRow[]).map(brandRowToBrand);
}

export async function getBrandById(brandId: string): Promise<Brand | null> {
  if (getDataSource() === "demo") {
    return DEMO_BRANDS.find((b) => b.id === brandId) ?? null;
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("brands")
    .select(BRAND_SELECT)
    .eq("id", brandId)
    .maybeSingle();

  if (error) throw new SupabaseDataError("getBrandById", error);
  if (!data) return null;
  return brandRowToBrand(data as unknown as BrandRow);
}
