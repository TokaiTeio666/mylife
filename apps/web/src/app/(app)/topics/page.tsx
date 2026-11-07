"use client";

import { Tags } from "lucide-react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

export default function TopicsPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const topics = useQuery({
    queryKey: queryKeys.topics(workspaceId ?? ""),
    queryFn: api.listTopics,
    enabled: Boolean(workspaceId),
  });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{copy.topics.title}</h1>
        <p className="text-small text-muted-foreground">{copy.topics.subtitle}</p>
      </header>

      {topics.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-28 w-full" />
          ))}
        </div>
      ) : topics.isError ? (
        <ErrorState error={topics.error} onRetry={() => topics.refetch()} />
      ) : topics.data && topics.data.items.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {topics.data.items.map((topic) => (
            <Card key={topic.topic}>
              <CardHeader>
                <CardTitle>{topic.title}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                <Badge variant="muted">
                  {copy.topics.count.replace("{count}", String(topic.open_count))}
                </Badge>
                <Link
                  href={`/topics/${encodeURIComponent(topic.topic)}`}
                  className="text-small text-accent hover:underline"
                >
                  {topic.path}
                </Link>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title={copy.topics.empty}
          description={copy.topics.emptyHint}
          icon={<Tags className="h-6 w-6" aria-hidden="true" />}
        />
      )}
    </div>
  );
}
