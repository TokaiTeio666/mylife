import type { Metadata } from "next";

import { LegalDocument } from "@/components/legal-document";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.legal.privacyTitle };

/**
 * 隐私政策（AC-64）。必须匿名可访问且返回 200，因此放在 `(app)` 组之外
 * （PHASE5-SPEC D21）。内容包含数据出境供应商与 Git 历史保留告知。
 */
export default function PrivacyPage() {
  return <LegalDocument doc="privacy" />;
}
