"use client";

import { ArrowLeft, Clock, Tag } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime } from "@/lib/utils";

export default function DocumentDetailPage() {
  const params = useParams<{ path: string[] }>();
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const path = (params.path ?? []).map(decodeURIComponent).join("/");

  const document = useQuery({
    queryKey: [...queryKeys.documents(workspaceId ?? "", path), "detail"],
    queryFn: () => api.documentByPath(path),
    enabled: Boolean(workspaceId && path),
  });

  return (
    <article className="mx-auto flex max-w-4xl flex-col gap-5">
      <div>
        <Link
          href="/documents"
          className="inline-flex items-center gap-1 text-small text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          {copy.documents.detailBack}
        </Link>
      </div>

      {document.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : document.isError ? (
        <ErrorState error={document.error} onRetry={() => document.refetch()} />
      ) : document.data ? (
        <>
          <header className="flex flex-col gap-3">
            <h1 className="text-display font-semibold tracking-tight">
              {document.data.title}
            </h1>
            <p className="font-mono text-caption text-muted-foreground">
              {document.data.path}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {document.data.tags.map((tag) => (
                <Badge key={tag} variant="muted">
                  <Tag className="mr-1 h-3 w-3" aria-hidden="true" />
                  {tag}
                </Badge>
              ))}
              <span className="inline-flex items-center gap-1 text-caption text-muted-foreground">
                <Clock className="h-3 w-3" aria-hidden="true" />
                {copy.documents.detailIndexedAt}: {formatDateTime(document.data.indexed_at)}
              </span>
              {document.data.occurred_on ? (
                <span className="text-caption text-muted-foreground">
                  {copy.documents.detailOccurredOn}: {document.data.occurred_on}
                </span>
              ) : null}
            </div>
          </header>

          <div
            className="markdown-body"
            // 服务端已完成 Markdown 渲染 + nh3 清洗（禁 script/事件属性）
            dangerouslySetInnerHTML={{ __html: document.data.content_html ?? "" }}
          />

          <section className="grid gap-4 border-t border-border pt-5 md:grid-cols-2">
            <div>
              <h2 className="mb-2 text-h3 font-medium">{copy.documents.detailLinks}</h2>
              {document.data.links.length === 0 ? (
                <p className="text-small text-muted-foreground">
                  {copy.documents.detailNoLinks}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {document.data.links.map((link) => (
                    <li key={`${link.target_path}-${link.link_text ?? ""}`}>
                      <Link
                        href={`/documents/${link.target_path
                          .split("/")
                          .map(encodeURIComponent)
                          .join("/")}`}
                        className="text-small text-primary underline-offset-4 hover:underline"
                      >
                        {link.link_text ?? link.target_path}
                      </Link>
                      {!link.resolved ? (
                        <Badge variant="muted" className="ml-2">
                          断链
                        </Badge>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <h2 className="mb-2 text-h3 font-medium">
                {copy.documents.detailBacklinks}
              </h2>
              {document.data.backlinks.length === 0 ? (
                <p className="text-small text-muted-foreground">
                  {copy.documents.detailNoLinks}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {document.data.backlinks.map((link, index) => (
                    <li key={`${link.target_path}-${index}`} className="text-small">
                      {link.link_text ?? link.target_path}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </>
      ) : null}
    </article>
  );
}
