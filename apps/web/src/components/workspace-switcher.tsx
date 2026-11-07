"use client";

import { Check, ChevronsUpDown, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api/client";
import type { Workspace } from "@/lib/api/types";
import { copy } from "@/lib/copy/zh-CN";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function WorkspaceSwitcher({
  workspaces,
  activeId,
}: {
  workspaces: Workspace[];
  activeId: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const router = useRouter();
  const queryClient = useQueryClient();

  const active = workspaces.find((w) => w.id === activeId) ?? workspaces[0];

  const switchMutation = useMutation({
    mutationFn: (id: string) => api.switchWorkspace(id),
    onSuccess: async () => {
      setOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      router.refresh();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : copy.auth.errors.default);
    },
  });

  const createMutation = useMutation({
    mutationFn: (nextName: string) =>
      api.createWorkspace(nextName),
    onSuccess: async () => {
      setCreating(false);
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      router.refresh();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : copy.auth.errors.default);
    },
  });

  return (
    <div className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 rounded-md border border-border bg-background px-3 py-2 text-small hover:bg-surface-2"
      >
        <span className="flex min-w-0 flex-col text-left">
          <span className="text-caption text-muted-foreground">
            {copy.workspace.switcherLabel}
          </span>
          <span className="truncate font-medium">{active?.name ?? "—"}</span>
        </span>
        <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </button>

      {open ? (
        <div
          role="listbox"
          className="absolute left-0 right-0 z-dropdown mt-1 rounded-md border border-border bg-background p-1 shadow-md"
        >
          {workspaces.map((workspace) => (
            <button
              key={workspace.id}
              role="option"
              aria-selected={workspace.id === activeId}
              onClick={() => switchMutation.mutate(workspace.id)}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-small hover:bg-surface-2",
                workspace.id === activeId && "bg-surface-2",
              )}
            >
              <span className="truncate">{workspace.name}</span>
              {workspace.id === activeId ? (
                <Check className="h-4 w-4 text-primary" aria-hidden="true" />
              ) : null}
            </button>
          ))}

          <div className="my-1 h-px bg-border" />

          {creating ? (
            <form
              className="flex flex-col gap-2 p-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (name.trim()) createMutation.mutate(name.trim());
              }}
            >
              <Input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={copy.workspace.namePlaceholder}
                aria-label={copy.workspace.nameLabel}
                maxLength={64}
              />
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={createMutation.isPending}>
                  {copy.workspace.createSubmit}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setCreating(false)}
                >
                  {copy.workspace.cancel}
                </Button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="flex w-full items-center gap-2 rounded-sm px-2 py-2 text-left text-small text-muted-foreground hover:bg-surface-2"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              {copy.workspace.create}
            </button>
          )}
          {error ? <p className="px-2 py-1 text-caption text-danger">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
