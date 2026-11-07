"use client";

import {
  CalendarCheck,
  FolderTree,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  Search,
  Settings,
  ShieldOff,
  Sun,
  Tags,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { cn } from "@/lib/utils";

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  available: boolean;
};

const NAV: NavItem[] = [
  { href: "/dashboard", label: copy.nav.dashboard, icon: LayoutDashboard, available: true },
  { href: "/documents", label: copy.nav.documents, icon: FolderTree, available: true },
  { href: "/search", label: copy.nav.search, icon: Search, available: true },
  { href: "/chat", label: copy.nav.chat, icon: MessageSquare, available: true },
  { href: "/reviews", label: copy.nav.reviews, icon: CalendarCheck, available: false },
  { href: "/topics", label: copy.nav.topics, icon: Tags, available: false },
  { href: "/settings", label: copy.nav.settings, icon: Settings, available: false },
];

function ThemeToggle() {
  const [dark, setDark] = React.useState(false);

  React.useEffect(() => {
    const stored = window.localStorage.getItem("mylife-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = stored ? stored === "dark" : prefersDark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    window.localStorage.setItem("mylife-theme", next ? "dark" : "light");
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={copy.topbar.theme}
      title={copy.topbar.theme}
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { data, isLoading, error } = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      router.replace("/login");
    }
  }, [error, router]);

  const logout = useMutation({
    mutationFn: () => api.logout(),
    onSuccess: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });

  const logoutAll = useMutation({
    mutationFn: () => api.logoutAll(),
    onSuccess: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });

  if (isLoading) {
    return (
      <div className="flex min-h-screen">
        <aside className="hidden w-60 shrink-0 border-r border-border bg-surface p-4 lg:block">
          <Skeleton className="mb-6 h-8 w-24" />
          <Skeleton className="mb-3 h-10 w-full" />
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        </aside>
        <div className="flex-1 p-6">
          <Skeleton className="mb-6 h-10 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <EmptyState title={copy.common.errorTitle} description={copy.common.errorHint} />
      </div>
    );
  }

  const activeWorkspace =
    data.workspaces.find((w) => w.id === data.active_workspace_id) ?? data.workspaces[0];

  const sidebar = (
    <div className="flex h-full flex-col gap-4 p-3">
      <div className="flex items-center justify-between px-1 py-2">
        <Link href="/dashboard" className="text-h2 font-semibold tracking-tight">
          {copy.app.name}
        </Link>
        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label={copy.common.close}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <WorkspaceSwitcher
        workspaces={data.workspaces}
        activeId={data.active_workspace_id}
      />

      <nav className="flex flex-col gap-1" aria-label="主导航">
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = pathname.startsWith(item.href);
          if (!item.available) {
            return (
              <span
                key={item.href}
                className="flex cursor-not-allowed items-center justify-between gap-2 rounded-md px-2 py-2 text-small text-muted-foreground/70"
                aria-disabled="true"
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {item.label}
                </span>
                <Badge variant="muted">{copy.nav.comingSoon}</Badge>
              </span>
            );
          }
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-2 text-small hover:bg-surface-2",
                active && "bg-surface-2 font-medium text-primary",
              )}
              onClick={() => setMobileOpen(false)}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto flex flex-col gap-1 border-t border-border pt-3">
        <Button
          variant="ghost"
          className="justify-start"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
          {copy.topbar.logout}
        </Button>
        <Button
          variant="ghost"
          className="justify-start text-muted-foreground"
          onClick={() => logoutAll.mutate()}
          disabled={logoutAll.isPending}
        >
          <ShieldOff className="h-4 w-4" aria-hidden="true" />
          {copy.topbar.logoutAll}
        </Button>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-background">
      <aside className="hidden w-60 shrink-0 border-r border-border bg-surface lg:block">
        {sidebar}
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-overlay lg:hidden">
          <button
            className="absolute inset-0 bg-black/30"
            aria-label={copy.common.close}
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute left-0 top-0 h-full w-64 border-r border-border bg-surface shadow-lg">
            {sidebar}
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-sticky flex h-14 items-center gap-3 border-b border-border bg-background/90 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label={copy.nav.expand}
          >
            <Menu className="h-5 w-5" />
          </Button>

          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div className="relative hidden w-full max-w-md md:block">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <input
                disabled
                aria-label={copy.topbar.searchPlaceholder}
                placeholder={copy.topbar.searchPlaceholder}
                className="h-9 w-full rounded-md border border-input bg-surface pl-9 pr-12 text-small placeholder:text-muted-foreground disabled:cursor-not-allowed"
              />
              <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-sm border border-border px-1.5 py-0.5 text-caption text-muted-foreground">
                {copy.topbar.commandHint}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <ThemeToggle />
            <div className="hidden items-center gap-2 rounded-md border border-border px-2 py-1 sm:flex">
              <span className="max-w-[10rem] truncate text-small">
                {data.user.display_name}
              </span>
              <Badge variant="muted">
                {activeWorkspace?.role === "owner" ? copy.workspace.roleOwner : activeWorkspace?.role}
              </Badge>
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
