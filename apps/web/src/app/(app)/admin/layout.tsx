"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { EmptyState } from "@/components/empty-state";
import { useCopy } from "@/lib/i18n";
import { useMe } from "@/lib/hooks/use-me";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/admin/users", key: "users" },
  { href: "/admin/workspaces", key: "workspaces" },
  { href: "/admin/invites", key: "invites" },
  { href: "/admin/audit", key: "audit" },
  { href: "/admin/system", key: "system" },
] as const;


export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const copy = useCopy();
  const me = useMe();
  const pathname = usePathname();

  const isAdmin =
    Boolean(me.data?.user.is_platform_admin) ||
    Boolean(me.data?.capabilities.includes("admin"));

  if (!me.isLoading && !isAdmin) {
    return (
      <EmptyState
        title={copy.admin.backofficeOnly}
        description={copy.common.errorHint}
      />
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{copy.admin.title}</h1>
        <p className="text-small text-muted-foreground">{copy.admin.subtitle}</p>
      </header>

      <nav className="flex flex-wrap gap-2 border-b border-border pb-2">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={cn(
              "rounded-md px-3 py-1.5 text-small hover:bg-surface-2",
              pathname === tab.href && "bg-surface-2 font-medium text-primary",
            )}
          >
            {copy.admin[tab.key]}
          </Link>
        ))}
      </nav>

      {children}
    </div>
  );
}
