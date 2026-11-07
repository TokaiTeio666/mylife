"use client";

import { ChevronRight, FileText, Folder, FolderOpen, Upload } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import type { TreeNode } from "@/lib/api/types";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

function TreeBranch({
  parent,
  childrenByParent,
  depth,
}: {
  parent: string | null;
  childrenByParent: Map<string | null, TreeNode[]>;
  depth: number;
}) {
  const nodes = childrenByParent.get(parent) ?? [];
  const [open, setOpen] = React.useState<Record<string, boolean>>({});

  return (
    <ul className={cn("flex flex-col gap-0.5", depth > 0 && "ml-4 border-l border-border pl-2")}>
      {nodes.map((node) => {
        if (node.type === "dir") {
          const expanded = open[node.path] ?? depth < 1;
          return (
            <li key={node.path}>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-small hover:bg-surface-2"
                onClick={() => setOpen((prev) => ({ ...prev, [node.path]: !expanded }))}
                aria-expanded={expanded}
              >
                <ChevronRight
                  className={cn("h-4 w-4 shrink-0 transition-transform", expanded && "rotate-90")}
                  aria-hidden="true"
                />
                {expanded ? (
                  <FolderOpen className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
                ) : (
                  <Folder className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
                )}
                <span className="truncate">{node.name}</span>
                <span className="ml-auto text-caption text-muted-foreground">
                  {node.child_count ?? 0}
                </span>
              </button>
              {expanded ? (
                <TreeBranch
                  parent={node.path}
                  childrenByParent={childrenByParent}
                  depth={depth + 1}
                />
              ) : null}
            </li>
          );
        }
        return (
          <li key={node.path}>
            <Link
              href={`/documents/${node.path
                .split("/")
                .map(encodeURIComponent)
                .join("/")}`}
              className="flex items-center gap-2 rounded-sm px-2 py-1.5 text-small text-foreground/90 hover:bg-surface-2"
            >
              <span className="h-4 w-4 shrink-0" aria-hidden="true" />
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="truncate">{node.title ?? node.name}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export default function DocumentsPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;

  const tree = useQuery({
    queryKey: queryKeys.documents(workspaceId ?? ""),
    queryFn: api.documentTree,
    enabled: Boolean(workspaceId),
  });

  const childrenByParent = React.useMemo(() => {
    const map = new Map<string | null, TreeNode[]>();
    for (const node of tree.data?.nodes ?? []) {
      const key = node.parent_path;
      const list = map.get(key) ?? [];
      list.push(node);
      map.set(key, list);
    }
    return map;
  }, [tree.data]);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header className="flex items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h1 font-semibold tracking-tight">{copy.documents.title}</h1>
          <p className="text-small text-muted-foreground">{copy.documents.subtitle}</p>
        </div>
        <div className="flex items-center gap-3">
          {tree.data ? (
            <p className="text-caption text-muted-foreground">
              {copy.documents.total.replace("{count}", String(tree.data.total_documents))}
            </p>
          ) : null}
          <Link
            href="/documents/import"
            className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-small hover:bg-surface-2"
          >
            <Upload className="h-4 w-4" aria-hidden="true" />
            {copy.documents.import}
          </Link>
        </div>
      </header>

      {tree.isLoading ? (
        <div className="flex flex-col gap-2" aria-label={copy.documents.loading}>
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : tree.isError ? (
        <ErrorState error={tree.error} onRetry={() => tree.refetch()} />
      ) : tree.data && tree.data.nodes.length === 0 ? (
        <EmptyState title={copy.documents.empty} description={copy.documents.emptyHint} />
      ) : (
        <div className="rounded-lg border border-border bg-surface p-2">
          <TreeBranch parent={null} childrenByParent={childrenByParent} depth={0} />
        </div>
      )}
    </div>
  );
}
