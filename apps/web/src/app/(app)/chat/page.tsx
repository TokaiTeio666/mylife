"use client";

import { MessageSquarePlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { formatDateTime } from "@/lib/utils";

export default function ChatListPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const router = useRouter();
  const queryClient = useQueryClient();

  const conversations = useQuery({
    queryKey: ["conversations", workspaceId],
    queryFn: api.listConversations,
    enabled: Boolean(workspaceId),
  });

  const create = useMutation({
    mutationFn: () => api.createConversation(),
    onSuccess: async (conversation) => {
      await queryClient.invalidateQueries({ queryKey: ["conversations", workspaceId] });
      router.push(`/chat/${conversation.id}`);
    },
  });

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <header className="flex items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h1 font-semibold tracking-tight">{copy.chat.title}</h1>
          <p className="text-small text-muted-foreground">{copy.chat.subtitle}</p>
        </div>
        <Button onClick={() => create.mutate()} disabled={create.isPending}>
          <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
          {copy.chat.newConversation}
        </Button>
      </header>

      {conversations.isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-20 w-full" />
          ))}
        </div>
      ) : conversations.isError ? (
        <ErrorState error={conversations.error} onRetry={() => conversations.refetch()} />
      ) : conversations.data && conversations.data.items.length === 0 ? (
        <EmptyState title={copy.chat.empty} description={copy.chat.emptyHint} />
      ) : (
        <ul className="flex flex-col gap-2">
          {conversations.data?.items.map((conversation) => (
            <li key={conversation.id}>
              <Link
                href={`/chat/${conversation.id}`}
                className="flex flex-col gap-1 rounded-lg border border-border bg-surface p-4 hover:bg-surface-2"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-medium">{conversation.title}</span>
                  <span className="shrink-0 text-caption text-muted-foreground">
                    {formatDateTime(conversation.updated_at)}
                  </span>
                </div>
                <span className="truncate text-small text-muted-foreground">
                  {conversation.last_message_preview || "—"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
