"use client";

import Link from "next/link";

import { useCopy } from "@/lib/i18n";

type Doc = "privacy" | "terms";

/** 法务页正文随 locale 切换；页面本身是匿名可访问的根级路由（D21）。 */
export function LegalDocument({ doc }: { doc: Doc }) {
  const copy = useCopy();
  const title = doc === "privacy" ? copy.legal.privacyTitle : copy.legal.termsTitle;
  const sections = doc === "privacy" ? copy.legal.privacySections : copy.legal.termsSections;

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-h1 font-semibold">{title}</h1>
      <p className="mt-2 text-small text-muted-foreground">
        {copy.legal.updatedAt}: 2026-11-08
      </p>

      <section className="mt-8 flex flex-col gap-3 text-body leading-relaxed">
        {sections.map((section) => (
          <div key={section.heading} className="flex flex-col gap-3">
            <h2 className="mt-4 text-h3 font-medium first:mt-0">{section.heading}</h2>
            <p
              className={
                section.notice ? "rounded-md bg-warning/10 px-3 py-2 text-warning" : undefined
              }
            >
              {section.body}
            </p>
          </div>
        ))}
      </section>

      <Link href="/" className="mt-10 inline-block text-small text-primary">
        {copy.legal.backHome}
      </Link>
    </main>
  );
}
