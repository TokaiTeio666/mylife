"use client";

import { CalendarCheck, FileText, ListTodo } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

function StatCard({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number | string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <p className="text-caption text-muted-foreground">{label}</p>
          <p className="text-h1 font-semibold tabular-nums">{value}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;

  const detail = useQuery({
    queryKey: queryKeys.workspaceDetail(workspaceId ?? ""),
    queryFn: () => api.workspaceDetail(workspaceId as string),
    enabled: Boolean(workspaceId),
  });

  const greeting = copy.dashboard.greeting.replace(
    "{name}",
    me.data?.user.display_name ?? "",
  );

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{greeting}</h1>
        <p className="text-small text-muted-foreground">{copy.dashboard.subtitle}</p>
      </header>

      {detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => detail.refetch()} />
      ) : (
        <section className="grid gap-3 sm:grid-cols-3" aria-label={copy.dashboard.title}>
          {detail.isLoading
            ? Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24" />)
            : (
              <>
                <StatCard
                  label={copy.dashboard.documents}
                  value={detail.data?.counts.documents ?? 0}
                  icon={FileText}
                />
                <StatCard
                  label={copy.dashboard.reviews}
                  value={detail.data?.counts.reviews ?? 0}
                  icon={CalendarCheck}
                />
                <StatCard
                  label={copy.dashboard.openTasks}
                  value={detail.data?.counts.open_tasks ?? 0}
                  icon={ListTodo}
                />
              </>
            )}
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{copy.dashboard.latestReviews}</CardTitle>
            <CardDescription>{copy.dashboard.latestReviewsHint}</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState title={copy.dashboard.latestReviewsEmpty} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{copy.dashboard.tasks}</CardTitle>
            <CardDescription>{copy.dashboard.tasksHint}</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState title={copy.dashboard.tasksEmpty} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{copy.dashboard.quickStart}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1 text-small text-muted-foreground">
          <p className="font-medium text-foreground">{copy.dashboard.quickStartImport}</p>
          <p>{copy.dashboard.quickStartImportHint}</p>
        </CardContent>
      </Card>
    </div>
  );
}
