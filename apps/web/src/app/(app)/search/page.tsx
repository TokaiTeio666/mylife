"use client";

import { Search as SearchIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { useCopy } from "@/lib/i18n";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

type Submitted = {
  q: string;
  path_prefix: string;
  tags: string;
  date_from: string;
  date_to: string;
  extensions: string;
};

export default function SearchPage() {
  const copy = useCopy();
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const [q, setQ] = React.useState("");
  const [filters, setFilters] = React.useState({
    path_prefix: "",
    tags: "",
    date_from: "",
    date_to: "",
    extensions: "",
  });
  const [submitted, setSubmitted] = React.useState<Submitted | null>(null);
  const [sourceType, setSourceType] = React.useState<"both" | "document" | "review">(
    "both",
  );

  const results = useQuery({
    queryKey: queryKeys.search(workspaceId ?? "", submitted?.q ?? "", {
      ...submitted,
      sourceType,
    }),
    queryFn: () =>
      api.search({
        q: submitted?.q,
        top_k: 20,
        source_type: sourceType,
        path_prefix: submitted?.path_prefix,
        tags: submitted?.tags,
        date_from: submitted?.date_from,
        date_to: submitted?.date_to,
        extensions: submitted?.extensions,
      }),
    enabled: Boolean(workspaceId && submitted),
  });

  const matchedLabel: Record<string, string> = {
    lexical: copy.search.matchedLexical,
    vector: copy.search.matchedVector,
    both: copy.search.matchedBoth,
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{copy.search.title}</h1>
        <p className="text-small text-muted-foreground">{copy.search.subtitle}</p>
      </header>

      <form
        className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = q.trim();
          if (!trimmed) return;
          setSubmitted({ q: trimmed, ...filters });
        }}
      >
        <div className="flex gap-2">
          <Input
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={copy.search.placeholder}
            aria-label={copy.search.placeholder}
            maxLength={500}
          />
          <Button type="submit" disabled={!q.trim()}>
            <SearchIcon className="h-4 w-4" aria-hidden="true" />
            {copy.search.submit}
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-small">
          <span className="text-muted-foreground">{copy.search.sourceType}</span>
          {(
            [
              ["both", copy.search.sourceBoth],
              ["document", copy.search.sourceDocument],
              ["review", copy.search.sourceReview],
            ] as const
          ).map(([value, label]) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={sourceType === value ? "default" : "outline"}
              onClick={() => setSourceType(value)}
            >
              {label}
            </Button>
          ))}
        </div>

        <details className="text-small">
          <summary className="cursor-pointer text-muted-foreground">
            {copy.search.filters}
          </summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="path_prefix">{copy.search.pathPrefix}</Label>
              <Input
                id="path_prefix"
                value={filters.path_prefix}
                onChange={(e) =>
                  setFilters((prev) => ({ ...prev, path_prefix: e.target.value }))
                }
                maxLength={200}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="tags">{copy.search.tags}</Label>
              <Input
                id="tags"
                value={filters.tags}
                onChange={(e) => setFilters((prev) => ({ ...prev, tags: e.target.value }))}
                placeholder={copy.search.tagsPlaceholder}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="date_from">{copy.search.dateFrom}</Label>
              <Input
                id="date_from"
                type="date"
                value={filters.date_from}
                onChange={(e) =>
                  setFilters((prev) => ({ ...prev, date_from: e.target.value }))
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="date_to">{copy.search.dateTo}</Label>
              <Input
                id="date_to"
                type="date"
                value={filters.date_to}
                onChange={(e) =>
                  setFilters((prev) => ({ ...prev, date_to: e.target.value }))
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="extensions">{copy.search.extension}</Label>
              <Input
                id="extensions"
                value={filters.extensions}
                onChange={(e) =>
                  setFilters((prev) => ({ ...prev, extensions: e.target.value }))
                }
                placeholder="md"
              />
            </div>
            <div className="flex items-end">
              <Button
                type="button"
                variant="ghost"
                onClick={() =>
                  setFilters({
                    path_prefix: "",
                    tags: "",
                    date_from: "",
                    date_to: "",
                    extensions: "",
                  })
                }
              >
                {copy.search.reset}
              </Button>
            </div>
          </div>
        </details>
      </form>

      {results.isFetching ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
      ) : results.isError ? (
        <ErrorState error={results.error} onRetry={() => results.refetch()} />
      ) : results.data ? (
        <section className="flex flex-col gap-3">
          {results.data.degraded ? (
            <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-small">
              {copy.search.degraded}
            </div>
          ) : null}
          <div className="flex items-center justify-between text-caption text-muted-foreground">
            <span>
              {copy.search.results.replace(
                "{count}",
                String(results.data.items.length),
              )}
            </span>
            <span>
              {copy.search.took.replace("{ms}", String(results.data.took_ms))}
            </span>
          </div>

          {results.data.items.length === 0 ? (
            <EmptyState title={copy.search.empty} description={copy.search.emptyHint} />
          ) : (
            <ul className="flex flex-col gap-2">
              {results.data.items.map((item) => (
                <li
                  key={item.chunk_id ?? item.path}
                  className="rounded-lg border border-border bg-surface p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <Link
                      href={
                        item.source_type === "review"
                          ? `/reviews/${
                              item.path.includes("每月复盘") /* i18n-ignore */
                                ? "monthly"
                                : item.path.includes("年度复盘") /* i18n-ignore */
                                  ? "yearly"
                                  : "daily"
                            }/${item.path.split("/").pop()?.replace(".md", "") ?? ""}`
                          : `/documents/${item.path
                              .split("/")
                              .map(encodeURIComponent)
                              .join("/")}`
                      }
                      className="text-h3 font-medium text-primary underline-offset-4 hover:underline"
                    >
                      {item.title}
                    </Link>
                    <div className="flex items-center gap-1">
                      <Badge variant={item.source_type === "review" ? "accent" : "muted"}>
                        {item.source_type === "review"
                          ? copy.search.sourceReview
                          : copy.search.sourceDocument}
                      </Badge>
                      <Badge variant="muted">
                        {matchedLabel[item.matched_by] ?? item.matched_by}
                      </Badge>
                    </div>
                  </div>
                  <p className="mt-1 font-mono text-caption text-muted-foreground">
                    {item.path}
                    {item.heading ? ` · ${item.heading}` : ""}
                  </p>
                  {item.snippet_html ? (
                    <p
                      className="mt-2 text-small leading-relaxed text-foreground/90 [&_mark]:rounded-sm [&_mark]:bg-citation-bg [&_mark]:px-0.5 [&_mark]:text-foreground"
                      // snippet_html 由服务端先转义再插入 <mark>
                      dangerouslySetInnerHTML={{ __html: item.snippet_html }}
                    />
                  ) : (
                    <p className="mt-2 whitespace-pre-wrap text-small leading-relaxed text-foreground/90">
                      {item.snippet}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  );
}
