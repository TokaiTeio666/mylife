import type { Metadata } from "next";

import { LegalDocument } from "@/components/legal-document";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.legal.termsTitle };

/** 用户协议（AC-64）；匿名可访问，位置见 `app/privacy/page.tsx` 说明。 */
export default function TermsPage() {
  return <LegalDocument doc="terms" />;
}
