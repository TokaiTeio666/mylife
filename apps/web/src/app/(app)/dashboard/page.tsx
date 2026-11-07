"use client";

import { useQuery } from "@tanstack/react-query";
import { CalendarCheck, FileText, ListTodo } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import type { ReviewSummary } from "@/lib/api/types";
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

function LatestRow({ label, review }: { label: string; review: ReviewSummary | null }) {
  if (!review) {
    return (
      <li className="flex items-center justify-between text-small text-muted-foreground">
        <span>{label}</span>
        <span>{copy.dashboard.latestReviewsEmpty}</span>
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center gap-2 text-small">
      <span className="text-muted-foreground">{label}</span>
      <Link
        href={`/reviews/${review.kind}/${review.period_key}`}
        className="font-medium text-primary hover:underline"
      >
        {review.title}
      </Link>
      <Badge variant="muted">v{review.version}</Badge>
      {review.degraded ? <Badge variant="warning">{copy.reviews.degraded}</Badge> : null}
    </li>
  );
}

export default function DashboardPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;

  const latest = useQuery({
    queryKey: queryKeys.dashboard(workspaceId ?? "", "latest"),
    queryFn: api.dashboardLatest,
    enabled: Boolean(workspaceId),
  });
  const tasks = useQuery({
    queryKey: queryKeys.dashboard(workspaceId ?? "", "tasks"),
    queryFn: () => api.dashboardTasks(),
    enabled: Boolean(workspaceId),
  });
  const stats = useQuery({
    queryKey: queryKeys.dashboard(workspaceId ?? "", "stats"),
    queryFn: () => api.dashboardStats("30d"),
    enabled: Boolean(workspaceId),
  });
  const year = new Date().getFullYear();
  const calendar = useQuery({
    queryKey: queryKeys.dashboard(workspaceId ?? "", `calendar-${year}`),
    queryFn: () => api.dashboardCalendar(year),
    enabled: Boolean(workspaceId),
  });

  const greeting = copy.dashboard.greeting.replace(
    "{name}",
    me.data?.user.display_name ?? "",
  );
  const completion = latest.data ? (latest.data.completion_rate * 100).toFixed(1) : "0.0";

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{greeting}</h1>
        <p className="text-small text-muted-foreground">{copy.dashboard.subtitle}</p>
      </header>

      {latest.isError ? (
        <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
      ) : (
        <section className="grid gap-3 sm:grid-cols-3" aria-label={copy.dashboard.title}>
          {latest.isLoading ? (
            Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-24" />
            ))
          ) : (
            <>
              <StatCard
                label={copy.dashboard.completionRate}
                value={`${completion}%`}
                icon={FileText}
              />
              <StatCard
                label={copy.dashboard.openTasks}
                value={latest.data?.open_tasks ?? 0}
                icon={ListTodo}
              />
              <StatCard
                label={copy.dashboard.reviews}
                value={stats.data?.reviews.daily ?? 0}
                icon={CalendarCheck}
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
            {latest.data ? (
              <ul className="flex flex-col gap-2">
                <LatestRow label={copy.reviews.kindDaily} review={latest.data.daily} />
                <LatestRow label={copy.reviews.kindWeekly} review={latest.data.weekly} />
                <LatestRow label={copy.reviews.kindYearly} review={latest.data.yearly} />
              </ul>
            ) : (
              <EmptyState title={copy.dashboard.latestReviewsEmpty} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{copy.dashboard.tasks}</CardTitle>
            <CardDescription>{copy.dashboard.tasksHint}</CardDescription>
          </CardHeader>
          <CardContent>
            {tasks.data && tasks.data.items.length > 0 ? (
              <ul className="flex flex-col gap-2 text-small">
                {tasks.data.items.slice(0, 8).map((task) => (
                  <li key={task.id} className="flex items-center gap-2">
                    <Badge variant={task.status === "carried" ? "warning" : "muted"}>
                      {task.days_open}d
                    </Badge>
                    <span className="truncate">{task.text}</span>
                    <span className="ml-auto text-caption text-muted-foreground">
                      {task.source_path ?? ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title={copy.dashboard.tasksEmpty} />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{copy.dashboard.calendar}</CardTitle>
          <CardDescription>
            {year} · {copy.dashboard.calendarHint}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {calendar.data ? (
            <div className="grid grid-cols-6 gap-2 sm:grid-cols-12">
              {calendar.data.months.map((month) => (
                <div
                  key={month.month}
                  className={`flex h-12 items-center justify-center rounded-md border text-caption ${
                    month.has_weekly
                      ? "border-accent/40 bg-accent/10 text-accent"
                      : "border-border text-muted-foreground"
                  }`}
                  title={month.weekly_period_key ?? ""}
                >
                  {month.month}月
                </div>
              ))}
            </div>
          ) : (
            <Skeleton className="h-12 w-full" />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
